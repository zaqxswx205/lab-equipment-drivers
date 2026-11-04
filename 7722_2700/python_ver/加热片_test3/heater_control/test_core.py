import csv
import tempfile
import threading
import unittest
import time
from pathlib import Path
from unittest.mock import patch

import core
from drive_profile import DriveController, Profile, Step


class Tests(unittest.TestCase):
    def test_pt100_reference(self):
        for r, t in [(18.52008, -200), (60.25584, -100), (100, 0), (138.5055, 100), (390.481125, 850)]:
            self.assertAlmostEqual(core.temperature(r), t, places=3)
        for r in (0, float('nan'), float('inf'), 9.9e37, 500):
            with self.assertRaises(ValueError):
                core.temperature(r)

    def test_channel_occupancy(self):
        for module, paired in [(7700, 111), (7702, 121)]:
            cfg = core.Config(slot1_module=module, slot1_wires=4, reference_channel=101, channels=[core.Channel(101), core.Channel(paired)])
            with self.assertRaisesRegex(ValueError, '配对通道'):
                cfg.validate()
        core.Config(slot1_module=7700, slot1_wires=4, reference_channel=102, channels=[core.Channel(101), core.Channel(102)]).validate()
        with self.assertRaisesRegex(ValueError, '重复'):
            core.Config(reference_channel=101, channels=[core.Channel(101), core.Channel(101)]).validate()
        for cfg in (core.Config(channels=[]), core.Config(power_port='com1'), core.Config(power_slew_v_s=-1),
                    core.Config(max_voltage=301), core.Config(frequency=float('nan')), core.Config(mode='DC'),
                    core.Config(slot1_module=1234), core.Config(slot2_wires=3), core.Config(slot1_wires=4, channels=[core.Channel(101, .1), core.Channel(102)])):
            with self.assertRaises(ValueError):
                cfg.validate()

    def test_config_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)/'config.ini'
            for wires in (2, 4):
                cfg = core.Config(slot1_module=7700, slot1_wires=wires, slot2_module=7702, slot2_wires=2,
                                  channels=[core.Channel(101), core.Channel(102, .1 if wires == 2 else 0), core.Channel(201, .2)])
                core.save_config(p, cfg)
                self.assertEqual(cfg, core.load_config(p))

    def test_legacy_uniform_settings_migrate_and_mixed_rejected(self):
        import configparser
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)/'config.ini'
            parser = configparser.ConfigParser()
            parser['Settings'] = {'reference_channel': '102'}
            for channel in (101, 102):
                parser[f'Channel {channel}'] = {'module': '7700', 'wires': '4', 'lead_ohm': '0'}
            def write():
                with p.open('w', encoding='utf-8') as f:
                    parser.write(f)
            write()
            cfg = core.load_config(p)
            self.assertEqual((cfg.slot1_module, cfg.slot1_wires), (7700, 4))
            for key, value in [('module', '7702'), ('wires', '2')]:
                old = parser['Channel 102'][key]
                parser['Channel 102'][key] = value
                write()
                with self.assertRaisesRegex(ValueError, '不一致'):
                    core.load_config(p)
                parser['Channel 102'][key] = old
            parser['Settings']['module'] = '7702'
            write()
            with self.assertRaisesRegex(ValueError, '不一致'):
                core.load_config(p)

    def test_available_channels_follow_slot_wiring(self):
        for module, count in [(7700, 20), (7702, 40)]:
            for wires in (2, 4):
                last = count//2 if wires == 4 else count
                channels = core.available_channels(module, wires)
                self.assertEqual(len(channels), last*2)
                self.assertEqual((channels[0], channels[-1]), (101, 200+last))
                for channel in channels:
                    core.Config(slot1_module=module, slot1_wires=wires, slot2_module=module, slot2_wires=wires, reference_channel=channel,
                                channels=[core.Channel(channel)]).validate()

    def test_drive_voltage_and_power_limit(self):
        cfg = core.Config()
        controller = DriveController(cfg)
        prev = 0
        for now in range(20):
            value = controller.update(Step(0, 'power', 1), prev**2/50, 1, now)
            self.assertLessEqual(value, cfg.max_voltage)
            self.assertLessEqual(value-prev, cfg.power_slew_v_s+1e-9)
            prev = value
        self.assertEqual(controller.update(Step(0, 'voltage', 3), 0, 1, 21), 3)
        self.assertEqual(controller.update(Step(0, 'power', 0), 0, 1, 22), 0)

    def test_simulation_log(self):
        cfg = core.Config(interval=.05, channels=[core.Channel(101, .4), core.Channel(102)])
        with tempfile.TemporaryDirectory() as d:
            events = []
            core.run(cfg, threading.Event(), lambda *e: events.append(e), True, max_cycles=3, output_dir=d)
            self.assertEqual(events[-1], ('done', {'error': '', 'off_ok': True}))
            with next(Path(d).glob('*.csv')).open(encoding='utf-8-sig', newline='') as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(len(rows), 6)
            self.assertEqual({r['channel'] for r in rows}, {'101', '102'})
            samples = [value for kind, value in events if kind == 'sample']
            self.assertEqual(len(samples), 3)
            latest = samples[-1]
            self.assertEqual(set(latest['resistances']), {101, 102})
            self.assertEqual(set(latest['sample_elapsed_s']), {101, 102})
            for row in rows[-2:]:
                channel = int(row['channel'])
                raw = latest['resistances'][channel]
                self.assertEqual(raw, float(row['resistance_ohm']))
                self.assertAlmostEqual(core.temperature(raw-(.4 if channel == 101 else 0)),
                                       latest['temperatures'][channel], places=6)
                self.assertGreaterEqual(latest['sample_elapsed_s'][channel], 0)
                self.assertLessEqual(latest['sample_elapsed_s'][channel], latest['elapsed_min']*60)

    def assert_shutdown_on_failure(self, reader, before_output=False):
        instances = []
        original = core.SimTransport
        def build():
            tp = original()
            instances.append(tp)
            return tp
        with tempfile.TemporaryDirectory() as d, patch.object(core, 'SimTransport', build), patch.object(core.SimMeter, 'read', reader):
            events = []
            core.run(core.Config(), threading.Event(), lambda *e: events.append(e), True, max_cycles=2, output_dir=d,
                     profile=Profile('time', [Step(0, 'voltage', 3)]))
        if before_output:
            self.assertTrue(any(k == 'drive_done' and '温度越限' in v['error'] for k, v in events))
            self.assertEqual(sum(k == 'sample' for k, _ in events), 2)
        else:
            self.assertTrue(events[-1][1]['error'])
        self.assertFalse(instances[0].state['output'])
        if before_output:
            self.assertNotIn('OUTP:STAT ON', instances[0].commands)

    def test_overtemperature_never_enables(self):
        self.assert_shutdown_on_failure(lambda *args: (core.resistance(90), 90), True)

    def test_sensor_loss_turns_off(self):
        calls = []
        def read(*args):
            calls.append(1)
            if len(calls) > 2:
                raise TimeoutError('sensor disconnected')
            return 100, 0
        self.assert_shutdown_on_failure(read)

    def test_stop_during_startup(self):
        stop = threading.Event()
        def setup(_):
            stop.set()
        instances = []
        original = core.SimTransport
        def build():
            tp = original()
            instances.append(tp)
            return tp
        with tempfile.TemporaryDirectory() as d, patch.object(core, 'SimTransport', build), patch.object(core.SimMeter, 'setup', setup):
            core.run(core.Config(), stop, lambda *e: None, True, output_dir=d)
        self.assertNotIn('OUTP:STAT ON', instances[0].commands)
        self.assertFalse(instances[0].state['output'])

    def test_meter_protocol_and_wiring(self):
        class FakeSerial:
            def __init__(self, *a, **kw):
                self.buffer = bytearray()
                self.commands = []
            def write(self, data):
                self.assert_cr = data.endswith(b'\r') and not data.endswith(b'\r\n')
                cmd = data.decode().strip()
                self.commands.append(cmd)
                reply = {'*IDN?': 'KEITHLEY INSTRUMENTS INC.,MODEL 2700,123,1',
                         '*OPT?': '7700,7702', 'SYST:FRSW?': '0', 'SYST:ERR?': '0,"No error"',
                         '*OPC?': '1', 'READ?': '138.5055,101'}.get(cmd)
                if reply:
                    self.buffer.extend((reply+'\r\n').encode())
                return len(data)
            def read(self, n):
                b = self.buffer[:n]
                del self.buffer[:n]
                return bytes(b)
            def flush(self):
                pass
            def close(self):
                self.closed = True
        for wires in (2, 4):
            cfg = core.Config(slot1_module=7700, slot1_wires=wires, settle=0, reference_channel=101, channels=[core.Channel(101)])
            with self.subTest(wires=wires), patch('serial.Serial', FakeSerial):
                meter = core.Meter(cfg, lambda _: None)
                meter.setup()
                r, t = meter.read(cfg.channels[0], threading.Event())
                self.assertAlmostEqual(t, 100, places=5)
                self.assertIn("SENS:FUNC 'FRES'" if wires == 4 else "SENS:FUNC 'RES'", meter.ser.commands)
                self.assertIn('FORM:ELEM READ,CHAN', meter.ser.commands)
                self.assertTrue(meter.ser.assert_cr)
                meter.close()
                self.assertTrue(meter.ser.closed)
        cfg = core.Config(slot2_module=7700, reference_channel=201, channels=[core.Channel(201)])
        with patch('serial.Serial', FakeSerial):
            meter = core.Meter(cfg, lambda _: None)
            with self.assertRaisesRegex(RuntimeError, '槽位 2 配置为 7700，设备实际识别为 7702'):
                meter.setup()
            self.assertNotIn('*RST', meter.ser.commands)
            meter.close()

    def test_slow_scan_continues_with_current_round_temperature(self):
        def read(meter, c, stop):
            if c.channel != meter.cfg.reference_channel:
                time.sleep(.08)
            return 100, 0
        events = []
        cfg = core.Config(interval=.05)
        with tempfile.TemporaryDirectory() as d, patch.object(core.SimMeter, 'read', read):
            core.run(cfg, threading.Event(), lambda *e: events.append(e), True, max_cycles=1, output_dir=d)
        self.assertFalse(events[-1][1]['error'])
        self.assertTrue(any(k == 'sample' for k, _ in events))

    def test_meter_timeout_includes_connection_and_byte_count(self):
        class NoResponse:
            def __init__(self, *args, **kwargs):
                pass
            def write(self, data):
                return len(data)
            def flush(self):
                pass
            def read(self, n):
                return b''
            def close(self):
                pass
        trace = []
        cfg = core.Config(meter_port='COM4', timeout=.01)
        with patch('serial.Serial', NoResponse):
            meter = core.Meter(cfg, trace.append)
            with self.assertRaisesRegex(TimeoutError, 'COM4, 9600 baud，收到0字节'):
                meter.query('*IDN?')
            meter.close()
        self.assertTrue(any('RX_HEX=' in line for line in trace))

    def test_shutdown_failure_is_reported(self):
        original = core.ITech7722.output_off
        failures = 0
        def off(power):
            nonlocal failures
            failures += 1
            if failures <= 11:
                raise RuntimeError('write failed')
            original(power)
        events = []
        with tempfile.TemporaryDirectory() as d, patch.object(core.ITech7722, 'output_off', off):
            core.run(core.Config(), threading.Event(), lambda *e: events.append(e), True, output_dir=d)
        self.assertTrue(events[-1][1]['off_ok'])
        self.assertTrue(any(k == 'shutdown_pending' and '手动关闭' in v for k, v in events))
        self.assertTrue(any(k == 'shutdown_confirmed' for k, _ in events))


if __name__ == '__main__':
    unittest.main()
