"""Drive profile dataclasses, file loading, and editor K-normalisation."""
from __future__ import annotations

import configparser
import io
import math
from dataclasses import dataclass, field

from model.config import Config


@dataclass(frozen=True)
class Step:
    at: float
    mode: str
    value: float
    trigger: str | None = None
    temperature_at: float | None = None
    performance_points: tuple[int, ...] = ()


@dataclass
class Profile:
    strategy: str = 'time'
    steps: list[Step] = field(default_factory=lambda: [Step(0., 'voltage', 0.)])

    def trigger_for(self, step):
        return step.trigger or self.strategy

    @property
    def requires_temperature(self):
        return any(self.trigger_for(step) in ("temperature", "time_or_temperature") for step in self.steps)

    @property
    def requires_performance(self):
        return any(step.performance_points for step in self.steps)

    def validate(self, cfg):
        if self.strategy not in ('time', 'temperature', 'mixed') or not self.steps:
            raise ValueError('驱动方案须为time、temperature或mixed，且至少有一个阶段')
        linked = [point for step in self.steps for point in step.performance_points]
        if linked and (any(type(n) is not int or n <= 0 for n in linked) or linked != list(range(1, len(linked)+1))):
            raise ValueError("performance_points须按阶段顺序连续引用1,2,3…，不可重复或跳号")
        previous = {}
        reference = next((c for c in cfg.channels if c.channel == cfg.reference_channel), None)
        if self.requires_temperature and reference is None:
            raise ValueError('参考温度通道必须是已添加的采集通道')
        for i, step in enumerate(self.steps, 1):
            trigger = self.trigger_for(step)
            if trigger not in ("time", "temperature", "time_or_temperature"):
                raise ValueError(f"阶段{i}须指定 trigger=time、temperature 或 time_or_temperature")
            if self.strategy != "mixed" and trigger != self.strategy:
                raise ValueError("混合触发条件须使用 strategy=mixed")
            if not math.isfinite(step.at) or not math.isfinite(step.value) or step.value < 0:
                raise ValueError(f'阶段{i}阈值与设定必须为有限数值，设定须非负')
            if step.mode not in ('voltage', 'power'):
                raise ValueError(f'阶段{i}模式只支持voltage(V)或power(W)')
            if step.performance_points and step.value <= 0:
                raise ValueError("等待性能点的阶段必须有非零输出设定")
            limit = cfg.max_voltage if step.mode == 'voltage' else cfg.max_power*.95
            if step.value > limit:
                if step.mode == 'voltage':
                    raise ValueError(
                        f'阶段{i}（电压模式）：设定电压 {step.value:.15g} V，'
                        f'超过“7722 交流驱动 → 最大电压(V)”设置的 {cfg.max_voltage:.15g} V 上限。')
                raise ValueError(
                    f'阶段{i}（功率模式）：目标有功功率 {step.value:.15g} W，'
                    f'超过允许目标上限 {limit:.15g} W'
                    f'（“7722 交流驱动 → 最大功率(W)”设置为 {cfg.max_power:.15g} W，目标不得超过其95%）。')
            if trigger == 'time_or_temperature':
                if step.at < 0 or step.temperature_at is None or not math.isfinite(step.temperature_at):
                    raise ValueError('时间或温度阶段须填写非负累计秒数at和有限温度temperature_at')
                if not reference.min_temperature < step.temperature_at < reference.max_temperature:
                    raise ValueError('降温阈值必须严格位于参考通道的温度保护范围内')
            elif trigger == 'time':
                if step.at < 0 or (trigger in previous and step.at <= previous[trigger]):
                    raise ValueError('时间阶段的at为非负秒数，必须按文件顺序严格递增')
            elif not reference.min_temperature < step.at < reference.max_temperature:
                raise ValueError('降温阈值必须严格位于参考通道的温度保护范围内')
            elif trigger in previous and (step.at > previous[trigger] or
                    (step.at == previous[trigger] and not (step.performance_points and i > 1 and self.steps[i-2].performance_points))):
                raise ValueError('温度阶段的at必须按文件顺序严格递减')
            previous[trigger] = step.at


def _read_parser(path):
    p = configparser.ConfigParser(interpolation=None)
    if not p.read(path, encoding='utf-8-sig'):
        raise FileNotFoundError(f'驱动方案不存在: {path}')
    return p


def _parse_profile(p):
    sections = [s for s in p.sections() if s != 'Drive']
    if sections != [f'Step {i}' for i in range(1, len(sections)+1)]:
        raise ValueError('阶段须依次命名为[Step 1]、[Step 2]…，不能缺号或乱序')
    unit = p.get('Drive', 'temperature_unit', fallback='C').strip().upper()
    if unit not in ('C', 'K'):
        raise ValueError('temperature_unit 只支持 C 或 K')
    time_unit = p.get('Drive', 'time_unit', fallback='s').strip().lower()
    if time_unit not in ('s', 'min'):
        raise ValueError('time_unit 只支持 s 或 min')
    strategy = p.get('Drive', 'strategy')
    steps = []
    point_index = 0
    for section in sections:
        trigger = p.get(section, 'trigger', fallback=strategy).strip().lower()
        at = p.getfloat(section, 'at')
        if trigger in ('time', 'time_or_temperature') and time_unit == 'min':
            at *= 60
        if trigger == 'temperature' and unit == 'K':
            at = round(at-273.15, 10)
        temperature_at = p.getfloat(section, 'temperature_at') if trigger == 'time_or_temperature' else None
        if temperature_at is not None and unit == 'K':
            temperature_at = round(temperature_at-273.15, 10)
        if p.get(section, 'heater_w', fallback='').strip():
            point_index += 1
            bindings = (point_index,)
        else:
            bindings = tuple(int(n.strip()) for n in p.get(section, 'performance_points', fallback='').split(',') if n.strip())
        steps.append(Step(at, p.get(section, 'mode').strip().lower(),
                          p.getfloat(section, 'value'),
                          trigger if strategy == 'mixed' or p.has_option(section, 'trigger') else None, temperature_at,
                          bindings))
    return Profile(strategy, steps)


def load_profile(path, cfg):
    profile = _parse_profile(_read_parser(path))
    profile.validate(cfg)
    return profile


def profile_text_kelvin(text):
    """编辑器统一展示K；无单位标记的旧方案按C转换。"""
    p = configparser.ConfigParser(interpolation=None)
    p.read_string(text)
    unit = p.get('Drive', 'temperature_unit', fallback='C').strip().upper()
    if unit not in ('C', 'K'):
        raise ValueError('temperature_unit 只支持 C 或 K')
    time_unit = p.get('Drive', 'time_unit', fallback='s').strip().lower()
    if time_unit not in ('s', 'min'):
        raise ValueError('time_unit 只支持 s 或 min')
    if unit == 'K' and time_unit == 's':
        return text
    for section in p.sections():
        if section == 'Drive':
            continue
        trigger = p.get(section, 'trigger', fallback=p.get('Drive', 'strategy'))
        if trigger in ('time', 'time_or_temperature') and time_unit == 'min':
            p[section]['at'] = str(p.getfloat(section, 'at')*60)
        if trigger == 'temperature' and unit != 'K':
            p[section]['at'] = str(round(p.getfloat(section, 'at')+273.15, 10))
        elif trigger == 'time_or_temperature' and unit != 'K':
            p[section]['temperature_at'] = str(round(p.getfloat(section, 'temperature_at')+273.15, 10))
    p['Drive']['temperature_unit'] = 'K'
    p['Drive']['time_unit'] = 's'
    out = io.StringIO()
    p.write(out)
    return out.getvalue()
