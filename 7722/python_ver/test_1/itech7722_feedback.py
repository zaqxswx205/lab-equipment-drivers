"""软件电流/功率闭环：根据真实回读提出有界的电压调整，不直接访问硬件。"""
import math

from itech7722_device import DeviceError


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
