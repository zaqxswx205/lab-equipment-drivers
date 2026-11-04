import threading
import unittest
from contextlib import redirect_stdout
import io
from decimal import Decimal
from unittest.mock import patch

from test_sweep import Instrument
import gs610_sweep as core


class RealtimeTests(unittest.TestCase):
    def test_four_wire_enabled_and_verified_before_output(self):
        instrument = Instrument()
        with redirect_stdout(io.StringIO()):
            core.GS610(instrument).sweep([Decimal(0)], Decimal('.002'), Decimal(12), 0)
        self.assertTrue(instrument.remote_sense)
        self.assertLess(instrument.commands.index(':SENS:RSEN ON'),
                        instrument.commands.index(':SENS:RSEN?'))
        self.assertLess(instrument.commands.index(':SENS:RSEN?'),
                        instrument.commands.index(':OUTP ON'))
        self.assertFalse(instrument.output)

    def test_failed_four_wire_switch_never_enables_output(self):
        class RefusesRemoteSense(Instrument):
            def query(self, command):
                response = super().query(command)
                return '0' if command == ':SENS:RSEN?' else response
        instrument = RefusesRemoteSense()
        with redirect_stdout(io.StringIO()), self.assertRaisesRegex(RuntimeError, '4W'):
            core.GS610(instrument).sweep([Decimal(0)], Decimal('.002'), Decimal(12), 0)
        self.assertNotIn(':OUTP ON', instrument.commands)
        self.assertFalse(instrument.output)

    def test_callback_after_csv_save(self):
        import csv
        import tempfile
        from pathlib import Path
        points, source_range, limit = core.plan_sweep('0', '.002', '.001', '12', False)
        seen = []
        with tempfile.TemporaryDirectory() as folder, redirect_stdout(io.StringIO()):
            path = Path(folder) / 'readings.csv'
            def callback(record):
                with path.open(encoding='utf-8-sig', newline='') as stream:
                    rows = list(csv.DictReader(stream))
                self.assertEqual(len(rows), record[0])
                seen.append(record)
            result = core.GS610(Instrument()).sweep(points, source_range, limit, 0,
                                                   csv_path=path, on_record=callback)
        self.assertEqual(result, seen)
        self.assertEqual(len(seen), 3)

    def test_cancel_after_first_reading_disables_output(self):
        instrument = Instrument()
        stop = threading.Event()
        points, source_range, limit = core.plan_sweep('0', '.002', '.001', '12')
        with redirect_stdout(io.StringIO()), self.assertRaises(core.SweepCancelled):
            core.GS610(instrument).sweep(points, source_range, limit, 0, keep_output=True,
                                        on_record=lambda row: stop.set(), stop_event=stop)
        self.assertFalse(instrument.output)
        self.assertEqual(instrument.commands.count(':READ?'), 1)

    def test_cancel_during_dwell_does_not_measure(self):
        instrument = Instrument()
        class StopAtDwell:
            def is_set(self):
                return False
            def wait(self, seconds):
                return True
        with redirect_stdout(io.StringIO()), self.assertRaises(core.SweepCancelled):
            core.GS610(instrument).sweep([Decimal(0)], Decimal('.002'), Decimal(12),
                                        30, stop_event=StopAtDwell())
        self.assertFalse(instrument.output)
        self.assertNotIn(':READ?', instrument.commands)

    def test_windows_port_rejects_activation_script(self):
        with patch.object(core.os, 'name', 'nt'):
            self.assertEqual(core.validate_port(' com12 '), 'COM12')
            for invalid in ['', 'COM0', 'C:/ProgramData/anaconda3/Scripts/activate']:
                with self.subTest(port=invalid), self.assertRaises(ValueError):
                    core.validate_port(invalid)


if __name__ == '__main__':
    unittest.main()
