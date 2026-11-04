"""Channel dataclass and available-channel calculator."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Channel:
    channel: int = 101
    lead_ohm: float = 0.
    min_temperature: float = -50.
    max_temperature: float = 80.


def available_channels(module, wires, slot=None):
    count = {7700: 20, 7702: 40}.get(module)
    if count is None or wires not in (2, 4):
        raise ValueError('每槽模块须选择7700或7702，接线制须选择两线或四线')
    if slot is not None and slot not in (1, 2):
        raise ValueError('槽位仅支持1或2')
    last = count//2 if wires == 4 else count
    return tuple(s*100+channel for s in ((slot,) if slot else (1, 2)) for channel in range(1, last+1))
