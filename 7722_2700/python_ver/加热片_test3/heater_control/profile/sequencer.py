"""Profile step sequencer."""
from __future__ import annotations

from .model import Profile, Step


class Sequencer:
    def __init__(self, profile):
        self.profile = profile
        self.index = -1

    def select(self, elapsed_minutes, reference_temperature):
        steps = self.profile.steps
        while self.index+1 < len(steps):
            next_step = steps[self.index+1]
            reached = (elapsed_minutes >= next_step.at if self.profile.strategy == 'time'
                       else reference_temperature <= next_step.at)
            if not reached:
                break
            self.index += 1
        return steps[self.index] if self.index >= 0 else Step(0., 'voltage', 0.)
