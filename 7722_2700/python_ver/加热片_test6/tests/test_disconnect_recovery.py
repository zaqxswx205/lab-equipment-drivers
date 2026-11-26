"""Fault injection for hung discovery, responsive Tk, and safe reconnection."""
from functools import partial
from pathlib import Path
import queue
import tempfile
import threading
import time
import tkinter as tk
import unittest
from unittest.mock import patch

from communication.discovery import DiscoveryJob, bounded_call
from communication.worker import DeviceLoop
from core import Config, Channel, SimTransport
from profile.model import Profile, Step
from runtime import RunControls
from session.supervisor import Supervisor


def fake_discovery(action, payload, connection, marker=None, stall=False):
    if action == 'ports':
        value = [('COM_TEST', '测试设备')]
    elif stall:
        time.sleep(60)
        return
    elif action == 'probe':
        profile = dict(baud=9600, bits=8, parity='N', stop=1, flow='none')
        value = ('COM_TEST', '未识别', '设备离线', None) if marker and Path(marker).exists() else (
            'COM_TEST', '7722电源', 'ITECH,IT-M7722,TEST,1', profile)
    else:
        value = None
    connection.send(dict(ok=True, value=value))
    connection.close()


class DiscoveryRecoveryTests(unittest.TestCase):
    def test_failed_panel_selection_can_recover_and_does_not_trap_close(self):
        from ui.app import App
        app = App()
        app.withdraw()
        record = ('COM_TEST', '7722电源', 'ITECH,IT-M7722,TEST,1', {'baud':9600})
        failure = dict(key='power_port', record=record, mode='AC', error='设备离线', uncertain=True)
        try:
            app.handle_scan_event('scan_panel_error', failure)
            self.assertTrue(app.off_failed)
            app.handle_scan_event('scan_panel_done', ('power_port', record, 'AC'))
            self.assertFalse(app.off_failed)
            self.assertEqual(app.variables['power_port'][0].get(), 'COM_TEST')
            self.assertNotIn('用途指定失败', app.system_text.get())
            app.handle_scan_event('scan_panel_error', failure)
            app.after(30, app.close)
            app.after(2500, app.destroy)
            start = time.monotonic()
            app.mainloop()
            self.assertLess(time.monotonic()-start, 1.)
        finally:
            try:
                app.destroy()
            except tk.TclError:
                pass

    def test_stuck_driver_has_total_deadline(self):
        start = time.monotonic()
        result = bounded_call('probe', {}, .2, threading.Event(), partial(fake_discovery, stall=True))
        self.assertFalse(result['ok'])
        self.assertIn('超时', result['error'])
        self.assertLess(time.monotonic()-start, 3)

    def test_tk_keeps_ticking_and_can_close_during_stuck_probe(self):
        from ui.app import App
        app = App()
        ticks = []
        def beat():
            ticks.append(time.monotonic())
            app.after(20, beat)
        def factory(kind, emit, **kwargs):
            return DiscoveryJob(kind, emit, target=partial(fake_discovery, stall=True), **kwargs)
        with patch('ui.scan.DiscoveryJob', factory):
            app.withdraw()
            app.scan_devices()
            job = app.discovery_job
            beat()
            app.after(700, app.close)
            app.after(3500, app.destroy)
            start = time.monotonic()
            app.mainloop()
        job.thread.join(3)
        self.assertFalse(job.thread.is_alive())
        self.assertGreater(len(ticks), 10)
        self.assertLess(time.monotonic()-start, 3)

    def test_offline_discovery_recovers_automatically(self):
        from ui.app import App
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory)/'offline'
            marker.touch()
            app = App()
            app.withdraw()
            result = []
            def observe():
                record = getattr(app, 'scan_known', {}).get('COM_TEST')
                if record and record[1] == '7722电源':
                    result.append(record)
                    app.quit()
                else:
                    app.after(30, observe)
            def factory(kind, emit, **kwargs):
                return DiscoveryJob(kind, emit, target=partial(fake_discovery, marker=str(marker)), **kwargs)
            try:
                with patch('ui.scan.DiscoveryJob', factory):
                    app.scan_devices()
                    app.after(700, marker.unlink)
                    app.after(7000, app.quit)
                    observe()
                    app.mainloop()
                self.assertTrue(result)
                self.assertFalse(app.scanning)
                self.assertNotIn('COM_TEST', app.scan_retry_ports)
                self.assertEqual(len(app.scan_table.get_children()), 1)
            finally:
                app.destroy()


class RuntimeRecoveryTests(unittest.TestCase):
    def test_power_reconnect_only_queries_even_if_output_is_still_on(self):
        class Transport(SimTransport):
            def query(self, command):
                return 'ITECH,IT-M7722,SN,1' if command == '*IDN?' else super().query(command)
        tp = Transport()
        tp.state['output'] = True
        events = queue.Queue()
        loop = DeviceLoop('drive', Config(), False, events, threading.Event(), threading.Event(), {},
                          dict(expected_identity='ITECH,IT-M7722,SN,1'))
        with patch('core.SerialTransport', return_value=tp):
            loop.reconnect()
        self.assertTrue(tp.state['output'])
        self.assertEqual(tp.commands, ['OUTP:STAT?'])
        self.assertTrue(loop.inhibit.is_set())
        recovered = [data for kind,data in list(events.queue) if kind=='reconnected']
        self.assertFalse(recovered[0]['off_confirmed'])
        with self.assertRaises(RuntimeError):
            loop.execute('set', dict(voltage=1, enabled=True))
        tp.close()

    def test_heater_reconnect_keeps_ac_and_meter_and_never_resumes_heating(self):
        cfg = Config(heater_enabled=True, heater_control='voltage', heater_voltage=1,
                     interval=.05, settle=0, channels=[Channel(101)], reference_channel=101)
        controls = RunControls(True, True, Profile('time', [Step(0, 'voltage', 1)]),
                               heater=True, heater_link=False)
        events = []
        def until(predicate, seconds=6):
            deadline = time.monotonic()+seconds
            while time.monotonic()<deadline:
                if predicate():
                    return True
                time.sleep(.02)
            return False
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory)/'offline'
            controls.worker_options = {'heater': {'offline_flag': str(marker)}}
            supervisor = Supervisor(cfg, threading.Event(), lambda *v:events.append(v), True,
                                    output_dir=directory, controls=controls)
            thread = threading.Thread(target=supervisor.run)
            thread.start()
            try:
                self.assertTrue(until(lambda: all(supervisor.clients.get(r) and supervisor.clients[r].output_on
                                                for r in ('heater', 'drive'))))
                marker.touch()
                self.assertTrue(until(lambda:'heater' in supervisor.faults))
                before = sum(k=='sample' for k,v in events)
                time.sleep(.2)
                self.assertGreater(sum(k=='sample' for k,v in events), before)
                self.assertTrue(supervisor.clients['drive'].output_on)
                marker.unlink()
                self.assertTrue(until(lambda:any(k=='connection_recovered' and v['role']=='heater' for k,v in events)))
                self.assertTrue(until(lambda:'heater' not in supervisor.clients))
                self.assertFalse(controls.heater.is_set())
                self.assertTrue(supervisor.clients['drive'].output_on)
                self.assertTrue(controls.acquisition.is_set())
            finally:
                marker.unlink(missing_ok=True)
                supervisor.stop.set()
                thread.join(10)
            self.assertFalse(thread.is_alive())
