"""ITech 7722 通信层：传输实现 + 可配置 SCPI 指令映射 + 设备高层 API。

指令映射不硬编码在业务逻辑里：IT7722 与 IT-M7700 等系列助记符存在差异，
默认指令依据 IT-M7700 Programming Guide，配置文件可覆盖映射。
通用电源的 SOUR:FUNC / SOUR:VOLT / SOUR:CURR 不能用于该系列。
"""

from __future__ import annotations

import math
import socket
import threading
import time

# ITECH IT-M7700 Programming Guide, Source/Protect/Measure/Programming Examples.
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


class BaseTransport:
    name = "base"

    def write(self, cmd: str) -> None:
        raise NotImplementedError

    def read(self) -> str:
        raise NotImplementedError

    def query(self, cmd: str) -> str:
        self.write(cmd)
        return self.read()

    def close(self) -> None:
        pass

    @property
    def is_open(self) -> bool:
        return True


class SerialTransport(BaseTransport):
    name = "serial"

    def __init__(self, port, baudrate=9600, bytesize=8, parity="N", stopbits=1,
                 timeout=2.0, terminator="\r\n"):
        import serial
        self._term = terminator
        self._timeout = float(timeout)
        self._ser = serial.Serial(
            port=port, baudrate=baudrate, bytesize=bytesize, parity=parity,
            stopbits=stopbits, timeout=0.05, write_timeout=self._timeout,
        )

    def write(self, cmd):
        self._ser.write((cmd + self._term).encode("ascii", "replace"))
        self._ser.flush()

    def read(self):
        """按“字节流空闲”收尾，不依赖换行符。

        ITECH 部分机型以 \\r 结尾、也有不分块回送的，用 readline() 会一直等到
        超时返回空串，界面就表现为“自检没反应”。
        """
        buf = bytearray()
        start = last = time.monotonic()
        while True:
            if time.monotonic() - start >= self._timeout:
                break
            waiting = self._ser.in_waiting
            if waiting:
                buf.extend(self._ser.read(waiting))
                last = time.monotonic()
                continue
            now = time.monotonic()
            if buf and now - last >= 0.08:
                break
            if now - start >= self._timeout:
                break
            time.sleep(0.005)
        return bytes(buf).decode("ascii", "replace").strip()

    def close(self):
        try:
            self._ser.close()
        except Exception:
            pass

    @property
    def is_open(self):
        return bool(getattr(self, "_ser", None) and self._ser.is_open)


class TcpTransport(BaseTransport):
    """LAN/以太网 SCPI。ITECH 设备常见端口 9999，ASRL/LAN 服务亦见 5025。"""

    name = "tcp"

    def __init__(self, host, port=9999, timeout=2.0, terminator="\n"):
        self._term = terminator
        self._sock = socket.create_connection((host, int(port)), timeout=timeout)
        self._sock.settimeout(timeout)
        self._timeout = float(timeout)
        self._buf = b""

    def write(self, cmd):
        self._sock.sendall((cmd + self._term).encode("ascii", "replace"))

    def read(self):
        deadline = time.monotonic() + self._timeout
        while True:
            self._buf = self._buf.lstrip(b"\r\n")
            ends = [i for i in (self._buf.find(b"\r"), self._buf.find(b"\n")) if i >= 0]
            if ends:
                index = min(ends)
                line, self._buf = self._buf[:index], self._buf[index + 1:]
                return line.decode("ascii", "replace").strip()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise DeviceError("TCP 读取超时，未收到完整应答")
            self._sock.settimeout(remaining)
            chunk = self._sock.recv(4096)
            if not chunk:
                raise DeviceError("连接已被设备关闭")
            self._buf += chunk

    def close(self):
        try:
            self._sock.close()
        except Exception:
            pass


VISA_INSTALL_HINT = (
    "USB(TMC)/VISA 接口需要 pyvisa 后端。安装（注意用运行本程序的那个解释器）：\n"
    '  C:\\ProgramData\\anaconda3\\python.exe -m pip install pyvisa pyvisa-py\n'
    "装了 NI VISA / Keysight IO Libraries 也可以，pyvisa 会自动使用它。\n"
    "若设备在设备管理器里显示为 COM 口（USB 转串口或 CDC 虚拟串口），请改用 type=serial，"
    "USB 线本身不需要 pyvisa。"
)


def _visa_manager():
    try:
        import pyvisa
    except ImportError as exc:
        raise DeviceError(VISA_INSTALL_HINT) from exc
    return pyvisa.ResourceManager()


def list_visa_resources() -> list[str]:
    """列出 VISA 可见仪器资源，供界面扫描（USB 与网口都会出现）。"""
    rm = _visa_manager()
    try:
        return [str(r) for r in rm.list_resources("?*INSTR")]
    finally:
        rm.close()


class VisaTransport(BaseTransport):
    name = "visa"

    def __init__(self, resource, timeout=2000, rm=None):
        if not resource:
            raise DeviceError("VISA 资源名为空，请在界面填写或改用“扫描”选取")
        self.resource = resource
        self._rm = rm or _visa_manager()
        try:
            self._inst = self._rm.open_resource(resource)
            self._inst.timeout = timeout
            self._inst.read_termination = "\n"
            self._inst.write_termination = "\n"
        except Exception:
            self.close()
            raise

    def write(self, cmd):
        self._inst.write(cmd)

    def read(self):
        return self._inst.read().strip()

    def query(self, cmd):
        return self._inst.query(cmd).strip()

    def close(self):
        for holder in (getattr(self, "_inst", None), getattr(self, "_rm", None)):
            try:
                if holder is not None:
                    holder.close()
            except Exception:
                pass


class UsbTransport(VisaTransport):
    """USB(TMC)。留空资源名时自动挑选枚举到的第一台 USB 仪器。"""

    name = "usb"

    def __init__(self, resource="", timeout=2.0):
        rm = _visa_manager()
        if not resource:
            try:
                found = [str(r) for r in rm.list_resources("?*INSTR")
                         if str(r).upper().startswith("USB")]
            except Exception:
                rm.close()
                raise
            if not found:
                rm.close()
                raise DeviceError("未发现 USB(TMC) 仪器：检查 USB 线是否插好、驱动是否装载、"
                                  "设备是否已上电；也可在设备管理器确认它是否被识别为 COM 口"
                                  "（若是请改用 type=serial）")
            resource = found[0]
        super().__init__(resource, timeout * 1000, rm=rm)


class SimTransport(BaseTransport):
    """离线仿真传输：按恒压/恒流/恒功率与保护限值给出合理读数。

    用于在没有接设备时验证时序、记录、曲线与 UI 自适应；不产生任何真实输出。
    """

    name = "sim"

    def __init__(self, load_resistance=50.0):
        self._lock = threading.Lock()
        self.load_resistance = load_resistance
        self.state = {
            "func": MODE_AC, "volt": 0.0, "volt_dc": 0.0, "volt_ac": 0.0,
            "curr_limit": 100.0, "power_limit": 100000.0, "freq": 50.0,
            "output": False, "ovp": None, "ocp": None, "opp": None,
        }
        self.commands: list[str] = []
        self.protected_reason = ""
        self.errors: list[str] = []

    def write(self, cmd):
        with self._lock:
            self.commands.append(cmd)
            self._apply(cmd)

    def read(self):
        return "OK"

    def _apply(self, cmd):
        up = cmd.upper()
        if up.startswith("NORM:"):
            up = "SOUR:" + up
        s = self.state
        if up.startswith("*IDN?"):
            s["_idn"] = "ITECH,SIM7722,SN0001,1.00"
            return
        if up == "SYST:REM":
            return
        if up.startswith("SOUR:NORM:MODE"):
            s["func"] = cmd.split()[-1].upper().replace("AC+DC", "ACDC")
            return
        if up.startswith("OUTP"):
            s["output"] = cmd.split()[-1].upper() in ("ON", "1")
            return
        for key, names in (
            ("volt_dc", ("SOUR:NORM:VOLT:DC",)),
            ("volt_ac", ("SOUR:NORM:VOLT:AC",)),
            ("volt", ("SOUR:NORM:VOLT",)),
            ("curr_limit", ("PROT:MAX:CURR:LIM",)),
            ("power_limit", ("SIM:POW",)),
            ("freq", ("SOUR:NORM:FREQ",)),
            ("ovp", ("PROT:RMS:VOLT",)),
            ("ocp", ("PROT:RMS:CURR",)),
            ("opp", ("PROT:POW",)),
        ):
            if up.split()[0] in names:
                try:
                    s[key] = float(cmd.split()[-1])
                except ValueError:
                    pass
                return
        # 未识别的助记符：像真机一样压入 -113，用于离线验证错误归因链路
        self.errors.append(f'-113,"Undefined header",{len(self.commands)}')

    def _next_error(self):
        if self.errors:
            return self.errors.pop(0)
        return '0,"No error",0'

    def _operating_point(self):
        """依据设定值与限值估算实际工作点（CC/CP 会被限住）。"""
        s = self.state
        if not s["output"]:
            return 0.0, 0.0, 0.0, 0.0
        v_set = 0.0
        if s["func"] in (MODE_AC, MODE_ACDC):
            v_set = s.get("volt_ac") or s.get("volt", 0.0)
        if s["func"] in (MODE_DC, MODE_ACDC):
            v_set = max(v_set, s.get("volt_dc") or s.get("volt", 0.0))
        if s["func"] == MODE_ACDC:
            v_set = math.hypot(s["volt_ac"], s["volt_dc"])
        i_set = s["curr_limit"]
        p_set = s["power_limit"]

        v = v_set
        if s.get("ovp") and v > s["ovp"]:
            self.protected_reason = "OVP"
            v = s["ovp"]
        i = min(v / self.load_resistance, i_set)
        p = v * i
        if p > p_set:
            i = p_set / v if v else 0.0
            p = p_set
            if s.get("opp"):
                self.protected_reason = "OPP"
        if s.get("ocp") and i > s["ocp"]:
            self.protected_reason = "OCP"
            i = s["ocp"]
            p = v * i
        jitter = 1 + 0.004 * math.sin(time.time() * 2.0)
        v *= jitter
        i *= jitter
        return v, i, v * i, s["freq"]

    def query(self, cmd):
        with self._lock:
            self.commands.append(cmd)
            up = cmd.upper()
            if "*IDN" in up:
                return self.state.get("_idn", "ITECH,SIM7722,SN0001,1.00")
            if up.startswith("SYST:ERR"):
                return self._next_error()
            v, i, p, f = self._operating_point()
            if "PFAC" in up:
                return "1.0000" if v and i else "0.0000"
            if ":VOLT:" in up or ":CURR:" in up:
                mode = self.state["func"]
                if mode == MODE_ACDC:
                    total = math.hypot(self.state["volt_ac"], self.state["volt_dc"])
                    component = self.state["volt_dc"] if ":DC?" in up else self.state["volt_ac"]
                    ratio = component / total if total else 0
                    v, i = v * ratio, i * ratio
                elif (":DC?" in up and mode == MODE_AC) or (":AC?" in up and mode == MODE_DC):
                    v, i = 0.0, 0.0
            if "VOLT" in up:
                return f"{v:.4f}"
            if "CURR" in up:
                return f"{i:.5f}"
            if "POW" in up:
                return f"{p:.4f}"
            if "FREQ" in up:
                return f"{f:.2f}"
            if up.startswith("OUTP") or up.startswith("SOUR:PROT"):
                return "OK"
            return "0"


def build_transport(conn: dict) -> BaseTransport:
    kind = str(conn.get("type", "sim")).lower()
    if kind == "serial":
        return SerialTransport(
            port=conn["port"],
            baudrate=int(conn.get("baudrate", 9600)),
            bytesize=int(conn.get("bytesize", 8)),
            parity=str(conn.get("parity", "N")),
            stopbits=float(conn.get("stopbits", 1)),
            timeout=float(conn.get("timeout", 2.0)),
            terminator=str(conn.get("terminator", "\\r\\n")).encode().decode("unicode_escape"),
        )
    if kind == "tcp":
        return TcpTransport(
            host=conn["host"],
            port=int(conn.get("tcp_port", 9999)),
            timeout=float(conn.get("timeout", 2.0)),
            terminator=str(conn.get("terminator", "\\n")).encode().decode("unicode_escape"),
        )
    if kind == "visa":
        return VisaTransport(resource=conn["resource"], timeout=float(conn.get("timeout", 2.0)) * 1000)
    if kind in ("usb", "usbtmc"):
        return UsbTransport(resource=str(conn.get("usb_resource", "")).strip(),
                            timeout=float(conn.get("timeout", 2.0)))
    if kind == "sim":
        return SimTransport(load_resistance=float(conn.get("sim_load_resistance", 50.0)))
    raise DeviceError(f"未知连接类型: {kind}（可用: sim / serial / usb / tcp / visa）")


class ITech7722:
    """高层设备 API。所有指令文本来自 scpi_map，便于按现场手册调整。"""

    def __init__(self, transport: BaseTransport, scpi_map: dict | None = None, log=None):
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
