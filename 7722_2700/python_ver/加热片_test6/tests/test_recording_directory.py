from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from core import Config, load_config, save_config
from model.config_io import ROOT, recording_path
from runtime import RunControls, run
from session.supervisor import Supervisor


class RecordingDirectoryTests(unittest.TestCase):
    def test_directory_roundtrip_default_and_relative_resolution(self):
        self.assertEqual(recording_path(Config()), ROOT/'session'/'data')
        self.assertEqual(recording_path(Config(data_directory='实验数据')), ROOT/'实验数据')
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder)/'config.ini'
            directory = str(Path(folder)/'实验数据')
            save_config(config, Config(data_directory=directory))
            self.assertEqual(load_config(config).data_directory, directory)
        with self.assertRaisesRegex(ValueError, '数据保存地址不能为空'):
            Config(data_directory='  ').validate()

    def test_both_backends_write_csv_and_trace_to_configured_directory(self):
        for processes in (False, True):
            with self.subTest(processes=processes), tempfile.TemporaryDirectory() as folder:
                destination = Path(folder)/'实验数据'
                controls = RunControls(True, False)
                controls.device_processes = processes
                events = []
                run(Config(interval=.05, settle=0, data_directory=str(destination)),
                    threading.Event(), lambda *event: events.append(event),
                    simulation=True, max_cycles=1, controls=controls)
                self.assertFalse(events[-1][1]['error'])
                self.assertEqual(len(list(destination.glob('*.csv'))), 1)
                self.assertEqual(len(list(destination.glob('*_communication.txt'))), 1)

    def test_explicit_output_directory_still_overrides_configuration(self):
        with tempfile.TemporaryDirectory() as folder:
            selected = Path(folder)/'selected'
            override = Path(folder)/'override'
            supervisor = Supervisor(Config(data_directory=str(selected)), threading.Event(),
                                    lambda *event: None, simulation=True, output_dir=override)
            try:
                self.assertEqual(supervisor.output_dir, override)
                self.assertFalse(selected.exists())
            finally:
                supervisor.logger.close()
                supervisor.log_file.close()

    def test_ui_location_persistence_session_lock_and_active_directory_view(self):
        from ui.app import App
        with patch('ui.app.load_config', return_value=Config()):
            app = App()
        app.withdraw()
        try:
            self.assertEqual(app.recording_row.grid_info()['row'], 1)
            with tempfile.TemporaryDirectory() as folder:
                directory = Path(folder)/'chosen'
                directory.mkdir()
                app.path = Path(folder)/'config.ini'
                with patch('ui.recordings.filedialog.askdirectory', return_value=str(directory)):
                    app.choose_data_directory()
                app.save()
                self.assertEqual(load_config(app.path).data_directory, str(directory))
                app.run_finished = False
                app.refresh_run_buttons()
                self.assertEqual(str(app.data_directory_entry['state']), 'disabled')
                self.assertEqual(str(app.choose_data_directory_button['state']), 'disabled')
                self.assertEqual(str(app.open_data_folder_button['state']), 'normal')
                # Even a programmatic change to the locked entry cannot redirect a live session.
                app.variables['data_directory'][0].set(str(Path(folder)/'other'))
                with patch('ui.recordings.os.startfile', create=True) as launch:
                    app.open_data_folder()
                    kind, message = app.events.get(timeout=3)
                    self.assertEqual(kind, 'data_folder_opened', message)
                    self.assertEqual(Path(launch.call_args.args[0]), directory)
                self.assertEqual(list(directory.iterdir()), [])
        finally:
            app.destroy()
