"""Main-process experiment supervisor. All hardware I/O lives in child processes."""
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path
import json
import time

from communication.process import DeviceProcess
from csv_recording import CsvRecording
from model.config_io import profile_path, recording_path
from model.measurements import check_measurements
from profile.model import load_profile, Step
from profile.controller import DriveController
from profile.sequencer import Sequencer
from session.controls import RunControls
from session.records import Logger
from session.shutdown import Shutdown
from session.rollback import Rollback
from session.publishing import publish
from session.timing import publish_clock


class Supervisor:
    asynchronous = True
    def __init__(self, cfg, stop, emit, simulation=False, max_cycles=None, output_dir=None, profile=None, controls=None, before_tick=None):
        self.before_tick = before_tick
        self.cfg,self.stop,self.emit,self.simulation = cfg,stop,emit,simulation
        self.controls = controls or RunControls(True,True,profile,heater=cfg.heater_enabled)
        self.profile,self.max_cycles = profile,max_cycles
        self.output_dir = Path(output_dir) if output_dir is not None else recording_path(cfg)
        self.output_dir.mkdir(parents=True,exist_ok=True)
        self.logger = Logger()
        prefix=('SIM_' if simulation else '')+datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        self.logger.open(self.output_dir/(prefix+'_communication.txt'))
        self.logger.log('CONFIG '+json.dumps(asdict(cfg),ensure_ascii=False))
        self.log_file=CsvRecording(self.output_dir,simulation,cfg.channels)
        self.clients={}
        self.shutdown=Shutdown(self)
        self.rollback=Rollback(self)
        self.devices=self  # PerformanceRun uses a small device lifecycle interface.
        self.performance=getattr(self.controls,'performance',None)
        self.performance_step=self.performance_heater_w=None
        self.drive_profile=None
        self.controller=self.sequencer=self.heater_controller=None
        self.current_step=None
        self.enabled=self.heater_enabled=False
        self.active_heater=False
        self.epoch=time.monotonic()
        self.drive_epoch=self.drive_started_at=None
        self.last_update=0.
        self.samples={}
        self.measurement_errors={}
        self.last_round=None
        self.cycles=0
        self.error=''
        self.off_ok=True
        self.exit_requested=False
        self.temperature_trigger_used=False
        self.faults={}
        self.announced={}
        self.last_clock=0.
        self.heater_cfg=replace(cfg,max_voltage=cfg.heater_max_voltage,max_current=cfg.heater_max_current,max_power=cfg.heater_max_power)

    @staticmethod
    def now():
        return time.monotonic()

    def stopped(self):
        if self.exit_requested:
            raise InterruptedError('已请求停止')

    def close_heater(self):
        if 'heater' not in self.clients:
            return True
        self.shutdown.request(drive=False)
        return False

    def fault(self,role,message,communication=False):
        text=role+'异常：'+message
        if self.faults.get(role)==text:
            return
        self.faults[role]=text
        self.error=text
        self.logger.log(text)
        self.emit('alarm',text)
        self.abort_performance(text)
        if role=='meter':
            self.emit('measurement_fault',text)
            self.shutdown.request(drive=True)
            self.temperature_trigger_used=True
        elif role=='drive' or not communication:
            self.shutdown.request(drive=True)
        # Heater communication loss only reports and freezes further changes.
        # AC and meter remain active until temperature protection requests a stop.
        if role in self.clients and role!='meter':
            self.clients[role].failed=True
            self.clients[role].ready=False
            self.clients[role].off_confirmed=False
            self.clients[role].request('inhibit')
        if self.performance:
            self.performance.reset_stability()
            self.performance.ready.clear()

    def abort_performance(self, reason):
        if not self.performance or self.performance.phase in ('aborted','complete'):
            return
        self.performance.phase='aborted'
        self.performance.ready.clear()
        self.performance.next_requested.clear()
        self.performance.close('aborted')
        self.performance_step=self.performance_heater_w=None
        self.temperature_trigger_used=True
        self.shutdown.request(drive=True)
        self.emit('performance_aborted', '测试已终止：'+reason+'；2700继续采集，电源关闭确认后可重新手动驱动。')

    def create(self,role):
        options=getattr(self.controls,'worker_options',{}).get(role,{})
        port = {'drive': self.cfg.power_port, 'heater': self.cfg.heater_port, 'meter': self.cfg.meter_port}[role].strip().upper()
        for other in self.clients:
            other_port = {'drive': self.cfg.power_port, 'heater': self.cfg.heater_port, 'meter': self.cfg.meter_port}[other].strip().upper()
            if other_port == port:
                raise ValueError('正在运行或请求启动的设备不能使用同一串口')
        self.clients[role]=DeviceProcess(role,self.cfg,self.simulation,options)
        self.logger.log('PROCESS '+role+' pid='+str(self.clients[role].process.pid))
        self.emit('status',{'drive':'正在连接交流7722…','heater':'正在连接加热7722…','meter':'正在连接2700…'}[role])

    def events(self):
        complete_round=None
        for role,client in list(self.clients.items()):
            for kind,data in client.poll():
                if kind=='trace':
                    self.logger.log(data['text'])
                elif kind=='ready':
                    client.ready=True
                    client.off_confirmed=True
                    if role=='drive':
                        self.drive_started_at=self.drive_epoch=self.now()
                        self.last_update=self.now()-self.cfg.interval
                        publish_clock(self)
                    elif role=='meter':
                        self.log_file.start(datetime.now())
                        self.emit('status','2700采集已开始（独立设备进程）')
                    else:
                        self.active_heater=True
                        self.emit('heater_starting','加热设备已连接，等待完整有效温度采样。')
                elif kind=='reconnected':
                    client.ready=True
                    client.off_confirmed=data['off_confirmed']
                    client.output_on=data.get('enabled', False)
                    previous=self.faults.pop(role,None)
                    if self.error==previous and not self.faults:
                        self.error=''
                    text={'drive':'交流7722','heater':'加热7722','meter':'2700'}[role]+'通信已恢复；不会自动开启电源输出。'
                    self.logger.log(text)
                    self.emit('connection_recovered', dict(role=role, text=text, previous=previous))
                    if role=='heater':
                        self.shutdown.request(drive=False)
                elif kind=='measurement':
                    try:
                        check_measurements(data['values'],self.heater_cfg if role=='heater' else self.cfg,
                                           mode='DC' if role=='heater' else 'AC')
                    except Exception as exc:
                        self.fault(role,str(exc))
                        continue
                    client.measurement=data['values']
                    client.output_on=data['enabled']
                    client.setting=data['setting']
                    if role=='drive':
                        self.enabled=data['enabled']
                        if self.enabled and self.controls.drive.is_set() and not self.shutdown.drive and not self.rollback.pending:
                            self.controls.drive_ready.set()
                    else:
                        self.heater_enabled=data['enabled']
                        target=self.performance_heater_w if self.performance_heater_w is not None else (self.cfg.heater_voltage if self.cfg.heater_control=='voltage' else self.cfg.heater_power)
                        key='voltage' if self.cfg.heater_control=='voltage' else 'power'
                        tolerance=max(.01,target*.01) if key=='voltage' else max(.02,target*self.cfg.power_tolerance_rel)
                        self.emit('heater_sample',dict(data['values'],control=self.cfg.heater_control,target=target,reached=abs(data['values'][key]-target)<=tolerance))
                        self.logger.log('HEATER '+json.dumps(data['values']))
                elif kind=='sample':
                    c=next(c for c in self.cfg.channels if c.channel==data['channel'])
                    t=data['temperature']
                    self.samples[c.channel]=(c,data['resistance'],t,data['stamp'],data['at'])
                    reason=('电阻无效/超出传感器换算范围' if t is None else
                            '温度越限' if not c.min_temperature<=t<c.max_temperature else '')
                    if reason:
                        self.measurement_errors[c.channel]=reason
                        self.temperature_trigger_used=True
                        self.shutdown.request(drive=True)
                        self.emit('alarm',f'通道{c.channel} {reason}；先确认加热OFF，再关闭交流；2700继续采集。')
                    else:
                        self.measurement_errors.pop(c.channel,None)
                    if data['last']:
                        complete_round=[self.samples[c.channel] for c in sorted(self.cfg.channels,key=lambda c:c.channel!=self.cfg.reference_channel) if c.channel in self.samples]
                        self.last_round=complete_round
                elif kind=='fault':
                    self.fault(role,data['message'],data.get('communication',False))
                elif kind=='reply':
                    if data['ok'] and data['op']=='off':
                        client.off_confirmed=True
                        client.output_on=False
                        if role=='heater':self.heater_enabled=False
                        else:self.enabled=False
                        self.logger.log(role+' OFF_CONFIRMED')
                    elif not data['ok']:
                        if data['op']=='off':
                            client.off_confirmed=False
                            self.shutdown.notice(role+'关断未确认：'+data['message']+'；2700继续采集。')
                        elif data['op']=='close':
                            client.closing=False
                            self.emit('alarm',role+'设备清理失败：'+data['message'])
                        elif (data['op']=='set' and self.rollback.pending and
                              data['id'] < self.rollback.pending['barrier']):
                            self.logger.log('回退：已丢弃旧阶段的调压请求')
                        elif data['op']!='set' or not self.shutdown.pending:
                            self.fault(role,data['message'],data.get('communication',False))
            if client.closing and client.reap():
                del self.clients[role]
                self.faults.pop(role,None)
                if role=='meter':
                    self.emit('acquisition_done',{'error':''})
                    self.controls.acquisition_pending.clear()
                    self.samples.clear()
                else:
                    self.emit(role+'_done',{'error':'','off_ok':True})
                    getattr(self.controls,role+'_pending').clear()
                    if role=='heater':
                        self.active_heater=self.heater_enabled=False
                        self.heater_controller=None
                    else:
                        publish_clock(self,running=False)
                        self.controls.drive_ready.clear()
        return complete_round

    def requests(self):
        c=self.controls
        import queue
        try:
            command=c.requests.get_nowait()
        except queue.Empty:
            command=None
        if command:
            self.rollback.request(command['index'],command.get('profile'),command.get('token'))
        if not c.drive.is_set() or not c.acquisition.is_set():
            self.abort_performance('驱动或采集停止请求')
        if self.stop.is_set() or self.max_cycles is not None and self.cycles>=self.max_cycles:
            if not self.exit_requested:
                self.exit_requested=True
                self.shutdown.request(drive=True)
                c.drive.clear();c.heater.clear()
        if self.exit_requested:
            if not self.shutdown.pending and not any(k in self.clients for k in ('heater','drive')):
                c.acquisition.clear()
        elif c.heater.is_set() and not c.acquisition.is_set():
            c.start_acquisition()
            self.emit('acquisition_starting','加热前自动启动2700采集。')
        for role,event in (('meter',c.acquisition),('heater',c.heater),('drive',c.drive)):
            client=self.clients.get(role)
            if event.is_set() and client is None and not self.exit_requested:
                if role=='heater' and self.rollback.pending:
                    c.heater.clear()
                    c.heater_pending.clear()
                    self.emit('status','回退处理中，请等待阶段切换完成后再启动加热。')
                    continue
                if role in ('heater','drive') and (self.shutdown.pending or any(v.closing for v in self.clients.values())):
                    continue
                if role=='heater' and not c.heater_allowed():continue
                if role=='drive':
                    heater = self.clients.get('heater')
                    if heater and not heater.off_confirmed:
                        if heater.ready:
                            self.shutdown.request()
                        continue
                    profile=c.profile() or self.profile or load_profile(profile_path(self.cfg),self.cfg)
                    profile.validate(self.cfg)
                    if profile.requires_temperature and not c.acquisition.is_set():raise ValueError('温度驱动需要先启动2700采集')
                    if profile.requires_performance and not self.performance:raise ValueError('缺少测试阶段参数')
                    self.drive_profile=profile
                    self.sequencer=Sequencer(profile)
                    self.controller=DriveController(self.cfg)
                    self.logger.log('PROFILE '+json.dumps(asdict(profile),ensure_ascii=False))
                if role=='heater':self.heater_controller=DriveController(self.heater_cfg)
                self.create(role)
            elif client and not event.is_set() and not client.closing:
                if role=='drive':self.shutdown.request(drive=True)
                elif role=='heater':self.shutdown.request(drive=False)
                elif self.shutdown.pending or 'heater' in self.clients:
                    c.acquisition.set()
                elif role=='meter':
                    if 'drive' in self.clients and self.drive_profile.requires_temperature:
                        self.shutdown.request(drive=True)
                        c.acquisition.set()
                    else:
                        client.closing=True
                        client.request('close')

    def update_outputs(self,round_samples):
        if self.rollback.pending:
            return
        now=self.now()
        if now-self.last_update<self.cfg.interval and not round_samples:return
        dt=min(self.cfg.interval,max(0.,now-self.last_update))
        self.last_update=now
        samples=round_samples or self.last_round or []
        valid=len(samples)==len(self.cfg.channels) and not self.measurement_errors and all(t is not None for c,r,t,stamp,at in samples)
        if round_samples and self.performance and not self.faults and not self.shutdown.pending:
            try:self.performance.update(self,round_samples,self.measurement_errors)
            except Exception as exc:
                self.fault('drive',str(exc))
        drive=self.clients.get('drive')
        if drive and drive.ready and not drive.closing and not drive.failed and self.controls.drive.is_set() and not self.shutdown.drive:
            reference=samples[0][2] if valid else None
            if not self.drive_profile.requires_temperature or reference is not None:
                step=self.performance_step or self.sequencer.select(now-self.drive_epoch,reference)
                self.current_step=step
                if step.value==0:
                    self.controller.update(step,0.,dt,now)
                    if not drive.off_confirmed:
                        self.shutdown.request(drive=True,close_drive=False)
                elif drive.measurement and not self.shutdown.pending:
                    try:
                        voltage=self.controller.update(step,drive.measurement['power'],dt,now)
                        if not drive.output_on or abs(voltage-drive.setting)>1e-8:
                            drive.request('set',voltage=voltage,enabled=True)
                    except Exception as exc:self.fault('drive',str(exc))
        if self.cfg.heater_temperature_enabled and valid and not self.temperature_trigger_used:
            if samples[0][2]<=self.cfg.heater_trigger_temperature:
                if self.cfg.heater_trigger_action=='off':
                    self.temperature_trigger_used=True
                    self.shutdown.request()
                elif self.controls.heater_allowed():
                    self.temperature_trigger_used=True
                    self.controls.start_heater()
                    self.emit('heater_starting','参考温度达到阈值，准备加热。')
        heater=self.clients.get('heater')
        if heater and self.controls.heater.is_set() and not self.controls.heater_allowed():
            self.shutdown.request()
        if heater and heater.ready and not heater.failed and not heater.closing and self.controls.heater.is_set() and valid and not self.shutdown.pending and (not self.controls.drive.is_set() or drive and drive.ready):
            target=self.performance_heater_w if self.performance_heater_w is not None else (self.cfg.heater_voltage if self.cfg.heater_control=='voltage' else self.cfg.heater_power)
            if target==0:
                if not heater.off_confirmed:heater.request('off')
            elif heater.measurement:
                try:
                    voltage=self.heater_controller.update(Step(0,self.cfg.heater_control,target),heater.measurement['power'] if heater.output_on else 0.,dt,now)
                    if not heater.output_on or abs(voltage-heater.setting)>1e-8:
                        heater.request('set',voltage=voltage,enabled=True)
                except Exception as exc:self.fault('heater',str(exc))
        if round_samples and self.performance and not self.faults and not self.shutdown.pending:
            self.performance.record(self,round_samples,drive.measurement if drive else None,heater.measurement if heater else None)

    def run(self):
        while True:
            try:
                if self.before_tick:
                    self.before_tick(self)
                samples=self.events()
                self.requests()
                self.shutdown.tick()
                self.rollback.tick()
                self.update_outputs(samples)
                if samples is not None:publish(self,samples)
                elif not self.controls.acquisition.is_set() and self.now()-getattr(self,'last_publish',0)>=self.cfg.interval:
                    publish(self,[]);self.last_publish=self.now()
                if self.drive_started_at and self.now()-self.last_clock>=1.:
                    publish_clock(self,running='drive' in self.clients)
                    self.last_clock=self.now()
                if not self.clients and not any(e.is_set() for e in (self.controls.drive,self.controls.heater,self.controls.acquisition)):
                    break
            except Exception as exc:
                self.fault('drive',str(exc))
            time.sleep(.02)
        if self.performance:self.performance.close()
        self.log_file.close()
        self.logger.close()
        self.emit('done',{'error':self.error,'off_ok':True})


def run(cfg,stop,emit,simulation=False,max_cycles=None,output_dir=None,profile=None,controls=None,before_tick=None):
    try:
        cfg.validate()
        ports=[cfg.power_port.upper() if controls is None or controls.drive.is_set() else None,
               cfg.heater_port.upper() if (controls.heater.is_set() if controls else cfg.heater_enabled) else None,
               cfg.meter_port.upper() if controls is None or controls.acquisition.is_set() or controls.heater.is_set() else None]
        used=[p.strip() for p in ports if p]
        if len(set(used))!=len(used):raise ValueError('正在运行或请求启动的设备不能使用同一串口')
        requested = controls or RunControls(True, True, profile, heater=cfg.heater_enabled)
        if requested.drive.is_set():
            candidate=requested.profile() or profile or load_profile(profile_path(cfg),cfg)
            candidate.validate(cfg)
            if candidate.requires_performance and not getattr(requested,'performance',None):
                raise ValueError('驱动方案含测试阶段，请提供本方案中的加热和判稳参数')
            if candidate.requires_temperature and not requested.acquisition.is_set():
                raise ValueError('温度驱动方案需要先启动2700采集')
        if controls and getattr(controls,'performance',None):
            p=controls.profile() or profile or load_profile(profile_path(cfg),cfg)
            controls.performance.validate_profile(p)
    except Exception as exc:
        emit('alarm',str(exc))
        emit('done',{'error':str(exc),'off_ok':True})
        return
    supervisor = Supervisor(cfg,stop,emit,simulation,max_cycles,output_dir,profile,controls,before_tick)
    supervisor.run()
