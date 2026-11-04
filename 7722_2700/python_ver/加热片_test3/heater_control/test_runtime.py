import csv
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import core
from drive_profile import Profile, Step
from runtime import RunControls


TIME = Profile('time', [Step(0, 'voltage', 3)])
TEMP = Profile('temperature', [Step(30, 'voltage', 3)])


class IndependentRuntimeTests(unittest.TestCase):
    def test_worker_and_port_remain_open_until_output_off_confirmed(self):
        pending, release = threading.Event(), threading.Event()
        original = core.SimTransport
        class DelayedOff(original):
            def write(self, command):
                if command == 'OUTP:STAT OFF' and self.state['output'] and not release.is_set():
                    self.commands.append(command)
                    return
                super().write(command)
        tp = DelayedOff()
        events, stop = [], threading.Event()
        controls = RunControls(drive=True, profile=TIME)
        def emit(kind, value):
            events.append((kind, value))
            if kind == 'shutdown_pending':
                pending.set()
        with tempfile.TemporaryDirectory() as d, patch.object(core, 'SimTransport', return_value=tp):
            worker = threading.Thread(target=core.run, args=(core.Config(interval=.05), stop, emit),
                                      kwargs=dict(simulation=True, max_cycles=1, output_dir=d, controls=controls))
            worker.start()
            try:
                self.assertTrue(pending.wait(2))
                stop.set()
                self.assertTrue(worker.is_alive())
                self.assertFalse(tp.closed)
                self.assertTrue(tp.state['output'])
                self.assertFalse(any(k == 'done' for k, _ in events))
            finally:
                release.set()
                worker.join(4)
            self.assertFalse(worker.is_alive())
            self.assertFalse(tp.state['output'])
            self.assertTrue(tp.closed)
            self.assertTrue(events[-1][1]['off_ok'])

    def test_overtemperature_turns_off_before_next_channel(self):
        transports, next_channel_output = [], []
        original = core.SimTransport
        count = 0
        def build():
            transport = original()
            transports.append(transport)
            return transport
        def read(meter, channel, stop):
            nonlocal count
            if channel.channel == meter.cfg.reference_channel:
                count += 1
                if count == 2:
                    self.assertTrue(transports[0].state['output'])
                    return core.resistance(90), 90
            elif count >= 2:
                next_channel_output.append(transports[0].state['output'])
            return 100, 0
        with patch.object(core, 'SimTransport', build), patch.object(core.SimMeter, 'read', read):
            events, rows = self.execute(RunControls(True, True, TIME), max_cycles=3)
        self.assertEqual(next_channel_output, [False, False])
        self.assertEqual(len(rows), 6)
        self.assertFalse(events[-1][1]['error'])
        self.assertTrue(any(k == 'drive_done' and '温度越限' in v['error'] for k, v in events))

    def test_individual_channel_limits_and_recording(self):
        cfg = core.Config(interval=.05, channels=[core.Channel(101, 0, -50, 100), core.Channel(102, 0, -20, 80)])
        with patch.object(core.SimMeter, 'read', lambda *args: (core.resistance(90), 90)):
            events, rows = self.execute(RunControls(True, True, TIME), cfg=cfg, max_cycles=2)
        sample = next(v for k, v in events if k == 'sample')
        self.assertNotIn(101, sample['measurement_errors'])
        self.assertIn('温度越限', sample['measurement_errors'][102])
        self.assertTrue(any(k == 'drive_done' and '通道102' in v['error'] for k, v in events))
        self.assertFalse(events[-1][1]['error'])

    def test_channel_limits_migrate_validate_and_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/'config.ini'
            path.write_text('[Settings]\nmin_temperature=-40\nmax_temperature=70\n'
                            '[Channel 101]\n[Channel 102]\nmax_temperature=90\n', encoding='utf-8')
            cfg = core.load_config(path)
            self.assertEqual([(c.min_temperature, c.max_temperature) for c in cfg.channels], [(-40,70), (-40,90)])
            core.save_config(path, cfg)
            self.assertEqual(core.load_config(path), cfg)
            self.assertNotIn('max_temperature', path.read_text().split('[Channel')[0])
            for lo, hi in ((20,20), (-201,80), (-50,851), (float('nan'),80)):
                cfg.channels[0].min_temperature, cfg.channels[0].max_temperature = lo, hi
                with self.assertRaisesRegex(ValueError, '通道 101'):
                    cfg.validate()

    def test_interval_between_channels_including_round_boundary(self):
        starts = []
        def read(meter, channel, stop):
            starts.append((channel.channel, time.monotonic()))
            return 100, 0
        with patch.object(core.SimMeter, 'read', read):
            events, _ = self.execute(RunControls(acquisition=True), max_cycles=2)
        self.assertEqual([ch for ch, _ in starts], [102, 101, 102, 101])
        for (_, before), (_, after) in zip(starts, starts[1:]):
            self.assertGreaterEqual(after-before, .045)
        self.assertFalse(events[-1][1]['error'])

    def test_stopping_acquisition_interrupts_channel_interval(self):
        controls = RunControls(acquisition=True)
        calls = []
        timer = None
        def read(meter, channel, stop):
            nonlocal timer
            calls.append(channel.channel)
            timer = threading.Timer(.05, controls.acquisition.clear)
            timer.start()
            return 100, 0
        started = time.monotonic()
        with patch.object(core.SimMeter, 'read', read):
            events, _ = self.execute(controls, cfg=core.Config(interval=60))
        if timer:
            timer.join()
        self.assertEqual(len(calls), 1)
        self.assertLess(time.monotonic()-started, 2)
        self.assertFalse(events[-1][1]['error'])

    def test_invalid_readings_are_recorded_and_recover_without_stopping(self):
        counts = {}
        def read(meter, channel, stop):
            n = counts.get(channel.channel, 0)
            counts[channel.channel] = n + 1
            if channel.channel == 101 and n == 0:
                return 9.9e37, None
            if channel.channel == 101 and n == 1:
                return core.resistance(90), 90
            return 100, 0
        with patch.object(core.SimMeter, 'read', read):
            events, rows = self.execute(RunControls(acquisition=True), max_cycles=3)
        self.assertEqual(len(rows), 6)
        bad = next(row for row in rows if row['resistance_ohm'] == '9.9e+37')
        self.assertEqual(bad['temperature_K'], '')
        samples = [v for k, v in events if k == 'sample']
        self.assertIn('电阻无效', samples[0]['measurement_errors'][101])
        self.assertIn('温度越限', samples[1]['measurement_errors'][101])
        self.assertEqual(samples[-1]['measurement_errors'], {})
        self.assertNotIn('measurement_status', bad)
        self.assertFalse(events[-1][1]['error'])

    def test_invalid_temperature_stops_drive_but_preserves_acquisition(self):
        def read(meter, channel, stop):
            return 9.9e37, None
        with patch.object(core.SimMeter, 'read', read):
            events, rows = self.execute(RunControls(True, True, TEMP), max_cycles=3)
        self.assertEqual(len(rows), 6)
        self.assertTrue(any(k == 'drive_done' and '采集与记录继续' in v['error'] for k, v in events))
        self.assertTrue(all(row['power_W'] == '' for row in rows))
        self.assertFalse(events[-1][1]['error'])

    def execute(self, controls, callback=None, cfg=None, max_cycles=8):
        events = []
        def emit(kind, value):
            events.append((kind, value))
            if callback:
                callback(kind, value)
        with tempfile.TemporaryDirectory() as d:
            core.run(cfg or core.Config(interval=.05), threading.Event(), emit, simulation=True,
                     output_dir=d, max_cycles=max_cycles, controls=controls)
            files = list(Path(d).glob('*.csv'))
            if files:
                with files[0].open(encoding='utf-8-sig', newline='') as f:
                    rows = list(csv.DictReader(f))
            else:
                rows = []
        return events, rows

    def test_acquisition_only_never_constructs_power_or_loads_profile(self):
        with patch.object(core, 'ITech7722', side_effect=AssertionError('不应访问电源')):
            events, rows = self.execute(RunControls(acquisition=True), cfg=core.Config(interval=.05, profile_file='missing.ini'), max_cycles=2)
        self.assertFalse(events[-1][1]['error'])
        self.assertEqual(len(rows), 4)
        self.assertTrue(all(row['power_W'] == '' and row['setting_voltage_V'] == '' for row in rows))
        self.assertTrue(all(v['power'] is None for k, v in events if k == 'sample'))

    def test_time_drive_only_never_constructs_meter(self):
        profile = Profile('time', [Step(0, 'voltage', 3), Step(.001, 'power', .1), Step(.002, 'voltage', 0)])
        with patch.object(core, 'SimMeter', side_effect=AssertionError('不应访问2700')), patch.object(core, 'Meter', side_effect=AssertionError('不应访问2700')):
            events, rows = self.execute(RunControls(drive=True, profile=profile), max_cycles=6)
        self.assertFalse(events[-1][1]['error'])
        self.assertTrue(all(row['channel'] == '' and row['temperature_K'] == '' for row in rows))
        self.assertEqual({row['stage'] for row in rows}, {'1', '2', '3'})
        self.assertEqual(rows[-1]['output_on'], 'False')
        self.assertTrue(all(not value['temperatures'] for kind, value in events if kind == 'sample'))

    def test_stopping_drive_preserves_acquisition(self):
        controls = RunControls(True, True, TIME)
        acquired_after_stop = []
        def update(kind, value):
            if kind == 'sample':
                if value['power'] is not None:
                    controls.drive.clear()
                else:
                    acquired_after_stop.append(value)
                    if len(acquired_after_stop) >= 2:
                        controls.acquisition.clear()
        events, rows = self.execute(controls, update)
        self.assertFalse(events[-1][1]['error'])
        self.assertEqual(len(acquired_after_stop), 2)
        self.assertTrue(all(v['temperatures'] for v in acquired_after_stop))
        self.assertTrue(any(kind == 'drive_done' for kind, _ in events))
        self.assertTrue(any(row['power_W'] == '' for row in rows))

    def test_stopping_acquisition_preserves_time_drive(self):
        controls = RunControls(True, True, TIME)
        drive_after_stop = []
        def update(kind, value):
            if kind == 'sample':
                if value['temperatures']:
                    controls.acquisition.clear()
                else:
                    drive_after_stop.append(value)
                    if len(drive_after_stop) >= 2:
                        controls.drive.clear()
        events, _ = self.execute(controls, update)
        self.assertFalse(events[-1][1]['error'])
        self.assertEqual(len(drive_after_stop), 2)
        self.assertTrue(all(v['power'] is not None for v in drive_after_stop))

    def test_temperature_drive_requires_acquisition_before_opening_power(self):
        with patch.object(core, 'SimTransport') as transport:
            events, _ = self.execute(RunControls(drive=True, profile=TEMP))
        transport.assert_not_called()
        self.assertIn('先启动2700采集', events[-1][1]['error'])

    def test_stopping_acquisition_stops_temperature_drive(self):
        controls = RunControls(True, True, TEMP)
        def update(kind, value):
            if kind == 'sample':
                controls.acquisition.clear()
        events, _ = self.execute(controls, update)
        self.assertFalse(events[-1][1]['error'])
        self.assertFalse(controls.drive.is_set())
        self.assertEqual(len([1 for k, v in events if k == 'sample']), 1)
        self.assertTrue(any(k == 'drive_done' and v['off_ok'] for k, v in events))

    def test_add_drive_to_acquisition_and_restart_drive(self):
        controls = RunControls(acquisition=True)
        drive_samples, drive_done = [], []
        def update(kind, value):
            if kind == 'sample':
                if value['power'] is None and not drive_samples:
                    controls.start_drive(TIME)
                elif value['power'] is not None:
                    drive_samples.append(value)
                    controls.drive.clear()
                    if len(drive_samples) == 2:
                        controls.acquisition.clear()
            elif kind == 'drive_done':
                drive_done.append(value)
                if len(drive_done) == 1:
                    controls.start_drive(TIME)
        events, _ = self.execute(controls, update)
        self.assertFalse(events[-1][1]['error'])
        self.assertEqual(len(drive_samples), 2)
        self.assertEqual(len(drive_done), 2)
        self.assertTrue(all(v['drive_elapsed_min'] < .01 for v in drive_samples))
        self.assertTrue(all(v['temperatures'] for v in drive_samples))

    def test_drive_request_cancelled_before_open_is_acknowledged(self):
        controls = RunControls(acquisition=True)
        cancelled = []
        def update(kind, value):
            if kind == 'sample' and not cancelled:
                cancelled.append(True)
                controls.start_drive(TIME)
                controls.drive.clear()
            elif kind == 'drive_done':
                controls.acquisition.clear()
        with patch.object(core, 'ITech7722', side_effect=AssertionError('已取消的驱动不应连接电源')):
            events, _ = self.execute(controls, update)
        self.assertFalse(events[-1][1]['error'])
        self.assertEqual(len([1 for k, v in events if k == 'drive_done']), 1)

    def test_acquisition_request_cancelled_before_open_is_acknowledged(self):
        controls = RunControls(drive=True, profile=TIME)
        cancelled = []
        def update(kind, value):
            if kind == 'sample' and not cancelled:
                cancelled.append(True)
                controls.start_acquisition()
                controls.acquisition.clear()
            elif kind == 'acquisition_done':
                controls.drive.clear()
        with patch.object(core, 'SimMeter', side_effect=AssertionError('已取消的采集不应连接2700')):
            events, _ = self.execute(controls, update)
        self.assertFalse(events[-1][1]['error'])
        self.assertEqual(len([1 for k, v in events if k == 'acquisition_done']), 1)


if __name__ == '__main__':
    unittest.main()
