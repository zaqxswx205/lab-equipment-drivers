"""Dialog helpers mixin."""
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from core import Config, profile_path
from drive_profile import load_profile, profile_text_kelvin
from ui.constants import BASE
from ui.ports import serial_port_list


class DialogMixin:
    def choose_profile(self):
        if not self.drive_idle():
            return
        path = filedialog.askopenfilename(parent=self, title='选择驱动方案', initialdir=BASE,
                                          filetypes=[('INI配置文件', '*.ini')])
        if path:
            self.variables['profile_file'][0].set(path)
            self.preview_profile()

    def preview_profile(self):
        self.profile_table.delete(*self.profile_table.get_children())
        try:
            cfg = self.collect()
            profile = load_profile(profile_path(cfg), cfg)
            self.profile_summary.set('按运行分钟切换；最后阶段持续保持。' if profile.strategy == 'time'
                                     else f'参考通道 {cfg.reference_channel}：降温到达阈值依次切换，触发后不回退。')
            for i, step in enumerate(profile.steps, 1):
                trigger = f'第 {step.at:g} 分钟起' if profile.strategy == 'time' else f'温度 ≤ {step.at+273.15:g} K'
                self.profile_table.insert('', 'end', values=(i, trigger, '交流电压' if step.mode == 'voltage' else '有功功率', f'{step.value:g} '+('V' if step.mode == 'voltage' else 'W')))
        except Exception as exc:
            self.profile_summary.set('方案不可运行：'+str(exc))

    def edit_profile(self):
        if not self.drive_idle():
            return
        try:
            cfg = self.collect()
            path = profile_path(cfg)
            content = profile_text_kelvin(path.read_text(encoding='utf-8-sig'))
        except Exception as exc:
            messagebox.showerror('无法读取方案', str(exc))
            return
        dialog = tk.Toplevel(self)
        dialog.title('编辑驱动方案 — '+path.name)
        dialog.geometry('680x620')
        dialog.transient(self)
        ttk.Label(dialog, text='strategy=time：at为分钟，严格递增；temperature：at为K，严格递减，temperature_unit=K。\nmode=voltage时value为V；mode=power时value为W；0表示关闭输出。', padding=10).pack(fill='x')
        editor = tk.Text(dialog, undo=True)
        editor.pack(fill='both', expand=True, padx=10)
        editor.insert('1.0', content)
        def save():
            tmp = path.with_suffix(path.suffix+'.tmp')
            try:
                current = self.collect()
                tmp.write_text(editor.get('1.0', 'end-1c'), encoding='utf-8')
                load_profile(tmp, current)
                tmp.replace(path)
            except Exception as exc:
                if tmp.exists():
                    tmp.unlink()
                messagebox.showerror('方案无效', str(exc), parent=dialog)
                return
            self.preview_profile()
            dialog.destroy()
        buttons = ttk.Frame(dialog, padding=10)
        buttons.pack(fill='x')
        ttk.Button(buttons, text='保存方案', command=save).pack(side='right')
        ttk.Button(buttons, text='取消', command=dialog.destroy).pack(side='right', padx=8)
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
                messagebox.showerror('参数无效', str(exc), parent=dialog)
                return
            for key in values:
                self.variables[key][0].set(str(getattr(cfg, key)))
            dialog.destroy()
        ttk.Button(body, text='确定', command=apply).grid(row=3, column=1, pady=10)
        dialog.grab_set()

    def refresh_ports(self):
        """Re-scan serial ports; keep the current value even if it is not listed."""
        found = serial_port_list()
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
        ttk.Label(body, text='串口格式：8N1；两台7722发送CRLF，2700发送CR。').grid(row=5, column=0, columnspan=2, pady=(10, 6))

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
                candidate.validate()
            except ValueError as exc:
                messagebox.showerror('连接参数无效', str(exc), parent=dialog)
                return
            for key in values:
                self.variables[key][0].set(str(getattr(candidate, key)))
            dialog.destroy()

        buttons = ttk.Frame(body)
        buttons.grid(row=6, column=0, columnspan=2, sticky='e', pady=(10, 0))
        ttk.Button(buttons, text='取消', command=dialog.destroy).pack(side='left', padx=6)
        ttk.Button(buttons, text='确定', command=apply).pack(side='left')
        dialog.bind('<Escape>', lambda _: dialog.destroy())
        dialog.grab_set()
