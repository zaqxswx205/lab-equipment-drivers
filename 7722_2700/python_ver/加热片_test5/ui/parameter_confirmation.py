from ui import notices
"""Blind parameter re-entry before a manual power start."""
from decimal import Decimal, InvalidOperation
import tkinter as tk
from tkinter import ttk


DRIVE_FIELDS = (
    ('max_voltage', '交流驱动 · 最大电压(V)'),
    ('max_current', '交流驱动 · 最大电流(A)'),
    ('max_power', '交流驱动 · 最大功率(W)'),
)
COMMON_FIELDS = (
    ('heater_power', '加热负载 · 目标功率(W)'),
    ('heater_max_voltage', '加热负载 · 保护电压/设定电压(V)'),
    ('heater_max_current', '加热负载 · 最大电流(A)'),
    ('heater_max_power', '加热负载 · 最大功率(W)'),
    ('reference_channel', '采集与保护 · 参考通道'),
    ('heater_trigger_temperature', '采集与保护 · 触发温度(K)'),
)


def fields_for(part):
    return (DRIVE_FIELDS if part == 'drive' else ()) + COMMON_FIELDS


def numeric_equal(entered, expected):
    try:
        value, target = Decimal(str(entered).strip()), Decimal(str(expected).strip())
        return value.is_finite() and target.is_finite() and value == target
    except (InvalidOperation, ValueError):
        return False


def confirmation_values(cfg, part):
    values = {key: getattr(cfg, key) for key, _ in fields_for(part)}
    values['heater_trigger_temperature'] = round(cfg.heater_trigger_temperature + 273.15, 10)
    return values


class ParameterConfirmation(tk.Toplevel):
    def __init__(self, parent, part, expected):
        super().__init__(parent)
        self.result = False
        self.expected = expected
        self.variables = {}
        self.markers = {}
        self.title('开始驱动前参数复核' if part == 'drive' else '开始加热前参数复核')
        self.transient(parent)
        self.resizable(False, False)
        body = ttk.Frame(self, padding=16)
        body.pack(fill='both', expand=True)
        ttk.Label(body, text='请重新输入以下参数；全部一致后才可启动。').grid(
            row=0, column=0, columnspan=3, sticky='w', pady=(0, 12))
        first = None
        for row, (key, label) in enumerate(fields_for(part), 1):
            ttk.Label(body, text=label).grid(row=row, column=0, sticky='w', pady=5)
            var = tk.StringVar(self, value='')
            self.variables[key] = var
            entry = ttk.Entry(body, textvariable=var, width=16)
            entry.grid(row=row, column=1, padx=12, pady=5)
            marker = tk.Label(body, text='待输入', fg='#666666', anchor='w', width=29)
            marker.grid(row=row, column=2, sticky='w')
            self.markers[key] = marker
            var.trace_add('write', lambda *_args: self.validate_inputs())
            first = first or entry
        buttons = ttk.Frame(body)
        buttons.grid(row=len(self.variables)+1, column=0, columnspan=3, sticky='e', pady=(12, 0))
        ttk.Button(buttons, text='取消', command=self.destroy).pack(side='left', padx=8)
        self.confirm_button = ttk.Button(buttons, text='确认并启动', command=self.accept, state='disabled')
        self.confirm_button.pack(side='left')
        self.bind('<Escape>', lambda _: self.destroy())
        self.bind('<Return>', lambda _: self.accept())
        self.protocol('WM_DELETE_WINDOW', self.destroy)
        self.grab_set()
        first.focus_set()

    def validate_inputs(self):
        matched = True
        for key, var in self.variables.items():
            text = var.get()
            valid = numeric_equal(text, self.expected[key])
            matched &= valid
            self.markers[key].configure(
                text='一致' if valid else '输入参数不一致，请确认' if text.strip() else '待输入',
                fg='#16803c' if valid else '#cc0000' if text.strip() else '#666666')
        self.confirm_button.configure(state='normal' if matched else 'disabled')
        return matched

    def accept(self):
        if self.validate_inputs():
            self.result = True
            self.destroy()


def confirm_parameters(parent, part, cfg):
    expected = confirmation_values(cfg, part)
    # A running session uses its original snapshot. Never confirm different UI values.
    if any(not numeric_equal(parent.variables[key][0].get(), value) for key, value in expected.items()):
        from tkinter import messagebox
        notices.error(self, '参数已变更', '主界面参数与本轮运行参数不一致，请停止本轮运行后重新设置。', parent=parent)
        return False
    dialog = ParameterConfirmation(parent, part, expected)
    parent.wait_window(dialog)
    return dialog.result
