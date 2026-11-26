import tempfile
import threading
import unittest
from unittest.mock import patch

import core
from acquisition_watchdog import AcquisitionWatchdog
from drive_profile import Profile, Step
from runtime import RunControls


class WatchdogTests(unittest.TestCase):
    def test_fault_during_blocked_read_shuts_off_without_waiting_for_reader(self):
        release, blocked, confirmed = threading.Event(), threading.Event(), threading.Event()
        transport = core.SimTransport()
        devices, events = [], []
        original = core.ITech7722
        class FastWatchdog(AcquisitionWatchdog):
            def expect(self, seconds, phase):
                super().expect(.25, phase)
        def build(*args, **kwargs):
            device = original(*args, **kwargs)
            devices.append(device)
            return device
        calls = 0
        def read(meter, channel, stop):
            nonlocal calls
            calls += 1
            if calls == 3:
                blocked.set()
                release.wait(5)
            return 100, 0
        def emit(kind, value):
            events.append((kind, value))
            if kind == 'shutdown_confirmed':
                confirmed.set()
        stop = threading.Event()
        controls = RunControls(True, True, Profile('time', [Step(0, 'voltage', 3)]))
        with tempfile.TemporaryDirectory() as d, patch('runtime.AcquisitionWatchdog', FastWatchdog), \
                patch.object(core, 'SimTransport', return_value=transport), \
                patch.object(core, 'ITech7722', side_effect=build), patch.object(core.SimMeter, 'read', read):
            worker = threading.Thread(target=core.run, args=(core.Config(interval=.05), stop, emit),
                                      kwargs=dict(simulation=True, max_cycles=3, output_dir=d, controls=controls))
            worker.start()
            try:
                self.assertTrue(blocked.wait(2))
                self.assertTrue(transport.state['output'])
                self.assertTrue(confirmed.wait(2))
                self.assertTrue(worker.is_alive())
                self.assertFalse(transport.state['output'])
                self.assertFalse(transport.closed)
                with self.assertRaisesRegex(RuntimeError, '锁止'):
                    devices[0].output_on()
                self.assertTrue(any(k == 'measurement_fault' for k, _ in events))
                self.assertFalse(any(k == 'done' for k, _ in events))
            finally:
                release.set()
                worker.join(4)
            self.assertFalse(worker.is_alive())
            self.assertTrue(transport.closed)
            self.assertIn('采集无响应', events[-1][1]['error'])
            self.assertTrue(events[-1][1]['off_ok'])

    def test_disarmed_watchdog_does_not_trigger(self):
        fault = threading.Event()
        watchdog = AcquisitionWatchdog(lambda reason: fault.set())
        watchdog.expect(.01, 'test')
        watchdog.disarm()
        self.assertFalse(fault.wait(.1))
        watchdog.close()

    def test_explicit_error_triggers_even_when_worker_closes(self):
        reasons = []
        watchdog = AcquisitionWatchdog(reasons.append)
        watchdog.trip('2700设备错误')
        watchdog.close()
        self.assertEqual(reasons, ['2700设备错误'])
