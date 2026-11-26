from tests.csv_helpers import legacy_rows
"""Per-channel curve selection reaches persistence, the meter and child processes."""
import csv
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import core
from runtime import RunControls
from model.pt100 import resistance, temperature
from model.rtd_curves import CURVE_LABELS
from model.user_pt100 import PT100_TABLE
from tests.test_slot_measurement import Port


class CurveSelectionTests(unittest.TestCase):
    def test_defaults_and_legacy_range_migration(self):
        self.assertAlmostEqual(temperature(1.8052)+273.15, 35.15)
        text = ('[Settings]\ntemperature_unit=K\npower_port=COM9\n'
                '[Channel 101]\nmin_temperature=30\nmax_temperature=353.15\n'
                '[Channel 102]\nmin_temperature=30\nmax_temperature=353.15\n')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'config.ini'
            path.write_text(text, encoding='utf-8')
            cfg = core.load_config(path)
            self.assertEqual(cfg.power_port, 'COM9')
            for channel in cfg.channels:
                self.assertEqual(channel.sensor_curve, 'user_table')
                self.assertEqual((channel.min_temperature, channel.max_temperature), (-238, 76))
            core.save_config(path, cfg)
            self.assertEqual(core.load_config(path), cfg)
            # An explicitly chosen Lake Shore curve keeps its wider limits.
            path.write_text(text.replace('max_temperature=353.15',
                                         'max_temperature=353.15\nsensor_curve=lakeshore'), encoding='utf-8')
            explicit = core.load_config(path)
            self.assertEqual([(c.sensor_curve, c.min_temperature, c.max_temperature)
                              for c in explicit.channels], [('lakeshore', -243.15, 80)]*2)

    def test_user_photo_reference_points_and_all_segments(self):
        self.assertEqual(len(PT100_TABLE), 152)
        expected = ((35.15, 1.8052), (73.15, 18.011), (109.15, 33.528),
                    (115.15, 36.069), (189.15, 66.594), (195.15, 69.015),
                    (269.15, 98.420), (275.15, 100.772), (349.15, 129.402))
        for sensor, scale in (('pt100', 1), ('pt1000', 10)):
            for kelvin, ohms in expected:
                self.assertAlmostEqual(resistance(kelvin-273.15, sensor, 'user_table'), ohms*scale, places=9)
                self.assertAlmostEqual(temperature(ohms*scale, sensor, 'user_table')+273.15, kelvin, places=9)
            for (t1, r1), (t2, r2) in zip(PT100_TABLE, PT100_TABLE[1:]):
                self.assertGreater(t2, t1)
                self.assertGreater(r2, r1)
                midpoint = (t1+t2)/2
                self.assertAlmostEqual(resistance(midpoint-273.15, sensor, 'user_table'), (r1+r2)*scale/2, places=9)
                self.assertAlmostEqual(temperature((r1+r2)*scale/2, sensor, 'user_table')+273.15, midpoint, places=9)
        self.assertNotAlmostEqual(resistance(-238, 'pt100', 'user_table'),
                                  resistance(-238, 'pt100', 'lakeshore'), places=2)
        for kelvin in (30, 35.149, 349.151, float('nan')):
            with self.assertRaises(ValueError):
                resistance(kelvin-273.15, 'pt100', 'user_table')
        with self.assertRaises(ValueError):
            temperature(100, 'pt100', 'unknown')

    def test_mixed_curve_configuration_roundtrip_and_bounds(self):
        cfg = core.Config(channels=[core.Channel(101, sensor_curve='lakeshore'),
            core.Channel(102, min_temperature=-238, max_temperature=76, sensor_curve='user_table')])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'config.ini'
            core.save_config(path, cfg)
            self.assertEqual(core.load_config(path), cfg)
            self.assertIn('sensor_curve = user_table', path.read_text(encoding='utf-8'))
        cfg.channels[1].min_temperature = -243.15
        with self.assertRaisesRegex(ValueError, '35.15 K'):
            cfg.validate()
        cfg.channels[1].sensor_curve = 'unknown'
        with self.assertRaisesRegex(ValueError, '换算曲线'):
            cfg.validate()

    def test_real_adapter_uses_each_curve_after_lead_compensation(self):
        class CurvePort(Port):
            def write(self, data):
                result = super().write(data)
                if data.decode().strip() == 'READ?':
                    raw = {101: 5.346, 102: 18.252}[self.channel]
                    self.reply = bytearray(f'{raw},{self.channel}\r\n'.encode())
                return result
        cfg = core.Config(settle=0, channels=[core.Channel(101, .2, sensor_curve='lakeshore'),
            core.Channel(102, .2, min_temperature=-238, max_temperature=76,
                         sensor_type='pt1000', sensor_curve='user_table')])
        with patch('serial.Serial', return_value=CurvePort('7702,NONE')):
            meter = core.Meter(cfg, lambda _: None)
            try:
                meter.setup()
                for channel, expected in zip(cfg.channels, (-237.15, -238.)):
                    self.assertAlmostEqual(meter.read(channel, threading.Event())[1], expected, places=9)
            finally:
                meter.close()

    def test_independent_process_simulation_retains_selected_curve(self):
        cfg = core.Config(interval=.05, settle=0, channels=[core.Channel(101, sensor_curve='lakeshore'),
            core.Channel(102, min_temperature=-238, max_temperature=76,
                         sensor_type='pt1000', sensor_curve='user_table')])
        controls = RunControls(acquisition=True)
        controls.device_processes = True
        events = []
        with tempfile.TemporaryDirectory() as directory:
            core.run(cfg, threading.Event(), lambda *event: events.append(event),
                     simulation=True, max_cycles=2, output_dir=directory, controls=controls)
            self.assertFalse(events[-1][1]['error'])
            with next(Path(directory).glob('*.csv')).open(encoding='utf-8-sig', newline='') as stream:
                rows = list(legacy_rows(stream))
            self.assertEqual({int(row['channel']) for row in rows}, {101, 102})
            for row in rows:
                channel = cfg.channels[int(row['channel'])-101]
                expected = resistance(float(row['temperature_K'])-273.15,
                                      channel.sensor_type, channel.sensor_curve)
                self.assertAlmostEqual(float(row['resistance_ohm']), expected, places=5)

    def test_ui_switch_update_save_restore_and_restart(self):
        from ui.app import App
        source = Path(__file__).resolve().parents[1]/'drive_profile.ini'
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'config.ini'
            core.save_config(path, core.Config(profile_file=str(source), channels=[
                core.Channel(101, sensor_curve='lakeshore'), core.Channel(102, sensor_curve='lakeshore')]))
            with patch('ui.app.BASE', Path(directory)):
                app = App()
            app.withdraw()
            try:
                app.channel_vars['sensor_curve'].set(CURVE_LABELS['user_table'])
                app.update_curve_editor()
                self.assertEqual(float(app.channel_vars['min_temperature'].get()), 35.15)
                app.channel_vars['max_temperature'].set('349.15')
                app.add_channel()
                self.assertEqual(app.table.set('101', 'sensor_curve'), CURVE_LABELS['user_table'])
                cfg = app.collect()
                self.assertEqual([c.sensor_curve for c in cfg.channels], ['user_table', 'lakeshore'])
                app.table.selection_set('101')
                app.select_channel()
                self.assertEqual(app.channel_vars['sensor_curve'].get(), CURVE_LABELS['user_table'])
                core.save_config(path, cfg)
            finally:
                app.destroy()
            with patch('ui.app.BASE', Path(directory)):
                reopened = App()
            reopened.withdraw()
            try:
                self.assertEqual(reopened.table.set('101', 'sensor_curve'), CURVE_LABELS['user_table'])
                self.assertEqual(float(reopened.table.set('101', 'min_temperature')), 35.15)
                self.assertNotIn('配置读取失败', reopened.system_text.get())
            finally:
                reopened.destroy()
