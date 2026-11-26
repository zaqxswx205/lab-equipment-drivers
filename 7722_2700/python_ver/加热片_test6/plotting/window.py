"""Independent Tk curve window. No device or runtime imports are permitted here."""
import queue
import tkinter as tk
from tkinter import ttk

from plotting.model import METRICS, PALETTE, bounds_for, validate_bounds, zoom, pan
from plotting.render import render, chart_rect
from plotting.layout import build_layout
from plotting.model import finite


class CurveWindow(tk.Tk):
    def __init__(self, snapshots, commands, requests):
        super().__init__()
        self.snapshots,self.commands,self.requests=snapshots,commands,requests
        self.title('实时曲线')
        self.geometry('1280x900');self.minsize(960,680)
        self.snapshot={};self.channels=[];self.channel_names={};self.session=None
        self.bounds={key:None for key in METRICS}
        self.dirty=True;self.drag_start=None
        self.follow=tk.BooleanVar(value=True)
        self.stats=tk.StringVar()
        self.hint=tk.StringVar(value='滚轮缩放 · 拖动平移 · 双击恢复跟随')
        self.hover_position=None
        build_layout(self)
        self.arrange()
        self.protocol('WM_DELETE_WINDOW',self.close)
        self.send('opened',None)
        self.after(100,self.poll)

    def send(self,kind,value):
        try:self.requests.put_nowait((kind,value))
        except queue.Full:pass

    def close(self):
        self.send('closed',None)
        self.destroy()

    def destroy(self):
        for timer in self.tk.call('after','info'):
            self.after_cancel(timer)
        super().destroy()

    def invalidate(self):
        self.dirty=True
        self.selected_count.set(f'{len(self.channel_list.curselection())} / {len(self.channels)} 已选')

    def select_all(self):
        self.channel_list.selection_set(0,'end');self.invalidate()

    def select_none(self):
        self.channel_list.selection_clear(0,'end');self.invalidate()

    def channel_label(self, channel):
        name = self.channel_names.get(channel, '')
        return f'{channel} · {name}' if name else str(channel)

    def arrange(self):
        self.hover_position=None
        for canvas in self.canvases.values():canvas.delete('cursor')
        self.empty_chart.grid_remove()
        for row,(key,frame) in enumerate(self.frames.items()):
            frame.grid_remove()
            shown=self.visible[key].get()
            self.chart_area.rowconfigure(row,weight=(2 if key=='temperature' else 1) if shown else 0,minsize=85 if shown else 0)
            if shown:frame.grid(row=row,column=0,sticky='nsew',pady=(0,8))
        if not any(v.get() for v in self.visible.values()):
            self.chart_area.rowconfigure(0,weight=1)
            self.empty_chart.grid(row=0,column=0,sticky='nsew')
        self.invalidate()

    def choose_metric(self):
        self.visible[self.metric_key()].set(True)
        self.arrange();self.show_bounds()

    def hover(self,key,event):
        canvas=self.canvases[key];canvas.delete('cursor')
        left,top,right,bottom=chart_rect(canvas)
        if right<=left or bottom<=top or not left<=event.x<=right or not top<=event.y<=bottom:return
        self.hover_position=(key,event.x,event.y)
        x0,x1,y0,y1=self.current_bounds(key)
        at=x0+(event.x-left)/(right-left)*(x1-x0)
        canvas.create_line(event.x,top,event.x,bottom,fill='#94a3b8',dash=(3,3),tags='cursor')
        readings=[]
        for label,color,samples in self.series(key):
            valid=[(t,v) for t,v in samples if finite(t) and finite(v) and x0<=t<=x1]
            if not valid:continue
            t,value=min(valid,key=lambda p:abs(p[0]-at))
            if y0<=value<=y1:
                x=left+(t-x0)/(x1-x0)*(right-left);y=bottom-(value-y0)/(y1-y0)*(bottom-top)
                canvas.create_oval(x-3,y-3,x+3,y+3,fill=color,outline='white',tags='cursor')
            readings.append(f'{label}: {value:.4f} {METRICS[key][1]}')
        self.hint.set(f'{at:.2f} s  ·  '+('   '.join(readings[:3]) if readings else '该范围暂无数据'))

    def leave_chart(self,key):
        self.canvases[key].delete('cursor');self.hover_position=None
        self.hint.set('滚轮缩放 · 拖动平移 · 双击恢复跟随')

    def series(self,key):
        if key=='power':
            data=self.snapshot.get('power',[])
            return [('交流7722','#218a50',data)] if data else []
        column=1 if key=='temperature' else 2
        selected=[self.channels[i] for i in self.channel_list.curselection()]
        history=self.snapshot.get('history',{})
        return [(self.channel_label(ch),PALETTE[self.channels.index(ch)%len(PALETTE)],
                 [(p[0],p[column]) for p in history.get(ch,[])]) for ch in selected if history.get(ch)]

    def current_bounds(self,key):
        return self.bounds[key] or bounds_for(self.series(key),METRICS[key][2])

    def freeze(self):
        for key in METRICS:
            self.bounds[key]=self.current_bounds(key)
        self.follow.set(False)
        self.view_note.set('浏览历史 · 采集继续')

    def toggle_follow(self):
        if self.follow.get():self.reset_view()
        else:self.freeze();self.invalidate()

    def reset_view(self):
        self.follow.set(True)
        self.view_note.set('自动跟随最新数据')
        self.bounds={key:None for key in METRICS}
        self.hint.set('自动跟随 · 滚轮缩放 · 拖动平移')
        self.invalidate()

    def metric_key(self):
        return next(key for key,(title,unit,_) in METRICS.items() if f'{title} ({unit})'==self.metric.get())

    def show_bounds(self):
        for var,value in zip(self.entries,self.current_bounds(self.metric_key())[:2]):
            var.set(f'{value:.9g}')

    def apply_bounds(self):
        try:
            limits=[v.get() for v in self.entries]+list(self.current_bounds(self.metric_key())[2:])
            values=validate_bounds(limits)
        except ValueError as exc:self.hint.set(str(exc));return
        self.freeze();self.bounds[self.metric_key()]=values;self.invalidate()
        self.hint.set('已固定时间范围；采集继续。双击图表恢复跟随。')

    def wheel(self,key,event):
        if not event.delta:return
        self.freeze()
        canvas=self.canvases[key]
        left,top,right,bottom=chart_rect(canvas)
        xf=max(0.,min(1.,(event.x-left)/max(1,right-left)))
        yf=max(0.,min(1.,1-(event.y-top)/max(1,bottom-top)))
        try:self.bounds[key]=zoom(self.bounds[key],.8 if event.delta>0 else 1.25,xf,yf)
        except ValueError:return
        self.metric.set(f'{METRICS[key][0]} ({METRICS[key][1]})');self.invalidate()
        self.hint.set('已暂停跟随；采集继续。双击图表恢复。')

    def begin_drag(self,key,event):
        self.freeze();self.drag_start=(key,event.x,event.y,self.bounds[key])

    def drag(self,key,event):
        if not self.drag_start or self.drag_start[0]!=key:return
        _,x,y,bounds=self.drag_start
        canvas=self.canvases[key]
        left,top,right,bottom=chart_rect(canvas)
        dx=-(event.x-x)*(bounds[1]-bounds[0])/max(1,right-left)
        dy=(event.y-y)*(bounds[3]-bounds[2])/max(1,bottom-top)
        try:self.bounds[key]=pan(bounds,dx,dy)
        except ValueError:return
        self.metric.set(f'{METRICS[key][0]} ({METRICS[key][1]})');self.invalidate()

    def update_snapshot(self,data):
        new_session=data.get('session_id')!=self.session
        self.session=data.get('session_id')
        self.snapshot=data
        channels=data.get('channels',[])
        names=data.get('channel_names',{})
        if channels!=self.channels or names!=self.channel_names:
            selected={self.channels[i] for i in self.channel_list.curselection()}
            active = self.channels[self.channel_list.index('active')] if self.channels else None
            top = self.channel_list.yview()[0]
            all_selected=len(selected)==len(self.channels)
            self.channel_list.delete(0,'end');self.channels=channels;self.channel_names=names
            for i,ch in enumerate(channels):
                self.channel_list.insert('end',f'  ●  {self.channel_label(ch)}')
                self.channel_list.itemconfig(i,fg=PALETTE[i%len(PALETTE)],selectforeground=PALETTE[i%len(PALETTE)])
                if all_selected or ch in selected:self.channel_list.selection_set(i)
                if ch == active:self.channel_list.activate(i)
            self.channel_list.yview_moveto(top)
        if new_session:self.reset_view()
        previous_stats=self.stats.get()
        self.stats.set(data.get('stats',''))
        if self.stats.get() and not previous_stats:self.stats_panel.pack(fill='x',side='bottom',padx=12,pady=(0,6))
        elif not self.stats.get() and previous_stats:self.stats_panel.pack_forget()
        count=max([len(v) for v in data.get('history',{}).values()]+[len(data.get('power',[])),0])
        self.sample_note.set(f'最近 {count} / 600 点')
        self.invalidate()

    def poll(self):
        try:
            while True:
                command=self.commands.get_nowait()
                if command=='close':self.close();return
                if command=='focus':self.deiconify();self.lift()
        except queue.Empty:pass
        try:
            data=self.snapshots.get_nowait()
            self.update_snapshot(data)
        except queue.Empty:pass
        if self.dirty:
            for key in METRICS:
                if self.visible[key].get():
                    if self.follow.get():self.bounds[key]=None
                    render(self.canvases[key],key,self.series(key),self.current_bounds(key))
            if self.hover_position:
                key,x,y=self.hover_position
                from types import SimpleNamespace
                self.hover(key,SimpleNamespace(x=x,y=y))
            # Do not overwrite coordinate entries while the user is typing.
            if not isinstance(self.focus_get(),ttk.Entry):self.show_bounds()
            self.dirty=False
        self.after(100,self.poll)


def run_window(snapshots,commands,requests):
    window=CurveWindow(snapshots,commands,requests)
    window.mainloop()
