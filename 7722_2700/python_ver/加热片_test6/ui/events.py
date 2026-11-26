from ui import notices
"""Event-polling and event-handling mixin."""
import math
import queue
import time
from collections import deque
from tkinter import messagebox


class EventsMixin:
    def update_reference_readout(self, temperature=None, status='--'):
        """Use the session configuration and the reference channel's latest Celsius sample."""
        latest = f'{temperature+273.15:.3f}' if temperature is not None and math.isfinite(temperature) else status
        self.reference_readout_table.item('latest', values=(self.cfg.reference_channel,
            f'{self.cfg.heater_trigger_temperature+273.15:.3f}', latest))

    def update_heater_readout(self, voltage=None, current=None, power=None):
        """Replace the heater's single latest-reading row with its own readings."""
        values = tuple(f'{value:.{digits}f}' if value is not None else '--'
                       for value, digits in ((voltage, 3), (current, 4), (power, 3)))
        self.heater_readout_table.item('latest', values=values)

    def update_drive_readout(self, voltage=None, current=None, power=None):
        """Replace the single latest-reading row; retain text for the curve window."""
        values = tuple(f'{value:.{digits}f}' if value is not None else '--'
                       for value, digits in ((voltage, 3), (current, 4), (power, 3)))
        self.drive_readout_table.item('latest', values=values)
        self.readout.set(f'实测电压 {values[0]} V    实测电流 {values[1]} A    实测功率 {values[2]} W')

    def report_status(self, text, level='status', source='notice', record=True):
        from ui.notices import StatusCenter
        if not hasattr(self, 'status_center'):
            self.status_center = StatusCenter()
        if level == 'status' and any(word in str(text) for word in ('失败','错误','异常','越限','无效','失效','故障','未确认')):
            level = 'error'
        rendered = self.status_center.update(source,text,level)
        if hasattr(self, 'system_text'):
            self.system_text.set(rendered)
        if hasattr(self, 'status_view'):
            self.status_view.display(rendered, alarm=bool(self.status_center.alert))
        if record and hasattr(self, 'messages') and text and (not self.messages or self.messages[-1] != str(text)):
            self.messages.append(str(text))

    def bind_status_center(self):
        sources = ((self.status,'notice'), (self.scan_status,'scan'),
                   (self.drive_status,'drive'), (self.acquisition_status,'acquisition'),
                   (self.heater_readout,'heater'), (self.performance_status,'performance'))
        for variable, source in sources:
            variable.trace_add('write',lambda *_, v=variable, name=source:
                               self.report_status(v.get(),source=name))
        # Keep an early configuration error if it happened before widgets existed.
        for variable, source in sources:
            if variable.get():
                self.report_status(variable.get(),source=source)

    def append(self, text):
        if not self.messages or self.messages[-1] != text:
            self.messages.append(text)
        if any(word in str(text) for word in ('失败','错误','越限','无效','失效','故障','未确认')):
            self.report_status(text,'error')

    def poll(self):
        self.controls.ui_heartbeat = time.monotonic()
        try:
            for _ in range(200):
                kind, value = self.events.get_nowait()
                if kind == 'channel_name_error':
                    self.report_status('记录通道名称更新失败：'+value, 'error', source='channel_names')
                elif kind in ('data_folder_opened', 'data_folder_open_failed'):
                    self.open_data_folder_button.configure(state='normal')
                    self.report_status(value, 'error' if kind == 'data_folder_open_failed' else 'status',
                                       source='recording')
                elif kind.startswith('scan_'):
                    self.handle_scan_event(kind, value)
                elif kind == 'manager_started':
                    self.manager_pid = value['pid']
                elif kind == 'manager_failed':
                    self.off_failed = True
                    self.controls.drive_ready.clear()
                    self.report_status(value, 'alarm')
                elif kind == 'manager_state':
                    self.manager_pid = value['pid']
                    self.rollback_pending=value.get('rollback_pending',False)
                elif kind == 'rollback_status':
                    self.report_status(value,source='runtime')
                elif kind == 'drive_clock':
                    from session.timing import DriveClock
                    if not hasattr(self, 'drive_clock'):
                        self.drive_clock = DriveClock()
                    self.drive_clock.update(value)
                elif kind == 'performance_aborted':
                    performance = getattr(self.controls,'performance',None)
                    if performance:
                        performance.phase='aborted'
                        performance.ready.clear()
                    self.performance_status.set(value)
                    self.append(value)
                elif kind == 'performance_stats':
                    if hasattr(self, 'performance_stats'):
                        self.performance_stats.set(value)
                elif kind == 'performance_status':
                    if hasattr(self, 'performance_status'):
                        self.performance_status.set(value)
                    self.status.set(value)
                    self.append(value)
                elif kind == 'measurement_fault':
                    self.update_reference_readout(status='采集失效')
                    self.report_status(value,'alarm')
                    self.acquisition_status.set('采集失效：'+value)
                    if self.drive_busy:
                        self.drive_status.set('测温故障保护：正在关断电源…')
                    for item in self.table.get_children():
                        self.table.set(item, 'temp', '采集失效')
                        self.table.set(item, 'resistance', '采集失效')
                    self.status.set(value)
                    self.append(value)
                elif kind == 'connection_recovered':
                    previous = value.get('previous')
                    if previous and hasattr(self, 'status_center'):
                        for source, (text, _) in list(self.status_center.alerts.items()):
                            if text == previous:
                                self.status_center.alerts.pop(source)
                    variable = {'drive':self.drive_status, 'heater':self.heater_readout,
                                'meter':self.acquisition_status}[value['role']]
                    variable.set(value['text'])
                    self.status.set(value['text'])
                    self.append(value['text'])
                elif kind == 'shutdown_pending':
                    self.report_status(value,'alarm')
                    self.drive_status.set(value)
                    if self.heater_busy:
                        self.heater_readout.set(value)
                    self.off_failed = True
                    self.status.set(value)
                    self.append(value)
                elif kind == 'shutdown_confirmed':
                    self.drive_status.set(value)
                    if self.heater_busy:
                        self.heater_readout.set('加热7722：输出已确认关闭')
                    self.off_failed = False
                    self.status.set(value)
                    self.append(value)
                elif kind == 'acquisition_starting':
                    self.update_reference_readout()
                    self.acquisition_busy = True
                    self.acquisition_status.set(value)
                    self.append(value)
                elif kind == 'heater_starting':
                    self.heater_busy = True
                    self.update_heater_readout()
                    self.heater_readout.set(value)
                    self.append(value)
                elif kind == 'heater_status':
                    self.heater_readout.set(value)
                    self.append(value)
                elif kind in ('status', 'alarm'):
                    if kind == 'alarm':
                        self.report_status(value,'alarm')
                    if self.heater_busy and (kind == 'alarm' or '加热' in value):
                        self.heater_readout.set(value)
                    if '2700' in value or '采集' in value or '通道' in value:
                        self.acquisition_status.set(value)
                    if '7722' in value or '驱动' in value or '电源' in value or kind == 'alarm' and self.drive_busy:
                        self.drive_status.set(value)
                    self.status.set(value)
                    self.append(value)
                elif kind == 'heater_sample':
                    self.update_heater_readout(value['voltage'], value['current'], value['power'])
                    state = '已达标' if value['reached'] else '调节中'
                    unit = 'V' if value['control'] == 'voltage' else 'W'
                    self.heater_readout.set(f"DC 目标 {value['target']:g} {unit}，{state}\n{value['voltage']:.3f} V / {value['current']:.4f} A / {value['power']:.3f} W")
                elif kind == 'sample':
                    reference = self.cfg.reference_channel
                    if reference in value['temperatures']:
                        self.update_reference_readout(value['temperatures'][reference], status='无效/超范围')
                    for c, t in value['temperatures'].items():
                        r = value['resistances'][c]
                        if self.table.exists(str(c)):
                            self.table.set(str(c), 'temp', f'{t+273.15:.3f}' if t is not None else '无效/超范围')
                            self.table.set(str(c), 'resistance', (f'{r:.4f}' if math.isfinite(r) and abs(r) < 1e30 else str(r)) if isinstance(r, (int, float)) else str(r))
                        valid_r = r if isinstance(r, (int, float)) and math.isfinite(r) and abs(r) < 1e30 else None
                        self.history.setdefault(c, deque(maxlen=600)).append((value['sample_elapsed_s'][c], t+273.15 if t is not None else None, valid_r))
                    if value['power'] is not None:
                        self.update_drive_readout(value['voltage'], value['current'], value['power'])
                        self.power_history.append((value['elapsed_min']*60, value['power']))
                    self.draw()
                    if not self.stop_event.is_set():
                        acq_text = '2700采集中' if self.controls.acquisition.is_set() else '2700未采集'
                        if self.controls.acquisition.is_set() and self.heater_busy:
                            acq_text += '（加热中，停止采集已锁定）'
                        if value['power'] is not None and self.controls.drive.is_set():
                            unit = 'V' if value['drive_mode'] == 'voltage' else 'W'
                            drive_text = f"7722驱动 阶段{value['stage']}，{value['drive_target']:g}{unit}"
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
                    self.drive_source = None
                    self.drive_status.set(value['error'] or ('交流驱动已停止，输出已关闭' if value['off_ok'] else '输出未确认关闭'))
                    self.drive_busy = False
                    self.active_profile = None
                    self.off_failed = self.off_failed or not value['off_ok']
                    self.update_drive_readout()
                    self.append(value['error'] or '驱动已停止，电源输出已关闭。')
                elif kind == 'heater_done':
                    self.heater_busy = False
                    self.update_heater_readout()
                    self.off_failed = self.off_failed or not value['off_ok']
                    self.heater_readout.set(value['error'] or ('加热已停止，DC输出已关闭' if value['off_ok'] else '加热关断未确认'))
                    self.append(self.heater_readout.get())
                elif kind == 'acquisition_done':
                    self.update_reference_readout()
                    self.acquisition_status.set(value['error'] or '2700采集已停止')
                    self.acquisition_busy = False
                    self.append(value['error'] or '2700采集已停止。')
                elif kind == 'done':
                    self.update_reference_readout()
                    if getattr(self.controls, 'performance', None) and hasattr(self, 'performance_status'):
                        self.performance_status.set(value['error'] or '性能点测试已结束。')
                    self.run_finished = True
                    self.acquisition_busy = self.drive_busy = self.heater_busy = False
                    self.active_profile = None
                    self.off_failed = self.off_failed or not value['off_ok']
                    self.status.set(value['error'] or '采集与驱动均已停止。')
                    self.append(self.status.get())
        except queue.Empty:
            pass
        if hasattr(self, 'drive_clock'):
            clock_text = self.drive_clock.text()
            if getattr(self,'drive_source',None)=='manual':
                clock_text = clock_text.replace('方案运行时间','手动驱动时间')
            if clock_text != getattr(self, '_clock_text', None):
                self._clock_text = clock_text
                self.report_status(clock_text,source='runtime',record=False)
        self.refresh_run_buttons()
        self.poll_curves()
        self.retry_discovery()
        if self.closing and (not self.worker or not self.worker.is_alive()):
            if self.off_failed and not getattr(self, 'scan_uncertain', {}):
                self.closing = False
                notices.error(self, '输出状态未确认', '请手动关闭电源并检查通信；窗口保留报警。')
            else:
                self.destroy()
                return
        self.after(100, self.poll)
