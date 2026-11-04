"""Run with python main.py. UI updates stay on the Tk main thread."""
import math
import queue
import threading
import tkinter as tk
from collections import deque
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from acquisition import (
    MODES,
    ChannelConfig,
    Settings,
    collect,
    convert_csv,
    valid_channels,
)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Keithley 2700 · 逐通道测量配置 · test_3")
        self.geometry("1220x980")
        self.minsize(1080, 900)
        self.events = queue.Queue()
        self.stop_event = threading.Event()
        self.worker = None
        self.exporting = False
        self.closing = False
        self.current_file = None
        self.points = {}
        self.channel_counts = {}
        self.channel_vars = {ch: tk.BooleanVar(value=ch in (101, 102)) for slot in (1, 2) for ch in range(slot * 100 + 1, slot * 100 + 43)}
        self.channel_configs = {ch: ChannelConfig(ch, "直流电流 DCI" if ch % 100 in (41, 42) else "直流电压 DCV") for ch in self.channel_vars}
        self.channel_configs[101] = ChannelConfig(101, "两线电阻 Ω2")
        self.point_modes = {}
        self.active_settings = None
        self.config_busy = False
        self.editor_dirty = False
        self.loading_editor = False
        self.dirty = True
        self.controls = []
        self.vars = {key: tk.StringVar(value=value) for key, value in {
            "input_source": "7702", "settle": "0.5", "port": "", "baud": "9600", "terminator": "CR", "timeout": "10",
            "mode": next(iter(MODES)), "interval": "1", "nplc": "1",
            "measure_range": "AUTO", "edit_mode": "两线电阻 Ω2", "edit_range": "AUTO", "edit_nplc": "1", "edit_settle": "0.5", "folder": str(Path(__file__).parent / "data")}.items()}
        self.simulation = tk.BooleanVar(value=False)
        self.flow = tk.BooleanVar(value=False)
        self.status = tk.StringVar(value="就绪 · 7702 / 7700 使用 R 输入；前面板使用 F 输入")
        self.readout = tk.StringVar(value="—")
        self.stats = tk.StringVar(value="已保存 0 点")
        self.file_label = tk.StringVar(value="TXT：尚未创建")
        self.warning_text = tk.StringVar(value="")
        self.modules_text = tk.StringVar(value="模块：尚未查询；请选实际型号与槽位后测试连接")
        self._build()
        for key in ("edit_mode", "edit_range", "edit_nplc", "edit_settle"):
            self.vars[key].trace_add("write", self.mark_editor_dirty)
        self.refresh_ports()
        self.protocol("WM_DELETE_WINDOW", self.close_app)
        self.after(100, self.poll)

    def field(self, parent, label, key, row, col, values=None, width=16):
        ttk.Label(parent, text=label).grid(row=row, column=col, sticky="w", padx=6, pady=5)
        if values:
            widget = ttk.Combobox(parent, textvariable=self.vars[key], values=values,
                                  state="readonly", width=width)
        else:
            widget = ttk.Entry(parent, textvariable=self.vars[key], width=width)
        widget.grid(row=row, column=col + 1, sticky="ew", padx=6, pady=5)
        self.controls.append((widget, "readonly" if values else "normal"))
        return widget

    def _build(self):
        root = ttk.Frame(self, padding=16)
        root.pack(fill="both", expand=True)
        ttk.Label(root, text="Keithley 2700 · 逐通道独立测量", font=("Microsoft YaHei UI", 20, "bold")).pack(anchor="w")
        ttk.Label(root, text="101测电阻、102测电压 · 独立量程 / NPLC / 稳定时间 · RS-232 · 逐点保存 TXT / 导出 CSV").pack(anchor="w", pady=(2, 12))
        setup_tabs = ttk.Notebook(root)
        setup_tabs.pack(fill="x", pady=(0, 8))
        connection_page = ttk.Frame(setup_tabs)
        channels_page = ttk.Frame(setup_tabs)
        setup_tabs.add(connection_page, text="连接与前面板参数")
        setup_tabs.add(channels_page, text="逐通道配置与保存目录")
        serial_box = ttk.LabelFrame(connection_page, text="串口连接（8 数据位 / 无校验 / 1 停止位）", padding=6)
        serial_box.pack(fill="x")
        self.port_box = self.field(serial_box, "串口", "port", 0, 0)
        # Editable: a disconnected COM port can be entered by hand.
        self.field(serial_box, "波特率", "baud", 0, 2, [300, 600, 1200, 2400, 4800, 9600, 19200])
        ttk.Label(serial_box, text="发送结束符：CR\n接收结束符：自动识别").grid(row=0, column=4, columnspan=2, sticky="w", padx=6)
        self.field(serial_box, "超时 / 秒", "timeout", 1, 0)
        for text, variable, column in [("XON/XOFF", self.flow, 2), ("模拟模式（不连接仪器）", self.simulation, 4)]:
            button = ttk.Checkbutton(serial_box, text=text, variable=variable)
            button.grid(row=1, column=column, columnspan=2, sticky="w", padx=6)
            self.controls.append((button, "normal"))
        refresh = ttk.Button(serial_box, text="刷新串口", command=self.refresh_ports)
        refresh.grid(row=0, column=6, padx=6)
        self.controls.append((refresh, "normal"))
        self.port_hint = ttk.Label(serial_box, text="")
        self.port_hint.grid(row=2, column=0, columnspan=7, sticky="w", padx=6)
        measure = ttk.LabelFrame(connection_page, text="前面板参数（模块采集以逐通道配置为准；每轮间隔通用）", padding=6)
        measure.pack(fill="x", pady=10)
        mode = self.field(measure, "测量功能", "mode", 0, 0, list(MODES), 20)
        mode.bind("<<ComboboxSelected>>", lambda _: self.update_hint())
        self.field(measure, "每轮间隔 / 秒", "interval", 0, 2)
        self.field(measure, "NPLC", "nplc", 0, 4, ["0.01", "0.1", "1", "5", "10"], 8)
        self.field(measure, "量程（基本单位）", "measure_range", 1, 0)
        ttk.Label(measure, text="AUTO = 自动量程；数值单位为 V / A / Ω。实际间隔受测量耗时限制。") .grid(row=1, column=2, columnspan=4, sticky="w", padx=6)
        self.wiring = ttk.Label(measure, text="")
        self.wiring.grid(row=2, column=0, columnspan=6, sticky="w", padx=6, pady=5)
        channel_box = ttk.LabelFrame(channels_page, text="输入来源与模块通道（101 = 槽位1通道01；201 = 槽位2通道01）", padding=6)
        channel_box.pack(fill="x", pady=(0, 8))
        source = self.field(channel_box, "输入来源", "input_source", 0, 0, ["7702", "7700", "FRONT"], 10)
        source.bind("<<ComboboxSelected>>", lambda _: self.update_hint())
        ttk.Label(channel_box, text="选中行编辑；Ctrl / Shift 可多选。修改后点击“应用并启用”。").grid(row=0, column=2, columnspan=4, sticky="w")
        config_frame = ttk.Frame(channel_box)
        config_frame.grid(row=1, column=0, columnspan=6, sticky="ew")
        self.config_table = ttk.Treeview(config_frame, columns=("enabled", "channel", "mode", "range", "nplc", "settle"), show="headings", height=4, selectmode="extended")
        for key, title, width in [("enabled", "采集", 65), ("channel", "通道", 75), ("mode", "测量功能", 180),
                                  ("range", "量程（基本单位）", 160), ("nplc", "NPLC", 80), ("settle", "稳定 / 秒", 90)]:
            self.config_table.heading(key, text=title)
            self.config_table.column(key, width=width, anchor="center")
        self.config_table.pack(side="left", fill="x", expand=True)
        config_scroll = ttk.Scrollbar(config_frame, orient="vertical", command=self.config_table.yview)
        config_scroll.pack(side="right", fill="y")
        self.config_table.configure(yscrollcommand=config_scroll.set)
        self.config_table.bind("<<TreeviewSelect>>", self.load_channel_editor)
        editor = ttk.Frame(channel_box)
        editor.grid(row=2, column=0, columnspan=6, sticky="ew")
        self.field(editor, "功能", "edit_mode", 0, 0, list(MODES), 17)
        self.field(editor, "量程", "edit_range", 0, 2, width=9)
        self.field(editor, "NPLC", "edit_nplc", 0, 4, ["0.01", "0.1", "1", "5", "10"], 5)
        self.field(editor, "稳定 / 秒", "edit_settle", 0, 6, width=6)
        config_actions = ttk.Frame(channel_box)
        config_actions.grid(row=3, column=0, columnspan=6, sticky="w")
        for title, action in [("应用并启用选中通道", self.apply_channel_editor),
                              ("禁用选中通道", self.disable_selected),
                              ("清空全部启用", self.disable_all)]:
            button = ttk.Button(config_actions, text=title, command=action)
            button.pack(side="left", padx=4)
            self.controls.append((button, "normal"))
        ttk.Label(channel_box, textvariable=self.modules_text).grid(row=4, column=0, columnspan=6, sticky="w", padx=6)
        self.update_hint()
        self.config_table.selection_set("101")
        folder = ttk.Frame(channels_page)
        folder.pack(fill="x")
        ttk.Label(folder, text="保存目录").pack(side="left")
        entry = ttk.Entry(folder, textvariable=self.vars["folder"])
        entry.pack(side="left", fill="x", expand=True, padx=8)
        self.controls.append((entry, "normal"))
        choose = ttk.Button(folder, text="选择目录", command=self.choose_folder)
        choose.pack(side="left")
        self.controls.append((choose, "normal"))
        actions = ttk.Frame(root)
        actions.pack(fill="x", pady=10)
        self.probe_button = ttk.Button(actions, text="测试连接", command=lambda: self.start(True))
        self.probe_button.pack(side="left")
        self.start_button = ttk.Button(actions, text="连接并开始采集", command=self.start)
        self.start_button.pack(side="left", padx=8)
        self.stop_button = ttk.Button(actions, text="停止采集", command=self.stop, state="disabled")
        self.stop_button.pack(side="left")
        self.export_button = ttk.Button(actions, text="TXT → CSV…", command=self.export)
        self.export_button.pack(side="right")
        ttk.Label(actions, textvariable=self.readout, font=("Consolas", 22, "bold")).pack(side="left", padx=24)
        table_frame = ttk.Frame(root)
        table_frame.pack(fill="x", pady=(0, 6))
        self.table = ttk.Treeview(table_frame, columns=("channel", "mode", "value", "unit", "status", "time", "count"), show="headings", height=5)
        for key, label, width in [("channel", "通道 / 曲线颜色", 130), ("mode", "测量功能", 140), ("value", "最新读数", 150), ("unit", "单位", 80),
                                  ("status", "状态", 100), ("time", "最近更新时间", 230), ("count", "已保存点数", 100)]:
            self.table.heading(key, text=label)
            self.table.column(key, width=width, anchor="center")
        scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=self.table.yview)
        self.table.configure(yscrollcommand=scrollbar.set)
        self.table.pack(side="left", fill="x", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.chart_tabs = ttk.Notebook(root)
        self.chart_tabs.pack(fill="both", expand=True)
        self.canvases = {}
        for unit in ("Ohm", "V", "A"):
            canvas = tk.Canvas(self.chart_tabs, bg="#101c30", highlightthickness=0, height=200)
            self.chart_tabs.add(canvas, text={"Ohm": "电阻 / Ω", "V": "电压 / V", "A": "电流 / A"}[unit])
            canvas.bind("<Configure>", lambda _: setattr(self, "dirty", True))
            self.canvases[unit] = canvas
        ttk.Label(root, text="每通道显示最近 2000 点；曲线按单位分页。表格显示全部通道，各通道依次测量；TXT 保存全部读数。").pack(anchor="w", pady=(6, 0))
        ttk.Label(root, textvariable=self.stats).pack(anchor="w")
        ttk.Label(root, textvariable=self.file_label, wraplength=1000).pack(anchor="w")
        ttk.Label(root, textvariable=self.warning_text, foreground="#a04b00", wraplength=1000).pack(anchor="w")
        ttk.Label(root, textvariable=self.status, wraplength=1000).pack(anchor="w", pady=(6, 0))

    def refresh_ports(self):
        try:
            from serial.tools import list_ports
            ports = list(list_ports.comports())
            self.port_hint.config(text="可用串口：" + ("；".join(p.device for p in ports) or "未检测到，可手动输入 COM 名称"))
            if ports and not self.vars["port"].get():
                self.vars["port"].set(ports[0].device)
        except ImportError:
            self.port_hint.config(text="未安装 pyserial：运行 python -m pip install -r requirements.txt；仍可使用模拟模式")

    def update_channels(self, active=False):
        self.config_busy = active
        self.config_table.configure(selectmode="none" if active else "extended")
        model = self.vars["input_source"].get()
        if model == "FRONT":
            return
        visible = set()
        for ch, config in self.channel_configs.items():
            if ch % 100 > (42 if model == "7702" else 22):
                self.channel_vars[ch].set(False)
                continue
            visible.add(str(ch))
            if ch not in valid_channels(config.mode, model):
                self.channel_vars[ch].set(False)
                mode = "直流电流 DCI" if ch % 100 in ((41, 42) if model == "7702" else (21, 22)) else "直流电压 DCV"
                config = self.channel_configs[ch] = ChannelConfig(ch, mode)
            values = ("✓" if self.channel_vars[ch].get() else "—", ch, config.mode, config.measure_range,
                      f"{config.nplc:g}", f"{config.settle:g}")
            if self.config_table.exists(str(ch)):
                self.config_table.item(str(ch), values=values)
            else:
                self.config_table.insert("", "end", iid=str(ch), values=values)
        for item in self.config_table.get_children():
            if item not in visible:
                self.config_table.delete(item)
        for index, item in enumerate(sorted(visible, key=int)):
            self.config_table.move(item, "", index)

    def mark_editor_dirty(self, *_):
        if not self.loading_editor:
            self.editor_dirty = True

    def load_channel_editor(self, _=None):
        selected = self.config_table.selection()
        if not selected or self.config_busy:
            return
        config = self.channel_configs[int(selected[0])]
        self.loading_editor = True
        for key, value in [("edit_mode", config.mode), ("edit_range", config.measure_range),
                           ("edit_nplc", f"{config.nplc:g}"), ("edit_settle", f"{config.settle:g}")]:
            self.vars[key].set(value)
        self.loading_editor = False
        self.editor_dirty = False

    def apply_channel_editor(self):
        if self.config_busy:
            return
        try:
            model = self.vars["input_source"].get()
            if model == "FRONT":
                raise ValueError("前面板采集请在连接与前面板参数页设置")
            selected = self.config_table.selection()
            if not selected:
                raise ValueError("请先选中需要配置的通道行")
            changes = []
            for channel in selected:
                config = ChannelConfig(int(channel), self.vars["edit_mode"].get(),
                                       self.vars["edit_range"].get().strip().upper(),
                                       float(self.vars["edit_nplc"].get()), float(self.vars["edit_settle"].get()))
                config.validate(model)
                changes.append(config)
            for config in changes:
                self.channel_configs[config.channel] = config
                self.channel_vars[config.channel].set(True)
            self.editor_dirty = False
            self.update_channels()
        except Exception as exc:
            messagebox.showerror("无法应用通道参数", str(exc), parent=self)

    def disable_selected(self):
        if not self.config_busy:
            for channel in self.config_table.selection():
                self.channel_vars[int(channel)].set(False)
            self.update_channels()

    def disable_all(self):
        if not self.config_busy:
            for variable in self.channel_vars.values():
                variable.set(False)
            self.update_channels()

    def update_hint(self):
        if self.vars["input_source"].get() == "FRONT":
            text = "输入开关切至 F。前面板使用本页参数，模块通道配置不参与采集。"
        else:
            offset = 20 if self.vars["input_source"].get() == "7702" else 10
            text = f"输入开关切至 R。模块采集请在逐通道配置页设置；四线感测配对 +{offset}，不可再启用感测通道。"
        self.wiring.configure(text=text)
        self.update_channels()

    def choose_folder(self):
        folder = filedialog.askdirectory(parent=self)
        if folder:
            self.vars["folder"].set(folder)

    def busy(self, active):
        for widget, state in self.controls:
            widget.configure(state="disabled" if active else state)
        self.update_channels(active)
        self.start_button.configure(state="disabled" if active else "normal")
        self.probe_button.configure(state="disabled" if active else "normal")
        self.stop_button.configure(state="normal" if active else "disabled")
        self.export_button.configure(state="disabled" if active or self.exporting else "normal")

    def start(self, probe=False):
        if self.worker and self.worker.is_alive():
            return
        try:
            if self.vars["input_source"].get() != "FRONT" and self.editor_dirty:
                raise ValueError("编辑区的通道参数尚未应用，请先点击“应用并启用选中通道”")
            settings = Settings(**{key: self.vars[key].get().strip() for key in ("port", "terminator", "mode", "measure_range")},
                                baud=int(self.vars["baud"].get()), timeout=float(self.vars["timeout"].get()),
                                interval=float(self.vars["interval"].get()), nplc=float(self.vars["nplc"].get()),
                                simulation=self.simulation.get(), flow=self.flow.get(),
                                input_source=self.vars["input_source"].get(),
                                channels=tuple(ch for ch, v in self.channel_vars.items() if v.get()),
                                settle=float(self.vars["settle"].get()),
                                channel_configs=tuple(self.channel_configs[ch] for ch, v in self.channel_vars.items() if v.get())
                                if self.vars["input_source"].get() != "FRONT" else ())
            settings.validate()
            self.active_settings = settings
            path = None
            if not probe:
                folder = Path(self.vars["folder"].get()).expanduser().resolve()
                folder.mkdir(parents=True, exist_ok=True)
                prefix = "SIM" if settings.simulation else "K2700"
                path = folder / f"{prefix}_{datetime.now():%Y%m%d_%H%M%S_%f}.txt"
                self.points = {str(ch) if ch else "FRONT": deque(maxlen=2000) for ch in settings.active_channels}
                self.channel_counts = dict.fromkeys(self.points, 0)
                self.point_modes = {str(ch) if ch else "FRONT": settings.config_for(ch).mode for ch in settings.active_channels}
                first_unit = MODES[settings.config_for(settings.active_channels[0]).mode][1]
                self.chart_tabs.select(self.canvases[first_unit])
                for item in self.table.get_children():
                    self.table.delete(item)
                for i, channel in enumerate(self.points):
                    color = self.color(i)
                    self.table.tag_configure(channel, foreground=color)
                    self.table.insert("", "end", iid=channel, values=(channel, self.point_modes[channel], "—", MODES[self.point_modes[channel]][1], "等待", "—", 0), tags=(channel,))
                self.current_file = None
                self.file_label.set("TXT：正在配置仪器，成功后创建文件")
                self.stats.set("已保存 0 点")
                self.readout.set("—")
                self.dirty = True
            self.stop_event.clear()
            self.warning_text.set("")
            self.busy(True)
            self.status.set("正在连接 / 配置…" + ("【模拟模式】" if settings.simulation else ""))
            self.worker = threading.Thread(target=collect, args=(settings, path, self.stop_event,
                                           lambda kind, data: self.events.put((kind, data)), probe), daemon=False)
            self.worker.start()
        except Exception as exc:
            messagebox.showerror("无法开始", str(exc), parent=self)

    def stop(self):
        self.stop_event.set()
        self.status.set("正在停止并关闭文件 / 串口…")
        self.stop_button.configure(state="disabled")

    def export(self):
        if self.exporting or (self.worker and self.worker.is_alive()):
            return
        source = filedialog.askopenfilename(parent=self, title="选择要转换的 TXT（可选择历史文件）",
                                           initialdir=self.vars["folder"].get(), filetypes=[("采集数据", "*.txt")])
        if not source:
            return
        dest = filedialog.asksaveasfilename(parent=self, title="保存 CSV（请选择未使用的文件名）", defaultextension=".csv",
                                          initialdir=str(Path(source).parent), initialfile=Path(source).stem + ".csv",
                                          filetypes=[("CSV", "*.csv")])
        if not dest:
            return
        self.exporting = True
        self.export_button.configure(state="disabled")
        self.status.set("正在转换 CSV…")
        def task():
            try:
                count = convert_csv(source, dest)
                self.events.put(("exported", f"已导出 {count} 点：{dest}"))
            except Exception as exc:
                self.events.put(("export_error", str(exc)))
        threading.Thread(target=task, daemon=False).start()

    def poll(self):
        for _ in range(500):
            try:
                kind, data = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "sample":
                index, row, elapsed = data
                value = float(row["value"]) if row["value"] else math.nan
                channel = row["channel"]
                self.points[channel].append((elapsed, value))
                self.channel_counts[channel] += 1
                self.table.item(channel, values=(channel, row["mode"], f"{value:.9g}" if row["status"] == "OK" else "—", row["unit"],
                                                row["status"], row["timestamp"], self.channel_counts[channel]))
                self.readout.set(f"{channel}: {value:.8g} {row['unit']}" if row["status"] == "OK" else f"{channel}: OVERLOAD")
                self.stats.set(f"已保存 {index} 点 · 最近读数时间 {row['timestamp']} · {'模拟模式' if self.active_settings.simulation else '实测'}")
                self.status.set("正在采集；每点已刷新并同步到 TXT" if row["status"] == "OK" else "超量程 / 无效测量：已记录状态，曲线断开")
                self.dirty = True
            elif kind == "identity":
                self.status.set("设备：" + data)
            elif kind == "modules":
                self.modules_text.set("模块检测：" + data)
            elif kind == "warning":
                previous = self.warning_text.get()
                self.warning_text.set((previous + "；" if previous else "") + data)
            elif kind == "file":
                self.current_file = data
                self.file_label.set("TXT：" + data)
            elif kind in ("error", "export_error"):
                self.status.set("错误：" + data)
                if kind == "export_error":
                    self.exporting = False
                if not self.closing:
                    messagebox.showerror("操作失败", data, parent=self)
            elif kind == "done":
                self.busy(False)
                if not self.status.get().startswith("错误"):
                    self.status.set("已停止，TXT 已保存；可选择 TXT → CSV 导出" if self.current_file else "连接测试结束")
            elif kind == "info":
                self.status.set(data)
            elif kind == "exported":
                self.exporting = False
                self.status.set(data)
        if not self.exporting and not (self.worker and self.worker.is_alive()):
            self.export_button.configure(state="normal")
        if self.dirty:
            self.draw()
            self.dirty = False
        if self.closing and not self.exporting and not (self.worker and self.worker.is_alive()):
            self.destroy()
            return
        self.after(100, self.poll)

    @staticmethod
    def color(index):
        # Distinct hues across the selected voltage channels.
        import colorsys
        r, g, b = colorsys.hsv_to_rgb((index * 0.61803398875) % 1, 0.65, 0.82)
        return f"#{int(r*255):02x}{int(g*255):02x}{int(b*255):02x}"

    def draw(self):
        for unit, canvas in self.canvases.items():
            self.draw_unit(canvas, unit)

    def draw_unit(self, c, unit):
        c.delete("all")
        w, h = c.winfo_width(), c.winfo_height()
        if w < 150 or h < 100:
            return
        left, top, right, bottom = 100, 24, w - 24, h - 40
        series = {ch: list(points) for ch, points in self.points.items() if MODES[self.point_modes[ch]][1] == unit}
        points = sorted(point for items in series.values() for point in items)
        finite = [v for _, v in points if math.isfinite(v)]
        lo, hi = (min(finite), max(finite)) if finite else (0, 1)
        pad = max((hi - lo) * 0.1, abs(hi) * 1e-6, 1e-12)
        lo, hi = lo - pad, hi + pad
        xlo, xhi = (points[0][0], max(points[-1][0], points[0][0] + 1)) if points else (0, 10)
        for i in range(6):
            y = top + (bottom - top) * i / 5
            c.create_line(left, y, right, y, fill="#2a3950")
            c.create_text(left - 8, y, anchor="e", text=f"{hi - (hi - lo) * i / 5:.6g}", fill="#bccbe0")
            x = left + (right - left) * i / 5
            c.create_text(x, bottom + 16, text=f"{xlo + (xhi - xlo) * i / 5:.1f}s", fill="#bccbe0")
        c.create_text(left, 10, anchor="w", text=unit, fill="#bccbe0")
        indices = {channel: index for index, channel in enumerate(self.points)}
        for channel, items in series.items():
            color = self.color(indices[channel])
            segment = []
            def paint():
                if len(segment) >= 4:
                    c.create_line(*segment, fill=color, width=2)
                elif segment:
                    x, y = segment
                    c.create_oval(x - 3, y - 3, x + 3, y + 3, fill=color, outline="")
            for t, value in items:
                if not math.isfinite(value):
                    paint()
                    segment = []
                    continue
                segment.extend([left + (t - xlo) / (xhi - xlo) * (right - left),
                                bottom - (value - lo) / (hi - lo) * (bottom - top)])
            paint()
        if not points:
            c.create_text((left + right) / 2, (top + bottom) / 2, text=f"等待 {unit} 通道数据", fill="#bccbe0", font=("Microsoft YaHei UI", 16))

    def close_app(self):
        self.closing = True
        self.stop_event.set()
        self.status.set("正在结束采集 / 导出并关闭…")


if __name__ == "__main__":
    App().mainloop()
