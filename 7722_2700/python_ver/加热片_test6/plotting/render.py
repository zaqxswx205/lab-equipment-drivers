"""Clean chart rendering with readable ticks, stable colors and clipped lines."""
import math
from plotting.model import METRICS, clipped_lines, finite


def chart_rect(canvas):
    return 72,24,canvas.winfo_width()-20,canvas.winfo_height()-30


def ticks(lo,hi,count=5):
    raw=(hi-lo)/max(1,count)
    power=10**math.floor(math.log10(raw))
    step=next((n*power for n in (1,2,2.5,5,10) if n*power>=raw),10*power)
    start=math.ceil(lo/step)*step
    return [start+i*step for i in range(count+2) if start+i*step<=hi+step*1e-8]


def render(canvas,key,series,bounds):
    canvas.delete('all')
    left,top,right,bottom=chart_rect(canvas)
    if right<=left or bottom<=top:return
    x0,x1,y0,y1=bounds
    for value in ticks(y0,y1,4):
        y=bottom-(value-y0)/(y1-y0)*(bottom-top)
        canvas.create_line(left,y,right,y,fill='#e2e8f0')
        canvas.create_text(left-10,y,anchor='e',text=f'{value:.6g}',fill='#475569',font=('Segoe UI',9))
    for value in ticks(x0,x1,6):
        x=left+(value-x0)/(x1-x0)*(right-left)
        canvas.create_line(x,top,x,bottom,fill='#e2e8f0')
        canvas.create_text(x,bottom+15,text=f'{value:.6g}',fill='#475569',font=('Segoe UI',9))
    canvas.create_line(left,top,left,bottom,right,bottom,fill='#94a3b8')
    canvas.create_text(right,top-12,text='时间 / s',anchor='e',fill='#64748b',font=('Microsoft YaHei UI',8))
    if not series:
        canvas.create_text((left+right)/2,(top+bottom)/2,text='等待采样数据，或在左侧选择通道',fill='#64748b',font=('Microsoft YaHei UI',10))
    legend_x=left
    for index,(label,color,samples) in enumerate(series):
        if legend_x+65<right-65:
            canvas.create_line(legend_x,top-12,legend_x+14,top-12,fill=color,width=2)
            display=label
            measure=lambda text: int(canvas.tk.call('font','measure',('Segoe UI',9),text))
            while display and measure(display)>right-65-(legend_x+20):
                display=display[:-1]
            if display != label:display=display[:-1]+'…'
            canvas.create_text(legend_x+20,top-12,text=display,anchor='w',fill='#475569',font=('Segoe UI',9))
            legend_x+=max(68,measure(display)+34)
        elif index==len(series)-1:
            canvas.create_text(legend_x,top-12,text='…',anchor='w',fill='#64748b')
        for points in clipped_lines(samples,bounds,(left,top,right,bottom)):
            canvas.create_line(*points,fill=color,width=1.8,dash=() if index<8 else (4,2))
        if samples:
            t,v=samples[-1]
            if finite(t) and finite(v) and x0<=t<=x1 and y0<=v<=y1:
                x=left+(t-x0)/(x1-x0)*(right-left);y=bottom-(v-y0)/(y1-y0)*(bottom-top)
                canvas.create_oval(x-2.5,y-2.5,x+2.5,y+2.5,fill=color,outline='white',width=1)
