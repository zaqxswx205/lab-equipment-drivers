"""Drive timing snapshots and monotonic UI extrapolation."""
import time


def publish_clock(session, running=True):
    if getattr(session, 'drive_started_at', None) is None:
        return
    now = time.monotonic()
    paused_at = session.performance.paused_at if session.performance else None
    session.emit('drive_clock', dict(observed_at=now, running=running,
        elapsed_s=max(0., now-session.drive_started_at),
        scheme_s=max(0., (paused_at if paused_at is not None else now)-session.drive_epoch),
        paused=paused_at is not None))


def duration(seconds):
    total=max(0,int(seconds))
    hours, remainder=divmod(total,3600)
    minutes, seconds=divmod(remainder,60)
    return f'{hours:02d}:{minutes:02d}:{seconds:02d}'


class DriveClock:
    def __init__(self):
        self.snapshot=None

    def update(self, snapshot):
        self.snapshot=dict(snapshot)

    def text(self, now=None):
        if self.snapshot is None:
            return ''
        value=self.snapshot
        delta=max(0.,(time.monotonic() if now is None else now)-value['observed_at']) if value['running'] else 0.
        actual=value['elapsed_s']+delta
        scheme=value['scheme_s']+(delta if not value['paused'] else 0.)
        suffix='（性能测试中，方案计时暂停）' if value['running'] and value['paused'] else '（已停止）' if not value['running'] else ''
        return f'方案运行时间 {duration(scheme)}；实际经过 {duration(actual)}'+suffix
