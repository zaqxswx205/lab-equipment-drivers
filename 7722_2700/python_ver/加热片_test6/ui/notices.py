"""One status destination for lifecycle messages, warnings and errors."""
from tkinter import messagebox


class StatusCenter:
    def __init__(self):
        self.states = {}
        self.notice = ''
        self.alerts = {}

    @property
    def alert(self):
        return '\n'.join(text for text, level in self.alerts.values())

    @property
    def level(self):
        return ('error' if any(level != 'warning' for text, level in self.alerts.values())
                else 'warning' if self.alerts else 'status')

    def update(self, source, text, level='status'):
        text = str(text).strip().replace('\n', '；')
        if source in ('profile', 'discovery', 'scan', 'performance', 'recording', 'channel_names') and level not in ('warning', 'error', 'alarm'):
            self.alerts.pop(source, None)
        if source in ('profile', 'discovery'):
            # A profile validation result describes the current form, not a
            # latched hardware fault. Resolving it must preserve other alarms.
            if level not in ('warning', 'error', 'alarm'):
                self.alerts.pop(source, None)
        elif source in ('drive', 'heater', 'acquisition', 'runtime'):
            self.states[source] = text
        else:
            self.notice = text
        if level in ('warning','error','alarm'):
            self.alerts.pop(source, None)
            self.alerts[source] = (text, level)
        return self.render()

    def render(self):
        parts=[]
        for text, level in dict.fromkeys(self.alerts.values()):
            parts.append(('【警告】' if level=='warning' else '【报警/错误】')+text)
        states=list(dict.fromkeys(value for value in self.states.values() if value))
        if self.notice and self.notice not in states and self.notice not in [text for text, _ in self.alerts.values()]:
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
