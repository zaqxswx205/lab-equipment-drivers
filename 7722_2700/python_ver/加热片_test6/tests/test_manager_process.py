"""Exercise the real manager/three-device IPC topology with simulated devices."""
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest

from core import Config, Channel
from performance import PerformanceRun, Point
from profile.model import Profile, Step
from runtime import RunControls, run
from session.manager import ControlFlag


class ManagerTests(unittest.TestCase):
    def cfg(self):
        return Config(interval=.05, settle=0, channels=[Channel(101)], reference_channel=101,
                      heater_enabled=True, heater_control='voltage', heater_voltage=1)

    def test_stale_state_cannot_undo_stop_button(self):
        flag = ControlFlag(threading.Event())
        flag.set()
        _, generation = flag.snapshot()
        flag.clear()
        self.assertFalse(flag.apply_remote(True, generation))
        self.assertFalse(flag.is_set())

    def test_manager_and_three_devices_have_separate_pids_and_ordered_stop(self):
        controls = RunControls(True, True, Profile('time',[Step(0,'voltage',1)]),
                               heater=True, heater_link=False)
        controls.device_processes = True
        events = []
        with tempfile.TemporaryDirectory() as directory:
            run(self.cfg(), threading.Event(), lambda *e:events.append(e), True, 15,
                directory, controls=controls)
            trace = next(Path(directory).glob('*communication.txt')).read_text(encoding='utf-8')
        manager = next(v['pid'] for k,v in events if k=='manager_started')
        snapshots = [v for k,v in events if k=='manager_state' and len(v.get('device_pids',{}))==3]
        self.assertTrue(snapshots)
        pids = set(snapshots[0]['device_pids'].values())
        self.assertEqual(len(pids|{manager,os.getpid()}), 5)
        self.assertLess(trace.index('heater OFF_CONFIRMED'), trace.index('drive OFF_CONFIRMED'))
        self.assertEqual(events[-1], ('done', {'error':'','off_ok':True}))

    def test_commands_start_stop_and_restart_drive_while_meter_continues(self):
        controls = RunControls(acquisition=True)
        controls.device_processes = True
        stop = threading.Event()
        events = []
        phase = [0]
        def emit(kind, value):
            events.append((kind,value))
            if kind=='sample' and phase[0]==0:
                phase[0]=1
                controls.start_drive(Profile('time',[Step(0,'voltage',1)]))
            elif kind=='sample' and phase[0]==1 and value['output_on']:
                phase[0]=2
                controls.drive.clear()
            elif kind=='drive_done' and phase[0]==2:
                phase[0]=3
                controls.start_drive(Profile('time',[Step(0,'voltage',2)]))
            elif kind=='sample' and phase[0]==3 and value['setting']==2:
                phase[0]=4
                stop.set()
        watchdog = threading.Timer(10, stop.set)
        watchdog.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                run(self.cfg(), stop, emit, True, output_dir=directory, controls=controls)
        finally:
            watchdog.cancel()
        self.assertEqual(phase[0],4)
        self.assertTrue(any(k=='sample' and v['temperatures'] for k,v in events))
        self.assertTrue(events[-1][1]['off_ok'])

    def test_ui_heartbeat_loss_requests_safe_stop_inside_manager(self):
        cfg = self.cfg()
        cfg.heartbeat_timeout = 1
        controls = RunControls(True,True,Profile('time',[Step(0,'voltage',1)]),
                               heater=True,heater_link=False)
        controls.device_processes = True
        controls.ui_heartbeat = time.monotonic()
        events = []
        with tempfile.TemporaryDirectory() as directory:
            run(cfg, threading.Event(), lambda *e:events.append(e), True,
                output_dir=directory, controls=controls)
        self.assertTrue(any(k=='alarm' and '界面心跳失联' in v for k,v in events))
        self.assertTrue(events[-1][1]['off_ok'])

    def test_manual_performance_advance_crosses_manager_boundary(self):
        cfg = self.cfg()
        cfg.heater_control='power'
        cfg.heater_power=.02
        cfg.power_slew_v_s=100
        profile = Profile('time',[Step(0,'voltage',1,performance_points=(1,)),Step(.1,'voltage',0)])
        controls = RunControls(True,True,profile)
        controls.device_processes=True
        controls.performance=PerformanceRun((Point(300,.02,'manual'),))
        stop=threading.Event()
        advanced=[]
        def emit(kind,value):
            if controls.performance.ready.is_set() and not advanced:
                advanced.append(True)
                controls.performance.advance()
            if controls.performance.phase=='complete':
                stop.set()
        watchdog=threading.Timer(10,stop.set)
        watchdog.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                run(cfg,stop,emit,True,output_dir=directory,controls=controls)
        finally:
            watchdog.cancel()
        self.assertTrue(advanced)
        self.assertEqual(controls.performance.phase,'complete')
