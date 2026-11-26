"""统一串口通信：端口创建、7722空闲帧接收、2700行帧接收。"""
import time

def open_serial(*args, **kwargs):
    import serial
    return serial.Serial(*args, **kwargs)

class SerialTransport:
    name = "serial"

    def __init__(self, port, baudrate=9600, bytesize=8, parity="N", stopbits=1,
                 timeout=2.0, terminator="\r\n"):
        self._term = terminator
        self._timeout = float(timeout)
        self._ser = open_serial(
            port=port, baudrate=baudrate, bytesize=bytesize, parity=parity,
            stopbits=stopbits, timeout=0.05, write_timeout=self._timeout,
        )

    def write(self, cmd):
        self._ser.write((cmd + self._term).encode("ascii", "replace"))
        self._ser.flush()

    def read(self):
        """按“字节流空闲”收尾，不依赖换行符。

        ITECH 部分机型以 \\r 结尾、也有不分块回送的，用 readline() 会一直等到
        超时返回空串，界面就表现为“自检没反应”。
        """
        buf = bytearray()
        start = last = time.monotonic()
        while True:
            if time.monotonic() - start >= self._timeout:
                break
            waiting = self._ser.in_waiting
            if waiting:
                buf.extend(self._ser.read(waiting))
                last = time.monotonic()
                continue
            now = time.monotonic()
            if buf and now - last >= 0.08:
                break
            if now - start >= self._timeout:
                break
            time.sleep(0.005)
        return bytes(buf).decode("ascii", "replace").strip()

    def query(self, command):
        self.write(command)
        return self.read()

    def close(self):
        try:
            self._ser.close()
        except Exception:
            pass

    @property
    def is_open(self):
        return bool(getattr(self, "_ser", None) and self._ser.is_open)


class LineProtocol:
    def write(self, command):
        # RS-232 input requires CR (manual 10-19). TX TERM only controls
        # instrument replies; it must not select our outgoing terminator.
        if not command or any(not 32 <= ord(char) <= 126 for char in command):
            raise ValueError("SCPI 命令必须为单行可打印 ASCII 字符")
        payload = (command + "\r").encode("ascii")
        self.trace("TX", repr(payload))
        self.serial.write(payload)

    def query(self, command):
        if self.stop.is_set():
            raise InterruptedError('已停止')
        self.write(command)
        self.pending_query = True
        deadline = time.monotonic() + self.settings.timeout
        while time.monotonic() < deadline:
            if self.stop.is_set():
                raise InterruptedError('已停止')
            # Accept CR, LF and CRLF, including terminators split over reads.
            for i, byte in enumerate(self.rx):
                if byte in (10, 13):
                    line = bytes(self.rx[:i]).strip()
                    del self.rx[:i + 1]
                    if line:
                        response = line.decode("ascii", errors="strict")
                        self.pending_query = False
                        self.trace("RX", repr(response))
                        return response
                    break
            else:
                chunk = self.serial.read(1)
                self.rx.extend(chunk)
                if len(self.rx) > 8192:
                    raise RuntimeError("串口响应过长，请检查串口设置")
        raise TimeoutError(f"{command} 响应超时；检查波特率、结束符、FLOW 和接线")



def scan_port(name, profiles, serial_factory=None):
    if serial_factory is None:
        serial_factory = open_serial
    errors = []
    tried = set()
    for c in profiles:
        signature = tuple(c[k] for k in ('baud', 'bits', 'parity', 'stop', 'flow'))
        if signature in tried:
            continue
        tried.add(signature)
        try:
            with serial_factory(name, int(c['baud']), bytesize=int(c['bits']), parity=c['parity'],
                                stopbits=int(c['stop']), timeout=0.1, write_timeout=1,
                                xonxoff=c['flow'] == 'xon', rtscts=c['flow'] == 'rtscts') as port:
                port.reset_input_buffer()
                # CRLF supports CR-terminated and LF-terminated SCPI devices.
                payload = b'*IDN?\r\n'
                if port.write(payload) != len(payload):
                    raise TimeoutError('身份查询发送不完整')
                response = bytearray()
                deadline = time.monotonic() + 1.5
                while time.monotonic() < deadline:
                    chunk = port.read(1)
                    if chunk in (b'\r', b'\n'):
                        if response:
                            break
                    elif chunk:
                        response.extend(chunk)
                    if len(response) > 1024:
                        raise ValueError('响应过长')
                else:
                    raise TimeoutError('身份查询无完整响应')
                identity = response.decode('ascii').strip()
                parts = [v.strip().upper() for v in identity.split(',')]
                if len(parts) < 2:
                    raise ValueError('不是标准身份响应')
                role = '采集设备' if parts[1] in ('2700', 'MODEL 2700') else '7722电源' if parts[1] in ('7722', 'IT7722', 'IT-M7722', 'IT-M7722L', 'IT-M7722H') else '其他设备'
                return role, identity, dict(c)
        except Exception as exc:
            errors.append(str(exc))
    return '未识别', '；'.join(dict.fromkeys(errors)), None
