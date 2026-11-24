"""Runtime controls for independent acquisition/drive/heater management."""
from __future__ import annotations

import threading
import time


class RunControls:
    def __init__(self, acquisition=False, drive=False, profile=None, heater=False, heater_link=True):
        self.drive_ready = threading.Event()
        self.heater_link = threading.Event()
        if heater_link:
            self.heater_link.set()
        self.heater = threading.Event()
        self.heater_pending = threading.Event()
        self.acquisition = threading.Event()
        self.drive = threading.Event()
        self.acquisition_pending = threading.Event()
        self.drive_pending = threading.Event()
        self._profile = profile
        self._lock = threading.Lock()
        if heater:
            self.start_heater()
        if acquisition:
            self.start_acquisition()
        if drive:
            self.drive_pending.set()
            self.drive.set()

    def heater_allowed(self):
        return not self.heater_link.is_set() or (self.drive.is_set() and self.drive_ready.is_set())

    def start_heater(self):
        with self._lock:
            self.heater_pending.set()
            self.heater.set()

    def start_acquisition(self):
        with self._lock:
            self.acquisition_pending.set()
            self.acquisition.set()

    def start_drive(self, profile):
        with self._lock:
            self._profile = profile
            self.drive_pending.set()
            self.drive.set()

    def take_cancelled(self, part, active):
        with self._lock:
            requested = getattr(self, part)
            pending = getattr(self, part+'_pending')
            if not requested.is_set() and (active or pending.is_set()):
                pending.clear()
                return True
            return False

    def profile(self):
        with self._lock:
            return self._profile


class AcquisitionCancel:
    def __init__(self, requested, stop):
        self.requested, self.stop = requested, stop

    def is_set(self):
        return self.stop.is_set() or not self.requested.is_set()

    def wait(self, seconds):
        deadline = time.monotonic()+seconds
        while not self.is_set():
            remaining = deadline-time.monotonic()
            if remaining <= 0:
                return False
            self.stop.wait(min(.02, remaining))
        return True
