"""Event-polling and event-handling mixin."""
import math
import queue
from collections import deque
from tkinter import messagebox


class EventsMixin:
    def append(self, text):
        self.messages.append(text)

    def poll(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == 'measurement_fault':
                    self.acquisition_status.set('采集失效：'+value)
                    if self.drive_busy:
                        self.drive_status.set('测温故障保护：正在关断电源…')
                    for item in self.table.get_children():
                        self.table.set(item, 'temp', '采集失效')
                        self.table.set(item, 'resistance', '采集失效')
                    self.status.set(value)
                    self.append(value)
                elif kind == 'shutdown_pending':
                    self.drive_status.set(value)
                    if self.heater_busy:
                        self.heater_readout.set(value)
                    self.off_failed = True
                    self.status.set(value)
                    self.readout.set('7722输出未确认关闭；后台持续关断重试，请手动关闭电源')
                    self.append(value)
                elif kind == 'shutdown_confirmed':
                    self.drive_status.set(value)
                    if self.heater_busy:
                        self.heater_readout.set('加热7722：输出已确认关闭')
                    self.off_failed = False
                    self.status.set(value)
                    self.append(value)
                elif kind == 'acquisition_starting':
                    self.acquisition_busy = True
                    self.acquisition_status.set(value)
                    self.append(value)
                elif kind == 'heater_starting':
                    self.heater_busy = True
                    self.heater_readout.set(value)
                    self.append(value)
                elif kind == 'heater_status':
                    self.heater_readout.set(value)
                    self.append(value)
                elif kind in ('status', 'alarm'):
                    if self.heater_busy and (kind == 'alarm' or '加热' in value):
                        self.heater_readout.set(value)
                    if '2700' in value or '采集' in value or '通道' in value:
                        self.acquisition_status.set(value)
                    if '7722' in value or '驱动' in value or '电源' in value or kind == 'alarm' and self.drive_busy:
                        self.drive_status.set(value)
                    self.status.set(value)
                    self.append(value)
                elif kind == 'heater_sample':
                    state = '已达标' if value['reached'] else '调节中'
                    unit = 'V' if value['control'] == 'voltage' else 'W'
                    self.heater_readout.set(f"DC 目标 {value['target']:g} {unit}，{state}\n{value['voltage']:.3f} V / {value['current']:.4f} A / {value['power']:.3f} W")
                elif kind == 'sample':
                    for c, t in value['temperatures'].items():
                        r = value['resistances'][c]
                        if self.table.exists(str(c)):
                            self.table.set(str(c), 'temp', f'{t+273.15:.3f}' if t is not None else '无效/超范围')
                            self.table.set(str(c), 'resistance', (f'{r:.4f}' if math.isfinite(r) and abs(r) < 1e30 else str(r)) if isinstance(r, (int, float)) else str(r))
                        valid_r = r if isinstance(r, (int, float)) and math.isfinite(r) and abs(r) < 1e30 else None
                        self.history.setdefault(c, deque(maxlen=600)).append((value['sample_elapsed_s'][c], t+273.15 if t is not None else None, valid_r))
                    if value['power'] is not None:
                        self.readout.set(f"设定电压 {value['setting']:.3f} V    实测电压 {value['voltage']:.3f} V    电流 {value['current']:.4f} A    功率 {value['power']:.3f} W")
                        self.power_history.append((value['elapsed_min']*60, value['power']))
                    self.draw()
                    if not self.stop_event.is_set():
                        acq_text = '2700采集中' if self.controls.acquisition.is_set() else '2700未采集'
                        if self.controls.acquisition.is_set() and self.heater_busy:
                            acq_text += '（加热中，停止采集已锁定）'
                        if value['power'] is not None and self.controls.drive.is_set():
                            unit = 'V' if value['drive_mode'] == 'voltage' else 'W'
                            drive_text = f"7722驱动 {value['drive_elapsed_min']:.2f}分钟，阶段{value['stage']}，{value['drive_target']:g}{unit}"
                        else:
                            drive_text = '7722未驱动'
                        errors = value.get('measurement_errors', {})
                        warning = '；'+'、'.join(f'{ch}: {msg}' for ch, msg in errors.items()) if errors else ''
                        if self.controls.acquisition.is_set():
                            self.acquisition_status.set(acq_text+warning)
                        if self.controls.drive.is_set():
                            self.drive_status.set(drive_text)
                        self.status.set(acq_text+'；'+drive_text+warning)
                elif kind == 'drive_done':
                    self.drive_status.set(value['error'] or ('交流驱动已停止，输出已关闭' if value['off_ok'] else '输出未确认关闭'))
                    self.drive_busy = False
                    self.active_profile = None
                    self.off_failed = self.off_failed or not value['off_ok']
                    self.readout.set('7722已停止；输出未确认关闭' if not value['off_ok'] else '7722输出已关闭；实时电源读数 --')
                    self.append(value['error'] or '驱动已停止，电源输出已关闭。')
                elif kind == 'heater_done':
                    self.heater_busy = False
                    self.off_failed = self.off_failed or not value['off_ok']
                    self.heater_readout.set(value['error'] or ('加热已停止，DC输出已关闭' if value['off_ok'] else '加热关断未确认'))
                    self.append(self.heater_readout.get())
                elif kind == 'acquisition_done':
                    self.acquisition_status.set(value['error'] or '2700采集已停止')
                    self.acquisition_busy = False
                    self.append(value['error'] or '2700采集已停止。')
                elif kind == 'done':
                    self.run_finished = True
                    self.acquisition_busy = self.drive_busy = self.heater_busy = False
                    self.active_profile = None
                    self.off_failed = self.off_failed or not value['off_ok']
                    self.status.set(value['error'] or '采集与驱动均已停止。')
                    self.append(self.status.get())
        except queue.Empty:
            pass
        self.refresh_run_buttons()
        if self.closing and (not self.worker or not self.worker.is_alive()):
            if self.off_failed:
                self.closing = False
                messagebox.showerror('输出状态未确认', '请手动关闭电源并检查通信；窗口保留报警。')
            else:
                self.destroy()
                return
        self.after(100, self.poll)
