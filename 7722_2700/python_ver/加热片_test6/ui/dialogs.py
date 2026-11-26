from ui import notices
"""Dialog helpers mixin."""
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from core import Config, profile_path
from drive_profile import load_profile, profile_text_kelvin
from ui.constants import BASE
from ui.ports import serial_port_list
from ui.profile_help import with_profile_help
from profile.settings import settings_from_profile


class DialogMixin:
    def choose_profile(self):
        if not self.drive_idle():
            return
        path = filedialog.askopenfilename(parent=self, title='选择驱动方案', initialdir=BASE,
                                          filetypes=[('INI配置文件', '*.ini')])
        if path:
            self.variables['profile_file'][0].set(path)
            self.reload_profile()

    def apply_profile_settings(self, cfg):
        for key in ('max_voltage', 'max_power', 'heater_control', 'heater_power', 'heater_max_power'):
            value = getattr(cfg, key)
            if key == 'heater_control':
                value = {'power': '恒定功率', 'voltage': '恒定电压'}[value]
            self.variables[key][0].set(str(value))
        self.update_heater_mode()

    def reload_profile(self):
        """An explicit file reload synchronises settings; typing only validates."""
        try:
            if self.run_finished:
                cfg = self.collect()
                self.apply_profile_settings(settings_from_profile(profile_path(cfg), cfg))
        except Exception as exc:
            self.profile_summary.set('方案校验未通过，请查看系统提示。')
            self.profile_table.delete(*self.profile_table.get_children())
            self.report_status('方案不可运行：'+str(exc), 'error', source='profile')
            return
        self.preview_profile()

    def bind_profile_validation(self):
        self.profile_validation_timer = None
        for variable, _ in self.variables.values():
            variable.trace_add('write', self.schedule_profile_validation)

    def schedule_profile_validation(self, *_):
        if self.profile_validation_timer is not None:
            self.after_cancel(self.profile_validation_timer)
        self.profile_validation_timer = self.after(300, self.preview_profile)

    def preview_profile(self):
        timer = getattr(self, 'profile_validation_timer', None)
        if timer is not None:
            self.after_cancel(timer)
            self.profile_validation_timer = None
        self.profile_table.delete(*self.profile_table.get_children())
        try:
            cfg = self.cfg if self.drive_busy else self.collect()
            profile = self.active_profile if self.drive_busy else load_profile(profile_path(cfg), cfg)
            if profile.requires_performance and not self.drive_busy:
                from plan_io import points_from_drive
                from performance import PerformanceRun
                legacy_path = BASE/'performance_points.ini'
                legacy = legacy_path.read_text(encoding='utf-8-sig') if legacy_path.exists() else ''
                points = points_from_drive(profile_path(cfg).read_text(encoding='utf-8-sig'), cfg, legacy)
                if points:
                    PerformanceRun(points).validate_profile(profile)
            self.profile_summary.set('按运行秒数切换；最后阶段持续保持。' if not profile.requires_temperature
                                     else f'按阶段顺序等待时间或降温条件；参考通道 {cfg.reference_channel}：降温到达阈值依次切换，触发后不回退。')
            if profile.requires_performance:
                self.profile_summary.set(self.profile_summary.get()+' 含加热测试阶段；仅点击“运行驱动方案”执行，“开始驱动”为手动模式。')
            for i, step in enumerate(profile.steps, 1):
                trigger = f'第 {step.at:g} 秒起' if profile.trigger_for(step) == 'time' else f'温度 ≤ {step.at+273.15:g} K'
                if profile.trigger_for(step) == 'time_or_temperature':
                    trigger = f'第 {step.at:g} 秒 或 温度 ≤ {step.temperature_at+273.15:g} K（先到触发）'
                if step.performance_points:
                    trigger += '；等待性能点 '+','.join(map(str, step.performance_points))+' 完成'
                self.profile_table.insert('', 'end', values=(i, trigger, '交流电压' if step.mode == 'voltage' else '有功功率', f'{step.value:g} '+('V' if step.mode == 'voltage' else 'W')))
            self.report_status('', source='profile', record=False)
        except Exception as exc:
            self.profile_summary.set('方案校验未通过，请查看系统提示。')
            self.report_status('方案不可运行：'+str(exc),'error', source='profile')

    def edit_profile(self):
        existing = getattr(self, 'profile_window', None)
        if existing is not None and existing.winfo_exists():
            existing.lift()
            return
        monitoring = not self.run_finished and getattr(self.controls, 'performance', None) is not None
        if not monitoring and not self.drive_idle():
            return
        try:
            from plan_io import import_legacy_rows, points_from_drive
            from ui.profile_editor import ProfileEditor, text_from_rows
            from performance import PerformanceRun
            cfg = self.cfg if monitoring else self.collect()
            path = profile_path(cfg)
            content = with_profile_help(profile_text_kelvin(path.read_text(encoding='utf-8-sig')))
            legacy_path = BASE/'performance_points.ini'
            legacy = legacy_path.read_text(encoding='utf-8-sig') if legacy_path.exists() else ''
            rows, migrated = import_legacy_rows(content, legacy)
            if migrated:
                content = text_from_rows(rows, content)
        except Exception as exc:
            notices.error(self, '无法读取方案', str(exc))
            return
        dialog = self.profile_window = tk.Toplevel(self)
        dialog.title('编辑驱动方案 — '+path.name)
        dialog.geometry('1360x760')
        dialog.minsize(960, 620)
        dialog.transient(self)
        editor = ProfileEditor(dialog, content)
        editor.pack(fill='both', expand=True, padx=10, pady=(8, 0))
        dialog.profile_editor = editor
        ttk.Label(dialog,textvariable=self.performance_stats,wraplength=1250).pack(fill='x',padx=10)
        def save():
            if not self.run_finished or (self.worker and self.worker.is_alive()):
                notices.warning(self, '运行中', '请先停止全部设备再保存；本轮使用启动时的参数。', parent=dialog)
                return
            tmp = path.with_suffix(path.suffix+'.tmp')
            try:
                current = self.collect()
                text = editor.get_text()
                tmp.write_text(text,encoding='utf-8')
                candidate = settings_from_profile(tmp, current)
                profile = load_profile(tmp,candidate)
                points = points_from_drive(text,candidate)
                if points:
                    PerformanceRun(points).validate_profile(profile)
                tmp.replace(path)
                self.apply_profile_settings(candidate)
            except Exception as exc:
                notices.error(self, '方案无效',str(exc),parent=dialog)
                return
            finally:
                if tmp.exists():
                    tmp.unlink()
            self.preview_profile()
            dialog.destroy()
        buttons = ttk.Frame(dialog,padding=10)
        buttons.pack(fill='x')
        ttk.Button(buttons,text='保存方案',command=save).pack(side='right')
        ttk.Button(buttons,text='关闭',command=dialog.destroy).pack(side='right',padx=8)
        dialog.grab_set()

    def power_settings(self):
        if not self.idle():
            return
        dialog = tk.Toplevel(self)
        dialog.title('有功功率反馈调节设置')
        dialog.transient(self)
        body = ttk.Frame(dialog, padding=16)
        body.pack()
        values = {}
        for i, (key, label) in enumerate((('power_slew_v_s', '最大升压速率(V/s)'), ('power_tolerance_rel', '功率相对容差(0–0.2)'), ('power_timeout', '持续未达标停机时间(s)'))):
            ttk.Label(body, text=label).grid(row=i, column=0, sticky='w', pady=5)
            values[key] = tk.StringVar(value=self.variables[key][0].get())
            ttk.Entry(body, textvariable=values[key], width=14).grid(row=i, column=1, padx=8)
        def apply():
            try:
                cfg = Config()
                for key, var in values.items():
                    setattr(cfg, key, float(var.get()))
                cfg.validate()
            except ValueError as exc:
                notices.error(self, '参数无效', str(exc), parent=dialog)
                return
            for key in values:
                self.variables[key][0].set(str(getattr(cfg, key)))
            dialog.destroy()
        ttk.Button(body, text='确定', command=apply).grid(row=3, column=1, pady=10)
        dialog.grab_set()

    def refresh_ports(self, found=None):
        """Re-scan serial ports; keep the current value even if it is not listed."""
        found = serial_port_list() if found is None else found
        self.port_devs = [device for device, _ in found]
        self.port_desc = {device.upper(): desc for device, desc in found}
        for key, combo in self.port_combos.items():
            current = self.variables[key][0].get().strip()
            values = list(self.port_devs)
            if current and current.upper() not in self.port_desc:
                values.append(current)
            combo.configure(values=values)
        names = '、'.join(self.port_devs) if self.port_devs else '未检测到，请检查连接与驱动'
        self.scan_status.set(f'已扫描串口：{names}')

    def connection_settings(self):
        if not self.idle():
            return
        dialog = tk.Toplevel(self)
        dialog.title('连接高级设置')
        dialog.transient(self)
        dialog.resizable(False, False)
        body = ttk.Frame(dialog, padding=16)
        body.pack(fill='both', expand=True)
        values = {}
        for row, (key, label, choices) in enumerate((
            ('power_baud', '7722 交流驱动（AC）波特率', (9600, 19200, 38400, 57600, 115200)),
            ('heater_baud', '7722 加热负载（DC）波特率', (9600, 19200, 38400, 57600, 115200)),
            ('meter_baud', '2700 采集与保护波特率', (300, 600, 1200, 2400, 4800, 9600, 19200)),
            ('meter_flow', '2700 XON/XOFF', ('关闭', '开启')),
            ('timeout', '设备通信超时 (s)', None),
            ('heartbeat_interval', '设备心跳间隔 (s)', None),
            ('heartbeat_timeout', '进程心跳超时 (s)', None),
        )):
            current = self.variables[key][0].get()
            if key == 'meter_flow':
                current = '开启' if current == 'True' else '关闭'
            var = tk.StringVar(value=current)
            values[key] = var
            ttk.Label(body, text=label).grid(row=row, column=0, sticky='w', padx=(0, 16), pady=6)
            if choices:
                widget = ttk.Combobox(body, textvariable=var, values=choices, width=18,
                                      state='normal' if key in ('power_baud', 'heater_baud') else 'readonly')
            else:
                widget = ttk.Entry(body, textvariable=var, width=21)
            widget.grid(row=row, column=1, pady=6)
        ttk.Label(body, text='串口格式：8N1；两台7722发送CRLF，2700发送CR。').grid(row=7, column=0, columnspan=2, pady=(10, 6))

        def apply():
            try:
                candidate = Config()
                candidate.power_baud = int(values['power_baud'].get())
                candidate.heater_baud = int(values['heater_baud'].get())
                if candidate.heater_baud <= 0:
                    raise ValueError('7722 加热负载波特率须为正整数')
                candidate.meter_baud = int(values['meter_baud'].get())
                candidate.meter_flow = values['meter_flow'].get() == '开启'
                candidate.timeout = float(values['timeout'].get())
                candidate.heartbeat_interval = float(values['heartbeat_interval'].get())
                candidate.heartbeat_timeout = float(values['heartbeat_timeout'].get())
                candidate.validate()
            except ValueError as exc:
                notices.error(self, '连接参数无效', str(exc), parent=dialog)
                return
            for key in values:
                self.variables[key][0].set(str(getattr(candidate, key)))
            dialog.destroy()

        buttons = ttk.Frame(body)
        buttons.grid(row=8, column=0, columnspan=2, sticky='e', pady=(10, 0))
        ttk.Button(buttons, text='取消', command=dialog.destroy).pack(side='left', padx=6)
        ttk.Button(buttons, text='确定', command=apply).pack(side='left')
        dialog.bind('<Escape>', lambda _: dialog.destroy())
        dialog.grab_set()
