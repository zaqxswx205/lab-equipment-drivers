"""Rollback timing begins at acknowledgement, with heater-OFF interlocking."""
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest

from core import Config, Channel
from profile.model import Profile, Step
from profile.sequencer import Sequencer
from runtime import RunControls, run
from performance import PerformanceRun, Point
from pathlib import Path
from session.rollback import Rollback
from session.shutdown import Shutdown
from session.supervisor import Supervisor


class Client:
    def __init__(self, off=False):
        self.ready=True
        self.failed=self.closing=False
        self.off_confirmed=off
        self.output_on=not off
        self.setting=2
        self.measurement={'power':.08}
        self.pending={}
        self.calls=[]
        self.serial=0
    def request(self,op,**data):
        self.calls.append(op)
        self.serial+=1
        if op not in ('inhibit','resume'):
            self.pending[self.serial]=op
        return self.serial


class RollbackTests(unittest.TestCase):
    def owner(self):
        profile=Profile('time',[Step(10,'voltage',1),Step(11,'voltage',2)])
        controls=RunControls(drive=True,profile=profile)
        s=SimpleNamespace(cfg=Config(),clients={'drive':Client()},controls=controls,
            stop=threading.Event(),exit_requested=False,drive_profile=profile,drive_started_at=0.,
            drive_epoch=0.,sequencer=Sequencer(profile),performance=None,clock=100.,
            events=[],logs=[],off_ok=True,enabled=True,performance_step=None,performance_heater_w=None)
        s.sequencer.index=1
        s.now=lambda:s.clock
        s.emit=lambda *v:s.events.append(v)
        s.logger=SimpleNamespace(log=s.logs.append)
        s.abort_performance=lambda text:None
        s.shutdown=Shutdown(s)
        s.rollback=Rollback(s)
        return s

    def test_time_stage_gets_full_second_after_command_ack(self):
        s=self.owner()
        s.rollback.request(0)
        s.shutdown.tick()
        s.clock=200
        s.rollback.tick()
        self.assertNotIn('set',s.clients['drive'].calls)
        s.clients['drive'].pending.clear()  # barrier acknowledgement
        s.rollback.tick()
        s.clock=205
        s.rollback.tick()
        self.assertEqual(s.drive_epoch,0.)
        s.clients['drive'].pending.clear()  # first target acknowledged
        s.clock=210
        s.rollback.tick()
        self.assertEqual(s.drive_epoch,200.)
        self.assertEqual(s.drive_started_at,0.)
        self.assertEqual(s.sequencer.select(210-s.drive_epoch,None).value,1)
        self.assertEqual(s.sequencer.select(211-s.drive_epoch,None).value,2)

    def test_heater_unknown_never_changes_ac_or_starts_rollback_clock(self):
        s=self.owner()
        s.clients['heater']=Client()
        s.rollback.request(0)
        s.clients['drive'].pending.clear()
        for _ in range(3):
            s.shutdown.tick()
            s.rollback.tick()
        self.assertIn('off',s.clients['heater'].calls)
        self.assertNotIn('set',s.clients['drive'].calls)
        self.assertNotIn('off',s.clients['drive'].calls)
        self.assertEqual(s.drive_epoch,0.)
        s.clients['heater'].off_confirmed=True
        s.shutdown.tick()
        s.rollback.tick()
        self.assertNotIn('set',s.clients['drive'].calls)  # wait for handle cleanup
        del s.clients['heater']
        s.rollback.tick()
        self.assertIn('set',s.clients['drive'].calls)

    def test_temperature_conditions_may_immediately_advance_after_rollback(self):
        s=self.owner()
        s.drive_profile=Profile('temperature',[Step(30,'voltage',1),Step(20,'voltage',2),Step(10,'voltage',3)])
        s.sequencer=Sequencer(s.drive_profile)
        s.sequencer.index=2
        s.rollback.request(0)
        s.shutdown.tick()
        s.clients['drive'].pending.clear()
        s.rollback.tick()
        s.clients['drive'].pending.clear()
        s.rollback.tick()
        self.assertEqual(s.sequencer.select(0,5).value,3)

    def test_future_stage_is_rejected_without_sending_any_command(self):
        s=self.owner()
        s.sequencer.index=0
        s.rollback.request(1)
        self.assertFalse(s.clients['drive'].calls)
        self.assertIsNone(s.rollback.pending)

    def test_rejected_old_voltage_request_does_not_cancel_rollback(self):
        with tempfile.TemporaryDirectory() as directory:
            profile=Profile('time',[Step(0,'voltage',1)])
            s=Supervisor(Config(),threading.Event(),lambda *v:None,True,
                         output_dir=directory,controls=RunControls(drive=True,profile=profile))
            client=Client()
            client.poll=lambda:[('reply',dict(id=1,op='set',ok=False,communication=False,
                                            message='设备已锁止，丢弃旧输出指令'))]
            s.clients['drive']=client
            s.rollback.pending=dict(index=0,barrier=2,applied=None)
            try:
                s.events()
                self.assertFalse(s.faults)
                self.assertIsNotNone(s.rollback.pending)
                self.assertTrue(s.controls.drive.is_set())
            finally:
                s.log_file.close()
                s.logger.close()

    def test_rollback_crosses_management_process_and_keeps_collecting(self):
        cfg=Config(interval=.05,settle=0,reference_channel=101,channels=[Channel(101)])
        profile=Profile('time',[Step(0,'voltage',1),Step(.4,'voltage',2)])
        controls=RunControls(True,True,profile)
        controls.device_processes=True
        stop=threading.Event()
        events=[]
        phase=[0]
        def emit(kind,value):
            events.append((kind,value))
            if phase[0]==0 and kind=='sample' and value.get('stage')==2:
                phase[0]=1
                controls.rollback(0)
            elif phase[0]==1 and kind=='rollback_status' and value.startswith('已回退'):
                phase[0]=2
            elif phase[0]==2 and kind=='sample' and value.get('stage')==1:
                phase[0]=3
            elif phase[0]==3 and kind=='sample' and value.get('stage')==2:
                phase[0]=4
                stop.set()
        timeout=threading.Timer(10,stop.set)
        timeout.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                run(cfg,stop,emit,True,output_dir=directory,controls=controls)
        finally:
            timeout.cancel()
        self.assertEqual(phase[0],4)
        self.assertTrue(events[-1][1]['off_ok'])

    def test_active_performance_heater_is_closed_before_repeating_point(self):
        cfg=Config(interval=.05,settle=0,reference_channel=101,channels=[Channel(101)],
                   heater_enabled=True,heater_power=.02,power_slew_v_s=100)
        profile=Profile('time',[Step(0,'voltage',1,performance_points=(1,)),Step(.1,'voltage',2)])
        controls=RunControls(True,True,profile)
        controls.device_processes=True
        controls.performance=PerformanceRun((Point(300,.02,'manual'),))
        stop=threading.Event()
        phase=[0]
        def emit(kind,value):
            if phase[0]==0 and controls.performance.ready.is_set():
                phase[0]=1
                controls.rollback(0)
            elif phase[0]==1 and kind=='rollback_status' and value.startswith('已回退'):
                phase[0]=2
            elif phase[0]==2 and controls.performance.ready.is_set() and kind=='heater_sample':
                phase[0]=3
                stop.set()
        timeout=threading.Timer(12,stop.set)
        timeout.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                run(cfg,stop,emit,True,output_dir=directory,controls=controls)
                trace=next(Path(directory).glob('*communication.txt')).read_text(encoding='utf-8')
                self.assertGreaterEqual(len(list(Path(directory).glob('*_performance.csv'))),2)
        finally:
            timeout.cancel()
        self.assertEqual(phase[0],3)
        self.assertLess(trace.index('heater OFF_CONFIRMED'),trace.index('已回退到阶段1'))
