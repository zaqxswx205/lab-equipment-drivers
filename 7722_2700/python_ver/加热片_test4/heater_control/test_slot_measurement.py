"""混合模块、混合电阻功能、配置迁移和逐槽校验的离线回归。"""
import configparser
import csv
from pathlib import Path
import re
import tempfile
import threading
import unittest
from unittest.mock import patch

from core import Channel, Config, Meter, load_config, run, save_config
from drive_profile import Profile


class Port:
    def __init__(self, models):
        self.models = models
        self.commands = []
        self.readings = []
        self.reply = bytearray()
        self.closed = False
        self.channel = None
        self.function = None

    def write(self, data):
        cmd = data.decode('ascii').strip()
        self.commands.append(cmd)
        reply = {'*IDN?': 'KEITHLEY,MODEL 2700,SN,1', '*OPT?': self.models,
                 'SYST:ERR?': '0,"No error"', '*OPC?': '1', 'SYST:FRSW?': '0'}.get(cmd)
        if cmd == 'ROUT:OPEN:ALL':
            self.channel = None
        if cmd.startswith('SENS:FUNC '):
            if self.channel is not None:
                raise AssertionError('切换电阻功能前必须打开所有通道')
            self.function = cmd.split("'")[1]
        if cmd.startswith('ROUT:CLOS '):
            self.channel = int(re.search(r'@(\d+)', cmd)[1])
        if cmd == 'READ?':
            if self.channel is None:
                raise AssertionError('未接通测量通道')
            self.readings.append((self.channel, self.function))
            reply = f'100,{self.channel}'
        if reply:
            self.reply.extend((reply+'\r\n').encode('ascii'))
        return len(data)

    def read(self, n):
        data = bytes(self.reply[:n])
        del self.reply[:n]
        return data

    def flush(self):
        pass

    def close(self):
        self.closed = True


class SlotTests(unittest.TestCase):
    def test_invalid_meter_resistance_preserves_raw_and_checks_errors(self):
        class InvalidPort(Port):
            def write(self, data):
                result = super().write(data)
                if data.decode().strip() == 'READ?':
                    self.reply = bytearray((self.raw+',101\r\n').encode())
                return result
        cfg = Config(settle=0)
        for raw in ('9.9E37', '0.55', 'NaN', 'bad'):
            with self.subTest(raw=raw):
                port = InvalidPort('7702,NONE')
                port.raw = raw
                with patch('serial.Serial', return_value=port):
                    meter = Meter(cfg, lambda _: None)
                    r, t = meter.read(Channel(101), threading.Event())
                    self.assertIsNone(t)
                    self.assertEqual(port.commands[-1], 'SYST:ERR?')
                    self.assertEqual(str(r).lower(), str(float(raw)).lower() if raw != 'bad' else raw)
                    meter.close()

    def test_mixed_slots_switch_resistance_functions(self):
        for modules in ((7700, 7702), (7702, 7700)):
            for wiring in ((4, 2), (2, 4)):
                with self.subTest(modules=modules, wiring=wiring):
                    cfg = Config(slot1_module=modules[0], slot2_module=modules[1],
                                 slot1_wires=wiring[0], slot2_wires=wiring[1], settle=0,
                                 reference_channel=101, channels=[Channel(101), Channel(201)])
                    cfg.validate()
                    port = Port(f'{modules[0]},{modules[1]}')
                    with patch('serial.Serial', return_value=port):
                        meter = Meter(cfg, lambda _: None)
                        meter.setup()
                        for c in cfg.channels+cfg.channels[::-1]:
                            self.assertAlmostEqual(meter.read(c, threading.Event())[1], 0., places=5)
                        meter.close()
                    expected = [(c.channel, 'FRES' if cfg.measurement_for(c.channel)[1] == 4 else 'RES')
                                for c in cfg.channels+cfg.channels[::-1]]
                    self.assertEqual(port.readings, expected)
                    self.assertTrue(port.closed)

    def test_per_slot_limits_pairs_and_compensation(self):
        cfg = Config(slot1_module=7700, slot1_wires=4, slot2_module=7702, slot2_wires=2,
                     reference_channel=101, channels=[Channel(101), Channel(240, .2)])
        cfg.validate()
        self.assertEqual(cfg.measurement_for(101), (7700, 4))
        self.assertEqual(cfg.measurement_for(240), (7702, 2))
        self.assertEqual(len(cfg.available_channels()), 50)
        for channel in (111, 120, 241, 301):
            with self.subTest(channel=channel), self.assertRaises(ValueError):
                Config(slot1_module=7700, slot1_wires=4, slot2_module=7702, slot2_wires=2,
                       reference_channel=101, channels=[Channel(101), Channel(channel)]).validate()
        with self.assertRaisesRegex(ValueError, '四线补偿'):
            Config(slot1_module=7700, slot1_wires=4, reference_channel=101,
                   channels=[Channel(101, .2)]).validate()

    def test_wrong_module_rejected_only_for_used_slot(self):
        cfg = Config(slot1_module=7700, slot2_module=7702, reference_channel=201, channels=[Channel(201)])
        for models, valid in [('NONE,7702', True), ('7700,7700', False)]:
            with self.subTest(models=models), patch('serial.Serial', return_value=Port(models)):
                meter = Meter(cfg, lambda _: None)
                if valid:
                    meter.setup()
                else:
                    with self.assertRaisesRegex(RuntimeError, '槽位 2'):
                        meter.setup()
                    self.assertNotIn('*RST', meter.ser.commands)
                meter.close()

    def test_legacy_global_and_per_channel_migration(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/'config.ini'
            p = configparser.ConfigParser()
            p['Settings'] = {'reference_channel': '101', 'module': '7700', 'wires': '4'}
            p['Channel 101'] = {'lead_ohm': '0'}
            p['Channel 201'] = {'lead_ohm': '0'}
            def write():
                with path.open('w', encoding='utf-8') as f:
                    p.write(f)
            write()
            cfg = load_config(path)
            self.assertEqual(cfg.measurement_for(101), (7700, 4))
            self.assertEqual(cfg.measurement_for(201), (7700, 4))
            del p['Settings']['module']
            del p['Settings']['wires']
            p['Channel 101'].update(module='7700', wires='4')
            p['Channel 201'].update(module='7702', wires='2', lead_ohm='.2')
            write()
            cfg = load_config(path)
            self.assertEqual(cfg.measurement_for(101), (7700, 4))
            self.assertEqual(cfg.measurement_for(201), (7702, 2))
            save_config(path, cfg)
            self.assertEqual(cfg, load_config(path))
            self.assertNotIn('\nmodule =', path.read_text(encoding='utf-8'))

    def test_csv_uses_each_slots_wiring(self):
        cfg = Config(slot1_wires=4, slot2_wires=2, interval=.05,
                     reference_channel=201, channels=[Channel(101), Channel(201, .2)])
        events = []
        with tempfile.TemporaryDirectory() as d:
            run(cfg, threading.Event(), lambda *e: events.append(e), simulation=True,
                max_cycles=1, output_dir=d, profile=Profile())
            self.assertFalse(events[-1][1]['error'])
            with next(Path(d).glob('*.csv')).open(encoding='utf-8-sig', newline='') as f:
                rows = list(csv.DictReader(f))
            self.assertEqual({row['channel']: row['wires'] for row in rows}, {'101': '4', '201': '2'})


if __name__ == '__main__':
    unittest.main()
