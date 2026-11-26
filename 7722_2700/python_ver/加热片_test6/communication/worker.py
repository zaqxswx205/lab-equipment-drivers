"""Device operations run only on this thread, including idle communication probes."""
import queue
import time
import threading


class DeviceLoop:
    def __init__(self, role, cfg, simulation, events, halt, inhibit, state, options):
        self.role, self.cfg, self.simulation = role, cfg, simulation
        self.events, self.halt, self.inhibit, self.state = events, halt, inhibit, state
        self.options = options
        self.device = None
        self.ready = False
        self.next_sample = self.next_probe = 0.
        self.channels = sorted(cfg.channels, key=lambda c: c.channel != cfg.reference_channel)
        self.index = 0
        self.identity = None
        self.output_on = False
        self.setting = 0.
        self.identity = options.get('expected_identity')
        self.recovery_only = options.get('recover_only', False)

    def check_offline(self):
        from pathlib import Path
        marker = self.options.get('offline_flag')
        if marker and Path(marker).exists():
            raise TimeoutError('测试：设备已断电/离线')

    def mark_disconnected(self):
        self.ready = False
        self.next_probe = time.monotonic()+2.
        if self.role != 'meter':
            self.recovery_only = True
            self.inhibit.set()

    def reconnect(self):
        """Reopen a fresh handle. Power recovery performs queries only, never ON/OFF."""
        import core
        self.check_offline()
        simulated_state = (dict(self.device.tp.state) if self.simulation and self.device and
                           self.role != 'meter' else None)
        if self.device:
            self.device.close()
            self.device = None
        if self.role == 'meter':
            self.initialize()
            self.emit('reconnected', off_confirmed=True)
            return
        self.inhibit.set()
        heater = self.role == 'heater'
        transport = core.SimTransport() if self.simulation else core.SerialTransport(
            self.cfg.heater_port if heater else self.cfg.power_port,
            self.cfg.heater_baud if heater else self.cfg.power_baud, timeout=self.cfg.timeout)
        if simulated_state is not None:
            transport.state.update(simulated_state)
        self.device = core.ITech7722(transport, log=lambda d,t: self.emit('trace', text=self.role+' '+d+' '+t))
        self.device._inhibited = self.inhibit
        actual = self.device.identify().strip()
        model = actual.split(',')
        allowed = ('SIM7722',) if self.simulation else ('IT-M7722','M7722','IT7722','IT-7722')
        if len(model)<2 or model[1].strip().upper() not in allowed:
            raise ValueError('重连设备不是7722')
        if self.identity and actual != self.identity:
            raise ValueError('重连设备身份已变化，拒绝恢复控制；请重新识别设备')
        self.identity = actual
        output = self.device.raw('OUTP:STAT?').strip().upper()
        if output not in ('0', 'OFF', '1', 'ON'):
            raise ValueError('重连后输出状态回读无效：'+output)
        self.output_on = output in ('1', 'ON')
        self.ready = True
        self.emit('reconnected', off_confirmed=not self.output_on, enabled=self.output_on,
                  identity=self.identity)

    def emit(self, kind, **data):
        self.events.put((kind, data))

    def initialize(self):
        import core
        self.check_offline()
        cfg = self.cfg
        if self.role == 'meter':
            if self.device is None:
                self.device = core.SimMeter(cfg, None) if self.simulation else core.Meter(cfg, lambda t: self.emit('trace', text=t))
            self.device.setup()
        else:
            heater = self.role == 'heater'
            transport = core.SimTransport() if self.simulation else core.SerialTransport(
                cfg.heater_port if heater else cfg.power_port,
                cfg.heater_baud if heater else cfg.power_baud, timeout=cfg.timeout)
            self.device = core.ITech7722(transport, log=lambda d,t: self.emit('trace', text=self.role+' '+d+' '+t))
            self.device._inhibited = self.inhibit
            self.identity = self.device.identify().strip()
            model = self.identity.split(',')
            allowed = ('SIM7722',) if self.simulation else ('IT-M7722','M7722','IT7722','IT-7722')
            if len(model)<2 or model[1].strip().upper() not in allowed:
                raise ValueError('电源型号不是7722，拒绝输出')
            self.device.prepare_run()
            self.device.output_off_confirmed(attempts=1, delay=0 if self.simulation else .2)
            self.device.set_function_mode('DC' if heater else 'AC')
            self.device.set_protection_limits(*( (cfg.heater_max_voltage,cfg.heater_max_current,cfg.heater_max_power)
                                                 if heater else (cfg.max_voltage,cfg.max_current,cfg.max_power)))
            self.device.apply_setting('CV',0,0 if heater else cfg.frequency)
        self.ready = True
        self.emit('ready', off_confirmed=True, identity=self.identity)

    def execute(self, op, data):
        if op == 'initialize':
            self.initialize()
        elif op == 'set':
            if self.inhibit.is_set():
                raise RuntimeError('设备已锁止，丢弃旧输出指令')
            self.device.set_voltage(data['voltage'])
            if data['enabled'] and not self.output_on:
                self.device.output_on()
            self.output_on, self.setting = data['enabled'], data['voltage']
            self.emit_measurement()
        elif op == 'barrier':
            pass  # Acknowledge only after every earlier I/O operation has finished.
        elif op == 'test_release_off' and self.simulation:
            self.options['off_unconfirmed'] = False
        elif op == 'off':
            if not self.ready:
                self.reconnect()
            if self.options.get('off_unconfirmed'):
                raise TimeoutError('测试：OFF回读无法确认')
            self.device.output_off_confirmed(attempts=1, delay=0 if self.simulation else .2)
            self.output_on, self.setting = False, 0.
        elif op == 'close':
            if self.role != 'meter' and self.output_on:
                raise RuntimeError('输出未确认OFF，拒绝释放设备进程')
            if self.device:
                self.device.close()
            self.halt.set()
        else:
            raise ValueError('未知设备操作：'+op)

    def emit_measurement(self):
        self.check_offline()
        if self.options.get('communication_fault'):
            raise TimeoutError('测试：设备无响应')
        if self.options.get('block_io'):
            time.sleep(float(self.options['block_io']))
        values = self.device.measure('DC' if self.role == 'heater' else 'AC')
        self.emit('measurement', values=values, enabled=self.output_on, setting=self.setting, at=time.monotonic())

    def sample(self):
        self.check_offline()
        from datetime import datetime
        channel = self.channels[self.index]
        if self.options.get('block_io'):
            time.sleep(float(self.options['block_io']))
        if self.options.get('communication_fault'):
            raise TimeoutError('测试：2700无响应')
        resistance, temperature = self.device.read(channel, self.halt)
        if 'temperature_c' in self.options:
            from model.pt100 import resistance as to_resistance
            temperature = self.options['temperature_c']
            resistance = to_resistance(temperature, channel.sensor_type, channel.sensor_curve)+channel.lead_ohm
        self.emit('sample', channel=channel.channel, resistance=resistance, temperature=temperature,
                  stamp=datetime.now(), at=time.monotonic(), last=self.index==len(self.channels)-1)
        self.index = (self.index+1)%len(self.channels)

    def run(self, jobs):
        self.state.update(started=time.monotonic(), operation='initialize')
        try:
            self.reconnect() if self.recovery_only else self.initialize()
        except Exception as exc:
            self.mark_disconnected()
            self.emit('fault', communication=isinstance(exc, (OSError, TimeoutError)), message=str(exc))
        finally:
            self.state['started'] = None
        while not self.halt.is_set():
            job = None
            try:
                job = jobs.get(timeout=.02)
                op = job['op']
                if op == 'set' and time.monotonic()>job['expires']:
                    raise RuntimeError('输出指令已过期，拒绝执行')
                self.state.update(started=time.monotonic(), operation=op, timed_out=False)
                self.execute(op, job['data'])
                self.emit('reply', id=job['id'], op=op, ok=True)
            except queue.Empty:
                now = time.monotonic()
                try:
                    if not self.ready:
                        if now >= self.next_probe:
                            self.next_probe = now+2.
                            self.state.update(started=now, operation='initialize', timed_out=False)
                            self.reconnect()
                        continue
                    self.state.update(started=now, operation='sample' if self.role=='meter' else 'heartbeat', timed_out=False)
                    if self.role == 'meter' and now>=self.next_sample:
                        self.next_sample = now+self.cfg.interval
                        self.sample()
                    elif self.role == 'meter' and now>=getattr(self,'next_identity',0):
                        if not self.simulation:
                            identity=self.device.query('*IDN?').split(',')
                            if len(identity)<2 or identity[1].strip().upper() not in ('2700','MODEL 2700'):
                                raise RuntimeError('2700身份回读无效')
                        self.next_identity=now+self.cfg.heartbeat_interval
                    elif self.role != 'meter' and now>=self.next_probe:
                        self.next_probe = now+max(.1,self.cfg.interval)
                        if now>=getattr(self, 'next_identity', 0):
                            if self.device.identify().strip()!=self.identity:
                                raise RuntimeError('设备身份回读发生变化')
                            self.next_identity = now+self.cfg.heartbeat_interval
                        self.emit_measurement()
                except Exception as exc:
                    self.mark_disconnected()
                    self.emit('fault', communication=isinstance(exc,(OSError,TimeoutError)),message=str(exc))
                    self.next_sample = self.next_probe = time.monotonic()+1.
                finally:
                    self.state['started'] = None
                continue
            except Exception as exc:
                if isinstance(exc, (OSError, TimeoutError, ValueError)):
                    self.mark_disconnected()
                self.emit('reply', id=job['id'], op=job['op'], ok=False,
                          communication=isinstance(exc,(OSError,TimeoutError)), message=str(exc))
            finally:
                self.state['started'] = None
