"""本项目独立实现的7722串口、CV控制及离线模拟。

实现电压源功能；制冷机功率模式由阶段驱动层根据有功功率反馈调压。
"""
from __future__ import annotations

import math
import re
import time
import threading

from instruments.transport7722 import SerialTransport, SimTransport


def error_code(reply):
    match = re.match(r'^\s*([+-]?\d+)\s*,', reply or '')
    return int(match[1]) if match else None


def number(value):
    value = float(value)
    if not math.isfinite(value):
        raise ValueError('电源参数必须为有限数值')
    return format(value, '.6f').rstrip('0').rstrip('.') or '0'


class ITech7722:
    """电压源接口。正常写入后检查错误队列，关断可独立执行。"""
    def __init__(self, transport, log=None):
        self.tp = transport
        self.log = log or (lambda direction, message: None)
        self.checked_writes = False
        self._io_lock = threading.RLock()
        self._inhibited = threading.Event()
        self.setting = {'mode': 'AC', 'value': 0., 'frequency': 50.}
        self.limits = {'max_voltage': 0., 'max_current': 0., 'max_power': 0.}

    def raw(self, command):
        with self._io_lock:
            return self._raw_locked(command)

    def inhibit_output(self):
        # 先锁存，再等待正在进行的串口事务；后续ON/调压均拒绝。
        self._inhibited.set()

    def _raw_locked(self, command):
        if self._inhibited.is_set() and not command.endswith('?') and command != 'OUTP:STAT OFF':
            raise RuntimeError('测温保护已锁止电源，禁止重新开启或调整输出')
        self.log('>>', command)
        if command.endswith('?'):
            reply = self.tp.query(command)
            self.log('<<', reply)
            return reply
        self.tp.write(command)
        return ''

    def error_queue(self, history=False):
        previous = []
        for _ in range(32):
            reply = self.raw('SYST:ERR?')
            code = error_code(reply)
            if code is None:
                raise RuntimeError('7722错误队列响应无效: '+reply)
            if code == 0:
                if previous and not history:
                    raise RuntimeError('7722拒绝指令: '+'; '.join(previous))
                return previous
            previous.append(reply)
            if history:
                self.log('历史错误', reply)
        raise RuntimeError('7722错误队列未正常结束')

    def write(self, command):
        self.raw(command)
        if self.checked_writes:
            self.error_queue()

    def identify(self):
        return self.raw('*IDN?')

    def prepare_run(self):
        self.error_queue(history=True)
        self.checked_writes = True
        self.write('SYST:REM')

    def set_function_mode(self, mode):
        if mode not in ('AC', 'DC'):
            raise ValueError('仅支持AC/DC输出模式')
        self.write('SOUR:NORM:MODE '+mode)
        self.setting['mode'] = mode

    def set_protection_limits(self, max_voltage, max_current, max_power):
        values = (max_voltage, max_current, max_power)
        if any(not math.isfinite(v) or not 0 < v <= rating
               for v, rating in zip(values, (300, 6, 600))):
            raise ValueError('7722保护参数超出300V/6A/600W有效范围')
        for cmd, v in zip(('PROT:RMS:VOLT', 'PROT:RMS:CURR', 'PROT:POW'), values):
            self.write(cmd+' '+number(v))
        self.limits = dict(zip(('max_voltage', 'max_current', 'max_power'), values))

    def set_voltage(self, voltage):
        if not math.isfinite(voltage) or not 0 <= voltage <= self.limits['max_voltage']:
            raise ValueError('电压设定超过配置上限')
        self.write(f"SOUR:NORM:VOLT:{self.setting['mode']} {number(voltage)}")
        self.setting['value'] = voltage

    def apply_setting(self, control, value, frequency):
        if control != 'CV':
            raise ValueError('电源接口只支持CV；功率目标由软件反馈调压实现')
        if self.setting['mode'] == 'AC' and (not math.isfinite(frequency) or frequency <= 0):
            raise ValueError('交流频率必须为有限正数')
        self.set_voltage(value)
        self.write('PROT:MAX:CURR:LIM '+number(self.limits['max_current']))
        if self.setting['mode'] == 'AC':
            self.write('SOUR:NORM:FREQ '+number(frequency))
        self.setting['frequency'] = frequency if self.setting['mode'] == 'AC' else 0.

    def output_on(self):
        self.write('OUTP:STAT ON')

    def output_off(self):
        self.write('OUTP:STAT OFF')

    def output_off_confirmed(self, attempts=5, delay=.2):
        """等待继电器状态更新；仅明确的OFF回读可以确认关断。"""
        checked = self.checked_writes
        self.checked_writes = False
        failure = '未收到OFF确认'
        try:
            for attempt in range(attempts):
                try:
                    self.output_off()
                    time.sleep(delay)
                    reply = self.raw('OUTP:STAT?').strip().upper()
                    if reply == 'OFF' or reply in ('0', '+0', '0.0', '+0.0'):
                        self.log('关断确认', f'第{attempt+1}次已确认OFF')
                        return
                    failure = f'OUTP:STAT? 返回 {reply!r}，未确认OFF'
                except Exception as exc:
                    failure = str(exc)
                self.log('关断重试', f'第{attempt+1}次: {failure}')
            raise RuntimeError(failure)
        finally:
            self.checked_writes = checked

    def measured_number(self, command, optional=False):
        reply = self.raw(command)
        try:
            value = float(reply)
            if math.isfinite(value):
                return value
        except (TypeError, ValueError):
            pass
        if optional:
            return None
        raise RuntimeError(f'7722测量响应无效: {command} = {reply!r}')

    def measure(self, mode=None):
        mode = mode or self.setting['mode']
        if mode not in ('AC', 'DC'):
            raise ValueError('无效测量模式')
        result = {
            'voltage': self.measured_number(f'MEAS:VOLT:{mode}?'),
            'current': self.measured_number(f'MEAS:CURR:{mode}?'),
            'power': self.measured_number('MEAS:POW?'),
            'power_factor': self.measured_number('MEAS:POW:PFAC?', optional=True),
        }
        if result['power_factor'] is not None and abs(result['power_factor']) > 1:
            result['power_factor'] = None
        return result

    def close(self):
        self.tp.close()
