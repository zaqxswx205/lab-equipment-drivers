"""Simulated Keithley 2700 for offline runs."""
from __future__ import annotations

import time

from model.pt100 import resistance


class SimMeter:
    def __init__(self, cfg, power):
        self.cfg, self.power, self.temp, self.last = cfg, power, 23., time.monotonic()

    def setup(self):
        pass

    def read(self, c, stop):
        now = time.monotonic()
        dt, self.last = now-self.last, now
        v = self.power.setting['value'] if self.power and self.power.tp.state['output'] else 0
        self.temp += (-v*v*.15 - (self.temp-23)*.04)*dt
        t = self.temp + (0 if c.channel == self.cfg.reference_channel else .3)
        return resistance(t)+c.lead_ohm, t

    def close(self):
        pass
