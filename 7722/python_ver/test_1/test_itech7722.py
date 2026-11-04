"""ITech 7722 驱动的离线测试（不驱动真实设备，使用 sim 传输）。

运行： C:\\ProgramData\\anaconda3\\python.exe -m unittest -v test_itech7722
"""

from __future__ import annotations

import csv
import os
import sys
import tempfile
import time
import types
import unittest

from itech7722_config import AppConfig, PARAMS, load_config, save_config
from itech7722_device import (CONTROL_CV, DeviceError, MODE_AC, MODE_DC, ITech7722, SimTransport)
from itech7722_recorder import Recorder, Sample
from itech7722_sequence import SequenceError, load_sequence


def make_cfg(tmp, conn=None, limits=None, setting=None, keypoints=None, recording=None):
    cfg = AppConfig()
    cfg.connection = conn or {"type": "sim", "sim_load_resistance": 50.0}
    cfg.limits = {"max_voltage": 300.0, "max_current": 10.0, "max_power": 600.0}
    if limits:
        cfg.limits.update(limits)
    cfg.setting = {"mode": MODE_AC, "control": CONTROL_CV, "value": 220.0,
                   "frequency": 50.0, "output_mode": "hold"}
    if setting:
        cfg.setting.update(setting)
    cfg.sequence = {"file": os.path.join(tmp, "seq.csv"), "loop": False}
    cfg.recording = {"enabled": True, "interval_ms": 50, "params": ["voltage", "current", "power"],
                     "log_file": "data_log.csv", "keypoint_file": "keypoint_log.csv"}
    if recording:
        cfg.recording.update(recording)
    cfg.keypoints = keypoints or {}
    cfg.keypoint_tolerance = 0.02
    return cfg


class ConfigTests(unittest.TestCase):
    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = make_cfg(tmp, keypoints={110.0: ["voltage", "current"],
                                           220.0: ["voltage", "power", "frequency"]})
            cfg.keypoint_tolerance = 0.05
            cfg.scpi["func_ac"] = "NORM:MODE AC"
            path = os.path.join(tmp, "c.ini")
            save_config(path, cfg)
            back = load_config(path)
            self.assertEqual(back.keypoints, {110.0: list(PARAMS), 220.0: list(PARAMS)})
            self.assertEqual(back.scpi["func_ac"], "NORM:MODE AC")
            self.assertEqual(back.setting["mode"], MODE_AC)
            self.assertEqual(back.setting["output_mode"], "hold")
            self.assertEqual(back.recording["interval_ms"], 50)
            self.assertEqual(back.limits["max_power"], 600.0)

    def test_output_mode_aliases(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "c.ini")
            cfg = make_cfg(tmp, setting={"output_mode": "seq"})
            save_config(path, cfg)
            self.assertEqual(load_config(path).setting["output_mode"], "seq")


class SequenceTests(unittest.TestCase):
    def _write(self, tmp, text):
        path = os.path.join(tmp, "s.csv")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def test_headered_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = self._write(tmp, "duration,control,value,frequency,record,desc\n"
                                 "3,CV,100,50,all,升压\n"
                                 "2,CI,2,60,voltage;current,恒流\n"
                                 "1,CV,220,50,none,驻留\n")
            steps = load_sequence(p)
            self.assertEqual(len(steps), 3)
            self.assertEqual([s.duration for s in steps], [3.0, 2.0, 1.0])
            self.assertEqual(steps[1].control, "CI")
            self.assertEqual(steps[1].frequency, 60.0)
            self.assertIsNone(steps[0].record)
            self.assertIsNone(steps[1].record)
            self.assertIsNone(steps[2].record)          # 显式 none
            self.assertEqual(steps[2].desc, "")

    def test_no_header_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = self._write(tmp, "5,CV,150,400,all,x\n6,CV,200,400,all,y\n")
            steps = load_sequence(p)
            self.assertEqual(len(steps), 2)
            self.assertEqual(steps[0].value, 150.0)
            self.assertEqual(steps[0].frequency, 400.0)

    def test_blank_record_means_inherit(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = self._write(tmp, "duration,value,record\n5,100,\n")
            self.assertIsNone(load_sequence(p)[0].record)

    def test_missing_file_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SequenceError):
                load_sequence(os.path.join(tmp, "nope.csv"))

    def test_demo_file_shipped(self):
        here = os.path.dirname(os.path.abspath(__file__))
        steps = load_sequence(os.path.join(here, "sequence_demo.csv"))
        self.assertGreaterEqual(len(steps), 5)
        self.assertTrue(all(s.control == "CV" for s in steps))


class DeviceTests(unittest.TestCase):
    def _dev(self):
        tp = SimTransport(load_resistance=50.0)
        return tp, ITech7722(tp)

    def test_ac_mode_sends_func_ac(self):
        tp, dev = self._dev()
        dev.set_function_mode(MODE_AC)
        dev.apply_setting(CONTROL_CV, 220.0, 50.0, {"max_voltage": 300.0,
                                                    "max_current": 10.0, "max_power": 600.0,
                                                    "mode": MODE_AC})
        self.assertIn("SOUR:NORM:MODE AC", tp.commands)
        self.assertIn("SOUR:NORM:VOLT:AC 220", tp.commands)
        self.assertIn("SOUR:NORM:FREQ 50", tp.commands)
        self.assertEqual(tp.state["func"], MODE_AC)

    def test_dc_mode_sends_func_dc_and_no_freq(self):
        tp, dev = self._dev()
        dev.set_function_mode(MODE_DC)
        dev.apply_setting(CONTROL_CV, 24.0, 50.0, {"max_voltage": 300.0, "max_current": 10.0,
                                                   "max_power": 600.0, "mode": MODE_DC})
        self.assertIn("SOUR:NORM:MODE DC", tp.commands)
        self.assertIn("SOUR:NORM:VOLT:DC 24", tp.commands)
        self.assertFalse([c for c in tp.commands if c.startswith("SOUR:NORM:FREQ")])

    def test_ci_uses_current_as_source_and_voltage_compliance(self):
        tp, dev = self._dev()
        dev.set_function_mode(MODE_DC)
        dev.apply_setting("CI", 2.0, 0.0, {"max_voltage": 100.0, "max_current": 10.0,
                                           "max_power": 600.0, "mode": MODE_DC})
        self.assertIn("PROT:MAX:CURR:LIM 2", tp.commands)
        self.assertIn("SOUR:NORM:VOLT:DC 100", tp.commands)

    def test_cp_sends_power(self):
        tp, dev = self._dev()
        dev.apply_setting("CP", 200.0, 50.0, {"max_voltage": 300.0, "max_current": 10.0,
                                              "max_power": 600.0, "mode": MODE_AC})
        self.assertEqual(tp.state["power_limit"], 200)

    def test_protection_limits_are_sent(self):
        tp, dev = self._dev()
        dev.set_protection_limits(300.0, 10.0, 600.0)
        self.assertIn("PROT:RMS:VOLT 300", tp.commands)
        self.assertIn("PROT:POW 600", tp.commands)

    def test_custom_scpi_map_overrides_defaults(self):
        tp = SimTransport()
        dev = ITech7722(tp, {"func_ac": "NORM:MODE AC"})
        dev.set_function_mode(MODE_AC)
        self.assertIn("NORM:MODE AC", tp.commands)

    def test_measure_reflects_setting(self):
        tp, dev = self._dev()
        dev.set_function_mode(MODE_AC)
        dev.apply_setting(CONTROL_CV, 100.0, 400.0, {"max_voltage": 300.0, "max_current": 10.0,
                                                     "max_power": 600.0, "mode": MODE_AC})
        dev.output_on()
        m = dev.measure(MODE_AC)
        self.assertAlmostEqual(m["voltage"], 100.0, delta=1.0)
        self.assertAlmostEqual(m["frequency"], 400.0, delta=0.01)
        self.assertAlmostEqual(m["power"], m["voltage"] * m["current"], delta=0.01)

    def test_output_off_then_measure_zero(self):
        tp, dev = self._dev()
        dev.apply_setting(CONTROL_CV, 100.0, 50.0, {"max_voltage": 300.0, "max_current": 10.0,
                                                     "max_power": 600.0, "mode": MODE_AC})
        dev.output_off()
        self.assertEqual(dev.measure()["voltage"], 0.0)


class RecorderTests(unittest.TestCase):
    def test_keypoint_edge_triggered_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = make_cfg(tmp, keypoints={100.0: ["voltage", "power"]})
            rec = Recorder(cfg, work_dir=tmp)
            rec.start()
            s1 = Sample("t1", 1.0, 1, voltage=100.4, current=2.0, power=200.8, frequency=50.0)
            s2 = Sample("t2", 1.5, 1, voltage=100.2, current=2.0, power=200.4, frequency=50.0)
            self.assertTrue(rec.log(s1, set_voltage=100))
            self.assertFalse(rec.log(s2, set_voltage=100))          # 同一关键点驻留期间不再重复写
            with open(rec.key_path, encoding="utf-8-sig") as fh:
                rows = list(csv.reader(fh))
            self.assertEqual(len(rows), 2)         # 表头 + 1 行
            self.assertEqual(rows[1][0], "100")
            self.assertEqual(rows[1][1], "t1")
            self.assertEqual(rows[1][2], "100.400")     # voltage 在清单里
            self.assertEqual(rows[1][3], "2.0000")            # current 不在清单里
            self.assertEqual(rows[1][4], "200.800")     # power 在清单里
            self.assertEqual(rows[1][5], "")            # frequency 不在清单里

    def test_keypoint_rearm_after_leaving_band(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = make_cfg(tmp, keypoints={100.0: ["voltage"]})
            rec = Recorder(cfg, work_dir=tmp)
            rec.start()
            self.assertTrue(rec.log(Sample("t", 0, 1, voltage=100.1), set_voltage=100))
            rec.log(Sample("t", 1, 1, voltage=150.0), set_voltage=150)
            self.assertTrue(rec.log(Sample("t", 2, 1, voltage=99.9), set_voltage=100))

    def test_per_step_param_filter(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = make_cfg(tmp, recording={"params": ["voltage", "current", "power"]})
            rec = Recorder(cfg, work_dir=tmp)
            rec.start()
            s = Sample("t", 0, 1, voltage=100.0, current=2.0, power=200.0)
            rec.log(s, "交流", "CV=100", params=["voltage"])
            rec.log(s, "交流", "CV=100", params=[])          # 显式不记录
            with open(rec.log_path, encoding="utf-8-sig") as fh:
                rows = [r for r in csv.reader(fh) if r and not r[0].startswith("#")]
            self.assertEqual(len(rows), 3)                   # 表头 + 两行；旧筛选不再跳过数据
            data = rows[1]
            self.assertEqual(data[2], "100.000")             # voltage
            self.assertEqual(data[3], "2.0000")                    # current 未选
            self.assertEqual(data[4], "200.000")                    # power 未选

    def test_disabled_recording_only_keeps_series(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = make_cfg(tmp, recording={"enabled": False})
            rec = Recorder(cfg, work_dir=tmp)
            rec.start()
            rec.log(Sample("t", 0, 1, voltage=1.0))
            self.assertEqual(len(rec.series), 1)
            self.assertEqual(rec.files(), [])


class EngineTests(unittest.TestCase):
    """定值/序列两条执行路径：确认真实下发指令并产生记录。"""

    def setUp(self):
        from itech7722_driver import Engine
        self.Engine = Engine

        class StubUI:
            def __init__(self):
                self.events = []
                self.scpi = []

            def post(self, kind, payload):
                self.events.append((kind, payload))

            def log_scpi(self, direction, text):
                self.scpi.append((direction, text))

        self.ui = StubUI()

    def _wait(self, engine, seconds):
        deadline = time.time() + seconds
        while engine.running and time.time() < deadline:
            time.sleep(0.02)

    def test_hold_run_emits_mode_and_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = make_cfg(tmp, setting={"mode": MODE_AC, "control": CONTROL_CV,
                                         "value": 100.0, "frequency": 400.0})
            eng = self.Engine(self.ui)
            tp = None
            eng.connect(cfg)
            tp = eng.device.tp
            rec = Recorder(cfg, work_dir=tmp)
            eng.start_run(cfg, [], rec)
            self._wait(eng, 1.0)
            eng.stop()
            self.assertIn("SOUR:NORM:MODE AC", tp.commands)
            self.assertIn("SOUR:NORM:FREQ 400", tp.commands)
            self.assertIn("OUTP:STAT ON", tp.commands)
            self.assertIn("OUTP:STAT OFF", tp.commands)
            kinds = [k for k, _ in self.ui.events]
            self.assertIn("sample", kinds)
            with open(rec.log_path, encoding="utf-8-sig") as fh:
                rows = [r for r in csv.reader(fh) if r and not r[0].startswith("#")]
            self.assertGreater(len(rows), 2)                 # 表头 + 采样行

    def test_sequence_run_walks_steps(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "s.csv")
            with open(p, "w", encoding="utf-8") as fh:
                fh.write("duration,control,value,frequency,record\n"
                         "0.4,CV,100,400,voltage\n"
                         "0.4,CV,120,400,voltage\n")
            cfg = make_cfg(tmp, setting={"mode": MODE_AC, "output_mode": "seq"})
            cfg.sequence = {"file": p, "loop": False}
            steps = load_sequence(p)
            eng = self.Engine(self.ui)
            eng.connect(cfg)
            tp = eng.device.tp
            rec = Recorder(cfg, work_dir=tmp)
            eng.start_run(cfg, steps, rec)
            self._wait(eng, 3.0)
            self.assertIn("SOUR:NORM:VOLT:AC 100", tp.commands)
            self.assertIn("SOUR:NORM:VOLT:AC 120", tp.commands)
            self.assertTrue([k for k, v in self.ui.events if k == "step"])
            eng.stop()

    def test_protection_stops_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = make_cfg(tmp, limits={"max_voltage": 50.0},
                           setting={"mode": MODE_AC, "control": CONTROL_CV, "value": 100.0,
                                    "frequency": 50.0})
            cfg.recording["interval_ms"] = 40
            # 清空保护指令模板：模拟该机型不支持远程 OVP，验证软件侧兜底保护
            cfg.scpi.update({"ovp": "", "ocp": "", "opp": ""})
            eng = self.Engine(self.ui)
            eng.connect(cfg)
            rec = Recorder(cfg, work_dir=tmp)
            eng.device.tp.load_resistance = 1e6              # 空载，回读贴近设定值
            # 设定合法，但设备回读异常升高：验证运行中的软件保护。
            cfg.setting["value"] = 40.0
            eng.device.measure = lambda mode: {"voltage": 100.0, "current": 0.0,
                                                "power": 0.0, "frequency": 50.0}
            eng.start_run(cfg, [], rec)
            deadline = time.time() + 2.0
            alarms = []
            while time.time() < deadline:
                alarms = [v for k, v in self.ui.events if k == "alarm"]
                if alarms or not eng.running:
                    break
                time.sleep(0.02)
            eng.stop()
            self.assertTrue(alarms, f"未触发保护，事件={self.ui.events}")
            self.assertIn("过压", alarms[0])


def _grid_cells(widget):
    info = widget.grid_info()
    row = int(info.get("row", 0) or 0)
    col = int(info.get("column", 0) or 0)
    rspan = int(info.get("rowspan", 1) or 1)
    cspan = int(info.get("columnspan", 1) or 1)
    return {(row + i, col + j) for i in range(rspan) for j in range(cspan)}


def find_grid_overlaps(root):
    """递归找出同一父容器里占用相同 grid 单元格的控件对。"""
    problems = []
    stack = [root]
    while stack:
        master = stack.pop()
        for child in master.winfo_children():
            stack.append(child)
        seen = {}
        for child in master.grid_slaves():
            for cell in _grid_cells(child):
                if cell in seen:
                    other = seen[cell]
                    pair = (other["text"] if "text" in other.keys() else str(other),
                            child["text"] if "text" in child.keys() else str(child), cell)
                    if pair not in problems:
                        problems.append(pair)
                else:
                    seen[cell] = child
    return problems


class TkGuiTestBase(unittest.TestCase):
    """建真实窗口做布局断言；tearDown 统一 shutdown，避免销毁后 after 任务仍被触发。"""

    def setUp(self):
        try:
            import tkinter as tk
        except ImportError:
            self.skipTest("无 tkinter")
        try:
            self.root = tk.Tk()
        except Exception as exc:
            self.skipTest(f"无法创建窗口: {exc}")
        self._apps = []

    def tearDown(self):
        for app in self._apps:
            try:
                app.shutdown()
            except Exception:
                pass
        try:
            self.root.destroy()
        except Exception:
            pass

    def _app(self, cfg=None):
        from itech7722_driver import App
        app = App(self.root, cfg or make_cfg(tempfile.gettempdir()))
        self._apps.append(app)
        return app

    def _pump(self, ms=300):
        end = time.time() + ms / 1000.0
        while time.time() < end:
            self.root.update()
            time.sleep(0.01)


class LayoutTests(TkGuiTestBase):
    """验证控件随窗口尺寸伸展（用户反馈的窗口问题）。"""

    def test_maximized_fills_window(self):
        app = self._app()
        self.root.update_idletasks()
        self.root.state("zoomed")
        self._pump(600)
        win_w, win_h = self.root.winfo_width(), self.root.winfo_height()
        tree_w, tree_h = app.tree.winfo_width(), app.tree.winfo_height()
        cv = app.canvas.get_tk_widget()
        self.assertTrue(app.tree.winfo_ismapped())
        self.assertGreater(tree_w, 300, "表格未随最大化变宽")
        self.assertGreater(tree_h, 150, "表格未随最大化变高（纵向权重缺失）")
        self.assertGreater(cv.winfo_width(), 300, "曲线区未随最大化变宽")
        self.assertGreater(cv.winfo_height(), 150, "曲线区未随最大化变高")
        self.assertGreater(tree_w + cv.winfo_width(), win_w * 0.6,
                           f"下排面板只占 {tree_w + cv.winfo_width()} / 窗口 {win_w}")
        self.assertGreater(tree_h + cv.winfo_height() + app.chart_inner.winfo_height(),
                           win_h * 0.3, f"下排高度未伸展 (tree_h={tree_h}, win_h={win_h})")

    def test_no_grid_cell_collisions(self):
        app = self._app()
        self.root.geometry("1200x800+60+40")
        self._pump(300)
        problems = find_grid_overlaps(self.root)
        self.assertEqual(problems, [], f"控件占用同一 grid 单元格: {problems}")

    def test_conn_hint_and_idn_labels_are_fully_visible(self):
        """接口说明与连接状态各自独立成行，不被按钮压住。"""
        app = self._app()
        self.root.geometry("1200x800+60+40")
        self._pump(300)
        app.var_conn_type.set("visa")
        app._update_conn_hint()
        self._pump(200)
        hint = app.conn_hint_label
        self.assertTrue(hint.winfo_ismapped(), "接口说明未显示")
        self.assertTrue(app.idn_label.winfo_ismapped(), "连接状态未显示")
        self.assertEqual(hint.winfo_height(), hint.winfo_reqheight(),
                         f"接口说明被裁切: 高 {hint.winfo_height()} < 需要 {hint.winfo_reqheight()}")
        self.assertEqual(app.idn_label.winfo_height(), app.idn_label.winfo_reqheight())
        self.assertNotEqual(hint.winfo_y(), app.idn_label.winfo_y(), "两行标签重叠在同一位置")
        self.assertIn("pyvisa", hint["text"])

    def test_panes_grow_with_window(self):
        app = self._app()
        self.root.geometry("900x600+80+60")
        self._pump(400)
        small = (app.tree.winfo_width(), app.tree.winfo_height(),
                 app.canvas.get_tk_widget().winfo_width(),
                 app.canvas.get_tk_widget().winfo_height())
        self.root.geometry("1500x1000+60+40")
        self._pump(700)
        big = (app.tree.winfo_width(), app.tree.winfo_height(),
               app.canvas.get_tk_widget().winfo_width(),
               app.canvas.get_tk_widget().winfo_height())
        self.assertTrue(app.tree.winfo_ismapped(), "表格未显示")
        for name, a, b in zip(("tree_w", "tree_h", "canvas_w", "canvas_h"), small, big):
            self.assertGreater(b, a, f"{name} 未随窗口放大: {small} -> {big}")
        self.assertGreater(big[2], 300, "曲线区过窄，说明未横向伸展")

    def test_outmode_switch_shows_hold_inputs(self):
        app = self._app()
        self.root.geometry("1100x800+60+40")
        self._pump(300)
        app.var_outmode.set("hold")
        app._on_outmode_change()
        self._pump(200)
        self.assertTrue(app.hold_box.winfo_manager() == "grid", "定值设定面板未显示")
        self.assertTrue(app.ent_value.winfo_ismapped(), "定值模式的设定值输入框不可见")
        self.assertTrue(app.ent_value.winfo_width() > 20, "设定值输入框被压缩")
        app.var_outmode.set("seq")
        app._on_outmode_change()
        self._pump(200)
        self.assertEqual(app.ent_value.winfo_ismapped(), 0, "切到序列模式后定值输入框应隐藏")

    def test_dc_mode_disables_frequency(self):
        app = self._app()
        self._pump(200)
        app.var_mode.set(MODE_DC)
        app._on_mode_change()
        self.assertEqual(str(app.ent_freq.cget("state")), "disabled")
        app.var_mode.set(MODE_AC)
        app._on_mode_change()
        self.assertEqual(str(app.ent_freq.cget("state")), "normal")


class FakePyVisAMixin:
    """向 sys.modules 注入假 pyvisa，用于验证 USB/VISA 传输自身的逻辑。"""

    def setUp(self):
        self._saved_pyvisa = sys.modules.get("pyvisa")
        super().setUp()

    def tearDown(self):
        super().tearDown()
        if self._saved_pyvisa is not None:
            sys.modules["pyvisa"] = self._saved_pyvisa
        else:
            sys.modules.pop("pyvisa", None)

    def install_fake_pyvisa(self, resources):
        state = {"rms": 0, "opened": [], "patterns": [], "rm_closed": 0}

        class FakeInst:
            def __init__(self, resource):
                self.resource = resource
                self.sent = []
                self.closed = False
                self.timeout = None
                self.read_termination = None
                self.write_termination = None

            def write(self, cmd):
                self.sent.append(("W", cmd))

            def read(self):
                return "OK\n"

            def query(self, cmd):
                self.sent.append(("Q", cmd))
                return "ITECH,IT7722,FAKE,1.00"

            def close(self):
                self.closed = True

        class FakeRM:
            def __init__(self):
                state["rms"] += 1

            def list_resources(self, pattern="?*INSTR"):
                state["patterns"].append(pattern)
                return list(resources)

            def open_resource(self, res):
                inst = FakeInst(res)
                state["opened"].append(inst)
                return inst

            def close(self):
                state["rm_closed"] += 1

        mod = types.ModuleType("pyvisa")
        mod.ResourceManager = FakeRM
        sys.modules["pyvisa"] = mod
        self.fake_state = state
        return state


class USBTransportTests(FakePyVisAMixin, unittest.TestCase):
    def _tp(self):
        import itech7722_device as d
        return d

    def test_routes_usb_and_alias(self):
        from itech7722_device import UsbTransport, build_transport
        self.install_fake_pyvisa(["USB0::0x0AA3::0x0772::INSTR"])
        self.assertIsInstance(build_transport({"type": "usb"}), UsbTransport)
        self.assertIsInstance(build_transport({"type": "USBTMC"}), UsbTransport)

    def test_backend_missing_gives_install_hint(self):
        from itech7722_device import build_transport
        sys.modules["pyvisa"] = None          # import pyvisa 会抛 ImportError
        with self.assertRaises(DeviceError) as ctx:
            build_transport({"type": "usb"})
        msg = str(ctx.exception)
        self.assertIn("pyvisa", msg)
        self.assertIn("pip install", msg)
        self.assertIn("serial", msg)          # 同时告知 COM 口应改用 serial

    def test_autopicks_first_usb_resource_with_single_manager(self):
        from itech7722_device import UsbTransport
        state = self.install_fake_pyvisa([
            "TCPIP0::192.168.1.5::inst0::INSTR",
            "USB0::0x0AA3::0x0772::INSTR",
            "USB1::0x1111::0x2222::INSTR"])
        tp = UsbTransport()
        self.assertEqual(tp.resource, "USB0::0x0AA3::0x0772::INSTR")
        self.assertEqual(state["rms"], 1, "一次连接重复打开了多个 VISA 会话")
        self.assertEqual(len(state["opened"]), 1)

    def test_explicit_resource_skips_scan(self):
        from itech7722_device import UsbTransport
        state = self.install_fake_pyvisa(["USB0::0x0AA3::0x0772::INSTR"])
        tp = UsbTransport(resource="USB2::0xAAAA::0xBBBB::INSTR")
        self.assertEqual(tp.resource, "USB2::0xAAAA::0xBBBB::INSTR")
        self.assertEqual(state["patterns"], [], "指定资源名后不应再扫描")

    def test_no_usb_instrument_points_at_com_port(self):
        from itech7722_device import UsbTransport
        self.install_fake_pyvisa(["TCPIP0::192.168.1.5::inst0::INSTR"])
        with self.assertRaises(DeviceError) as ctx:
            UsbTransport()
        self.assertIn("COM", str(ctx.exception))

    def test_write_query_and_terminators(self):
        from itech7722_device import UsbTransport
        self.install_fake_pyvisa(["USB0::0x0AA3::0x0772::INSTR"])
        tp = UsbTransport()
        inst = self.fake_state["opened"][0]
        tp.write("SOUR:NORM:MODE AC")
        self.assertEqual(inst.sent, [("W", "SOUR:NORM:MODE AC")])
        self.assertEqual(tp.query("*IDN?"), "ITECH,IT7722,FAKE,1.00")
        self.assertEqual(inst.read_termination, "\n")
        self.assertEqual(inst.write_termination, "\n")
        self.assertEqual(inst.timeout, 2000)

    def test_close_releases_session_and_manager(self):
        from itech7722_device import UsbTransport
        self.install_fake_pyvisa(["USB0::0x0AA3::0x0772::INSTR"])
        tp = UsbTransport()
        inst = self.fake_state["opened"][0]
        tp.close()
        self.assertTrue(inst.closed)
        self.assertEqual(self.fake_state["rm_closed"], 1)

    def test_list_visa_resources_closes_manager(self):
        import itech7722_device as d
        res = ["USB0::0x0AA3::0x0772::INSTR", "TCPIP0::1.2.3.4::inst0::INSTR"]
        self.install_fake_pyvisa(res)
        self.assertEqual(d.list_visa_resources(), res)
        self.assertEqual(self.fake_state["rm_closed"], 1)

    def test_usb_path_actually_writes_setting_commands(self):
        """回归：USB/VISA 路径也必须真的下发 SOUR: 设定（历史上被静默丢弃过）。"""
        from itech7722_device import ITech7722, UsbTransport
        self.install_fake_pyvisa(["USB0::0x0AA3::0x0772::INSTR"])
        dev = ITech7722(UsbTransport())
        dev.set_function_mode(MODE_AC)
        dev.apply_setting(CONTROL_CV, 220.0, 50.0, {"max_voltage": 300.0, "max_current": 10.0,
                                                   "max_power": 600.0, "mode": MODE_AC})
        sent = [c for kind, c in self.fake_state["opened"][0].sent if kind == "W"]
        self.assertIn("SOUR:NORM:MODE AC", sent)
        self.assertIn("SOUR:NORM:VOLT:AC 220", sent)
        self.assertIn("SOUR:NORM:FREQ 50", sent)

    def test_unknown_type_lists_usb(self):
        from itech7722_device import build_transport
        with self.assertRaises(DeviceError) as ctx:
            build_transport({"type": "gpib"})
        self.assertIn("usb", str(ctx.exception))


class ConnectionOptionsTests(FakePyVisAMixin, TkGuiTestBase):
    def test_scan_lists_all_brands_and_does_not_choose_between_multiple_ports(self):
        app = self._app()
        app._serial_ports_detailed = lambda: [("COM3", 0x1234, 0x1, "FTDI"),
                                             ("COM9", None, None, "Other device")]
        app._refresh_ports()
        self.assertEqual(tuple(app.port_combo.cget("values")), ("COM3", "COM9"))
        self.assertEqual(app.var_port.get(), "")
        with self.assertRaisesRegex(ValueError, "请选择串口"):
            app._collect_cfg()

    def test_keypoint_ui_has_only_set_voltage_and_updates_running_rules(self):
        from unittest.mock import Mock
        app = self._app()
        self.assertEqual(tuple(app.key_tree.cget("columns")), ("volt",))
        self.assertFalse(hasattr(app, "var_keytol"))
        app.engine.update_keypoints = Mock()
        app.var_keyvolt.set("5")
        app.on_add_keypoint()
        self.assertIn(5, app.cfg.keypoints)
        app.engine.update_keypoints.assert_called_once()
        item = next(i for i in app.key_tree.get_children()
                    if float(app.key_tree.item(i)["values"][0]) == 5)
        app.key_tree.selection_set(item)
        app.on_del_keypoint()
        self.assertNotIn(5, app.cfg.keypoints)
        self.assertEqual(app.engine.update_keypoints.call_count, 2)

    def test_configuration_buttons_removed_and_preferences_saved_automatically(self):
        from tkinter import ttk
        app = self._app()
        captions = []

        def walk(widget):
            for child in widget.winfo_children():
                if isinstance(child, ttk.Button):
                    captions.append(child.cget("text"))
                walk(child)

        walk(self.root)
        self.assertNotIn("加载配置", captions)
        self.assertNotIn("保存配置", captions)
        with tempfile.TemporaryDirectory() as tmp:
            app.config_path = os.path.join(tmp, "preferences.ini")
            app.var_feedback_maxv.set("20")
            app.var_interval.set("2")
            app._save_preferences()
            back = load_config(app.config_path)
            self.assertEqual(back.feedback["max_voltage"], 20)
            self.assertEqual(back.recording["interval_ms"], 2000)

    def test_startup_restores_preferences_but_resets_output_to_five_volts(self):
        from itech7722_driver import load_preferences
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "preferences.ini")
            cfg = AppConfig()
            cfg.setting.update(control="CP", value=50, output_mode="seq")
            cfg.feedback["max_voltage"] = 20
            save_config(path, cfg)
            back = load_preferences(path)
            self.assertEqual(back.setting["value"], 5)
            self.assertEqual(back.setting["control"], "CV")
            self.assertEqual(back.setting["output_mode"], "hold")
            self.assertEqual(back.feedback["max_voltage"], 20)

    def test_one_second_record_interval_is_shown_and_collected_in_seconds(self):
        app = self._app(AppConfig())
        self.assertEqual(app.var_interval.get(), "1")
        self.assertEqual(app._collect_cfg().recording["interval_ms"], 1000)
        app.var_interval.set("2")
        self.assertEqual(app._collect_cfg().recording["interval_ms"], 2000)

    def test_live_samples_update_table_curve_and_rendered_canvas(self):
        app = self._app()
        errors = []
        self.root.report_callback_exception = lambda *args: errors.append(args)
        draws = []
        app.canvas.mpl_connect("draw_event", lambda event: draws.append(event))
        app.post("sample", (Sample("t1", 1, 1, voltage=5, current=0.17, power=0.85), "CV", 5))
        self._pump(ms=400)
        self.assertFalse(errors, errors)
        self.assertEqual(app._x, [1])
        self.assertEqual(list(app.ax.lines[0].get_ydata()), [0.85])
        self.assertTrue(draws)
        draws.clear()
        app.post("sample", (Sample("t2", 2, 2, voltage=6, current=0.2, power=1.2), "CV", 6))
        self._pump(ms=400)
        self.assertFalse(errors, errors)
        self.assertEqual(len(app.tree.get_children()), 2)
        self.assertEqual(list(app.ax.lines[0].get_ydata()), [0.85, 1.2])
        self.assertTrue(draws)

    def test_record_checkmarks_use_custom_icons_and_toggle_boolean(self):
        import tkinter as tk
        from tkinter import ttk
        app = self._app()
        self.assertEqual(len(app._check_images), 2)
        self.assertNotEqual(app._check_images[0].get(4, 4), app._check_images[1].get(4, 4))
        buttons = []

        def walk(widget):
            for child in widget.winfo_children():
                if isinstance(child, ttk.Checkbutton):
                    buttons.append(child)
                walk(child)

        walk(self.root)
        self.assertTrue(buttons)
        for button in buttons:
            self.assertEqual(button.cget("style"), "Clear.TCheckbutton")
        enabled = next(b for b in buttons if b.cget("text") == "启用记录")
        before = app.var_record.get()
        enabled.invoke()
        self.assertEqual(app.var_record.get(), not before)

    def test_feedback_controls_are_enabled_only_for_current_and_power(self):
        from itech7722_config import CONTROL_NAMES
        app = self._app()
        for code in ("CI", "CP"):
            app.var_control.set(CONTROL_NAMES[code])
            app._update_value_hint()
            self.assertNotIn("仿真", app.var_control.get())
            self.assertEqual(str(app.ent_feedback_maxv.cget("state")), "normal")
        app.var_feedback_maxv.set("12")
        app.var_feedback_step.set("0.1")
        cfg = app._collect_cfg()
        self.assertEqual(cfg.feedback["max_voltage"], 12)
        self.assertEqual(cfg.feedback["step_voltage"], 0.1)
        app.var_control.set(CONTROL_NAMES["CV"])
        app._update_value_hint()
        self.assertEqual(str(app.ent_feedback_maxv.cget("state")), "disabled")

    def test_gui_offers_serial_without_usbtmc(self):
        app = self._app()
        values = list(app.conn_type_combo.cget("values"))
        self.assertNotIn("usb", values)
        self.assertNotIn("sim", values)
        self.assertFalse(hasattr(app, "var_load_r"))
        for kind in ("serial", "tcp", "visa"):
            self.assertIn(kind, values)
        self.assertFalse(hasattr(app, "var_usb_resource"))

    def test_serial_hint_explains_usb_virtual_port(self):
        app = self._app()
        app.var_conn_type.set("serial")
        app._update_conn_hint()
        self.assertIn("USB", app.var_conn_hint.get())
        self.assertIn("COM", app.var_conn_hint.get())

    def test_old_usb_config_opens_as_serial(self):
        cfg = make_cfg(tempfile.gettempdir(), conn={"type": "usb", "port": "COM6",
                       "usb_resource": "USB0::OLD::INSTR"})
        app = self._app(cfg)
        back = app._collect_cfg()
        self.assertEqual(back.connection["type"], "serial")
        self.assertNotIn("usb_resource", back.connection)

    def test_scan_visa_fills_resource_without_usb_field(self):
        self.install_fake_pyvisa(["TCPIP0::192.168.1.5::inst0::INSTR"])
        app = self._app()
        app._scan_visa()
        self.assertEqual(app.var_resource.get(), "TCPIP0::192.168.1.5::inst0::INSTR")



class SerialReadTests(unittest.TestCase):
    """串口收包不能依赖换行符：ITECH 部分机型以 \\r 结尾，readline() 会白等超时。"""

    def setUp(self):
        self._saved_serial = sys.modules.get("serial")

    def tearDown(self):
        if self._saved_serial is not None:
            sys.modules["serial"] = self._saved_serial
        else:
            sys.modules.pop("serial", None)

    def _fake_serial(self, reply_chunks):
        class FakeSer:
            def __init__(self, **kw):
                self.kw = kw
                self.chunks = list(reply_chunks)
                self.sent = []
                self.flushed = 0

            def write(self, data):
                self.sent.append(data)

            def flush(self):
                self.flushed += 1

            @property
            def in_waiting(self):
                return len(self.chunks[0]) if self.chunks else 0

            def read(self, n):
                return self.chunks.pop(0)[:n] if self.chunks else b""

            def close(self):
                pass

        mod = types.ModuleType("serial")
        mod.Serial = FakeSer
        sys.modules["serial"] = mod
        return FakeSer

    def test_read_handles_cr_only_terminator(self):
        from itech7722_device import SerialTransport
        self._fake_serial([b"ITECH,IT7722,SN1,1.00\r"])
        tp = SerialTransport(port="COM9", timeout=0.5)
        self.assertEqual(tp.read(), "ITECH,IT7722,SN1,1.00")

    def test_read_handles_split_reply_without_terminator(self):
        from itech7722_device import SerialTransport
        self._fake_serial([b"220.15", b"0\r\n"])
        tp = SerialTransport(port="COM9", timeout=0.5)
        self.assertEqual(tp.read(), "220.150")

    def test_write_appends_terminator_and_flushes(self):
        from itech7722_device import SerialTransport
        FakeSer = self._fake_serial([])
        tp = SerialTransport(port="COM9", timeout=0.5, terminator="\r\n")
        tp.write("*IDN?")
        inst = tp._ser
        self.assertIsInstance(inst, FakeSer)
        self.assertEqual(inst.sent, [b"*IDN?\r\n"])
        self.assertEqual(inst.flushed, 1)

    def test_read_returns_empty_on_no_response(self):
        from itech7722_device import SerialTransport
        self._fake_serial([])
        tp = SerialTransport(port="COM9", timeout=0.2)
        started = time.time()
        self.assertEqual(tp.read(), "")
        self.assertLess(time.time() - started, 1.5)


class ErrorQueueTests(unittest.TestCase):
    """-113 必须能归因到具体指令，否则用户只能盲猜助记符。"""

    def _dev(self):
        tp = SimTransport()
        return tp, ITech7722(tp)

    def test_probe_flags_undefined_header(self):
        from itech7722_device import error_code
        tp, dev = self._dev()
        errs = dev.probe("BOGUS:CMD 1")
        self.assertTrue(any(error_code(e) == -113 for e in errs), errs)

    def test_probe_accepts_known_header(self):
        from itech7722_device import error_code
        tp, dev = self._dev()
        errs = dev.probe("SOUR:NORM:MODE AC")
        self.assertEqual([e for e in errs if (error_code(e) or 0) != 0], [])

    def test_error_code_parsing(self):
        from itech7722_device import error_code
        self.assertEqual(error_code('-113,"Undefined header",1'), -113)
        self.assertEqual(error_code('0,"No error",0'), 0)
        self.assertEqual(error_code('+1,"Execution error",2'), 1)
        self.assertIsNone(error_code(""))
        self.assertIsNone(error_code("garbage"))

    def test_clear_errors_empties_queue(self):
        from itech7722_device import error_code
        tp, dev = self._dev()
        dev.raw("BOGUS:A 1")
        dev.raw("BOGUS:B 2")
        self.assertEqual(dev.clear_errors(), 2)
        errs = dev.error_queue()
        self.assertTrue(all((error_code(e) or 0) == 0 for e in errs), errs)


class ConnectionDiagnosticTests(FakePyVisAMixin, TkGuiTestBase):
    """自检/自动识别的真实行为：无应答要丢连接、ITECH 串口要被认出来。"""

    def test_collect_preserves_transport_options(self):
        cfg = make_cfg(tempfile.gettempdir(), conn={"type": "sim", "timeout": 0.8,
                       "terminator": "\\r", "bytesize": 7, "parity": "E", "stopbits": 2})
        app = self._app(cfg)
        back = app._collect_cfg()
        for key in ("timeout", "terminator", "bytesize", "parity", "stopbits"):
            self.assertEqual(back.connection[key], cfg.connection[key])

    def test_idn_button_reports_silent_device_without_blocking_ui(self):
        import threading
        app = self._app()
        device = ITech7722(SimTransport())
        app.engine.device = device
        entered, release = threading.Event(), threading.Event()

        def identify():
            entered.set()
            release.wait(2)
            return ""

        device.identify = identify
        try:
            app.on_idn()
            self.assertTrue(entered.wait(1))
            self.assertIn("正在自检", app.var_idn.get())
            self.assertTrue(app._diagnostic_busy)
        finally:
            release.set()
        self._pump(ms=200)
        self.assertIn("超时", app.var_idn.get())
        self.assertFalse(app._diagnostic_busy)

    def test_idn_button_shows_missing_connection_inline(self):
        app = self._app()
        app.on_idn()
        self.assertIn("未连接", app.var_idn.get())

    def test_find_port_handles_missing_pid(self):
        app = self._app()
        app._serial_ports_detailed = lambda: [("COM6", 0x2EC7, None, "ITECH")]
        app._refresh_ports()
        self.assertIn("COM6", app.status_var.get())

    def test_manual_command_controls_removed(self):
        app = self._app()
        self.assertFalse(hasattr(app, "var_raw"))
        self.assertFalse(hasattr(app, "on_raw"))

    def test_alarm_survives_stopped_event(self):
        app = self._app()
        app.engine.last_error = "触发保护：过压"
        app.post("alarm", app.engine.last_error)
        app.post("stopped", app.engine.last_error)
        self._pump(ms=100)
        self.assertIn("过压", app.status_var.get())
        self.assertEqual(app.status_color, "#c00")

    def test_connect_drops_device_when_idn_silent(self):
        import itech7722_engine as drv
        from itech7722_device import BaseTransport, DeviceError

        class SilentTransport(BaseTransport):
            name = "silent"

            def write(self, cmd):
                pass

            def read(self):
                return ""

            def query(self, cmd):
                return ""

        saved = drv.build_transport
        drv.build_transport = lambda conn: SilentTransport()
        try:
            eng = drv.Engine(type("UI", (), {"post": lambda s, k, p: None,
                                             "log_scpi": lambda s, d, t: None})())
            with self.assertRaises(DeviceError) as ctx:
                eng.connect(make_cfg(tempfile.gettempdir()))
            self.assertIsNone(eng.device, "识别失败后仍留着半开连接，后续自检会用脏会话")
            self.assertIn("结束符", str(ctx.exception))
        finally:
            drv.build_transport = saved

    def test_autodetect_sets_serial_for_itech_cdc_port(self):
        app = self._app()
        app._serial_ports_detailed = lambda: [("COM6", 0x2EC7, 0x7722, "USB 串行设备 (COM6)")]
        app.var_conn_type.set("usb")
        app._refresh_ports()
        self.assertEqual(app.var_conn_type.get(), "serial")
        self.assertEqual(app.var_port.get(), "COM6")
        self.assertIn("COM6", app.status_var.get())
        self.assertIn("USB", app.status_var.get())

    def test_scan_reports_no_available_ports(self):
        app = self._app()
        app._serial_ports_detailed = lambda: []
        app._refresh_ports()
        self.assertIn("未发现串口", app.status_var.get())
        self.assertEqual(app.var_port.get(), "")

    def test_keypoint_rules_embedded_in_data_recording(self):
        app = self._app()
        rules = app.key_tree.master
        self.assertEqual(rules.cget("text"), "关键设定电压记录")
        self.assertEqual(rules.master.cget("text"), "数据记录")
        self._pump(ms=100)
        self.assertTrue(app.key_tree.winfo_ismapped())

    def test_debug_and_help_pages_removed_diagnostics_still_work(self):
        from tkinter import ttk
        app = self._app()
        def widgets(parent):
            for child in parent.winfo_children():
                yield child
                yield from widgets(child)
        self.assertFalse(any(isinstance(w, ttk.Notebook) for w in widgets(self.root)))
        self.assertFalse(hasattr(app, "scpi_tree"))
        self.assertFalse(hasattr(app, "scpi_text"))
        app.post("scpi", "设备应答")
        self._pump(ms=100)
        self.assertTrue(any("设备应答" in line for line in app._diagnostics))
        for i in range(600):
            app._append_scpi(str(i))
        self.assertEqual(len(app._diagnostics), 500)


class ShippedConfigTests(unittest.TestCase):
    def test_project_config_is_loadable(self):
        here = os.path.dirname(os.path.abspath(__file__))
        cfg = load_config(os.path.join(here, "sample_config.ini"))
        self.assertEqual(cfg.scpi["func_ac"], "SOUR:NORM:MODE AC")
        self.assertIn(110.0, cfg.keypoints)
        self.assertIn(220.0, cfg.keypoints)
        self.assertEqual(cfg.keypoints[220.0], list(PARAMS))
        self.assertEqual(cfg.limits["max_voltage"], 300.0)
        self.assertEqual(cfg.setting["mode"], MODE_AC)


if __name__ == "__main__":
    unittest.main(verbosity=2)
