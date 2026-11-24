"""Communication trace logger."""
from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path


class Logger:
    def __init__(self):
        self.trace = None
        self._lock = threading.Lock()

    def open(self, path):
        self.trace = path.open('x', encoding='utf-8')

    def log(self, text):
        with self._lock:
            if self.trace:
                self.trace.write(datetime.now().isoformat(timespec='milliseconds')+' '+text+'\n')
                self.trace.flush()

    def close(self):
        if self.trace:
            self.trace.close()
            self.trace = None
