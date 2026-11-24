import unittest
from device_scan import scan_port


class FakeSerial:
    def __init__(self, reply):
        self.reply = bytearray(reply)
        self.sent = []
        self.closed = False
    def __enter__(self):
        return self
    def __exit__(self, *args):
        self.closed = True
    def reset_input_buffer(self):
        pass
    def write(self, payload):
        self.sent.append(payload)
        return len(payload)
    def read(self, size):
        return bytes([self.reply.pop(0)]) if self.reply else b''


class ScanTests(unittest.TestCase):
    def test_identity_and_read_only_query(self):
        profile = dict(baud=9600, bits=8, parity='N', stop=1, flow='none')
        for identity, expected in [('KEITHLEY,MODEL 2700,SN,1', '采集设备'),
                                   ('ITECH,IT-M7722,SN,1', '7722电源'),
                                   ('YOKOGAWA,GS610,SN,1', '其他设备')]:
            with self.subTest(identity=identity):
                port = FakeSerial((identity+'\r\n').encode())
                role, reply, matched = scan_port('COM9', [profile], lambda *a, **k: port)
                self.assertEqual((role, reply, matched), (expected, identity, profile))
                self.assertEqual(port.sent, [b'*IDN?\r\n'])
                self.assertTrue(port.closed)

    def test_duplicate_profiles_and_open_errors(self):
        calls = []
        def fail(*args, **kwargs):
            calls.append(args)
            raise OSError('port busy')
        profile = dict(baud=9600, bits=8, parity='N', stop=1, flow='none')
        self.assertEqual(scan_port('COM9', [profile, profile], fail), ('未识别', 'port busy', None))
        self.assertEqual(len(calls), 1)
