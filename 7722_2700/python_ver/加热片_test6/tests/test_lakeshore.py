"""Independent interpolation values and configuration/UI range regressions."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from core import Channel, Config, load_config, save_config
from model.lakeshore import PT100_TABLE
from model.pt100 import resistance, temperature


class LakeShoreTests(unittest.TestCase):
    def test_requested_range_interpolation(self):
        # Linear interpolation of source intervals 32–36 K and 315–355 K.
        for kelvin, ohms in ((30., 3.820), (35.15, 4.9524125),
                             (349.15, 129.3716475), (800., 289.830)):
            for sensor, scale in (('pt100', 1), ('pt1000', 10)):
                with self.subTest(kelvin=kelvin, sensor=sensor):
                    self.assertAlmostEqual(resistance(kelvin-273.15, sensor, 'lakeshore'), ohms*scale, places=8)
                    self.assertAlmostEqual(temperature(ohms*scale, sensor, 'lakeshore')+273.15, kelvin, places=8)

    def test_all_source_breakpoints_and_segment_roundtrips(self):
        self.assertEqual(len(PT100_TABLE), 29)
        for sensor, scale in (('pt100', 1), ('pt1000', 10)):
            for kelvin, ohms in PT100_TABLE:
                self.assertAlmostEqual(resistance(kelvin-273.15, sensor, 'lakeshore'), ohms*scale, places=9)
                self.assertAlmostEqual(temperature(ohms*scale, sensor, 'lakeshore')+273.15, kelvin, places=9)
            for (t1, r1), (t2, r2) in zip(PT100_TABLE, PT100_TABLE[1:]):
                self.assertGreater(t2, t1)
                self.assertGreater(r2, r1)
                for fraction in (.1, .5, .9):
                    kelvin = t1 + fraction*(t2-t1)
                    self.assertAlmostEqual(temperature(resistance(kelvin-273.15, sensor, 'lakeshore'), sensor, 'lakeshore')+273.15,
                                           kelvin, places=9)

    def test_no_extrapolation_or_nonfinite_values(self):
        for sensor, scale in (('pt100', 1), ('pt1000', 10)):
            for kelvin in (20, 29.999, 800.001, float('nan'), float('inf')):
                with self.subTest(sensor=sensor, kelvin=kelvin), self.assertRaisesRegex(ValueError, '30–800 K'):
                    resistance(kelvin-273.15, sensor, 'lakeshore')
            for ohms in (3.819, 289.831, float('nan'), float('inf')):
                with self.subTest(sensor=sensor, ohms=ohms), self.assertRaisesRegex(ValueError, '30–800 K'):
                    temperature(ohms*scale, sensor, 'lakeshore')

    def test_protection_range_and_30k_config_roundtrip(self):
        for lower, upper in ((30, 349.15), (30, 800), (35.15, 349.15)):
            cfg = Config(reference_channel=101,
                         channels=[Channel(101, min_temperature=round(lower-273.15, 10),
                                           max_temperature=round(upper-273.15, 10), sensor_curve='lakeshore')])
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory)/'config.ini'
                save_config(path, cfg)
                self.assertEqual(load_config(path), cfg)
        for lower, upper in ((20, 349.15), (30, 801), (30, 30)):
            with self.subTest(lower=lower, upper=upper), self.assertRaisesRegex(ValueError, '30 K'):
                Config(reference_channel=101,
                       channels=[Channel(101, min_temperature=lower-273.15,
                                         max_temperature=upper-273.15, sensor_curve='lakeshore')]).validate()

    def test_startup_keeps_30k_instead_of_falling_back(self):
        from ui.app import App
        source = Path(__file__).resolve().parents[1]/'drive_time.ini'
        with tempfile.TemporaryDirectory() as directory:
            cfg = Config(profile_file=str(source), channels=[
                Channel(101, min_temperature=-243.15, sensor_curve='lakeshore'),
                Channel(102, min_temperature=-243.15, sensor_curve='lakeshore')])
            save_config(Path(directory)/'config.ini', cfg)
            with patch('ui.app.BASE', Path(directory)):
                app = App()
            app.withdraw()
            try:
                self.assertEqual(float(app.channel_vars['min_temperature'].get()), 35.15)
                for channel in ('101', '102'):
                    self.assertEqual(float(app.table.set(channel, 'min_temperature')), 30)
                self.assertNotIn('配置读取失败', app.system_text.get())
                self.assertEqual(app.collect(), cfg)
            finally:
                app.destroy()
