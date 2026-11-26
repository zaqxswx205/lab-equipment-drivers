"""Verify project-local driver loading and safety across the library boundary."""
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import library_bootstrap
import itech7722
import keithley2700
from power7722 import ITech7722, SimTransport
from instruments.meter2700 import Meter
from keithley2700 import Instrument
from core import Config

class SharedDriverTests(unittest.TestCase):
    def test_drivers_are_loaded_from_project_directory(self):
        expected = Path(__file__).resolve().parents[1] / 'modules'
        self.assertEqual(library_bootstrap.MODULES_DIR, expected)
        for driver in (itech7722, keithley2700):
            self.assertEqual(Path(driver.__file__).resolve().parent, expected)

    def test_library_voltage_path_cannot_bypass_inhibit(self):
        transport = SimTransport()
        power = ITech7722(transport)
        power.set_protection_limits(5, 1, 5)
        power.inhibit_output()
        before = list(transport.commands)
        with self.assertRaisesRegex(RuntimeError, '锁止'):
            itech7722.ITech7722.set_voltage(power, 1)
        self.assertEqual(transport.commands, before)
        power.output_off_confirmed(delay=0)

    def test_meter_uses_shared_checked_write(self):
        with patch('serial.Serial'):
            meter = Meter(Config(), lambda message: None)
            try:
                self.assertIsInstance(meter.instrument, Instrument)
                with patch.object(Instrument, 'checked_write') as checked:
                    meter.checked('ROUT:OPEN:ALL')
                    checked.assert_called_once_with('ROUT:OPEN:ALL')
            finally:
                meter.close()
