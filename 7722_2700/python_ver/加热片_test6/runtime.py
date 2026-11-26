"""统一运行入口：真实设备使用独立进程，内存模拟用于兼容单元测试。"""
from __future__ import annotations

from acquisition_watchdog import AcquisitionWatchdog
from profile.model import load_profile
from session.controls import AcquisitionCancel, RunControls
from session.runner import run as _simulation_run
from session.manager import run as _process_run

def run(cfg, stop, emit, simulation=False, max_cycles=None, output_dir=None, profile=None, controls=None):
    """Hardware always uses isolated processes; legacy in-memory simulation remains for unit fixtures.

    Set controls.device_processes=True to exercise the process backend with simulated devices.
    """
    implementation = (_simulation_run if simulation and not getattr(controls, "device_processes", False)
                      else _process_run)
    return implementation(cfg, stop, emit, simulation, max_cycles, output_dir, profile, controls)

__all__ = [
    'RunControls', 'AcquisitionCancel', 'AcquisitionWatchdog',
    'load_profile', 'run',
]
