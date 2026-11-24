"""Profile step sequencer."""
from __future__ import annotations

from .model import Profile, Step


class Sequencer:
    def __init__(self, profile):
        self.profile = profile
        self.index = -1
        self.completed_performance_stages = set()

    def release_performance(self):
        self.completed_performance_stages.add(self.index)

    def select(self, elapsed_seconds, reference_temperature):
        steps = self.profile.steps
        while self.index+1 < len(steps):
            if self.index >= 0 and steps[self.index].performance_points and self.index not in self.completed_performance_stages:
                break
            next_step = steps[self.index+1]
            trigger = self.profile.trigger_for(next_step)
            time_reached = elapsed_seconds >= next_step.at
            threshold = next_step.temperature_at if trigger == 'time_or_temperature' else next_step.at
            temperature_reached = (threshold is not None and reference_temperature is not None
                                   and reference_temperature <= threshold)
            if trigger == 'time':
                reached = time_reached
            elif trigger == 'time_or_temperature':
                reached = time_reached or temperature_reached
            else:
                reached = temperature_reached
            if not reached:
                break
            self.index += 1
        return steps[self.index] if self.index >= 0 else Step(0., 'voltage', 0.)
