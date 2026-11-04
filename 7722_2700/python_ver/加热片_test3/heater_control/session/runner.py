"""Main run loop orchestration."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from pathlib import Path
import json
import time

import core
from csv_recording import CsvRecording
from model.config_io import profile_path
from session.acquisition import run_acquisition
from session.controls import RunControls, AcquisitionCancel
from session.devices import DeviceManager
from session.drive import run_drive
from session.heater import run_heater
from session.records import Logger


class Session:
    def __init__(self, cfg, stop, emit, simulation, max_cycles, output_dir, profile, controls, acquisition_cancel, logger, devices):
        self.cfg = cfg
        self.stop = stop
        self.emit = emit
        self.simulation = simulation
        self.max_cycles = max_cycles
        self.output_dir = output_dir
        self.profile = profile
        self.controls = controls
        self.acquisition_cancel = acquisition_cancel
        self.logger = logger
        self.devices = devices
        self.power = None
        self.meter = None
        self.heater = None
        self.heater_controller = None
        self.heater_cfg = None
        self.heater_enabled = False
        self.heater_last = None
        self.temperature_trigger_used = False
        self.log_file = None
        self.error = ''
        self.off_ok = True
        self.active_acquisition = False
        self.active_drive = False
        self.active_heater = False
        self.epoch = None
        self.drive_epoch = None
        self.last = None
        self.last_sample_started = None
        self.sequencer = None
        self.controller = None
        self.drive_profile = None
        self.enabled = False
        self.cycles = 0
        self.watchdog = None

    def stopped(self):
        if self.watchdog and self.watchdog.reason:
            raise RuntimeError(self.watchdog.reason)
        if self.stop.is_set():
            raise InterruptedError('已停止')


def measurement_fault(session, reason):
    session.controls.drive.clear()
    session.controls.heater.clear()
    session.stop.set()
    for device in (session.power, session.heater):
        if device:
            device.inhibit_output()
    session.logger.log(reason)
    session.emit('measurement_fault', reason)
    session.emit('alarm', reason)
    session.emit('shutdown_pending', reason+'；正在关闭全部7722输出。')
    session.devices.shutdown_devices()
    session.emit('shutdown_confirmed', '测温故障保护：全部7722输出已确认OFF；驱动保持停止。')


def run(cfg, stop, emit, simulation=False, max_cycles=None, output_dir=None, profile=None, controls=None):
    import runtime  # local import avoids cycle with this module
    controls = controls or RunControls(True, True, profile, heater=cfg.heater_enabled)
    acquisition_cancel = AcquisitionCancel(controls.acquisition, stop)
    logger = Logger()
    devices = DeviceManager()
    session = Session(cfg, stop, emit, simulation, max_cycles, output_dir, profile, controls, acquisition_cancel, logger, devices)
    devices.session = session
    log_file = None
    watchdog = None
    try:
        cfg.validate()
        if controls.drive.is_set():
            candidate = controls.profile() or profile or runtime.load_profile(profile_path(cfg), cfg)
            candidate.validate(cfg)
            if candidate.strategy == 'temperature' and not controls.acquisition.is_set():
                raise ValueError('温度驱动方案需要先启动2700采集')
        folder = Path(output_dir or Path(__file__).parent/'data')
        folder.mkdir(parents=True, exist_ok=True)
        prefix = ('SIM_' if simulation else '')+datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        trace_path = folder/(prefix+'_communication.txt')
        logger.open(trace_path)
        logger.log('CONFIG '+json.dumps(asdict(cfg), ensure_ascii=False))
        log_file = CsvRecording(folder, simulation)
        session.log_file = log_file
        watchdog = runtime.AcquisitionWatchdog(lambda reason: measurement_fault(session, reason))
        session.watchdog = watchdog
        while not stop.is_set():
            start = time.monotonic()
            if controls.heater.is_set() and controls.heater_allowed() and not session.active_acquisition and not controls.acquisition.is_set():
                controls.start_acquisition()
                emit('acquisition_starting', '启动加热前，正在自动启动2700采集…')
            if (session.active_acquisition and (session.active_heater or controls.heater.is_set() or controls.heater_pending.is_set())
                    and not controls.acquisition.is_set()):
                controls.acquisition.set()
                emit('status', '加热启动或运行中，2700采集已锁定；请先停止加热并确认输出关闭。')
            ports = [port.strip().upper() for requested, port in
                     ((controls.drive.is_set() or session.active_drive, cfg.power_port),
                      (controls.heater.is_set() or session.active_heater, cfg.heater_port),
                      (controls.acquisition.is_set() or session.active_acquisition, cfg.meter_port)) if requested]
            if len(ports) != len(set(ports)):
                raise ValueError('正在运行或请求启动的设备不能使用同一串口；单台7722测试请只启动加热')
            if not controls.acquisition.is_set() and session.active_drive and session.drive_profile.strategy == 'temperature':
                controls.drive.clear()
                emit('status', '温度方案的采集已停止，同步停止驱动')
            heater_cancel_reason = ''
            if controls.heater.is_set() and controls.heater_link.is_set() and not controls.drive.is_set():
                controls.heater.clear()
                heater_cancel_reason = '加热联动保护：交流驱动未开启或已停止，加热保持停止。'
                emit('heater_status', heater_cancel_reason)
            if controls.take_cancelled('drive', session.active_drive):
                devices.close_drive()
                session.active_drive = False
                emit('drive_done', {'error': '', 'off_ok': True})
            if controls.take_cancelled('heater', session.active_heater):
                devices.close_heater()
                session.active_heater = False
                emit('heater_done', {'error': heater_cancel_reason, 'off_ok': True})
            if controls.take_cancelled('acquisition', session.active_acquisition):
                devices.close_acquisition()
                session.active_acquisition = False
                watchdog.disarm()
                session.last_sample_started = None
                emit('acquisition_done', {'error': ''})
            if not controls.acquisition.is_set() and not controls.drive.is_set() and not controls.heater.is_set():
                break
            if controls.drive.is_set() and not session.active_drive:
                drive_profile = controls.profile() or profile or runtime.load_profile(profile_path(cfg), cfg)
                drive_profile.validate(cfg)
                if drive_profile.strategy == 'temperature' and not controls.acquisition.is_set():
                    raise ValueError('温度驱动方案需要先启动2700采集')
                logger.log('PROFILE '+json.dumps(asdict(drive_profile), ensure_ascii=False))
                emit('status', '正在连接7722并检查驱动设置…')
                devices.start_drive(drive_profile)
            if controls.heater.is_set() and not session.active_heater and not controls.heater_allowed():
                controls.heater.clear()
                controls.heater_pending.clear()
                emit('heater_done', {'error': '加热联动保护：请先开启7722交流驱动输出。', 'off_ok': True})
            if controls.heater.is_set() and not session.active_heater:
                devices.start_heater()
            if controls.acquisition.is_set() and not session.active_acquisition:
                devices.start_acquisition()
            session.stopped()
            if session.epoch is None:
                session.epoch = time.monotonic()
                if session.active_drive:
                    session.drive_epoch = session.last = session.epoch
            samples, measurement_errors = run_acquisition(session)
            session.stopped()
            if not controls.drive.is_set() and session.active_drive:
                continue
            m, step, drive_elapsed = run_drive(session, samples)
            hm = run_heater(session, samples, measurement_errors)
            elapsed = (time.monotonic()-session.epoch)/60
            drive_fields = ([session.drive_profile.strategy, session.sequencer.index+1, step.mode, step.value,
                             session.controller.voltage, session.enabled, m['voltage'], m['current'], m['power'], m['power_factor']]
                            if m else ['']*10)
            for c, r, t, stamp, _ in samples:
                log_file.write(stamp, [c.channel, cfg.measurement_for(c.channel)[1], r,
                                     t+273.15 if t is not None else '',
                                     *drive_fields])
            if not samples and m:
                log_file.write(datetime.now(), ['', '', '', '', *drive_fields])
            if samples or m or hm:
                emit('sample', {'temperatures': {c.channel: t for c, r, t, stamp, at in samples},
                                'measurement_errors': measurement_errors,
                                'resistances': {c.channel: r for c, r, t, stamp, at in samples},
                                'sample_elapsed_s': {c.channel: at-session.epoch for c, r, t, stamp, at in samples},
                                'elapsed_min': elapsed, 'drive_elapsed_min': drive_elapsed,
                                'setting': session.controller.voltage if m else None, 'stage': session.sequencer.index+1 if m else None,
                                'drive_mode': step.mode if m else None, 'drive_target': step.value if m else None,
                                'output_on': session.enabled if m else None, 'strategy': session.drive_profile.strategy if m else None,
                                'voltage': m['voltage'] if m else None, 'current': m['current'] if m else None,
                                'power': m['power'] if m else None, 'power_factor': m['power_factor'] if m else None})
                session.cycles += 1
            if max_cycles is not None and session.cycles >= max_cycles:
                break
            if not session.active_acquisition:
                stop.wait(max(0, cfg.interval-(time.monotonic()-start)))
    except InterruptedError:
        pass
    except Exception as exc:
        session.error = str(exc)
        logger.log('ERROR '+session.error)
        emit('alarm', session.error)
    finally:
        controls.acquisition.clear()
        controls.drive.clear()
        controls.heater.clear()
        if watchdog:
            watchdog.close()
            if watchdog.reason:
                session.error = watchdog.reason
        try:
            devices.shutdown_devices()
            devices.close_heater()
            devices.close_drive()
        except Exception as exc:
            session.error = (session.error+'；' if session.error else '')+str(exc)
            emit('alarm', session.error)
        try:
            devices.close_acquisition()
        except Exception as exc:
            logger.log('2700清理失败: '+str(exc))
        for f in (log_file, logger.trace):
            if f:
                f.close()
        if session.active_drive or controls.drive_pending.is_set():
            controls.drive_pending.clear()
            emit('drive_done', {'error': session.error, 'off_ok': session.off_ok})
        if session.active_heater or controls.heater_pending.is_set():
            controls.heater_pending.clear()
            emit('heater_done', {'error': session.error, 'off_ok': session.off_ok})
        if session.active_acquisition or controls.acquisition_pending.is_set():
            controls.acquisition_pending.clear()
            emit('acquisition_done', {'error': session.error})
        emit('done', {'error': session.error, 'off_ok': session.off_ok})
