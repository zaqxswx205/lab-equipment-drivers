"""Shared 7722 serial transport, with application protocol validation."""
from __future__ import annotations
from instruments.serial_transport import SerialTransport as LibrarySerialTransport

class SerialTransport(LibrarySerialTransport):
    def __init__(self, port, baudrate=9600, timeout=3.):
        super().__init__(port, baudrate=baudrate, timeout=timeout, terminator="\r\n")
        self.serial = self._ser

    def write(self, command):
        if not command or any(ord(c) < 32 or ord(c) > 126 for c in command):
            raise ValueError('SCPI指令必须是单行ASCII')
        super().write(command)

    def query(self, command):
        reply = super().query(command)
        if not reply:
            raise TimeoutError(f'7722 {command} 响应超时或不完整')
        return reply

class SimTransport:
    """本地确定性电阻负载模型，不导入任何旧项目模块。"""
    name = 'sim'

    def __init__(self):
        self.state = {'output': False, 'mode': 'AC', 'voltage': 0., 'frequency': 50.,
                      'current_limit': 1., 'ovp': 300., 'ocp': 6., 'opp': 600.}
        self.commands = []
        self.errors = []
        self.closed = False

    def write(self, command):
        self.commands.append(command)
        if command == 'SYST:REM':
            return
        key, _, argument = command.partition(' ')
        if key == 'OUTP:STAT':
            self.state['output'] = argument in ('ON', '1')
        elif key == 'SOUR:NORM:MODE':
            self.state['mode'] = argument
        elif key in ('SOUR:NORM:VOLT:AC', 'SOUR:NORM:VOLT:DC'):
            self.state['voltage'] = float(argument)
        else:
            field = {'SOUR:NORM:FREQ': 'frequency', 'PROT:MAX:CURR:LIM': 'current_limit',
                     'PROT:RMS:VOLT': 'ovp', 'PROT:RMS:CURR': 'ocp', 'PROT:POW': 'opp'}.get(key)
            if field:
                self.state[field] = float(argument)
            else:
                self.errors.append('-113,"Undefined header"')

    def query(self, command):
        self.commands.append(command)
        if command == '*IDN?':
            return 'ITECH,SIM7722,LOCAL,1.0'
        if command == 'SYST:ERR?':
            return self.errors.pop(0) if self.errors else '0,"No error"'
        if command == 'SOUR:NORM:MODE?':
            return self.state['mode']
        if command == 'OUTP:STAT?':
            return '1' if self.state['output'] else '0'
        v = min(self.state['voltage'], self.state['ovp']) if self.state['output'] else 0.
        i = min(v/50., self.state['current_limit'], self.state['ocp'])
        i = min(i, self.state['opp']/v) if v else 0.
        readings = {'MEAS:POW?': v*i, 'MEAS:POW:PFAC?': 1. if v*i else 0.}
        readings.update({f'MEAS:VOLT:{mode}?': v for mode in ('AC', 'DC')})
        readings.update({f'MEAS:CURR:{mode}?': i for mode in ('AC', 'DC')})
        if command not in readings:
            raise RuntimeError('模拟器不支持指令: '+command)
        return str(readings[command])

    def close(self):
        self.closed = True
