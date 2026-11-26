"""Nonblocking heater-OFF-before-AC-OFF interlock, shared by every stop path."""
import time


class Shutdown:
    def __init__(self, owner):
        self.owner = owner
        self.heater = self.drive = False
        self.close_drive = False
        self.close_heater = True
        self.next_retry = 0.
        self.last_message = ''

    @property
    def pending(self):
        return self.heater or self.drive

    def request(self, drive=False, heater=True, close_drive=True):
        self.heater |= heater or drive
        self.drive |= drive
        self.close_drive |= drive and close_drive
        o = self.owner
        if self.heater:
            o.controls.heater.clear()
        if drive and close_drive:
            o.controls.drive.clear()
        # Inhibit old heater commands immediately. AC retains its current output
        # until the heater's explicit OFF acknowledgement has arrived.
        if o.clients.get('heater'):
            o.clients['heater'].request('inhibit')

    def notice(self, text):
        if text != self.last_message:
            self.owner.emit('shutdown_pending', text)
            self.owner.logger.log(text)
            self.last_message = text

    def tick(self):
        if not self.pending:
            return
        o = self.owner
        heater = o.clients.get('heater')
        if heater and not heater.off_confirmed:
            self.notice('加热输出关闭未确认；交流驱动保持当前输出；2700继续采集。')
            if time.monotonic()>=self.next_retry and not heater.closing:
                heater.request('off')
                self.next_retry = time.monotonic()+1.
            return
        if self.heater:
            self.heater = False
            if heater and not heater.closing:
                heater.closing = True
                heater.request('close')
        if self.drive:
            drive = o.clients.get('drive')
            if drive and not drive.off_confirmed:
                self.notice('加热已确认OFF；正在关闭交流驱动；2700继续采集。')
                if time.monotonic()>=self.next_retry and not drive.closing:
                    drive.request('inhibit')
                    drive.request('off')
                    self.next_retry = time.monotonic()+1.
                return
            self.drive = False
            o.enabled = False
            o.controls.drive_ready.clear()
            if drive and self.close_drive and not drive.closing:
                drive.closing = True
                drive.request('close')
            elif drive and not self.close_drive:
                drive.request('resume')
            self.close_drive = False
        if not self.pending:
            o.off_ok = True
            self.last_message = ''
            o.emit('shutdown_confirmed', '加热输出已确认OFF；本次电源关断请求已完成；2700继续采集。')
