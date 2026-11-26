"""AC drive stage update for one loop iteration."""
from __future__ import annotations

import time

import core


def run_drive(session, samples):
    cfg = session.cfg
    m = None
    step = None
    drive_elapsed = None
    if not session.active_drive:
        return m, step, drive_elapsed
    before = session.power.measure('AC')
    core.check_measurements(before, cfg)
    now = time.monotonic()
    if session.drive_profile.requires_temperature and not samples:
        raise RuntimeError('温度方案没有有效的参考温度')
    clock = session.performance.paused_at if session.performance and session.performance.paused_at is not None else now
    drive_elapsed = (clock-session.drive_epoch)/60
    step = session.performance_step or session.sequencer.select(now-session.drive_epoch, samples[0][2] if samples else None)
    voltage = session.controller.update(step, before['power'], min(now-session.last, cfg.interval), now)
    session.last = now
    session.stopped()
    if not session.controls.drive.is_set():
        return m, step, drive_elapsed
    if step.value == 0 and session.enabled:
        session.power.output_off_confirmed(delay=0 if session.simulation else .2)
        session.enabled = False
        session.controls.drive_ready.clear()
    session.power.set_voltage(voltage)
    session.stopped()
    if not session.controls.drive.is_set():
        return m, step, drive_elapsed
    if step.value > 0 and not session.enabled:
        session.power.output_on()
        session.enabled = True
        session.controls.drive_ready.set()
    m = session.power.measure('AC')
    core.check_measurements(m, cfg)
    session.logger.log(f'STAGE {session.sequencer.index+1} {step.mode} {step.value:g}; drive_elapsed_min={drive_elapsed:.6f}')
    return m, step, drive_elapsed
