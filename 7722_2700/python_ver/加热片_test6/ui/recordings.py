"""Recording directory controls and nonblocking folder opening."""
from dataclasses import replace
import os
import threading
from tkinter import filedialog

from model.config_io import recording_path


class RecordingsMixin:
    def choose_data_directory(self):
        if self.closing or self.scanning or not self.run_finished or (self.worker and self.worker.is_alive()):
            return
        folder = recording_path(replace(self.cfg, data_directory=self.variables['data_directory'][0].get()))
        selected = filedialog.askdirectory(parent=self, title='选择数据保存目录',
                                           initialdir=str(folder if folder.is_dir() else folder.parent),
                                           mustexist=True)
        if selected:
            self.variables['data_directory'][0].set(selected)
            self.report_status('数据保存目录已选择；下次启动采集或驱动时生效。点击“保存配置”可记住此目录。',
                               source='recording')

    def open_data_folder(self):
        if self.closing or str(self.open_data_folder_button['state']) == 'disabled':
            return
        # A running session keeps its original directory even if other fields change.
        active = not self.run_finished or bool(self.worker and self.worker.is_alive())
        cfg = self.cfg if active else replace(self.cfg, data_directory=self.variables['data_directory'][0].get())
        if not cfg.data_directory.strip():
            self.report_status('请先选择数据保存目录。', 'error', source='recording')
            return
        folder = recording_path(cfg)
        self.open_data_folder_button.configure(state='disabled')
        self.report_status('正在打开数据保存文件夹…', source='recording')

        def open_folder():
            try:
                folder.mkdir(parents=True, exist_ok=True)
                os.startfile(str(folder))
                event = ('data_folder_opened', f'已打开数据保存文件夹：{folder}')
            except Exception as exc:
                event = ('data_folder_open_failed', '打开数据保存文件夹失败：'+str(exc))
            self.events.put(event)

        threading.Thread(target=open_folder, daemon=True, name='open-data-folder').start()
