"""2700 Pt100采集与7722制冷机交流阶段驱动，不依赖GUI。

此模块现在是一个薄 facade，向后端子模块转发，并保持原有公共接口不变。
"""
from __future__ import annotations

from instruments.meter2700 import Meter
from instruments.meter2700_sim import SimMeter
from instruments.transport7722 import SerialTransport, SimTransport
from model.channels import Channel, available_channels
from model.config import Config
from model.config_io import load_config, profile_path, save_config
from model.measurements import check_measurements
from model.pt100 import resistance, temperature
from power7722 import ITech7722, error_code

__all__ = [
    'resistance', 'temperature', 'Channel', 'Config', 'available_channels',
    'save_config', 'load_config', 'profile_path', 'Meter', 'SimMeter',
    'SerialTransport', 'SimTransport', 'ITech7722', 'error_code',
    'check_measurements', 'run',
]


def run(cfg, stop, emit, simulation=False, max_cycles=None, output_dir=None, profile=None, controls=None):
    """默认联动运行；传入RunControls可独立启停采集或驱动。"""
    import runtime
    return runtime.run(cfg, stop, emit, simulation=simulation, max_cycles=max_cycles,
                       output_dir=output_dir, profile=profile, controls=controls)
