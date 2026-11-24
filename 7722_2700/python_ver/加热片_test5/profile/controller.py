"""CV direct drive; power targets are reached by software voltage feedback."""
from __future__ import annotations

import math


class DriveController:
    """CV直接执行；功率目标通过测量反馈调压，不冒充电源硬件CP。"""
    def __init__(self, cfg):
        self.cfg = cfg
        self.voltage = 0.
        self.target = None
        self.outside_since = None

    def update(self, step, measured_power, dt, now):
        cfg = self.cfg
        target = (step.mode, step.value)
        if target != self.target:
            self.target = target
            self.outside_since = None
        if step.value == 0:
            self.voltage = 0.
            self.outside_since = None
            return 0.
        if step.mode == 'voltage':
            self.voltage = step.value
            return self.voltage
        if not math.isfinite(measured_power) or measured_power < 0:
            raise RuntimeError('功率反馈读数无效')
        tolerance = max(.02, step.value*cfg.power_tolerance_rel)
        if abs(measured_power-step.value) <= tolerance:
            self.outside_since = None
            return self.voltage
        if self.outside_since is None:
            self.outside_since = now
        if now-self.outside_since >= cfg.power_timeout:
            raise RuntimeError('功率目标持续未达到：请检查负载、最大电压/电流及驱动方案')
        step_v = cfg.power_slew_v_s*max(0., dt)
        if measured_power < step.value:
            desired = self.voltage*math.sqrt(step.value/measured_power) if measured_power > 1e-6 else self.voltage+step_v
            self.voltage = min(cfg.max_voltage, self.voltage+step_v, desired)
        else:
            # 降压不限升压速率，优先减小超调；该估计不保证制冷机瞬态响应。
            self.voltage = max(0., min(cfg.max_voltage, self.voltage*math.sqrt(step.value/measured_power)))
        return self.voltage
