from ui import notices
"""Main application window."""
import queue
import threading
from collections import deque
from pathlib import Path

import tkinter as tk
from tkinter import messagebox

from core import Config, load_config, profile_path
from profile.settings import settings_from_profile
from runtime import RunControls
from ui.scan import ScanMixin
from ui.actions import ActionsMixin
from ui.constants import BASE
from ui.dialogs import DialogMixin
from ui.events import EventsMixin
from ui.form import FormMixin
from ui.layout import LayoutMixin
from ui.plots import PlotMixin
from ui.recordings import RecordingsMixin
from ui.channel_names import ChannelNamesMixin


class App( ScanMixin, LayoutMixin, FormMixin, ActionsMixin, EventsMixin, PlotMixin, DialogMixin, RecordingsMixin, ChannelNamesMixin, tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('制冷机交流驱动与温度采集')
        self.geometry('1480x985')
        self.minsize(1320, 950)
        self.scanning = False
        self.worker = None
        self.run_finished = True
        self.controls = RunControls()
        self.acquisition_busy = False
        self.drive_busy = False
        self.heater_busy = False
        self.active_profile = None
        self.stop_event = threading.Event()
        self.events = queue.Queue()
        self.closing = False
        self.off_failed = False
        self.plot_process = None
        self.plot_session_id = 0
        self.history = {}
        self.power_history = deque(maxlen=600)
        self.variables = {}
        self.path = BASE/'config.ini'
        try:
            self.cfg = load_config(self.path) if self.path.exists() else Config()
        except Exception as exc:
            notices.error(self, '配置读取失败，使用默认值', str(exc))
            self.cfg = Config()
        try:
            self.cfg = settings_from_profile(profile_path(self.cfg), self.cfg)
        except Exception as exc:
            self.report_status('方案不可运行：'+str(exc), 'error', source='profile')
        self.channel_names = {c.channel: c.name for c in self.cfg.channels}
        self.build()
        self.bind_profile_validation()
        self.refresh_run_buttons()
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.after(100, self.poll)

    def close(self):
        self.commit_channel_name()
        if self.scanning:
            job = getattr(self, 'discovery_job', None)
            if job:
                job.cancel()
            self.scanning = False
        if self.off_failed and not getattr(self, 'scan_uncertain', {}) and (not self.worker or not self.worker.is_alive()):
            notices.error(self, '输出状态未确认', '请手动关闭电源并检查通信；输出状态未确认时不能直接退出。')
            return
        self.closing = True
        self.stop()

    def destroy(self):
        self.commit_channel_name()
        job = getattr(self, 'discovery_job', None)
        if job:
            job.cancel()
        for timer in self.tk.call('after', 'info'):
            self.after_cancel(timer)
        viewer = getattr(self, 'plot_process', None)
        if viewer:
            viewer.close()
            self.plot_process = None
        super().destroy()
