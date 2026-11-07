"""7722 serial transport and offline simulation transport."""
from __future__ import annotations

import time


class SerialTransport:
    """8N1、CRLF发送；接收兼容CR/LF和无结束符的空闲收尾。"""
    name = 'serial'

    def __init__(self, port, baudrate=9600, timeout=3.):
        import serial
        self.timeout = float(timeout)
        self.serial = serial.Serial(port=port, baudrate=baudrate, bytesize=8,
                                    parity='N', stopbits=1, timeout=.05,
                                    write_timeout=self.timeout, xonxoff=False,
                                    rtscts=False, dsrdtr=False)

    def write(self, command):
        if not command or any(ord(c) < 32 or ord(c) > 126 for c in command):
            raise ValueError('SCPI指令必须是单行ASCII')
        payload = (command+'\r\n').encode('ascii')
        if self.serial.write(payload) != len(payload):
            raise IOError('7722串口发送不完整')
        self.serial.flush()

    def query(self, command):
        self.write(command)
        data = bytearray()
        deadline = time.monotonic() + self.timeout
        last_byte = time.monotonic()
        while time.monotonic() < deadline:
            byte = self.serial.read(1)
            now = time.monotonic()
            if byte in (b'\r', b'\n'):
                if data:
                    return data.decode('ascii').strip()
            elif byte:
                data.extend(byte)
                last_byte = now
                if len(data) > 4096:
                    raise IOError('7722响应过长')
            elif data and now-last_byte >= .1:
                return data.decode('ascii').strip()
        raise TimeoutError(f'7722 {command} 响应超时或不完整')

    def close(self):
        self.serial.close()


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
