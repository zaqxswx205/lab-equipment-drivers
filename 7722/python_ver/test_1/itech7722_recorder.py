"""数据记录：常规采样日志 + 关键电压点定向记录 + 曲线缓存。

关键点命中采用边沿触发：同一关键电压在持续停留期间只记录一次，
离开该电压区间后再次进入才会重新记录，避免整段驻留被重复写入。
"""

from __future__ import annotations

import csv
import os
import time
from datetime import datetime

from itech7722_config import PARAMS
from itech7722_device import format_number

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
