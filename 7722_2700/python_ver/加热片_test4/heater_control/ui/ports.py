"""Serial port utilities for the heater control UI."""
import re


def _port_sort_key(device):
    """Sort COM2 before COM10 (numeric segments compared as numbers)."""
    return [(0, int(part)) if part.isdigit() else (1, part.upper())
            for part in re.split(r'(\d+)', device)]


def serial_port_list():
    """Return [(device, description)] for serial ports currently attached."""
    try:
        from serial.tools import list_ports
    except Exception:
        return []
    found = [(p.device, getattr(p, 'description', '') or '无描述') for p in list_ports.comports()]
    found.sort(key=lambda item: _port_sort_key(item[0]))
    return found
