"""Read-only SCPI identity discovery using the configured serial profiles."""
import time


def scan_port(name, profiles, serial_factory=None):
    if serial_factory is None:
        from serial import Serial
        serial_factory = Serial
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
