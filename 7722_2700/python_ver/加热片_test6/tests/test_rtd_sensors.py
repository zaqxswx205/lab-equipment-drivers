from tests.csv_helpers import legacy_rows
"""Pt100/Pt1000 conversion, mixed channel acquisition, config and UI regression."""
import csv
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import core
from runtime import RunControls
from model.pt100 import resistance, temperature
from tests.test_slot_measurement import Port


class ConversionTests(unittest.TestCase):
    def test_reference_resistances_and_round_trip_for_both_types(self):
        # Independent points from Lake Shore 331 Table D-4.
        reference=[(-243.15,3.820),(-237.15,5.146),(-168.15,32.081),
                   (41.85,116.270),(526.85,289.830)]
        for sensor,scale in (('pt100',1),('pt1000',10)):
            for t,r in reference:
                with self.subTest(sensor=sensor,t=t):
                    self.assertAlmostEqual(resistance(t,sensor,'lakeshore'),r*scale,places=5)
                    self.assertAlmostEqual(temperature(r*scale,sensor,'lakeshore'),t,places=6)
        self.assertAlmostEqual(temperature(1162.70,'Pt1000','lakeshore'),41.85,places=6)
        with self.assertRaises(ValueError):temperature(1000)  # Defaults to Pt100.

    def test_invalid_readings_and_sensor_types_are_rejected(self):
        for sensor in ('pt100','pt1000'):
            for r in (0,-1,float('nan'),float('inf'),9.9e37,resistance(-238,sensor)-.01,resistance(76,sensor)+.01):
                with self.subTest(sensor=sensor,r=r),self.assertRaises(ValueError):temperature(r,sensor)
        with self.assertRaises(ValueError):resistance(0,'pt500')
        with self.assertRaisesRegex(ValueError,'传感器类型'):
            core.Config(channels=[core.Channel(102,sensor_type='pt500')]).validate()

    def test_legacy_config_defaults_and_mixed_config_round_trip(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'config.ini'
            path.write_text('[Settings]\nreference_channel=101\n[Channel 101]\nlead_ohm=0\n',encoding='utf-8')
            self.assertEqual(core.load_config(path).channels[0].sensor_type,'pt100')
            cfg=core.Config(channels=[core.Channel(101,.2),core.Channel(102,.4,sensor_type='pt1000')])
            core.save_config(path,cfg)
            self.assertEqual(core.load_config(path),cfg)
            self.assertIn('sensor_type = pt1000',path.read_text(encoding='utf-8'))
            path.write_text(path.read_text(encoding='utf-8').replace('sensor_type = pt1000','sensor_type = Pt1000'),encoding='utf-8')
            self.assertEqual(core.load_config(path),cfg)


class MeterTests(unittest.TestCase):
    def test_real_meter_adapter_mixes_types_and_subtracts_lead_before_conversion(self):
        class SensorPort(Port):
            def write(self,data):
                result=super().write(data)
                if data.decode().strip()=='READ?':
                    value={101:5.346,102:51.86,201:320.81}[self.channel]
                    self.reply=bytearray(f'{value},{self.channel}\r\n'.encode())
                return result
        cfg=core.Config(settle=0,slot2_module=7700,slot2_wires=4,
            channels=[core.Channel(101,.2,sensor_curve='lakeshore'),core.Channel(102,.4,sensor_type='pt1000',sensor_curve='lakeshore'),
                      core.Channel(201,max_temperature=150,sensor_type='pt1000',sensor_curve='lakeshore')])
        cfg.validate()
        port=SensorPort('7702,7700')
        with patch('serial.Serial',return_value=port):
            meter=core.Meter(cfg,lambda t:None)
            try:
                meter.setup()
                for channel,expected_r,expected_t in zip(cfg.channels,(5.346,51.86,320.81),(-237.15,-237.15,-168.15)):
                    r,t=meter.read(channel,threading.Event())
                    self.assertEqual(r,expected_r)
                    self.assertAlmostEqual(t,expected_t,places=6)
                self.assertEqual(port.readings,[(101,'RES'),(102,'RES'),(201,'FRES')])
            finally:meter.close()

    def test_simulation_and_device_process_preserve_each_channels_sensor(self):
        cfg=core.Config(interval=.05,settle=0,channels=[core.Channel(101,.2),core.Channel(102,.4,sensor_type='pt1000')])
        for processes in (False,True):
            with self.subTest(processes=processes),tempfile.TemporaryDirectory() as folder:
                controls=RunControls(acquisition=True);controls.device_processes=processes
                events=[]
                core.run(cfg,threading.Event(),lambda *e:events.append(e),simulation=True,
                         max_cycles=2,output_dir=folder,controls=controls)
                self.assertFalse(events[-1][1]['error'])
                with next(Path(folder).glob('*.csv')).open(encoding='utf-8-sig',newline='') as f:
                    rows=list(legacy_rows(f))
                self.assertEqual({int(row['channel']) for row in rows},{101,102})
                for row in rows:
                    ch=next(c for c in cfg.channels if c.channel==int(row['channel']))
                    expected=resistance(float(row['temperature_K'])-273.15,ch.sensor_type)+ch.lead_ohm
                    self.assertAlmostEqual(float(row['resistance_ohm']),expected,places=5)


class SensorUiTests(unittest.TestCase):
    def test_channel_editor_updates_collects_and_restores_sensor_type(self):
        from ui.app import App
        app=App();app.withdraw()
        try:
            self.assertEqual(tuple(app.table['columns'])[:3],('channel','name','sensor_type'))
            app.channel_vars['channel'].set('101')
            app.channel_vars['sensor_type'].set('Pt1000')
            app.channel_vars['min_temperature'].set('223.15')
            app.channel_vars['max_temperature'].set('349.15')
            app.add_channel()
            self.assertEqual(app.table.set('101','sensor_type'),'Pt1000')
            self.assertEqual(app.table.set('102','sensor_type'),'Pt100')
            cfg=app.collect()
            self.assertEqual(next(c for c in cfg.channels if c.channel==101).sensor_type,'pt1000')
            self.assertEqual(next(c for c in cfg.channels if c.channel==102).sensor_type,'pt100')
            app.channel_vars['sensor_type'].set('Pt100')
            app.table.selection_set('101');app.select_channel()
            self.assertEqual(app.channel_vars['sensor_type'].get(),'Pt1000')
        finally:app.destroy()


if __name__=='__main__':unittest.main()
