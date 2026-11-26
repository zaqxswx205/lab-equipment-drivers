"""Device-management process and a UI-side IPC relay (no hardware work)."""
import multiprocessing as mp
import os
import queue
import threading
import time

from session.controls import RunControls

FLAGS = ('acquisition', 'drive', 'heater', 'heater_link', 'drive_ready',
         'acquisition_pending', 'drive_pending', 'heater_pending')


class ControlFlag:
    """A remote snapshot cannot overwrite a button operation made after capture."""
    def __init__(self, event):
        self.event, self.generation, self.lock = event, 0, threading.Lock()

    def is_set(self):
        return self.event.is_set()

    def set(self):
        with self.lock:
            self.generation += 1
            self.event.set()

    def clear(self):
        with self.lock:
            self.generation += 1
            self.event.clear()

    def wait(self, timeout=None):
        return self.event.wait(timeout)

    def snapshot(self):
        with self.lock:
            return self.event.is_set(), self.generation

    def apply_remote(self, value, generation):
        with self.lock:
            if self.generation != generation:
                return False
            (self.event.set if value else self.event.clear)()
            return True


def flag_values(controls):
    return {key: getattr(controls, key).is_set() for key in FLAGS}


def manager_process(cfg, initial, commands, messages, stop, simulation, max_cycles, output_dir):
    from performance import PerformanceRun
    from session.supervisor import run as run_devices
    controls = RunControls(profile=initial['profile'])
    controls.drive_source=initial['drive_source']
    for key, value in initial['flags'].items():
        (getattr(controls, key).set if value else getattr(controls, key).clear)()
    controls.worker_options = initial['options']
    if initial['points']:
        controls.performance = PerformanceRun(initial['points'])
    acknowledged = 0
    last_ui = time.monotonic()
    last_state = 0.
    ui_lost = False

    def state():
        performance = getattr(controls, 'performance', None)
        return dict(ack=acknowledged, flags=flag_values(controls), pid=os.getpid(),
                    drive_token=supervisor_ref[0].drive_started_at if supervisor_ref else None,
                    stage=supervisor_ref[0].sequencer.index if supervisor_ref and supervisor_ref[0].sequencer else -1,
                    rollback_pending=bool(supervisor_ref and supervisor_ref[0].rollback.pending),
                    performance=dict(index=performance.index, phase=performance.phase,
                                     ready=performance.ready.is_set()) if performance else None)

    supervisor_ref=[]

    def emit(kind, value):
        if kind == 'done':
            messages.put(('manager_state', state()))
        messages.put((kind, value))

    def tick(supervisor):
        nonlocal acknowledged, last_ui, last_state, ui_lost
        supervisor_ref[:]=[supervisor]
        for _ in range(100):
            try:
                command = commands.get_nowait()
            except queue.Empty:
                break
            if command['kind'] == 'heartbeat':
                last_ui = max(last_ui, command['at'])
            elif command['kind'] == 'control':
                acknowledged = command['seq']
                if not ui_lost:
                    if 'profile' in command:
                        controls._profile = command['profile']
                    controls.drive_source=command.get('drive_source',controls.drive_source)
                    for key, value in command['flags'].items():
                        (getattr(controls, key).set if value else getattr(controls, key).clear)()
            elif command['kind'] == 'next' and not ui_lost:
                performance = getattr(controls, 'performance', None)
                if performance:
                    performance.advance()
            elif command['kind'] == 'rollback' and not ui_lost:
                supervisor.rollback.request(command['index'],command.get('profile'),command.get('token'))
            elif command['kind'] == 'rename_channel' and not ui_lost:
                from session.channel_names import rename_channel
                try:
                    rename_channel(supervisor.cfg, command['channel'], command['name'])
                    supervisor.logger.log(f'CHANNEL_NAME {command["channel"]} {command["name"]!r}')
                except ValueError as exc:
                    emit('channel_name_error', str(exc))
            elif command['kind'] == 'clear_performance':
                performance = getattr(controls, 'performance', None)
                if performance and performance.phase in ('aborted','complete'):
                    performance.close()
                    controls.performance = supervisor.performance = None
                    supervisor.performance_step = supervisor.performance_heater_w = None
        now = time.monotonic()
        if not ui_lost and now-last_ui > cfg.heartbeat_timeout:
            ui_lost = True
            stop.set()
            emit('alarm', '界面心跳失联：锁止新输出，先确认加热OFF，再处理交流；2700继续采集。')
        if now-last_state >= .1:
            snapshot = state()
            snapshot['device_pids'] = {role: client.process.pid for role,client in supervisor.clients.items()
                                       if not client.retired}
            messages.put(('manager_state', snapshot))
            last_state = now

    messages.put(('manager_started', dict(pid=os.getpid())))
    try:
        run_devices(cfg, stop, emit, simulation, max_cycles, output_dir,
                    initial['profile'], controls, before_tick=tick)
    except BaseException as exc:
        # Children retain their own parent-heartbeat fail-safe if management dies.
        messages.put(('manager_failed', '设备管理进程异常：'+str(exc)))
        raise


def run(cfg, stop, emit, simulation=False, max_cycles=None, output_dir=None, profile=None, controls=None):
    """Blocking IPC relay, run on the GUI's background thread, never the Tk thread."""
    controls = controls or RunControls(True, True, profile, heater=cfg.heater_enabled)
    for key in FLAGS:
        if not isinstance(getattr(controls,key), ControlFlag):
            setattr(controls, key, ControlFlag(getattr(controls,key)))
    generations = {key:getattr(controls,key).snapshot()[1] for key in FLAGS}
    performance = getattr(controls, 'performance', None)
    baseline = flag_values(controls)
    current_profile = controls.profile() or profile
    drive_source=controls.drive_source
    initial = dict(flags=baseline, profile=current_profile, drive_source=drive_source,
                   points=tuple(performance.points) if performance else (),
                   options=getattr(controls, 'worker_options', {}))
    ctx = mp.get_context('spawn')
    commands, messages = ctx.Queue(), ctx.Queue()
    child_stop = ctx.Event()
    if stop.is_set():
        child_stop.set()
    process = ctx.Process(target=manager_process,
                          args=(cfg, initial, commands, messages, child_stop, simulation, max_cycles, output_dir),
                          name='device-manager')
    pending = {}
    sequence = 0
    last_sent = 0.
    last_state = time.monotonic()
    notified = False
    finished = False
    try:
        process.start()
    except Exception as exc:
        for channel in (commands, messages):
            channel.cancel_join_thread()
            channel.close()
        emit('done', dict(error='无法启动设备管理进程：'+str(exc), off_ok=True))
        return
    try:
        while True:
            now = time.monotonic()
            for _ in range(20):
                try:
                    commands.put(controls.requests.get_nowait())
                except queue.Empty:
                    break
            if performance and getattr(controls,'performance',None) is None:
                commands.put(dict(kind='clear_performance'))
                performance = None
            captured = {key:getattr(controls,key).snapshot() for key in FLAGS}
            changed = {key:value for key,(value,generation) in captured.items()
                       if baseline[key]!=value or generations[key]!=generation}
            new_profile = controls.profile()
            if changed or new_profile != current_profile or controls.drive_source != drive_source:
                sequence += 1
                drive_source=controls.drive_source
                command = dict(kind='control', seq=sequence, flags=changed,drive_source=drive_source)
                if new_profile != current_profile:
                    command['profile'] = new_profile
                    current_profile = new_profile
                commands.put(command)
                for key,value in changed.items():
                    baseline[key] = value
                    generations[key] = captured[key][1]
                    pending[key] = sequence
            if performance and performance.next_requested.is_set():
                performance.next_requested.clear()
                commands.put(dict(kind='next'))
            if stop.is_set():
                child_stop.set()
            if now-last_sent >= .3:
                commands.put(dict(kind='heartbeat', at=getattr(controls, 'ui_heartbeat', now)))
                last_sent = now
            for _ in range(200):
                try:
                    kind, value = messages.get_nowait()
                except queue.Empty:
                    break
                if kind == 'manager_state':
                    last_state = now
                    controls.drive_epoch_token=value['drive_token']
                    for key, remote in value['flags'].items():
                        # An older snapshot cannot undo a new button operation.
                        if (pending.get(key, 0) <= value['ack'] and
                                getattr(controls,key).apply_remote(remote, captured[key][1])):
                            baseline[key] = remote
                            pending.pop(key, None)
                    if performance and value['performance']:
                        info = value['performance']
                        performance.index, performance.phase = info['index'], info['phase']
                        (performance.ready.set if info['ready'] else performance.ready.clear)()
                if kind == 'done':
                    finished = True
                if kind == 'manager_failed':
                    notified = True
                    child_stop.set()
                emit(kind, value)
            if not process.is_alive():
                # Give the queue feeder a final opportunity to deliver done.
                if finished:
                    break
                if process.exitcode != 0 or now-last_state > 1.:
                    if not notified:
                        emit('manager_failed', '设备管理进程失联：输出状态无法确认；禁止重新开启电源，设备进程执行失联保护。')
                    break
            elif now-last_state > max(10., cfg.heartbeat_timeout) and not notified:
                notified = True
                child_stop.set()
                emit('manager_failed', '设备管理进程响应超时；已请求顺序关断，输出状态未确认。')
            time.sleep(.02)
    finally:
        # Never terminate management while output OFF remains unconfirmed.
        if not process.is_alive():
            process.join(timeout=0)
            process.close()
        for channel in (commands, messages):
            channel.cancel_join_thread()
            channel.close()
