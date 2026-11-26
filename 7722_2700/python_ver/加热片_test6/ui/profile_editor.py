from ui import notices
"""Tabular drive editing with an optional INI and examples view."""
import configparser
import io
import re
import tkinter as tk
from tkinter import ttk, messagebox

from drive_profile import profile_text_kelvin
from ui.profile_help import with_profile_help

TRIGGERS = {'time': '仅时间', 'temperature': '仅温度', 'time_or_temperature': '时间或温度'}
MODES = {'voltage': '电压(V)', 'power': '功率(W)'}
COMPLETION = {'manual':'手动', 'duration':'持续时长', 'peak_to_peak':'峰峰值判稳'}
TEST_FIELDS = ('heater_w','stability_mode','duration_s','window_s','peak_to_peak_k')
COLUMNS = ('stage', 'trigger', 'seconds', 'temperature', 'mode', 'value', 'heater_w', 'stability_mode', 'judgment_s', 'peak_to_peak_k')


def judgment_field(row):
    return {'duration':'duration_s', 'peak_to_peak':'window_s'}.get(row['stability_mode'])


def cell_enabled(row, key):
    if key == 'judgment_s':
        return judgment_field(row) is not None
    if key == 'peak_to_peak_k':
        return row['stability_mode'] == 'peak_to_peak'
    if key == 'stability_mode':
        return True
    if key == 'seconds':
        return row['trigger'] != 'temperature'
    return key != 'stage'


def set_cell_value(row, key, value):
    if not cell_enabled(row,key):
        return False
    if key == 'judgment_s':
        row[judgment_field(row)] = value
    else:
        if key == 'stability_mode':
            before = judgment_field(row)
            after = {'duration':'duration_s', 'peak_to_peak':'window_s'}.get(value)
            # Changing the meaning of the shared column keeps its visible value.
            if before and after:
                row[after] = row[before]
        row[key] = value
    return True


def rows_from_text(text):
    parser = configparser.ConfigParser(interpolation=None)
    parser.read_string(profile_text_kelvin(text))
    sections = [s for s in parser.sections() if s != 'Drive']
    if sections != [f'Step {i}' for i in range(1, len(sections)+1)]:
        raise ValueError('阶段须按Step 1、Step 2…连续编号')
    rows = []
    for name in sections:
        section = dict(parser[name])
        trigger = section.get('trigger', parser.get('Drive', 'strategy'))
        rows.append(dict(trigger=trigger,
                         seconds=section.get('at', '') if trigger != 'temperature' else '',
                         temperature=section.get('at', '') if trigger == 'temperature' else section.get('temperature_at', section.get('test_temperature_k', '')),
                         mode=section.get('mode', 'voltage'), value=section.get('value', '0'),
                         points=section.get('performance_points', ''), extra=section,
                         **{key: section.get(key, {'heater_w':'', 'stability_mode':'manual', 'duration_s':'300', 'window_s':'300', 'peak_to_peak_k':'.1'}[key]) for key in TEST_FIELDS}))
    return rows


def text_from_rows(rows, original):
    if not rows:
        raise ValueError('至少保留一个阶段')
    parser = configparser.ConfigParser(interpolation=None)
    parser.read_string(profile_text_kelvin(original))
    for section in list(parser.sections()):
        if section != 'Drive':
            parser.remove_section(section)
    parser['Drive'].update(strategy='mixed', time_unit='s', temperature_unit='K')
    point_index = 0
    for i, row in enumerate(rows, 1):
        section = dict(row.get('extra', {}))
        section.update(trigger=row['trigger'], mode=row['mode'], value=row['value'],
                       at=row['temperature'] if row['trigger'] == 'temperature' else row['seconds'])
        section.pop('temperature_at', None)
        section.pop('performance_points', None)
        if row['trigger'] == 'time_or_temperature':
            section['temperature_at'] = row['temperature']
        for key in (*TEST_FIELDS, 'test_temperature_k'):
            section.pop(key, None)
        if row.get('heater_w', '').strip():
            point_index += 1
            section['performance_points'] = str(point_index)
            section['test_temperature_k'] = row['temperature']
            section.update({key: row[key] for key in TEST_FIELDS})
        else:
            section.update({key: row[key] for key in TEST_FIELDS if key != 'heater_w'})
            if row['points'].strip():
                section['performance_points'] = row['points'].strip()
        parser[f'Step {i}'] = section
    out = io.StringIO()
    parser.write(out)
    # Keep commented stages and user notes when rebuilding active table rows.
    comments = "\n\n".join(re.findall(r"(?m)^(?:[ \t]*[;#][^\n]*(?:\n|$))+", original))
    return with_profile_help(comments + "\n\n" + out.getvalue())


class ProfileEditor(ttk.Frame):
    def __init__(self, parent, text):
        super().__init__(parent)
        self.source = text
        self.rows = rows_from_text(text)
        self.dirty = False
        self.cell = None
        self.active = 0
        self.tabs = ttk.Notebook(self)
        self.tabs.pack(fill='both', expand=True)
        self.table_page = ttk.Frame(self.tabs)
        self.raw_page = ttk.Frame(self.tabs)
        self.tabs.add(self.table_page, text='阶段表格')
        self.tabs.add(self.raw_page, text='INI方案与填写示例')
        toolbar = ttk.Frame(self.table_page, padding=(0, 8))
        toolbar.pack(fill='x')
        for label, command in [('新增阶段', self.add), ('删除阶段', self.delete),
                                ('上移', lambda: self.move(-1)), ('下移', lambda: self.move(1))]:
            ttk.Button(toolbar, text=label, command=command).pack(side='left', padx=(0, 6))
        ttk.Label(toolbar, text='双击单元格编辑；回车或点击别处提交。').pack(side='left', padx=8)
        body = ttk.Frame(self.table_page)
        body.pack(fill='both', expand=True)
        self.table = ttk.Treeview(body, columns=COLUMNS, show='headings', selectmode='browse')
        for key, title, width in zip(COLUMNS,
            ('阶段', '进入条件', '启动时间点(s)', '目标温度(K)', '输出模式', '制冷机设定', '加热功率(W)', '完成条件', '判定时长(s)', '允许峰峰值(K)'),
            (60, 110, 105, 110, 105, 110, 105, 120, 120, 120)):
            self.table.heading(key, text=title)
            self.table.column(key, width=width, minwidth=60, stretch=True)
        vertical = ttk.Scrollbar(body, orient='vertical', command=self.table.yview)
        horizontal = ttk.Scrollbar(body, orient='horizontal', command=self.table.xview)
        self.table.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)
        self.table.grid(row=0, column=0, sticky='nsew')
        vertical.grid(row=0, column=1, sticky='ns')
        horizontal.grid(row=1, column=0, sticky='ew')
        self.table.bind('<Button-1>', lambda _: self.commit_cell())
        self.table.bind('<Double-1>', self.edit_cell)
        ttk.Label(self.table_page, text='判定时长：持续时长=测试秒数，峰峰值判稳=窗口秒数；手动不使用。—表示不适用，不能编辑。',
                  padding=(0, 8)).pack(fill='x')
        self.raw = tk.Text(self.raw_page, undo=True, wrap='none')
        raw_y = ttk.Scrollbar(self.raw_page, orient='vertical', command=self.raw.yview)
        raw_x = ttk.Scrollbar(self.raw_page, orient='horizontal', command=self.raw.xview)
        self.raw.configure(yscrollcommand=raw_y.set, xscrollcommand=raw_x.set)
        self.raw_page.rowconfigure(0, weight=1)
        self.raw_page.columnconfigure(0, weight=1)
        self.raw.grid(row=0, column=0, sticky='nsew')
        raw_y.grid(row=0, column=1, sticky='ns')
        raw_x.grid(row=1, column=0, sticky='ew')
        self.raw.insert('1.0', text)
        self.tabs.bind('<<NotebookTabChanged>>', self.tab_changed)
        self.redraw()

    def redraw(self, selection=None):
        self.table.delete(*self.table.get_children())
        for i, row in enumerate(self.rows):
            self.table.insert('', 'end', iid=str(i), values=(i+1, TRIGGERS.get(row['trigger'],row['trigger']),
                row['seconds'] if row['trigger'] != 'temperature' else '',
                row['temperature'],
                MODES.get(row['mode'],row['mode']), row['value'], row['heater_w'],
                COMPLETION.get(row['stability_mode'],row['stability_mode']),
                row[judgment_field(row)] if cell_enabled(row,'judgment_s') else '—',
                row['peak_to_peak_k'] if cell_enabled(row,'peak_to_peak_k') else '—'))
        if selection is not None and self.rows:
            iid = str(min(selection, len(self.rows)-1))
            self.table.selection_set(iid)
            self.table.see(iid)

    def commit_cell(self, _=None):
        if self.cell:
            widget, index, key = self.cell
            value = widget.get()
            self.cell = None
            widget.destroy()
            mapping = TRIGGERS if key == 'trigger' else MODES if key == 'mode' else COMPLETION if key == 'stability_mode' else {}
            value = next((k for k,v in mapping.items() if v == value), value)
            set_cell_value(self.rows[index], key, value)
            self.dirty = True
            self.redraw(index)

    def edit_cell(self, event):
        self.commit_cell()
        iid = self.table.identify_row(event.y)
        column = self.table.identify_column(event.x)
        if not iid or not column:
            return
        key = COLUMNS[int(column[1:])-1]
        if key == 'stage':
            return
        row = self.rows[int(iid)]
        if not cell_enabled(row,key):
            return
        box = self.table.bbox(iid, column)
        if not box:
            return
        if key in ('trigger','mode','stability_mode'):
            mapping = TRIGGERS if key == 'trigger' else MODES if key == 'mode' else COMPLETION
            widget = ttk.Combobox(self.table, values=list(mapping.values()), state='readonly')
            widget.set(mapping.get(row[key], row[key]))
            widget.bind('<<ComboboxSelected>>', self.commit_cell)
        else:
            widget = ttk.Entry(self.table)
            widget.insert(0, row[judgment_field(row)] if key == 'judgment_s' else row[key])
        self.cell = widget, int(iid), key
        widget.place(x=box[0], y=box[1], width=box[2], height=box[3])
        widget.focus_set()
        widget.bind('<Return>', self.commit_cell)
        # A combobox popup takes focus while opening. Commit it on selection,
        # Enter, table click, or save, so opening the list cannot destroy it.
        if not isinstance(widget, ttk.Combobox):
            widget.bind('<FocusOut>', self.commit_cell)
        def cancel(_):
            self.cell = None
            widget.destroy()
        widget.bind('<Escape>', cancel)

    def selected(self):
        selected = self.table.selection()
        return int(selected[0]) if selected else None

    def add(self):
        self.commit_cell()
        self.rows.append(dict(trigger='time',seconds='',temperature='',mode='voltage',value='0',points='',extra={},heater_w='',stability_mode='manual',duration_s='300',window_s='300',peak_to_peak_k='.1'))
        self.dirty = True
        self.redraw(len(self.rows)-1)

    def delete(self):
        self.commit_cell()
        index = self.selected()
        if index is not None:
            if len(self.rows) == 1:
                notices.warning(self, '无法删除', '至少保留一个阶段。', parent=self)
                return
            self.rows.pop(index)
            self.dirty = True
            self.redraw(index)

    def move(self, offset):
        self.commit_cell()
        index = self.selected()
        if index is not None and 0 <= index+offset < len(self.rows):
            other = index+offset
            self.rows[index], self.rows[other] = self.rows[other], self.rows[index]
            self.dirty = True
            self.redraw(other)

    def tab_changed(self, _=None):
        selected = self.tabs.index(self.tabs.select())
        if selected == self.active:
            return
        try:
            self.commit_cell()
            if selected == 1:
                self.source = text_from_rows(self.rows, self.source) if self.dirty else self.source
                self.raw.delete('1.0', 'end')
                self.raw.insert('1.0', self.source)
                self.dirty = False
            else:
                source = with_profile_help(profile_text_kelvin(self.raw.get('1.0', 'end-1c')))
                rows = rows_from_text(source)
                self.source, self.rows = source, rows
                self.dirty = False
                self.redraw()
            self.active = selected
        except Exception as exc:
            self.tabs.select(self.active)
            notices.error(self, '方案格式错误', str(exc), parent=self)

    def get_text(self):
        self.commit_cell()
        if self.active == 1:
            return self.raw.get('1.0', 'end-1c')
        return text_from_rows(self.rows, self.source) if self.dirty else self.source
