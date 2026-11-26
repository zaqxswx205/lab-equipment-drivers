"""Main-window bridge to the read-only curve process; no Canvas work here."""
import time
from plotting.process import PlotProcess


class PlotMixin:
    def refresh_plot_channels(self):
        self.draw()

    def draw(self):
        self._plot_dirty=True

    def plot_snapshot(self):
        heater_pending=self.heater_busy or self.controls.heater.is_set() or self.controls.heater_pending.is_set()
        return dict(session_id=self.plot_session_id,running=not self.run_finished,
            acquisition_running=self.controls.acquisition.is_set(),
            channels=sorted(int(ch) for ch in self.table.get_children()),
            channel_names={int(ch):self.channel_names.get(int(ch),'') for ch in self.table.get_children()},
            history={ch:list(points) for ch,points in self.history.items()},
            power=list(self.power_history),status=self.system_text.get(),
            alert=bool(getattr(getattr(self,'status_center',None),'alert',False)),
            stats=self.performance_stats.get(),
            stop_drive_allowed=not self.closing and self.drive_busy and self.controls.drive.is_set(),
            stop_acquisition_allowed=not self.closing and self.acquisition_busy and self.controls.acquisition.is_set() and not heater_pending)

    def open_curves(self):
        if self.closing:return
        if self.plot_process and self.plot_process.alive:
            self.plot_process.command('focus')
            return
        if self.plot_process:
            self.plot_process.close()
            self.plot_process=None
        try:
            self.plot_process=PlotProcess()
            self.plot_process.publish(self.plot_snapshot())
        except Exception as exc:
            self.report_status('曲线窗口启动失败：'+str(exc),'error',source='curves')

    def poll_curves(self):
        viewer=self.plot_process
        if not viewer:return
        for kind,session_id in viewer.poll():
            if session_id!=self.plot_session_id or self.closing:continue
            # A delayed command from an earlier run must never stop a new run.
            if kind=='stop_drive' and self.drive_busy and self.controls.drive.is_set():
                self.stop_drive()
            elif (kind=='stop_acquisition' and self.acquisition_busy and self.controls.acquisition.is_set()
                  and not (self.heater_busy or self.controls.heater.is_set() or self.controls.heater_pending.is_set())):
                self.stop_acquisition()
        if not viewer.alive:
            if not viewer.closed:
                self.report_status('曲线窗口已退出，可重新打开；设备运行和记录继续。',source='curves')
            viewer.close()
            self.plot_process=None
            return
        if time.monotonic()-viewer.last_sent>=.25:
            viewer.publish(self.plot_snapshot())

