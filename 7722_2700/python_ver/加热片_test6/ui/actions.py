from ui import notices
"""Run-control actions mixin."""
import threading
import time
from dataclasses import replace
from performance import PerformanceRun
from pathlib import Path
from tkinter import messagebox

from core import Config, run, save_config
from drive_profile import load_profile
from runtime import RunControls
from ui.constants import BASE
from ui.parameter_confirmation import confirm_parameters
from ui.manual_drive import ask_manual_drive


class ActionsMixin:
    def idle(self):
        if self.scanning:
            notices.warning(self, '扫描中', '请等待设备识别完成。')
            return False
        if self.worker and self.worker.is_alive():
            notices.warning(self, '运行中', '请先停止，再修改或保存配置。')
            return False
        return True

    def drive_idle(self):
        if self.drive_busy:
            notices.warning(self, '驱动运行中', '请先停止驱动，再切换或编辑方案；采集可以继续。')
            return False
        return True

    def save(self):
        if self.idle():
            try:
                self.cfg = self.collect()
                self.update_reference_readout()
                save_config(self.path, self.cfg)
                self.preview_profile()
                self.status.set('配置已保存。')
            except Exception as exc:
                notices.error(self, '配置错误', str(exc))

    def update_heater_link(self):
        if self.heater_link.get():
            self.controls.heater_link.set()
            if not self.controls.heater_allowed() and self.heater_busy:
                self.stop_heater()
        else:
            self.controls.heater_link.clear()
        self.refresh_run_buttons()

    def toggle_heater(self):
        self.stop_heater() if self.heater_busy else self.start_part('heater')

    def stop_heater(self):
        if getattr(self.controls, 'performance', None) and not self.run_finished:
            self.controls.drive.clear()
        self.controls.heater.clear()
        if self.heater_busy:
            self.heater_readout.set('正在停止加热，等待DC输出关闭确认…')
        self.refresh_run_buttons()

    def toggle_acquisition(self):
        self.stop_acquisition() if self.acquisition_busy else self.start_part('acquisition')

    def next_stage(self):
        performance = getattr(self.controls, 'performance', None)
        if performance and performance.ready.is_set() and not self.run_finished:
            if messagebox.askokcancel('结束当前测试阶段','确认结束当前测试阶段并放行后续阶段？'):
                performance.advance()

    def toggle_drive(self):
        self.stop_drive() if self.drive_busy else self.start_part('drive')

    def rollback_stage(self):
        if (not self.drive_busy or getattr(self,'drive_source',None)!='profile' or
                self.off_failed or self.closing or getattr(self,'rollback_pending',False)):
            self.report_status('当前不能回退：请在驱动方案正常运行且未关断时操作。','warning')
            return
        selected=self.profile_table.selection()
        if not selected:
            self.report_status('请先在驱动方案表格中选择要回退的阶段。','warning')
            return
        index=int(self.profile_table.item(selected[0],'values')[0])-1
        self.controls.rollback(index)

    def toggle_profile(self):
        self.stop_drive() if self.drive_busy else self.start_part('drive', use_profile=True)

    def start_part(self, part, use_profile=False):
        points = None
        performance = getattr(self.controls, 'performance', None)
        if not self.run_finished and performance and performance.phase not in ('aborted','complete'):
            notices.warning(self, '性能点测试中', '请使用“下一阶段”或停止整轮驱动。')
            return
        if self.scanning:
            return
        if part == 'heater' and self.heater_link.get() and not (self.controls.drive.is_set() and self.controls.drive_ready.is_set()):
            self.heater_readout.set('加热联动保护：请先开启7722交流驱动输出。')
            return
        if self.closing or (self.worker and self.worker.is_alive() and self.run_finished):
            return
        if not self.run_finished and not (self.acquisition_busy or self.drive_busy or self.heater_busy):
            return
        if part in ('drive', 'heater') and self.off_failed:
            notices.error(self, '输出状态未确认', '请手动确认电源输出已关闭，检查通信后重启软件。')
            return
        if getattr(self, '_parameter_confirmation_open', False):
            return
        new_session = self.run_finished
        validating_profile = False
        try:
            if part == 'heater':
                self.variables['heater_enabled'][0].set('True')
            cfg = self.collect() if new_session else self.cfg
            if part == 'drive' and use_profile:
                from plan_io import points_from_drive
                source_path = Path(self.variables['profile_file'][0].get())
                source_path = source_path if source_path.is_absolute() else BASE/source_path
                validating_profile = True
                candidate = load_profile(source_path, cfg)
                if candidate.requires_performance:
                    if not new_session:
                        raise ValueError('含测试阶段的驱动方案须先停止全部设备再开始')
                    legacy_path = BASE/'performance_points.ini'
                    legacy = legacy_path.read_text(encoding='utf-8-sig') if legacy_path.exists() else ''
                    points = points_from_drive(source_path.read_text(encoding='utf-8-sig'), cfg, legacy)
                validating_profile = False
            if points:
                cfg = replace(cfg, heater_enabled=True, heater_control='power',
                              heater_power=points[0].heater_w, heater_temperature_enabled=False)
                cfg.validate()
                if len({p.strip().upper() for p in (cfg.power_port, cfg.heater_port, cfg.meter_port)}) != 3:
                    raise ValueError('性能点测试的两台电源与2700必须使用三个不同串口')
            if part == 'heater':
                if cfg.heater_port.strip().upper() == cfg.meter_port.strip().upper():
                    raise ValueError('加热会自动启动2700采集，加热电源与2700不能使用同一串口。')
                if self.acquisition_busy and not self.controls.acquisition.is_set():
                    raise ValueError('请等待2700停止清理完成后再启动加热。')
            requested_port = {'heater': cfg.heater_port, 'drive': cfg.power_port, 'acquisition': cfg.meter_port}[part].strip().upper()
            for other, port in (('heater', cfg.heater_port), ('drive', cfg.power_port), ('acquisition', cfg.meter_port)):
                if other != part and getattr(self, other+'_busy') and requested_port == port.strip().upper():
                    raise ValueError('该串口已由另一设备使用。单台7722测试时请只启动加热。')
            path = Path(self.variables['profile_file'][0].get())
            path = path if path.is_absolute() else BASE/path
            validating_profile = part == 'drive' and use_profile
            profile = load_profile(path, cfg) if validating_profile else None
            if part == 'drive' and not use_profile:
                profile = ask_manual_drive(self, cfg)
                if profile is None:
                    return
            if profile and profile.requires_performance and not points:
                raise ValueError('测试阶段参数缺失，请在驱动方案表格中填写加热与完成条件')
            if profile:
                profile.validate(cfg)
            if points:
                PerformanceRun(points).validate_profile(profile)
            validating_profile = False
            if profile and profile.requires_temperature and not points and not self.controls.acquisition.is_set():
                raise ValueError('温度驱动方案需要先启动2700采集；单独测试请使用时间方案。')
            if part in ('drive', 'heater'):
                self._parameter_confirmation_open = True
                try:
                    confirmed = confirm_parameters(self, part, cfg)
                finally:
                    self._parameter_confirmation_open = False
                if not confirmed:
                    return
                if self.closing or self.off_failed or self.run_finished != new_session or (not new_session and self.stop_event.is_set()):
                    raise ValueError('运行状态已变化，请重新点击开始并复核参数。')
                if part == 'heater' and self.heater_link.get() and not self.controls.heater_allowed():
                    raise ValueError('交流驱动已停止，请重新确认加热联动状态。')
                if profile and profile.requires_temperature and not points and not self.controls.acquisition.is_set():
                    raise ValueError('2700采集已停止，请先重新启动采集。')
            if new_session:
                save_config(self.path, cfg)
        except Exception as exc:
            if validating_profile:
                self.report_status('方案不可运行：'+str(exc), 'error', source='profile')
            else:
                notices.error(self, '无法启动', str(exc))
            return
        if new_session:
            self.cfg = cfg
            self.controls = RunControls(heater_link=self.heater_link.get())
            self.controls.ui_heartbeat = time.monotonic()
            if points:
                self.controls.performance = PerformanceRun(points)
                self.controls.start_acquisition()
                self.acquisition_busy = True
            self.stop_event.clear()
            self.plot_session_id = getattr(self, 'plot_session_id', 0)+1
            self.history = {}
            self.power_history.clear()
            self.update_drive_readout()
            self.update_heater_readout()
            self.update_reference_readout()
            for item in self.table.get_children():
                self.table.set(item, 'temp', '--')
                self.table.set(item, 'resistance', '--')
            self.run_finished = False
            self.draw()
        elif part == 'drive' and not use_profile:
            self.controls.performance = None
        if part == 'acquisition':
            self.update_reference_readout()
            self.acquisition_busy = True
            self.controls.start_acquisition()
        elif part == 'heater':
            self.update_heater_readout()
            if not self.controls.acquisition.is_set():
                self.update_reference_readout()
                self.acquisition_busy = True
                self.controls.start_acquisition()
                self.acquisition_status.set('启动加热前，正在自动启动2700采集…')
            self.heater_busy = True
            self.controls.start_heater()
        else:
            self.drive_busy = True
            self.drive_source = 'profile' if use_profile else 'manual'
            self.controls.drive_source = self.drive_source
            if not use_profile:
                self.performance_status.set('手动驱动：未运行性能点测试。')
                self.performance_stats.set('')
            self.active_profile = profile
            self.controls.start_drive(profile)
            self.heater_link.set(True)
            self.controls.heater_link.set()
        target_status = {'acquisition': self.acquisition_status, 'drive': self.drive_status, 'heater': self.heater_readout}[part]
        target_status.set({'acquisition': '正在启动2700采集…', 'drive': '正在启动7722驱动方案…' if use_profile else '正在启动7722手动驱动…', 'heater': '正在启动7722加热负载（DC）…'}[part])
        self.status.set(target_status.get())
        if new_session:
            self.worker = threading.Thread(target=run, args=(self.cfg, self.stop_event, lambda kind, value: self.events.put((kind, value))),
                                           kwargs={'controls': self.controls}, daemon=False)
            try:
                self.worker.start()
            except Exception as exc:
                self.worker = None
                self.run_finished = True
                self.acquisition_busy = self.drive_busy = self.heater_busy = False
                self.controls.acquisition.clear()
                self.controls.drive.clear()
                self.controls.heater.clear()
                target_status.set('启动失败：'+str(exc))
                notices.error(self, '启动失败', str(exc))
        self.refresh_run_buttons()

    def stop_drive(self):
        if getattr(self.controls, 'performance', None) and not self.run_finished:
            self.controls.heater.clear()
        self.controls.drive.clear()
        if self.heater_link.get() and self.heater_busy:
            self.stop_heater()
        if self.drive_busy:
            self.drive_status.set('正在停止驱动，等待电源输出关闭确认…')
            self.status.set(self.drive_status.get())
        self.refresh_run_buttons()

    def stop_acquisition(self):
        if getattr(self.controls, 'performance', None) and not self.run_finished:
            self.stop()
            return
        if self.heater_busy or self.controls.heater.is_set() or self.controls.heater_pending.is_set():
            self.acquisition_status.set('加热启动或运行中，采集已锁定；请先停止加热并确认输出关闭。')
            self.refresh_run_buttons()
            return
        self.heater_link.set(True)
        self.controls.heater_link.set()
        self.controls.acquisition.clear()
        if self.acquisition_busy:
            self.acquisition_status.set('正在停止采集，等待2700清理…')
        if self.active_profile and self.active_profile.requires_temperature and self.drive_busy:
            self.controls.drive.clear()
            self.drive_status.set('温度方案：采集停止，正在同步停止驱动…')
            self.status.set('正在停止采集；温度驱动方案也将同步停止。')
        elif self.acquisition_busy:
            self.status.set('正在停止采集；时间驱动方案可继续。')
        self.refresh_run_buttons()

    def stop(self):
        self.stop_event.set()
        self.controls.heater.clear()
        self.controls.acquisition.clear()
        self.controls.drive.clear()
        if not self.run_finished:
            if self.acquisition_busy:
                self.acquisition_status.set('停止请求：电源关断确认完成前，2700继续采集。')
            if self.heater_busy:
                self.heater_readout.set('正在停止加热，等待DC输出关闭确认…')
            if self.drive_busy:
                self.drive_status.set('正在停止驱动，等待电源关断确认…')
            self.status.set('正在请求停止：先确认加热OFF，再关闭交流驱动；期间2700继续采集。')
        self.refresh_run_buttons()

    def refresh_run_buttons(self):
        live = bool(self.worker and self.worker.is_alive())
        blocked = (self.scanning or live or not self.run_finished or self.closing or self.off_failed)
        state = 'disabled' if blocked else 'normal'
        self.scan_button.configure(state=state)
        self.advance_button.configure(state=state)
        self.data_directory_entry.configure(state=state)
        self.choose_data_directory_button.configure(state=state)
        settling = not (self.acquisition_busy or self.drive_busy or self.heater_busy) and (not self.run_finished or live)
        for part, busy, button in (('acquisition', self.acquisition_busy, self.acquisition_button),
                                   ('drive', self.drive_busy, self.drive_button),
                                   ('heater', self.heater_busy, self.heater_button)):
            requested = getattr(self.controls, part).is_set()
            label = {'acquisition': '采集', 'drive': '驱动', 'heater': '加热'}[part]
            text = ('停止'+label if requested else '正在停止'+label+'…') if busy else '开始'+label
            blocked = self.scanning or self.closing or settling or (busy and not requested) or (part in ('drive', 'heater') and self.off_failed)
            if part == 'acquisition' and busy and (self.heater_busy or self.controls.heater.is_set() or self.controls.heater_pending.is_set()):
                blocked = True
            if part == 'heater' and not busy and self.heater_link.get() and not self.controls.heater_allowed():
                blocked = True
            button.configure(text='输出状态未确认' if part in ('drive', 'heater') and self.off_failed else text,
                             state='disabled' if blocked else 'normal')
        # The starts are separate; both use the same ordered stop/cleanup state.
        if hasattr(self, 'run_profile_button'):
            text = ('停止驱动方案' if self.controls.drive.is_set() else '正在停止驱动方案…') if self.drive_busy else '运行驱动方案'
            self.run_profile_button.configure(text='输出状态未确认' if self.off_failed else text,
                                              state=self.drive_button.cget('state'))
            if self.drive_busy and getattr(self,'drive_source',None)=='manual':
                text = '手动驱动运行中' if self.controls.drive.is_set() else '正在停止手动驱动…'
                self.run_profile_button.configure(text='输出状态未确认' if self.off_failed else text,state='disabled')
        for combo in self.measurement_combos.values():
            combo.configure(state='disabled' if live or not self.run_finished else 'readonly')
        if hasattr(self,'rollback_button'):
            allowed=(self.drive_busy and getattr(self,'drive_source',None)=='profile' and
                     self.controls.drive.is_set() and not self.off_failed and not self.closing and
                     not getattr(self,'rollback_pending',False))
            self.rollback_button.configure(state='normal' if allowed else 'disabled')
