import threading
import unittest
from contextlib import redirect_stdout
import io
from decimal import Decimal
from unittest.mock import patch

from test_sweep import Instrument
import gs610_sweep as core


class RealtimeTests(unittest.TestCase):
    def test_internal_scan_reads_stored_values_not_live_level(self):
        class Buffered(Instrument):
            def query(self, command):
                if command == ':TRAC:DATA:SENS?':
                    return str(Decimal('0.25') * (self.read_index + 1))
                if command == ':TRAC:DATA:TIME?':
                    return str(Decimal('1.3') * self.read_index)
                return super().query(command)
        points, source_range, limit = core.plan_sweep('0', '.002', '.001', '12', False)
        instrument = Buffered()
        with redirect_stdout(io.StringIO()):
            rows = core.GS610(instrument).sweep(points, source_range, limit, 1)
        self.assertEqual([row[2] for row in rows], points)
        self.assertEqual([row[3] for row in rows], list(map(Decimal, ['.25', '.5', '.75'])))
        self.assertAlmostEqual(rows[2][1] - rows[0][1], 2.6)
        self.assertIn(':TRIG:TIM 1.300000', instrument.commands)
        self.assertIn(':SENS:DEL 1', instrument.commands)

    def test_internal_sampling_error_stops_output(self):
        class SamplingError(Instrument):
            def query(self, command):
                if command == ':STAT:SOUR:EVEN?' and ':SWE:TRIG' in self.commands:
                    return '128'
                return super().query(command)
        instrument = SamplingError()
        with redirect_stdout(io.StringIO()), self.assertRaisesRegex(RuntimeError, 'Sampling error'):
            core.GS610(instrument).sweep([Decimal(0)], Decimal('.002'), Decimal(12), 0)
        self.assertFalse(instrument.output)

    def test_linear_waits_for_ready_before_output(self):
        class Preparing(Instrument):
            def __init__(self):
                super().__init__()
                self.ready_reads = 0

            def query(self, command):
                if command == ':STAT:SOUR:COND?':
                    self.ready_reads += 1
                    self.commands.append(command)
                    return '0' if self.ready_reads == 1 else '32'
                return super().query(command)

        instrument = Preparing()
        with redirect_stdout(io.StringIO()), patch.object(core.time, 'sleep'):
            core.GS610(instrument).sweep([Decimal(0)], Decimal('.002'), Decimal(12), 0)
        self.assertEqual(instrument.ready_reads, 2)
        self.assertLess(instrument.commands.index(':STAT:SOUR:COND?'),
                        instrument.commands.index(':OUTP ON'))

    def test_rejected_trigger_reports_error_without_fetch_timeout(self):
        class RejectTrigger(Instrument):
            def query(self, command):
                if command == ':SYST:ERR?' and ':SWE:TRIG' in self.commands and self.output:
                    return '-221,"Setting conflict"'
                return super().query(command)

        instrument = RejectTrigger()
        with redirect_stdout(io.StringIO()), self.assertRaisesRegex(RuntimeError, 'Setting conflict'):
            core.GS610(instrument).sweep([Decimal(0)], Decimal('.002'), Decimal(12), 0)
        self.assertNotIn(':FETC?', instrument.commands)
        self.assertFalse(instrument.output)

    def test_low_battery_warns_but_does_not_hide_next_error(self):
        class ErrorQueue(Instrument):
            def __init__(self, errors):
                super().__init__()
                self.errors = iter(errors)

            def query(self, command):
                return next(self.errors) if command == ':SYST:ERR?' else super().query(command)

        seen = []
        device = core.GS610(ErrorQueue(['301,"Low battery"', '0,"No error"']), seen.append)
        device.check_error()
        self.assertEqual(len(seen), 1)
        device = core.GS610(ErrorQueue(['301,"Low battery"', '-221,"Setting conflict"']))
        device.write(':SOUR:CURR:RANG 3')
        with self.assertRaisesRegex(RuntimeError, ':SOUR:CURR:RANG 3'):
            device.check_error()

    def test_full_current_linear_round_trip(self):
        points, source_range, limit = core.plan_sweep('-3.2', '3.2', '.08', '12')
        instrument = Instrument()
        with redirect_stdout(io.StringIO()):
            result = core.GS610(instrument).sweep(points, source_range, limit, 0)
        self.assertEqual(len(result), 162)
        self.assertEqual([row[2] for row in result], points)
        self.assertIn(':SOUR:CURR:RANG 3', instrument.commands)
        self.assertIn(':SOUR:CURR:SWE:STEP 0.08', instrument.commands)
        self.assertFalse(instrument.output)

    def test_linear_round_trip_uses_instrument_steps(self):
        instrument = Instrument()
        points, source_range, limit = core.plan_sweep('0', '.002', '.001', '12')
        with redirect_stdout(io.StringIO()):
            core.GS610(instrument).sweep(points, source_range, limit, 0)
        commands = instrument.commands
        self.assertEqual(commands.count(':SWE:TRIG'), 2)
        self.assertNotIn('*TRG', commands)
        self.assertEqual(commands.count(':TRAC:DATA:SENS?'), len(points))
        self.assertNotIn(':INIT', commands)
        self.assertNotIn(':FETC?', commands)
        self.assertIn(':TRAC:AUTO ON', commands)
        self.assertIn(':TRIG:TIM 0.300000', commands)
        self.assertEqual(sum(c.startswith(':SOUR:CURR:LEV ') for c in commands), 1)
        self.assertIn(':SOUR:CURR:SWE:STAR 0.002', commands)
        self.assertIn(':SOUR:CURR:SWE:STOP 0.000', commands)
        self.assertLess(commands.index(':TRIG:SOUR TIM'), commands.index(':OUTP ON'))
        self.assertLess(commands.index(':SWE:TRIG'), commands.index(':TRAC:DATA:SENS?'))

    def test_linear_rejects_unequal_last_step(self):
        with self.assertRaisesRegex(ValueError, '整数倍'):
            core.plan_sweep('0', '.0025', '.001', '12')

    def test_refused_linear_mode_does_not_enable_output(self):
        class RefusesLinear(Instrument):
            def query(self, command):
                return 'FIX' if command == ':SOUR:MODE?' else super().query(command)
        instrument = RefusesLinear()
        with redirect_stdout(io.StringIO()), self.assertRaisesRegex(RuntimeError, 'Linear'):
            core.GS610(instrument).sweep([Decimal(0)], Decimal('.002'), Decimal(12), 0)
        self.assertNotIn(':OUTP ON', instrument.commands)

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
        self.assertEqual(instrument.commands.count(':TRAC:DATA:SENS?'), 1)

    def test_cancel_during_dwell_does_not_measure(self):
        class EmptyBuffer(Instrument):
            def query(self, command):
                return '0' if command == ':TRAC:ACT?' else super().query(command)
        instrument = EmptyBuffer()
        class StopAtDwell:
            def is_set(self):
                return False
            def wait(self, seconds):
                return True
        with redirect_stdout(io.StringIO()), self.assertRaises(core.SweepCancelled):
            core.GS610(instrument).sweep([Decimal(0)], Decimal('.002'), Decimal(12),
                                        30, stop_event=StopAtDwell())
        self.assertFalse(instrument.output)
        self.assertNotIn(':TRAC:DATA:SENS?', instrument.commands)

    def test_windows_port_rejects_activation_script(self):
        with patch.object(core.os, 'name', 'nt'):
            self.assertEqual(core.validate_port(' com12 '), 'COM12')
            for invalid in ['', 'COM0', 'C:/ProgramData/anaconda3/Scripts/activate']:
                with self.subTest(port=invalid), self.assertRaises(ValueError):
                    core.validate_port(invalid)


if __name__ == '__main__':
    unittest.main()
