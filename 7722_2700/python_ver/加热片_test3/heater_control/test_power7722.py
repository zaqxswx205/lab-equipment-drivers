import unittest
from unittest.mock import patch

from power7722 import ITech7722, SerialTransport, SimTransport, error_code


class PowerTests(unittest.TestCase):
    def test_shutdown_waits_through_on_and_confirms_off(self):
        tp = SimTransport()
        power = ITech7722(tp)
        power.checked_writes = True
        replies = iter(('ON', 'ON', 'OFF'))
        with patch.object(tp, 'query', side_effect=lambda cmd: next(replies)), patch('power7722.time.sleep') as wait:
            power.output_off_confirmed()
        self.assertEqual(tp.commands.count('OUTP:STAT OFF'), 3)
        self.assertEqual(wait.call_count, 3)
        self.assertTrue(power.checked_writes)
        self.assertFalse(tp.closed)

    def test_shutdown_unknown_or_on_does_not_claim_off(self):
        for reply in ('ON', '1', '', 'nan', 'unknown'):
            with self.subTest(reply=reply):
                power = ITech7722(SimTransport())
                with patch.object(power.tp, 'query', return_value=reply), patch('power7722.time.sleep'):
                    with self.assertRaisesRegex(RuntimeError, '未确认OFF'):
                        power.output_off_confirmed()

    def test_ac_dc_measure_and_shutdown(self):
        for mode in ('AC', 'DC'):
            with self.subTest(mode=mode):
                tp = SimTransport()
                power = ITech7722(tp)
                power.prepare_run()
                power.output_off()
                power.set_function_mode(mode)
                power.set_protection_limits(5, 1, 20)
                power.apply_setting('CV', 3, 50)
                power.output_on()
                measured = power.measure()
                self.assertEqual(measured['voltage'], 3)
                self.assertAlmostEqual(measured['current'], .06)
                self.assertAlmostEqual(measured['power'], .18)
                power.output_off()
                self.assertEqual(power.raw('OUTP:STAT?'), '0')
                self.assertIn(f'SOUR:NORM:VOLT:{mode} 3', tp.commands)

    def test_device_error_and_invalid_measurement(self):
        tp = SimTransport()
        power = ITech7722(tp)
        power.prepare_run()
        with self.assertRaisesRegex(RuntimeError, '拒绝指令'):
            power.write('INVALID 1')
        tp.query = lambda cmd: 'nan'
        with self.assertRaisesRegex(RuntimeError, '测量响应无效'):
            power.measure()
        with self.assertRaisesRegex(RuntimeError, '错误队列响应无效'):
            power.error_queue()

    def test_limits_before_write(self):
        tp = SimTransport()
        power = ITech7722(tp)
        for values in ((301, 1, 20), (5, 7, 20), (5, 1, float('nan'))):
            with self.assertRaises(ValueError):
                power.set_protection_limits(*values)
        self.assertEqual(tp.commands, [])
        power.set_protection_limits(5, 1, 20)
        for value in (-1, 6, float('inf')):
            with self.assertRaises(ValueError):
                power.set_voltage(value)

    def test_serial_crlf_and_reply(self):
        class Port:
            def __init__(self, **kw):
                self.reply = bytearray(b'\r\nITECH,IT-M7722,SN,1.0\r\n')
                self.sent = []
            def write(self, data):
                self.sent.append(data)
                return len(data)
            def flush(self):
                pass
            def read(self, n):
                data = self.reply[:n]
                del self.reply[:n]
                return bytes(data)
            def close(self):
                self.closed = True
        with patch('serial.Serial', Port):
            tp = SerialTransport('COM9')
            self.assertEqual(tp.query('*IDN?'), 'ITECH,IT-M7722,SN,1.0')
            self.assertEqual(tp.serial.sent, [b'*IDN?\r\n'])
            with self.assertRaises(ValueError):
                tp.write('OUTP ON\nOUTP OFF')
            tp.close()
            self.assertTrue(tp.serial.closed)

    def test_error_code(self):
        self.assertEqual(error_code('-113,"Undefined header"'), -113)
        self.assertEqual(error_code(' 0,"No error"'), 0)
        for reply in ('', 'garbage', '0bad', None):
            self.assertIsNone(error_code(reply))


if __name__ == '__main__':
    unittest.main()
