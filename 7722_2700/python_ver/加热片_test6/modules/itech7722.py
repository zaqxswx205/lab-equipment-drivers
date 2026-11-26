"""7722设备操作与SCPI协议；串口收发由通信层提供。"""
from __future__ import annotations
import math
import threading
import time

DEFAULT_SCPI = {
    "idn": "*IDN?", "remote": "SYST:REM",
    "func_dc": "SOUR:NORM:MODE DC", "func_ac": "SOUR:NORM:MODE AC",
    "func_acdc": "SOUR:NORM:MODE AC+DC",
    "volt": "", "volt_dc": "SOUR:NORM:VOLT:DC {v}",
    "volt_ac": "SOUR:NORM:VOLT:AC {v}",
    "curr": "PROT:MAX:CURR:LIM {v}",
    # 手册没有恒功率输出设定；PROT:POW 是保护，不能当成 CP 控制。
    "power": "", "freq": "SOUR:NORM:FREQ {f}",
    "out_on": "OUTP:STAT ON", "out_off": "OUTP:STAT OFF",
    "ovp": "PROT:RMS:VOLT {v}", "ocp": "PROT:RMS:CURR {v}",
    "opp": "PROT:POW {v}",
    "meas_volt": "", "meas_volt_dc": "MEAS:VOLT:DC?",
    "meas_volt_ac": "MEAS:VOLT:AC?", "meas_curr": "",
    "meas_curr_dc": "MEAS:CURR:DC?", "meas_curr_ac": "MEAS:CURR:AC?",
    "meas_power": "MEAS:POW?", "meas_freq": "MEAS:FREQ?",
    "meas_power_factor": "MEAS:POW:PFAC?",
    "err_next": "SYST:ERR?",
}

MODE_DC = "DC"

MODE_AC = "AC"

MODE_ACDC = "ACDC"

CONTROL_CV = "CV"

CONTROL_CI = "CI"

CONTROL_CP = "CP"

class DeviceError(RuntimeError):
    pass

def format_number(value) -> str:
    """SCPI 数值：去掉无意义的尾随 0，避免下发 'SOUR:VOLT 220.0' 这类写法。"""
    if not math.isfinite(float(value)):
        raise DeviceError(f"不能下发非有限数值: {value}")
    txt = f"{float(value):.6f}".rstrip("0").rstrip(".")
    return txt or "0"

def error_code(text) -> int | None:
    """解析 SYST:ERR? 返回的错误码，如 '-113,"Undefined header",1' → -113。"""
    txt = (text or "").strip()
    num = ""
    for ch in txt:
        if ch in "+-0123456789":
            num += ch
        else:
            break
    try:
        return int(num)
    except ValueError:
        return None

class ITech7722:
    """高层设备 API。所有指令文本来自 scpi_map，便于按现场手册调整。"""

    def __init__(self, transport, scpi_map: dict | None = None, log=None):
        self.tp = transport
        self.scpi = dict(DEFAULT_SCPI)
        if scpi_map:
            self.scpi.update(scpi_map)
        self._log = log or (lambda direction, text: None)
        self._lock = threading.RLock()
        self.checked_writes = False
        self.limits = {"max_voltage": 0.0, "max_current": 0.0, "max_power": 0.0}
        self.setting = {"control": CONTROL_CV, "value": 0.0, "frequency": 0.0, "mode": MODE_AC}

    def _fmt(self, key, **kw):
        template = self.scpi.get(key)
        if not template:
            return None
        args = {k: format_number(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else v
                for k, v in kw.items()}
        try:
            return template.format(**args)
        except (KeyError, ValueError, IndexError) as exc:
            raise DeviceError(f"指令模板 {key}={template!r} 缺少占位符 {exc}") from exc

    def _send(self, key, **kw):
        cmd = self._fmt(key, **kw)
        if not cmd:
            # 模板为空表示该机型不支持这条指令，跳过下发
            return None
        with self._lock:
            self._log(">>", cmd)
            if cmd.strip().endswith("?"):
                reply = self.tp.query(cmd)
                self._log("<<", reply)
                return reply
            self.tp.write(cmd)
            if self.checked_writes and self.scpi.get("err_next"):
                errors = self.error_queue()
                bad = [e for e in errors if error_code(e) != 0]
                if not errors or error_code(errors[-1]) != 0 or bad:
                    detail = "; ".join(bad) or "错误队列无有效结束应答"
                    raise DeviceError(f"指令 {key} [{cmd}] 失败: {detail}")
        return None

    def raw(self, cmd: str) -> str:
        cmd = cmd.strip()
        if not cmd:
            return ""
        with self._lock:
            self._log(">>", cmd)
            if cmd.endswith("?"):
                reply = self.tp.query(cmd)
            else:
                # 写指令只发不读：设备不会应答，去读会白等一个超时
                self.tp.write(cmd)
                reply = ""
            if reply:
                self._log("<<", reply)
        return reply

    def identify(self) -> str:
        return self._send("idn") or ""

    def prepare_run(self):
        """先记录并读完旧错误，再逐条核验本次写入；不清除保护状态。"""
        if self.scpi.get("err_next"):
            previous = self.error_queue()
            if not previous or error_code(previous[-1]) != 0:
                raise DeviceError("启动前错误队列未读完或应答无效，请检查通信和 err_next 映射")
            bad = [e for e in previous if error_code(e) != 0]
            if bad:
                self._log("!!", "上次会话残留错误（已读取）: " + "; ".join(bad))
        self.checked_writes = True
        self._require("remote")
        self._send("remote")

    def error_queue(self, limit: int = 64) -> list[str]:
        """逐条读出设备错误队列，遇到 0/No error 停止。"""
        out: list[str] = []
        for _ in range(limit):
            txt = self._send("err_next")
            if not txt:
                break
            out.append(txt)
            code = error_code(txt)
            if code is None or code == 0:
                break
        return out

    def clear_errors(self, limit: int = 64) -> int:
        """读空残留错误，避免把上一条指令的报错归给当前指令。返回清掉的条数。"""
        n = 0
        for _ in range(limit):
            txt = self._send("err_next")
            code = error_code(txt)
            if not txt or code is None or code == 0:
                break
            n += 1
        return n

    def probe(self, cmd: str) -> list[str]:
        """发送一条指令并回读紧随其后的错误队列，用于确认助记符是否被设备识别。

        -113 (Undefined header) 表示助记符与本机不符，需要改 config.ini 的 [SCPI] 段。
        """
        self.clear_errors()
        self.raw(cmd)
        time.sleep(0.05)
        return self.error_queue()

    def set_function_mode(self, mode: str) -> None:
        mode = mode.upper()
        key = {MODE_DC: "func_dc", MODE_AC: "func_ac", MODE_ACDC: "func_acdc"}.get(mode)
        if key is None:
            raise DeviceError(f"未知输出模式: {mode}")
        self._require(key)
        self._send(key)
        self.setting["mode"] = mode

    def set_protection_limits(self, max_voltage, max_current, max_power) -> None:
        """把保护限值下发到设备；模板缺失时只记录软件限值。"""
        self.limits = {"max_voltage": float(max_voltage),
                       "max_current": float(max_current),
                       "max_power": float(max_power)}
        if max_voltage > 0:
            self._send("ovp", v=max_voltage)
        if max_current > 0:
            self._send("ocp", v=max_current)
        if max_power > 0:
            self._send("opp", v=max_power)

    def apply_setting(self, control: str, value: float, frequency: float,
                      limits: dict | None = None) -> None:
        """按控制量下发设定。

        CV=给定电压（电流/功率取限值作裕量）；CI=给定电流（电压取上限值）；
        CP=给定功率（电压电流取上限值）。交流模式额外下发频率。
        """
        control = control.upper()
        lim = limits or self.limits
        mv = float(lim.get("max_voltage", 0.0) or 0.0)
        mc = float(lim.get("max_current", 0.0) or 0.0)
        mp = float(lim.get("max_power", 0.0) or 0.0)
        mode = str(lim.get("mode", self.setting["mode"])).upper()
        if control not in (CONTROL_CV, CONTROL_CI, CONTROL_CP):
            raise DeviceError(f"未知控制量: {control}")
        if not math.isfinite(float(value)) or value < 0:
            raise DeviceError("设定值必须为有限非负数")
        if mode in (MODE_AC, MODE_ACDC):
            if not math.isfinite(float(frequency)) or frequency <= 0:
                raise DeviceError("交流频率必须为有限正数")
            self._require("freq")
        voltage_key = "volt_dc" if mode == MODE_DC else "volt_ac"
        if not self.scpi.get(voltage_key):
            voltage_key = "volt"
        self._require(voltage_key)
        if control != CONTROL_CV and self.tp.name != "sim":
            raise DeviceError("IT-M7700 当前驱动仅支持电压设定 CV；电流限值/功率保护不是恒流/恒功率控制")
        if control == CONTROL_CV:
            if self.tp.name == "sim":
                self.tp.state["power_limit"] = mp
            self._send(voltage_key, v=value)
            if mode == MODE_ACDC:
                # 单设定值界面未提供偏置，显式清零以免沿用面板残留直流量。
                self._require("volt_dc")
                self._send("volt_dc", v=0)
            if mc > 0:
                self._send("curr", v=mc)
            if mp > 0:
                self._send("power", v=mp)
        elif control == CONTROL_CI:
            self._send("curr", v=value)
            self._send(voltage_key, v=mv)
            self.tp.state["power_limit"] = mp
        else:
            # CI/CP 仅为离线模拟功能，不能向真机发送不存在的 SOUR:POW。
            self.tp.state["power_limit"] = float(value)
            self._send(voltage_key, v=mv)
            self._send("curr", v=mc)

        if mode in (MODE_AC, MODE_ACDC):
            self._send("freq", f=float(frequency or 0.0))
            self.setting["frequency"] = float(frequency or 0.0)
        else:
            self.setting["frequency"] = 0.0

        self.setting.update({"control": control, "value": float(value)})

    def output_on(self) -> None:
        self._require("out_on")
        self._send("out_on")

    def set_voltage(self, voltage, mode=None):
        """闭环仅更新电压，保留已经设置的频率、合规上限与硬件保护。"""
        value = float(voltage)
        if not math.isfinite(value) or value < 0 or value > self.limits["max_voltage"]:
            raise DeviceError("闭环电压设定超出有效范围")
        mode = mode or self.setting["mode"]
        key = "volt_dc" if mode == MODE_DC else "volt_ac"
        if not self.scpi.get(key):
            key = "volt"
        self._require(key)
        self._send(key, v=value)
        self.setting["value"] = value

    def output_off(self) -> None:
        self._require("out_off")
        self._send("out_off")

    def _require(self, key):
        if not (self.scpi.get(key) or "").strip():
            raise DeviceError(f"必需的 SCPI 指令映射为空: {key}")

    def _num(self, key, **kw):
        txt = self._send(key, **kw)
        if txt in (None, ""):
            return None
        try:
            value = float(txt.split(",")[0])
            return value if math.isfinite(value) else None
        except ValueError:
            return None

    def measure(self, mode: str | None = None) -> dict:
        """读取电压/电流/功率/频率。不支持的量返回 None，UI 侧显示 '--'。"""
        mode = (mode or self.setting["mode"]).upper()
        if mode == MODE_DC:
            v = self._num("meas_volt_dc")
            if v is None:
                v = self._num("meas_volt")
            i = self._num("meas_curr_dc")
            if i is None:
                i = self._num("meas_curr")
            f = 0.0
        elif mode == MODE_AC:
            v = self._num("meas_volt_ac")
            if v is None:
                v = self._num("meas_volt")
            i = self._num("meas_curr_ac")
            if i is None:
                i = self._num("meas_curr")
            f = self._num("meas_freq")
        else:
            va, vd = self._num("meas_volt_ac"), self._num("meas_volt_dc")
            ia, idc = self._num("meas_curr_ac"), self._num("meas_curr_dc")
            v = math.hypot(va, vd) if va is not None and vd is not None else None
            i = math.hypot(ia, idc) if ia is not None and idc is not None else None
            f = self._num("meas_freq")
        p = self._num("meas_power")
        if mode == MODE_DC and p is None and v is not None and i is not None:
            p = v * i
        pf = self._num("meas_power_factor")
        if pf is not None and abs(pf) > 1:
            pf = None
        return {"voltage": v, "current": i, "power": p, "frequency": f, "power_factor": pf}

    def close(self):
        self.tp.close()

MODE_NAMES = {MODE_DC: "直流", MODE_AC: "交流", MODE_ACDC: "交直流"}

MODE_CODES = {v: k for k, v in MODE_NAMES.items()}

CONTROL_NAMES = {CONTROL_CV: "电压源(给定电压)", "CI": "电流闭环(调节电压)", "CP": "功率闭环(调节电压)"}

CONTROL_CODES = {name: code for code, name in CONTROL_NAMES.items()}

CONTROL_ALIASES = {
    CONTROL_CV: CONTROL_CV, "V": CONTROL_CV, "VOLT": CONTROL_CV, "电压": CONTROL_CV,
    CONTROL_CI: CONTROL_CI, "I": CONTROL_CI, "CURR": CONTROL_CI, "电流": CONTROL_CI,
    CONTROL_CP: CONTROL_CP, "P": CONTROL_CP, "POW": CONTROL_CP, "功率": CONTROL_CP,
}
