import csv
import tempfile
import threading
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import core
from performance import PerformanceRun, Point, parse_plan
from profile.model import Profile, Step, load_profile
from profile.sequencer import Sequencer
from runtime import RunControls


class GateTests(unittest.TestCase):
    def test_gate_cannot_be_skipped_even_when_later_triggers_are_met(self):
        steps = [Step(0,'voltage',1,performance_points=(1,2)),
                 Step(1,'voltage',2,performance_points=(3,)), Step(2,'voltage',0)]
        seq = Sequencer(Profile('time',steps))
        self.assertEqual(seq.select(100,0), steps[0])
        self.assertEqual(seq.select(200,0), steps[0])
        seq.release_performance()
        self.assertEqual(seq.select(200,0), steps[1])
        seq.release_performance()
        self.assertEqual(seq.select(200,0), steps[2])

    def test_pause_uses_arrival_stage_and_preserves_elapsed_on_resume(self):
        cfg = core.Config(channels=[core.Channel(101)], reference_channel=101)
        steps = [Step(0,'voltage',1,performance_points=(1,)),
                 Step(1,'voltage',2,performance_points=(2,))]
        p = PerformanceRun((Point(300,.02),Point(290,.02)))
        controls = RunControls(acquisition=True,drive=True)
        controls.drive_ready.set()
        s = SimpleNamespace(cfg=cfg, controls=controls, drive_epoch=0., last=0.,
                            sequencer=Sequencer(Profile('time',steps)),
                            performance_step=None, performance_heater_w=None,
                            emit=Mock(), logger=Mock(), devices=Mock(), active_heater=True,
                            heater_enabled=False, stop=threading.Event())
        samples = [(cfg.channels[0],100,20,datetime.now(),2)]
        with patch('performance.time.monotonic',return_value=2.):
            p.update(s,samples,{})
        self.assertEqual(s.performance_step,steps[0])
        self.assertEqual(p.paused_at,2.)
        s.heater_enabled = True
        p.update(s,samples,{})
        p.advance()
        with patch('performance.time.monotonic',return_value=20.):
            p.update(s,samples,{})
        self.assertEqual(s.drive_epoch,18.)
        self.assertIsNone(p.paused_at)
        self.assertIsNone(s.performance_step)
        self.assertEqual(s.sequencer.select(20-s.drive_epoch,20),steps[1])
        s.devices.close_heater.assert_called_once()

    def test_profile_roundtrip_keeps_bindings(self):
        text = '[Drive]\nstrategy=time\n[Step 1]\nat=0\nmode=voltage\nvalue=1\nperformance_points=1,2\n'
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'profile.ini'
            path.write_text(text,encoding='utf-8')
            self.assertEqual(load_profile(path,core.Config()).steps[0].performance_points,(1,2))

    def test_bindings_must_match_plan_and_no_device_open_on_failure(self):
        cfg = core.Config()
        p = PerformanceRun((Point(300,.02),))
        with self.assertRaises(ValueError):
            p.validate_profile(Profile())
        controls = RunControls(acquisition=True,drive=True,
                               profile=Profile('time',[Step(0,'voltage',1,performance_points=(1,))]))
        events=[]
        with tempfile.TemporaryDirectory() as folder, patch.object(core,'SerialTransport') as serial:
            core.run(cfg,threading.Event(),lambda *event:events.append(event),
                     output_dir=folder,controls=controls)
            serial.assert_not_called()
        self.assertTrue(events[-1][1]['error'])

    def test_automatic_points_release_steps_and_driver_continues_to_off(self):
        cfg = core.Config(channels=[core.Channel(101)],reference_channel=101,
                          interval=.05,settle=0,heater_enabled=True,
                          heater_control='power',heater_power=.02)
        steps = [Step(0,'voltage',1,performance_points=(1,)),
                 Step(.01,'voltage',2,performance_points=(2,)),Step(.02,'voltage',0)]
        controls = RunControls(acquisition=True,drive=True,profile=Profile('time',steps))
        p = controls.performance = PerformanceRun((Point(300,.02,'duration',.05),
                                                  Point(300,.02,'peak_to_peak',300,.05,.1)))
        events=[]
        with tempfile.TemporaryDirectory() as folder, patch.object(core.SimMeter,'read',return_value=(100,0)):
            core.run(cfg,threading.Event(),lambda *event: events.append(event),simulation=True,
                     max_cycles=20,output_dir=folder,controls=controls)
            with next(Path(folder).glob('*_performance_summary.csv')).open(encoding='utf-8-sig') as stream:
                summary=list(csv.DictReader(stream))
        self.assertFalse(events[-1][1]['error'])
        self.assertEqual(p.phase,'complete')
        targets=[v['drive_target'] for k,v in events if k=='sample']
        self.assertEqual(list(dict.fromkeys(targets)),[1,2,0])
        self.assertEqual([r['outcome'] for r in summary],['duration_complete','peak_to_peak_complete'])
        self.assertEqual([r['drive_target'] for r in summary],['1','2'])
