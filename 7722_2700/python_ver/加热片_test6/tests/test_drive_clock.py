import tempfile
import threading
import unittest
from unittest.mock import patch

import core
from session.timing import DriveClock, duration
from runtime import RunControls
from drive_profile import Profile,Step


class DriveClockTests(unittest.TestCase):
    def test_live_seconds_pause_and_stop(self):
        clock=DriveClock()
        self.assertEqual(clock.text(100),'')
        clock.update(dict(observed_at=100,running=True,elapsed_s=0,scheme_s=0,paused=False))
        self.assertIn('方案运行时间 00:00:10；实际经过 00:00:10',clock.text(110))
        clock.update(dict(observed_at=110,running=True,elapsed_s=10,scheme_s=10,paused=True))
        self.assertIn('方案运行时间 00:00:10；实际经过 00:00:30',clock.text(130))
        clock.update(dict(observed_at=130,running=False,elapsed_s=30,scheme_s=10,paused=True))
        self.assertIn('实际经过 00:00:30（已停止）',clock.text(500))

    def test_new_start_resets_clock_and_long_runs_do_not_wrap(self):
        clock=DriveClock()
        clock.update(dict(observed_at=1,running=False,elapsed_s=1000,scheme_s=900,paused=False))
        clock.update(dict(observed_at=100,running=True,elapsed_s=0,scheme_s=0,paused=False))
        self.assertIn('方案运行时间 00:00:00',clock.text(100))
        self.assertEqual(duration(90061),'25:01:01')

    def test_start_event_follows_initialization_and_precedes_samples(self):
        events=[]
        original=core.SimMeter.setup
        def setup(meter):
            events.append(('meter_initialized',None))
            original(meter)
        cfg=core.Config(interval=.05,settle=0)
        controls=RunControls(acquisition=True,drive=True,profile=Profile('time',[Step(10,'voltage',1)]))
        with tempfile.TemporaryDirectory() as folder, patch.object(core.SimMeter,'setup',setup):
            core.run(cfg,threading.Event(),lambda *event:events.append(event),simulation=True,
                     max_cycles=1,output_dir=folder,controls=controls)
        kinds=[kind for kind,value in events]
        self.assertLess(kinds.index('meter_initialized'),kinds.index('drive_clock'))
        self.assertLess(kinds.index('drive_clock'),kinds.index('sample'))
        clocks=[value for kind,value in events if kind=='drive_clock']
        self.assertLess(clocks[0]['elapsed_s'],.1)
        self.assertFalse(clocks[-1]['running'])
        self.assertFalse(events[-1][1]['error'])
