"""独立请求采集/驱动，同一工作线程顺序访问设备，关闭一个不影响另一个。

此模块现在是一个薄 facade，向后端子模块转发，并保持原有公共接口不变。
"""
from __future__ import annotations

from acquisition_watchdog import AcquisitionWatchdog
from profile.model import load_profile
from session.controls import AcquisitionCancel, RunControls
from session.runner import run

__all__ = [
    'RunControls', 'AcquisitionCancel', 'AcquisitionWatchdog',
    'load_profile', 'run',
]
