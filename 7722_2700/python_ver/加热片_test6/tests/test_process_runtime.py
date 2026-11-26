"""Process isolation and ordered shutdown regression tests (no physical devices)."""
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace

from core import Config, Channel
from profile.model import Profile, Step
from runtime import RunControls
from session.shutdown import Shutdown
from session.supervisor import Supervisor, run


class FakeClient:
    def __init__(self, off=False):
        self.off_confirmed=off
        self.closing=False
        self.calls=[]
    def request(self, op, **data):
        self.calls.append(op)


class ShutdownTests(unittest.TestCase):
    def owner(self):
        events=[]
        return SimpleNamespace(clients={'heater':FakeClient(), 'drive':FakeClient()},
            controls=RunControls(True,True,heater=True), emit=lambda *v:events.append(v),
            logger=SimpleNamespace(log=lambda t:None), enabled=True,off_ok=True,events=events)

    def test_heater_unknown_never_sends_ac_off_and_keeps_acquisition(self):
        o=self.owner();s=Shutdown(o)
        s.request(drive=True)
        for _ in range(5):s.tick()
        self.assertIn('off',o.clients['heater'].calls)
        self.assertNotIn('off',o.clients['drive'].calls)
        self.assertTrue(o.controls.acquisition.is_set())
        self.assertTrue(o.enabled)
        self.assertTrue(any('加热输出关闭未确认' in value for kind,value in o.events))

    def test_ac_off_only_follows_explicit_heater_off_and_closes_after_ack(self):
        o=self.owner();s=Shutdown(o);s.request(drive=True);s.tick()
        o.clients['heater'].off_confirmed=True
        s.next_retry=0;s.tick()
        self.assertIn('off',o.clients['drive'].calls)
        self.assertNotIn('close',o.clients['drive'].calls)
        o.clients['drive'].off_confirmed=True;s.tick()
        self.assertIn('close',o.clients['drive'].calls)
        self.assertFalse(s.pending)
        self.assertTrue(o.controls.acquisition.is_set())

    def test_zero_stage_also_waits_for_heater_and_retains_ac_connection(self):
        o=self.owner();s=Shutdown(o);s.request(drive=True,close_drive=False);s.tick()
        self.assertNotIn('off',o.clients['drive'].calls)
        o.clients['heater'].off_confirmed=True;s.next_retry=0;s.tick()
        o.clients['drive'].off_confirmed=True;s.tick()
        self.assertNotIn('close',o.clients['drive'].calls)
        self.assertIn('resume',o.clients['drive'].calls)
        self.assertTrue(o.controls.drive.is_set())


class ProcessTests(unittest.TestCase):
    def config(self):
        return Config(heater_enabled=True,heater_control='voltage',heater_voltage=1,
                      interval=.05,settle=0,channels=[Channel(101)],reference_channel=101)

    def controls(self):
        c=RunControls(True,True,Profile('time',[Step(0,'voltage',1)]),heater=True,heater_link=False)
        c.device_processes=True
        return c

    def test_independent_processes_collect_and_stop_in_order(self):
        events=[];c=self.controls()
        with tempfile.TemporaryDirectory() as d:
            run(self.config(),threading.Event(),lambda *v:events.append(v),True,8,d,controls=c)
            from pathlib import Path
            trace=next(Path(d).glob('*communication.txt')).read_text(encoding='utf-8')
        self.assertEqual(events[-1][0],'done')
        self.assertTrue(events[-1][1]['off_ok'])
        self.assertTrue(any(k=='sample' and v['temperatures'] for k,v in events))
        self.assertEqual(trace.count('PROCESS '),3)
        self.assertLess(trace.index('heater OFF_CONFIRMED'),trace.index('drive OFF_CONFIRMED'))

    def test_heater_communication_fault_does_not_stop_ac_or_meter(self):
        c=self.controls();c.worker_options={'heater':{'communication_fault':True}}
        events=[]
        with tempfile.TemporaryDirectory() as d:
            run(self.config(),threading.Event(),lambda *v:events.append(v),True,10,d,controls=c)
        self.assertTrue(any(k=='alarm' and 'heater' in v for k,v in events))
        self.assertTrue(any(k=='sample' and v['output_on'] for k,v in events))
        self.assertGreater(sum(k=='sample' and bool(v['temperatures']) for k,v in events),2)

    def test_meter_fault_keeps_meter_process_and_stops_heater_before_ac(self):
        c=self.controls();c.worker_options={'meter':{'communication_fault':True}}
        events=[];stop=threading.Event()
        def emit(k,v):
            events.append((k,v))
            if k=='shutdown_confirmed':stop.set()
        with tempfile.TemporaryDirectory() as d:
            run(self.config(),stop,emit,True,output_dir=d,controls=c)
        self.assertTrue(any(k=='measurement_fault' for k,v in events))
        self.assertLess(next(i for i,(k,v) in enumerate(events) if k=='shutdown_confirmed'),
                        next(i for i,(k,v) in enumerate(events) if k=='acquisition_done'))

    def test_process_backend_executes_inline_performance_stage(self):
        from performance import PerformanceRun, Point
        c=RunControls(True,True,Profile('time',[
            Step(0,'voltage',1,performance_points=(1,)),Step(.1,'voltage',0)]))
        c.performance=PerformanceRun((Point(300,.02,'duration',.1),))
        cfg=self.config();cfg.heater_control='power';cfg.heater_power=.02;cfg.power_slew_v_s=100
        events=[]
        with tempfile.TemporaryDirectory() as d:
            run(cfg,threading.Event(),lambda *v:events.append(v),True,40,d,controls=c)
        self.assertEqual(c.performance.phase,'complete')
        self.assertTrue(any(k=='performance_stats' for k,v in events))
        self.assertFalse(events[-1][1]['error'])

    def test_blocked_device_io_has_independent_process_heartbeat(self):
        from communication.process import DeviceProcess
        cfg=self.config();cfg.timeout=.1
        endpoint=DeviceProcess('heater',cfg,True,{'block_io':6.})
        original_pid=endpoint.process.pid
        events=[]
        try:
            deadline=time.monotonic()+8
            while time.monotonic()<deadline:
                events.extend(endpoint.poll())
                if any(k=='fault' and '调用超时' in v['message'] for k,v in events):break
                time.sleep(.02)
            self.assertTrue(any(k=='fault' and '调用超时' in v['message'] for k,v in events))
            self.assertTrue(endpoint.process.is_alive())
            self.assertNotEqual(endpoint.process.pid,original_pid)
            self.assertLess(time.monotonic()-endpoint.last_process_heartbeat,1.)
        finally:
            endpoint.closing=True
            endpoint.request('close')
            deadline=time.monotonic()+8
            while time.monotonic()<deadline:
                endpoint.poll()
                if endpoint.reap():break
                time.sleep(.02)
        self.assertFalse(endpoint.process.is_alive())

    def test_unconfirmed_heater_keeps_meter_live_and_ac_on_without_blocking(self):
        c=self.controls();c.worker_options={'heater':{'off_unconfirmed':True}}
        events=[];result={}
        with tempfile.TemporaryDirectory() as d:
            supervisor=Supervisor(self.config(),threading.Event(),lambda *v:events.append(v),True,output_dir=d,controls=c)
            thread=threading.Thread(target=supervisor.run)
            thread.start()
            try:
                deadline=time.monotonic()+10
                while time.monotonic()<deadline:
                    heater=supervisor.clients.get('heater')
                    drive=supervisor.clients.get('drive')
                    if heater and heater.output_on and drive and drive.output_on:break
                    time.sleep(.02)
                self.assertTrue(heater and heater.output_on and drive and drive.output_on)
                c.drive.clear()
                deadline=time.monotonic()+3
                while time.monotonic()<deadline and not any(k=='shutdown_pending' for k,v in events):time.sleep(.02)
                before=sum(k=='sample' for k,v in events)
                time.sleep(.25)
                self.assertGreater(sum(k=='sample' for k,v in events),before)
                self.assertTrue(supervisor.clients['drive'].output_on)
                self.assertNotIn('off',supervisor.clients['drive'].pending.values())
                self.assertTrue(c.acquisition.is_set())
            finally:
                # Test teardown only: release the injected refusal before closing.
                # Sending an explicit test-only command avoids killing an output process.
                h=supervisor.clients.get('heater')
                if h:h.request('test_release_off')
                supervisor.stop.set()
                thread.join(10)
            self.assertFalse(thread.is_alive())


if __name__=='__main__':unittest.main()
