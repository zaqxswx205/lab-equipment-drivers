import threading
import unittest
from decimal import Decimal

from displacement_limit import find_limits, validate_thresholds
from gs610_driver import SweepCancelled


class Source:
    def __init__(self):
        self.currents = []

    def set_current(self, current):
        self.current = current
        self.currents.append(current)

    def write(self, command):
        pass

    def check_error(self):
        pass

    def query(self, command):
        return '1'

    def measure_voltage(self):
        return '0'


class Meter:
    def __init__(self, source, overload=False, flat=False):
        self.source, self.overload, self.flat = source, overload, flat

    def measure(self, channel):
        # Exactly +/-2 V at +/-0.08 A must not terminate the search.
        return '', (0 if self.flat else float(self.source.current * 25)), ('OVERLOAD' if self.overload else 'OK')


class LimitTests(unittest.TestCase):
    def test_custom_thresholds(self):
        source = Source()
        result = find_limits(source, Meter(source), threading.Event(), 0, lambda *row: None, -4, 6)
        self.assertEqual(result, (Decimal('-.24'), Decimal('.32')))

    def test_invalid_thresholds(self):
        for lower, upper in [(0, 2), (2, 2), (-2, 0), (-2, -2), ('nan', 2), (-2, 'inf')]:
            with self.subTest(lower=lower, upper=upper), self.assertRaises(ValueError):
                validate_thresholds(lower, upper)

    def test_crossings_and_zero_reset(self):
        source = Source()
        rows = []
        result = find_limits(source, Meter(source), threading.Event(), 0, lambda *row: rows.append(row))
        self.assertEqual(result, (Decimal('-.16'), Decimal('.16')))
        self.assertEqual(source.currents, list(map(Decimal, ['0', '-.08', '-.16', '0', '0', '.08', '.16', '0'])))
        self.assertEqual(len(rows), 6)

    def test_no_crossing_stops_at_hardware_bound(self):
        source = Source()
        with self.assertRaisesRegex(RuntimeError, '仍未跨过阈值'):
            find_limits(source, Meter(source, flat=True), threading.Event(), 0, lambda *row: None)
        self.assertEqual(source.currents[-1], Decimal('-3.2'))
        self.assertEqual(len(source.currents), 41)

    def test_overload_aborts(self):
        source = Source()
        with self.assertRaisesRegex(RuntimeError, '采集电压无效'):
            find_limits(source, Meter(source, overload=True), threading.Event(), 0, lambda *row: None)

    def test_cancel_before_output(self):
        source = Source()
        stop = threading.Event()
        stop.set()
        with self.assertRaises(SweepCancelled):
            find_limits(source, Meter(source), stop, 0, lambda *row: None)
        self.assertEqual(source.currents, [])


if __name__ == '__main__':
    unittest.main()
