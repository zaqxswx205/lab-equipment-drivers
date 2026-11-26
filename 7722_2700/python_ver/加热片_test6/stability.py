"""Elapsed-time or rolling-temperature-window completion; no temperature control."""
import math
from collections import deque


class Stability:
    def __init__(self, mode='manual', duration_s=300., window_s=300., peak_to_peak_k=.1):
        if mode not in ('manual', 'duration', 'peak_to_peak'):
            raise ValueError('stability_mode须为manual、duration或peak_to_peak')
        if not all(math.isfinite(v) and v > 0 for v in (duration_s, window_s, peak_to_peak_k)):
            raise ValueError('测试时长、判稳窗口和允许峰峰值须为有限正数')
        self.mode = mode
        self.duration_s = duration_s
        self.window_s = window_s
        self.peak_to_peak_k = peak_to_peak_k
        self.reset()

    def reset(self):
        self.start = None
        self.samples = deque()
        self.elapsed_s = 0.
        self.peak_to_peak = None

    def update(self, at, temperature_k, power_ready=True):
        if not math.isfinite(at) or not math.isfinite(temperature_k):
            raise ValueError('判稳时间与温度必须有效')
        if not power_ready:
            self.reset()
            return False
        if self.start is None:
            self.start = at
        if self.samples and at <= self.samples[-1][0]:
            return False
        self.elapsed_s = at-self.start
        self.samples.append((at, temperature_k))
        # Retain the boundary sample immediately before the window; this is
        # conservative and ensures the measured span covers the full window.
        while len(self.samples) > 1 and self.samples[1][0] <= at-self.window_s:
            self.samples.popleft()
        self.peak_to_peak = max(t for _, t in self.samples)-min(t for _, t in self.samples)
        if self.mode == 'duration':
            return self.elapsed_s >= self.duration_s
        if self.mode == 'peak_to_peak':
            return (len(self.samples) >= 2 and at-self.samples[0][0] >= self.window_s and
                    self.peak_to_peak <= self.peak_to_peak_k)
        return False
