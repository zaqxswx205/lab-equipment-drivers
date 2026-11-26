import csv
from datetime import datetime
from pathlib import Path
import queue
import tempfile
import threading
import unittest
from unittest.mock import patch

from core import Config, Channel, load_config, save_config
from csv_recording import CsvRecording
from model.channels import clean_channel_name
from model.config_io import save_channel_name
from plotting.window import CurveWindow
from runtime import RunControls, run


class ChannelNameTests(unittest.TestCase):
    def test_config_roundtrip_and_targeted_save_preserve_device_settings(self):
        cfg = Config(max_voltage=17, channels=[Channel(101, name='冷头,一级'), Channel(102, name='热端 50%')])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'config.ini'
            save_config(path, cfg)
            self.assertEqual(load_config(path), cfg)
            save_channel_name(path, 101, ' 冷头“一级” ')
            restored = load_config(path)
            self.assertEqual(restored.channels[0].name, '冷头“一级”')
            self.assertEqual(restored.channels[1], cfg.channels[1])
            self.assertEqual(restored.max_voltage, 17)
            self.assertEqual(restored.channels[0].min_temperature, cfg.channels[0].min_temperature)
            save_channel_name(path, 101, '')
            self.assertEqual(load_config(path).channels[0].name, '')
        for invalid in ('x'*41, '冷头\n热端', '\t冷头'):
            with self.assertRaises(ValueError):
                clean_channel_name(invalid)

    def test_curve_labels_and_snapshot_keep_selection_and_view(self):
        requests = queue.Queue()
        window = CurveWindow(queue.Queue(), queue.Queue(), requests)
        try:
            requests.get_nowait()
            data = dict(session_id=7, channels=[101, 102], channel_names={101:'冷头', 102:'热端'},
                        history={101:[(0, 273.15, 100)], 102:[(0, 280, 103)]})
            window.update_snapshot(data)
            window.select_none()
            window.channel_list.selection_set(1)
            window.channel_list.activate(1)
            window.freeze()
            bounds = dict(window.bounds)
            window.update_snapshot(dict(data, channel_names={101:'冷头', 102:'热端,右侧'}))
            self.assertEqual(window.channel_list.curselection(), (1,))
            self.assertEqual(window.series('temperature')[0][0], '102 · 热端,右侧')
            self.assertIn('102 · 热端,右侧', window.channel_list.get(1))
            self.assertEqual(window.bounds, bounds)
            self.assertFalse(window.follow.get())
            self.assertTrue(requests.empty())
        finally:
            window.destroy()

    def test_main_editor_updates_live_and_keeps_unsaved_device_parameters(self):
        from ui.app import App
        cfg = Config()
        with patch('ui.app.load_config', return_value=cfg):
            app = App()
        app.withdraw()
        try:
            with tempfile.TemporaryDirectory() as folder:
                app.path = Path(folder)/'config.ini'
                save_config(app.path, Config(max_voltage=17))
                app.variables['max_voltage'][0].set('25')
                app.run_finished = False
                app.refresh_run_buttons()
                self.assertEqual(str(app.channel_name_entry['state']), 'normal')
                app.channel_vars['channel'].set('102')
                app.channel_vars['name'].set('热端')
                app.commit_channel_name()
                self.assertEqual(app.controls.requests.get_nowait(),
                                 dict(kind='rename_channel', channel=102, name='热端'))
                self.assertEqual(app.channel_names[101], '')
                self.assertEqual(app.channel_names[102], '热端')
                self.assertEqual(app.table.set('102', 'name'), '热端')
                self.assertEqual(app.collect().channels[1].name, '热端')
                self.assertEqual(app.plot_snapshot()['channel_names'][102], '热端')
                self.assertEqual(load_config(app.path).channels[1].name, '热端')
                self.assertEqual(load_config(app.path).max_voltage, 17)
                self.assertEqual(app.variables['max_voltage'][0].get(), '25')
        finally:
            app.plot_process = None
            app.destroy()

    def test_switching_channels_flushes_the_old_name_and_debounces_the_new_name(self):
        from ui.app import App
        with patch('ui.app.load_config', return_value=Config()):
            app = App()
        app.withdraw()
        try:
            with tempfile.TemporaryDirectory() as folder:
                app.path = Path(folder)/'config.ini'
                save_config(app.path, Config())
                app.channel_vars['name'].set('冷头')
                app.channel_vars['channel'].set('102')
                self.assertEqual(app.channel_names[101], '冷头')
                self.assertEqual(app.channel_vars['name'].get(), '')
                app.channel_vars['name'].set('热端')
                import tkinter as tk
                finished = tk.BooleanVar(master=app, value=False)
                app.after(500, lambda:finished.set(True))
                app.wait_variable(finished)
                self.assertEqual(app.table.set('102', 'name'), '热端')
                self.assertEqual([c.name for c in load_config(app.path).channels], ['冷头', '热端'])
                app.table.selection_set('101')
                app.select_channel()
                self.assertEqual(app.channel_vars['name'].get(), '冷头')
        finally:
            app.destroy()

    def test_new_channel_takes_its_name_and_invalid_names_do_not_replace_existing_names(self):
        from ui.app import App
        with patch('ui.app.load_config', return_value=Config()):
            app = App()
        app.withdraw()
        try:
            with tempfile.TemporaryDirectory() as folder:
                app.path = Path(folder)/'config.ini'
                save_config(app.path, Config())
                app.channel_vars['channel'].set('103')
                app.channel_vars['name'].set('冷端二级')
                app.add_channel()
                self.assertEqual(app.table.set('103','name'), '冷端二级')
                app.save()
                self.assertEqual(load_config(app.path).channels[-1].name, '冷端二级')
                app.channel_vars['name'].set('x'*41)
                app.commit_channel_name()
                self.assertEqual(app.channel_names[103], '冷端二级')
                self.assertEqual(load_config(app.path).channels[-1].name, '冷端二级')
                app.channel_vars['name'].set('冷端二级')
                app.commit_channel_name()
                self.assertNotIn('通道命名失败', app.system_text.get())
        finally:
            app.destroy()

    def test_live_rename_reaches_csv_in_both_backends_without_rotating_file(self):
        for processes in (False, True):
            with self.subTest(processes=processes), tempfile.TemporaryDirectory() as folder:
                cfg = Config(interval=.05, settle=0, reference_channel=101,
                             channels=[Channel(101, name='旧名称')])
                controls = RunControls(True, False)
                controls.device_processes = processes
                renamed = False
                events = []
                def emit(kind, value):
                    nonlocal renamed
                    events.append((kind, value))
                    if kind == 'sample' and not renamed:
                        renamed = True
                        controls.rename_channel(101, '冷头,一级')
                run(cfg, threading.Event(), emit, simulation=True, max_cycles=12,
                    output_dir=folder, controls=controls)
                self.assertFalse(events[-1][1]['error'])
                files = list(Path(folder).glob('*.csv'))
                self.assertEqual(len(files), 1)
                with files[0].open(encoding='utf-8-sig', newline='') as stream:
                    rows = list(csv.DictReader(stream))
                self.assertEqual(rows[0]['通道101名称'], '旧名称')
                self.assertEqual(rows[-1]['通道101名称'], '冷头,一级')
                self.assertTrue(all(None not in row and row['通道101温度(K)'] for row in rows))
                self.assertEqual(list(rows[0])[-1], '驱动方案阶段')

    def test_name_columns_survive_rollover_and_keep_duplicate_names_distinguishable(self):
        cfg = Config(channels=[Channel(101, name='冷头'), Channel(102, name='冷头')])
        with tempfile.TemporaryDirectory() as folder:
            recorder = CsvRecording(folder, channels=cfg.channels)
            for when in (datetime(2026, 11, 26, 23, 59, 59), datetime(2026, 11, 27)):
                recorder.write_snapshot(when, [(cfg.channels[0],100,0,when,0),
                                              (cfg.channels[1],101,1,when,0)], cfg=cfg)
            recorder.close()
            for path in Path(folder).glob('*.csv'):
                with path.open(encoding='utf-8-sig', newline='') as stream:
                    row = next(csv.DictReader(stream))
                self.assertEqual(row['通道101名称'], '冷头')
                self.assertEqual(row['通道102名称'], '冷头')
                self.assertNotEqual(row['通道101温度(K)'], row['通道102温度(K)'])

    def test_performance_detail_also_records_channel_name(self):
        from tests.test_performance import PerformanceTests
        fixture = PerformanceTests()
        cfg = fixture.cfg()
        cfg.channels[0].name = '冷头'
        cfg.channels[1].name = '参考热端'
        with patch.object(fixture, 'cfg', return_value=cfg):
            events, rows, performance = fixture.run_points([(300,.02,.02)], [20])
        self.assertFalse(events[-1][1]['error'])
        self.assertTrue(rows)
        self.assertEqual({(row['channel'],row['channel_name']) for row in rows},
                         {('101','冷头'),('102','参考热端')})
