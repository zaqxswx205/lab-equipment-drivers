"""Synchronise editable settings from a drive file before starting a session."""
import math
from dataclasses import replace

from .model import _parse_profile, _read_parser


def power_limit(target):
    """Include the 5% headroom, avoiding a floating-point boundary rejection."""
    limit = target / .95
    if limit * .95 < target:
        limit = math.nextafter(limit, math.inf)
    return limit


def settings_from_profile(path, cfg):
    """Return a validated copy; never change the caller or start device output.

    Keep user limits that already cover every stage. The file cannot tell us
    the required current or the voltage needed to achieve a power target.
    """
    parser = _read_parser(path)
    profile = _parse_profile(parser)
    candidate = replace(cfg)
    for step in profile.steps:
        if not math.isfinite(step.value) or step.value < 0:
            raise ValueError('方案设定须为非负有限数值')
        if step.mode == 'voltage':
            candidate.max_voltage = max(candidate.max_voltage, step.value)
        elif step.mode == 'power':
            candidate.max_power = max(candidate.max_power, power_limit(step.value))
    heater = [parser.getfloat(s, 'heater_w') for s in parser.sections()
              if s != 'Drive' and parser.get(s, 'heater_w', fallback='').strip()]
    if heater:
        if any(not math.isfinite(value) or value <= 0 for value in heater):
            raise ValueError('方案加热功率须为正的有限数值')
        candidate.heater_control = 'power'
        candidate.heater_power = heater[0]
        candidate.heater_max_power = max(candidate.heater_max_power, power_limit(max(heater)))
        # Validate the heater settings even when its output is currently disabled.
        replace(candidate, heater_enabled=True).validate()
        from plan_io import points_from_drive
        from performance import PerformanceRun
        import io
        content = io.StringIO()
        parser.write(content)
        points = points_from_drive(content.getvalue(), candidate)
        PerformanceRun(points).validate_profile(profile)
    candidate.validate()
    profile.validate(candidate)
    return candidate
