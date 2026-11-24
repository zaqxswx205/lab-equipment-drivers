"""Drive-stage-linked performance points with manual or automatic completion (no PID)."""
import configparser
import csv
import math
import threading
import time
from dataclasses import dataclass
from datetime import datetime

from stability import Stability


@dataclass(frozen=True)
class Point:
    temperature_k: float
    heater_w: float
    stability_mode: str = "manual"
    duration_s: float = 300.
    window_s: float = 300.
    peak_to_peak_k: float = .1


def parse_plan(text, cfg):
    parser = configparser.ConfigParser(interpolation=None)
    parser.read_string(text)
    if not parser.sections() or parser.sections() != [f'Point {i}' for i in range(1, len(parser.sections())+1)]:
        raise ValueError('性能点须按[Point 1]、[Point 2]…连续编号')
    points = tuple(Point(parser.getfloat(s, 'temperature_k'), parser.getfloat(s, 'heater_w'),
                         parser.get(s, 'stability_mode', fallback='manual').strip(),
                         parser.getfloat(s, 'duration_s', fallback=300.),
                         parser.getfloat(s, 'window_s', fallback=300.),
                         parser.getfloat(s, 'peak_to_peak_k', fallback=.1)) for s in parser.sections())
    reference = next(c for c in cfg.channels if c.channel == cfg.reference_channel)
    for i, p in enumerate(points, 1):
        Stability(p.stability_mode, p.duration_s, p.window_s, p.peak_to_peak_k)
        if not all(math.isfinite(v) for v in (p.temperature_k, p.heater_w)):
            raise ValueError(f'点{i}参数必须为有限数值')
        if not 0 < p.temperature_k or not reference.min_temperature < p.temperature_k-273.15 < reference.max_temperature:
            raise ValueError(f'点{i}温度须严格位于参考通道保护范围内')
        if not 0 < p.heater_w <= cfg.heater_max_power*.95:
            raise ValueError(f'点{i}功率须大于0且不高于对应功率保护的95%')
        if i > 1 and p.temperature_k > points[i-2].temperature_k:
            raise ValueError('当前只支持相同温度或逐点降低温度的降温流程')
    return points


class PerformanceRun:
    def __init__(self, points):
        self.points = tuple(points)
        self.index = 0
        self.phase = 'cooling'
        self.next_requested = threading.Event()
        self.ready = threading.Event()
        self.file = None
        self.writer = None
        self.summary_file = None
        self.summary_writer = None
        self.reset_statistics()
        self.reset_stability()
        self.paused_at = None
        self.held_step = None

    def reset_stability(self):
        p = self.point
        self.stability = Stability(p.stability_mode, p.duration_s, p.window_s, p.peak_to_peak_k)
        self.auto_completed = False

    def validate_profile(self, profile):
        linked = [n for step in profile.steps for n in step.performance_points]
        if linked != list(range(1, len(self.points)+1)):
            raise ValueError("驱动方案须通过performance_points按顺序关联所有性能点，如Step 2关联1,2，Step 3关联3")

    def reset_statistics(self):
        self.count = 0
        self.mean = self.m2 = 0.
        self.minimum = float('inf')
        self.maximum = float('-inf')

    def summarize(self, outcome):
        if self.count and self.summary_writer:
            p = self.point
            self.summary_writer.writerow([self.index+1, p.temperature_k, p.heater_w,
                                          self.held_step.mode, self.held_step.value,
                                          outcome, self.count, self.mean, self.minimum, self.maximum,
                                          self.maximum-self.minimum, math.sqrt(self.m2/self.count),
                                          self.mean-p.temperature_k])
            self.summary_file.flush()
        self.reset_statistics()

    @property
    def point(self):
        return self.points[self.index]

    def status(self, s, message):
        p = self.point
        text = f'性能点 {self.index+1}/{len(self.points)} · {p.temperature_k:g} K @ {p.heater_w:g} W · 判稳 {p.stability_mode}：{message}'
        s.emit('performance_status', text)
        s.logger.log(text)

    def advance(self):
        if self.ready.is_set():
            self.next_requested.set()

    def update(self, s, samples, errors):
        if self.phase == "complete":
            return
        if not s.controls.drive.is_set() or not s.controls.acquisition.is_set() or errors:
            raise RuntimeError('性能点测试的驱动或采集已停止/测温异常，终止整轮测试')
        if len(samples) != len(s.cfg.channels) or any(t is None for c, r, t, stamp, at in samples):
            raise RuntimeError('性能点测试需要完整有效的温度采样')
        reference = next(t for c, r, t, stamp, at in samples if c.channel == s.cfg.reference_channel)
        if self.phase != 'cooling' and not s.controls.heater.is_set():
            raise RuntimeError('性能点加热已停止，终止整轮测试')
        if self.phase == 'testing' and self.next_requested.is_set():
            self.next_requested.clear()
            self.ready.clear()
            self.summarize(self.point.stability_mode+'_complete' if self.auto_completed else 'manual_complete')
            self.status(s, '本点记录结束，关闭加热输出')
            s.controls.heater.clear()
            s.devices.close_heater()
            s.active_heater = False
            s.controls.heater_pending.clear()
            s.emit('heater_done', {'error': '', 'off_ok': True})
            if self.index+1 == self.held_step.performance_points[-1]:
                s.sequencer.release_performance()
            finished = self.index == len(self.points)-1
            if not finished:
                self.index += 1
                self.reset_stability()
            self.phase = 'complete' if finished else 'cooling'
            s.performance_step = None
            now = time.monotonic()
            s.drive_epoch += now-self.paused_at
            s.last = now
            self.paused_at = None
            self.held_step = None
            from session.timing import publish_clock
            publish_clock(s)
            self.status(s, '全部性能点完成，放行驱动方案后续阶段，采集继续' if finished else
                        '恢复驱动方案；关联点全部完成后放行下一Step，暂停时间不计入累计时间')
            # Execute the resumed profile for at least one round before the next
            # point can capture it, including consecutive points at one temperature.
            return
        if self.phase == 'cooling':
            now = time.monotonic()
            # Evaluate the current reference before freezing, so a temperature
            # stage reached in this same sample is the one used for the test.
            step = s.sequencer.select(now-s.drive_epoch, reference)
            if self.index+1 not in step.performance_points or reference+273.15 > self.point.temperature_k:
                return
            if not s.controls.heater_allowed():
                return
            if step.value <= 0:
                raise RuntimeError('性能点到温时驱动方案设定为零输出，不能加载测试；请调整驱动方案')
            self.paused_at = now
            self.held_step = s.performance_step = step
            from session.timing import publish_clock
            publish_clock(s)
            s.performance_heater_w = self.point.heater_w
            self.phase = 'loading'
            s.controls.start_heater()
            s.emit('heater_starting', '性能点到温，正在启动指定功率加热…')
            self.status(s, '温度阈值已到达，正在加载；不执行温度闭环')
        if self.phase == 'loading' and s.heater_enabled:
            self.phase = 'testing'
            self.ready.set()
            self.status(s, '测试记录中；判稳方式：'+self.point.stability_mode+'；可手动结束本点')

    def record(self, s, samples, drive, heater):
        if self.phase == "complete":
            return
        if self.file is None:
            from pathlib import Path
            self.file = (Path(s.output_dir or Path(__file__).parent/'session'/'data') /
                         (('SIM_' if s.simulation else '')+datetime.now().strftime('%Y%m%d_%H%M%S_%f')+'_performance.csv')).open('x', newline='', encoding='utf-8-sig')
            summary_path = self.file.name.replace('_performance.csv', '_performance_summary.csv')
            self.summary_file = open(summary_path, 'x', newline='', encoding='utf-8-sig')
            self.summary_writer = csv.writer(self.summary_file)
            self.summary_writer.writerow(['point','target_K','heater_W','drive_mode','drive_target','outcome','n','mean_K',
                                          'min_K','max_K','peak_to_peak_K','std_K','mean_error_K'])
            self.writer = csv.writer(self.file)
            self.writer.writerow(['timestamp','point','phase','target_K','heater_target_W','drive_mode','drive_target',
                                  'channel','temperature_K','resistance_ohm','drive_power_W','heater_power_W'])
        for c, r, t, stamp, at in samples:
            p = self.point
            self.writer.writerow([stamp.isoformat(),self.index+1,self.phase,p.temperature_k,p.heater_w,
                                  *(s.controller.target or ('', '')),
                                  c.channel,t+273.15 if t is not None else '',r,
                                  drive['power'] if drive else '',heater['power'] if heater else ''])
        self.file.flush()
        if self.phase == 'testing':
            value = next(t+273.15 for c, r, t, stamp, at in samples if c.channel == s.cfg.reference_channel)
            self.count += 1
            delta = value-self.mean
            self.mean += delta/self.count
            self.m2 += delta*(value-self.mean)
            self.minimum = min(self.minimum, value)
            self.maximum = max(self.maximum, value)
            step = s.performance_step
            drive_key = 'power' if step.mode == 'power' else 'voltage'
            tolerance = max(.02, step.value*s.cfg.power_tolerance_rel) if drive_key == 'power' else max(.01, step.value*.01)
            power_ready = (drive is not None and heater is not None and
                           abs(drive[drive_key]-step.value) <= tolerance and
                           abs(heater['power']-self.point.heater_w) <= max(.02, self.point.heater_w*s.cfg.power_tolerance_rel))
            at = next(at for c, r, t, stamp, at in samples if c.channel == s.cfg.reference_channel)
            stable = self.stability.update(at, value, power_ready)
            s.emit('performance_stats', f'本点参考通道：{value:.4f} K；样本{self.count}；'
                   f'均值{self.mean:.4f} K；峰峰值{self.maximum-self.minimum:.4f} K；'
                   f'标准差{math.sqrt(self.m2/self.count):.4f} K；'
                   f'判稳计时{self.stability.elapsed_s:.1f}s；窗口峰峰值'
                   f'{format(self.stability.peak_to_peak, ".4f") if self.stability.peak_to_peak is not None else "--"} K；'
                   + ('功率已达标' if power_ready else '等待功率达标，判稳计时重置'))
            if stable:
                self.auto_completed = True
                self.next_requested.set()
                self.status(s, '判稳条件已满足，下一轮关闭加热并进入下一点')


    def close(self):
        self.ready.clear()
        try:
            self.summarize('interrupted')
        finally:
            if self.file:
                self.file.close()
            if self.summary_file:
                self.summary_file.close()
