"""Form fields and channel-editing mixin."""
import tkinter as tk
from tkinter import messagebox, ttk

from core import Channel, Config, available_channels


FIELDS = [
    ('7722 交流驱动', 'frequency', '交流频率(Hz)'),
    ('7722 交流驱动', 'max_voltage', '最大电压(V)'),
    ('7722 交流驱动', 'max_current', '最大电流(A)'),
    ('7722 交流驱动', 'max_power', '最大功率(W)'),
    ('7722 加热负载', 'heater_power', '目标功率(W)'),
    ('7722 加热负载', 'heater_max_voltage', '保护电压(V)'),
    ('7722 加热负载', 'heater_max_current', '最大电流(A)'),
    ('7722 加热负载', 'heater_max_power', '最大功率(W)'),
    ('2700 采集与保护', 'settle', '采样稳定时间(s)'),
    ('2700 采集与保护', 'interval', '通道采样间隔(s)'),
    ('2700 采集与保护', 'reference_channel', '参考温度通道'),
    ('2700 采集与保护', 'heater_trigger_temperature', '加热触发温度(K)'),
]


class FormMixin:
    def collect(self):
        cfg = Config()
        for key, (var, cls) in self.variables.items():
            value = var.get() == 'True' if cls is bool else cls(var.get())
            if key == 'heater_control':
                value = {'恒定电压': 'voltage', '恒定功率': 'power'}[value]
            if key == 'heater_trigger_action':
                value = {'到温度开启': 'on', '到温度关闭': 'off'}[value]
            if key == 'heater_trigger_temperature':
                value = round(value-273.15, 10)
            setattr(cfg, key, value)
        if cfg.heater_control == 'voltage':
            cfg.heater_voltage = cfg.heater_max_voltage
        cfg.channels = []
        for item in self.table.get_children():
            cfg.channels.append(Channel(int(self.table.set(item, 'channel')), float(self.table.set(item, 'lead')),
                                        round(float(self.table.set(item, 'min_temperature'))-273.15, 10), round(float(self.table.set(item, 'max_temperature'))-273.15, 10)))
        cfg.validate()
        return cfg

    def measurement_settings(self):
        cfg = Config()
        for key in self.measurement_combos:
            setattr(cfg, key, int(self.variables[key][0].get()))
        return cfg

    def update_lead_editor(self, event=None):
        cfg = self.measurement_settings()
        _, wires = cfg.measurement_for(int(self.channel_vars['channel'].get()))
        if wires == 4:
            self.channel_vars['lead_ohm'].set('0')
        self.lead_entry.configure(state='disabled' if wires == 4 else 'normal')

    def update_measurement_options(self, event=None):
        if not self.run_finished:
            for key in self.measurement_combos:
                self.variables[key][0].set(str(getattr(self.cfg, key)))
            return
        cfg = self.measurement_settings()
        allowed = cfg.available_channels()
        self.channel_combo.configure(values=allowed)
        if int(self.channel_vars['channel'].get()) not in allowed:
            self.channel_vars['channel'].set(str(allowed[0]))
        self.update_lead_editor()
        invalid, cleared = [], False
        for item in self.table.get_children():
            channel = int(self.table.set(item, 'channel'))
            module, wires = cfg.measurement_for(channel)
            offset = 10 if module == 7700 else 20
            self.table.set(item, 'module', module)
            self.table.set(item, 'wires', wires)
            if channel not in allowed:
                invalid.append(str(channel))
            self.table.set(item, 'pair', str(channel+offset) if wires == 4 and channel in allowed else '--')
            if wires == 4:
                cleared |= float(self.table.set(item, 'lead')) != 0
                self.table.set(item, 'lead', '0.0')
        hints = []
        for slot in (1, 2):
            module, wires = cfg.measurement_for(slot*100+1)
            last = available_channels(module, wires, slot)[-1]
            hints.append(f'Slot {slot}: {module}/{wires}线 {slot*100+1}–{last}')
        hint = '；'.join(hints)
        if invalid:
            hint += '。请删除不兼容通道：'+ '、'.join(invalid)
        self.measurement_hint.set(hint)
        if event is not None:
            self.status.set('槽位测温设置已修改；'+('四线槽位的两线补偿已清零。' if cleared else '保存或启动后生效。'))
            self.preview_profile()

    def update_heater_mode(self, _event=None):
        voltage_mode = self.variables['heater_control'][0].get() == '恒定电压'
        self.heater_voltage_label.configure(text='设定电压(V)' if voltage_mode else '保护电压(V)')
        for key, entry in self.heater_target_entries.items():
            active = key == 'heater_max_voltage' or not voltage_mode
            entry.configure(state='normal' if active else 'disabled')

    def add_channel(self):
        if not self.idle():
            return
        try:
            v = self.channel_vars
            c = Channel(int(v['channel'].get()), float(v['lead_ohm'].get()),
                        round(float(v['min_temperature'].get())-273.15, 10), round(float(v['max_temperature'].get())-273.15, 10))
            test = self.measurement_settings()
            test.reference_channel = c.channel
            test.channels = [c]
            test.validate()
            module, wires = test.measurement_for(c.channel)
            key = str(c.channel)
            pair = c.channel + (10 if module == 7700 else 20) if wires == 4 else '--'
            values = (c.channel, module, wires, pair, c.lead_ohm, round(c.min_temperature+273.15, 10), round(c.max_temperature+273.15, 10), '--', '--')
            if self.table.exists(key):
                self.table.item(key, values=values)
            else:
                self.table.insert('', 'end', iid=key, values=values)
            self.update_measurement_options()
            self.preview_profile()
            self.refresh_plot_channels()
        except Exception as exc:
            messagebox.showerror('通道无效', str(exc))

    def remove_channel(self):
        if self.idle():
            for item in self.table.selection():
                self.table.delete(item)
            self.update_measurement_options()
            self.preview_profile()
            self.refresh_plot_channels()

    def select_channel(self, _=None):
        if self.table.selection():
            item = self.table.selection()[0]
            self.channel_vars['channel'].set(self.table.set(item, 'channel'))
            self.channel_vars['lead_ohm'].set(self.table.set(item, 'lead'))
            for key in ('min_temperature', 'max_temperature'):
                self.channel_vars[key].set(self.table.set(item, key))
            self.update_lead_editor()
