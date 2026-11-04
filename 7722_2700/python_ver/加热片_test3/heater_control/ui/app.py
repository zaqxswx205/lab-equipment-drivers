"""Main application window."""
import queue
import threading
from collections import deque
from pathlib import Path

import tkinter as tk
from tkinter import messagebox

from core import Config, load_config
from runtime import RunControls
from ui.actions import ActionsMixin
from ui.constants import BASE
from ui.dialogs import DialogMixin
from ui.events import EventsMixin
from ui.form import FormMixin
from ui.layout import LayoutMixin
from ui.plots import PlotMixin


class App(LayoutMixin, FormMixin, ActionsMixin, EventsMixin, PlotMixin, DialogMixin, tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('7722 + 2700 · 制冷机交流驱动与温度采集')
        self.geometry('1480x985')
        self.minsize(1320, 950)
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
        self.history = {}
        self.power_history = deque(maxlen=600)
        self.variables = {}
        self.path = BASE/'config.ini'
        try:
            self.cfg = load_config(self.path) if self.path.exists() else Config()
        except Exception as exc:
            messagebox.showerror('配置读取失败，使用默认值', str(exc))
            self.cfg = Config()
        self.build()
        self.refresh_run_buttons()
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.after(100, self.poll)

    def close(self):
        if self.off_failed and (not self.worker or not self.worker.is_alive()):
            messagebox.showerror('输出状态未确认', '请手动关闭电源并检查通信；输出状态未确认时不能直接退出。')
            return
        self.closing = True
        self.stop()
