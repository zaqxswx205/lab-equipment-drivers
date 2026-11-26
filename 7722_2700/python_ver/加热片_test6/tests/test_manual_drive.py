"""Manual start never executes a disk plan; aborted testing does not trap restart."""
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from core import Config, Channel
from drive_profile import Profile, Step
from performance import PerformanceRun, Point
from runtime import RunControls, run


class ManualDriveTests(unittest.TestCase):
    def app(self):
        from ui.app import App
        app=App()
        app.withdraw()
        self.addCleanup(app.destroy)
        return app

    def test_manual_button_does_not_read_plan_or_create_performance(self):
        app=self.app()
        manual=Profile('time',[Step(0,'voltage',1)])
        with patch('ui.actions.ask_manual_drive',return_value=manual), \
             patch('ui.actions.confirm_parameters',return_value=True), \
             patch('ui.actions.load_profile',side_effect=AssertionError('手动驱动不读方案')), \
             patch('ui.actions.save_config'), patch('ui.actions.threading.Thread') as thread:
            app.drive_button.invoke()
            thread.assert_called_once()
        self.assertIsNone(getattr(app.controls,'performance',None))
        self.assertEqual(app.controls.profile(),manual)
        self.assertEqual(app.drive_source,'manual')

    def test_profile_button_is_a_separate_explicit_entry(self):
        app=self.app()
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'plan.ini'
            path.write_text('[Drive]\nstrategy=time\ntemperature_unit=K\n'
                            '[Step 1]\nat=0\nmode=voltage\nvalue=1\n'
                            'heater_w=.1\ntest_temperature_k=280\n',encoding='utf-8')
            app.variables['profile_file'][0].set(str(path))
            with patch('ui.actions.ask_manual_drive',side_effect=AssertionError('方案入口不询问手动目标')), \
                 patch('ui.actions.confirm_parameters',return_value=True), \
                 patch('ui.actions.save_config'),patch('ui.actions.threading.Thread'):
                app.run_profile_button.invoke()
            self.assertIsNotNone(app.controls.performance)
            self.assertEqual(app.drive_source,'profile')

    def test_aborted_performance_allows_manual_retry_without_stopping_meter(self):
        app=self.app()
        app.run_finished=False
        app.worker=SimpleNamespace(is_alive=lambda:True)
        app.acquisition_busy=True
        app.controls.start_acquisition()
        app.controls.performance=PerformanceRun((Point(280,.1),))
        app.controls.performance.phase='aborted'
        with patch('ui.actions.ask_manual_drive',return_value=Profile('time',[Step(0,'voltage',1)])), \
             patch('ui.actions.confirm_parameters',return_value=True):
            app.start_part('drive')
        self.assertTrue(app.controls.drive.is_set())
        self.assertTrue(app.controls.acquisition.is_set())
        self.assertIsNone(app.controls.performance)
        self.assertFalse(app.stop_event.is_set())

    def test_offline_plan_abort_online_then_manual_restart_through_manager(self):
        cfg=Config(interval=.05,settle=0,channels=[Channel(101)],reference_channel=101,
                   heater_enabled=True,heater_power=.1,heater_temperature_enabled=False)
        profile=Profile('time',[Step(0,'voltage',1,performance_points=(1,))])
        controls=RunControls(True,True,profile)
        controls.performance=PerformanceRun((Point(300,.1),))
        controls.device_processes=True
        stop=threading.Event()
        events=[]
        stage=[0]
        with tempfile.TemporaryDirectory() as directory:
            marker=Path(directory)/'offline'
            marker.touch()
            controls.worker_options={'drive':{'offline_flag':str(marker)}}
            def emit(kind,value):
                events.append((kind,value))
                if stage[0]==0 and kind=='sample' and controls.performance.phase=='aborted':
                    stage[0]=1
                    marker.unlink(missing_ok=True)
                elif stage[0]==1 and kind=='drive_done':
                    stage[0]=2
                    controls.performance=None
                    controls.start_drive(Profile('time',[Step(0,'voltage',2)]))
                elif stage[0]==2 and kind=='sample' and value['output_on'] and value['setting']==2:
                    stage[0]=3
                    stop.set()
            watchdog=threading.Timer(12,stop.set)
            watchdog.start()
            try:
                run(cfg,stop,emit,True,output_dir=directory,controls=controls)
            finally:
                watchdog.cancel()
            self.assertEqual(stage[0],3)
            self.assertTrue(any(k=='performance_aborted' for k,v in events))
            self.assertFalse(any(k=='alarm' and '性能点测试的驱动或采集已停止' in v for k,v in events))
            self.assertTrue(events[-1][1]['off_ok'])
