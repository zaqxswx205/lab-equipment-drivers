"""Pt100 resistance <-> temperature conversion."""
from __future__ import annotations

import math


def resistance(t):
    return 100 * (1 + 3.9083e-3*t - 5.775e-7*t*t
                  + (-4.183e-12*(t-100)*t**3 if t < 0 else 0))


def temperature(r):
    if not math.isfinite(r) or not resistance(-200)-1e-9 <= r <= resistance(850)+1e-9:
        raise ValueError(f'Pt100 电阻无效/超范围: {r!r} Ω')
    lo, hi = -200., 850.
    for _ in range(50):
        mid = (lo + hi) / 2
        if resistance(mid) < r:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2
