from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import core
from drive_profile import Profile, Step
from runtime import RunControls


class HeaterTests(unittest.TestCase):
    def test_duplicate_port_and_invalid_target(self):
        for changes in (dict(heater_power=20), dict(heater_power=float('nan'))):
            with self.assertRaises(ValueError):
                core.Config(heater_enabled=True, **changes).validate()

    def test_configuration_round_trip(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'config.ini'
            cfg = core.Config(heater_enabled=True, heater_power=.3)
            core.save_config(path, cfg)
            loaded = core.load_config(path)
            self.assertTrue(loaded.heater_enabled)
            self.assertEqual(loaded.heater_power, .3)

    def test_heater_tracks_power_and_both_outputs_close(self):
        devices, events = [], []
        original = core.SimTransport
        def build():
            device = original()
            devices.append(device)
            return device
        controls = RunControls(heater_link=False, heater=True, drive=True, profile=Profile('time', [Step(0, 'voltage', 1)]))
        cfg = core.Config(heater_enabled=True, interval=.05, power_slew_v_s=50)
        with tempfile.TemporaryDirectory() as folder, patch.object(core, 'SimTransport', build):
            core.run(cfg, threading.Event(), lambda k, v: events.append((k, v)),
                     simulation=True, max_cycles=5, output_dir=folder, controls=controls)
        readings = [v for k, v in events if k == 'heater_sample']
        self.assertTrue(readings[-1]['reached'])
        self.assertAlmostEqual(readings[-1]['power'], cfg.heater_power)
        self.assertEqual(len(devices), 2)
        self.assertEqual(devices[1].state['mode'], 'DC')
        self.assertIn('MEAS:VOLT:DC?', devices[1].commands)
        self.assertNotIn('MEAS:VOLT:AC?', devices[1].commands)
        self.assertTrue(all(d.closed and not d.state['output'] for d in devices))
        self.assertFalse(events[-1][1]['error'])

    def test_unreachable_heater_target_stops_both(self):
        events = []
        cfg = core.Config(heater_enabled=True, heater_max_voltage=.1, interval=.05, power_timeout=.1)
        controls = RunControls(heater_link=False, heater=True, drive=True, profile=Profile('time', [Step(0, 'voltage', 1)]))
        with tempfile.TemporaryDirectory() as folder:
            core.run(cfg, threading.Event(), lambda k, v: events.append((k, v)),
                     simulation=True, max_cycles=10, output_dir=folder, controls=controls)
        self.assertIn('功率目标持续未达到', events[-1][1]['error'])
        self.assertTrue(events[-1][1]['off_ok'])

    def test_blocked_meter_turns_off_and_inhibits_both_supplies(self):
        from acquisition_watchdog import AcquisitionWatchdog
        release, blocked, confirmed = threading.Event(), threading.Event(), threading.Event()
        devices, events = [], []
        original = core.SimTransport
        class FastWatchdog(AcquisitionWatchdog):
            def expect(self, seconds, phase):
                super().expect(.25, phase)
        def build():
            device = original()
            devices.append(device)
            return device
        calls = 0
        def read(meter, channel, stop):
            nonlocal calls
            calls += 1
            if calls == 3:
                blocked.set()
                release.wait(5)
            return 100, 0
        def emit(kind, value):
            events.append((kind, value))
            if kind == 'shutdown_confirmed':
                confirmed.set()
        controls = RunControls(True, True, Profile('time', [Step(0, 'voltage', 1)]), heater=True, heater_link=False)
        cfg = core.Config(heater_enabled=True, interval=.05)
        with tempfile.TemporaryDirectory() as folder, patch('runtime.AcquisitionWatchdog', FastWatchdog), \
                patch.object(core, 'SimTransport', build), patch.object(core.SimMeter, 'read', read):
            worker = threading.Thread(target=core.run, args=(cfg, threading.Event(), emit),
                                      kwargs=dict(simulation=True, max_cycles=3, output_dir=folder, controls=controls))
            worker.start()
            try:
                self.assertTrue(blocked.wait(2))
                self.assertEqual(len(devices), 2)
                self.assertTrue(all(d.state['output'] for d in devices))
                self.assertTrue(confirmed.wait(2))
                self.assertTrue(worker.is_alive())
                self.assertTrue(all(not d.state['output'] and not d.closed for d in devices))
            finally:
                release.set()
                worker.join(4)
            self.assertFalse(worker.is_alive())
            self.assertTrue(all(d.closed for d in devices))
            self.assertIn('采集无响应', events[-1][1]['error'])

    def test_dc_voltage_setting_and_zero_output(self):
        for target in (2., 0.):
            with self.subTest(target=target):
                devices, events, output_states = [], [], []
                original = core.SimTransport
                def build():
                    device = original()
                    devices.append(device)
                    return device
                def emit(kind, value):
                    events.append((kind, value))
                    if kind == 'heater_sample':
                        output_states.append(devices[1].state['output'])
                controls = RunControls(heater_link=False, heater=True, drive=True, profile=Profile('time', [Step(0, 'voltage', 1)]))
                cfg = core.Config(heater_enabled=True, heater_control='voltage', heater_voltage=target, interval=.05)
                with tempfile.TemporaryDirectory() as folder, patch.object(core, 'SimTransport', build):
                    core.run(cfg, threading.Event(), emit, simulation=True, max_cycles=2,
                             output_dir=folder, controls=controls)
                readings = [v for k, v in events if k == 'heater_sample']
                self.assertEqual(len(readings), 2)
                self.assertTrue(all(v['voltage'] == target and v['control'] == 'voltage' for v in readings))
                self.assertEqual(output_states, [target > 0, target > 0])
                self.assertEqual(devices[1].state['mode'], 'DC')
                self.assertNotIn('SOUR:NORM:FREQ 50', devices[1].commands)
                self.assertFalse(events[-1][1]['error'])

    def test_invalid_voltage_mode_settings(self):
        for changes in (dict(heater_control='invalid'), dict(heater_control='voltage', heater_voltage=6)):
            with self.assertRaises(ValueError):
                core.Config(heater_enabled=True, **changes).validate()

    def test_single_power_connection_automatically_starts_meter(self):
        for mode, target in (('voltage', 2.), ('power', .2)):
            with self.subTest(mode=mode):
                class FakeSerial(core.SimTransport):
                    def query(self, command):
                        if command == '*IDN?':
                            return 'ITECH,IT7722,TEST,1.0'
                        return super().query(command)
                device = FakeSerial()
                events = []
                cfg = core.Config(heater_enabled=True, heater_port='COM6', heater_control=mode,
                                  heater_voltage=2., heater_power=.2, profile_file='missing.ini',
                                  interval=.05, power_slew_v_s=50)
                with tempfile.TemporaryDirectory() as folder, \
                        patch.object(core, 'SerialTransport', return_value=device) as serial, \
                        patch.object(core, 'Meter', side_effect=lambda cfg, log: core.SimMeter(cfg, None)) as meter, \
                        patch('runtime.load_profile', side_effect=AssertionError('profile must not load')):
                    core.run(cfg, threading.Event(), lambda k,v: events.append((k,v)),
                             max_cycles=5, output_dir=folder, controls=RunControls(heater_link=False, heater=True))
                serial.assert_called_once_with('COM6', 9600, timeout=cfg.timeout)
                meter.assert_called_once()
                readings = [v for k,v in events if k == 'heater_sample']
                self.assertTrue(readings[-1]['reached'])
                self.assertEqual(readings[-1]['target'], target)
                self.assertTrue(device.closed)
                self.assertFalse(device.state['output'])
                self.assertNotIn('SOUR:NORM:MODE AC', device.commands)
                self.assertFalse(events[-1][1]['error'])

    def test_reject_simultaneous_same_port_before_open(self):
        events = []
        cfg = core.Config(heater_enabled=True, heater_port='COM6')
        controls = RunControls(heater_link=False, heater=True, drive=True, profile=Profile('time', [Step(0,'voltage',1)]))
        with tempfile.TemporaryDirectory() as folder, patch.object(core, 'SerialTransport') as serial:
            core.run(cfg, threading.Event(), lambda k,v: events.append((k,v)), output_dir=folder, controls=controls)
        serial.assert_not_called()
        self.assertIn('同一串口', events[-1][1]['error'])

    def test_independent_stop_keeps_other_supply_running(self):
        for cancelled in ('drive', 'heater'):
            with self.subTest(cancelled=cancelled):
                events = []
                controls = RunControls(heater_link=False, heater=True, drive=True, profile=Profile('time', [Step(0,'voltage',1)]))
                count = 0
                def emit(kind, value):
                    nonlocal count
                    events.append((kind,value))
                    if kind == 'heater_sample':
                        count += 1
                        if count == 1:
                            getattr(controls, cancelled).clear()
                cfg = core.Config(heater_enabled=True, heater_control='voltage', heater_voltage=1, interval=.05)
                with tempfile.TemporaryDirectory() as folder:
                    core.run(cfg, threading.Event(), emit, simulation=True, max_cycles=4, output_dir=folder, controls=controls)
                index = next(i for i,(k,v) in enumerate(events) if k == cancelled+'_done')
                remaining = events[index+1:]
                if cancelled == 'drive':
                    self.assertTrue(any(k == 'heater_sample' for k,v in remaining))
                else:
                    self.assertTrue(any(k == 'sample' and v['power'] is not None for k,v in remaining))
                self.assertFalse(events[-1][1]['error'])

    def test_dc_signed_current_is_preserved_and_magnitude_protected(self):
        cfg = core.Config(max_current=1.)
        for value in (-.0003, -.0006, -.003, -.25, 0., .25):
            measurements = dict(voltage=0., current=value, power=0.)
            core.check_measurements(measurements, cfg, mode='DC')
            self.assertEqual(measurements['current'], value)
        for mode, current in (('DC', -1.01), ('DC', 1.01), ('AC', -.0003),
                              ('DC', float('nan')), ('DC', float('inf'))):
            with self.subTest(mode=mode, current=current), self.assertRaises(RuntimeError):
                core.check_measurements(dict(voltage=0., current=current, power=0.), cfg, mode=mode)

    def test_logged_startup_sequence_stale_power_and_delayed_readback(self):
        class DelayedMeasurement(core.SimTransport):
            def __init__(self):
                super().__init__()
                self.after_voltage_write = False
                self.delayed_remaining = 2
                self.applied = []
            def write(self, command):
                super().write(command)
                if command.startswith('SOUR:NORM:VOLT:DC '):
                    self.after_voltage_write = self.delayed_remaining > 0
                    self.applied.append(self.state['voltage'])
            def query(self, command):
                if command == 'MEAS:VOLT:DC?' and self.after_voltage_write:
                    return '0.00'
                if command == 'MEAS:CURR:DC?' and self.after_voltage_write:
                    return '-0.0003' if self.state['output'] else '-0.0006'
                if command == 'MEAS:POW?':
                    if not self.state['output']:
                        return '0.317'
                    if self.after_voltage_write:
                        self.after_voltage_write = False
                        self.delayed_remaining -= 1
                        return '0.000'
                return super().query(command)
        for mode in ('power', 'voltage'):
            with self.subTest(mode=mode):
                device, events = DelayedMeasurement(), []
                cfg = core.Config(heater_enabled=True, heater_control=mode, heater_voltage=1., heater_power=.5,
                                  heater_max_voltage=15., interval=.05, power_slew_v_s=50.)
                with tempfile.TemporaryDirectory() as folder, patch.object(core,'SimTransport',return_value=device):
                    core.run(cfg, threading.Event(), lambda k,v: events.append((k,v)), simulation=True,
                             max_cycles=8, output_dir=folder, controls=RunControls(heater_link=False, heater=True))
                readings = [v for k,v in events if k == 'heater_sample']
                self.assertEqual(len(readings), 8)
                self.assertEqual(readings[0]['current'], -.0003)
                self.assertGreater(device.applied[1], 0.)
                self.assertTrue(any(v['reached'] for v in readings[1:]) if mode == 'power' else device.applied[-1] == 1.)
                self.assertFalse(events[-1][1]['error'])
                self.assertFalse(device.state['output'])

    def test_link_default_blocks_heater_without_drive(self):
        controls, events = RunControls(heater=True), []
        self.assertTrue(controls.heater_link.is_set())
        with tempfile.TemporaryDirectory() as folder, patch.object(core, 'SimTransport') as transport:
            core.run(core.Config(), threading.Event(), lambda k,v: events.append((k,v)),
                     simulation=True, max_cycles=1, output_dir=folder, controls=controls)
        transport.assert_not_called()
        self.assertTrue(any(k == 'heater_done' and '联动保护' in v['error'] for k,v in events))

    def test_link_allows_heater_after_drive_output_and_stops_on_drive_stop(self):
        controls = RunControls(drive=True, profile=Profile('time',[Step(0,'voltage',1)]))
        events = []
        def emit(kind, value):
            events.append((kind,value))
            if kind == 'sample' and value['output_on'] and not controls.heater.is_set():
                controls.start_heater()
            if kind == 'heater_sample':
                controls.drive.clear()
        with tempfile.TemporaryDirectory() as folder:
            core.run(core.Config(heater_enabled=True, heater_control='voltage', heater_voltage=1, interval=.05),
                     threading.Event(), emit, simulation=True, max_cycles=6, output_dir=folder, controls=controls)
        self.assertTrue(any(k == 'heater_sample' for k,v in events))
        self.assertTrue(any(k == 'heater_done' and v['off_ok'] for k,v in events))
        self.assertFalse(controls.drive_ready.is_set())
        self.assertFalse(controls.heater.is_set())
        self.assertFalse(events[-1][1]['error'])

    def test_link_stops_heater_at_zero_drive_stage(self):
        controls = RunControls(drive=True, profile=Profile('time',[Step(0,'voltage',1),Step(.002,'voltage',0)]))
        events=[]
        started=False
        def emit(kind, value):
            nonlocal started
            events.append((kind,value))
            if kind == 'sample' and value['output_on'] and not started:
                started=True
                controls.start_heater()
        with tempfile.TemporaryDirectory() as folder:
            core.run(core.Config(heater_enabled=True, heater_control='voltage',heater_voltage=1,interval=.05),
                     threading.Event(), emit, simulation=True,max_cycles=6,output_dir=folder,controls=controls)
        self.assertTrue(any(k == 'heater_sample' for k,v in events))
        self.assertTrue(any(k == 'heater_done' and '联动保护' in v['error'] for k,v in events))
        self.assertFalse(events[-1][1]['error'])
