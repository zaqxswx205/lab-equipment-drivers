"""Selected reference-table interpolation; public temperatures remain Celsius."""
from __future__ import annotations
import math
from bisect import bisect_left

from .rtd_curves import CURVE_LABELS, curve_table

SENSOR_TYPES = ('pt100', 'pt1000')


def nominal_resistance(sensor_type):
    """Resistance at 0 degrees Celsius, in ohms."""
    if not isinstance(sensor_type,str) or sensor_type.lower() not in SENSOR_TYPES:
        raise ValueError('传感器类型只支持Pt100或Pt1000')
    return 100. if sensor_type.lower() == 'pt100' else 1000.


def _interpolate(value, xs, ys, description, curve):
    # Celsius/Kelvin and Pt1000 scaling can introduce endpoint roundoff.
    if not math.isfinite(value) or not xs[0]-1e-9 <= value <= xs[-1]+1e-9:
        raise ValueError(f'{description} 无效/超范围: {value!r}；所选曲线{CURVE_LABELS[curve]}')
    value = min(xs[-1], max(xs[0], value))
    index = bisect_left(xs, value)
    if xs[index] == value:
        return ys[index]
    fraction = (value-xs[index-1]) / (xs[index]-xs[index-1])
    return ys[index-1] + fraction * (ys[index]-ys[index-1])


def resistance(t, sensor_type='pt100', sensor_curve='user_table'):
    scale = nominal_resistance(sensor_type) / 100.
    table = curve_table(sensor_curve)
    temperatures, resistances = zip(*table)
    return _interpolate(t+273.15, temperatures, resistances,
                        '温度(K)', sensor_curve) * scale


def temperature(r, sensor_type='pt100', sensor_curve='user_table'):
    scale = nominal_resistance(sensor_type) / 100.
    table = curve_table(sensor_curve)
    temperatures, resistances = zip(*table)
    kelvin = _interpolate(r/scale, resistances, temperatures,
                          f'{sensor_type.capitalize()} 电阻(Ω，折算为Pt100)', sensor_curve)
    return kelvin-273.15
