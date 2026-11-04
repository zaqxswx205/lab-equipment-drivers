"""Tk interface for internally timed GS610 sweeps and 2700 readings."""
import csv
import math
import os
import queue
import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from device_scan import scan_port
from gs610_driver import GS610, SerialInstrument, number, plan_sweep
from keithley2700 import Instrument, Settings
from sweep_plan import linear_runs
from displacement_limit import find_limits, validate_thresholds


class App:
    def __init__(self, root):
        self.root = root
        root.title('驱动设备 / 采集设备')
        root.geometry('1280x850')
        root.minsize(1100, 750)
        self.events = queue.Queue()
        self.stop = threading.Event()
        self.running = False
        self.scanning = False
        self.port_boxes = []
        self.data = []
        self.config = [dict(baud='9600', flow='none', term='CR', bits='8', parity='N', stop='1'),
                       dict(baud='9600', flow='none', term='LF', bits='8', parity='N', stop='1')]
        self.ports = [tk.StringVar(value='COM3'), tk.StringVar(value='COM4')]
        upper = ttk.Frame(root, padding=10)
        upper.pack(fill='x')
        upper.columnconfigure(0, weight=3)
        upper.columnconfigure(1, weight=1)
        controls = ttk.Frame(upper)
        controls.grid(row=0, column=0, sticky='nsew', padx=(0, 12))
        top = ttk.Frame(controls)
        top.pack(fill='x')
        try:
            from serial.tools import list_ports
            ports = [p.device for p in list_ports.comports()]
        except ImportError:
            ports = []
        top.columnconfigure(0, weight=1, uniform='device')
        top.columnconfigure(1, weight=1, uniform='device')
        groups = []
        for i, name in enumerate(('采集设备', '驱动设备')):
            group = ttk.LabelFrame(top, text=name, padding=12)
            group.grid(row=0, column=i, sticky='nsew', padx=5)
            groups.append(group)
            group.columnconfigure(0, minsize=155)
            group.columnconfigure(1, weight=1)
            serial_row = ttk.Frame(group)
            serial_row.grid(row=0, column=0, columnspan=2, sticky='ew', pady=(0, 8))
            ttk.Label(serial_row, text='串口', width=8, anchor='w').grid(row=0, column=0, padx=(0, 8))
            box = ttk.Combobox(serial_row, textvariable=self.ports[i], values=ports, width=12)
            box.grid(row=0, column=1, sticky='w')
            self.port_boxes.append(box)
            ttk.Button(serial_row, text='串口高级设置', command=lambda i=i: self.advanced(i)).grid(row=0, column=2, padx=(12, 0))
        acquisition, drive = groups
        for row, (label, value) in enumerate((('采集方式', '前面板电压采集'),
                                            ('前面板开关', 'F'), ('积分时间', 'NPLC = 1')), 1):
            ttk.Label(acquisition, text=label).grid(row=row, column=0, sticky='w', pady=5)
            ttk.Label(acquisition, text=value).grid(row=row, column=1, sticky='w', pady=5)
        self.values = {}
        for col, (key, label, default) in enumerate((('start', '起始电流 A', '-3.2'), ('end', '截止电流 A', '3.2'),
                                                    ('step', '步长 A', '0.08'), ('limit', '限压 V', '12'))):
            row = col + 1
            ttk.Label(drive, text=label).grid(row=row, column=0, sticky='w', pady=5)
            self.values[key] = tk.StringVar(value=default)
            ttk.Entry(drive, textvariable=self.values[key], width=12).grid(row=row, column=1, sticky='w', pady=5)
        self.returning = tk.BooleanVar(value=True)
        self.values['period'] = tk.StringVar(value='2')
        ttk.Label(drive, text='每点输出保持时间 s').grid(row=5, column=0, sticky='w', pady=5)
        ttk.Entry(drive, textvariable=self.values['period'], width=12).grid(row=5, column=1, sticky='w', pady=5)
        self.delay_label = tk.StringVar()
        def update_delay(*_):
            try:
                period = float(self.values['period'].get())
                if not math.isfinite(period) or period <= 0:
                    raise ValueError()
                self.delay_label.set(f'{period / 2:g} s（保持时间的1/2）')
            except ValueError:
                self.delay_label.set('等待有效保持时间')
        self.values['period'].trace_add('write', update_delay)
        update_delay()
        ttk.Label(acquisition, text='自动采样延迟').grid(row=4, column=0, sticky='w', pady=5)
        ttk.Label(acquisition, textvariable=self.delay_label).grid(row=4, column=1, sticky='w', pady=5)
        ttk.Label(drive, text='Linear / INT；本地感测；0.08 A = 80 mA').grid(row=6, column=0, columnspan=2, sticky='w', pady=5)
        for row, (key, label, default) in enumerate((
                ('negative_voltage', '位移极限负向停止电压 V', '-2'),
                ('positive_voltage', '位移极限正向停止电压 V', '2')), 7):
            self.values[key] = tk.StringVar(value=default)
            ttk.Label(drive, text=label).grid(row=row, column=0, sticky='w', pady=5)
            ttk.Entry(drive, textvariable=self.values[key], width=12).grid(row=row, column=1, sticky='w', pady=5)
        save_row = ttk.Frame(top)
        save_row.grid(row=1, column=0, columnspan=2, sticky='ew', pady=(10, 0))
        save_row.columnconfigure(1, weight=1)
        self.csv_path = tk.StringVar(value=str(self.default_csv_path()))
        self.last_saved_path = None
        ttk.Label(save_row, text='数据记录地址').grid(row=0, column=0, padx=(5, 10))
        ttk.Entry(save_row, textvariable=self.csv_path).grid(row=0, column=1, sticky='ew')
        ttk.Button(save_row, text='选择保存位置…', command=self.choose_csv).grid(row=0, column=2, padx=(10, 5))
        ttk.Button(save_row, text='打开数据记录文件', command=self.open_csv).grid(row=0, column=3, padx=(5, 5))
        buttons = ttk.Frame(controls)
        buttons.pack(fill='x', padx=5, pady=(12, 0))
        self.start_button = ttk.Button(buttons, text='开始测试', command=self.toggle_test)
        self.limit_button = ttk.Button(buttons, text='位移极限', command=lambda: self.start(limit_search=True))
        self.limit_button.pack(side='left')
        self.start_button.pack(side='left', padx=(10, 0))
        self.return_button = ttk.Checkbutton(buttons, text='返程测试（截止 → 起始）', variable=self.returning)
        self.return_button.pack(side='left', padx=15)
        self.scan_button = ttk.Button(buttons, text='扫描串口设备', command=self.scan_devices)
        self.scan_button.pack(side='left', padx=(0, 10), before=self.limit_button)
        self.status = tk.StringVar(value='就绪：采集设备前面板开关置 F，驱动设备使用本地感测。')
        ttk.Label(controls, textvariable=self.status, padding=(5, 10), wraplength=780).pack(fill='x')
        realtime = ttk.LabelFrame(upper, text='历史数据（实时更新）', padding=12)
        realtime.grid(row=0, column=1, sticky='nsew')
        realtime.columnconfigure(0, weight=1)
        history_frame = ttk.Frame(realtime)
        history_frame.grid(row=0, column=0, sticky='nsew')
        realtime.rowconfigure(0, weight=1)
        history_frame.columnconfigure(0, weight=1)
        history_frame.rowconfigure(0, weight=1)
        self.history = ttk.Treeview(history_frame, columns=('current', 'drive_voltage', 'voltage'), show='headings', height=6)
        for key, label in (('current', '驱动电流 (A)'), ('drive_voltage', '驱动电压 (V)'), ('voltage', '采集电压 (V)')):
            self.history.heading(key, text=label)
            self.history.column(key, width=110, minwidth=95, anchor='e')
        self.history.grid(row=0, column=0, sticky='nsew')
        scrollbar = ttk.Scrollbar(history_frame, orient='vertical', command=self.history.yview)
        scrollbar.grid(row=0, column=1, sticky='ns')
        self.history.configure(yscrollcommand=scrollbar.set)
        curve = ttk.LabelFrame(root, text='实时曲线', padding=5)
        curve.pack(fill='both', expand=True, padx=10, pady=(0, 10))
        self.canvas = tk.Canvas(curve, bg='white', height=280)
        self.canvas.pack(fill='both', expand=True)
        self.canvas.bind('<Configure>', lambda e: self.plot())
        root.protocol('WM_DELETE_WINDOW', self.close)
        root.after(100, self.poll)

    def scan_devices(self):
        if self.running or self.scanning:
            return
        self.scanning = True
        self.limit_button.config(state='disabled')
        self.start_button.config(state='disabled')
        self.scan_button.config(state='disabled')
        self.status.set('正在预读串口设备身份…')
        win = tk.Toplevel(self.root)
        win.title('串口设备识别结果')
        win.geometry('850x340')
        ttk.Label(win, text='按两台设备当前高级设置查询 *IDN?；未识别可能是通信参数不匹配或非SCPI设备。', padding=10).pack(fill='x')
        self.scan_table = ttk.Treeview(win, columns=('port', 'role', 'identity', 'profile'), show='headings')
        for key, label, width in [('port', '串口', 80), ('role', '设备用途', 100), ('identity', '身份 / 未识别原因', 450), ('profile', '通信参数', 180)]:
            self.scan_table.heading(key, text=label)
            self.scan_table.column(key, width=width)
        self.scan_table.pack(fill='both', expand=True, padx=10, pady=10)
        profiles = [dict(c) for c in self.config]
        def worker():
            results = []
            try:
                from serial.tools import list_ports
                available = sorted(list_ports.comports(), key=lambda p: p.device)
                for port in available:
                    role, identity, profile = scan_port(port.device, profiles)
                    result = (port.device, role, identity, profile)
                    results.append(result)
                    self.events.put(('scan_record', result))
                self.events.put(('scan_done', results))
            except Exception as exc:
                self.events.put(('scan_error', str(exc)))
        threading.Thread(target=worker, daemon=True).start()

    def finish_scan(self):
        self.scanning = False
        self.limit_button.config(state='normal')
        self.start_button.config(state='normal')
        self.scan_button.config(state='normal')

    @staticmethod
    def default_csv_path():
        return Path(__file__).resolve().parent / 'result' / (datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '.csv')

    def open_csv(self):
        try:
            # During acquisition, open the actual file even if the path entry
            # has been edited since the test started.
            path = self.last_saved_path if self.running else Path(self.csv_path.get().strip()).expanduser().resolve()
            if path is None or not path.is_file():
                messagebox.showinfo('文件尚未生成', '数据记录文件尚未生成，请开始测试并生成CSV后再打开。')
                return
            if path.suffix.lower() != '.csv':
                raise ValueError('请选择CSV数据记录文件')
            os.startfile(str(path))
        except Exception as exc:
            messagebox.showerror('打开文件失败', str(exc))

    def choose_csv(self):
        if self.running:
            return
        current = Path(self.csv_path.get()).expanduser()
        directory = current.parent
        if not directory.is_dir():
            directory = Path(__file__).resolve().parent / 'result'
            directory.mkdir(exist_ok=True)
        selected = filedialog.asksaveasfilename(
            parent=self.root, title='选择数据记录地址', initialdir=str(directory),
            initialfile=current.name, defaultextension='.csv',
            filetypes=[('CSV 数据文件', '*.csv')])
        if selected:
            if Path(selected).exists():
                messagebox.showerror('文件已存在', '请选择新的文件名，避免覆盖已有测试数据。')
                return
            self.csv_path.set(selected)

    def advanced(self, index):
        if self.running or self.scanning:
            return
        win = tk.Toplevel(self.root)
        win.title(('采集设备', '驱动设备')[index] + ' 串口高级设置')
        variables = {}
        options = {'baud': ('波特率', ['300', '600', '1200', '2400', '4800', '9600', '19200'] if index == 0 else ['9600', '14400', '19200', '38400', '57600', '115200']),
                   'flow': ('流控', ['none', 'xon'] if index == 0 else ['none', 'xon', 'rtscts']),
                   'term': ('接收结束符', ['CR', 'LF', 'CRLF']),
                   'bits': ('数据位', ['8'] if index == 0 else ['7', '8']),
                   'parity': ('校验', ['N'] if index == 0 else ['N', 'E', 'O']),
                   'stop': ('停止位', ['1'] if index == 0 else ['1', '2'])}
        for row, (key, (label, choices)) in enumerate(options.items()):
            ttk.Label(win, text=label).grid(row=row, column=0, padx=12, pady=6)
            variables[key] = tk.StringVar(value=self.config[index][key])
            ttk.Combobox(win, textvariable=variables[key], values=choices, state='readonly').grid(row=row, column=1, padx=12)
        def save():
            self.config[index] = {k: v.get() for k, v in variables.items()}
            win.destroy()
        ttk.Button(win, text='保存', command=save).grid(row=6, column=1, pady=12)

    def toggle_test(self):
        if self.running:
            self.stop.set()
            self.start_button.config(text='正在停止…', state='disabled')
            self.status.set('正在停止并关闭输出，请等待设备清理完成。')
        else:
            self.start()

    def start(self, limit_search=False):
        if self.running or self.scanning:
            return
        try:
            thresholds = validate_thresholds(self.values['negative_voltage'].get(), self.values['positive_voltage'].get()) if limit_search else (-2, 2)
            if not self.csv_path.get().strip():
                raise ValueError('请选择数据记录地址')
            path = Path(self.csv_path.get().strip()).expanduser().resolve()
            # Repeated runs receive a new filename in the selected directory.
            if path == self.last_saved_path:
                path = path.parent / self.default_csv_path().name
                self.csv_path.set(str(path))
            if path.suffix.lower() != '.csv':
                raise ValueError('数据记录文件需使用.csv扩展名')
            if path.exists():
                raise ValueError('数据记录文件已存在，请选择新的文件名')
            path.parent.mkdir(parents=True, exist_ok=True)
            period = float(self.values['period'].get())
            delay = period / 2
            if not all(math.isfinite(v) for v in (period, delay)) or not 0.3 <= period <= 3600:
                raise ValueError('每点输出保持时间必须在0.3到3600秒之间')
            if delay < 0 or delay >= period:
                raise ValueError('采样延迟必须非负且小于每点保持时间，并预留读数时间')
            parameters = ('-3.2', '3.2', '0.08', self.values['limit'].get()) if limit_search else tuple(self.values[k].get() for k in ('start', 'end', 'step', 'limit'))
            points, source_range, limit = plan_sweep(*parameters, False, allow_partial=True)
            if len(points) > 65535:
                raise ValueError('每程最多65535点')
            ports = [v.get().strip() for v in self.ports]
            if not all(ports) or ports[0].upper() == ports[1].upper():
                raise ValueError('请设置两个不同的串口')
        except Exception as exc:
            messagebox.showerror('参数错误', str(exc))
            return
        self.data.clear()
        self.history.delete(*self.history.get_children())
        self.plot()
        self.stop.clear()
        self.running = True
        self.limit_button.config(state='disabled')
        self.start_button.config(text='停止并关闭输出', state='normal')
        self.return_button.config(state='disabled')
        self.scan_button.config(state='disabled')
        configs = [dict(c) for c in self.config]
        self.last_saved_path = path
        threading.Thread(target=self.acquire, args=(points, source_range, limit, ports, configs, self.returning.get(), period, delay, path, limit_search, thresholds), daemon=True).start()

    def acquire(self, points, source_range, limit, ports, configs, returning, period, delay, path, limit_search=False, thresholds=(-2, 2)):
        meter = None
        port = None
        source = None
        try:
            import serial
            c = configs[0]
            settings = Settings(port=ports[0], baud=int(c['baud']), flow=c['flow'] == 'xon', terminator=c['term'], input_source='FRONT', nplc=1)
            settings.validate()
            meter = Instrument(settings, self.stop)
            self.events.put(('status', '正在连接采集设备'))
            meter.open()
            meter.configure()
            c = configs[1]
            port = serial.Serial(ports[1], int(c['baud']), bytesize=int(c['bits']), parity=c['parity'], stopbits=int(c['stop']),
                                 timeout=1, write_timeout=1, xonxoff=c['flow'] == 'xon', rtscts=c['flow'] == 'rtscts')
            port.reset_input_buffer()
            source = GS610(SerialInstrument(port, c['term']))
            identity = source.query('*IDN?').split(',')
            if len(identity) < 2 or identity[1].strip().upper() not in ('GS610', '765501'):
                raise RuntimeError('驱动设备型号不匹配')
            source.configure(source_range, limit)
            source.write(':SENS:RSEN OFF')
            source.check_error()
            with path.open('x', newline='', encoding='utf-8-sig') as f:
                writer = csv.writer(f)
                writer.writerow(['timestamp', 'direction', 'point', 'set_current_A', 'drive_voltage_V', 'voltage_V'])
                if limit_search:
                    def record_limit(leg, index, current, voltage, drive_voltage):
                        writer.writerow([datetime.now().strftime('%Y-%m-%d %H:%M:%S'), leg, index, str(current), drive_voltage, voltage])
                        f.flush()
                        os.fsync(f.fileno())
                        self.events.put(('record', (leg, index, str(current), voltage, 0, drive_voltage)))
                    lower, upper = find_limits(source, meter, self.stop, period, record_limit, *thresholds)
                    source.write(':OUTP OFF')
                    source.check_error()
                    if self.stop.is_set():
                        return
                    calibrated, _, _ = plan_sweep(lower, upper, '0.08', limit, False, allow_partial=True)
                    source.configure_linear(calibrated, 0, self.stop, trigger_period=period)
                    source.set_current(number('0'))
                    self.events.put(('limits', (str(lower), str(upper), str(path))))
                    return
                legs = [('去程', points)] + ([('返程', list(reversed(points)))] if returning else [])
                runs = [(leg, run) for leg, sequence in legs for run in linear_runs(sequence)]
                offsets = {'去程': 0, '返程': 0}
                for leg, sequence in runs:
                    if self.stop.is_set():
                        break
                    source.set_current(sequence[0])
                    # The external meter's delay must not lengthen the source's
                    # internal measurement cycle. Reserve the full INT period.
                    source.configure_linear(sequence, 0, self.stop, trigger_period=period)
                    source.write(':OUTP ON')
                    source.check_error()
                    if source.query(':OUTP?') != '1':
                        raise RuntimeError('驱动设备输出未开启')
                    source.query(':STAT:SOUR:EVEN?')
                    anchor = time.monotonic()
                    source.write(':SWE:TRIG')
                    for i, current in enumerate(sequence):
                        target = anchor + i * period + delay
                        if self.stop.wait(max(0, target - time.monotonic())):
                            break
                        read_start = time.monotonic()
                        if read_start - target > 0.2:
                            raise RuntimeError('采样调度延迟超过0.2秒，停止以避免点位错配')
                        raw, value, status = meter.measure(0)
                        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                        # Fetch the latest internal voltage reading (panel M).
                        # FETCh does not initiate an additional measurement.
                        drive_voltage = float(source.measure_voltage())
                        if abs(drive_voltage) >= 9e37:
                            raise RuntimeError('驱动设备电压读数超量程')
                        elapsed = time.monotonic() - anchor
                        point_index = offsets[leg] + i + 1
                        writer.writerow([timestamp, leg, point_index, str(current), drive_voltage, value])
                        f.flush()
                        os.fsync(f.fileno())
                        if elapsed >= (i + 1) * period:
                            raise RuntimeError('两台设备读数跨越下一电流点，停止；请增大输出保持时间或检查串口速度')
                        if status != 'OK':
                            raise RuntimeError('采集电压超量程')
                        self.events.put(('record', (leg, point_index, str(current), value, elapsed, drive_voltage)))
                    if not self.stop.is_set():
                        self.stop.wait(max(0, anchor + len(sequence) * period - time.monotonic()))
                    source.write(':OUTP OFF')
                    source.check_error()
                    if self.stop.is_set():
                        break
                    # Validate the internal sweep completed all points without sampling errors.
                    source.wait_stored_point(len(sequence), 3, self.stop)
                    source.write(':TRAC:STAT OFF')
                    source.check_error()
                    offsets[leg] += len(sequence)
            self.events.put(('status', f'{"已停止" if self.stop.is_set() else "完成"}；CSV：{path}'))
        except Exception as exc:
            self.events.put(('status', f'测试停止：{exc}'))
        finally:
            if source is not None:
                try:
                    source.write(':OUTP OFF')
                    if source.query(':OUTP?') != '0':
                        raise RuntimeError('输出仍开启')
                except Exception as exc:
                    self.events.put(('error', f'关闭驱动设备输出失败，请在仪器面板立即确认：{exc}'))
            if port is not None:
                port.close()
            if meter is not None:
                try:
                    meter.close()
                except Exception as exc:
                    self.events.put(('error', f'采集设备关闭连接失败：{exc}'))
            self.events.put(('done', None))

    def poll(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == 'record':
                    self.data.append(value)
                    follow_latest = self.history.yview()[1] >= 0.999
                    item = self.history.insert('', 'end', values=(f'{float(value[2]):.8g}', f'{value[5]:.8g}', f'{value[3]:.8g}'))
                    if follow_latest:
                        self.history.see(item)
                    self.status.set(f'{value[0]} {value[1]} 点：{value[2]} A，{value[3]:.8g} V')
                    self.plot()
                elif kind == 'done':
                    self.running = False
                    self.limit_button.config(state='normal')
                    self.start_button.config(text='开始测试', state='normal')
                    self.return_button.config(state='normal')
                    self.scan_button.config(state='normal')
                elif kind == 'limits':
                    lower, upper, path = value
                    self.values['start'].set(lower)
                    self.values['end'].set(upper)
                    self.values['step'].set('0.08')
                    self.status.set(f'位移极限完成：{lower} A ～ {upper} A；已写入610扫描设置，输出关闭；CSV：{path}')
                elif kind == 'scan_record':
                    name, role, identity, profile = value
                    summary = f"{profile['baud']} / {profile['bits']}{profile['parity']}{profile['stop']} / {profile['flow']}" if profile else ''
                    if self.scan_table.winfo_exists():
                        self.scan_table.insert('', 'end', values=(name, role, identity, summary))
                    self.status.set(f'已扫描 {name}：{role}')
                elif kind == 'scan_done':
                    for box in self.port_boxes:
                        box.config(values=[row[0] for row in value])
                    for index, role in enumerate(('采集设备', '驱动设备')):
                        matches = [row for row in value if row[1] == role]
                        if len(matches) == 1:
                            name, _, _, profile = matches[0]
                            self.ports[index].set(name)
                            for key in ('baud', 'bits', 'parity', 'stop', 'flow'):
                                self.config[index][key] = profile[key]
                    self.finish_scan()
                    self.status.set(f'扫描完成：共{len(value)}个串口；唯一匹配的设备已自动填入，多台同类设备请手动选择。')
                elif kind == 'scan_error':
                    self.finish_scan()
                    self.status.set(f'扫描失败：{value}')
                elif kind == 'error':
                    messagebox.showerror('设备状态需确认', value)
                else:
                    self.status.set(value)
        except queue.Empty:
            pass
        self.root.after(100, self.poll)

    def plot(self):
        c = self.canvas
        c.delete('all')
        w, h = max(c.winfo_width(), 200), max(c.winfo_height(), 150)
        left, right = 145, w - 30
        c.create_text(w / 2, 15, text='实时 I–V 曲线：蓝色去程 / 红色返程')
        c.create_line(left, 30, left, h - 40, right, h - 40)
        c.create_text(w / 2, h - 12, text='驱动设备设定电流 (A)')
        c.create_text(24, h / 2, text='采集设备电压 (V)', angle=90, tags='y_title')
        if not self.data:
            return
        xs = [float(v[2]) for v in self.data]
        ys = [v[3] for v in self.data]
        xmin, xmax, ymin, ymax = min(xs), max(xs), min(ys), max(ys)
        dx, dy = xmax - xmin or 1e-6, ymax - ymin or 1e-6
        c.create_text(left, h - 28, text=f'{xmin:.5g}')
        c.create_text(right, h - 28, text=f'{xmax:.5g}')
        c.create_text(left - 10, 35, text=f'{ymax:.5g}', anchor='e')
        c.create_text(left - 10, h - 45, text=f'{ymin:.5g}', anchor='e')
        for leg, color in [('去程', '#1769aa'), ('返程', '#c62828'), ('负向极限', '#1769aa'), ('正向极限', '#c62828')]:
            coords = []
            for v in self.data:
                if v[0] == leg:
                    x = left + (float(v[2]) - xmin) / dx * (right - left)
                    y = h - 40 - (v[3] - ymin) / dy * (h - 80)
                    coords.extend((x, y))
                    c.create_oval(x - 2, y - 2, x + 2, y + 2, fill=color, outline=color)
            if len(coords) >= 4:
                c.create_line(*coords, fill=color, width=2)

    def close(self):
        if self.scanning:
            self.status.set('正在扫描串口，请等待扫描结束后关闭。')
        elif self.running:
            self.toggle_test()
            self.status.set('正在停止并关闭输出，完成后可关闭窗口。')
        else:
            self.root.destroy()


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == '__main__':
    main()
