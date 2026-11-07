"""Identify a physical 7722 by changing its panel mode with output disabled."""
from instruments.transport7722 import SerialTransport
from power7722 import ITech7722


class PanelIdentificationError(RuntimeError):
    def __init__(self, message, off_confirmed):
        super().__init__(message)
        self.off_confirmed = off_confirmed


def identify_panel(record, mode, timeout=3., transport_factory=SerialTransport):
    if mode not in ('AC', 'DC') or record[1] != '7722电源':
        raise ValueError('仅可对7722指定AC/DC用途')
    name, _, identity, profile = record
    tp = None
    power = None
    off_confirmed = False
    try:
        tp = transport_factory(name, baudrate=int(profile['baud']), timeout=timeout)
        power = ITech7722(tp)
        power.output_off_confirmed()
        off_confirmed = True
        if power.identify().strip() != identity.strip():
            raise RuntimeError('设备身份已变化，请重新扫描串口')
        power.prepare_run()
        power.set_function_mode(mode)
        actual = power.raw('SOUR:NORM:MODE?').strip().upper()
        if actual != mode:
            raise RuntimeError(f'模式回读为 {actual!r}，未确认{mode}')
    except Exception as exc:
        raise PanelIdentificationError(str(exc), off_confirmed) from exc
    finally:
        if power is not None:
            try:
                power.output_off_confirmed()
                off_confirmed = True
            except Exception as exc:
                raise PanelIdentificationError('输出关闭未确认：'+str(exc), False) from exc
            finally:
                tp.close()
        elif tp is not None:
            tp.close()
