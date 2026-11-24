import csv
from datetime import datetime
from pathlib import Path
import re
import tempfile
import threading
import unittest
from unittest.mock import patch

import core
from csv_recording import CsvRecording, HEADERS
from drive_profile import Profile, Step
from runtime import RunControls


class CsvRecordingTests(unittest.TestCase):
    def rows(self, path):
        with path.open(encoding='utf-8-sig', newline='') as f:
            return list(csv.DictReader(f))

    def test_time_columns_day_rollover_and_same_second_collision(self):
        with tempfile.TemporaryDirectory() as d:
            recorder = CsvRecording(d)
            when = datetime(2026, 11, 2, 23, 59, 59, 987654)
            first = recorder.start(when)
            fields = [101, 2, 100, 273.15, *['']*10]
            recorder.write(when, fields)
            second = recorder.start(when)
            recorder.write(when, fields)
            recorder.write(datetime(2026, 11, 3, 0, 0, 0, 123456), fields)
            recorder.close()
            self.assertEqual(first.name, '20261102_235959.csv')
            self.assertEqual(second.name, '20261102_235959_02.csv')
            third = Path(d)/'20261103_000000.csv'
            self.assertTrue(third.exists())
            self.assertEqual(self.rows(first)[0]['time'], '2026-11-02 23:59:59')
            self.assertEqual(self.rows(third)[0]['time'], '2026-11-03 00:00:00')
            for path in (first, second, third):
                row = self.rows(path)[0]
                self.assertEqual(list(row), HEADERS)
                self.assertNotIn(None, row)
                for removed in ('elapsed_min', 'reference_channel', 'drive_elapsed_min', 'measurement_status'):
                    self.assertNotIn(removed, row)

    def test_restarting_acquisition_opens_new_csv_while_drive_keeps_running(self):
        controls = RunControls(True, True, Profile('time', [Step(0, 'voltage', 3)]))
        samples, events = [], []
        def emit(kind, value):
            events.append((kind, value))
            if kind == 'sample':
                samples.append(value)
                if len(samples) == 1:
                    controls.acquisition.clear()
            elif kind == 'acquisition_done' and controls.drive.is_set():
                controls.start_acquisition()
        with tempfile.TemporaryDirectory() as d:
            with patch.object(core.SimMeter, 'read', return_value=(100, 0)):
                core.run(core.Config(interval=.05), threading.Event(), emit,
                         simulation=True, max_cycles=3, output_dir=d, controls=controls)
            files = sorted(Path(d).glob('*.csv'))
            self.assertEqual(len(files), 2)
            self.assertEqual(sum(len(self.rows(path)) for path in files), 6)
            for path in files:
                self.assertRegex(path.name, r'^SIM_\d{8}_\d{6}(?:_\d+)?\.csv$')
                for row in self.rows(path):
                    self.assertEqual(float(row['temperature_K']), 273.15)
                    self.assertNotEqual(row['power_W'], '')
                    self.assertTrue(re.fullmatch(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}', row['time']))
            self.assertFalse(events[-1][1]['error'])
