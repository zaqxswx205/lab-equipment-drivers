"""Fixed-height, selectable status text with independent scrollbars."""
import tkinter as tk
from tkinter import ttk


class StatusView(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent, width=1, height=100)
        self.grid_propagate(False)
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.text = tk.Text(self, wrap='none', state='disabled', width=1, height=4,
                            font='TkDefaultFont', padx=4, pady=3, exportselection=False)
        self.text.grid(row=0, column=0, sticky='nsew')
        horizontal = ttk.Scrollbar(self, orient='horizontal', command=self.text.xview)
        vertical = ttk.Scrollbar(self, orient='vertical', command=self.text.yview)
        horizontal.grid(row=1, column=0, sticky='ew')
        vertical.grid(row=0, column=1, sticky='ns')
        self.text.configure(xscrollcommand=horizontal.set, yscrollcommand=vertical.set)
        self.normal_foreground = self.text.cget('foreground')
        self.content = ''
        self.menu = tk.Menu(self, tearoff=False)
        self.menu.add_command(label='复制', command=self.copy)
        self.menu.add_command(label='全选', command=self.select_all)
        self.text.bind('<Control-a>', self.select_all)
        self.text.bind('<Control-A>', self.select_all)
        self.text.bind('<Control-c>', self.copy)
        self.text.bind('<Control-C>', self.copy)
        self.text.bind('<Button-3>', self.popup)

    def select_all(self, event=None):
        self.text.focus_set()
        self.text.tag_add('sel', '1.0', 'end-1c')
        return 'break'

    def copy(self, event=None):
        if self.text.tag_ranges('sel'):
            value = self.text.get('sel.first', 'sel.last')
            self.text.clipboard_clear()
            self.text.clipboard_append(value)
        return 'break'

    def popup(self, event):
        try:
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu.grab_release()

    def display(self, content, alarm=False):
        self.text.configure(foreground='#b42318' if alarm else self.normal_foreground)
        if content == self.content:
            return
        # Change only the edited span: selection in unchanged alarms survives
        # routine updates of the sampling/device state elsewhere in the text.
        previous = self.content
        prefix = 0
        while prefix < min(len(previous), len(content)) and previous[prefix] == content[prefix]:
            prefix += 1
        suffix = 0
        while (suffix < min(len(previous),len(content))-prefix and
               previous[len(previous)-1-suffix] == content[len(content)-1-suffix]):
            suffix += 1
        x, y = self.text.xview()[0], self.text.yview()[0]
        self.text.configure(state='normal')
        self.text.replace(f'1.0+{prefix}c', f'1.0+{len(previous)-suffix}c',
                          content[prefix:len(content)-suffix] if suffix else content[prefix:])
        self.text.configure(state='disabled')
        self.text.xview_moveto(x)
        self.text.yview_moveto(y)
        self.content = content
