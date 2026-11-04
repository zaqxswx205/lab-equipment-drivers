"""制冷机阶段配置：按分钟或降温阈值前进，阶段不回退。

此模块现在是一个薄 facade，向后端子模块转发，并保持原有公共接口不变。
"""
from __future__ import annotations

from profile.controller import DriveController
from profile.model import Profile, Step, load_profile, profile_text_kelvin
from profile.sequencer import Sequencer

__all__ = [
    'Step', 'Profile', 'Sequencer', 'DriveController',
    'load_profile', 'profile_text_kelvin',
]
