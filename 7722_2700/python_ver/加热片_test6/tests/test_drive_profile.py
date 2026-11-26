from tests.csv_helpers import legacy_rows
import csv
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from core import Config, run
from drive_profile import DriveController, Profile, Sequencer, Step, load_profile


class DriveTests(unittest.TestCase):
    def test_default_seconds_and_commented_examples(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'drive.ini'
            path.write_text('[Drive]\nstrategy=time\ntime_unit=s\n'
                            '[Step 1]\nat=10\nmode=voltage\nvalue=5\n'
                            '[Step 2]\nat=11\nmode=voltage\nvalue=10\n'
                            '; [Step 3]\n; at=20\n', encoding='utf-8')
            profile = load_profile(path, Config(max_voltage=10))
        self.assertEqual(len(profile.steps), 2)
        sequence = Sequencer(profile)
        for seconds, target in ((9.999, 0), (10, 5), (10.999, 5), (11, 10), (40, 10)):
            self.assertEqual(sequence.select(seconds, None).value, target)

    def test_explicit_minute_file_converts_to_seconds(self):
        from drive_profile import profile_text_kelvin
        text = ('[Drive]\nstrategy=mixed\ntime_unit=min\ntemperature_unit=K\n'
                '[Step 1]\ntrigger=time_or_temperature\nat=1\ntemperature_at=280\n'
                'mode=voltage\nvalue=3\n')
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/'profile.ini'
            path.write_text(text, encoding='utf-8')
            original = load_profile(path, Config())
            self.assertEqual(original.steps[0].at, 60)
            converted = profile_text_kelvin(text)
            path.write_text(converted, encoding='utf-8')
            self.assertEqual(load_profile(path, Config()), original)
            self.assertEqual(profile_text_kelvin(converted), converted)

    def test_time_boundaries_and_mixed_modes(self):
        profile = Profile('time', [Step(1, 'voltage', 3), Step(2, 'power', .2), Step(5, 'voltage', 0)])
        profile.validate(Config())
        sequence = Sequencer(profile)
        self.assertEqual(sequence.select(.999, 20).value, 0)
        self.assertEqual(sequence.select(1, 20), profile.steps[0])
        self.assertEqual(sequence.select(2, 20), profile.steps[1])
        self.assertEqual(sequence.select(100, 20), profile.steps[2])

    def test_cooling_thresholds_never_retreat(self):
        profile = Profile('temperature', [Step(30, 'voltage', 3), Step(10, 'power', .2), Step(0, 'voltage', 0)])
        profile.validate(Config())
        sequence = Sequencer(profile)
        self.assertEqual(sequence.select(0, 31).value, 0)
        self.assertEqual(sequence.index, -1)
        self.assertEqual(sequence.select(0, 30), profile.steps[0])
        self.assertEqual(sequence.select(0, 10), profile.steps[1])
        self.assertEqual(sequence.select(0, 40), profile.steps[1])
        self.assertEqual(sequence.select(0, 0), profile.steps[2])
        self.assertEqual(sequence.select(0, 20), profile.steps[2])
        cold_start = Sequencer(profile)
        self.assertEqual(cold_start.select(0, -1), profile.steps[-1])

    def test_reject_invalid_profiles(self):
        for profile in (Profile('bad'), Profile('time', []),
                        Profile('time', [Step(0, 'voltage', 11)]),
                        Profile('time', [Step(0, 'power', 20)]),
                        Profile('time', [Step(0, 'CP', 1)]),
                        Profile('time', [Step(float('nan'), 'voltage', 0)]),
                        Profile('time', [Step(-1, 'voltage', 0)]),
                        Profile('time', [Step(1, 'voltage', 0), Step(1, 'voltage', 1)]),
                        Profile('temperature', [Step(10, 'voltage', 0), Step(20, 'voltage', 1)]),
                        Profile('temperature', [Step(-243.15, 'voltage', 0)])):
            with self.subTest(profile=profile), self.assertRaises(ValueError):
                profile.validate(Config())

    def test_file_sequence_validation_and_examples(self):
        base = Path(__file__).resolve().parents[1]
        self.assertEqual(load_profile(base/'drive_time.ini', Config()).strategy, 'time')
        self.assertEqual(load_profile(base/'drive_temperature.ini', Config()).strategy, 'temperature')
        with tempfile.TemporaryDirectory() as d:
            file = Path(d)/'bad.ini'
            file.write_text('[Drive]\nstrategy=time\n[Step 2]\nat=0\nmode=voltage\nvalue=0\n', encoding='utf-8')
            with self.assertRaises(ValueError):
                load_profile(file, Config())

    def test_mixed_triggers_wait_in_order_and_never_retreat(self):
        profile = Profile('mixed', [Step(0, 'voltage', 3, 'time'),
                                   Step(10, 'power', .2, 'temperature'),
                                   Step(5, 'voltage', 0, 'time')])
        profile.validate(Config())
        sequence = Sequencer(profile)
        self.assertEqual(sequence.select(0, 20), profile.steps[0])
        self.assertEqual(sequence.select(6, 20), profile.steps[0])
        self.assertEqual(sequence.select(6, 10), profile.steps[2])
        self.assertEqual(sequence.select(7, 30), profile.steps[2])

    def test_mixed_file_units_and_validation(self):
        from drive_profile import profile_text_kelvin
        text = ('[Drive]\nstrategy=mixed\n[Step 1]\ntrigger=time\nat=0\nmode=voltage\nvalue=3\n'
                '[Step 2]\ntrigger=temperature\nat=10\nmode=power\nvalue=.2\n')
        converted = profile_text_kelvin(text)
        self.assertIn('at = 283.15', converted)
        self.assertEqual(profile_text_kelvin(converted), converted)
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/'mixed.ini'
            path.write_text(text, encoding='utf-8')
            original = load_profile(path, Config())
            path.write_text(converted, encoding='utf-8')
            self.assertEqual(load_profile(path, Config()), original)
            self.assertTrue(original.requires_temperature)
            self.assertEqual(original.steps[0].at, 0)
        for profile in (Profile('mixed', [Step(0, 'voltage', 0)]),
                        Profile('mixed', [Step(100, 'voltage', 0, 'temperature')]),
                        Profile('mixed', [Step(0, 'voltage', 0, 'bad')])):
            with self.assertRaises(ValueError):
                profile.validate(Config())

    def test_mixed_temperature_requires_acquisition_before_opening_ports(self):
        from runtime import RunControls
        profile = Profile('mixed', [Step(0, 'voltage', 3, 'time'),
                                   Step(10, 'voltage', 0, 'temperature')])
        events = []
        with patch('core.SerialTransport') as transport:
            run(Config(), threading.Event(), lambda *event: events.append(event),
                profile=profile, controls=RunControls(drive=True, profile=profile))
        transport.assert_not_called()
        self.assertTrue(events[-1][1]['error'])

    def test_either_trigger_time_first_or_temperature_first(self):
        profile = Profile('mixed', [Step(5, 'voltage', 3, 'time_or_temperature', 10),
                                   Step(10, 'voltage', 0, 'time')])
        profile.validate(Config())
        self.assertTrue(profile.requires_temperature)
        for minute, temperature in ((5, 20), (1, 10), (5, 10)):
            sequence = Sequencer(profile)
            self.assertEqual(sequence.select(0, 20).value, 0)
            self.assertEqual(sequence.select(minute, temperature), profile.steps[0])
            self.assertEqual(sequence.select(6, 30), profile.steps[0])
        from drive_profile import profile_text_kelvin
        text = ('[Drive]\nstrategy=mixed\n[Step 1]\ntrigger=time_or_temperature\n'
                'at=5\ntemperature_at=10\nmode=voltage\nvalue=3\n')
        converted = profile_text_kelvin(text)
        self.assertIn('temperature_at = 283.15', converted)
        self.assertIn('at = 5', converted)
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/'mixed.ini'
            path.write_text(converted, encoding='utf-8')
            self.assertEqual(load_profile(path, Config()).steps[0], profile.steps[0])
        with self.assertRaises(ValueError):
            Profile('mixed', [Step(5, 'voltage', 3, 'time_or_temperature')]).validate(Config())

    def test_power_reaches_and_holds(self):
        cfg = Config(max_voltage=20)
        controller = DriveController(cfg)
        for now in range(40):
            power = controller.voltage**2/50
            controller.update(Step(0, 'power', 2), power, 1, now)
        self.assertAlmostEqual(controller.voltage**2/50, 2, delta=.1)
        previous = controller.voltage
        self.assertEqual(controller.update(Step(0, 'power', 2), 2.01, 1, 41), previous)

    def test_unreachable_power_stops_and_change_resets_timeout(self):
        cfg = Config(power_timeout=5)
        controller = DriveController(cfg)
        controller.update(Step(0, 'power', 10), 0, 1, 0)
        with self.assertRaisesRegex(RuntimeError, '持续未达到'):
            controller.update(Step(0, 'power', 10), 0, 1, 5)
        controller.update(Step(0, 'power', 1), 0, 1, 6)
        self.assertEqual(controller.outside_since, 6)

    def test_invalid_plan_never_opens_port(self):
        events = []
        with patch('core.SerialTransport') as transport:
            run(Config(), threading.Event(), lambda *event: events.append(event), profile=Profile('time', [Step(0, 'voltage', 500)]))
        transport.assert_not_called()
        self.assertTrue(events[-1][1]['error'])

    def test_runtime_mixed_stages_and_zero_turns_output_off(self):
        cfg = Config(interval=.05)
        profile = Profile('time', [Step(0, 'voltage', 3), Step(0.15, 'power', .1), Step(0.3, 'voltage', 0)])
        events = []
        with tempfile.TemporaryDirectory() as d:
            run(cfg, threading.Event(), lambda *event: events.append(event), True, max_cycles=5, output_dir=d, profile=profile)
            self.assertFalse(events[-1][1]['error'])
            with next(Path(d).glob('*.csv')).open(encoding='utf-8-sig', newline='') as f:
                rows = list(legacy_rows(f))
            self.assertEqual({r['stage'] for r in rows}, {'1', '2', '3'})
            self.assertEqual({r['drive_mode'] for r in rows}, {'voltage', 'power'})
            self.assertEqual(rows[-1]['output_on'], 'False')
            self.assertEqual(float(rows[-1]['setting_voltage_V']), 0)

    def test_runtime_temperature_rebound_keeps_stage(self):
        temperatures = iter((40., 30., 10., 20., 0.))
        current = [40.]
        def read(meter, channel, stop):
            if channel.channel == meter.cfg.reference_channel:
                current[0] = next(temperatures)
            return 100., current[0]
        profile = Profile('temperature', [Step(30, 'voltage', 3), Step(10, 'power', .1), Step(0, 'voltage', 0)])
        events = []
        with tempfile.TemporaryDirectory() as d, patch('core.SimMeter.read', read):
            run(Config(interval=.05), threading.Event(), lambda *event: events.append(event), True,
                max_cycles=5, output_dir=d, profile=profile)
        self.assertFalse(events[-1][1]['error'])
        samples = [value for kind, value in events if kind == 'sample']
        self.assertEqual([value['stage'] for value in samples], [0, 1, 2, 2, 3])
        self.assertEqual([value['output_on'] for value in samples], [False, True, True, True, False])


if __name__ == '__main__':
    unittest.main()
