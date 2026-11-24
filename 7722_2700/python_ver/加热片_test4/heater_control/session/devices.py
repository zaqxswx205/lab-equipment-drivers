"""Device lifecycle: open, shutdown, and close for drive/heater/acquisition."""
from __future__ import annotations

import time
from dataclasses import replace
from datetime import datetime

import core
from profile.controller import DriveController
from profile.model import Step
from profile.sequencer import Sequencer


class DeviceManager:
    def __init__(self):
        self.session = None

    def start_drive(self, drive_profile):
        s = self.session
        cfg = s.cfg
        s.drive_profile = drive_profile
        tp = core.SimTransport() if s.simulation else core.SerialTransport(cfg.power_port, cfg.power_baud, timeout=cfg.timeout)
        power = core.ITech7722(tp, log=lambda d, msg: s.logger.log('7722 '+d+' '+msg))
        s.power = power
        model = power.identify().split(',')
        allowed = ('SIM7722',) if s.simulation else ('IT-M7722', 'M7722', 'IT7722', 'IT-7722')
        if len(model) < 2 or model[1].strip().upper() not in allowed:
            raise RuntimeError('电源型号不是7722，拒绝开启输出')
        power.prepare_run()
        power.output_off_confirmed(delay=0 if s.simulation else .2)
        s.stopped()
        power.set_function_mode('AC')
        power.set_protection_limits(cfg.max_voltage, cfg.max_current, cfg.max_power)
        power.apply_setting('CV', 0, cfg.frequency)
        s.controller = DriveController(cfg)
        s.sequencer = Sequencer(drive_profile)
        s.enabled = False
        s.drive_epoch = s.last = time.monotonic()
        s.active_drive = True

    def start_heater(self):
        s = self.session
        cfg = s.cfg
        replace(cfg, heater_enabled=True).validate()
        s.emit('heater_status', '正在连接7722加热负载并检查DC设置…')
        allowed = ('SIM7722',) if s.simulation else ('IT-M7722', 'M7722', 'IT7722', 'IT-7722')
        s.heater_cfg = replace(cfg, max_voltage=cfg.heater_max_voltage,
                               max_current=cfg.heater_max_current, max_power=cfg.heater_max_power)
        tp = core.SimTransport() if s.simulation else core.SerialTransport(cfg.heater_port, cfg.heater_baud, timeout=cfg.timeout)
        heater = core.ITech7722(tp, log=lambda d, msg: s.logger.log('加热7722 '+d+' '+msg))
        s.heater = heater
        model = heater.identify().split(',')
        if len(model) < 2 or model[1].strip().upper() not in allowed:
            raise RuntimeError('加热电源型号不是7722，拒绝开启输出')
        heater.prepare_run()
        heater.output_off_confirmed(delay=0 if s.simulation else .2)
        s.stopped()
        heater.set_function_mode('DC')
        heater.set_protection_limits(cfg.heater_max_voltage, cfg.heater_max_current, cfg.heater_max_power)
        heater.apply_setting('CV', 0, 0.)
        s.heater_controller = DriveController(s.heater_cfg)
        s.heater_enabled = False
        s.heater_last = time.monotonic()-cfg.interval
        s.active_heater = True
        if cfg.heater_trigger_action == 'on':
            s.temperature_trigger_used = True
        s.emit('heater_starting', '7722加热负载已连接，正在启动DC输出…')

    def start_acquisition(self):
        s = self.session
        cfg = s.cfg
        s.emit('status', '正在连接2700并检查采集设置…')
        s.watchdog.expect(max(15., cfg.timeout*5), '2700连接和初始化')
        try:
            s.meter = core.SimMeter(cfg, s.power) if s.simulation else core.Meter(cfg, s.logger.log)
            s.meter.setup()
        except Exception as exc:
            s.watchdog.trip(f'2700连接或初始化失败：{exc}')
            raise
        s.stopped()
        s.log_file.start(datetime.now())
        s.active_acquisition = True
        s.watchdog.expect(cfg.interval+cfg.settle+max(5., cfg.timeout*3), '等待下一通道采样')

    def shutdown_devices(self, selected=None):
        s = self.session
        pending = [(name, device) for name, device in (('驱动7722', s.power), ('加热7722', s.heater))
                   if device and (selected is None or device in selected)]
        for name, device in pending:
            try:
                device.raw('OUTP:STAT OFF')
            except Exception as exc:
                s.logger.log(name+' OFF发送失败: '+str(exc))
        warned = False
        while pending:
            remaining = []
            for name, device in pending:
                try:
                    device.output_off_confirmed(attempts=1, delay=0 if s.simulation else .2)
                except Exception as exc:
                    remaining.append((name, device))
                    s.off_ok = False
                    message = f'{name}关输出尚未确认，请手动关闭电源；后台持续重试：{exc}'
                    s.logger.log(message)
                    s.emit('shutdown_pending', message)
                    warned = True
            pending = remaining
            if pending:
                time.sleep(1)
        s.off_ok = True
        if warned:
            s.emit('shutdown_confirmed', '全部7722输出均已确认OFF。')

    def close_drive(self):
        s = self.session
        s.controls.drive_ready.clear()
        if s.watchdog and s.watchdog.reason:
            s.watchdog.close()
        if s.power:
            s.controls.drive.clear()
            self.shutdown_devices([s.power])
            try:
                s.power.close()
            finally:
                s.power = None
                s.enabled = False

    def close_heater(self):
        s = self.session
        if s.watchdog and s.watchdog.reason:
            s.watchdog.close()
        if s.heater:
            s.controls.heater.clear()
            self.shutdown_devices([s.heater])
            try:
                s.heater.close()
            finally:
                s.heater = None
                s.heater_enabled = False

    def close_acquisition(self):
        s = self.session
        if s.meter:
            try:
                s.meter.close()
            finally:
                s.meter = None
