import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import core
from drive_profile import load_profile
from performance import PerformanceRun
from plan_io import import_legacy_rows, points_from_drive
from runtime import RunControls
from ui.profile_editor import rows_from_text,text_from_rows


class InlineStageTests(unittest.TestCase):
    def source(self):
        return ('[Drive]\nstrategy=mixed\ntime_unit=s\ntemperature_unit=K\n'
                '[Step 1]\ntrigger=temperature\nat=300\nmode=voltage\nvalue=1\n'
                'heater_w=.02\nstability_mode=duration\nduration_s=.05\n'
                '[Step 2]\ntrigger=temperature\nat=300\nmode=voltage\nvalue=2\n'
                'heater_w=.03\nstability_mode=peak_to_peak\nwindow_s=.05\npeak_to_peak_k=.1\n'
                '[Step 3]\ntrigger=time\nat=0\nmode=voltage\nvalue=0\n')

    def test_one_file_controls_sequential_tests_at_same_temperature(self):
        cfg=core.Config(channels=[core.Channel(101)],reference_channel=101,interval=.05,settle=0,
                        heater_enabled=True,heater_control='power',heater_power=.02,power_slew_v_s=100)
        text=text_from_rows(rows_from_text(self.source()),self.source())
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'drive.ini'
            path.write_text(text,encoding='utf-8')
            profile=load_profile(path,cfg)
            points=points_from_drive(text,cfg)
            controls=RunControls(acquisition=True,drive=True,profile=profile)
            controls.performance=PerformanceRun(points)
            events=[]
            with patch.object(core.SimMeter,'read',return_value=(100,0)):
                core.run(cfg,threading.Event(),lambda *event:events.append(event),simulation=True,
                         max_cycles=20,controls=controls,output_dir=folder)
        self.assertFalse(events[-1][1]['error'])
        self.assertEqual(controls.performance.phase,'complete')
        self.assertEqual(list(dict.fromkeys(v['drive_target'] for k,v in events if k=='sample')),[1,2,0])

    def test_legacy_group_expands_into_rows_without_losing_loads(self):
        text='[Drive]\nstrategy=temperature\ntemperature_unit=K\n[Step 1]\nat=300\nmode=voltage\nvalue=1\nperformance_points=1,2\n'
        legacy='[Point 1]\ntemperature_k=300\nheater_w=.02\n[Point 2]\ntemperature_k=300\nheater_w=.03\n'
        rows,migrated=import_legacy_rows(text,legacy)
        self.assertTrue(migrated)
        self.assertEqual([r['heater_w'] for r in rows],['.02','.03'])
        converted=text_from_rows(rows,text)
        self.assertEqual([p.heater_w for p in points_from_drive(converted,core.Config())],[.02,.03])

    def test_time_trigger_keeps_test_temperature(self):
        text='[Drive]\nstrategy=time\ntemperature_unit=K\n[Step 1]\nat=0\nmode=voltage\nvalue=1\nheater_w=.02\ntest_temperature_k=300\n'
        rows=rows_from_text(text)
        self.assertEqual(rows[0]['temperature'],'300')
        self.assertEqual(points_from_drive(text_from_rows(rows,text),core.Config())[0].temperature_k,300)

    def test_missing_legacy_parameters_are_not_silently_dropped(self):
        text='[Drive]\nstrategy=time\n[Step 1]\nat=0\nmode=voltage\nvalue=1\nperformance_points=1\n'
        with self.assertRaises(ValueError):
            import_legacy_rows(text,'')
