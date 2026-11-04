"""Keithley 2700 meter driver."""
from __future__ import annotations

import math
import time

from model.pt100 import temperature
from power7722 import error_code


class Meter:
    def __init__(self, cfg, log):
        import serial
        self.cfg, self.log = cfg, log
        self.ser = serial.Serial(cfg.meter_port, cfg.meter_baud, timeout=.05,
                                 write_timeout=cfg.timeout, xonxoff=cfg.meter_flow,
                                 rtscts=False, dsrdtr=False)
        self.log(f'2700 串口已打开: {cfg.meter_port}, {cfg.meter_baud} baud, 8N1, '
                 f'FLOW={"XON/XOFF" if cfg.meter_flow else "NONE"}, TX=CR')

    def write(self, cmd):
        self.log('2700 >> ' + cmd)
        self.ser.write((cmd+'\r').encode('ascii'))
        self.ser.flush()

    def query(self, cmd):
        self.write(cmd)
        buf = bytearray()
        deadline = time.monotonic() + self.cfg.timeout
        while time.monotonic() < deadline:
            b = self.ser.read(1)
            if b in (b'\r', b'\n'):
                if buf:
                    reply = buf.decode('ascii').strip()
                    self.log('2700 << '+reply)
                    return reply
            elif b:
                buf.extend(b)
                if len(buf) > 4096:
                    raise RuntimeError('2700 响应过长')
        detail = (f'2700 {cmd} 响应超时（{self.cfg.meter_port}, {self.cfg.meter_baud} baud，'
                  f'收到{len(buf)}字节）')
        self.log(detail+'；RX_HEX='+buf.hex())
        if not buf:
            detail += '；请确认设备RS-232为ON，并核对BAUD、FLOW及串口接线'
        else:
            detail += '；已收到部分数据但未收到结束符，请核对波特率和Tx TERM'
        raise TimeoutError(detail)

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
        self.write(cmd)
        if self.query('*OPC?') != '1':
            raise RuntimeError('2700 未确认完成: '+cmd)
        self.errors()

    def input_check(self):
        if float(self.query('SYST:FRSW?')) != 0:
            raise RuntimeError('请将2700 FRONT/REAR实体开关切至REAR')

    def setup(self):
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
        func = 'FRES' if wires == 4 else 'RES'
        for cmd in (f"SENS:FUNC '{func}'", f'SENS:{func}:RANG:AUTO ON',
                    f'SENS:{func}:NPLC {self.cfg.nplc:g}', f'ROUT:CLOS (@{c.channel})'):
            self.checked(cmd)
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
            t = temperature(r - c.lead_ohm)
        except (ValueError, TypeError):
            t = None
        self.errors()
        return r, t

    def close(self):
        try:
            for cmd in ('ABOR', 'ROUT:OPEN:ALL', 'SYST:LOC'):
                self.write(cmd)
        finally:
            self.ser.close()
