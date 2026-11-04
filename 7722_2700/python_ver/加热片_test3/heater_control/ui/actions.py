"""Run-control actions mixin."""
import threading
from pathlib import Path
from tkinter import messagebox

from core import Config, run, save_config
from drive_profile import load_profile
from runtime import RunControls
from ui.constants import BASE


class ActionsMixin:
    def idle(self):
        if self.worker and self.worker.is_alive():
            messagebox.showwarning('运行中', '请先停止，再修改或保存配置。')
            return False
        return True

    def drive_idle(self):
        if self.drive_busy:
            messagebox.showwarning('驱动运行中', '请先停止驱动，再切换或编辑方案；采集可以继续。')
            return False
        return True

    def save(self):
        if self.idle():
            try:
                self.cfg = self.collect()
                save_config(self.path, self.cfg)
                self.preview_profile()
                self.status.set('配置已保存。')
            except Exception as exc:
                messagebox.showerror('配置错误', str(exc))

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
        self.controls.heater.clear()
        if self.heater_busy:
            self.heater_readout.set('正在停止加热，等待DC输出关闭确认…')
        self.refresh_run_buttons()

    def toggle_acquisition(self):
        self.stop_acquisition() if self.acquisition_busy else self.start_part('acquisition')

    def toggle_drive(self):
        self.stop_drive() if self.drive_busy else self.start_part('drive')

    def start_part(self, part):
        if part == 'heater' and self.heater_link.get() and not (self.controls.drive.is_set() and self.controls.drive_ready.is_set()):
            self.heater_readout.set('加热联动保护：请先开启7722交流驱动输出。')
            return
        if self.closing or (self.worker and self.worker.is_alive() and self.run_finished):
            return
        if not self.run_finished and not (self.acquisition_busy or self.drive_busy or self.heater_busy):
            return
        if part in ('drive', 'heater') and self.off_failed:
            messagebox.showerror('输出状态未确认', '请手动确认电源输出已关闭，检查通信后重启软件。')
            return
        new_session = self.run_finished
        try:
            if part == 'heater':
                self.variables['heater_enabled'][0].set('True')
            cfg = self.collect() if new_session else self.cfg
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
            profile = load_profile(path, cfg) if part == 'drive' else None
            if profile and profile.strategy == 'temperature' and not self.controls.acquisition.is_set():
                raise ValueError('温度驱动方案需要先启动2700采集；单独测试请使用时间方案。')
            if new_session:
                save_config(self.path, cfg)
        except Exception as exc:
            messagebox.showerror('无法启动', str(exc))
            return
        if new_session:
            self.cfg = cfg
            self.controls = RunControls(heater_link=self.heater_link.get())
            self.stop_event.clear()
            self.history = {}
            self.power_history.clear()
            self.readout.set('设定电压 -- V    实测电压 -- V    电流 -- A    功率 -- W')
            for item in self.table.get_children():
                self.table.set(item, 'temp', '--')
                self.table.set(item, 'resistance', '--')
            self.run_finished = False
            self.draw()
        if part == 'acquisition':
            self.acquisition_busy = True
            self.controls.start_acquisition()
        elif part == 'heater':
            if not self.controls.acquisition.is_set():
                self.acquisition_busy = True
                self.controls.start_acquisition()
                self.acquisition_status.set('启动加热前，正在自动启动2700采集…')
            self.heater_busy = True
            self.controls.start_heater()
        else:
            self.drive_busy = True
            self.active_profile = profile
            self.controls.start_drive(profile)
            self.heater_link.set(True)
            self.controls.heater_link.set()
        target_status = {'acquisition': self.acquisition_status, 'drive': self.drive_status, 'heater': self.heater_readout}[part]
        target_status.set({'acquisition': '正在启动2700采集…', 'drive': '正在启动7722交流驱动方案…', 'heater': '正在启动7722加热负载（DC）…'}[part])
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
                messagebox.showerror('启动失败', str(exc))
        self.refresh_run_buttons()

    def stop_drive(self):
        self.controls.drive.clear()
        if self.heater_link.get() and self.heater_busy:
            self.stop_heater()
        if self.drive_busy:
            self.drive_status.set('正在停止驱动，等待电源输出关闭确认…')
            self.status.set(self.drive_status.get())
        self.refresh_run_buttons()

    def stop_acquisition(self):
        if self.heater_busy or self.controls.heater.is_set() or self.controls.heater_pending.is_set():
            self.acquisition_status.set('加热启动或运行中，采集已锁定；请先停止加热并确认输出关闭。')
            self.refresh_run_buttons()
            return
        self.heater_link.set(True)
        self.controls.heater_link.set()
        self.controls.acquisition.clear()
        if self.acquisition_busy:
            self.acquisition_status.set('正在停止采集，等待2700清理…')
        if self.active_profile and self.active_profile.strategy == 'temperature' and self.drive_busy:
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
                self.acquisition_status.set('正在停止采集，等待2700清理…')
            if self.heater_busy:
                self.heater_readout.set('正在停止加热，等待DC输出关闭确认…')
            if self.drive_busy:
                self.drive_status.set('正在停止驱动，等待电源关断确认…')
            self.status.set('正在停止采集与驱动，等待通信与设备清理…')
        self.refresh_run_buttons()

    def refresh_run_buttons(self):
        live = bool(self.worker and self.worker.is_alive())
        settling = not (self.acquisition_busy or self.drive_busy or self.heater_busy) and (not self.run_finished or live)
        for part, busy, button in (('acquisition', self.acquisition_busy, self.acquisition_button),
                                   ('drive', self.drive_busy, self.drive_button),
                                   ('heater', self.heater_busy, self.heater_button)):
            requested = getattr(self.controls, part).is_set()
            label = {'acquisition': '采集', 'drive': '驱动', 'heater': '加热'}[part]
            text = ('停止'+label if requested else '正在停止'+label+'…') if busy else '开始'+label
            blocked = self.closing or settling or (busy and not requested) or (part in ('drive', 'heater') and self.off_failed)
            if part == 'acquisition' and busy and (self.heater_busy or self.controls.heater.is_set() or self.controls.heater_pending.is_set()):
                blocked = True
            if part == 'heater' and not busy and self.heater_link.get() and not self.controls.heater_allowed():
                blocked = True
            button.configure(text='输出状态未确认' if part in ('drive', 'heater') and self.off_failed else text,
                             state='disabled' if blocked else 'normal')
        self.plot_stop_drive.configure(state='normal' if self.drive_busy and self.controls.drive.is_set() else 'disabled')
        self.plot_stop_acquisition.configure(state='normal' if self.acquisition_busy and self.controls.acquisition.is_set() and not (self.heater_busy or self.controls.heater.is_set() or self.controls.heater_pending.is_set()) else 'disabled')
        for combo in self.measurement_combos.values():
            combo.configure(state='disabled' if live or not self.run_finished else 'readonly')
