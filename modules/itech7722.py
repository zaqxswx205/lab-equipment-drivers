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


class ITECH7722(ITech7722):
    """便捷入口：传入连接字典或已创建的 transport；退出 with 时关闭输出。

    构造函数只建立连接，不会自动开启输出。close() 总会尝试关闭输出，
    即使关闭输出失败也会释放连接，并将异常交给调用者。
    """

    def __init__(self, connection=None, *, transport=None, scpi_map=None, log=None):
        if connection is not None and transport is not None:
            raise ValueError("connection 与 transport 只能指定一个")
        if connection is None and transport is None:
            raise ValueError("请显式指定连接参数；离线测试使用 {'type': 'sim'}")
        super().__init__(transport if transport is not None else build_transport(connection),
                         scpi_map=scpi_map, log=log)
        self._closed = False

    def close(self):
        if self._closed:
            return
        try:
            self.output_off()
        finally:
            try:
                super().close()
            finally:
                self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()


# ---- config ----
"""config.ini 的读写：连接参数、SCPI 指令映射、保护限值、设定值、记录规则。"""


import configparser
import copy
import math
import os
import tempfile
from dataclasses import dataclass, field


PARAMS = ("voltage", "current", "power", "power_factor")
MODE_NAMES = {MODE_DC: "直流", MODE_AC: "交流", MODE_ACDC: "交直流"}
MODE_CODES = {v: k for k, v in MODE_NAMES.items()}
CONTROL_NAMES = {CONTROL_CV: "电压源(给定电压)", "CI": "电流闭环(调节电压)", "CP": "功率闭环(调节电压)"}
CONTROL_CODES = {name: code for code, name in CONTROL_NAMES.items()}


def _f(value, default=0.0):
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        if default is None:
            return None
        raise ValueError(f"无效数值: {value!r}") from None


@dataclass
class AppConfig:
    connection: dict = field(default_factory=lambda: {"type": "serial", "port": "COM6",
                                                       "baudrate": 9600, "timeout": 2.0})
    scpi: dict = field(default_factory=lambda: dict(DEFAULT_SCPI))
    limits: dict = field(default_factory=lambda: {"max_voltage": 300.0, "max_current": 6.0,
                                                   "max_power": 600.0})
    setting: dict = field(default_factory=lambda: {"mode": MODE_AC, "control": CONTROL_CV,
                                                    "value": 5.0, "frequency": 50.0,
                                                    "output_mode": "hold"})
    sequence: dict = field(default_factory=lambda: {"file": "sequence_demo.csv", "loop": False})
    recording: dict = field(default_factory=lambda: {
        "interval_ms": 1000, "params": list(PARAMS), "enabled": True,
        "log_file": "data_log.csv", "keypoint_file": "keypoint_log.csv"})
    keypoints: dict = field(default_factory=dict)
    keypoint_tolerance: float = 0.02
    feedback: dict = field(default_factory=lambda: {
        "max_voltage": 5.0, "step_voltage": 0.2, "interval_ms": 200.0,
        "tolerance_rel": 0.01, "timeout_s": 60.0, "settle_timeout_s": 3.0,
        "readback_tolerance_v": 1.0})

    def copy(self) -> "AppConfig":
        return copy.deepcopy(self)


def validate_run(cfg: AppConfig, steps) -> None:
    """在任何硬件写入前校验完整运行计划，拒绝隐式降级或非有限数值。"""
    def number(value, name, positive=False):
        try:
            result = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"{name}必须是数值") from None
        if not math.isfinite(result) or result < 0 or (positive and result == 0):
            raise ValueError(f"{name}必须是有限{'正数' if positive else '非负数'}")
        return result

    mode = cfg.setting.get("mode")
    if mode not in MODE_NAMES:
        raise ValueError(f"未知工作模式: {mode}")
    out = cfg.setting.get("output_mode")
    if out not in ("hold", "seq"):
        raise ValueError(f"未知输出方式: {out}")
    if out == "seq" and not steps:
        raise ValueError("序列模式必须包含有效步骤")
    if out == "hold" and steps:
        raise ValueError("定值模式不能传入序列步骤")
    for name in ("max_voltage", "max_current", "max_power"):
        number(cfg.limits.get(name), name, positive=True)
    number(cfg.recording.get("interval_ms"), "采样间隔", positive=True)
    for voltage in cfg.keypoints:
        number(voltage, "关键设定电压")
    default_freq = number(cfg.setting.get("frequency"), "频率")
    plans = [(s.control, s.value, s.frequency or default_freq) for s in steps] if steps else [
        (cfg.setting.get("control"), cfg.setting.get("value"), default_freq)]
    for index, (control, value, frequency) in enumerate(plans, 1):
        if control not in CONTROL_NAMES:
            raise ValueError(f"步骤 {index}: 未知控制量 {control}")
        value = number(value, f"步骤 {index} 设定值")
        limit_key = {"CV": "max_voltage", "CI": "max_current", "CP": "max_power"}[control]
        limit = float(cfg.limits[limit_key])
        if limit and value > limit:
            raise ValueError(f"步骤 {index}: 设定值 {value:g} 超过 {limit_key}={limit:g}")
        number(frequency, f"步骤 {index} 频率", positive=mode != MODE_DC)
    for step in steps:
        number(step.duration, f"步骤 {step.index + 1} 时长", positive=True)
    if any(control in ("CI", "CP") for control, _, _ in plans):
        if out != "hold":
            raise ValueError("软件电流/功率闭环目前只支持定值模式，序列请使用 CV")
        for key in ("max_voltage", "step_voltage", "interval_ms", "tolerance_rel",
                    "timeout_s", "settle_timeout_s", "readback_tolerance_v"):
            number(cfg.feedback.get(key), f"闭环 {key}", positive=True)
        if cfg.feedback["interval_ms"] < 50 or cfg.feedback["tolerance_rel"] > 0.05:
            raise ValueError("闭环周期至少50ms，目标相对容差不能超过5%")
        if cfg.feedback["step_voltage"] > cfg.feedback["max_voltage"]:
            raise ValueError("闭环升压步长不能超过闭环电压上限")
        key = "max_current" if cfg.setting["control"] == "CI" else "max_power"
        if cfg.setting["value"] > float(cfg.limits[key]) * 0.95:
            raise ValueError("闭环目标需低于对应保护限值的95%，为调节保留余量")


# 仅匹配旧版本内置模板，保留用户自定义映射。
LEGACY_SCPI = {'func_dc': 'SOUR:FUNC DC', 'func_ac': 'SOUR:FUNC AC', 'func_acdc': 'SOUR:FUNC DCAC', 'volt': 'SOUR:VOLT {v}', 'volt_dc': 'SOUR:VOLT:DC {v}', 'volt_ac': 'SOUR:VOLT:AC {v}', 'curr': 'SOUR:CURR {v}', 'power': 'SOUR:POW {v}', 'freq': 'SOUR:FREQ {f}', 'ovp': 'SOUR:PROT:OVP {v}', 'ocp': 'SOUR:PROT:OCP {v}', 'opp': 'SOUR:PROT:OPP {v}', 'meas_volt': 'MEAS:VOLT?', 'meas_curr': 'MEAS:CURR?'}


def load_config(path: str) -> AppConfig:
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str
    if not parser.read(path, encoding="utf-8-sig"):
        raise FileNotFoundError(f"配置文件不存在: {path}")
    cfg = AppConfig()

    if "Connection" in parser:
        cfg.connection.update({k: v.strip() for k, v in parser["Connection"].items()})
    if str(cfg.connection.get("type", "")).lower() in ("usb", "usbtmc"):
        cfg.connection["type"] = "serial"
    cfg.connection.pop("usb_resource", None)
    if "SCPI" in parser:
        cfg.scpi.update({k: DEFAULT_SCPI[k] if LEGACY_SCPI.get(k) == v.strip() else v.strip()
                         for k, v in parser["SCPI"].items()})
    if "Limits" in parser:
        sec = parser["Limits"]
        cfg.limits = {"max_voltage": _f(sec.get("max_voltage", 0.0)),
                      "max_current": _f(sec.get("max_current", 0.0)),
                      "max_power": _f(sec.get("max_power", 0.0))}
    if "Setting" in parser:
        sec = parser["Setting"]
        mode = str(sec.get("mode", MODE_AC)).strip().upper()
        out = str(sec.get("output_mode", "hold")).strip().lower()
        if mode not in ("AC", "DC", "ACDC", "DCAC", "交直流"):
            raise ValueError(f"未知工作模式: {mode}")
        if out not in ("hold", "seq", "sequence", "序列", "序列模式"):
            raise ValueError(f"未知输出方式: {out}")
        cfg.setting = {
            "mode": MODE_ACDC if mode in ("ACDC", "DCAC", "交直流") else mode,
            "control": str(sec.get("control", CONTROL_CV)).strip().upper(),
            "value": _f(sec.get("value", 0.0)),
            "frequency": _f(sec.get("frequency", 0.0)),
            "output_mode": "seq" if out in ("seq", "sequence", "序列", "序列模式") else "hold",
        }
    if "Sequence" in parser:
        sec = parser["Sequence"]
        cfg.sequence = {"file": str(sec.get("file", "")).strip(),
                        "loop": str(sec.get("loop", "false")).strip().lower() in ("1", "true", "yes", "on")}
    if "Recording" in parser:
        sec = parser["Recording"]
        cfg.recording.update({
            "interval_ms": int(_f(sec.get("interval_ms", 1000), 1000)),
            "params": list(PARAMS),
            "enabled": str(sec.get("enabled", "true")).strip().lower() in ("1", "true", "yes", "on"),
            "log_file": str(sec.get("log_file", "data_log.csv")).strip(),
            "keypoint_file": str(sec.get("keypoint_file", "keypoint_log.csv")).strip(),
        })
    if "Keypoints" in parser:
        sec = parser["Keypoints"]
        cfg.keypoints = {}
        for key, value in sec.items():
            if key.strip().lower() == "tolerance":
                continue
            volt = _f(key, None)
            if volt is None or not math.isfinite(volt) or volt < 0:
                raise ValueError(f"无效关键电压: {key!r}")
            cfg.keypoints[volt] = list(PARAMS)
    if "Feedback" in parser:
        for key in cfg.feedback:
            if key in parser["Feedback"]:
                cfg.feedback[key] = _f(parser["Feedback"][key])
    base = os.path.dirname(os.path.abspath(path))
    for section, key in ((cfg.sequence, "file"), (cfg.recording, "log_file"),
                         (cfg.recording, "keypoint_file")):
        value = section.get(key)
        if value and not os.path.isabs(value):
            section[key] = os.path.normpath(os.path.join(base, value))
    return cfg


def save_config(path: str, cfg: AppConfig) -> None:
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str
    parser["Connection"] = {k: str(v) for k, v in cfg.connection.items()}
    parser["SCPI"] = {k: str(v) for k, v in cfg.scpi.items() if k in DEFAULT_SCPI or v}
    parser["Limits"] = {k: f"{_f(v):.6g}" for k, v in cfg.limits.items()}
    parser["Setting"] = {k: str(v) for k, v in cfg.setting.items()}
    parser["Feedback"] = {k: str(v) for k, v in cfg.feedback.items()}
    parser["Sequence"] = {"file": str(cfg.sequence.get("file", "")),
                          "loop": "true" if cfg.sequence.get("loop") else "false"}
    parser["Recording"] = {"enabled": "true" if cfg.recording.get("enabled") else "false",
                            "interval_ms": str(int(cfg.recording.get("interval_ms", 1000))),
                            "params": ",".join(PARAMS),
                            "log_file": str(cfg.recording.get("log_file", "data_log.csv")),
                            "keypoint_file": str(cfg.recording.get("keypoint_file", "keypoint_log.csv"))}
    parser["Keypoints"] = {}
    for volt in sorted(cfg.keypoints):
        parser["Keypoints"][f"{volt:g}"] = ",".join(PARAMS)
    target = os.path.abspath(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=os.path.dirname(target),
                                         prefix=".config_", suffix=".tmp", delete=False) as fh:
            temporary = fh.name
            fh.write(DEFAULT_COMMENTS)
            parser.write(fh)
        os.replace(temporary, target)
    finally:
        if temporary and os.path.exists(temporary):
            os.remove(temporary)


DEFAULT_COMMENTS = """\
; ITech 7722 驱动配置文件
; type: serial(RS232或USB虚拟串口,设备显示为COM口) /
;       tcp(网口) / visa(直接给VISA资源名)；本机 USB 线通过 serial/COM6 通信
; [SCPI] 段的值是实际下发指令，{v}=数值 {f}=频率。不同系列助记符可能不同，
; 请按随机 Programming Guide 校正；界面右下角 SCPI 调试台可逐条验证后再写回本文件。
; [Keypoints] 键=关键电压(V)，命中后统一记录电压、电流、功率、功率因子；按下发设定电压相等触发。
"""


# ---- sequence ----
"""时序（序列）文件解析：按时间顺序给出每一步的输出设定与记录要求。"""


import csv
import math
import os


CONTROL_ALIASES = {
    CONTROL_CV: CONTROL_CV, "V": CONTROL_CV, "VOLT": CONTROL_CV, "电压": CONTROL_CV,
    CONTROL_CI: CONTROL_CI, "I": CONTROL_CI, "CURR": CONTROL_CI, "电流": CONTROL_CI,
    CONTROL_CP: CONTROL_CP, "P": CONTROL_CP, "POW": CONTROL_CP, "功率": CONTROL_CP,
}

_COLUMN_MAP = {
    "time": "duration", "t": "duration", "duration": "duration", "时长": "duration",
    "时间": "duration", "秒": "duration",
    "control": "control", "mode": "control", "type": "control", "控制": "control",
    "设定类型": "control",
    "value": "value", "set": "value", "设定": "value", "设定值": "value",
    "frequency": "frequency", "freq": "frequency", "频率": "frequency",
    "record": "record", "记录": "record", "record_params": "record", "记录参数": "record",
    "desc": "desc", "说明": "desc", "备注": "desc",
    "volt": "volt", "电压v": "volt", "voltage": "volt",
    "curr": "curr", "电流a": "curr", "current": "curr",
    "power": "pow", "功率w": "pow",
}


class SequenceError(ValueError):
    pass


class Step:
    __slots__ = ("duration", "control", "value", "frequency", "record", "desc",
                 "volt", "curr", "pow", "index")

    def __init__(self, index, duration, control=CONTROL_CV, value=0.0, frequency=0.0,
                 record=None, desc="", volt=None, curr=None, pow=None):
        self.index = index
        self.duration = float(duration)
        self.control = control
        self.value = float(value)
        self.frequency = float(frequency)
        self.record = None  # 兼容旧调用；记录字段统一，不接受逐步筛选
        self.desc = ""  # 旧构造参数兼容，不再使用步骤备注
        self.volt = volt
        self.curr = curr
        self.pow = pow

    @property
    def explicit_settings(self) -> bool:
        return self.volt is not None or self.curr is not None or self.pow is not None

    def label(self) -> str:
        unit = {CONTROL_CV: "V", CONTROL_CI: "A", CONTROL_CP: "W"}.get(self.control, "")
        head = f"#{self.index + 1} {self.control}={self.value:g}{unit} f={self.frequency:g}Hz {self.duration:g}s"
        return head


def _split_row(row: list[str]) -> list[str]:
    """兼容分号结尾的 Excel 式导出与单列分隔写法。"""
    if len(row) == 1 and row[0]:
        return [c.strip() for c in row[0].split(",")]
    return [c.strip() for c in row]


def load_sequence(path: str) -> list[Step]:
    if not path or not os.path.isfile(path):
        raise SequenceError(f"时序文件不存在: {path or '(未指定)'}")

    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        raise SequenceError(f"无法读取时序文件: {exc}") from exc
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = raw.decode("gb18030")
        except UnicodeDecodeError:
            raise SequenceError("时序文件编码无法识别，请另存为 CSV UTF-8 格式") from None
    import io
    rows = [_split_row(r) for r in csv.reader(io.StringIO(text)) if any(c.strip() for c in r)]
    rows = [r for r in rows if not (r[0].startswith("#") or r[0].startswith(";"))]
    if not rows:
        raise SequenceError("时序文件为空")

    header = [h.strip().lower() for h in rows[0]]
    named = _COLUMN_MAP.get(header[0]) == "duration" or sum(h in _COLUMN_MAP for h in header) >= 2
    if named:
        fields = [_COLUMN_MAP.get(h, h) for h in header]
        body = rows[1:]
    else:
        fields = (["duration", "control", "value", "frequency", "record", "desc"]
                  if len(header) == 6 else ["duration", "control", "value", "frequency", "desc"])[:len(header)]
        body = rows

    steps: list[Step] = []
    for n, row in enumerate(body):
        if len(row) < len(fields):
            row = row + [""] * (len(fields) - len(row))
        rec = dict(zip(fields, row))
        duration = rec.get("duration") or "0"
        try:
            dur_val = float(duration)
        except ValueError:
            raise SequenceError(f"第 {n + 1} 行时长无法解析: {duration!r}") from None

        control_raw = (rec.get("control") or CONTROL_CV).strip().upper()
        if control_raw not in CONTROL_ALIASES:
            raise SequenceError(f"步骤 {n + 1}: 未知控制类型 {control_raw!r}")
        control = CONTROL_ALIASES[control_raw]

        def _num(key):
            v = (rec.get(key) or "").strip()
            if v in ("", "-"):
                return None
            try:
                result = float(v)
            except ValueError:
                raise SequenceError(f"步骤 {n + 1}: {key} 无法解析: {v!r}") from None
            if not math.isfinite(result) or result < 0:
                raise SequenceError(f"步骤 {n + 1}: {key} 必须为有限非负数")
            return result

        volt, curr, pow_ = _num("volt"), _num("curr"), _num("pow")
        if sum(v is not None for v in (volt, curr, pow_)) > 1:
            raise SequenceError(f"步骤 {n + 1}: voltage/current/power 只能指定一个控制量")
        if not math.isfinite(dur_val) or dur_val <= 0:
            raise SequenceError(f"步骤 {n + 1}: 时长必须为有限正数")
        value = _num("value")
        if volt is not None:
            control, value = CONTROL_CV, volt
        elif curr is not None:
            control, value = CONTROL_CI, curr
        elif pow_ is not None:
            control, value = CONTROL_CP, pow_
        elif value is None:
            raise SequenceError(f"第 {n + 1} 行缺少设定值")

        steps.append(Step(
            index=n, duration=dur_val, control=control, value=value,
            frequency=float(_num("frequency") or 0.0),
            volt=volt, curr=curr, pow=pow_))
    if not steps:
        raise SequenceError("时序文件中没有有效步骤")
    return steps


# ---- recorder ----
"""数据记录：常规采样日志 + 关键电压点定向记录 + 曲线缓存。

关键点命中采用边沿触发：同一关键电压在持续停留期间只记录一次，
离开该电压区间后再次进入才会重新记录，避免整段驻留被重复写入。
"""


import csv
import os
import time
from datetime import datetime


TIME_COLUMNS = ("time", "step")


class Sample:
    __slots__ = ("timestamp", "elapsed", "step", "voltage", "current", "power",
                  "frequency", "power_factor", "status", "desc")

    def __init__(self, timestamp, elapsed, step, voltage=None, current=None,
                 power=None, frequency=None, status="正常", desc="", power_factor=None):
        self.timestamp = timestamp
        self.elapsed = elapsed
        self.step = step
        self.voltage = voltage
        self.current = current
        self.power = power
        self.frequency = frequency
        self.power_factor = power_factor
        self.status = status
        self.desc = desc

    def value(self, param):
        return getattr(self, param, None)

    @classmethod
    def now(cls, elapsed, step, measurements, status="正常", desc=""):
        return cls(datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
                   elapsed, step,
                   voltage=measurements.get("voltage"), current=measurements.get("current"),
                   power=measurements.get("power"), frequency=measurements.get("frequency"),
                   status=status, desc=desc, power_factor=measurements.get("power_factor"))


def _fmt(value, digits):
    return "" if value is None else f"{value:.{digits}f}"


_DIGITS = {"voltage": 3, "current": 4, "power": 3, "power_factor": 4}


class Recorder:
    def __init__(self, config, work_dir="."):
        self.cfg = config.copy()
        self.work_dir = work_dir
        self.params = list(PARAMS)
        self.enabled = bool(config.recording.get("enabled", True))
        self.log_path = os.path.join(work_dir, config.recording.get("log_file", "data_log.csv"))
        self.key_path = os.path.join(work_dir, config.recording.get("keypoint_file", "keypoint_log.csv"))
        self.keypoints = {float(k): list(PARAMS) for k in (config.keypoints or {})}
        self._active_keys: set[float] = set()
        self.series: list[Sample] = []
        self.max_series = 20000
        self._started_at: datetime | None = None
        self._started_mono = None

    def reset(self, started_at: datetime | None = None):
        self._started_at = started_at or datetime.now()
        self._started_mono = time.monotonic()
        self._active_keys.clear()
        self.series.clear()

    @property
    def elapsed(self) -> float:
        if self._started_mono is None:
            return 0.0
        return time.monotonic() - self._started_mono

    def key_for_voltage(self, voltage):
        """按实际下发精度匹配设定电压，不使用回读电压或命中范围。"""
        if voltage is None:
            return None
        for kv in self.keypoints:
            if format_number(voltage) == format_number(kv):
                return (kv, self.keypoints[kv])
        return None

    def update_keypoints(self, rules):
        self.keypoints = {float(k): list(PARAMS) for k in rules}
        self._active_keys.intersection_update(self.keypoints)

    def next_rising_keypoint(self, current, proposed):
        """闭环升压时落在途经的关键设定值上，避免变步长直接跳过该设定。"""
        points = [float(format_number(k)) for k in self.keypoints
                  if current < float(format_number(k)) <= proposed]
        return min(points) if points else proposed

    def start(self, meta: dict | None = None):
        if not self.enabled:
            return
        header = list(TIME_COLUMNS) + self.params + ["mode", "setting"]
        if os.path.normcase(os.path.abspath(self.log_path)) == os.path.normcase(os.path.abspath(self.key_path)):
            raise ValueError("常规记录和关键点记录不能使用同一个文件")
        for path in (self.log_path, self.key_path):
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        if os.path.exists(self.log_path) and os.path.getsize(self.log_path):
            with open(self.log_path, encoding="utf-8-sig", newline="") as fh:
                previous = next((r for r in csv.reader(fh) if r and not r[0].startswith("#")), None)
            if previous != header:
                self._backup_old_schema(self.log_path)
        write_header = not os.path.exists(self.log_path) or os.path.getsize(self.log_path) == 0
        with open(self.log_path, "a", newline="", encoding="utf-8-sig") as fh:
            writer = csv.writer(fh)
            if write_header:
                if meta:
                    writer.writerow(["# " + "; ".join(f"{k}={v}" for k, v in meta.items())])
                writer.writerow(header)
        key_header = ["关键点设定电压(V)", "time"] + list(PARAMS)
        if os.path.exists(self.key_path) and os.path.getsize(self.key_path):
            with open(self.key_path, encoding="utf-8-sig", newline="") as fh:
                if next(csv.reader(fh), None) != key_header:
                    self._backup_old_schema(self.key_path)
        if not os.path.exists(self.key_path) or os.path.getsize(self.key_path) == 0:
            with open(self.key_path, "w", newline="", encoding="utf-8-sig") as fh:
                csv.writer(fh).writerow(key_header)

    @staticmethod
    def _backup_old_schema(path):
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        backup = f"{path}.backup_{stamp}"
        # 使用独占创建保留原始字节；备份成功后才清空原路径，不覆盖历史文件。
        with open(path, "rb") as source, open(backup, "xb") as target:
            target.write(source.read())
        with open(path, "w", encoding="utf-8-sig"):
            pass

    def log(self, sample: Sample, mode="", setting="", params=None,
            set_voltage=None, write_regular=True) -> bool:
        """写入常规记录，并处理关键点命中。返回是否触发了关键点记录。

        统一记录全部四项。params 参数仅保留旧调用兼容性，不再筛选或跳过记录。
        """
        self.series.append(sample)
        if len(self.series) > self.max_series:
            del self.series[:len(self.series) - self.max_series]
        if not self.enabled:
            return False

        # 每次都更新驻留集合，包括从一个关键点直接跳到另一个关键点的情况。
        hit = self.key_for_voltage(set_voltage) if set_voltage is not None else None
        triggered = bool(hit and hit[0] not in self._active_keys)
        self._active_keys = {hit[0]} if hit else set()

        if write_regular:
            with open(self.log_path, "a", newline="", encoding="utf-8-sig") as fh:
                row = [sample.timestamp, sample.step]
                row += [_fmt(sample.value(p), _DIGITS[p]) for p in self.params]
                row += [mode, setting]
                csv.writer(fh).writerow(row)

        if triggered:
            kv, kparams = hit
            with open(self.key_path, "a", newline="", encoding="utf-8-sig") as fh:
                row = [f"{kv:g}", sample.timestamp]
                row += [_fmt(sample.value(p), _DIGITS[p]) if p in kparams else "" for p in PARAMS]
                csv.writer(fh).writerow(row)
        return triggered

    def files(self):
        return [p for p in (self.log_path, self.key_path) if os.path.exists(p)]


# ---- feedback ----
"""软件电流/功率闭环：根据真实回读提出有界的电压调整，不直接访问硬件。"""
import math



class FeedbackError(DeviceError):
    """控制无法继续，区别于串口或 SCPI 通信故障。"""


class VoltageFeedback:
    def __init__(self, control, target, limits, options):
        self.control = control
        self.target = float(target)
        self.limits = limits
        self.options = options
        self.voltage = 0.0
        self.ceiling = min(float(options["max_voltage"]), float(limits["max_voltage"]) * 0.95)
        self.step = float(options["step_voltage"])
        self.tolerance = min(self.target * 0.1,
                             max(self.target * float(options["tolerance_rel"]),
                                 0.002 if control == "CI" else 0.02))
        # 容差只作用于目标上方；将调节中心放在上半区，避免渐近逼近时停在目标以下。
        self.aim = self.target + self.tolerance * 0.5
        # 达标后允许小幅上侧波动，避免2W附近2.01/2.04W反复触发降压再升压。
        self.hold_upper = self.target + max(self.tolerance, self.target * 0.05)
        self._holding = False
        self._over_since = None
        self._unsettled_since = None
        self._outside_since = None
        self._limited_since = None

    def update(self, measurements, now):
        v, i, p = (measurements[k] for k in ("voltage", "current", "power"))
        if not all(x is not None and math.isfinite(x) for x in (v, i, p)):
            raise FeedbackError("闭环回读无效，停止调压")
        if i < -0.002 or p < -0.02:
            raise FeedbackError("闭环检测到反向电流或功率，当前控制不支持回馈负载")
        v, i, p = abs(v), abs(i), max(0, p)
        actual = i if self.control == "CI" else p
        # 真实仪器在0V附近也会回读毫安级偏置。此时V/I、V*sqrt(P目标/P)
        # 没有估算意义，不能将保护预测或下一设定钳在0V。
        readback_tolerance = float(self.options.get("readback_tolerance_v", 1.0))
        model_ready = v >= 0.05 and self.voltage >= readback_tolerance
        if self.target == 0:
            self.voltage = 0.0
            return 0.0, "零目标，保持零电压"

        # 即使目标已达到，也不允许贴近另一项保护上限持续运行。
        safety = self.ceiling
        if model_ready and i > 1e-9:
            safety = min(safety, v * float(self.limits["max_current"]) * 0.95 / i)
        if model_ready and p > 1e-9:
            safety = min(safety, v * math.sqrt(float(self.limits["max_power"]) * 0.95 / p))
        if self.voltage > safety + 1e-6:
            self._holding = False
            self.voltage = max(0, safety)
            return self.voltage, "接近保护边界，降压"

        if self.target <= actual <= self.hold_upper:
            self._outside_since = self._limited_since = None
            self._holding = True
            self._over_since = None
            return self.voltage, "目标已达到，保持电压"
        if self._holding and self.hold_upper < actual <= self.target * 1.2:
            if self._over_since is None:
                self._over_since = now
            if now - self._over_since < 1.0:
                return self.voltage, "等待偏差确认，保持电压"
        else:
            self._over_since = None
        self._holding = False
        if self._outside_since is None:
            self._outside_since = now
        if now - self._outside_since >= float(self.options["timeout_s"]):
            raise FeedbackError("闭环调节超时，未达到目标；请检查负载和电压上限")

        if actual > self.hold_upper:
            ratio = self.aim / actual
            desired = self.voltage * (ratio if self.control == "CI" else math.sqrt(ratio))
            # 降压不受升压步长约束，优先消除超调。
            self.voltage = max(0, min(self.voltage, desired, safety))
            self._unsettled_since = None
            return self.voltage, "超过目标，降压修正"

        # 前一次设定尚未反映在回读时，不叠加下一次升压，避免通信/负载延迟导致连续冲高。
        if abs(v - self.voltage) > max(readback_tolerance, self.voltage * 0.03):
            if self._unsettled_since is None:
                self._unsettled_since = now
            if now - self._unsettled_since >= float(self.options["settle_timeout_s"]):
                raise FeedbackError(f"电压回读持续偏离：设定{self.voltage:g}V、回读{v:g}V，"
                                    f"允许偏差{max(readback_tolerance, self.voltage * 0.03):g}V；"
                                    "请检查输出状态、限流及接线")
            return self.voltage, "等待电压稳定"
        self._unsettled_since = None

        if not model_ready or actual <= 1e-9:
            desired = self.voltage + min(self.step, 0.1)
        else:
            ratio = self.aim / actual
            predicted = self.voltage * (ratio if self.control == "CI" else math.sqrt(ratio))
            # 最小正向探测步避免仪器设定分辨率或浮点舍入使输出卡在目标以下。
            increment = max(min(self.step, 0.01), max(0, predicted - self.voltage) * 0.5)
            desired = self.voltage + increment
        candidate = min(self.voltage + self.step, desired, safety)
        # SCPI 仅发送六位小数，与内部状态保持同一精度。
        candidate = max(0, round(candidate, 6))
        if candidate > safety:
            candidate = max(0, math.floor(safety * 1e6) / 1e6)
        if candidate <= self.voltage + 1e-6:
            if self._limited_since is None:
                self._limited_since = now
            if now - self._limited_since >= float(self.options["settle_timeout_s"]):
                raise FeedbackError(f"已到调节边界：电压设定{self.voltage:g}V，"
                                    f"有效上限{safety:g}V，回读{actual:g}，目标{self.target:g}；无法达到目标")
            return self.voltage, "已到调节边界"
        self._limited_since = None
        self.voltage = candidate
        return self.voltage, "逐步升压"


# ---- engine ----
"""运行引擎：独立于 Tk 和绘图库，统一控制输出、采样和异常收尾。"""

import math
import queue
import threading
import time


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



class ITECHEngine(Engine):
    """无需界面的运行引擎。events 队列保存 (事件名, 数据)，由调用线程消费。

    可选 on_event / on_scpi 回调应线程安全且不得抛出异常。
    """

    def __init__(self, on_event=None, on_scpi=None):
        self.events = queue.SimpleQueue()
        self._on_event = on_event
        self._on_scpi = on_scpi
        super().__init__(self)

    def post(self, kind, value):
        self.events.put((kind, value))
        if self._on_event is not None:
            self._on_event(kind, value)

    def log_scpi(self, direction, text):
        if self._on_scpi is not None:
            self._on_scpi(direction, text)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.disconnect()
