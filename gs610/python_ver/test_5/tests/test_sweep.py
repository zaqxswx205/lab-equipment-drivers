import csv
import io
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from decimal import Decimal
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import gs610_sweep as app


class Instrument:
    def __init__(self, fail_read=False):
        self.commands = []
        self.output = False
        self.current = '0'
        self.linear = {}
        self.linear_index = 0
        self.stored = []
        self.read_index = 0
        self.period = 1.3
        self.remote_sense = False
        self.fail_read = fail_read

    def write(self, command):
        self.commands.append(command)
        if command.startswith(':OUTP '):
            self.output = command == ':OUTP ON'
        elif command.startswith(':SENS:RSEN '):
            self.remote_sense = command.split()[-1] in ('ON', '1')
        elif command.startswith(':SOUR:CURR:SWE:'):
            key, value = command.split()
            self.linear[key.rsplit(':', 1)[1]] = value
        elif command.startswith(':TRIG:TIM '):
            self.period = float(command.split()[-1])
        elif command.startswith(':TRAC:DATA:NUMB '):
            self.read_index = int(command.split()[-1]) - 1
        elif command == ':SWE:TRIG':
            from decimal import Decimal
            start = Decimal(self.linear['STAR'])
            stop = Decimal(self.linear['STOP'])
            step = Decimal(self.linear['STEP'])
            count = int(abs(stop - start) / step) + 1
            self.stored = [start + (step if stop >= start else -step) * i for i in range(count)]
            self.current = str(stop)
        elif command.startswith(':SOUR:CURR:LEV '):
            self.current = command.split()[-1]

    def query(self, command):
        self.commands.append(command)
        if command == ':TRIG:SOUR?':
            return 'TIM'
        if command == ':TRIG:TIM?':
            return str(self.period)
        if command == ':STAT:SOUR:EVEN?':
            return '0'
        if command == ':TRAC:ACT?':
            return str(len(self.stored))
        if command == ':TRAC:DATA:SOUR?':
            return str(self.stored[self.read_index])
        if command == ':TRAC:DATA:TIME?':
            return str(self.read_index * self.period)
        if command == ':TRAC:DATA:SENS?':
            if self.fail_read:
                raise TimeoutError('simulated timeout')
            return '0.125'
        if command == ':STAT:SOUR:COND?':
            return '32'
        if command == ':SOUR:MODE?':
            return 'SWE'
        if command == ':SOUR:CURR:SWE:SPAC?':
            return 'LIN'
        if command.startswith(':SOUR:CURR:SWE:') and command.endswith('?'):
            return self.linear[command.rsplit(':', 1)[1][:-1]]
        if command == ':SYST:ERR?':
            return '0,"No error"'
        if command == ':OUTP?':
            return str(int(self.output))
        if command == ':SOUR:CURR:LEV?':
            return self.current
        if command == ':SENS:RSEN?':
            return str(int(self.remote_sense))
        if command == ':FETC?':
            if self.fail_read:
                raise TimeoutError('simulated timeout')
            return '0.125'
        return {':SOUR:VOLT:PROT:ULIM?': '12', ':SOUR:VOLT:PROT:LLIM?': '-12',
                ':SOUR:VOLT:PROT:STAT?': '1', ':SENS:FUNC?': 'VOLT',
                ':STAT:SENS:COND?': '0'}[command]


class SweepTests(unittest.TestCase):
    def test_default_round_trip(self):
        points, source_range, limit = app.plan_sweep('-3.2', '3.2', '.08', '12')
        self.assertEqual(len(points), 162)
        self.assertEqual(points[80:82], [Decimal('3.2')] * 2)
        self.assertEqual(points[0], points[-1])
        self.assertEqual((source_range, limit), (Decimal(3), Decimal(12)))

    def test_validation(self):
        for args in [('0', '3.3', '.1', '12'), ('0', '3.2', '.08', '13'),
                     ('0', '1', '0', '12'), ('0', '1', '.000001', '12'),
                     ('NaN', '1', '.1', '12')]:
            with self.subTest(args=args), self.assertRaises(ValueError):
                app.plan_sweep(*args)

    def test_round_trip_csv_and_output_off(self):
        points, source_range, limit = app.plan_sweep('0', '.002', '.001', '12')
        instrument = Instrument()
        with tempfile.TemporaryDirectory() as folder, redirect_stdout(io.StringIO()):
            dest = Path(folder) / 'data.csv'
            records = app.GS610(instrument).sweep(points, source_range, limit, 0, csv_path=dest)
            with dest.open(encoding='utf-8-sig', newline='') as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(len(records), 6)
            self.assertEqual(len(rows), 3)
            self.assertEqual(rows[0]['set_current_A'], '0.000')
            self.assertEqual(rows[0]['return_set_current_A'], '0.002')
            self.assertEqual(rows[-1]['return_point'], '6')
            self.assertFalse(instrument.output)

    def test_timeout_closes_output_even_with_keep_output(self):
        instrument = Instrument(fail_read=True)
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            with self.assertRaises(TimeoutError):
                app.GS610(instrument).sweep([Decimal(0)], Decimal('.002'), Decimal(12),
                                          0, keep_output=True)
        self.assertFalse(instrument.output)
        self.assertEqual(instrument.commands[-3:], [':OUTP OFF', ':SYST:ERR?', ':OUTP?'])

    def test_success_can_keep_output(self):
        instrument = Instrument()
        with redirect_stdout(io.StringIO()):
            app.GS610(instrument).sweep([Decimal(0)], Decimal('.002'), Decimal(12),
                                      0, keep_output=True)
        self.assertTrue(instrument.output)

    def test_partial_response_disables_further_queries(self):
        class Port:
            def write(self, value):
                return len(value)
            def read_until(self, *args, **kwargs):
                return b'incomplete'
        instrument = app.SerialInstrument(Port())
        with self.assertRaises(TimeoutError):
            instrument.query('*IDN?')
        with self.assertRaises(RuntimeError):
            instrument.query(':READ?')
        instrument.write(':OUTP OFF')


if __name__ == '__main__':
    unittest.main()
