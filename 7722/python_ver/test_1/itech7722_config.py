"""config.ini 的读写：连接参数、SCPI 指令映射、保护限值、设定值、记录规则。"""

from __future__ import annotations

import configparser
import copy
import math
import os
import tempfile
from dataclasses import dataclass, field

from itech7722_device import (CONTROL_CV, DEFAULT_SCPI, MODE_AC, MODE_ACDC, MODE_DC)

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
