"""One status destination for lifecycle messages, warnings and errors."""
from tkinter import messagebox


class StatusCenter:
    def __init__(self):
        self.states = {}
        self.notice = ''
        self.alert = ''
        self.level = 'status'

    def update(self, source, text, level='status'):
        text = str(text).strip().replace('\n', '；')
        if source in ('drive', 'heater', 'acquisition', 'runtime'):
            self.states[source] = text
        else:
            self.notice = text
        if level in ('warning','error','alarm'):
            self.alert = text
            self.level = level
        return self.render()

    def render(self):
        parts=[]
        if self.alert:
            parts.append(('【警告】' if self.level=='warning' else '【报警/错误】')+self.alert)
        states=list(dict.fromkeys(value for value in self.states.values() if value))
        if self.notice and self.notice not in states and self.notice != self.alert:
            parts.append(self.notice)
        if states:
            parts.append('；'.join(states))
        return '\n'.join(parts)


def notify(owner, title, text, level='error', **kwargs):
    target=owner
    visited=set()
    while target is not None and id(target) not in visited:
        visited.add(id(target))
        report=getattr(target,'report_status',None)
        if callable(report):
            report(f'{title}：{text}',level)
            return 'ok'
        target=getattr(target,'master',None)
    # Test harnesses and standalone dialogs may not have an application center.
    method=messagebox.showerror if level=='error' else messagebox.showwarning if level=='warning' else messagebox.showinfo
    return method(title,text,**kwargs)


def error(owner,title,text,**kwargs):
    return notify(owner,title,text,'error',**kwargs)


def warning(owner,title,text,**kwargs):
    return notify(owner,title,text,'warning',**kwargs)


def info(owner,title,text,**kwargs):
    return notify(owner,title,text,'status',**kwargs)
