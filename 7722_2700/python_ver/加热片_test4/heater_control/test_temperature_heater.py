from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import core
from drive_profile import Profile, Step
from runtime import RunControls


class TemperatureHeaterTests(unittest.TestCase):
    def run_trigger(self, action='on', linked=False, drive=False, stop_heater=False):
        cfg = core.Config(heater_temperature_enabled=True, heater_trigger_action=action,
                          heater_trigger_temperature=0., heater_control='voltage', heater_voltage=1., interval=.05)
        controls = RunControls(acquisition=True, drive=drive, heater=action == 'off', heater_link=linked,
                               profile=Profile('time', [Step(0, 'voltage', 1)]))
        events, devices = [], []
        count = 0
        original = core.SimTransport
        def build():
            device = original()
            devices.append(device)
            return device
        def read(meter, channel, stop):
            nonlocal count
            if channel.channel == cfg.reference_channel:
                count += 1
            t = 5. if count < 2 else -1.
            return core.resistance(t), t
        def emit(kind, value):
            events.append((kind, value))
            if kind == 'heater_sample' and stop_heater:
                controls.heater.clear()
        with tempfile.TemporaryDirectory() as folder, patch.object(core,'SimTransport',build), patch.object(core.SimMeter,'read',read):
            core.run(cfg, threading.Event(), emit, simulation=True, max_cycles=6, output_dir=folder, controls=controls)
        return events, devices

    def test_temperature_trigger_on(self):
        events, devices = self.run_trigger()
        self.assertEqual(len(devices), 1)
        self.assertTrue(any(k == 'heater_sample' for k,v in events))
        self.assertFalse(events[-1][1]['error'])
        self.assertTrue(devices[0].closed and not devices[0].state['output'])

    def test_temperature_trigger_off(self):
        events, devices = self.run_trigger(action='off')
        self.assertEqual(len(devices), 1)
        index = next(i for i,(k,v) in enumerate(events) if k == 'heater_status' and 'DC输出已确认关闭' in v)
        self.assertTrue(any(k == 'heater_sample' for k,v in events[:index]))
        self.assertFalse(any(k == 'heater_sample' for k,v in events[index+1:]))
        self.assertTrue(any(k == 'sample' for k,v in events[index+1:]))
        self.assertTrue(devices[0].closed and not devices[0].state['output'])
        self.assertFalse(events[-1][1]['error'])

    def test_link_blocks_temperature_trigger_without_drive(self):
        events, devices = self.run_trigger(linked=True)
        self.assertEqual(devices, [])
        self.assertFalse(any(k == 'heater_sample' for k,v in events))

    def test_temperature_and_drive_conditions_together(self):
        events, devices = self.run_trigger(linked=True, drive=True)
        self.assertEqual(len(devices), 2)
        self.assertTrue(any(k == 'heater_sample' for k,v in events))
        self.assertFalse(events[-1][1]['error'])

    def test_manual_stop_does_not_retrigger(self):
        events, devices = self.run_trigger(stop_heater=True)
        self.assertEqual(len(devices), 1)
        self.assertEqual(sum(k == 'heater_sample' for k,v in events), 1)

    def test_kelvin_round_trip(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'config.ini'
            cfg = core.Config(heater_temperature_enabled=True, heater_trigger_temperature=-10., heater_trigger_action='off')
            core.save_config(path, cfg)
            self.assertIn('heater_trigger_temperature = 263.15', path.read_text(encoding='utf-8'))
            loaded = core.load_config(path)
            self.assertEqual(loaded.heater_trigger_temperature, -10.)
            self.assertTrue(loaded.heater_temperature_enabled)
            self.assertEqual(loaded.heater_trigger_action, 'off')

    def test_acquisition_stop_is_rejected_until_heater_shutdown(self):
        controls = RunControls(acquisition=True, heater=True, heater_link=False)
        events=[]
        samples=0
        def emit(kind,value):
            nonlocal samples
            events.append((kind,value))
            if kind == 'heater_sample':
                samples += 1
                if samples == 1:
                    controls.acquisition.clear()
                if samples == 2:
                    controls.heater.clear()
            if kind == 'heater_done':
                controls.acquisition.clear()
        with tempfile.TemporaryDirectory() as folder:
            core.run(core.Config(heater_enabled=True, heater_control='voltage', heater_voltage=1., interval=.05),
                     threading.Event(),emit,simulation=True,max_cycles=5,output_dir=folder,controls=controls)
        self.assertTrue(any(k == 'status' and '采集已锁定' in v for k,v in events))
        heater_done = next(i for i,(k,v) in enumerate(events) if k == 'heater_done')
        acquisition_done = next(i for i,(k,v) in enumerate(events) if k == 'acquisition_done')
        self.assertGreater(acquisition_done, heater_done)
        self.assertFalse(events[-1][1]['error'])

    def test_heater_automatically_starts_sampling_before_output(self):
        controls = RunControls(heater=True, heater_link=False)
        device, events = core.SimTransport(), []
        read_count = 0
        class CheckedMeter(core.SimMeter):
            def read(self, channel, stop):
                nonlocal read_count
                self_test.assertFalse(device.state['output'])
                read_count += 1
                return 100., 0.
        self_test = self
        with tempfile.TemporaryDirectory() as folder, patch.object(core,'SimTransport',return_value=device), patch.object(core,'SimMeter',CheckedMeter):
            core.run(core.Config(heater_enabled=True, heater_control='voltage', heater_voltage=1., interval=.05),
                     threading.Event(),lambda k,v: events.append((k,v)),simulation=True,max_cycles=1,
                     output_dir=folder,controls=controls)
        self.assertEqual(read_count, 2)
        self.assertTrue(any(k == 'acquisition_starting' for k,v in events))
        self.assertTrue(any(k == 'heater_sample' for k,v in events))
        self.assertIn('OUTP:STAT ON',device.commands)
        self.assertFalse(events[-1][1]['error'])

    def test_meter_initialization_failure_never_enables_heater(self):
        device, events = core.SimTransport(), []
        with tempfile.TemporaryDirectory() as folder, patch.object(core,'SimTransport',return_value=device), \
                patch.object(core.SimMeter,'setup',side_effect=RuntimeError('test meter disconnected')):
            core.run(core.Config(heater_enabled=True, interval=.05), threading.Event(),
                     lambda k,v: events.append((k,v)),simulation=True,max_cycles=1,output_dir=folder,
                     controls=RunControls(heater=True,heater_link=False))
        self.assertNotIn('OUTP:STAT ON',device.commands)
        self.assertTrue(device.closed)
        self.assertIn('test meter disconnected', events[-1][1]['error'])
