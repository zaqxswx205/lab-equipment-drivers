"""Heater temperature trigger and DC output update for one loop iteration."""
from __future__ import annotations

import time

import core
from profile.model import Step


def run_heater(session, samples, measurement_errors):
    cfg = session.cfg
    hm = None
    if (cfg.heater_temperature_enabled and not session.temperature_trigger_used and
            session.active_acquisition and session.controls.acquisition.is_set() and samples and not measurement_errors):
        reference = next((t for c, r, t, stamp, at in samples if c.channel == cfg.reference_channel), None)
        reached = reference is not None and reference <= cfg.heater_trigger_temperature
        if reached and cfg.heater_trigger_action == 'off' and (session.active_heater or session.controls.heater.is_set()):
            session.temperature_trigger_used = True
            session.controls.heater.clear()
            session.devices.close_heater()
            session.active_heater = False
            session.controls.heater_pending.clear()
            message = f'参考通道{cfg.reference_channel}达到 {cfg.heater_trigger_temperature+273.15:g} K，加热已停止，DC输出已确认关闭。'
            session.logger.log(message)
            session.emit('heater_done', {'error': '', 'off_ok': True})
            session.emit('heater_status', message)
        elif (reached and cfg.heater_trigger_action == 'on' and not session.active_heater and
              not session.controls.heater_pending.is_set() and session.controls.heater_allowed()):
            session.temperature_trigger_used = True
            session.controls.start_heater()
            message = f'参考通道{cfg.reference_channel}达到加热触发温度 {cfg.heater_trigger_temperature+273.15:g} K，准备启动加热。'
            session.logger.log(message)
            session.emit('heater_starting', message)
    if session.active_heater and not session.controls.heater_allowed():
        session.devices.close_heater()
        session.active_heater = False
        session.controls.heater_pending.clear()
        session.emit('heater_done', {'error': '加热联动保护：交流驱动输出未开启，加热已停止。', 'off_ok': True})
    if session.active_heater and session.heater and session.controls.heater.is_set():
        if not session.active_acquisition or not session.controls.acquisition.is_set() or len(samples) != len(cfg.channels) or measurement_errors:
            raise RuntimeError('加热需要2700完成一轮有效且未越限的温度采集')
        before = session.heater.measure('DC')
        core.check_measurements(before, session.heater_cfg, mode='DC')
        now = time.monotonic()
        heater_target = cfg.heater_voltage if cfg.heater_control == 'voltage' else cfg.heater_power
        feedback_power = before['power'] if session.heater_enabled else 0.
        voltage = session.heater_controller.update(Step(0., cfg.heater_control, heater_target), feedback_power,
                                                   min(now-session.heater_last, cfg.interval), now)
        session.heater_last = now
        session.stopped()
        if not session.controls.heater.is_set() or not session.controls.heater_allowed():
            return hm
        session.heater.set_voltage(voltage)
        session.stopped()
        if not session.controls.heater.is_set() or not session.controls.heater_allowed():
            return hm
        if heater_target > 0 and not session.heater_enabled:
            session.heater.output_on()
            session.heater_enabled = True
        hm = session.heater.measure('DC')
        core.check_measurements(hm, session.heater_cfg, mode='DC')
        reached = (abs(hm['voltage']-heater_target) <= max(.01, heater_target*.01)
                   if cfg.heater_control == 'voltage' else
                   abs(hm['power']-heater_target) <= max(.02, heater_target*cfg.power_tolerance_rel))
        session.emit('heater_sample', dict(hm, target=heater_target, control=cfg.heater_control, reached=reached))
        session.logger.log(f'HEATER DC {cfg.heater_control} target={heater_target:g} voltage={voltage:g}V power={hm["power"]:g}W reached={reached}')
    return hm
