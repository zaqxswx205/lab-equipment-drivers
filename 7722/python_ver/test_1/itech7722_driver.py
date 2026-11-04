"""ITech 7722 驱动主程序：GUI + 运行引擎 + 数据记录 + 功率曲线。

设备交互在后台线程，UI/绘图更新通过队列回主线程。
"""

from __future__ import annotations

import os
import queue
import math
import threading
from collections import deque
from datetime import datetime

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import matplotlib
matplotlib.use("TkAgg")
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

matplotlib.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DengXin", "sans-serif"]
matplotlib.rcParams["axes.unicode_minus"] = False

from itech7722_config import AppConfig, CONTROL_NAMES, MODE_NAMES, PARAMS, load_config, save_config, validate_run
from itech7722_device import (CONTROL_CV, DeviceError, ITech7722, MODE_AC,
                              MODE_ACDC, MODE_DC, build_transport, error_code, list_visa_resources)
from itech7722_recorder import Recorder, Sample
from itech7722_sequence import SequenceError, load_sequence

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CFG = os.path.join(HERE, "config.ini")
UNIT_BY_CONTROL = {CONTROL_CV: "V", "CI": "A", "CP": "W"}
CONTROL_BY_NAME = {name: code for code, name in CONTROL_NAMES.items()}


def _num(text, default=None):
    txt = str(text).strip()
    if txt == "":
        return default
    value = float(txt)
    if not math.isfinite(value):
        raise ValueError("数值必须有限，不能为 NaN 或无穷大")
    return value


from itech7722_engine import Engine


class App:
    def __init__(self, root: tk.Tk, cfg: AppConfig, config_path=DEFAULT_CFG):
        self.root = root
        self.cfg = cfg
        self.config_path = config_path
        self.q: queue.Queue = queue.Queue()
        self.engine = Engine(self)
        self.steps = []
        self._closed = False
        self._diagnostic_busy = False
        self._diagnostics = deque(maxlen=500)
        self._poll_job = None
        self._chart_job = None
        self._chart_size = (0, 0)
        self._draw_job = None
        self._x: list[float] = []
        self._p: list[float] = []

        root.title("ITech 7722 驱动程序")
        root.geometry("1280x860")
        root.minsize(940, 620)
        self._build_ui()
        self._apply_cfg_to_ui(cfg)
        self._on_mode_change()
        self._on_outmode_change()
        self._poll_job = self.root.after(60, self._poll)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        # 兜底：绕过 _on_close 直接 destroy（如宿主脚本收尾）时也要停掉 after 轮询
        root.bind("<Destroy>", self._on_destroy, add="+")

    # ---------- 布局 ----------
    def _build_ui(self):
        style = ttk.Style(self.root)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        self._configure_checkmarks(style)
        style.configure("TLabelframe", padding=6)

        self.status_var = tk.StringVar(value="就绪")
        status = ttk.Label(self.root, textvariable=self.status_var, anchor=tk.W, padding=(10, 4))
        status.pack(side=tk.BOTTOM, fill=tk.X)

        outer = ttk.Panedwindow(self.root, orient=tk.VERTICAL)
        outer.pack(fill=tk.BOTH, expand=True)

        top = ttk.Frame(outer, padding=(8, 6))
        outer.add(top, weight=0)
        mid = ttk.Panedwindow(outer, orient=tk.HORIZONTAL)
        outer.add(mid, weight=4)

        self._build_conn_box(top)
        self._build_mode_box(top)
        self._build_record_box(top)
        top.columnconfigure(1, weight=1)
        top.columnconfigure(2, weight=1)

        self._build_table(mid)
        self._build_chart(mid)

    def _configure_checkmarks(self, style):
        """显式使用空框/勾号图标，避开clam主题把选中状态画成叉号。"""
        empty = tk.PhotoImage(master=self.root, width=18, height=18)
        checked = tk.PhotoImage(master=self.root, width=18, height=18)
        for icon in (empty, checked):
            icon.put("#718096", to=(1, 1, 16, 16))
            icon.put("#ffffff", to=(2, 2, 15, 15))
        checked.put("#16834a", to=(1, 1, 16, 16))
        for x, y in ((4, 8), (5, 9), (6, 10), (7, 11), (8, 10),
                     (9, 9), (10, 8), (11, 7), (12, 6), (13, 5)):
            checked.put("#ffffff", to=(x, y, x + 2, y + 2))
        self._check_images = (empty, checked)
        element = f"ClearCheck{id(self)}.indicator"
        style.element_create(element, "image", empty, ("selected", checked))
        style.layout("Clear.TCheckbutton", [
            ("Checkbutton.padding", {"sticky": "nswe", "children": [
                (element, {"side": "left", "sticky": ""}),
                ("Checkbutton.focus", {"side": "left", "sticky": "w", "children": [
                    ("Checkbutton.label", {"sticky": "nswe"})]})]})])

    def _build_conn_box(self, parent):
        box = ttk.LabelFrame(parent, text="设备连接")
        box.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S), padx=(0, 6))
        for col in (1, 3):
            box.columnconfigure(col, weight=1)

        self.var_conn_type = tk.StringVar()
        self.var_port = tk.StringVar()
        self.var_baud = tk.StringVar(value="9600")
        self.var_host = tk.StringVar()
        self.var_tcpport = tk.StringVar(value="9999")
        self.var_resource = tk.StringVar()
        self.var_conn_hint = tk.StringVar()

        ttk.Label(box, text="接口").grid(row=0, column=0, sticky=tk.W)
        self.conn_type_combo = ttk.Combobox(box, textvariable=self.var_conn_type, state="readonly",
                                            width=9, values=["serial", "tcp", "visa"])
        self.conn_type_combo.grid(row=0, column=1, sticky=tk.W, padx=4)
        self.conn_type_combo.bind("<<ComboboxSelected>>", lambda e: self._update_conn_hint())
        ttk.Label(box, text="串口").grid(row=0, column=2, sticky=tk.W)
        self.port_combo = ttk.Combobox(box, textvariable=self.var_port, width=9,
                                       values=self._serial_ports())
        self.port_combo.grid(row=0, column=3, sticky=tk.EW, padx=4)
        ttk.Label(box, text="波特率").grid(row=1, column=0, sticky=tk.W, pady=(4, 0))
        ttk.Combobox(box, textvariable=self.var_baud, width=9,
                     values=["9600", "19200", "38400", "57600", "115200"]).grid(row=1, column=1,
                                                                                sticky=tk.W, padx=4, pady=(4, 0))
        ttk.Label(box, text="主机IP").grid(row=1, column=2, sticky=tk.W, pady=(4, 0))
        ttk.Entry(box, textvariable=self.var_host, width=12).grid(row=1, column=3, sticky=tk.EW,
                                                                  padx=4, pady=(4, 0))
        ttk.Label(box, text="TCP端口").grid(row=2, column=0, sticky=tk.W, pady=(4, 0))
        ttk.Entry(box, textvariable=self.var_tcpport, width=9).grid(row=2, column=1, sticky=tk.W,
                                                                    padx=4, pady=(4, 0))
        ttk.Label(box, text="VISA资源").grid(row=2, column=2, sticky=tk.W, pady=(4, 0))
        ttk.Entry(box, textvariable=self.var_resource, width=12).grid(row=2, column=3, sticky=tk.EW,
                                                                      padx=4, pady=(4, 0))
        self.conn_hint_label = ttk.Label(box, textvariable=self.var_conn_hint, foreground="#555",
                                         wraplength=250, justify=tk.LEFT)
        self.conn_hint_label.grid(row=4, column=0, columnspan=4, sticky=tk.W, pady=(4, 0))

        btns = ttk.Frame(box)
        btns.grid(row=5, column=0, columnspan=4, sticky=(tk.W, tk.E), pady=(6, 0))
        ttk.Button(btns, text="扫描串口", command=self._refresh_ports).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(btns, text="扫描VISA", command=self._scan_visa).pack(side=tk.LEFT)

        btns2 = ttk.Frame(box)
        btns2.grid(row=6, column=0, columnspan=4, sticky=(tk.W, tk.E), pady=(6, 0))
        self.btn_conn = ttk.Button(btns2, text="连接", command=self.on_connect)
        self.btn_conn.pack(side=tk.LEFT)
        ttk.Button(btns2, text="*IDN? 自检", command=self.on_idn).pack(side=tk.LEFT, padx=6)
        ttk.Button(btns2, text="读错误队列", command=self.on_read_errors).pack(side=tk.LEFT)
        self.var_idn = tk.StringVar(value="未连接")
        idn_box = ttk.Frame(box)
        idn_box.grid(row=7, column=0, columnspan=4, sticky=tk.EW, pady=(6, 0))
        idn_box.columnconfigure(0, weight=1)
        self.idn_label = tk.Text(idn_box, width=30, height=4, wrap=tk.CHAR,
                                 background="white", foreground="black",
                                 relief=tk.SUNKEN, borderwidth=1)
        self.idn_label.grid(row=0, column=0, sticky=tk.NSEW)
        idn_scroll = ttk.Scrollbar(idn_box, orient=tk.VERTICAL, command=self.idn_label.yview)
        idn_scroll.grid(row=0, column=1, sticky=tk.NS)
        self.idn_label.configure(yscrollcommand=idn_scroll.set)
        self.var_idn.trace_add("write", self._update_idn_text)
        self._update_idn_text()

    def _update_idn_text(self, *_):
        """同步连接提示，保留换行且禁止用户修改设备状态。"""
        self.idn_label.configure(state=tk.NORMAL)
        self.idn_label.delete("1.0", tk.END)
        self.idn_label.insert("1.0", self.var_idn.get())
        self.idn_label.configure(state=tk.DISABLED)
        self.idn_label.yview_moveto(0)

    def _build_mode_box(self, parent):
        box = ttk.LabelFrame(parent, text="输出设定")
        box.grid(row=0, column=1, sticky=(tk.W, tk.E, tk.N, tk.S), padx=6)
        box.columnconfigure(2, weight=1)
        box.columnconfigure(5, weight=1)
        box.rowconfigure(4, weight=1)

        self.var_mode = tk.StringVar()
        self.var_outmode = tk.StringVar()
        self.var_control = tk.StringVar(value=CONTROL_NAMES[CONTROL_CV])
        self.var_value = tk.StringVar(value="5")
        self.var_freq = tk.StringVar(value="50")
        self.var_maxv = tk.StringVar(value="300")
        self.var_maxi = tk.StringVar(value="6")
        self.var_maxp = tk.StringVar(value="600")
        self.var_feedback_maxv = tk.StringVar(value="5")
        self.var_feedback_step = tk.StringVar(value="0.2")

        modes = ttk.Frame(box)
        modes.grid(row=0, column=0, columnspan=6, sticky=tk.EW)
        modes.columnconfigure(0, weight=1)
        modes.columnconfigure(1, weight=1)
        mb = ttk.LabelFrame(modes, text="工作模式")
        mb.grid(row=0, column=0, sticky=tk.EW, padx=(0, 3))
        for label, code in (("直流", MODE_DC), ("交流", MODE_AC), ("交直流", MODE_ACDC)):
            ttk.Radiobutton(mb, text=label, variable=self.var_mode, value=code,
                            command=self._on_mode_change).pack(side=tk.LEFT, padx=2)

        ob = ttk.LabelFrame(modes, text="输出方式")
        ob.grid(row=0, column=1, sticky=tk.EW, padx=(3, 0))
        for label, code in (("定值模式", "hold"), ("序列模式", "seq")):
            ttk.Radiobutton(ob, text=label, variable=self.var_outmode, value=code,
                            command=self._on_outmode_change).pack(side=tk.LEFT, padx=2)

        self.hold_box = ttk.LabelFrame(box, text="定值设定")
        self.hold_box.grid(row=1, column=0, columnspan=6, sticky=(tk.W, tk.E), pady=(6, 0))
        self.hold_box.columnconfigure(1, weight=1)
        self.hold_box.columnconfigure(3, weight=1)
        ttk.Label(self.hold_box, text="控制量").grid(row=0, column=0, sticky=tk.W)
        self.cb_control = ttk.Combobox(self.hold_box, textvariable=self.var_control, state="readonly",
                                       values=list(CONTROL_NAMES.values()), width=16)
        self.cb_control.grid(row=0, column=1, sticky=tk.EW, padx=4)
        self.cb_control.bind("<<ComboboxSelected>>", lambda e: self._update_value_hint())
        self.ent_value = ttk.Entry(self.hold_box, textvariable=self.var_value)
        self.ent_value.grid(row=0, column=3, sticky=tk.EW, padx=4)
        self.lbl_value_hint = ttk.Label(self.hold_box, text="设定值(V)")
        self.lbl_value_hint.grid(row=0, column=2, sticky=tk.W)
        self.lbl_freq_hint = ttk.Label(self.hold_box, text="频率(Hz)")
        self.lbl_freq_hint.grid(row=1, column=2, sticky=tk.W, pady=(4, 0))
        self.ent_freq = ttk.Entry(self.hold_box, textvariable=self.var_freq)
        self.ent_freq.grid(row=1, column=3, sticky=tk.EW, padx=4, pady=(4, 0))
        ttk.Label(self.hold_box, text="频率(直流忽略)").grid(row=1, column=0, columnspan=2,
                                                            sticky=tk.W, pady=(4, 0))
        ttk.Label(self.hold_box, text="闭环电压上限V").grid(row=2, column=0, sticky=tk.W, pady=(4, 0))
        self.ent_feedback_maxv = ttk.Entry(self.hold_box, textvariable=self.var_feedback_maxv, width=9)
        self.ent_feedback_maxv.grid(row=2, column=1, sticky=tk.EW, padx=4, pady=(4, 0))
        ttk.Label(self.hold_box, text="升压步长V").grid(row=2, column=2, sticky=tk.W, pady=(4, 0))
        self.ent_feedback_step = ttk.Entry(self.hold_box, textvariable=self.var_feedback_step, width=9)
        self.ent_feedback_step.grid(row=2, column=3, sticky=tk.EW, padx=4, pady=(4, 0))

        lim = ttk.LabelFrame(box, text="保护限值（启动对象的电压/电流/功率上限）")
        lim.grid(row=2, column=0, columnspan=6, sticky=(tk.W, tk.E), pady=(6, 0))
        for col in (1, 3, 5):
            lim.columnconfigure(col, weight=1)
        ttk.Label(lim, text="最大电压V").grid(row=0, column=0, sticky=tk.W)
        ttk.Entry(lim, textvariable=self.var_maxv).grid(row=0, column=1, sticky=tk.EW, padx=(2, 8))
        ttk.Label(lim, text="最大电流A").grid(row=0, column=2, sticky=tk.W)
        ttk.Entry(lim, textvariable=self.var_maxi).grid(row=0, column=3, sticky=tk.EW, padx=(2, 8))
        ttk.Label(lim, text="最大功率W").grid(row=0, column=4, sticky=tk.W)
        ttk.Entry(lim, textvariable=self.var_maxp).grid(row=0, column=5, sticky=tk.EW, padx=2)

        self.seq_box = ttk.LabelFrame(box, text="序列（时序）配置")
        self.seq_box.grid(row=3, column=0, columnspan=6, sticky=(tk.W, tk.E), pady=(6, 0))
        self.seq_box.columnconfigure(1, weight=1)
        self.var_seqfile = tk.StringVar()
        self.var_loop = tk.BooleanVar(value=False)
        self.var_stepinfo = tk.StringVar(value="未载入")
        ttk.Label(self.seq_box, text="文件").grid(row=0, column=0, sticky=tk.W)
        ttk.Entry(self.seq_box, textvariable=self.var_seqfile).grid(row=0, column=1, sticky=tk.EW, padx=4)
        ttk.Button(self.seq_box, text="浏览…", command=self.on_browse_seq, width=7).grid(row=0, column=2)
        ttk.Button(self.seq_box, text="载入", command=self.on_load_seq, width=6).grid(row=0, column=3,
                                                                                      padx=(4, 0))
        ttk.Checkbutton(self.seq_box, style="Clear.TCheckbutton", text="循环", variable=self.var_loop).grid(row=1, column=0,
                                                                               sticky=tk.W, pady=(4, 0))
        ttk.Label(self.seq_box, textvariable=self.var_stepinfo).grid(row=1, column=1, columnspan=3,
                                                                     sticky=tk.W, pady=(4, 0))

        actions = ttk.Frame(box)
        actions.grid(row=4, column=0, columnspan=6, sticky=tk.SW, pady=(8, 0))
        self.btn_start = ttk.Button(actions, text="启动", command=self.on_start)
        self.btn_start.pack(side=tk.LEFT, padx=(0, 6))
        self.btn_stop = ttk.Button(actions, text="停止", command=self.on_stop, state=tk.DISABLED)
        self.btn_stop.pack(side=tk.LEFT)

    def _build_record_box(self, parent):
        box = ttk.LabelFrame(parent, text="数据记录")
        box.grid(row=0, column=2, sticky=(tk.W, tk.E, tk.N, tk.S), padx=(6, 0))
        box.columnconfigure(1, weight=1)

        self.var_interval = tk.StringVar(value="1")
        self.var_record = tk.BooleanVar(value=True)
        ttk.Checkbutton(box, style="Clear.TCheckbutton", text="启用记录", variable=self.var_record).grid(row=0, column=0,
                                                                            columnspan=2, sticky=tk.W)
        ttk.Label(box, text="记录间隔(s)").grid(row=1, column=0, sticky=tk.W, pady=(4, 0))
        ttk.Entry(box, textvariable=self.var_interval).grid(row=1, column=1, sticky=tk.EW, padx=4,
                                                            pady=(4, 0))
        self.btn_open_logs = ttk.Button(box, text="打开记录文件", command=self.on_open_logs)
        self.btn_open_logs.grid(row=2, column=0, columnspan=2, sticky=tk.W, pady=(6, 0))
        self._build_keypoint_rules(box)

    def _build_table(self, parent):
        frame = ttk.LabelFrame(parent, text="数据记录（滚动显示最近 2000 行）")
        parent.add(frame, weight=1)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        cols = ("time", "elapsed", "voltage", "current", "power", "power_factor", "status")
        heads = ("时间", "经过s", "电压V", "电流A", "功率W", "功率因子", "状态")
        widths = (118, 58, 74, 74, 78, 66, 120)
        self.tree = ttk.Treeview(frame, columns=cols, show="headings")
        for c, h, w in zip(cols, heads, widths):
            self.tree.heading(c, text=h)
            self.tree.column(c, width=w, minwidth=40, stretch=True,
                             anchor=tk.W if c == "time" else tk.E)
        ys = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=ys.set)
        self.tree.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        ys.grid(row=0, column=1, sticky=(tk.N, tk.S))
        self.tree.tag_configure("alarm", foreground="#c00")
        self.tree.tag_configure("key", foreground="#06c")

    def _build_chart(self, parent):
        holder = ttk.LabelFrame(parent, text="功率曲线")
        parent.add(holder, weight=1)
        holder.columnconfigure(0, weight=1)
        holder.rowconfigure(0, weight=1)
        inner = ttk.Frame(holder)
        inner.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        inner.columnconfigure(0, weight=1)
        inner.rowconfigure(0, weight=1)
        self.chart_inner = inner

        self.fig = Figure(figsize=(6, 4), dpi=100)
        self.ax = self.fig.add_subplot(111)
        self.ax.set_xlabel("时间 (s)")
        self.ax.set_ylabel("功率 (W)")
        self.ax.grid(True, alpha=0.35)
        self.canvas = FigureCanvasTkAgg(self.fig, master=inner)
        self.canvas.get_tk_widget().grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        toolbar = ttk.Frame(holder)
        toolbar.grid(row=1, column=0, sticky=(tk.W, tk.E), pady=(4, 0))
        ttk.Button(toolbar, text="清空曲线", command=self._clear_chart).pack(side=tk.LEFT)
        ttk.Button(toolbar, text="导出曲线PNG", command=self._export_png).pack(side=tk.LEFT, padx=6)
        inner.bind("<Configure>", self._on_chart_configure)

    def _build_keypoint_rules(self, parent):
        box = ttk.LabelFrame(parent, text="关键设定电压记录")
        box.grid(row=3, column=0, columnspan=2, sticky=tk.NSEW, pady=(8, 0))
        box.columnconfigure(0, weight=1)
        box.rowconfigure(2, weight=1)
        parent.rowconfigure(3, weight=1)

        self.var_keyvolt = tk.StringVar()
        inputs = ttk.Frame(box)
        inputs.grid(row=0, column=0, columnspan=2, sticky=tk.EW)
        inputs.columnconfigure(1, weight=1)
        ttk.Label(inputs, text="设定电压(V)").grid(row=0, column=0, sticky=tk.W)
        ttk.Entry(inputs, textvariable=self.var_keyvolt, width=10).grid(
            row=0, column=1, sticky=tk.EW, padx=(4, 0))
        buttons = ttk.Frame(box)
        buttons.grid(row=1, column=0, columnspan=2, sticky=tk.W, pady=4)
        ttk.Button(buttons, text="添加", command=self.on_add_keypoint).pack(side=tk.LEFT)
        ttk.Button(buttons, text="删除选中", command=self.on_del_keypoint).pack(side=tk.LEFT, padx=4)

        self.key_tree = ttk.Treeview(box, columns=("volt",), show="headings", height=3)
        self.key_tree.heading("volt", text="关键设定电压(V)")
        self.key_tree.column("volt", width=140, minwidth=80, stretch=True)
        self.key_tree.grid(row=2, column=0, sticky=tk.NSEW)
        scrollbar = ttk.Scrollbar(box, orient=tk.VERTICAL, command=self.key_tree.yview)
        self.key_tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.grid(row=2, column=1, sticky=tk.NS)

    # ---------- 配置同步 ----------
    def _serial_ports(self):
        return [p[0] for p in self._serial_ports_detailed()]

    def _serial_ports_detailed(self):
        """返回 (设备名, VID, PID, 描述)，VID 用于认出 ITECH 的 USB 虚拟串口。"""
        try:
            from serial.tools import list_ports
        except Exception:
            return []
        out = []
        for p in list_ports.comports():
            out.append((p.device, getattr(p, "vid", None), getattr(p, "pid", None),
                        p.description or ""))
        return out

    def _refresh_ports(self):
        found = self._serial_ports_detailed()
        ports = [p[0] for p in found]
        self.port_combo.configure(values=ports)
        if not self.engine.device:
            self.var_conn_type.set("serial")
            if self.var_port.get() not in ports:
                self.var_port.set(ports[0] if len(ports) == 1 else "")
            self._update_conn_hint()
        description = "; ".join(f"{p[0]} ({p[3]})" for p in found)
        self.set_status("可用串口: " + (description or "未发现串口，请检查连接与驱动")
                        + ("；请选择串口并连接，用 *IDN? 确认设备" if ports else ""))

    CONN_HINTS = {
        "serial": "USB 虚拟串口或 RS232：扫描并选择 COM 口，按设备设置波特率；连接后用 *IDN? 确认型号。",
        "tcp": "网口 SCPI：填设备 IP 与端口（ITECH 常见 9999）。",
        "visa": "直接给 VISA 资源名，USB/GPIB/网口通吃，需要 pyvisa 或 NI/Keysight VISA。",
    }

    def _update_conn_hint(self):
        self.var_conn_hint.set(self.CONN_HINTS.get(self.var_conn_type.get().lower(), ""))

    def _scan_visa(self):
        try:
            found = list_visa_resources()
        except DeviceError as exc:
            messagebox.showwarning("扫描失败", str(exc))
            self.set_status("VISA 扫描失败：后端不可用", alarm=True)
            return
        if not found:
            messagebox.showinfo("扫描结果", "未发现VISA仪器。USB虚拟串口请点“扫描串口”，选择serial接口连接。")
            self.set_status("VISA未发现仪器；可扫描串口选择COM接口")
            return
        if not self.var_resource.get().strip():
            self.var_resource.set(found[0])
        self.set_status("VISA 资源: " + ", ".join(found))

    def _collect_cfg(self) -> AppConfig:
        cfg = self.cfg.copy()
        if self.var_conn_type.get() == "serial" and not self.var_port.get().strip():
            raise ValueError("请选择串口")
        cfg.connection.update({
            "type": self.var_conn_type.get().strip().lower() or "serial",
            "port": self.var_port.get().strip(),
            "baudrate": int(_num(self.var_baud.get(), 9600)),
            "host": self.var_host.get().strip() or "192.168.1.100",
            "tcp_port": int(_num(self.var_tcpport.get(), 9999)),
            "resource": self.var_resource.get().strip() or "TCPIP0::192.168.1.100::inst0::INSTR",
        })
        cfg.limits = {"max_voltage": _num(self.var_maxv.get(), 0.0),
                      "max_current": _num(self.var_maxi.get(), 0.0),
                      "max_power": _num(self.var_maxp.get(), 0.0)}
        cfg.setting = {"mode": self.var_mode.get(), "control": CONTROL_BY_NAME.get(
            self.var_control.get(), CONTROL_CV), "value": _num(self.var_value.get(), 0.0),
            "frequency": _num(self.var_freq.get(), 0.0), "output_mode": self.var_outmode.get()}
        cfg.sequence = {"file": self.var_seqfile.get().strip(), "loop": bool(self.var_loop.get())}
        cfg.recording = dict(self.cfg.recording,
                             interval_ms=int(_num(self.var_interval.get(), 1) * 1000),
                             enabled=bool(self.var_record.get()),
                             params=list(PARAMS))
        cfg.keypoints = dict(self.cfg.keypoints)
        cfg.feedback.update(max_voltage=_num(self.var_feedback_maxv.get(), 5),
                            step_voltage=_num(self.var_feedback_step.get(), 0.2))
        return cfg

    def _apply_cfg_to_ui(self, cfg: AppConfig):
        cfg = cfg.copy()
        if cfg.connection.get("type") in ("sim", "usb", "usbtmc"):
            cfg.connection["type"] = "serial"
        cfg.connection.pop("usb_resource", None)
        cfg.connection.pop("sim_load_resistance", None)
        cfg.keypoints = {v: list(PARAMS) for v in cfg.keypoints}
        self.cfg = cfg
        c = cfg.connection
        self.var_conn_type.set(str(c.get("type", "serial")))
        self.var_port.set(str(c.get("port", "COM6")))
        self.var_baud.set(str(c.get("baudrate", 9600)))
        self.var_host.set(str(c.get("host", "")))
        self.var_tcpport.set(str(c.get("tcp_port", 9999)))
        self.var_resource.set(str(c.get("resource", "")))
        self._update_conn_hint()
        self.var_maxv.set(f"{cfg.limits['max_voltage']:g}")
        self.var_maxi.set(f"{cfg.limits['max_current']:g}")
        self.var_maxp.set(f"{cfg.limits['max_power']:g}")
        self.var_mode.set(cfg.setting["mode"])
        self.var_outmode.set(cfg.setting.get("output_mode", "hold"))
        self.var_control.set(CONTROL_NAMES.get(cfg.setting["control"], CONTROL_NAMES[CONTROL_CV]))
        self.var_value.set(f"{cfg.setting['value']:g}")
        self.var_freq.set(f"{cfg.setting['frequency']:g}")
        self.var_feedback_maxv.set(f"{cfg.feedback['max_voltage']:g}")
        self.var_feedback_step.set(f"{cfg.feedback['step_voltage']:g}")
        self.var_seqfile.set(cfg.sequence.get("file", ""))
        self.var_loop.set(bool(cfg.sequence.get("loop")))
        self.var_interval.set(f"{cfg.recording.get('interval_ms', 1000) / 1000:g}")
        self.var_record.set(bool(cfg.recording.get("enabled", True)))
        self._refresh_key_tree()
        self._on_mode_change()
        self._on_outmode_change()

    def _refresh_key_tree(self):
        self.key_tree.delete(*self.key_tree.get_children())
        for volt in sorted(self.cfg.keypoints):
            self.key_tree.insert("", tk.END, values=(f"{volt:g}",))

    # ---------- 事件 ----------
    def _on_mode_change(self):
        dc = self.var_mode.get() == MODE_DC
        self.ent_freq.configure(state=tk.DISABLED if dc else tk.NORMAL)
        self._update_value_hint()

    def _on_outmode_change(self):
        seq = self.var_outmode.get() == "seq"
        if seq:
            self.hold_box.grid_remove()
        else:
            self.hold_box.grid()
        if seq:
            self.seq_box.grid()
            if not self.steps:
                self.on_load_seq(quiet=True)
        else:
            self.seq_box.grid_remove()

    def _update_value_hint(self):
        code = CONTROL_BY_NAME.get(self.var_control.get(), CONTROL_CV)
        for entry in (self.ent_feedback_maxv, self.ent_feedback_step):
            entry.configure(state=tk.DISABLED if code == CONTROL_CV else tk.NORMAL)
        self.lbl_value_hint.config(text=f"设定值({UNIT_BY_CONTROL.get(code, '')})")
        if self.var_mode.get() == MODE_DC:
            self.var_freq.set("0")

    def set_status(self, text, alarm=False):
        self.status_var.set(text)
        self.status_color = "#c00" if alarm else "#0a6"

    def log_scpi(self, direction, text):
        self.q.put(("scpi", f"{direction} {text}"))

    def post(self, kind, payload):
        self.q.put((kind, payload))

    def _poll(self):
        if self._closed:
            return
        self._poll_job = self.root.after(60, self._poll)
        for _ in range(300):
            try:
                kind, payload = self.q.get_nowait()
            except queue.Empty:
                return
            if kind == "scpi":
                self._append_scpi(payload)
            elif kind == "sample":
                self._on_sample(*payload)
            elif kind == "alarm":
                self.set_status(payload, alarm=True)
                self._append_scpi(f"!! {payload}")
            elif kind == "info":
                self.set_status(payload)
            elif kind == "step":
                self.set_status(f"当前步骤: {payload}")
            elif kind == "stopped":
                if self.engine.running:
                    continue
                self.set_status(payload, alarm=bool(self.engine.last_error))
                self._run_buttons(False)
            elif kind == "idn_result":
                self._diagnostic_busy = False
                device, idn, error = payload
                if device is not self.engine.device:
                    continue
                if error:
                    self.var_idn.set(error)
                    self.set_status(error, alarm=True)
                    self._append_scpi("!! " + error)
                else:
                    self.var_idn.set(f"已识别: {idn}")
                    self.set_status(f"设备已识别: {idn}")

    def _append_scpi(self, line):
        self._diagnostics.append(f"{datetime.now().strftime('%H:%M:%S.%f')[:-3]} {line}")

    def _on_sample(self, sample: Sample, control, value):
        tag = "alarm" if str(sample.status).startswith("保护") else ""
        item = self.tree.insert("", tk.END, values=(sample.timestamp, f"{sample.elapsed:.2f}",
                                              self._s(sample.voltage, 3), self._s(sample.current, 4),
                                              self._s(sample.power, 3), self._s(sample.power_factor, 4),
                                              sample.status), tags=(tag,) if tag else ())
        kids = self.tree.get_children()
        if len(kids) > 2000:
            self.tree.delete(*kids[:len(kids) - 2000])
        self.tree.see(item)
        self._x.append(sample.elapsed)
        self._p.append(sample.power or 0.0)
        for values in (self._x, self._p):
            del values[:-20000]
        self._schedule_draw()

    @staticmethod
    def _s(value, digits):
        return "--" if value is None else f"{value:.{digits}f}"

    def shutdown(self):
        """取消挂起的 after 任务并停掉轮询；宿主直接 destroy 窗口前应先调它。"""
        self._closed = True
        for job in (self._poll_job, self._chart_job, self._draw_job):
            if job:
                try:
                    self.root.after_cancel(job)
                except Exception:
                    pass
        self._poll_job = self._chart_job = self._draw_job = None

    def _on_destroy(self, event):
        if event.widget is not self.root:
            return
        self.shutdown()

    def _on_close(self):
        if self._diagnostic_busy:
            self.set_status("正在自检，查询结束后可关闭窗口")
            return
        try:
            self.engine.disconnect()
        except DeviceError as exc:
            self.set_status(str(exc), alarm=True)
            return
        try:
            self._save_preferences()
        except (ValueError, OSError) as exc:
            self.set_status(f"参数自动保存失败：{exc}，请修正后关闭", alarm=True)
            return
        self.shutdown()
        self.root.destroy()

    # ---------- 按钮动作 ----------
    def on_connect(self):
        if self._diagnostic_busy:
            self.set_status("正在自检，请等待查询完成后再操作连接")
            return
        if self.engine.device:
            try:
                self.engine.disconnect()
            except DeviceError as exc:
                self.set_status(str(exc), alarm=True)
                return
            self._run_buttons(False)
            self.btn_conn.configure(text="连接")
            self.var_idn.set("未连接")
            return
        try:
            cfg = self._collect_cfg()
        except ValueError as exc:
            messagebox.showerror("参数错误", f"数值无法解析: {exc}")
            return
        try:
            idn = self.engine.connect(cfg)
        except Exception as exc:
            messagebox.showerror("连接失败", f"{type(exc).__name__}: {exc}")
            return
        self.var_idn.set(f"已连接: {idn}")
        self.btn_conn.configure(text="断开")
        self.set_status(f"设备已识别: {idn}")

    def on_idn(self):
        if self.engine.running:
            self.set_status("运行期间请使用采样数据确认通信；停止后可执行自检")
            return
        if self._diagnostic_busy:
            self.set_status("*IDN? 正在自检，请等待")
            return
        if not self.engine.device:
            self.var_idn.set("未连接：请先查找串口并点击连接；连接时会自动执行 *IDN?")
            self.set_status("自检未执行：设备未连接", alarm=True)
            return
        device = self.engine.device
        self._diagnostic_busy = True
        self.var_idn.set(f"*IDN? 正在自检（{device.tp.name}）…")
        self.set_status("*IDN? 正在等待设备回复…")

        def query():
            idn, error = "", ""
            try:
                idn = device.identify().strip()
                if not idn:
                    error = (f"*IDN? 超时，无设备应答（{device.tp.name}）。"
                             "检查 COM 口、波特率和结束符；当前会话未确认型号。")
                elif "," not in idn or error_code(idn) is not None:
                    error = f"*IDN? 收到非型号应答: {idn!r}，可能有残留应答或通信参数不匹配"
            except Exception as exc:
                error = f"*IDN? 自检失败（{device.tp.name}）: {type(exc).__name__}: {exc}"
            self.post("idn_result", (device, idn, error))

        threading.Thread(target=query, daemon=True).start()

    def _collect_errors(self):
        try:
            return self.engine.device.error_queue()
        except Exception:
            return []

    def on_read_errors(self):
        """读出设备错误队列：-113=助记符不认识，-104=参数带逗号，-108/108=块数据未定义。"""
        if self.engine.running:
            self.set_status("运行期间由引擎检查错误队列，停止后可手动读取")
            return
        if not self.engine.device:
            messagebox.showwarning("提示", "请先连接设备")
            return
        errs = self._collect_errors()
        if not errs:
            self.set_status("错误队列为空（设备未报错）")
            self._append_scpi("SYST:ERR? -> 队列为空")
            return
        for e in errs:
            self._append_scpi(f"设备错误: {e}")
        bad = [e for e in errs if error_code(e) != 0]
        self.set_status("错误队列: " + "; ".join(errs), alarm=bool(bad))
        if any(error_code(e) == -113 for e in errs):
            self._append_scpi("!! -113 Undefined header：该助记符本机不认，"
                              "请检查设备型号与通信指令是否匹配")
        if any(error_code(e) == -350 for e in errs):
            self._append_scpi("!! -350 表示错误队列溢出，通常由连续错误指令累积；本次已按顺序读取旧错误。")

    def on_start(self):
        if self._diagnostic_busy:
            self.set_status("正在自检，请等待查询完成后再启动")
            return
        if self.engine.running:
            return
        try:
            cfg = self._collect_cfg()
        except ValueError as exc:
            messagebox.showerror("参数错误", f"数值无法解析: {exc}")
            return
        steps = []
        try:
            if cfg.setting.get("output_mode") == "seq":
                steps = load_sequence(cfg.sequence["file"])
            validate_run(cfg, steps)
        except (ValueError, OSError) as exc:
            messagebox.showerror("运行参数错误", str(exc))
            return
        if not self.engine.device:
            try:
                idn = self.engine.connect(cfg)
                self.btn_conn.configure(text="断开")
                self.var_idn.set(f"已连接: {idn}")
            except Exception as exc:
                messagebox.showerror("连接失败", f"{type(exc).__name__}: {exc}")
                return
        self._clear_chart()
        recorder = Recorder(cfg, work_dir=HERE)
        try:
            self._save_preferences(cfg)
            self.engine.start_run(cfg, steps, recorder)
        except Exception as exc:
            messagebox.showerror("启动失败", f"{type(exc).__name__}: {exc}")
            return
        self._run_buttons(True)
        mode_txt = MODE_NAMES.get(cfg.setting["mode"], "")
        out_txt = "序列模式" if steps else "定值模式"
        self.set_status(f"已启动：{mode_txt} / {out_txt}，限值 "
                        f"{cfg.limits['max_voltage']:g}V {cfg.limits['max_current']:g}A "
                        f"{cfg.limits['max_power']:g}W")

    def on_stop(self):
        stopped = self.engine.stop()
        self._run_buttons(not stopped)
        self.set_status(self.engine.last_error or ("已停止" if stopped else "正在停止，等待通信结束"),
                        alarm=bool(self.engine.last_error))

    def _run_buttons(self, running):
        self.btn_start.configure(state=tk.DISABLED if running else tk.NORMAL)
        self.btn_stop.configure(state=tk.NORMAL if running else tk.DISABLED)

    def _save_preferences(self, cfg=None):
        cfg = cfg or self._collect_cfg()
        save_config(self.config_path, cfg)
        self.cfg = cfg.copy()

    def on_browse_seq(self):
        path = filedialog.askopenfilename(title="选择时序文件", initialdir=HERE,
                                          filetypes=[("CSV", "*.csv"), ("TXT", "*.txt"),
                                                     ("所有文件", "*.*")])
        if path:
            self.var_seqfile.set(path)
            self.on_load_seq()

    def on_load_seq(self, quiet=False):
        path = self.var_seqfile.get().strip()
        if not path:
            if not quiet:
                messagebox.showwarning("提示", "未指定时序文件")
            return
        try:
            self.steps = load_sequence(path)
        except SequenceError as exc:
            self.steps = []
            self.var_stepinfo.set("载入失败")
            if not quiet:
                messagebox.showerror("时序文件错误", str(exc))
            return
        total = sum(st.duration for st in self.steps)
        self.var_stepinfo.set(f"{len(self.steps)} 步，共 {total:g}s")
        if not quiet:
            self.set_status(f"时序载入：{len(self.steps)} 步 / {total:g}s")

    def on_add_keypoint(self):
        try:
            volt = _num(self.var_keyvolt.get())
        except ValueError:
            messagebox.showerror("参数错误", "关键电压必须是数值")
            return
        if volt is None or not math.isfinite(volt) or volt < 0:
            messagebox.showerror("参数错误", "关键电压需为非负数")
            return
        params = list(PARAMS)
        self.cfg.keypoints[float(volt)] = params
        self._refresh_key_tree()
        self.var_keyvolt.set("")
        self.engine.update_keypoints(self.cfg.keypoints)
        self.set_status(f"关键设定 {volt:g}V 已添加，下次采样生效")

    def on_del_keypoint(self):
        sel = self.key_tree.selection()
        if not sel:
            messagebox.showwarning("提示", "请先选中要删除的关键点")
            return
        for item in sel:
            volt = float(self.key_tree.item(item)["values"][0])
            self.cfg.keypoints.pop(volt, None)
        self._refresh_key_tree()
        self.engine.update_keypoints(self.cfg.keypoints)

    def on_open_logs(self):
        try:
            cfg = self._collect_cfg()
        except ValueError:
            cfg = self.cfg
        files = Recorder(cfg, work_dir=HERE).files()
        if not files:
            messagebox.showinfo("提示", "还没有生成记录文件")
            return
        for f in files:
            try:
                os.startfile(f)
            except Exception:
                self.set_status(f"记录文件: {f}")

    # ---------- 曲线 ----------
    def _on_chart_configure(self, event):
        if event.widget is not self.chart_inner:
            return
        w, h = int(event.width), int(event.height)
        if w < 40 or h < 40:
            return
        if (w, h) == self._chart_size:
            return
        self._chart_size = (w, h)
        if self._chart_job:
            self.root.after_cancel(self._chart_job)
        self._chart_job = self.root.after(120, self._apply_chart_size)

    def _apply_chart_size(self):
        self._chart_job = None
        w, h = self._chart_size
        if not w or not h:
            return
        self.fig.set_size_inches(w / self.fig.dpi, h / self.fig.dpi, forward=True)
        self._draw_now()

    def _schedule_draw(self):
        if self._draw_job is not None:
            return
        self._draw_job = self.root.after(200, self._draw_now)

    def _draw_now(self):
        if self._draw_job is not None:
            self.root.after_cancel(self._draw_job)
            self._draw_job = None
        self.fig.clear()
        ax = self.fig.add_subplot(111)
        self.ax = ax
        ax.set_xlabel("时间 (s)")
        ax.set_ylabel("功率 (W)")
        ax.grid(True, alpha=0.35)
        if self._x:
            ax.plot(self._x, self._p, color="#c62828", lw=1.6, marker=".", markersize=3, label="功率")
            ax.legend(loc="best", fontsize=8)
            ax.relim()
            ax.autoscale_view()
        self.canvas.draw_idle()

    def _clear_chart(self):
        self._x, self._p = [], []
        self._draw_now()

    def _export_png(self):
        path = filedialog.asksaveasfilename(title="导出曲线", defaultextension=".png",
                                            initialdir=HERE, filetypes=[("PNG", "*.png")])
        if path:
            self.fig.savefig(path, dpi=200)
            self.set_status(f"曲线已导出: {os.path.basename(path)}")


def load_preferences(path=DEFAULT_CFG):
    cfg = load_config(path) if os.path.exists(path) else AppConfig()
    # 保留连接/保护等偏好；每次启动仍回到5V定值电压模式，防止沿用高输出目标。
    cfg.setting.update(control=CONTROL_CV, value=5.0, output_mode="hold")
    return cfg


def main():
    cfg = load_preferences()
    root = tk.Tk()
    App(root, cfg)
    root.mainloop()


if __name__ == "__main__":
    main()
