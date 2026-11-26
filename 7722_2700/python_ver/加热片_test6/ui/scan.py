from ui import notices
"""Background identity discovery; all widget updates stay on the Tk thread."""
import time
import tkinter as tk
from tkinter import messagebox, ttk

from communication.discovery import DiscoveryJob


def selected_device_description(record, key):
    port, role, identity, profile = record
    purpose = {'power_port': '7722交流驱动（AC）', 'heater_port': '7722加热负载（DC）',
               'meter_port': '2700采集'}.get(key, key)
    baud = f"；{profile['baud']} baud" if profile and 'baud' in profile else ''
    return f'{port} → {purpose}；设备身份：{identity}（{role}）{baud}'


class ScanMixin:
    def scan_devices(self):
        if self.scanning or self.closing or self.off_failed or not self.run_finished or (self.worker and self.worker.is_alive()):
            return
        try:
            profiles = [dict(baud=int(self.variables[key][0].get()), bits=8, parity='N', stop=1,
                             flow='xon' if key == 'meter_baud' and self.variables['meter_flow'][0].get() == 'True' else 'none')
                        for key in ('meter_baud', 'power_baud', 'heater_baud')]
            if any(p['baud'] <= 0 for p in profiles):
                raise ValueError('波特率须为正整数')
        except ValueError as exc:
            notices.error(self, '通信参数错误', str(exc))
            return
        self.scanning = True
        self.refresh_run_buttons()
        self.scan_status.set('正在读取串口设备身份…')
        win = tk.Toplevel(self)
        win.title('串口设备识别结果')
        win.geometry('950x380')
        ttk.Label(win, text='按当前高级设置查询 *IDN?；唯一的2700自动填入。指定7722用途时关闭输出，并切到AC（交流驱动）或DC（加热），请观察面板。', padding=10).pack(fill='x')
        self.scan_table = ttk.Treeview(win, columns=('port', 'role', 'identity', 'profile'), show='headings')
        for key, label, width in [('port', '串口', 80), ('role', '设备类型', 100), ('identity', '身份 / 未识别原因', 480), ('profile', '通信参数', 180)]:
            self.scan_table.heading(key, text=label)
            self.scan_table.column(key, width=width)
        self.scan_table.pack(fill='both', expand=True, padx=10, pady=10)
        self.scan_results = {}
        self.scan_known = {}
        self.scan_retry_ports = set()
        self.scan_panel_retries = {}
        self.scan_uncertain = {}
        self.scan_profiles = profiles
        buttons = ttk.Frame(win, padding=10)
        buttons.pack(fill='x')
        for key, label in [('power_port', '指定交流驱动（AC，输出关）'), ('heater_port', '指定加热（DC，输出关）'), ('meter_port', '选中设备用于采集')]:
            ttk.Button(buttons, text=label, command=lambda k=key: self.apply_scan_selection(k)).pack(side='left', padx=4)

        self.discovery_job = DiscoveryJob('scan', lambda k,v: self.events.put((k,v)), profiles=profiles)
        self.discovery_job.start()

    def apply_scan_selection(self, key):
        if self.scanning or self.closing or self.off_failed or not self.run_finished or not self.idle():
            return
        selected = self.scan_table.selection()
        if not selected:
            return
        record = self.scan_results[selected[0]]
        expected = '采集设备' if key == 'meter_port' else '7722电源'
        if record[1] != expected:
            notices.warning(self, '设备类型不匹配', selected_device_description(record,key)+'；请选择'+expected+'。')
            return
        if key == 'meter_port':
            self.apply_scan_record(key, record)
            self.scan_status.set(selected_device_description(record,key)+'；已填入，请保存配置。')
            return
        try:
            timeout = float(self.variables['timeout'][0].get())
            import math
            if not math.isfinite(timeout) or timeout <= 0:
                raise ValueError('通信超时须为有限正数')
        except ValueError as exc:
            notices.error(self, '通信参数错误', selected_device_description(record,key)+'；'+str(exc))
            return
        mode = 'AC' if key == 'power_port' else 'DC'
        self.scanning = True
        self.refresh_run_buttons()
        self.scan_status.set(selected_device_description(record,key)+'；正在关闭输出并确认用途…')
        self.start_panel_job(key, record, mode, timeout)

    def start_panel_job(self, key, record, mode, timeout):
        self.scan_panel_timeout = timeout
        self.discovery_job = DiscoveryJob('panel', lambda k,v: self.events.put((k,v)),
                                         key=key, record=record, mode=mode, timeout=timeout)
        self.discovery_job.start()

    def retry_discovery(self):
        """Retry only while serial ports cannot be owned by a running session."""
        if (self.closing or self.scanning or not self.run_finished or
                (self.worker and self.worker.is_alive()) or
                time.monotonic() < getattr(self, 'scan_retry_at', float('inf'))):
            return
        pending = getattr(self, 'scan_panel_retries', {})
        ports = getattr(self, 'scan_retry_ports', set())
        if not pending and not ports:
            return
        self.scanning = True
        self.refresh_run_buttons()
        if pending:
            key, record, mode = next(iter(pending.values()))
            self.scan_status.set(f'正在重连 {record[0]}，确认输出关闭后恢复设备选择…')
            self.start_panel_job(key, record, mode, getattr(self, 'scan_panel_timeout', 3.))
        else:
            self.scan_status.set('正在重试离线串口：'+'、'.join(sorted(ports)))
            self.discovery_job = DiscoveryJob('scan', lambda k,v: self.events.put((k,v)),
                                  profiles=self.scan_profiles, only_ports=ports.copy())
            self.discovery_job.start()

    def apply_scan_record(self, key, record):
        name, _, _, profile = record
        self.variables[key][0].set(name)
        self.variables[key.replace('_port', '_baud')][0].set(str(profile['baud']))
        if key == 'meter_port':
            self.variables['meter_flow'][0].set(str(profile['flow'] == 'xon'))

    def handle_scan_event(self, kind, value):
        if kind == 'scan_progress':
            self.scan_status.set(value)
            return
        if kind == 'scan_record':
            name, role, identity, profile = value
            if not hasattr(self, 'scan_known'):
                self.scan_known, self.scan_retry_ports = {}, set()
            self.scan_known[name] = value
            if role == '未识别':
                self.scan_retry_ports.add(name)
            else:
                self.scan_retry_ports.discard(name)
            if self.scan_table.winfo_exists():
                summary = f"{profile['baud']} / 8N1 / {profile['flow']}" if profile else ''
                item = next((item for item,row in self.scan_results.items() if row[0]==name), None)
                if item and self.scan_table.exists(item):
                    self.scan_table.item(item, values=(name, role, identity, summary))
                else:
                    item = self.scan_table.insert('', 'end', values=(name, role, identity, summary))
                self.scan_results[item] = value
            self.scan_status.set(f'已扫描 {name}：{role}')
        else:
            self.scanning = False
            self.scan_retry_at = time.monotonic()+2.
            if kind == 'scan_panel_done':
                key, record, mode = value
                getattr(self, 'scan_panel_retries', {}).pop(record[0], None)
                uncertain = getattr(self, 'scan_uncertain', {})
                if record[0] in uncertain:
                    uncertain.pop(record[0])
                    if not uncertain:
                        self.off_failed = getattr(self, 'scan_prior_off_failed', False)
                self.apply_scan_record(key, record)
                text = selected_device_description(record,key)+f'；已设为{mode}，输出已确认关闭；请查看面板并保存配置。'
                self.scan_status.set(text)
                self.append(text)
                center = getattr(self, 'status_center', None)
                if center:
                    prefix = selected_device_description(record,key)+'；用途指定失败：'
                    for source, (message, _) in list(center.alerts.items()):
                        if message.startswith(prefix):
                            center.alerts.pop(source)
                if not getattr(self, 'scan_panel_retries', {}):
                    self.report_status('', source='discovery', record=False)
            elif kind == 'scan_panel_error':
                error, uncertain = value['error'], value['uncertain']
                record, key = value['record'], value['key']
                if not hasattr(self, 'scan_panel_retries'):
                    self.scan_panel_retries, self.scan_uncertain = {}, {}
                self.scan_panel_retries[record[0]] = (key, record, value['mode'])
                if uncertain:
                    if not self.scan_uncertain:
                        self.scan_prior_off_failed = self.off_failed
                    self.scan_uncertain[record[0]] = record
                self.off_failed = self.off_failed or uncertain
                context = selected_device_description(record,key)
                text = context+'；用途指定失败：'+error
                if uncertain:
                    text += f'；{record[0]}输出关闭未确认，请手动关闭这台电源并检查通信。'
                else:
                    text += f'；{record[0]}输出已确认关闭，未应用本次用途指定。'
                self.scan_status.set(text)
                self.append(text)
                self.report_status(text, 'error', source='discovery')
            elif kind == 'scan_done':
                known = list(getattr(self, 'scan_known', {}).values()) or value
                self.refresh_ports(found=[(row[0], row[2]) for row in known])
                meters = [row for row in known if row[1] == '采集设备']
                if len(meters) == 1:
                    self.apply_scan_record('meter_port', meters[0])
                missing = getattr(self, 'scan_retry_ports', set())
                self.scan_status.set(('离线串口：'+'、'.join(sorted(missing))+'；空闲时自动重试，可关闭窗口。') if missing else
                                     f'识别完成：共{len(known)}个串口；7722可指定用途。')
                if not missing:
                    self.report_status('', source='discovery', record=False)
            elif kind == 'scan_cancelled':
                self.scan_status.set('已取消串口识别。')
            else:
                self.scan_status.set('扫描失败：'+value)
            self.refresh_run_buttons()
