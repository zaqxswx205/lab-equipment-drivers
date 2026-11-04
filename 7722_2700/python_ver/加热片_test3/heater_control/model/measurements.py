"""Power/heater measurement validation helpers."""
from __future__ import annotations

import math


def check_measurements(measurements, cfg, mode=None):
    """DC电流按幅值检查上限，保留原始符号；AC有效值须非负。"""
    mode = mode or cfg.mode
    if mode not in ('AC', 'DC'):
        raise ValueError('测量校验模式须为AC或DC')
    for key, limit in [('voltage', cfg.max_voltage), ('current', cfg.max_current), ('power', cfg.max_power)]:
        value = measurements[key]
        if value is None or not math.isfinite(value):
            raise RuntimeError(f'电源{key}回读无效或越限: {value}')
        magnitude = abs(value) if key == 'current' and mode == 'DC' else value
        if magnitude < 0 or magnitude > limit:
            raise RuntimeError(f'电源{key}回读无效或越限: {value}')
