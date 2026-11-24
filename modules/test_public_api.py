import threading
import unittest
from unittest.mock import Mock

from itech7722 import ITECH7722, ITECHEngine, AppConfig, Recorder, SimTransport
from keithley2700 import Keithley2700, Keithley7700, Keithley7702, Settings


class PublicAPITests(unittest.TestCase):
    def test_itech_context_disables_output(self):
        tp = SimTransport()
        with ITECH7722(transport=tp) as dev:
            dev.prepare_run()
            dev.set_function_mode('DC')
            dev.set_protection_limits(5, 1, 5)
            dev.apply_setting('CV', 1, 0)
            dev.output_on()
            self.assertGreater(dev.measure()['voltage'], 0)
        self.assertFalse(tp.state['output'])
        dev.close()

    def test_itech_close_releases_transport_when_output_off_fails(self):
        tp = Mock()
        tp.write.side_effect = OSError('disconnected')
        with self.assertRaises(OSError):
            ITECH7722(transport=tp).close()
        tp.close.assert_called_once()

    def test_invalid_keithley_settings_never_open_port(self):
        factory = Mock()
        with self.assertRaises(ValueError):
            Keithley7702(channels=(143,), serial_factory=factory).open()
        factory.assert_not_called()

    def test_keithley_front_and_module_context(self):
        # Reuse original serial protocol emulators, through verify_original.py aliases.
        from test_acquisition import FakeSerial
        from test_7700 import ModuleSerial
        fake = FakeSerial()
        with Keithley2700(Settings(), serial_factory=lambda **kw: fake) as dev:
            self.assertEqual(dev.measure()[2], 'OK')
            with self.assertRaises(ValueError):
                dev.measure(101)
        self.assertTrue(fake.closed)
        fake = ModuleSerial(options='7702,7702')
        with Keithley7702(channels=(101, 140), settle=0,
                          serial_factory=lambda **kw: fake) as dev:
            self.assertEqual(set(dev.read_all()), {101, 140})
            with self.assertRaises(ValueError):
                dev.measure()
        self.assertTrue(fake.closed)
        dev.close()
        self.assertEqual(Keithley7700.valid_channels('直流电流 DCI', 1), (121, 122))
        self.assertEqual(Keithley7702.valid_channels('直流电流 DCI', 2), (241, 242))

    def test_headless_engine(self):
        import tempfile
        from pathlib import Path
        cfg = AppConfig()
        cfg.connection = {'type': 'sim'}
        with tempfile.TemporaryDirectory() as folder:
            cfg.recording['log_file'] = str(Path(folder) / 'data.csv')
            cfg.recording['keypoint_file'] = str(Path(folder) / 'key.csv')
            with ITECHEngine() as engine:
                self.assertIn('SIM7722', engine.connect(cfg))
                engine.start_run(cfg, [], Recorder(cfg))
                self.assertTrue(engine.stop())
                self.assertFalse(engine.running)
                self.assertFalse(engine.output_on)


if __name__ == '__main__':
    unittest.main()
