"""2700设备操作与测量参数；串口收发由通信层提供。"""
from __future__ import annotations
import math
import time
from dataclasses import dataclass
from instruments.serial_transport import LineProtocol


MODES = {
    "直流电压 DCV": ("VOLT:DC", "V"),
    "交流电压 ACV": ("VOLT:AC", "V"),
    "直流电流 DCI": ("CURR:DC", "A"),
    "交流电流 ACI": ("CURR:AC", "A"),
    "两线电阻 Ω2": ("RES", "Ohm"),
    "四线电阻 Ω4": ("FRES", "Ohm"),
}


def valid_channels(mode, model="7700"):
    function = MODES.get(mode, ("", ""))[0]
    if model not in ("7700", "7702"):
        raise ValueError("模块型号必须为 7700 或 7702")
    count = 40 if model == "7702" else 20
    local = range(count + 1, count + 3) if function.startswith("CURR") else range(1, count // 2 + 1) if function == "FRES" else range(1, count + 1)
    return tuple(slot * 100 + ch for slot in (1, 2) for ch in local)


@dataclass(frozen=True)
class ChannelConfig:
    channel: int
    mode: str = "直流电压 DCV"
    measure_range: str = "AUTO"
    nplc: float = 1.0
    settle: float = 0.5

    def validate(self, model):
        if self.mode not in MODES:
            raise ValueError(f"通道 {self.channel} 测量功能无效")
        if type(self.channel) is not int or self.channel not in valid_channels(self.mode, model):
            raise ValueError(f"通道 {self.channel} 不支持 {model} 的 {self.mode}")
        if not math.isfinite(self.nplc) or not 0.01 <= self.nplc <= 10:
            raise ValueError(f"通道 {self.channel} NPLC 必须在 0.01 到 10 之间")
        if not math.isfinite(self.settle) or not 0 <= self.settle <= 60:
            raise ValueError(f"通道 {self.channel} 稳定时间必须在 0 到 60 秒之间")
        if self.measure_range.upper() != "AUTO":
            value = float(self.measure_range)
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"通道 {self.channel} 量程必须为 AUTO 或正数")


@dataclass(frozen=True)
class Settings:
    port: str = "COM1"
    baud: int = 9600
    terminator: str = "CR"
    flow: bool = False
    timeout: float = 10.0
    mode: str = "直流电压 DCV"
    interval: float = 1.0
    nplc: float = 1.0
    measure_range: str = "AUTO"
    simulation: bool = False

    input_source: str = "FRONT"
    channels: tuple[int, ...] = (101, 102)
    settle: float = 0.5
    channel_configs: tuple[ChannelConfig, ...] = ()

    def config_for(self, channel):
        for config in self.channel_configs:
            if config.channel == channel:
                return config
        return ChannelConfig(channel, self.mode, self.measure_range, self.nplc, self.settle)

    @property
    def active_channels(self):
        return tuple(sorted(self.channels)) if self.input_source in ("7700", "7702") else (0,)

    def validate(self):
        if self.input_source not in ("FRONT", "7700", "7702"):
            raise ValueError("输入来源必须为 FRONT、7700 或 7702")
        if not math.isfinite(self.settle) or not 0 <= self.settle <= 60:
            raise ValueError("切换稳定时间必须在 0 到 60 秒之间")
        if self.input_source in ("7700", "7702"):
            if not self.channels or len(set(self.channels)) != len(self.channels):
                raise ValueError("请至少选择一个通道，且通道不得重复")
            configured = [config.channel for config in self.channel_configs]
            if self.channel_configs and (len(configured) != len(set(configured)) or set(configured) != set(self.channels)):
                raise ValueError("逐通道配置必须与已启用通道一一对应，不得缺失或重复")
            for channel in self.channels:
                self.config_for(channel).validate(self.input_source)
            paired_offset = 20 if self.input_source == "7702" else 10
            for channel in self.channels:
                if self.config_for(channel).mode == "四线电阻 Ω4" and channel + paired_offset in self.channels:
                    raise ValueError(f"四线通道 {channel} 占用感测通道 {channel + paired_offset}，不能同时将感测通道用于其他测量")
        elif self.channel_configs:
            raise ValueError("前面板采集不使用模块逐通道配置")
        if self.mode not in MODES:
            raise ValueError("测量模式无效")
        if not self.port.strip() and not self.simulation:
            raise ValueError("请选择或输入串口")
        if self.baud not in (300, 600, 1200, 2400, 4800, 9600, 19200):
            raise ValueError("波特率必须为仪器支持的值")
        if self.terminator not in ("LF", "CR", "CRLF"):
            raise ValueError("结束符无效")
        if not math.isfinite(self.interval) or self.interval < 0.05:
            raise ValueError("采样间隔至少为 0.05 秒")
        if not math.isfinite(self.timeout) or self.timeout < 1:
            raise ValueError("通信超时至少为 1 秒")
        if not math.isfinite(self.nplc) or not 0.01 <= self.nplc <= 10:
            raise ValueError("NPLC 必须在 0.01 到 10 之间")
        if self.measure_range.upper() != "AUTO":
            value = float(self.measure_range)
            if not math.isfinite(value) or value <= 0:
                raise ValueError("量程必须为 AUTO 或正数（V / A / Ω 基本单位）")


def parse_reading(raw):
    try:
        value = float(raw.strip())
    except ValueError as exc:
        raise ValueError(f"无法解析仪器读数：{raw!r}") from exc
    if not math.isfinite(value) or abs(value) >= 9e37:
        return None, "OVERLOAD"
    return value, "OK"


def parse_channel_reading(raw, expected):
    parts = raw.strip().split(",")
    if len(parts) != 2:
        raise ValueError(f"模块响应应包含读数和通道号：{raw!r}")
    try:
        actual = int(parts[1].strip())
    except ValueError as exc:
        raise ValueError(f"模块通道号格式异常：{raw!r}") from exc
    if actual != expected:
        raise ValueError(f"通道不匹配：期望 {expected}，仪器返回 {actual}；停止以避免数据归属错误")
    return parse_reading(parts[0])


class Instrument(LineProtocol):
    def __init__(self, settings, stop, serial_factory=None, trace=None):
        self.settings, self.stop = settings, stop
        self.serial_factory = serial_factory
        self.serial = None
        self.rx = bytearray()
        self.pending_query = False
        self.startup_errors = []
        self.module_options = ()
        self.applied_parameters = None
        self.trace = trace or (lambda direction, data: None)

    def read_errors(self):
        errors = []
        for _ in range(32):
            response = self.query("SYST:ERR?")
            code, separator, _ = response.partition(",")
            if not separator:
                raise RuntimeError(f"仪器错误队列响应格式异常：{response!r}")
            try:
                number = int(code)
            except ValueError as exc:
                raise RuntimeError(f"仪器错误码格式异常：{response!r}") from exc
            if number == 0:
                return errors
            errors.append(response)
        raise RuntimeError("仪器错误队列持续产生错误，无法建立通信基线")

    def checked_write(self, command):
        self.write(command)
        if self.query("*OPC?").strip() != "1":
            raise RuntimeError(f"仪器未确认指令完成：{command}")
        errors = self.read_errors()
        if errors:
            raise RuntimeError(f"执行 {command} 后仪器报告新错误：" + "; ".join(errors))

    def apply_parameters(self, config):
        key = (config.mode, config.measure_range.upper(), config.nplc)
        if key == self.applied_parameters:
            return
        function, _ = MODES[config.mode]
        commands = [f"SENS:FUNC '{function}'",
                    f"SENS:{function}:RANG:AUTO {'ON' if config.measure_range.upper() == 'AUTO' else 'OFF'}"]
        if config.measure_range.upper() != "AUTO":
            commands.append(f"SENS:{function}:RANG {float(config.measure_range):.12g}")
        if function.endswith(":AC"):
            commands.append(f"SENS:{function}:DET:BAND 300")
        commands.append(f"SENS:{function}:NPLC {config.nplc:g}")
        for command in commands:
            self.checked_write(command)
        self.applied_parameters = key

    def close(self):
        if self.serial is not None:
            try:
                # A completed connection test needs no transmission break.
                # Use the documented Ctrl-C only when a query was interrupted.
                if self.pending_query:
                    self.trace("TX", repr(b"\x03"))
                    self.serial.write(b"\x03")
                    self.serial.flush()
                    time.sleep(0.1)
                self.write("ABOR")
                if self.settings.input_source in ("7700", "7702"):
                    self.write("ROUT:SCAN:LSEL NONE")
                    self.write("ROUT:OPEN:ALL")
                self.write("SYST:LOC")
                self.serial.flush()
            finally:
                self.serial.close()

