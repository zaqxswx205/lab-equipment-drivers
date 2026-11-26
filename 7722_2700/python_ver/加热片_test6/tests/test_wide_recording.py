"""Inspect physical wide CSV rows, including both independent power supplies."""
import csv
from datetime import datetime
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest

from core import Config, Channel
from csv_recording import CsvRecording
from profile.model import Profile, Step
from profile.sequencer import Sequencer
from runtime import RunControls, run
from session.publishing import publish


class WideRecordingTests(unittest.TestCase):
    def read(self,path):
        with path.open(encoding='utf-8-sig',newline='') as stream:
            return list(csv.DictReader(stream))

    def test_one_row_contains_all_temperatures_then_both_power_supplies_and_stage(self):
        cfg=Config(channels=[Channel(102),Channel(101)])
        when=datetime(2026,11,26,14,50,0)
        samples=[(cfg.channels[0],101,24.,when,0),(cfg.channels[1],100,0.,when,0)]
        drive=dict(voltage=2.,current=.3,power=.6,power_factor=.95)
        heater=dict(voltage=5.,current=-.1,power=.5)
        with tempfile.TemporaryDirectory() as directory:
            recorder=CsvRecording(directory,channels=cfg.channels)
            recorder.write_snapshot(when,samples,drive,heater,3,'voltage',2,2,True,True,cfg)
            recorder.close()
            rows=self.read(next(Path(directory).glob('*.csv')))
        self.assertEqual(len(rows),1)
        row=rows[0]
        self.assertEqual(list(row)[:11],['时间','通道101名称','通道101温度(K)','通道102名称','通道102温度(K)',
            '交流电压(V)','交流电流(A)','交流功率(W)','加热电压(V)','加热电流(A)','加热功率(W)'])
        self.assertEqual(list(row)[-1],'驱动方案阶段')
        self.assertEqual(float(row['通道101温度(K)']),273.15)
        self.assertEqual(float(row['通道102温度(K)']),297.15)
        self.assertEqual(float(row['交流功率(W)']),.6)
        self.assertEqual(float(row['加热电流(A)']),-.1)
        self.assertEqual(row['驱动方案阶段'],'3')
        self.assertNotIn(None,row)

    def test_invalid_temperature_and_missing_devices_are_blank_and_rollover_keeps_headers(self):
        cfg=Config(channels=[Channel(101)],reference_channel=101)
        when=datetime(2026,11,26,23,59,59)
        with tempfile.TemporaryDirectory() as directory:
            recorder=CsvRecording(directory,channels=cfg.channels)
            for stamp in (when,datetime(2026,11,27,0,0,0)):
                recorder.write_snapshot(stamp,[(cfg.channels[0],9.9e37,None,stamp,0)],cfg=cfg)
            recorder.close()
            files=list(Path(directory).glob('*.csv'))
            self.assertEqual(len(files),2)
            for path in files:
                row=self.read(path)[0]
                self.assertEqual(row['通道101温度(K)'],'')
                self.assertEqual(float(row['通道101电阻(Ω)']),9.9e37)
                for key in ('交流电压(V)','加热功率(W)','驱动方案阶段'):
                    self.assertEqual(row[key],'')

    def test_failed_heater_does_not_reuse_its_cached_readings(self):
        cfg=Config(channels=[Channel(101)],reference_channel=101)
        when=datetime.now()
        profile=Profile('time',[Step(0,'voltage',1)])
        controls=RunControls(True,True,profile)
        controls.drive_source='manual'
        with tempfile.TemporaryDirectory() as directory:
            recorder=CsvRecording(directory,channels=cfg.channels)
            owner=SimpleNamespace(cfg=cfg,controls=controls,clients={
                'drive':SimpleNamespace(failed=False,measurement=dict(voltage=1,current=.02,power=.02,power_factor=1),setting=1),
                'heater':SimpleNamespace(failed=True,measurement=dict(voltage=99,current=99,power=99))},
                current_step=profile.steps[0],sequencer=Sequencer(profile),drive_epoch=0,
                drive_profile=profile,enabled=True,heater_enabled=True,epoch=0,now=lambda:1,
                log_file=recorder,cycles=0,measurement_errors={},emit=lambda *v:None)
            owner.sequencer.index=0
            publish(owner,[(cfg.channels[0],100,0,when,1)])
            recorder.close()
            row=self.read(next(Path(directory).glob('*.csv')))[0]
        self.assertEqual(row['加热电压(V)'],'')
        self.assertEqual(row['驱动方案阶段'],'')
        self.assertEqual(float(row['交流电压(V)']),1)

    def test_manager_records_heater_values_and_distinguishes_manual_from_plan(self):
        cfg=Config(interval=.05,settle=0,channels=[Channel(101)],reference_channel=101,
                   heater_enabled=True,heater_control='voltage',heater_voltage=1)
        for source in ('manual','profile'):
            with self.subTest(source=source),tempfile.TemporaryDirectory() as directory:
                controls=RunControls(True,True,Profile('time',[Step(0,'voltage',1)]),heater=True,heater_link=False)
                controls.device_processes=True
                controls.drive_source=source
                events=[]
                run(cfg,threading.Event(),lambda *v:events.append(v),True,15,directory,controls=controls)
                self.assertTrue(events[-1][1]['off_ok'])
                rows=[row for path in Path(directory).glob('*.csv') for row in self.read(path)]
                active=[row for row in rows if row['加热输出状态']=='True']
                self.assertTrue(active)
                self.assertTrue(any(float(row['加热功率(W)'])>0 for row in active))
                if source=='manual':
                    self.assertTrue(all(row['驱动方案阶段']=='' for row in rows))
                else:
                    self.assertTrue(any(row['驱动方案阶段']=='1' for row in rows))
