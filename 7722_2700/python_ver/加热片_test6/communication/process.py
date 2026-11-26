"""One serial owner per device process; its main thread supervises the I/O thread."""
import multiprocessing as mp
import queue
import threading
import time


def device_process(role, cfg, simulation, commands, events, options):
    from communication.worker import DeviceLoop
    jobs = queue.Queue(maxsize=8)
    halt, inhibit = threading.Event(), threading.Event()
    state = {'started': None, 'operation': '', 'timed_out': False}
    loop = DeviceLoop(role, cfg, simulation, events, halt, inhibit, state, options)
    worker = threading.Thread(target=loop.run, args=(jobs,), name=role+'-device', daemon=True)
    worker.start()
    last_heartbeat = 0.
    last_parent = time.monotonic()
    parent_failed = False
    parent_off_retry = 0.
    while not halt.is_set():
        now = time.monotonic()
        if now-last_heartbeat >= .5:
            events.put(('process_heartbeat', {'at': now}))
            last_heartbeat = now
        if now-last_parent > cfg.heartbeat_timeout and not parent_failed:
            parent_failed = True
            if role != 'meter':
                inhibit.set()
            events.put(('fault', dict(communication=True, message='主控心跳失联；保持交流状态并保留2700采集')))
        if parent_failed and role == 'heater' and now >= parent_off_retry:
            parent_off_retry = now+1.
            try:
                jobs.put_nowait(dict(id=0,op='off',data={},expires=now+30.))
            except queue.Full:
                pass
        started = state['started']
        limit = (max(10., min(30., cfg.timeout*8)) if state['operation'] == 'initialize' else
                 max(cfg.heartbeat_timeout, cfg.timeout*8+cfg.settle))
        if started is not None and now-started > limit and not state['timed_out']:
            state['timed_out'] = True
            inhibit.set()
            events.put(('fault', {'communication': True, 'restart': True,
                                 'message': role+'通信调用超时：'+state['operation']}))
        try:
            job = commands.get(timeout=.05)
        except queue.Empty:
            continue
        if job['op'] == 'parent_heartbeat':
            last_parent = now
            continue
        if job['op'] == 'inhibit':
            inhibit.set()
            continue
        # OFF invalidates queued output commands before the I/O thread can resume.
        if job['op'] in ('off', 'close'):
            inhibit.set()
        if job['op'] == 'resume':
            if not parent_failed:
                inhibit.clear()
            continue
        try:
            jobs.put_nowait(job)
        except queue.Full:
            events.put(('reply', {'id': job['id'], 'op': job['op'], 'ok': False,
                                  'communication': True, 'message': '设备请求队列已满'}))
    worker.join(timeout=.2)


class DeviceProcess:
    def __init__(self, role, cfg, simulation=False, options=None):
        self.cfg, self.simulation, self.options = cfg, simulation, dict(options or {})
        self.role = role
        self.heartbeat_timeout = cfg.heartbeat_timeout
        self.identity = None
        self._spawn()
        self.serial = 0
        self.pending = {}
        self.ready = False
        self.off_confirmed = False
        self.closing = False
        self.failed = False
        self.measurement = None
        self.output_on = False
        self.setting = 0.
        self.last_process_heartbeat = time.monotonic()
        self.started_at = time.monotonic()
        self.dead_reported = False
        self.last_parent_sent = 0.

    def _spawn(self):
        self.retired = False
        ctx = mp.get_context('spawn')
        self.commands, self.events = ctx.Queue(), ctx.Queue()
        self.process = ctx.Process(target=device_process,
            args=(self.role, self.cfg, self.simulation, self.commands, self.events, self.options), name=self.role)
        self.process.start()

    def _retire(self):
        if self.process.is_alive():
            self.process.terminate()
        self.process.join(timeout=.2)
        if self.process.is_alive():
            self.process.kill()
            self.process.join(timeout=.2)
        if self.process.is_alive():
            return False
        for channel in (self.commands, self.events):
            channel.cancel_join_thread()
            channel.close()
        self.process.close()
        self.retired = True
        return True

    def recover_process(self):
        if not self._retire():
            return False
        self.options.update(recover_only=True, expected_identity=self.identity)
        self.pending.clear()
        self.ready, self.failed, self.off_confirmed = False, True, False
        self.closing = False
        self._spawn()
        self.last_process_heartbeat = self.started_at = time.monotonic()
        self.last_parent_sent = 0.
        self.dead_reported = False
        return True

    def request(self, op, **data):
        if op == 'set' and (self.closing or self.failed):
            return None
        if op in self.pending.values():
            return None
        self.serial += 1
        if op not in ('inhibit', 'resume'):
            self.pending[self.serial] = op
        if op == 'set' and data.get('enabled'):
            self.off_confirmed = False
        self.commands.put(dict(id=self.serial, op=op, data=data, expires=time.monotonic()+30.))
        return self.serial

    def poll(self):
        if self.retired:
            return []
        now = time.monotonic()
        if now-self.last_parent_sent >= .5 and self.process.is_alive():
            self.commands.put(dict(id=0,op='parent_heartbeat',data={}))
            self.last_parent_sent = now
        result = []
        for _ in range(200):
            try:
                kind, data = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == 'reply':
                self.pending.pop(data['id'], None)
            if kind == 'process_heartbeat':
                self.last_process_heartbeat = time.monotonic()
            if kind in ('ready', 'reconnected') and data.get('identity'):
                self.identity = data['identity']
            if kind == 'fault' and data.get('restart'):
                if self.closing and self.off_confirmed:
                    # OFF already acknowledged: a blocked handle close may be reaped.
                    self._retire()
                    return result
                result.append((kind, data))
                self.recover_process()
                return result
            result.append((kind, data))
        now = time.monotonic()
        if (not self.process.is_alive() or now-self.last_process_heartbeat > self.heartbeat_timeout) and not self.closing and not self.dead_reported:
            self.dead_reported = True
            self.failed = True
            result.append(('fault', dict(communication=True, message=self.role+'进程失联；设备输出状态无法确认')))
            self.recover_process()
        return result

    def reap(self):
        if self.retired:
            return True
        if self.process.is_alive():
            return False
        self.process.join(timeout=0)
        for channel in (self.commands, self.events):
            channel.cancel_join_thread()
            channel.close()
        return True
