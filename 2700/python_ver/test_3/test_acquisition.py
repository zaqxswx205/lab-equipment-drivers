import csv
import tempfile
import threading
import unittest
from collections import deque
from pathlib import Path

from acquisition import (FIELDS, MODES, Instrument, Settings, TextLog, collect,
                         convert_csv, parse_reading)


class FakeSerial:
    def __init__(self, **kwargs):
        self.commands = []
        self.response = bytearray()
        self.closed = False
        self.front = "1"
        self.writes = []
        self.errors = deque()

    def reset_input_buffer(self):
        self.response.clear()

    def write(self, data):
        self.writes.append(data)
        command = data.decode("ascii").strip()
        self.commands.append(command)
        response = {"*IDN?": "KEITHLEY INSTRUMENTS INC.,MODEL 2700,12345,A01",
                    "SYST:FRSW?": self.front, "SYST:ERR?": None,
                    "READ?": "+1.234E-3", "*OPC?": "1"}.get(command)
        if command == "SYST:ERR?":
            response = self.errors.popleft() if self.errors else '0,"No error"'
        if response is not None:
            self.response.extend((response + "\r\n").encode())

    def read(self, size):
        data = self.response[:size]
        del self.response[:size]
        return bytes(data)

    def close(self):
        self.closed = True

    def flush(self):
        pass


class Tests(unittest.TestCase):
    def test_existing_520_is_recorded_before_abor_and_allows_collection(self):
        with tempfile.TemporaryDirectory() as folder:
            fake = FakeSerial()
            fake.errors.extend(['520,"Saved setup scancard mismatch"', '-101,"Invalid character"'])
            events = []
            stop = threading.Event()
            def emit(kind, data):
                events.append((kind, data))
                if kind == "sample":
                    stop.set()
            collect(Settings(), Path(folder) / "log.txt", stop, emit,
                    serial_factory=lambda **_: fake)
            self.assertEqual(sum(kind == "warning" for kind, _ in events), 1)
            summary = next(data for kind, data in events if kind == "warning")
            self.assertIn("520", summary)
            self.assertIn("-101", summary)
            self.assertTrue(any(kind == "sample" for kind, _ in events))
            self.assertFalse(any(kind == "error" for kind, _ in events), events)
            self.assertLess(fake.commands.index("SYST:ERR?"), fake.commands.index("ABOR"))

    def test_new_520_after_abor_is_fatal(self):
        class AborErrorSerial(FakeSerial):
            def write(self, data):
                super().write(data)
                if data == b"ABOR\r":
                    self.errors.append('520,"Saved setup scancard mismatch"')
        fake = AborErrorSerial()
        instrument = Instrument(Settings(), threading.Event(), lambda **_: fake)
        instrument.open()
        self.assertEqual(instrument.startup_errors, [])
        with self.assertRaisesRegex(RuntimeError, "ABOR.*520"):
            instrument.configure()
        instrument.close()

    def test_connection_probe_uses_cr_and_no_break_character(self):
        class StrictSerial(FakeSerial):
            def write(self, data):
                if not data.endswith(b"\r") or any(b < 32 or b > 126 for b in data[:-1]):
                    raise AssertionError(f"Invalid serial command: {data!r}")
                super().write(data)
        # Even a legacy LF setting must not affect outbound CR termination.
        for terminator in ("LF", "CR", "CRLF"):
            fake = StrictSerial()
            events = []
            collect(Settings(terminator=terminator), None, threading.Event(),
                    lambda *event: events.append(event), probe=True,
                    serial_factory=lambda **_: fake)
            self.assertFalse(any(kind == "error" for kind, _ in events), events)
            self.assertIn(b"*IDN?\r", fake.writes)
            self.assertNotIn(b"\x03", fake.writes)
            self.assertTrue(fake.closed)

    def test_modes_and_front_guard(self):
        for mode, (function, _) in MODES.items():
            fake = FakeSerial()
            instrument = Instrument(Settings(mode=mode), threading.Event(), lambda **_: fake)
            instrument.open()
            instrument.configure()
            self.assertIn("CONF:" + function, fake.commands)
            self.assertIn("FORM:ELEM READ", fake.commands)
            self.assertEqual(instrument.query("READ?"), "+1.234E-3")
            fake.front = "0"
            with self.assertRaisesRegex(RuntimeError, "FRONT/REAR"):
                instrument.check_front()
            instrument.close()
            self.assertTrue(fake.closed)

    def test_overload(self):
        self.assertEqual(parse_reading("+9.9E37"), (None, "OVERLOAD"))
        self.assertEqual(parse_reading("NAN"), (None, "OVERLOAD"))
        self.assertEqual(parse_reading("-2.1E-6"), (-2.1e-6, "OK"))
        with self.assertRaises(ValueError):
            parse_reading("garbage")

    def test_real_pipeline_save_before_emit_and_convert(self):
        with tempfile.TemporaryDirectory() as folder:
            txt, out = Path(folder) / "log.txt", Path(folder) / "log.csv"
            stop = threading.Event()
            fake = FakeSerial()
            errors, samples = [], []
            def emit(kind, data):
                if kind == "sample":
                    samples.append(data)
                    # Another reader sees the row while acquisition is running.
                    self.assertEqual(len(txt.read_text(encoding="utf-8").splitlines()), 2)
                    stop.set()
                if kind == "error":
                    errors.append(data)
            collect(Settings(interval=0.05), txt, stop, emit, serial_factory=lambda **_: fake)
            self.assertFalse(errors)
            self.assertEqual(len(samples), 1)
            self.assertTrue(fake.closed)
            self.assertEqual(convert_csv(txt, out), 1)
            with out.open(encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(rows[0]["value"], "0.001234")
            self.assertNotIn("source", rows[0])
            self.assertEqual(list(rows[0]), FIELDS)
            with self.assertRaises(ValueError):
                convert_csv(txt, txt)
            with self.assertRaises(ValueError):
                convert_csv(txt, out)
            with self.assertRaises(FileExistsError):
                TextLog(txt)

    def test_simulation_and_cancellation(self):
        with tempfile.TemporaryDirectory() as folder:
            stop = threading.Event()
            events = []
            def emit(kind, data):
                events.append((kind, data))
                if kind == "sample":
                    stop.set()
            collect(Settings(simulation=True), Path(folder) / "sim.txt", stop, emit)
            row = next(data[1] for kind, data in events if kind == "sample")
            self.assertEqual(set(row), set(FIELDS))
            self.assertEqual(events[-1][0], "done")

    def test_malformed_export_does_not_leave_partial_csv(self):
        with tempfile.TemporaryDirectory() as folder:
            txt, out = Path(folder) / "bad.txt", Path(folder) / "bad.csv"
            txt.write_text("\t".join(FIELDS) + "\nonly_one_column\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                convert_csv(txt, out)
            self.assertFalse(out.exists())

    def test_rear_input_stops_without_creating_log(self):
        with tempfile.TemporaryDirectory() as folder:
            txt = Path(folder) / "log.txt"
            fake = FakeSerial()
            fake.front = "0"
            events = []
            collect(Settings(), txt, threading.Event(), lambda *event: events.append(event),
                    serial_factory=lambda **_: fake)
            self.assertFalse(txt.exists())
            self.assertTrue(fake.closed)
            self.assertTrue(any(kind == "error" for kind, _ in events))
            self.assertEqual(events[-1][0], "done")

    def test_timeout_stop_and_invalid_settings(self):
        fake = FakeSerial()
        stop = threading.Event()
        instrument = Instrument(Settings(timeout=0.01), stop, lambda **_: fake)
        instrument.serial = fake
        with self.assertRaises(TimeoutError):
            instrument.query("NO_RESPONSE?")
        stop.set()
        from acquisition import Cancelled
        with self.assertRaises(Cancelled):
            instrument.query("READ?")
        for settings in [Settings(interval=float("nan")), Settings(nplc=11),
                         Settings(measure_range="-1"), Settings(timeout=0), Settings(baud=115200)]:
            with self.assertRaises(ValueError):
                settings.validate()


if __name__ == "__main__":
    unittest.main()
