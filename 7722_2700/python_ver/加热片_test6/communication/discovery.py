"""Disposable serial-discovery processes supervised outside the Tk thread."""
import multiprocessing as mp
import threading
import time


def discovery_worker(action, payload, connection):
    try:
        if action == 'ports':
            from serial.tools import list_ports
            value = [(p.device, p.description) for p in list_ports.comports()]
        elif action == 'probe':
            from device_scan import scan_port
            value = (payload['port'], *scan_port(payload['port'], payload['profiles']))
        elif action == 'panel':
            from panel_identification import identify_panel
            identify_panel(payload['record'], payload['mode'], payload['timeout'])
            value = None
        else:
            raise ValueError('未知识别任务：'+action)
        connection.send(dict(ok=True, value=value))
    except Exception as exc:
        connection.send(dict(ok=False, error=str(exc)[:1500],
                             uncertain=not getattr(exc, 'off_confirmed', False)))
    finally:
        connection.close()


def bounded_call(action, payload, timeout, cancel, target=discovery_worker):
    """Only the supervisor thread waits; a stuck driver cannot outlive a task."""
    ctx = mp.get_context('spawn')
    receiver, sender = ctx.Pipe(duplex=False)
    process = ctx.Process(target=target, args=(action, payload, sender), daemon=True,
                          name='discovery-'+action)
    try:
        process.start()
        sender.close()
        deadline = time.monotonic()+timeout
        while not cancel.is_set():
            if receiver.poll(.05):
                try:
                    return receiver.recv()
                except EOFError:
                    break
            if time.monotonic() >= deadline:
                return dict(ok=False, error=f'串口任务超时（{timeout:g}秒），已释放连接，等待设备重新上线', uncertain=True)
            if not process.is_alive():
                break
        if cancel.is_set():
            raise InterruptedError('已取消串口识别')
        return dict(ok=False, error='串口识别子进程退出，等待重试', uncertain=True)
    finally:
        sender.close()
        receiver.close()
        if process.pid is not None:
            if process.is_alive():
                process.terminate()
            process.join(timeout=.5)
            if process.is_alive():
                process.kill()
                process.join(timeout=.5)
            if not process.is_alive():
                process.close()


class DiscoveryJob:
    def __init__(self, kind, emit, profiles=(), record=None, key=None, mode=None,
                 timeout=3., only_ports=None, target=discovery_worker):
        self.target = target
        self.kind, self.emit, self.profiles = kind, emit, profiles
        self.record, self.key, self.mode, self.timeout = record, key, mode, timeout
        self.only_ports = only_ports
        self.cancel_event = threading.Event()
        self.thread = threading.Thread(target=self.run, name='discovery-supervisor', daemon=True)

    def start(self):
        self.thread.start()

    def cancel(self):
        self.cancel_event.set()

    def run(self):
        try:
            if self.kind == 'panel':
                result = bounded_call('panel', dict(record=self.record, mode=self.mode, timeout=self.timeout),
                                      min(20., max(5., self.timeout*4+2)), self.cancel_event, self.target)
                if result['ok']:
                    self.emit('scan_panel_done', (self.key, self.record, self.mode))
                else:
                    self.emit('scan_panel_error', dict(key=self.key, record=self.record, mode=self.mode,
                              error=result['error'], uncertain=result.get('uncertain', True)))
                return
            enumeration = bounded_call('ports', {}, 5., self.cancel_event, self.target)
            if not enumeration['ok']:
                raise RuntimeError(enumeration['error'])
            ports = sorted(set(p[0] for p in enumeration['value']))
            if self.only_ports is not None:
                ports = sorted(set(self.only_ports))
            results = []
            unique = {tuple(sorted(p.items())) for p in self.profiles}
            budget = max(5., len(unique)*3.+2.)
            for port in ports:
                if self.cancel_event.is_set():
                    raise InterruptedError('已取消串口识别')
                self.emit('scan_progress', f'正在识别 {port}（单串口最长{budget:g}秒）…')
                result = bounded_call('probe', dict(port=port, profiles=self.profiles), budget, self.cancel_event, self.target)
                record = result['value'] if result['ok'] else (port, '未识别', result['error'], None)
                results.append(record)
                self.emit('scan_record', record)
            self.emit('scan_done', results)
        except InterruptedError:
            self.emit('scan_cancelled', '')
        except Exception as exc:
            self.emit('scan_error', str(exc))
