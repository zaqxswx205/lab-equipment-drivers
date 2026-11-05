"""Background identity discovery; all widget updates stay on the Tk thread."""
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from device_scan import scan_port


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
            messagebox.showerror('通信参数错误', str(exc))
            return
        self.scanning = True
        self.refresh_run_buttons()
        self.scan_status.set('正在读取串口设备身份…')
        win = tk.Toplevel(self)
        win.title('串口设备识别结果')
        win.geometry('950x380')
        ttk.Label(win, text='按当前高级设置查询 *IDN?；唯一的2700自动填入。7722请选择用途后应用；不会开启输出。', padding=10).pack(fill='x')
        self.scan_table = ttk.Treeview(win, columns=('port', 'role', 'identity', 'profile'), show='headings')
        for key, label, width in [('port', '串口', 80), ('role', '设备类型', 100), ('identity', '身份 / 未识别原因', 480), ('profile', '通信参数', 180)]:
            self.scan_table.heading(key, text=label)
            self.scan_table.column(key, width=width)
        self.scan_table.pack(fill='both', expand=True, padx=10, pady=10)
        self.scan_results = {}
        buttons = ttk.Frame(win, padding=10)
        buttons.pack(fill='x')
        for key, label in [('power_port', '选中设备用于交流驱动'), ('heater_port', '选中设备用于加热'), ('meter_port', '选中设备用于采集')]:
            ttk.Button(buttons, text=label, command=lambda k=key: self.apply_scan_selection(k)).pack(side='left', padx=4)

        def worker():
            try:
                from serial.tools import list_ports
                results = []
                for port in sorted(list_ports.comports(), key=lambda p: p.device):
                    record = (port.device, *scan_port(port.device, profiles))
                    results.append(record)
                    self.events.put(('scan_record', record))
                self.events.put(('scan_done', results))
            except Exception as exc:
                self.events.put(('scan_error', str(exc)))
        threading.Thread(target=worker, daemon=True).start()

    def apply_scan_selection(self, key):
        if self.scanning or not self.idle():
            return
        selected = self.scan_table.selection()
        if not selected:
            return
        record = self.scan_results[selected[0]]
        expected = '采集设备' if key == 'meter_port' else '7722电源'
        if record[1] != expected:
            messagebox.showwarning('设备类型不匹配', '请选择'+expected+'。')
            return
        self.apply_scan_record(key, record)
        self.scan_status.set(f'{record[0]} 已填入；请核对用途并保存配置。')

    def apply_scan_record(self, key, record):
        name, _, _, profile = record
        self.variables[key][0].set(name)
        self.variables[key.replace('_port', '_baud')][0].set(str(profile['baud']))
        if key == 'meter_port':
            self.variables['meter_flow'][0].set(str(profile['flow'] == 'xon'))

    def handle_scan_event(self, kind, value):
        if kind == 'scan_record':
            name, role, identity, profile = value
            if self.scan_table.winfo_exists():
                summary = f"{profile['baud']} / 8N1 / {profile['flow']}" if profile else ''
                item = self.scan_table.insert('', 'end', values=(name, role, identity, summary))
                self.scan_results[item] = value
            self.scan_status.set(f'已扫描 {name}：{role}')
        else:
            self.scanning = False
            if kind == 'scan_done':
                self.refresh_ports()
                meters = [row for row in value if row[1] == '采集设备']
                if len(meters) == 1:
                    self.apply_scan_record('meter_port', meters[0])
                self.scan_status.set(f'识别完成：共{len(value)}个串口；唯一2700已填入，7722请选用途。')
            else:
                self.scan_status.set('扫描失败：'+value)
            self.refresh_run_buttons()
