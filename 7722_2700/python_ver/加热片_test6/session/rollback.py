"""Nonblocking rollback: heater OFF, drain old drive commands, apply, then clock."""
from performance import PerformanceRun
from profile.controller import DriveController
from session.timing import publish_clock


class Rollback:
    def __init__(self, owner):
        self.owner = owner
        self.pending = None

    def request(self, index, profile=None, token=None):
        s = self.owner
        drive = s.clients.get('drive')
        reason = ''
        if self.pending or s.stop.is_set() or s.exit_requested or s.shutdown.pending:
            reason = '正在回退或关断，请等待处理完成'
        elif not drive or not drive.ready or drive.failed or drive.closing or not s.controls.drive.is_set():
            reason = '交流驱动未正常运行，不能回退'
        elif profile is not None and profile != s.drive_profile or token is not None and token != s.drive_started_at:
            reason = '运行会话已变化，请重新选择阶段'
        elif type(index) is not int or not 0 <= index <= s.sequencer.index:
            reason = '只能回退到已经执行的阶段（可重新执行当前阶段）'
        if reason:
            s.emit('rollback_status', '回退未执行：'+reason)
            return
        if s.performance:
            s.performance.close('rollback')
            s.performance.phase='rewinding'
            s.performance.next_requested.clear()
        s.performance_step = s.performance_heater_w = None
        s.controls.drive_ready.clear()
        s.temperature_trigger_used=True
        drive.request('inhibit')
        barrier = drive.request('barrier')
        self.pending = dict(index=index, barrier=barrier, applied=None)
        s.shutdown.request(drive=False)
        s.emit('rollback_status', f'准备回退到阶段{index+1}：先确认加热OFF，期间交流保持、2700继续采集。')

    def tick(self):
        if not self.pending:
            return
        s = self.owner
        drive = s.clients.get('drive')
        if (s.stop.is_set() or s.exit_requested or not s.controls.drive.is_set() or
                not drive or drive.failed or s.shutdown.drive):
            self.pending=None
            s.abort_performance('回退被停止或设备异常取消')
            s.emit('rollback_status', '回退已取消；按安全关断规则处理。')
            return
        if s.shutdown.pending or 'heater' in s.clients or self.pending['barrier'] in drive.pending:
            return
        step = s.drive_profile.steps[self.pending['index']]
        if self.pending['applied'] is None:
            s.controller=DriveController(s.cfg)
            s.controller.voltage=drive.setting
            drive.request('resume')
            if step.value==0:
                request = drive.request('off')
            else:
                voltage=s.controller.update(step,(drive.measurement or {}).get('power',0),s.cfg.interval,s.now())
                request=drive.request('set',voltage=voltage,enabled=True)
            self.pending['applied']=request
            return
        if self.pending['applied'] in drive.pending:
            return
        now=s.now()
        index=self.pending['index']
        # A pure temperature stage anchors the clock to its preceding time stage.
        anchor=0.
        for item in s.drive_profile.steps[:index+1]:
            if s.drive_profile.trigger_for(item) in ('time','time_or_temperature'):
                anchor=item.at
        s.sequencer.index=index
        s.sequencer.completed_performance_stages={i for i in range(index)
                                                  if s.drive_profile.steps[i].performance_points}
        if s.performance:
            points=s.performance.points
            count=sum(len(item.performance_points) for item in s.drive_profile.steps[:index])
            s.performance=PerformanceRun(points)
            s.performance.index=min(count,len(points)-1)
            s.performance.phase='complete' if count==len(points) else 'cooling'
            s.performance.reset_stability()
            s.controls.performance=s.performance
        s.drive_epoch=now-anchor
        s.current_step=step
        s.last_update=now
        s.controller.outside_since=None
        if drive.output_on:
            s.controls.drive_ready.set()
        drive.request('resume')
        self.pending=None
        text=f'已回退到阶段{index+1}；本阶段重新计时，方案时间从{anchor:g}秒继续，实际经过时间保留。'
        s.logger.log(text)
        s.emit('rollback_status', text)
        publish_clock(s)
