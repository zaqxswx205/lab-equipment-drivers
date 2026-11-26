"""Keithley 2700 meter driver."""
from __future__ import annotations

import math
import time
import threading
import library_bootstrap
from keithley2700 import ChannelConfig, Instrument, Settings

from instruments.serial_transport import open_serial
from model.pt100 import temperature
from power7722 import error_code


class Meter:
    def __init__(self, cfg, log):
        self.cfg, self.log = cfg, log
        # Keep slot-specific setup here: the shared high-level API only accepts
        # one card model per configuration. All serial I/O uses Instrument.
        self.instrument = Instrument(Settings(port=cfg.meter_port, baud=cfg.meter_baud,
                                             flow=cfg.meter_flow, timeout=cfg.timeout,
                                             input_source='7702'), threading.Event(),
                                     trace=lambda direction, data: log(f'2700 {direction} {data}'))
        self.instrument.serial = open_serial(cfg.meter_port, cfg.meter_baud, timeout=.05,
                                              write_timeout=cfg.timeout, xonxoff=cfg.meter_flow,
                                              rtscts=False, dsrdtr=False)
        self.ser = self.instrument.serial

    def write(self, cmd):
        return self.instrument.write(cmd)

    def query(self, cmd):
        try:
            return self.instrument.query(cmd)
        except TimeoutError as exc:
            detail = (f'2700 {cmd} 响应超时（{self.cfg.meter_port}, {self.cfg.meter_baud} baud，'
                      f'收到{len(self.instrument.rx)}字节）')
            self.log(detail + '；RX_HEX=' + self.instrument.rx.hex())
            raise TimeoutError(detail) from exc

    def errors(self, history=False):
        for _ in range(32):
            reply = self.query('SYST:ERR?')
            code = error_code(reply)
            if code == 0:
                return
            if code is None or not history:
                raise RuntimeError('2700 错误: '+reply)
            self.log('2700 历史错误: '+reply)
        raise RuntimeError('2700 错误队列未结束')

    def checked(self, cmd):
        return self.instrument.checked_write(cmd)

    def input_check(self):
        if float(self.query('SYST:FRSW?')) != 0:
            raise RuntimeError('请将2700 FRONT/REAR实体开关切至REAR')

    def setup(self):
        # Reset invalidates the shared driver's record of applied parameters.
        self.instrument.applied_parameters = None
        self.write('SYST:REM')
        model = self.query('*IDN?').split(',')
        if len(model) < 2 or model[1].strip().upper() not in ('2700', 'MODEL 2700'):
            raise RuntimeError('连接设备不是Keithley 2700')
        self.errors(history=True)
        self.input_check()
        modules = [m.strip(' "') for m in self.query('*OPT?').split(',')]
        for c in self.cfg.channels:
            slot = c.channel//100-1
            module, _ = self.cfg.measurement_for(c.channel)
            if len(modules) <= slot or modules[slot] != str(module):
                actual = modules[slot] if len(modules) > slot else '未返回槽位信息'
                raise RuntimeError(
                    f'槽位 {slot+1} 配置为 {module}，设备实际识别为 {actual}。'
                    f'请将测温通道中的 Slot {slot+1} 模块设置与实际模块一致，再开始采集。')
        for cmd in ('ABOR', '*RST', '*CLS', 'INIT:CONT OFF', 'ROUT:SCAN:LSEL NONE',
                    'ROUT:CLOS:ACON OFF', 'ROUT:OPEN:ALL', 'TRIG:SOUR IMM',
                    'TRIG:COUN 1', 'SAMP:COUN 1', 'FORM:DATA ASC', 'FORM:ELEM READ,CHAN'):
            self.checked(cmd)

    def read(self, c, stop):
        self.input_check()
        self.checked('ROUT:OPEN:ALL')
        _, wires = self.cfg.measurement_for(c.channel)
        parameters = ChannelConfig(c.channel, '四线电阻 Ω4' if wires == 4 else '两线电阻 Ω2',
                                   'AUTO', self.cfg.nplc, self.cfg.settle)
        try:
            self.instrument.apply_parameters(parameters)
        except Exception:
            # A partially applied change must never leave a usable stale cache.
            self.instrument.applied_parameters = None
            raise
        self.checked(f'ROUT:CLOS (@{c.channel})')
        if stop.wait(self.cfg.settle):
            raise InterruptedError('已停止')
        parts = self.query('READ?').split(',')
        if len(parts) != 2 or float(parts[1]) != c.channel:
            raise RuntimeError(f'2700返回通道不匹配: {parts}')
        try:
            r = float(parts[0])
        except ValueError:
            r = parts[0].strip()
        try:
            t = temperature(r - c.lead_ohm, c.sensor_type, c.sensor_curve)
        except (ValueError, TypeError):
            t = None
        self.errors()
        return r, t

    def close(self):
        self.instrument.close()
