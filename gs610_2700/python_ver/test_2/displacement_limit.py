"""Feedback-controlled displacement limit search using fixed current output."""
import math
import time
from decimal import Decimal

from gs610_driver import SweepCancelled, current_points


def validate_thresholds(lower_voltage, upper_voltage):
    lower_voltage, upper_voltage = float(lower_voltage), float(upper_voltage)
    if not math.isfinite(lower_voltage) or lower_voltage >= 0:
        raise ValueError('负向停止电压必须是小于0的有限数值')
    if not math.isfinite(upper_voltage) or upper_voltage <= 0:
        raise ValueError('正向停止电压必须是大于0的有限数值')
    return lower_voltage, upper_voltage


def find_limits(source, meter, stop, period, record, lower_voltage=-2, upper_voltage=2):
    """Return first strict threshold crossings; never exceed +/-3.2 A."""
    lower_voltage, upper_voltage = validate_thresholds(lower_voltage, upper_voltage)
    limits = []
    for direction, endpoint in (('负向极限', '-3.2'), ('正向极限', '3.2')):
        for index, current in enumerate(current_points('0', endpoint, '0.08'), 1):
            if stop.is_set():
                raise SweepCancelled('位移极限搜索已停止')
            source.set_current(current)
            if index == 1:
                source.write(':OUTP ON')
                source.check_error()
                if source.query(':OUTP?') != '1':
                    raise RuntimeError('驱动设备输出未开启')
            started = time.monotonic()
            if stop.wait(period / 2):
                raise SweepCancelled('位移极限搜索已停止')
            _, voltage, status = meter.measure(0)
            if status != 'OK' or voltage is None or not math.isfinite(voltage):
                raise RuntimeError('采集电压无效，停止位移极限搜索')
            drive_voltage = float(source.measure_voltage())
            if not math.isfinite(drive_voltage) or abs(drive_voltage) >= 9e37:
                raise RuntimeError('驱动设备电压读数无效')
            record(direction, index, current, voltage, drive_voltage)
            # Threshold crossing must belong to the requested side of zero.
            crossed = (current < 0 and voltage < lower_voltage) if endpoint[0] == '-' else (current > 0 and voltage > upper_voltage)
            if crossed:
                limits.append(current)
                break
            if stop.wait(max(0, period - (time.monotonic() - started))):
                raise SweepCancelled('位移极限搜索已停止')
        else:
            raise RuntimeError(f'{direction}到达 {endpoint} A 仍未跨过阈值，未更新扫描范围')
        source.set_current(Decimal('0'))
    if stop.is_set():
        raise SweepCancelled('位移极限搜索已停止')
    return tuple(limits)
