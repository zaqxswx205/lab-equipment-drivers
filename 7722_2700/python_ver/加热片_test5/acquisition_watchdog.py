"""独立线程监督采集进度，不依赖阻塞的2700调用自行超时。"""
import threading
import time


class AcquisitionWatchdog:
    def __init__(self, on_fault):
        self.on_fault = on_fault
        self.lock = threading.Lock()
        self.deadline = None
        self.phase = ''
        self.reason = ''
        self.finished = threading.Event()
        self.thread = threading.Thread(target=self._run, name='2700-safety-watchdog', daemon=False)
        self.thread.start()

    def expect(self, seconds, phase):
        with self.lock:
            if not self.reason:
                self.deadline = time.monotonic()+seconds
                self.phase = phase

    def disarm(self):
        with self.lock:
            self.deadline = None

    def trip(self, reason):
        with self.lock:
            if not self.reason:
                self.reason = reason

    def _run(self):
        while True:
            with self.lock:
                if not self.reason and self.deadline is not None and time.monotonic() >= self.deadline:
                    self.reason = f'2700采集无响应：{self.phase}超过允许时间，测温保护失效'
                reason = self.reason
            if reason:
                self.on_fault(reason)
                return
            if self.finished.wait(.05):
                # close与trip同时发生时仍必须处理已锁存的故障。
                with self.lock:
                    reason = self.reason
                if reason:
                    self.on_fault(reason)
                return

    def close(self):
        self.finished.set()
        self.thread.join()
