"""Config persistence, legacy migration, and profile path resolution."""
from __future__ import annotations

import configparser
from dataclasses import asdict
from pathlib import Path

from .channels import Channel
from .config import Config


ROOT = Path(__file__).resolve().parent.parent


def save_config(path, cfg):
    cfg.validate()
    p = configparser.ConfigParser()
    p['Settings'] = {k: str(v) for k, v in asdict(cfg).items() if k != 'channels'}
    p['Settings']['temperature_unit'] = 'K'
    p['Settings']['heater_trigger_temperature'] = str(round(cfg.heater_trigger_temperature+273.15, 10))
    for c in cfg.channels:
        p[f'Channel {c.channel}'] = {k: str(v) for k, v in asdict(c).items() if k != 'channel'}
        for key in ('min_temperature', 'max_temperature'):
            p[f'Channel {c.channel}'][key] = str(round(getattr(c, key)+273.15, 10))
    path = Path(path)
    tmp = path.with_suffix('.ini.tmp')
    with tmp.open('w', encoding='utf-8') as f:
        p.write(f)
    tmp.replace(path)


def load_config(path):
    p = configparser.ConfigParser()
    if not p.read(path, encoding='utf-8-sig'):
        raise FileNotFoundError(path)
    c = Config()
    unit = p.get('Settings', 'temperature_unit', fallback='C').strip().upper()
    if unit not in ('C', 'K'):
        raise ValueError('temperature_unit 只支持 C 或 K')
    for k, default in asdict(c).items():
        if k == 'channels' or not p.has_option('Settings', k):
            continue
        getter = p.getboolean if isinstance(default, bool) else p.getint if isinstance(default, int) else p.getfloat if isinstance(default, float) else p.get
        setattr(c, k, getter('Settings', k))
    if unit == 'K' and p.has_option('Settings', 'heater_trigger_temperature'):
        c.heater_trigger_temperature = round(c.heater_trigger_temperature-273.15, 10)
    sections = [s for s in p.sections() if s.startswith('Channel ')]
    for slot in (1, 2):
        slot_sections = [s for s in sections if int(s.split()[1])//100 == slot]
        for key in ('module', 'wires'):
            field_name = f'slot{slot}_{key}'
            legacy = {p.getint(s, key) for s in slot_sections if p.has_option(s, key)}
            configured = []
            if p.has_option('Settings', field_name):
                configured.append(p.getint('Settings', field_name))
            if p.has_option('Settings', key):
                configured.append(p.getint('Settings', key))
            values = legacy | set(configured)
            if len(values) > 1:
                raise ValueError(f'Slot {slot} 的{key}不一致，请统一同一槽位的设置')
            if values:
                setattr(c, field_name, values.pop())
    c.channels = [Channel(
        int(s.split()[1]), p.getfloat(s, 'lead_ohm', fallback=0),
        p.getfloat(s, 'min_temperature', fallback=p.getfloat('Settings', 'min_temperature', fallback=-50.)),
        p.getfloat(s, 'max_temperature', fallback=p.getfloat('Settings', 'max_temperature', fallback=80.)))
        for s in sections]
    if unit == 'K':
        for channel in c.channels:
            section = f'Channel {channel.channel}'
            for key in ('min_temperature', 'max_temperature'):
                if p.has_option(section, key) or p.has_option('Settings', key):
                    setattr(channel, key, round(getattr(channel, key)-273.15, 10))
    if not p.has_option('Settings', 'reference_channel') and p.has_option('Settings', 'control_channel'):
        c.reference_channel = p.getint('Settings', 'control_channel')
    c.validate()
    return c


def profile_path(cfg):
    path = Path(cfg.profile_file)
    return path if path.is_absolute() else ROOT/path
