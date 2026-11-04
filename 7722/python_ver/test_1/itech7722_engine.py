"""运行引擎：独立于 Tk 和绘图库，统一控制输出、采样和异常收尾。"""
from __future__ import annotations

import math
import queue
import threading
import time

from itech7722_config import AppConfig, CONTROL_NAMES, MODE_NAMES, validate_run
from itech7722_device import DeviceError, ITech7722, build_transport, error_code
from itech7722_recorder import Recorder, Sample
from itech7722_feedback import VoltageFeedback, FeedbackError

UNIT_BY_CONTROL = {"CV": "V", "CI": "A", "CP": "W"}


class Engine:
    """执行定值/序列两种输出模式并周期采样。"""

    def __init__(self, ui):
        self.ui = ui
        self.device: ITech7722 | None = None
        self.thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.running = False
        self.output_on = False
        self._state_lock = threading.RLock()
        self.last_error = ""
        self.connection = None
        self.model = ""
        self._keypoint_updates = queue.SimpleQueue()

    def update_keypoints(self, rules):
        if self.running:
            self._keypoint_updates.put({float(k): list(v) for k, v in rules.items()})

    def connect(self, cfg: AppConfig) -> str:
        if self.device or self.running:
            raise DeviceError("已有连接，请先断开")
        transport = build_transport(cfg.connection)
        self.device = ITech7722(transport, cfg.scpi, log=self.ui.log_scpi)
        try:
            idn = self.device.identify().strip()
            if idn and ("," not in idn or error_code(idn) is not None):
                raise DeviceError(f"*IDN? 收到非型号应答: {idn!r}，请检查结束符和残留应答")
        except Exception:
            self._drop_connection()
            raise
        if not idn:
            errs = []
            try:
                errs = self.device.error_queue()
            except Exception:
                pass
            self._drop_connection()
            hint = ("未收到 *IDN? 应答。逐项排查：结束符（依次试 \\n、\\r、\\r\\n）、波特率、"
                    "设备是否处于 Remote/未键盘锁定、USB 线走的是 COM 口还是 USBTMC。")
            if errs:
                hint += " 设备错误队列：" + "; ".join(errs)
            raise DeviceError(hint)
        self.connection = dict(cfg.connection)
        self.model = idn.split(",")[1].strip().upper()
        return idn

    def _drop_connection(self):
        if self.device:
            try:
                self.device.close()
            except Exception:
                pass
        self.device = None
        self.connection = None
        self.model = ""

    def disconnect(self):
        if not self.stop():
            raise DeviceError("正在等待通信结束，暂不能断开连接")
        if self.device:
            try:
                self.device.output_off()
            except Exception as exc:
                raise DeviceError(f"关闭输出失败，连接保留以便重试: {exc}") from exc
            self.output_on = False
        self._drop_connection()

    def start_run(self, cfg: AppConfig, steps, recorder: Recorder):
        if self.running or (self.thread and self.thread.is_alive()):
            raise DeviceError("已在运行中")
        if not self.device:
            raise DeviceError("设备未连接")
        if self.connection is not None and cfg.connection != self.connection:
            raise DeviceError("连接参数已修改，请先断开并重新连接，确认实际控制的设备")
        cfg = cfg.copy()
        steps = list(steps)
        validate_run(cfg, steps)
        if self.device.tp.name != "sim" and self.model in ("IT-M7722", "M7722"):
            ratings = {"max_voltage": 300.0, "max_current": 6.0, "max_power": 600.0}
            for key, maximum in ratings.items():
                if float(cfg.limits[key]) > maximum:
                    raise DeviceError(f"IT-M7722 的 {key} 不能超过 {maximum:g}；请修改保护限值")
        self.device.scpi.update(cfg.scpi)
        if cfg.setting["output_mode"] == "hold" and cfg.setting["control"] in ("CI", "CP"):
            for key in ("ovp", "ocp", "opp", "curr", "err_next"):
                self.device._require(key)
        self.last_error = ""
        while not self._keypoint_updates.empty():
            self._keypoint_updates.get_nowait()
        self._stop.clear()
        self.running = True
        self.thread = threading.Thread(target=self._loop, args=(cfg, steps, recorder), daemon=True)
        try:
            self.thread.start()
        except Exception:
            self.running = False
            raise

    def _turn_on(self):
        with self._state_lock:
            if not self._stop.is_set() and not self.output_on:
                self.device.output_on()
                self.output_on = True

    def _check_device_errors(self):
        if not self.device.scpi.get("err_next"):
            return
        errors = self.device.error_queue()
        if not errors:
            raise DeviceError("错误队列无应答，无法确认设定是否被设备接受")
        bad = [message for message in errors if error_code(message) != 0]
        if bad:
            raise DeviceError("设备拒绝设定: " + "; ".join(bad))

    def stop(self, timeout=3.0):
        with self._state_lock:
            self._stop.set()
        thread = self.thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=timeout)
        if thread and thread.is_alive():
            return False
        self.thread = None
        return True

    def toggle_output(self, on: bool):
        if not self.device:
            raise DeviceError("设备未连接")
        if on:
            raise DeviceError("请通过启动运行开启输出，以确保设定和保护生效")
        else:
            if self.stop():
                self.device.output_off()
                self.output_on = False

    def _loop(self, cfg, steps, recorder):
        interval = max(0.05, int(cfg.recording.get("interval_ms", 1000)) / 1000.0)
        limits = dict(cfg.limits, mode=cfg.setting["mode"])
        if not steps and cfg.setting["control"] in ("CI", "CP"):
            limits["strict"] = True
        try:
            self.device.prepare_run()
            self.device.output_off()
            self.output_on = False
            recorder.reset()
            recorder.start({"接口": cfg.connection.get("type"),
                        "工作模式": MODE_NAMES.get(cfg.setting["mode"], cfg.setting["mode"]),
                        "最大电压V": cfg.limits["max_voltage"], "最大电流A": cfg.limits["max_current"],
                        "最大功率W": cfg.limits["max_power"],
                        "关键点": sorted(recorder.keypoints)})
            if self._stop.is_set():
                return
            self.device.set_function_mode(cfg.setting["mode"])
            self.device.set_protection_limits(cfg.limits["max_voltage"], cfg.limits["max_current"],
                                              cfg.limits["max_power"])
            if steps:
                self._run_sequence(cfg, steps, recorder, limits, interval)
            elif cfg.setting["control"] in ("CI", "CP"):
                self._run_feedback(cfg, recorder, limits, interval)
            else:
                self._run_hold(cfg, recorder, limits, interval)
        except FeedbackError as exc:
            self.last_error = f"闭环调节停止: {exc}"
            self.ui.post("alarm", self.last_error)
        except DeviceError as exc:
            self.last_error = f"通信异常: {exc}"
            self.ui.post("alarm", self.last_error)
        except Exception as exc:
            self.last_error = f"运行异常: {exc!r}"
            self.ui.post("alarm", self.last_error)
        finally:
            try:
                if self.device:
                    self.device.output_off()
                self.output_on = False
            except Exception as exc:
                self.last_error = f"关闭输出失败，请检查设备输出状态: {exc}"
                self.ui.post("alarm", self.last_error)
            self.device.checked_writes = False
            self.running = False
            self.ui.post("stopped", self.last_error or "运行结束，输出已关闭")

    def _check_protection(self, m, limits):
        mv, mc, mp = limits["max_voltage"], limits["max_current"], limits["max_power"]
        v, i, p = m.get("voltage"), m.get("current"), m.get("power")
        for name, value in (("电压", v), ("电流", i), ("功率", p)):
            if value is None or not math.isfinite(value):
                return f"{name}回读无效，无法确认输出状态"
        margin = 1.0 if limits.get("strict") else 1.002
        if mv and abs(v) > mv * margin:
            return f"过压 {v:.2f}V > {mv:g}V"
        if mc and abs(i) > mc * margin:
            return f"过流 {i:.3f}A > {mc:g}A"
        if mp and abs(p) > mp * margin:
            return f"过功率 {p:.2f}W > {mp:g}W"
        return ""

    def _sample(self, recorder, limits, step_no, desc, control, value, params=None,
                measurements=None, status="正常", write_record=True):
        m = measurements if measurements is not None else self.device.measure(limits["mode"])
        trip = self._check_protection(m, limits)
        if m.get("power_factor") is None:
            status += "；功率因子无有效回读"
        if trip:
            self.last_error = f"触发保护：{trip}"
            self._stop.set()
            self.device.output_off()
            self.output_on = False
        sample = Sample.now(recorder.elapsed, step_no, m,
                            status=status if not trip else "保护:" + trip, desc=desc)
        while not self._keypoint_updates.empty():
            recorder.update_keypoints(self._keypoint_updates.get_nowait())
        command_voltage = float(self.device.setting["value"])
        hit = recorder.log(sample, MODE_NAMES.get(limits["mode"], limits["mode"]),
                           f"{control}={value:g}", params=params, set_voltage=command_voltage,
                           write_regular=write_record or bool(trip))
        self.ui.post("sample", (sample, control, value))
        if hit:
            readback = "--" if sample.voltage is None else f"{sample.voltage:.3f}V"
            self.ui.post("info", f"关键设定 {command_voltage:g}V 已记录，回读 {readback}")
        return trip

    def _run_feedback(self, cfg, recorder, limits, record_interval):
        control, target = cfg.setting["control"], float(cfg.setting["value"])
        regulator = VoltageFeedback(control, target, limits, cfg.feedback)
        # 使用电压源启动，实际电流/功率来自回读，绝不把目标写成限流/保护参数。
        self.device.apply_setting("CV", 0.0, cfg.setting["frequency"], limits)
        self._check_device_errors()
        self._turn_on()
        self.ui.post("info", f"{CONTROL_NAMES[control]}目标 {target:g}{UNIT_BY_CONTROL[control]}，"
                             f"从0V开始，闭环电压上限 {regulator.ceiling:g}V")
        interval = float(cfg.feedback["interval_ms"]) / 1000
        next_record = 0.0
        n = 0
        while not self._stop.is_set():
            cycle_start = time.monotonic()
            m = self.device.measure(limits["mode"])
            n += 1
            if self._check_protection(m, limits):
                self._sample(recorder, limits, n, "闭环保护", control, target, measurements=m)
                self.ui.post("alarm", self.last_error)
                return
            voltage, status = regulator.update(m, time.monotonic())
            write_record = cycle_start >= next_record
            self._sample(recorder, limits, n, f"电压设定={self.device.setting['value']:g}V; 下一设定={voltage:g}V",
                         control, target, measurements=m, status=status, write_record=write_record)
            if write_record:
                next_record = cycle_start + record_interval
            current_voltage = float(self.device.setting["value"])
            if voltage > current_voltage:
                voltage = recorder.next_rising_keypoint(current_voltage, voltage)
                regulator.voltage = voltage
            # 与停止请求互斥；停止请求生效后不再下发新的升压设定。
            with self._state_lock:
                if self._stop.is_set():
                    return
                if abs(voltage - float(self.device.setting["value"])) >= 1e-6:
                    self.device.set_voltage(voltage, limits["mode"])
            self._stop.wait(max(0, interval - (time.monotonic() - cycle_start)))

    def _run_hold(self, cfg, recorder, limits, interval):
        control = cfg.setting["control"]
        value = cfg.setting["value"]
        freq = cfg.setting["frequency"]
        self.device.apply_setting(control, value, freq, limits)
        self._check_device_errors()
        self._turn_on()
        self.ui.post("info", f"定值输出：{CONTROL_NAMES.get(control, control)} = {value:g}"
                             f"{UNIT_BY_CONTROL.get(control, '')} 频率 {freq:g}Hz")
        n = 0
        while not self._stop.is_set():
            cycle_start = time.monotonic()
            n += 1
            trip = self._sample(recorder, limits, n, "定值", control, value)
            if trip:
                self.ui.post("alarm", f"触发保护：{trip}")
                self._stop.set()
                return
            self._stop.wait(max(0, interval - (time.monotonic() - cycle_start)))

    def _run_sequence(self, cfg, steps, recorder, limits, interval):
        while not self._stop.is_set():
            for st in steps:
                if self._stop.is_set():
                    return
                self.device.apply_setting(st.control, st.value,
                                          st.frequency or cfg.setting["frequency"], limits)
                self._check_device_errors()
                self._turn_on()
                self.ui.post("step", st.label())
                deadline = time.monotonic() + max(0.0, st.duration)
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0 or self._stop.is_set():
                        break
                    trip = self._sample(recorder, limits, st.index + 1, "序列",
                                        st.control, st.value)
                    if trip:
                        self.ui.post("alarm", f"触发保护：{trip}（步骤 {st.index + 1}）")
                        self._stop.set()
                        return
                    self._stop.wait(max(0, min(interval, deadline - time.monotonic())))
            if not cfg.sequence.get("loop"):
                self.ui.post("info", "序列执行完成")
                return

