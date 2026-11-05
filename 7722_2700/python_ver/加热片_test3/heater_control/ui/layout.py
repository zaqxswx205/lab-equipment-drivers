"""Tk widget layout construction mixin."""
import tkinter as tk
from collections import deque
from tkinter import ttk

from core import Channel, Config, available_channels
from ui.form import FIELDS
from ui.ports import serial_port_list


class LayoutMixin:
    def build(self):
        self.status = tk.StringVar(value='采集与驱动均未运行，可分别启动。')
        self.drive_status = tk.StringVar(value='交流驱动未运行')
        self.acquisition_status = tk.StringVar(value='2700未采集')
        self.scan_status = tk.StringVar(value='')
        self.readout = tk.StringVar(value='设定电压 -- V    实测电压 -- V    电流 -- A    功率 -- W')
        self.main_tabs = ttk.Notebook(self)
        self.main_tabs.pack(fill='both', expand=True)
        self.settings_page = ttk.Frame(self.main_tabs)
        self.curves_page = ttk.Frame(self.main_tabs)
        self.main_tabs.add(self.settings_page, text='设备与实验设置')
        self.main_tabs.add(self.curves_page, text='实时曲线')
        self.main_tabs.bind('<<NotebookTabChanged>>', lambda _: self.after_idle(self.draw))
        settings_top = ttk.Frame(self.settings_page)
        settings_top.pack(fill='x', padx=10, pady=(4, 0))
        settings_top.columnconfigure(0, weight=3, minsize=900)
        settings_top.columnconfigure(1, weight=1, minsize=340)
        panels = ttk.LabelFrame(settings_top, text='设备设置', padding=8)
        panels.grid(row=0, column=0, sticky='nsew', padx=(0, 6))
        panels.columnconfigure((0, 1, 2), weight=1, uniform='panel')
        control = ttk.LabelFrame(panels, text='7722 交流驱动', padding=8)
        control.grid(row=0, column=0, sticky='nsew', padx=(0, 6))
        heating = ttk.LabelFrame(panels, text='7722 加热负载', padding=8)
        heating.grid(row=0, column=1, sticky='nsew', padx=6)
        tuning = ttk.LabelFrame(panels, text='2700 采集与保护', padding=8)
        tuning.grid(row=0, column=2, sticky='nsew', padx=(6, 0))
        groups = {'7722 交流驱动': control, '7722 加热负载': heating, '2700 采集与保护': tuning}
        self.port_combos = {}
        self.port_desc = {}
        self.port_devs = []
        for frame, key in ((control, 'power_port'), (heating, 'heater_port'), (tuning, 'meter_port')):
            frame.columnconfigure(1, weight=1)
            ttk.Label(frame, text='串口号').grid(row=0, column=0, sticky='w', padx=(0, 8), pady=2)
            var = tk.StringVar(value=getattr(self.cfg, key))
            self.variables[key] = (var, str)
            combo = ttk.Combobox(frame, textvariable=var, width=10)
            combo.grid(row=0, column=1, sticky='w', pady=2)
            combo.bind('<Button-1>', lambda _e: self.refresh_ports())
            self.port_combos[key] = combo
        for key in ('power_baud', 'heater_baud', 'meter_baud', 'meter_flow', 'timeout'):
            self.variables[key] = (tk.StringVar(value=str(getattr(self.cfg, key))), type(getattr(self.cfg, key)))
        for key in ('profile_file', 'power_slew_v_s', 'power_tolerance_rel', 'power_timeout'):
            self.variables[key] = (tk.StringVar(value=str(getattr(self.cfg, key))), type(getattr(self.cfg, key)))
        self.variables['heater_enabled'] = (tk.StringVar(value=str(self.cfg.heater_enabled)), bool)
        self.refresh_ports()
        ttk.Label(heating, text='控制方式（DC）').grid(row=1, column=0, sticky='w', pady=2)
        self.variables['heater_control'] = (tk.StringVar(value='恒定电压' if self.cfg.heater_control == 'voltage' else '恒定功率'), str)
        self.heater_mode_combo = ttk.Combobox(heating, textvariable=self.variables['heater_control'][0],
                                             values=('恒定电压', '恒定功率'), state='readonly', width=10)
        self.heater_mode_combo.grid(row=1, column=1, sticky='w')
        self.heater_mode_combo.bind('<<ComboboxSelected>>', self.update_heater_mode)
        self.heater_target_entries = {}
        counts = dict.fromkeys(groups, 1)
        counts['7722 加热负载'] = 2
        defaults = Config()
        for group, key, label in FIELDS:
            i = counts[group]
            counts[group] += 1
            frame = groups[group]
            frame.columnconfigure(1, weight=1)
            field_label = ttk.Label(frame, text=label)
            field_label.grid(row=i, column=0, sticky='w', padx=(0, 8), pady=2)
            if key == 'heater_max_voltage':
                self.heater_voltage_label = field_label
            initial = getattr(self.cfg, key)
            if key == 'heater_trigger_temperature':
                initial = round(initial+273.15, 10)
            if key == 'heater_max_voltage' and self.cfg.heater_control == 'voltage':
                initial = self.cfg.heater_voltage
            var = tk.StringVar(value=str(initial))
            self.variables[key] = (var, type(getattr(defaults, key)))
            widget = ttk.Combobox(frame, textvariable=var, values=('AC', 'DC'), width=10, state='readonly') if key == 'mode' else ttk.Entry(frame, textvariable=var, width=10)
            widget.grid(row=i, column=1, sticky='w', pady=2)
            if key in ('heater_max_voltage', 'heater_power'):
                self.heater_target_entries[key] = widget
        self.variables['heater_temperature_enabled'] = (tk.StringVar(value=str(self.cfg.heater_temperature_enabled)), bool)
        self.variables['heater_trigger_action'] = (tk.StringVar(value='到温度开启' if self.cfg.heater_trigger_action == 'on' else '到温度关闭'), str)
        ttk.Checkbutton(tuning, text='温度控制加热', variable=self.variables['heater_temperature_enabled'][0],
                        onvalue='True', offvalue='False').grid(row=counts['2700 采集与保护'], column=0, sticky='w', pady=4)
        ttk.Combobox(tuning, textvariable=self.variables['heater_trigger_action'][0], values=('到温度开启', '到温度关闭'),
                     state='readonly', width=10).grid(row=counts['2700 采集与保护'], column=1, sticky='w')
        counts['2700 采集与保护'] += 1
        self.update_heater_mode()
        ttk.Label(control, text='输出固定 AC；设定来自驱动方案。', wraplength=230).grid(row=counts['7722 交流驱动'], column=0, columnspan=2, sticky='w', pady=6)
        ttk.Button(control, text='功率调节设置…', command=self.power_settings).grid(row=counts['7722 交流驱动']+1, column=0, columnspan=2, sticky='w')
        ttk.Label(heating, text='输出固定 DC；功率模式自动反馈调压。', wraplength=230).grid(row=counts['7722 加热负载'], column=0, columnspan=2, sticky='w')
        self.heater_readout = tk.StringVar(value='加热7722：未运行')
        ttk.Label(heating, textvariable=self.heater_readout, wraplength=230).grid(row=counts['7722 加热负载']+1, column=0, columnspan=2, sticky='w')
        self.drive_status_label = ttk.Label(control, textvariable=self.drive_status, wraplength=230, justify='left')
        self.drive_status_label.grid(row=counts['7722 交流驱动']+2, column=0, columnspan=2, sticky='nw', pady=(8, 0))
        self.acquisition_status_label = ttk.Label(tuning, textvariable=self.acquisition_status, wraplength=230, justify='left')
        self.acquisition_status_label.grid(row=counts['2700 采集与保护'], column=0, columnspan=2, sticky='nw', pady=(8, 0))
        scan_row = ttk.Frame(panels)
        scan_row.grid(row=1, column=0, columnspan=3, sticky='ew', pady=(8, 0))
        self.scan_button = ttk.Button(scan_row, text='刷新串口并识别', command=self.scan_devices)
        self.scan_button.pack(side='left')
        ttk.Button(scan_row, text='高级设置…', command=self.connection_settings).pack(side='left', padx=(8, 0))
        ttk.Label(scan_row, textvariable=self.scan_status, wraplength=320,
                  justify='left').pack(side='left', padx=(16, 0))
        run_row = ttk.Frame(panels)
        run_row.grid(row=2, column=0, columnspan=3, sticky='ew', pady=(6, 0))
        self.save_button = ttk.Button(run_row, text='保存配置', command=self.save)
        self.save_button.pack(side='left')
        self.drive_button = ttk.Button(run_row, text='开始驱动', command=self.toggle_drive)
        self.drive_button.pack(side='left', padx=(8, 0))
        self.acquisition_button = ttk.Button(run_row, text='开始采集', command=self.toggle_acquisition)
        self.acquisition_button.pack(side='left', padx=(8, 0))
        self.heater_button = ttk.Button(run_row, text='开始加热', command=self.toggle_heater)
        self.heater_button.pack(side='left', padx=(8, 0))
        self.heater_link = tk.BooleanVar(value=True)
        self.heater_link_button = ttk.Checkbutton(run_row, text='加热联动保护', variable=self.heater_link, command=self.update_heater_link)
        self.heater_link_button.pack(side='left', padx=(8, 0))
        scheme = ttk.LabelFrame(settings_top, text='驱动方案配置文件', padding=6)
        scheme.grid(row=0, column=1, sticky='nsew', padx=(6, 0))
        file_row = ttk.Frame(scheme)
        file_row.pack(fill='x')
        ttk.Entry(file_row, textvariable=self.variables['profile_file'][0], state='readonly').pack(side='left', fill='x', expand=True)
        scheme_buttons = ttk.Frame(scheme)
        scheme_buttons.pack(fill='x', pady=(6, 0))
        ttk.Button(scheme_buttons, text='选择方案…', command=self.choose_profile).pack(side='left')
        ttk.Button(scheme_buttons, text='编辑方案…', command=self.edit_profile).pack(side='left', padx=5)
        ttk.Button(scheme_buttons, text='刷新预览', command=self.preview_profile).pack(side='left')
        self.profile_summary = tk.StringVar()
        ttk.Label(scheme, textvariable=self.profile_summary, wraplength=300, justify='left').pack(anchor='w', pady=4)
        preview = ttk.Frame(scheme)
        preview.pack(fill='both', expand=True)
        self.profile_table = ttk.Treeview(preview, columns=('stage', 'trigger', 'mode', 'value'), show='headings', height=3)
        for key, label, width in zip(self.profile_table['columns'], ('阶段', '触发条件', '驱动方式', '设定值'), (45, 145, 95, 75)):
            self.profile_table.heading(key, text=label)
            self.profile_table.column(key, width=width, anchor='center')
        self.profile_table.pack(side='left', fill='both', expand=True)
        scroll = ttk.Scrollbar(preview, orient='vertical', command=self.profile_table.yview)
        scroll.pack(side='right', fill='y')
        self.profile_table.configure(yscrollcommand=scroll.set)
        channel_frame = ttk.LabelFrame(self.settings_page, text='测温通道', padding=6)
        channel_frame.pack(fill='both', expand=True, padx=10, pady=8)
        shared = ttk.Frame(channel_frame)
        shared.pack(fill='x', pady=(0, 4))
        self.measurement_combos = {}
        for slot in (1, 2):
            for suffix, label, values in (('module', f'Slot {slot}（{slot}xx）', (7700, 7702)),
                                          ('wires', '线制', (2, 4))):
                key = f'slot{slot}_{suffix}'
                ttk.Label(shared, text=label).pack(side='left', padx=4)
                var = tk.StringVar(value=str(getattr(self.cfg, key)))
                self.variables[key] = (var, int)
                combo = ttk.Combobox(shared, textvariable=var, values=values,
                                     width=6 if suffix == 'module' else 3, state='readonly')
                combo.pack(side='left', padx=(0, 12))
                combo.bind('<<ComboboxSelected>>', self.update_measurement_options)
                self.measurement_combos[key] = combo
        self.measurement_hint = tk.StringVar()
        ttk.Label(shared, textvariable=self.measurement_hint, justify='left').pack(side='left', padx=4)
        edit = ttk.Frame(channel_frame)
        edit.pack(fill='x')
        self.channel_vars = {}
        for key, label, values, default in [
            ('channel', '通道', self.cfg.available_channels(), 101),
            ('lead_ohm', '两线总引线补偿(Ω)', None, 0),
            ('min_temperature', '保护下限(K)', None, 223.15),
            ('max_temperature', '保护上限(K)', None, 353.15)]:
            ttk.Label(edit, text=label).pack(side='left', padx=4)
            var = tk.StringVar(value=str(default))
            self.channel_vars[key] = var
            w = ttk.Combobox(edit, textvariable=var, values=values, width=8, state='readonly') if values else ttk.Entry(edit, textvariable=var, width=8)
            w.pack(side='left')
            if key == 'channel':
                self.channel_combo = w
                w.bind('<<ComboboxSelected>>', self.update_lead_editor)
            elif key == 'lead_ohm':
                self.lead_entry = w
        ttk.Button(edit, text='添加/更新', command=self.add_channel).pack(side='left', padx=8)
        ttk.Button(edit, text='删除所选', command=self.remove_channel).pack(side='left')
        self.table = ttk.Treeview(channel_frame, columns=('channel', 'module', 'wires', 'pair', 'lead', 'min_temperature', 'max_temperature', 'resistance', 'temp'), show='headings', height=3)
        for key, label in zip(self.table['columns'], ('测温通道', '所属槽位模块', '接线制（线）', '四线感测配对通道', '两线补偿电阻(Ω)', '保护下限(K)', '保护上限(K)', '实时阻值(Ω)', '实时温度(K)')):
            self.table.heading(key, text=label)
            self.table.column(key, width=110, minwidth=70, anchor='center')
        self.table.pack(fill='both', expand=True, pady=5)
        self.table.bind('<<TreeviewSelect>>', self.select_channel)
        for c in self.cfg.channels:
            module, wires = self.cfg.measurement_for(c.channel)
            self.table.insert('', 'end', iid=str(c.channel), values=(c.channel, module, wires, '--', c.lead_ohm, round(c.min_temperature+273.15, 10), round(c.max_temperature+273.15, 10), '--', '--'))
        self.update_measurement_options()
        self.preview_profile()
        ttk.Label(self.settings_page, textvariable=self.readout, padding=8).pack(fill='x')
        self.messages = deque(maxlen=600)
        curve_header = ttk.Frame(self.curves_page, padding=10)
        curve_header.pack(fill='x')
        ttk.Label(curve_header, textvariable=self.status, wraplength=900, justify='left').pack(side='left', fill='x', expand=True)
        self.plot_stop_drive = ttk.Button(curve_header, text='停止驱动', command=self.stop_drive, state='disabled')
        self.plot_stop_drive.pack(side='right', padx=(8, 0))
        self.plot_stop_acquisition = ttk.Button(curve_header, text='停止采集', command=self.stop_acquisition, state='disabled')
        self.plot_stop_acquisition.pack(side='right', padx=(12, 0))
        ttk.Label(self.curves_page, textvariable=self.readout, padding=(10, 0, 10, 10)).pack(fill='x')
        self.plot_tabs = ttk.Notebook(self.curves_page)
        self.plot_tabs.pack(fill='both', expand=True, padx=10, pady=(0, 6))
        self.plot_canvases = {}
        for key, title in (('temperature', '各通道温度 (K)'), ('resistance', '各通道阻值 (Ω)'),
                           ('power', '7722 有功功率 (W)')):
            frame = ttk.Frame(self.plot_tabs)
            self.plot_tabs.add(frame, text=title)
            canvas = tk.Canvas(frame, height=400, bg='#f6f8fa', highlightthickness=0)
            canvas.pack(fill='both', expand=True)
            canvas.bind('<Configure>', lambda _: self.draw())
            self.plot_canvases[key] = canvas
        self.plot_tabs.bind('<<NotebookTabChanged>>', lambda _: self.draw())
        plot_options = ttk.Frame(self.curves_page, padding=10)
        plot_options.pack(fill='x', before=self.plot_tabs)
        ttk.Label(plot_options, text='显示通道').pack(side='left')
        self.plot_channel = tk.StringVar(value='全部通道')
        self.plot_channel_combo = ttk.Combobox(plot_options, textvariable=self.plot_channel, width=12, state='readonly')
        self.plot_channel_combo.pack(side='left', padx=(8, 12))
        self.plot_channel_combo.bind('<<ComboboxSelected>>', lambda _: self.draw())
        ttk.Label(plot_options, text='阻值为未扣除引线补偿的实测值；功率为7722实测有功功率；曲线保留最近600点。').pack(side='left')
        self.refresh_plot_channels()
