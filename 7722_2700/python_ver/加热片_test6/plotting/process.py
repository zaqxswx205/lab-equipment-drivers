"""Small, bounded display channel. Never blocks acquisition or device control."""
import multiprocessing as mp
import queue
import time


class PlotProcess:
    def __init__(self):
        from plotting.window import run_window
        ctx = mp.get_context('spawn')
        self.snapshots = ctx.Queue(maxsize=1)
        self.commands = ctx.Queue(maxsize=8)
        self.requests = ctx.Queue(maxsize=16)
        self.process = ctx.Process(target=run_window,
            args=(self.snapshots, self.commands, self.requests), name='realtime-curves', daemon=True)
        self.closed = False
        self.last_sent = 0.
        try:
            self.process.start()
        except Exception:
            for channel in (self.snapshots,self.commands,self.requests):
                channel.cancel_join_thread()
                channel.close()
            raise

    @property
    def alive(self):
        return not self.closed and self.process.is_alive()

    def command(self, name):
        try:
            self.commands.put_nowait(name)
        except queue.Full:
            pass

    def publish(self, snapshot):
        if not self.alive:
            return False
        self.last_sent = time.monotonic()
        try:
            self.snapshots.put_nowait(snapshot)
        except queue.Full:
            # The next refresh sends the newest complete snapshot. No growing
            # backlog and no impact on the separate, lossless CSV recording.
            return False
        self.last_sent = time.monotonic()
        return True

    def poll(self):
        result = []
        while True:
            try:
                event = self.requests.get_nowait()
            except queue.Empty:
                break
            result.append(event)
            if event[0] == 'closed':
                self.closed = True
        return result

    def close(self):
        self.command('close')
        self.process.join(timeout=.4)
        if self.process.is_alive():
            # This process has display data only: never terminate a device here.
            self.process.terminate()
            self.process.join(timeout=.4)
        self.closed = True
        for channel in (self.snapshots, self.commands, self.requests):
            channel.cancel_join_thread()
            channel.close()
