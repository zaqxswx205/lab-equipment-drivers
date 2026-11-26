"""Live channel-name editing, independent of device/protection settings."""
from model.channels import clean_channel_name
from model.config_io import save_channel_name


class ChannelNamesMixin:
    def bind_channel_name_editor(self):
        self.channel_name_timer = None
        self.pending_channel_name = None
        self.loading_channel_name = False
        self.load_channel_name()
        self.channel_vars['channel'].trace_add('write', self.load_channel_name)
        self.channel_vars['name'].trace_add('write', self.schedule_channel_name)
        self.channel_name_entry.bind('<Return>', self.commit_channel_name)
        self.channel_name_entry.bind('<FocusOut>', self.commit_channel_name)

    def load_channel_name(self, *_):
        self.commit_channel_name()
        channel = int(self.channel_vars['channel'].get())
        self.loading_channel_name = True
        try:
            self.channel_vars['name'].set(self.channel_names.get(channel, ''))
        finally:
            self.loading_channel_name = False

    def schedule_channel_name(self, *_):
        if self.loading_channel_name:
            return
        # Capture the channel now; switching rows must not rename the new row.
        self.pending_channel_name = (int(self.channel_vars['channel'].get()), self.channel_vars['name'].get())
        if self.channel_name_timer is not None:
            self.after_cancel(self.channel_name_timer)
        self.channel_name_timer = self.after(400, self.commit_channel_name)

    def commit_channel_name(self, *_):
        timer = getattr(self, 'channel_name_timer', None)
        if timer is not None:
            self.after_cancel(timer)
            self.channel_name_timer = None
        pending = getattr(self, 'pending_channel_name', None)
        self.pending_channel_name = None
        if pending is not None:
            self.set_channel_name(*pending)

    def set_channel_name(self, channel, name):
        try:
            name = clean_channel_name(name)
            self.channel_names[channel] = name
            if not self.table.exists(str(channel)):
                # A name can be entered before adding a new measurement channel.
                self.report_status(f'通道 {channel} 名称已填写；添加通道后点击“保存配置”可保存。',
                                   source='channel_names')
                return
            self.table.set(str(channel), 'name', name)
            active = not self.run_finished or bool(self.worker and self.worker.is_alive())
            if active:
                self.controls.rename_channel(channel, name)
            else:
                for item in self.cfg.channels:
                    if item.channel == channel:
                        item.name = name
            try:
                save_channel_name(self.path, channel, name)
                saved, level = '名称已保存。', 'status'
            except Exception as exc:
                saved, level = f'名称已生效，但尚未写入配置：{exc}', 'warning'
            self.report_status(f'通道 {channel}：{name or "恢复默认名称"}；'+saved,
                               level, source='channel_names')
            self.draw()
        except Exception as exc:
            self.report_status('通道命名失败：'+str(exc), 'error', source='channel_names')
