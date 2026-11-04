"""时序（序列）文件解析：按时间顺序给出每一步的输出设定与记录要求。"""

from __future__ import annotations

import csv
import math
import os

from itech7722_device import CONTROL_CV, CONTROL_CI, CONTROL_CP

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
