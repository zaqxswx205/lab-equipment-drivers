"""Channel dataclass and available-channel calculator."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Channel:
    channel: int = 101
    lead_ohm: float = 0.
    min_temperature: float = -238.
    max_temperature: float = 76.
    sensor_type: str = 'pt100'
    sensor_curve: str = 'user_table'
    name: str = ''


def clean_channel_name(name):
    if not isinstance(name, str) or any(ord(char) < 32 for char in name):
        raise ValueError('通道名称须为单行文字')
    name = name.strip()
    if len(name) > 40:
        raise ValueError('通道名称最多40个字符')
    return name


def available_channels(module, wires, slot=None):
    count = {7700: 20, 7702: 40}.get(module)
    if count is None or wires not in (2, 4):
        raise ValueError('每槽模块须选择7700或7702，接线制须选择两线或四线')
    if slot is not None and slot not in (1, 2):
        raise ValueError('槽位仅支持1或2')
    last = count//2 if wires == 4 else count
    return tuple(s*100+channel for s in ((slot,) if slot else (1, 2)) for channel in range(1, last+1))
