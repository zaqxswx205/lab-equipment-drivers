"""Keithley 2700 serial acquisition and durable, streaming text files."""
from __future__ import annotations

import csv
import math
import os
import random
import threading
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

MODES = {
    "直流电压 DCV": ("VOLT:DC", "V"),
    "交流电压 ACV": ("VOLT:AC", "V"),
    "直流电流 DCI": ("CURR:DC", "A"),
    "交流电流 ACI": ("CURR:AC", "A"),
    "两线电阻 Ω2": ("RES", "Ohm"),
    "四线电阻 Ω4": ("FRES", "Ohm"),
}
OLD_FIELDS = ["timestamp", "mode", "unit", "value", "status", "raw", "source"]
PREVIOUS_FIELDS = ["timestamp", "channel", "mode", "unit", "value", "status", "raw", "source"]
FULL_FIELDS = PREVIOUS_FIELDS + ["range_setting", "nplc_setting", "settle_s"]
FIELDS = ["timestamp", "channel", "mode", "unit", "value", "status"]
LEGACY_FIELDS = ["timestamp", "elapsed_s", "mode", "unit", "value", "status", "raw", "source"]


def valid_channels(mode, model="7700"):
    function = MODES.get(mode, ("", ""))[0]
    if model not in ("7700", "7702"):
        raise ValueError("模块型号必须为 7700 或 7702")
    count = 40 if model == "7702" else 20
    local = range(count + 1, count + 3) if function.startswith("CURR") else range(1, count // 2 + 1) if function == "FRES" else range(1, count + 1)
    return tuple(slot * 100 + ch for slot in (1, 2) for ch in local)


class Cancelled(Exception):
    pass


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


class Instrument:
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

    def open(self):
        if self.serial_factory is None:
            import serial
            self.serial_factory = serial.Serial
        s = self.settings
        self.serial = self.serial_factory(port=s.port, baudrate=s.baud, bytesize=8,
                                          parity="N", stopbits=1, timeout=0.1,
                                          write_timeout=1, xonxoff=s.flow,
                                          rtscts=False, dsrdtr=False)
        self.serial.reset_input_buffer()
        self.write("SYST:REM")
        identity = self.query("*IDN?")
        parts = [part.strip().upper() for part in identity.split(",")]
        if len(parts) < 2 or parts[1] not in ("2700", "MODEL 2700"):
            raise RuntimeError(f"连接的设备不是 Keithley 2700：{identity}")
        # Error queries pop the oldest entry, which may predate this session.
        # Establish an empty baseline before attributing errors to new commands.
        self.startup_errors = self.read_errors()
        for error in self.startup_errors:
            self.trace("EXISTING_ERROR", error)
        self.check_front()
        if s.input_source in ("7700", "7702"):
            options = [item.strip().strip('"').upper() for item in self.query("*OPT?").split(",")]
            self.module_options = tuple(options[:2])
            detected = [slot for slot, model in enumerate(options[:2], 1) if model == s.input_source]
            summary = "；".join(f"槽位{slot}={options[slot - 1] if len(options) >= slot else '未知'}" for slot in (1, 2))
            if not detected:
                raise RuntimeError(f"主机未识别到所选型号 {s.input_source}：{summary}。\n"
                                   "请先确认软件输入来源与实际模块型号一致；在仪器面板按 SHIFT → CARD → CONFIG 查看两个槽位。"
                                   "若均为 NONE，请关机并断开电源及测试信号，检查模块是否插到位，重新开机后再测试连接。")
            for slot in sorted({ch // 100 for ch in s.channels}):
                if slot not in detected:
                    available = "、".join(str(item) for item in detected)
                    raise RuntimeError(f"所选槽位 {slot} 未检测到 {s.input_source}：{summary}。\n"
                                       f"{s.input_source} 实际位于槽位 {available}；请清空空槽位的勾选，并选择对应槽位的通道（槽位1为1xx，槽位2为2xx）。")
        errors = self.read_errors()
        if errors:
            raise RuntimeError("输入与模块检查期间产生新错误：" + "; ".join(errors))
        return identity

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

    def write(self, command):
        # RS-232 input requires CR (manual 10-19). TX TERM only controls
        # instrument replies; it must not select our outgoing terminator.
        if not command or any(not 32 <= ord(char) <= 126 for char in command):
            raise ValueError("SCPI 命令必须为单行可打印 ASCII 字符")
        payload = (command + "\r").encode("ascii")
        self.trace("TX", repr(payload))
        self.serial.write(payload)

    def query(self, command):
        if self.stop.is_set():
            raise Cancelled()
        self.write(command)
        self.pending_query = True
        deadline = time.monotonic() + self.settings.timeout
        while time.monotonic() < deadline:
            if self.stop.is_set():
                raise Cancelled()
            # Accept CR, LF and CRLF, including terminators split over reads.
            for i, byte in enumerate(self.rx):
                if byte in (10, 13):
                    line = bytes(self.rx[:i]).strip()
                    del self.rx[:i + 1]
                    if line:
                        response = line.decode("ascii", errors="strict")
                        self.pending_query = False
                        self.trace("RX", repr(response))
                        return response
                    break
            else:
                chunk = self.serial.read(1)
                self.rx.extend(chunk)
                if len(self.rx) > 8192:
                    raise RuntimeError("串口响应过长，请检查串口设置")
        raise TimeoutError(f"{command} 响应超时；检查波特率、结束符、FLOW 和接线")

    def check_front(self):
        expected = 0 if self.settings.input_source in ("7700", "7702") else 1
        if float(self.query("SYST:FRSW?")) != expected:
            position = f"R（后面板/{self.settings.input_source}）" if expected == 0 else "F（前面板）"
            raise RuntimeError(f"请将仪器 FRONT/REAR 实体开关切到 {position} 后重新开始")

    def checked_write(self, command):
        self.write(command)
        if self.query("*OPC?").strip() != "1":
            raise RuntimeError(f"仪器未确认指令完成：{command}")
        errors = self.read_errors()
        if errors:
            raise RuntimeError(f"执行 {command} 后仪器报告新错误：" + "; ".join(errors))

    def measure(self, channel):
        self.check_front()
        if self.settings.input_source in ("7700", "7702"):
            config = self.settings.config_for(channel)
            if self.settings.channel_configs:
                # Disconnect every input before changing function, especially
                # when moving from resistance excitation to a voltage channel.
                self.checked_write("ROUT:OPEN:ALL")
                self.apply_parameters(config)
            # System CLOSE handles 4-wire pairs / backplane relays.
            self.checked_write(f"ROUT:CLOS (@{channel})")
            if self.stop.wait(config.settle):
                raise Cancelled()
        raw = self.query("READ?")
        if self.settings.input_source in ("7700", "7702"):
            value, status = parse_channel_reading(raw, channel)
        else:
            value, status = parse_reading(raw)
        errors = self.read_errors()
        if errors:
            raise RuntimeError(f"通道 {channel or 'FRONT'} 测量失败：" + "; ".join(errors))
        return raw, value, status

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

    def configure(self):
        s = self.settings
        function, _ = MODES[s.mode]
        if s.channel_configs:
            commands = ["ABOR", "*RST", "*CLS", "INIT:CONT OFF",
                        "ROUT:SCAN:LSEL NONE", "ROUT:CLOS:ACON OFF", "ROUT:OPEN:ALL",
                        "TRIG:SOUR IMM", "TRIG:COUN 1", "SAMP:COUN 1",
                        "FORM:DATA ASC", "FORM:ELEM READ,CHAN"]
            self.applied_parameters = None
            for command in commands:
                self.checked_write(command)
            return
        commands = ["ABOR", "*RST", "*CLS", "INIT:CONT OFF",
                    f"CONF:{function}", f"SENS:{function}:RANG:AUTO {'ON' if s.measure_range.upper() == 'AUTO' else 'OFF'}"]
        if s.measure_range.upper() != "AUTO":
            commands.append(f"SENS:{function}:RANG {float(s.measure_range):.12g}")
        # AC integration rate commands require 300 Hz bandwidth (manual 4-12).
        if function.endswith(":AC"):
            commands.append(f"SENS:{function}:DET:BAND 300")
        commands += [f"SENS:{function}:NPLC {s.nplc:g}",
                     "TRIG:SOUR IMM", "TRIG:COUN 1", "SAMP:COUN 1",
                     "FORM:DATA ASC", "FORM:ELEM READ"]
        if s.input_source in ("7700", "7702"):
            commands += ["ROUT:SCAN:LSEL NONE", "ROUT:CLOS:ACON OFF",
                         "ROUT:OPEN:ALL", "FORM:ELEM READ,CHAN"]
        for command in commands:
            self.checked_write(command)

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


class TextLog:
    def __init__(self, path):
        # Exclusive creation prevents accidental loss of an earlier experiment.
        self.file = open(path, "x", encoding="utf-8", newline="")
        try:
            self.writer = csv.DictWriter(self.file, FIELDS, delimiter="\t")
            self.writer.writeheader()
            self.sync()
        except BaseException:
            self.file.close()
            raise

    def sync(self):
        self.file.flush()
        os.fsync(self.file.fileno())

    def append(self, row):
        self.writer.writerow(row)
        self.sync()

    def close(self):
        self.file.close()


def convert_csv(source, destination):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if source == destination:
        raise ValueError("输出文件不能与输入文件相同")
    # Stream the complete file, independent of the limited display buffer.
    created = False
    try:
        with source.open(encoding="utf-8-sig", newline="") as src:
            reader = csv.DictReader(src, delimiter="\t")
            if reader.fieldnames not in (FIELDS, FULL_FIELDS, PREVIOUS_FIELDS, OLD_FIELDS, LEGACY_FIELDS):
                raise ValueError("请选择本程序生成的 TXT 数据文件")
            with destination.open("x", encoding="utf-8-sig", newline="") as dst:
                created = True
                writer = csv.DictWriter(dst, FIELDS)
                writer.writeheader()
                count = 0
                for row in reader:
                    if None in row or any(value is None for value in row.values()):
                        raise ValueError(f"TXT 第 {count + 2} 行不完整，请检查文件")
                    writer.writerow({field: row.get(field, "FRONT" if field == "channel" else "") for field in FIELDS})
                    count += 1
                dst.flush()
                os.fsync(dst.fileno())
                return count
    except FileExistsError:
        raise ValueError("CSV 已存在，请选择新的文件名") from None
    except BaseException:
        if created:
            destination.unlink(missing_ok=True)
        raise


def collect(settings, path, stop, emit, probe=False, serial_factory=None):
    instrument, log = None, None
    trace_file, trace_path = None, None
    try:
        settings.validate()
        if settings.simulation:
            emit("identity", "模拟设备（无真实测量）")
        else:
            trace_dir = Path(__file__).resolve().parent / "logs"
            if serial_factory is not None:
                trace_dir = trace_dir / "test_sessions"
            trace_dir.mkdir(parents=True, exist_ok=True)
            trace_path = trace_dir / f"serial_{datetime.now():%Y%m%d_%H%M%S_%f}.log"
            trace_file = trace_path.open("x", encoding="utf-8")
            def trace(direction, data):
                trace_file.write(f"{datetime.now().astimezone().isoformat(timespec='milliseconds')} {direction} {data}\n")
                trace_file.flush()
            trace("SETTINGS", f"port={settings.port} baud={settings.baud} 8N1 xonxoff={settings.flow} TX=CR RX=auto input={settings.input_source} channels={settings.active_channels} configs={settings.channel_configs!r}")
            instrument = Instrument(settings, stop, serial_factory, trace)
            try:
                identity = instrument.open()
            finally:
                if instrument.module_options:
                    emit("modules", "；".join(f"槽位{slot}={model}" for slot, model in enumerate(instrument.module_options, 1)))
            emit("identity", identity)
            if instrument.startup_errors:
                counts = Counter(error.split(",", 1)[0].strip() for error in instrument.startup_errors)
                summary = "、".join(f"{code} × {count}" for code, count in counts.items())
                emit("warning", f"历史错误队列已读取：{summary}。本次连接检查无新增错误；详情见通信日志。")
        if probe:
            emit("info", "连接成功，输入开关及所选模块检查通过" if instrument else "模拟模式测试通过")
            return
        if instrument:
            instrument.configure()
        log = TextLog(path)
        emit("file", str(path))
        start = time.monotonic()
        index = 0
        while not stop.is_set():
            tick = time.monotonic()
            for channel in settings.active_channels:
                if stop.is_set():
                    break
                config = settings.config_for(channel)
                if instrument:
                    raw, value, status = instrument.measure(channel)
                else:
                    base = {"V": 1.0, "A": 0.01, "Ohm": 1000.0}[MODES[config.mode][1]]
                    raw = f"{base * (1 + (channel % 100) * 0.02 + 0.04 * math.sin((time.monotonic() - start) / 3 + channel) + random.gauss(0, 0.002)):.10e}"
                    value, status = parse_reading(raw)
                elapsed = time.monotonic() - start
                row = dict(timestamp=datetime.now().isoformat(timespec="milliseconds"),
                           channel=str(channel) if channel else "FRONT", mode=config.mode,
                           unit=MODES[config.mode][1], value="" if value is None else f"{value:.12g}",
                           status=status)
                log.append(row)
                index += 1
                emit("sample", (index, row, elapsed))
            # A slow measurement extends the interval; never queue catch-up reads.
            stop.wait(max(0, settings.interval - (time.monotonic() - tick)))
    except Cancelled:
        pass
    except Exception as exc:
        emit("error", str(exc) + (f"\n通信日志：{trace_path}" if trace_path else ""))
    finally:
        try:
            if log:
                log.close()
        finally:
            try:
                if instrument:
                    instrument.close()
            except Exception as exc:
                emit("error", f"关闭串口失败：{exc}")
            finally:
                if trace_file:
                    trace_file.close()
                emit("done", None)
