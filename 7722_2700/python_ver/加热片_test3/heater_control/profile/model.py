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


@dataclass
class Profile:
    strategy: str = 'time'
    steps: list[Step] = field(default_factory=lambda: [Step(0., 'voltage', 0.)])

    def validate(self, cfg):
        if self.strategy not in ('time', 'temperature') or not self.steps:
            raise ValueError('驱动方案须为time或temperature，且至少有一个阶段')
        previous = None
        reference = next((c for c in cfg.channels if c.channel == cfg.reference_channel), None)
        if self.strategy == 'temperature' and reference is None:
            raise ValueError('参考温度通道必须是已添加的采集通道')
        for i, step in enumerate(self.steps, 1):
            if not math.isfinite(step.at) or not math.isfinite(step.value) or step.value < 0:
                raise ValueError(f'阶段{i}阈值与设定必须为有限数值，设定须非负')
            if step.mode not in ('voltage', 'power'):
                raise ValueError(f'阶段{i}模式只支持voltage(V)或power(W)')
            limit = cfg.max_voltage if step.mode == 'voltage' else cfg.max_power*.95
            if step.value > limit:
                raise ValueError(f'阶段{i}设定超过允许值{limit:g}；功率目标须不高于功率保护的95%')
            if self.strategy == 'time':
                if step.at < 0 or (previous is not None and step.at <= previous):
                    raise ValueError('时间阶段的at为非负分钟数，必须按文件顺序严格递增')
            elif not reference.min_temperature < step.at < reference.max_temperature:
                raise ValueError('降温阈值必须严格位于参考通道的温度保护范围内')
            elif previous is not None and step.at >= previous:
                raise ValueError('温度阶段的at必须按文件顺序严格递减')
            previous = step.at


def load_profile(path, cfg):
    p = configparser.ConfigParser(interpolation=None)
    if not p.read(path, encoding='utf-8-sig'):
        raise FileNotFoundError(f'驱动方案不存在: {path}')
    sections = [s for s in p.sections() if s != 'Drive']
    if sections != [f'Step {i}' for i in range(1, len(sections)+1)]:
        raise ValueError('阶段须依次命名为[Step 1]、[Step 2]…，不能缺号或乱序')
    unit = p.get('Drive', 'temperature_unit', fallback='C').strip().upper()
    if unit not in ('C', 'K'):
        raise ValueError('temperature_unit 只支持 C 或 K')
    strategy = p.get('Drive', 'strategy')
    profile = Profile(strategy, [
        Step(round(p.getfloat(s, 'at')-273.15, 10) if strategy == 'temperature' and unit == 'K' else p.getfloat(s, 'at'), p.get(s, 'mode').strip().lower(), p.getfloat(s, 'value'))
        for s in sections])
    profile.validate(cfg)
    return profile


def profile_text_kelvin(text):
    """编辑器统一展示K；无单位标记的旧方案按C转换。"""
    p = configparser.ConfigParser(interpolation=None)
    p.read_string(text)
    unit = p.get('Drive', 'temperature_unit', fallback='C').strip().upper()
    if unit not in ('C', 'K'):
        raise ValueError('temperature_unit 只支持 C 或 K')
    if p.get('Drive', 'strategy') != 'temperature' or unit == 'K':
        return text
    for section in p.sections():
        if section != 'Drive':
            p[section]['at'] = str(round(p.getfloat(section, 'at')+273.15, 10))
    p['Drive']['temperature_unit'] = 'K'
    out = io.StringIO()
    p.write(out)
    return out.getvalue()
