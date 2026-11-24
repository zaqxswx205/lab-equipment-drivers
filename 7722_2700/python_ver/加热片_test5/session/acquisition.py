"""One round of 2700 acquisition, including interval wait and limit handling."""
from __future__ import annotations

import time
from datetime import datetime


def run_acquisition(session):
    cfg = session.cfg
    samples = []
    measurement_errors = {}
    if not (session.active_acquisition and session.controls.acquisition.is_set()):
        return samples, measurement_errors
    if session.simulation:
        session.meter.power = session.power
    for channel in sorted(cfg.channels, key=lambda c: c.channel != cfg.reference_channel):
        session.stopped()
        if not session.controls.acquisition.is_set():
            break
        try:
            while session.last_sample_started is not None:
                remaining = cfg.interval-(time.monotonic()-session.last_sample_started)
                if remaining <= 0:
                    break
                if session.active_heater and not session.controls.heater.is_set():
                    session.devices.close_heater()
                    session.active_heater = False
                    session.controls.heater_pending.clear()
                    session.emit('heater_done', {'error': '', 'off_ok': True})
                if session.active_drive and not session.controls.drive.is_set():
                    session.devices.close_drive()
                    session.active_drive = False
                    session.controls.drive_pending.clear()
                    session.emit('drive_done', {'error': '', 'off_ok': True})
                if session.acquisition_cancel.wait(min(.02, remaining)):
                    raise InterruptedError('已停止')
            if session.acquisition_cancel.is_set():
                raise InterruptedError('已停止')
            session.last_sample_started = time.monotonic()
            session.watchdog.expect(cfg.settle+max(5., cfg.timeout*3), f'通道{channel.channel}读取')
            r, t = session.meter.read(channel, session.acquisition_cancel)
        except InterruptedError:
            if session.stop.is_set():
                raise
            break
        except Exception as exc:
            session.watchdog.trip(f'2700通道{channel.channel}采集失败：{exc}')
            raise
        session.stopped()
        session.watchdog.expect(cfg.interval+cfg.settle+max(5., cfg.timeout*3), '采集处理或等待下一通道')
        acquired = time.monotonic()
        if t is None:
            measurement_errors[channel.channel] = '电阻无效/超出Pt100范围'
        elif not channel.min_temperature <= t < channel.max_temperature:
            measurement_errors[channel.channel] = (
                f'温度越限: {t+273.15:.3f} K（允许 {channel.min_temperature+273.15:g} ≤ 温度 < {channel.max_temperature+273.15:g} K）')
        if channel.channel in measurement_errors:
            if cfg.heater_temperature_enabled:
                session.temperature_trigger_used = True
            session.logger.log(f'2700 通道{channel.channel}: {measurement_errors[channel.channel]}; resistance={r!r}')
        samples.append((channel, r, t, datetime.now(), acquired))
        if (session.active_drive or session.active_heater) and channel.channel in measurement_errors:
            reason = f'通道{channel.channel} {measurement_errors[channel.channel]}'
            session.devices.shutdown_devices()
            if session.active_heater:
                session.devices.close_heater()
                session.active_heater = False
                session.controls.heater_pending.clear()
                session.emit('heater_done', {'error': reason+'；加热已停止，采集继续。', 'off_ok': True})
            if session.active_drive:
                session.devices.close_drive()
                session.controls.drive.clear()
                session.controls.drive_pending.clear()
                session.active_drive = False
                session.emit('drive_done', {'error': reason+'；驱动已停止，采集与记录继续。', 'off_ok': True})
    return samples, measurement_errors
