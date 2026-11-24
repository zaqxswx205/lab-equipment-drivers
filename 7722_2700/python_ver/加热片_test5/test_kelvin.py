import csv
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import core
from drive_profile import load_profile, profile_text_kelvin
from runtime import RunControls


class KelvinTests(unittest.TestCase):
    def test_kelvin_threshold_storage_and_no_double_conversion(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/'config.ini'
            cfg = core.Config()
            core.save_config(path, cfg)
            text = path.read_text(encoding='utf-8')
            self.assertIn('temperature_unit = K', text)
            self.assertIn('min_temperature = 223.15', text)
            self.assertIn('max_temperature = 353.15', text)
            self.assertEqual(core.load_config(path), cfg)
            core.save_config(path, core.load_config(path))
            self.assertEqual(path.read_text(encoding='utf-8'), text)

    def test_old_and_kelvin_profile_have_same_actual_trigger(self):
        old = '[Drive]\nstrategy=temperature\n[Step 1]\nat=0\nmode=voltage\nvalue=3\n'
        converted = profile_text_kelvin(old)
        self.assertIn('at = 273.15', converted)
        self.assertEqual(profile_text_kelvin(converted), converted)
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/'profile.ini'
            path.write_text(old, encoding='utf-8')
            before = load_profile(path, core.Config())
            path.write_text(converted, encoding='utf-8')
            self.assertEqual(load_profile(path, core.Config()), before)

    def test_zero_celsius_recorded_as_273_kelvin(self):
        with tempfile.TemporaryDirectory() as d:
            with patch.object(core.SimMeter, 'read', return_value=(100, 0)):
                core.run(core.Config(interval=.05), threading.Event(), lambda *args: None,
                         simulation=True, max_cycles=1, output_dir=d, controls=RunControls(acquisition=True))
            with next(Path(d).glob('*.csv')).open(encoding='utf-8-sig', newline='') as f:
                rows = list(csv.DictReader(f))
            self.assertTrue(all(float(row['temperature_K']) == 273.15 for row in rows))
            self.assertNotIn('temperature_C', rows[0])
