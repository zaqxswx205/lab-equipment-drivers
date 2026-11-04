"""GS610 desktop interface. Run with: python gs610_gui.py"""
import math
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from gs610_sweep import (GS610, SerialInstrument, SweepCancelled, default_result_path,
                         plan_sweep, prompt_seconds, result_path, validate_port)


class DemoInstrument:
    """Explicit simulation for trying the interface without hardware."""
    def __init__(self):
        self.output = False
        self.current = '0'
        self.remote_sense = False
        self.limit = '12'

    def write(self, command):
        if command.startswith(':OUTP '):
            self.output = command == ':OUTP ON'
        elif command.startswith(':SENS:RSEN '):
            self.remote_sense = command.split()[-1] in ('ON', '1')
        elif command.startswith(':SOUR:CURR:LEV '):
            self.current = command.split()[-1]
        elif command.startswith(':SOUR:VOLT:PROT:ULIM '):
            self.limit = command.split()[-1]

    def query(self, command):
        if command == ':SYST:ERR?':
            return '0,"No error"'
        if command == ':OUTP?':
            return str(int(self.output))
        if command == ':SOUR:CURR:LEV?':
            return self.current
        if command == ':SENS:RSEN?':
            return str(int(self.remote_sense))
        if command == ':READ?':
            value = float(self.current) * 2
            return str(max(-float(self.limit), min(float(self.limit), value)))
        return {':SOUR:VOLT:PROT:ULIM?': self.limit,
                ':SOUR:VOLT:PROT:LLIM?': '-' + self.limit,
                ':SOUR:VOLT:PROT:STAT?': '1', ':SENS:FUNC?': 'VOLT',
                ':STAT:SENS:COND?': '0'}[command]


class SweepApp:
    def __init__(self, root):
        self.root = root
        root.title('GS610 · 电流扫描与实时电压采集')
        root.geometry('1180x820')
        root.minsize(1020, 720)
        self.events = queue.Queue()
        self.stop_event = threading.Event()
        self.worker = None
        self.closing = False
        self.records = []
        self.total = 0
        self.settings_widgets = []
        self.values = {}
        self.demo = tk.BooleanVar(value=False)
        self.round_trip = tk.BooleanVar(value=True)
        self.status = tk.StringVar(value='就绪：设置参数后开始采集')
        self.voltage = tk.StringVar(value='— V')
        self.current = tk.StringVar(value='设定电流：— A')
        self.progress_text = tk.StringVar(value='0 / 0 点')
        self.identity = tk.StringVar(value='设备：未连接')
        self.file_is_default = True
        self._build()
        root.protocol('WM_DELETE_WINDOW', self.close)
        self.refresh_ports()
        root.after(50, self.poll)

    def field(self, parent, row, column, key, label, default, options=None, width=14):
        ttk.Label(parent, text=label).grid(row=row, column=column, padx=(8, 4), pady=5, sticky='w')
        var = tk.StringVar(value=default)
        self.values[key] = var
        if options:
            widget = ttk.Combobox(parent, textvariable=var, values=options, width=width, state='readonly')
        else:
            widget = ttk.Entry(parent, textvariable=var, width=width)
        widget.grid(row=row, column=column + 1, padx=(0, 8), pady=5, sticky='ew')
        self.settings_widgets.append((widget, 'readonly' if options else 'normal'))
        return widget

    def _build(self):
        outer = ttk.Frame(self.root, padding=14)
        outer.pack(fill='both', expand=True)
        ttk.Label(outer, text='GS610 电压采集', font=('Microsoft YaHei UI', 19, 'bold')).pack(anchor='w')
        ttk.Label(outer, text='4W 四线制：OUTPUT 输出电流 · SENSE 测量电压 · 每点保存 CSV').pack(anchor='w', pady=(0, 10))
        connection = ttk.LabelFrame(outer, text='串口连接', padding=5)
        connection.pack(fill='x', pady=(0, 8))
        self.port_box = self.field(connection, 0, 0, 'port', '串口号', '')
        refresh = ttk.Button(connection, text='刷新串口', command=self.refresh_ports)
        refresh.grid(row=0, column=2, padx=8)
        serial_settings = ttk.Button(connection, text='串口设置', command=self.open_serial_settings)
        serial_settings.grid(row=0, column=3, padx=8)
        self.settings_widgets.extend([(refresh, 'normal'), (serial_settings, 'normal')])
        for key, default in [('baudrate', '9600'), ('bytesize', '8'), ('parity', 'N'),
                             ('stopbits', '1'), ('flow', 'none'), ('terminator', 'LF'),
                             ('timeout', '5')]:
            self.values[key] = tk.StringVar(value=default)
        settings = ttk.LabelFrame(outer, text='扫描参数', padding=5)
        settings.pack(fill='x')
        self.field(settings, 0, 0, 'start', '初始电流 (A)', '-3.2')
        self.field(settings, 0, 2, 'stop', '截止电流 (A)', '3.2')
        self.field(settings, 0, 4, 'step', '步进电流 (A)', '0.08')
        self.field(settings, 1, 0, 'voltage_limit', '限压 (V)', '12')
        self.field(settings, 1, 2, 'dwell', '采样前等待 (s)', '1')
        for column in (1, 3, 5):
            settings.columnconfigure(column, weight=1)
        modes = ttk.Frame(settings)
        modes.grid(row=2, column=0, columnspan=6, sticky='w', padx=8, pady=6)
        for text, var in [('往返扫描（初始 → 截止 → 初始）', self.round_trip),
                          ('模拟演示（不连接设备，生成模拟数据）', self.demo)]:
            widget = ttk.Checkbutton(modes, text=text, variable=var)
            widget.pack(side='left', padx=(0, 24))
            self.settings_widgets.append((widget, 'normal'))
        dest = ttk.Frame(outer)
        dest.pack(fill='x', pady=8)
        ttk.Label(dest, text='CSV 文件').pack(side='left', padx=(0, 8))
        self.values['csv'] = tk.StringVar(value=default_result_path())
        entry = ttk.Entry(dest, textvariable=self.values['csv'])
        entry.pack(side='left', fill='x', expand=True)
        entry.bind('<KeyRelease>', self.mark_custom_path)
        browse = ttk.Button(dest, text='选择位置', command=self.browse)
        browse.pack(side='left', padx=(8, 0))
        self.settings_widgets.extend([(entry, 'normal'), (browse, 'normal')])
        controls = ttk.Frame(outer)
        controls.pack(fill='x')
        self.start_button = ttk.Button(controls, text='开始采集', command=self.start)
        self.start_button.pack(side='left')
        self.stop_button = ttk.Button(controls, text='停止并关闭输出', command=self.stop, state='disabled')
        self.stop_button.pack(side='left', padx=8)
        ttk.Label(controls, textvariable=self.status).pack(side='left', padx=6)
        reading = ttk.Frame(outer)
        reading.pack(fill='x', pady=8)
        ttk.Label(reading, textvariable=self.voltage, font=('Consolas', 30, 'bold'), foreground='#006f85').pack(side='left')
        details = ttk.Frame(reading)
        details.pack(side='left', padx=30)
        ttk.Label(details, textvariable=self.current).pack(anchor='w')
        ttk.Label(details, textvariable=self.identity).pack(anchor='w')
        ttk.Label(reading, textvariable=self.progress_text).pack(side='right')
        self.progress = ttk.Progressbar(outer, mode='determinate')
        self.progress.pack(fill='x', pady=(0, 8))
        pane = ttk.Panedwindow(outer, orient='horizontal')
        pane.pack(fill='both', expand=True)
        chart_frame = ttk.LabelFrame(pane, text='实时电压曲线（横轴：时间 s / 纵轴：电压 V）')
        self.canvas = tk.Canvas(chart_frame, background='#f7fafc', highlightthickness=0, height=230)
        self.canvas.pack(fill='both', expand=True)
        self.canvas.bind('<Configure>', lambda event: self.draw_plot())
        pane.add(chart_frame, weight=3)
        table_frame = ttk.LabelFrame(pane, text='采集记录（显示最近 500 点；CSV 保存全部数据）')
        columns = ('point', 'time', 'current', 'voltage')
        self.table = ttk.Treeview(table_frame, columns=columns, show='headings', height=8)
        for key, label, width in zip(columns, ['点号', '时间 (s)', '电流 (A)', '电压 (V)'], [52, 80, 95, 110]):
            self.table.heading(key, text=label)
            self.table.column(key, width=width, anchor='e', stretch=True)
        bar = ttk.Scrollbar(table_frame, orient='vertical', command=self.table.yview)
        self.table.configure(yscrollcommand=bar.set)
        bar.pack(side='right', fill='y')
        self.table.pack(fill='both', expand=True)
        pane.add(table_frame, weight=2)
        ttk.Label(outer, text='正常结束、停止或异常时均尝试关闭输出；通信故障时请在设备面板确认输出状态。').pack(anchor='w', pady=(8, 0))

    def open_serial_settings(self):
        if self.worker is not None:
            return
        dialog = tk.Toplevel(self.root)
        dialog.title('串口设置')
        dialog.transient(self.root)
        dialog.resizable(False, False)
        frame = ttk.Frame(dialog, padding=18)
        frame.pack(fill='both', expand=True)
        draft = {}
        fields = [
            ('baudrate', '波特率', [9600, 14400, 19200, 38400, 57600, 115200]),
            ('bytesize', '数据位', [7, 8]),
            ('parity', '校验位', ['N', 'E', 'O']),
            ('stopbits', '停止位', [1, 2]),
            ('flow', '流控', ['none', 'xon', 'rtscts']),
            ('terminator', '结束符', ['CR', 'LF', 'CRLF']),
            ('timeout', '读写超时 (s)', None),
        ]
        for row, (key, label, options) in enumerate(fields):
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky='w', padx=(0, 18), pady=6)
            draft[key] = tk.StringVar(value=self.values[key].get())
            if options:
                widget = ttk.Combobox(frame, textvariable=draft[key], values=options,
                                      width=22, state='readonly')
            else:
                widget = ttk.Entry(frame, textvariable=draft[key], width=24)
            widget.grid(row=row, column=1, sticky='ew', pady=6)
        ttk.Label(frame, text='参数须与仪器一致；N / E / O = 无 / 偶 / 奇校验。').grid(
            row=len(fields), column=0, columnspan=2, sticky='w', pady=(8, 12))

        def save():
            try:
                prompt_seconds(float(draft['timeout'].get()), positive=True)
            except ValueError as exc:
                messagebox.showerror('参数有误', f'读写超时：{exc}', parent=dialog)
                return
            for key, value in draft.items():
                self.values[key].set(value.get().strip())
            dialog.destroy()

        buttons = ttk.Frame(frame)
        buttons.grid(row=len(fields) + 1, column=0, columnspan=2, sticky='e')
        ttk.Button(buttons, text='取消', command=dialog.destroy).pack(side='left', padx=8)
        ttk.Button(buttons, text='确定', command=save).pack(side='left')
        dialog.bind('<Escape>', lambda event: dialog.destroy())
        dialog.bind('<Return>', lambda event: save())
        dialog.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - dialog.winfo_width()) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - dialog.winfo_height()) // 2
        dialog.geometry(f'+{max(0, x)}+{max(0, y)}')
        dialog.grab_set()
        dialog.focus_set()

    def mark_custom_path(self, event=None):
        self.file_is_default = False

    def browse(self):
        path = filedialog.asksaveasfilename(title='选择新的 CSV 文件', defaultextension='.csv',
                                          filetypes=[('CSV 文件', '*.csv')], initialfile=Path(default_result_path()).name)
        if path:
            self.values['csv'].set(path)
            self.file_is_default = False

    def refresh_ports(self):
        try:
            from serial.tools import list_ports
            ports = sorted(list_ports.comports(), key=lambda item: item.device)
            if ports and not self.values['port'].get():
                self.values['port'].set(ports[0].device)
            self.status.set('可用串口：' + '；'.join(f'{p.device} ({p.description})' for p in ports)
                            if ports else '未发现串口，请检查连接；也可勾选模拟演示')
        except ImportError:
            self.status.set('请先安装依赖：python -m pip install -r requirements.txt；可先模拟演示')

    def get_settings(self):
        config = {key: value.get().strip() for key, value in self.values.items()}
        config['demo'] = self.demo.get()
        config['points'], config['range'], config['limit'] = plan_sweep(
            config['start'], config['stop'], config['step'], config['voltage_limit'], self.round_trip.get())
        config['dwell'], config['timeout'] = float(config['dwell']), float(config['timeout'])
        prompt_seconds(config['dwell'])
        prompt_seconds(config['timeout'], positive=True)
        if not config['demo']:
            config['port'] = validate_port(config['port'])
        if not config['csv']:
            raise ValueError('请选择 CSV 保存位置')
        if self.file_is_default:
            prefix = 'demo-' if config['demo'] else ''
            path = Path(default_result_path())
            config['csv'] = str(path.with_name(prefix + path.name))
            self.values['csv'].set(config['csv'])
        path = result_path(config['csv'])
        if path.exists():
            raise ValueError('CSV 文件已存在，请选择新文件名，避免覆盖数据')
        path.parent.mkdir(parents=True, exist_ok=True)
        config['csv'] = str(path)
        return config

    def set_running(self, running):
        for widget, normal_state in self.settings_widgets:
            widget.configure(state='disabled' if running else normal_state)
        self.start_button.configure(state='disabled' if running else 'normal')
        self.stop_button.configure(state='normal' if running else 'disabled')

    def start(self):
        if self.worker is not None:
            return
        try:
            config = self.get_settings()
        except (ValueError, ArithmeticError, OSError) as exc:
            messagebox.showerror('参数有误', str(exc), parent=self.root)
            return
        self.records.clear()
        self.table.delete(*self.table.get_children())
        self.voltage.set('— V')
        self.current.set('设定电流：— A')
        self.identity.set('模拟数据（非实测）' if config['demo'] else '设备：正在连接')
        self.total = len(config['points'])
        self.progress.configure(maximum=self.total, value=0)
        self.progress_text.set(f'0 / {self.total} 点')
        self.stop_event.clear()
        self.set_running(True)
        self.status.set('模拟演示运行中' if config['demo'] else '连接设备并配置扫描中')
        self.draw_plot()
        self.worker = threading.Thread(target=self.acquire, args=(config,), daemon=False)
        self.worker.start()

    def acquire(self, config):
        # Only this worker owns the serial port; Tk widgets are updated by poll().
        port = None
        kind, detail = 'done', '扫描完成，输出已关闭'
        try:
            if config['demo']:
                device = GS610(DemoInstrument())
            else:
                import serial
                port = serial.Serial(port=config['port'], baudrate=int(config['baudrate']),
                                     bytesize=int(config['bytesize']), parity=config['parity'],
                                     stopbits=int(config['stopbits']), timeout=config['timeout'],
                                     write_timeout=config['timeout'], xonxoff=config['flow'] == 'xon',
                                     rtscts=config['flow'] == 'rtscts', dsrdtr=False)
                port.reset_input_buffer()
                device = GS610(SerialInstrument(port, config['terminator']))
                identity = device.query('*IDN?')
                fields = [field.strip().upper() for field in identity.split(',')]
                if len(fields) < 2 or fields[1] not in ('765501', 'GS610'):
                    raise RuntimeError('设备型号不是 GS610 (765501)，停止运行')
                self.events.put(('identity', identity))
            device.sweep(config['points'], config['range'], config['limit'], config['dwell'],
                         csv_path=config['csv'], on_record=lambda row: self.events.put(('record', row)),
                         stop_event=self.stop_event)
        except SweepCancelled:
            kind, detail = 'stopped', '扫描已停止；已执行关闭输出流程，请确认设备输出状态'
        except Exception as exc:
            kind, detail = 'error', f'{exc}\n请在设备面板确认并关闭输出。'
        finally:
            if port is not None:
                try:
                    port.close()
                except Exception as exc:
                    kind, detail = 'error', f'关闭串口失败：{exc}'
            self.events.put(('finished', (kind, detail, config['csv'])))

    def poll(self):
        changed = False
        for _ in range(200):
            try:
                event, data = self.events.get_nowait()
            except queue.Empty:
                break
            if event == 'record':
                self.records.append(data)
                index, elapsed, current, voltage = data
                self.voltage.set(f'{float(voltage):+.9g} V')
                self.current.set(f'设定电流：{current} A')
                self.progress_text.set(f'{index} / {self.total} 点')
                self.progress.configure(value=index)
                item = self.table.insert('', 'end', values=(index, f'{elapsed:.3f}', str(current), str(voltage)))
                children = self.table.get_children()
                if len(children) > 500:
                    self.table.delete(children[0])
                self.table.see(item)
                changed = True
            elif event == 'identity':
                self.identity.set('设备：' + data)
                self.status.set('正在采集')
            elif event == 'finished':
                kind, detail, path = data
                self.worker = None
                self.set_running(False)
                self.status.set(detail.split('\n')[0])
                if self.closing:
                    self.root.destroy()
                    return
                if kind == 'error':
                    messagebox.showerror('采集失败', detail + f'\n已采集数据（如有）：{path}', parent=self.root)
        if changed:
            self.draw_plot()
        self.root.after(50, self.poll)

    def draw_plot(self):
        canvas = self.canvas
        canvas.delete('all')
        w, h = max(canvas.winfo_width(), 320), max(canvas.winfo_height(), 180)
        left, right, top, bottom = 78, w - 22, 22, h - 40
        if not self.records:
            canvas.create_text(w / 2, h / 2, text='等待采集电压数据', fill='#65778a')
            return
        xmax = max(self.records[-1][1], 0.001)
        ys = [float(row[3]) for row in self.records]
        low, high = min(ys), max(ys)
        pad = max((high - low) * 0.1, abs(high) * 0.01, 0.000001)
        low, high = low - pad, high + pad
        for i in range(5):
            y = top + (bottom - top) * i / 4
            canvas.create_line(left, y, right, y, fill='#dce5ed')
            canvas.create_text(left - 7, y, anchor='e', text=f'{high - (high-low)*i/4:.5g}', fill='#425466')
            x = left + (right - left) * i / 4
            canvas.create_text(x, bottom + 18, text=f'{xmax*i/4:.2f}', fill='#425466')
        canvas.create_line(left, top, left, bottom, right, bottom, fill='#8193a5')
        # Preserve voltage extrema in each time bucket when the scan exceeds display pixels.
        stride = max(1, math.ceil(len(self.records) / max(1, int(right-left))))
        indices = {0, len(self.records)-1}
        for start in range(0, len(ys), stride):
            chunk = range(start, min(start + stride, len(ys)))
            indices.add(min(chunk, key=ys.__getitem__))
            indices.add(max(chunk, key=ys.__getitem__))
        coords = []
        for index in sorted(indices):
            coords.extend((left + self.records[index][1] / xmax * (right-left),
                           bottom - (ys[index]-low)/(high-low)*(bottom-top)))
        if len(coords) >= 4:
            canvas.create_line(*coords, fill='#007f9e', width=2)
        x, y = coords[-2:]
        canvas.create_oval(x-3, y-3, x+3, y+3, fill='#007f9e', outline='')

    def stop(self):
        if self.worker is not None:
            self.stop_event.set()
            self.stop_button.configure(state='disabled')
            self.status.set('正在停止并关闭输出；等待当前串口操作结束…')

    def close(self):
        if self.worker is not None:
            self.closing = True
            self.stop()
        else:
            self.root.destroy()


def main():
    root = tk.Tk()
    SweepApp(root)
    root.mainloop()


if __name__ == '__main__':
    main()
