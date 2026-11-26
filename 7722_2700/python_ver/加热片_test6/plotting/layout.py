"""Curve-window widgets; channel names are edited in the main window."""
import tkinter as tk
from tkinter import ttk
from plotting.model import METRICS
from plotting.style import apply_theme


def build_layout(w):
    apply_theme(w)
    w.selected_count=tk.StringVar(value='0 / 0 已选')
    w.view_note=tk.StringVar(value='自动跟随')
    w.sample_note=tk.StringVar(value='等待采样 · 最近600点')
    top=ttk.Frame(w,style='Surface.TFrame',padding=(12,12,12,8));top.pack(fill='x')
    top.columnconfigure(0,weight=1)
    left=ttk.Frame(top,style='Surface.TFrame');left.grid(row=0,column=0,sticky='new')
    axis=ttk.LabelFrame(left,text='曲线设置',padding=8);axis.pack(fill='x')
    w.metric=tk.StringVar(value='温度 (K)')
    w.metric_choice=ttk.Combobox(axis,textvariable=w.metric,
        values=[f'{title} ({unit})' for title,unit,_ in METRICS.values()],width=12,state='readonly')
    w.metric_choice.pack(side='left',padx=(0,8))
    w.metric_choice.bind('<<ComboboxSelected>>',lambda e:w.choose_metric())
    ttk.Label(axis,text='时间(s)',style='Card.TLabel').pack(side='left',padx=(0,4))
    w.entries=[]
    for i in range(2):
        if i:ttk.Label(axis,text='—',style='Muted.TLabel').pack(side='left',padx=3)
        value=tk.StringVar();w.entries.append(value)
        entry=ttk.Entry(axis,textvariable=value,width=7);entry.pack(side='left')
        entry.bind('<Return>',lambda e:w.apply_bounds())
    w.apply_range_button=ttk.Button(axis,text='应用范围',width=14,style='Primary.TButton',command=w.apply_bounds)
    w.apply_range_button.pack(side='left',padx=6)
    w.reset_range_button=ttk.Button(axis,text='恢复自动范围',width=14,style='Primary.TButton',command=w.reset_view)
    w.reset_range_button.pack(side='left',padx=(0,6))
    w.follow_control=ttk.Checkbutton(axis,text='自动跟随',variable=w.follow,style='Card.TCheckbutton',command=w.toggle_follow)
    w.follow_control.pack(side='left')
    ttk.Separator(axis,orient='vertical').pack(side='left',fill='y',padx=8)
    ttk.Label(axis,text='显示',style='Card.TLabel').pack(side='left',padx=(0,4))
    w.visible={key:tk.BooleanVar(value=True) for key in METRICS}
    w.graph_controls={}
    for key,label in (('temperature','温度'),('resistance','电阻'),('power','功率')):
        toggle=ttk.Checkbutton(axis,text=label,variable=w.visible[key],style='Card.TCheckbutton',command=w.arrange)
        toggle.pack(side='left',padx=(0,4))
        w.graph_controls[key]=toggle
    body=ttk.Frame(w,style='Surface.TFrame',padding=(12,0,12,0));body.pack(fill='both',expand=True)
    sidebar=ttk.LabelFrame(body,text='测温通道',padding=12);sidebar.pack(side='left',fill='y',padx=(0,10))
    ttk.Label(sidebar,text='点击通道即可多选',style='Muted.TLabel',wraplength=190).pack(anchor='w',pady=(3,10))
    selection=ttk.Frame(sidebar,style='Card.TFrame');selection.pack(fill='x',pady=(0,8))
    ttk.Button(selection,text='全选',command=w.select_all,padding=(6,3)).pack(side='left')
    ttk.Button(selection,text='清空',command=w.select_none,padding=(6,3)).pack(side='left',padx=(6,0))
    listing=ttk.Frame(sidebar,style='Card.TFrame');listing.pack(fill='both',expand=True)
    w.channel_list=tk.Listbox(listing,selectmode='multiple',exportselection=False,width=21,
        font=('Microsoft YaHei UI',11),borderwidth=0,highlightthickness=0,activestyle='none')
    w.channel_list.pack(side='left',fill='both',expand=True)
    scroll=ttk.Scrollbar(listing,command=w.channel_list.yview);scroll.pack(side='right',fill='y')
    w.channel_list.configure(yscrollcommand=scroll.set)
    w.channel_list.bind('<<ListboxSelect>>',lambda e:w.invalidate())
    horizontal=ttk.Scrollbar(sidebar,orient='horizontal',command=w.channel_list.xview)
    horizontal.pack(fill='x')
    w.channel_list.configure(xscrollcommand=horizontal.set)
    ttk.Label(sidebar,textvariable=w.selected_count,style='Muted.TLabel').pack(anchor='w',pady=(8,14))
    w.chart_area=ttk.Frame(body,style='Surface.TFrame');w.chart_area.pack(side='left',fill='both',expand=True)
    w.chart_area.columnconfigure(0,weight=1)
    w.frames={};w.canvases={}
    for key,(title,unit,_) in METRICS.items():
        frame=ttk.LabelFrame(w.chart_area,text=f'{title} / {unit}',padding=(10,8,10,4))
        heading=ttk.Frame(frame,style='Card.TFrame');heading.pack(fill='x')
        ttk.Label(heading,text='滚轮缩放 · 拖动平移',style='Muted.TLabel').pack(side='right')
        canvas=tk.Canvas(frame,height=135,bg='#f8fafc',highlightthickness=0)
        canvas.pack(fill='both',expand=True)
        w.frames[key],w.canvases[key]=frame,canvas
        canvas.bind('<Configure>',lambda e:w.invalidate())
        canvas.bind('<MouseWheel>',lambda e,k=key:w.wheel(k,e))
        canvas.bind('<ButtonPress-1>',lambda e,k=key:w.begin_drag(k,e))
        canvas.bind('<B1-Motion>',lambda e,k=key:w.drag(k,e))
        canvas.bind('<Double-Button-1>',lambda e:w.reset_view())
        canvas.bind('<Motion>',lambda e,k=key:w.hover(k,e))
        canvas.bind('<Leave>',lambda e,k=key:w.leave_chart(k))
    w.empty_chart=ttk.Label(w.chart_area,text='请在顶部勾选要显示的图表',style='Footer.TLabel',anchor='center')
    footer=ttk.Frame(w,style='Surface.TFrame',padding=(12,7));footer.pack(fill='x')
    ttk.Label(footer,textvariable=w.hint,style='Footer.TLabel').pack(side='left',fill='x',expand=True)
    ttk.Label(footer,textvariable=w.sample_note,style='Footer.TLabel').pack(side='right',padx=(8,0))
    ttk.Label(footer,textvariable=w.view_note,style='Footer.TLabel').pack(side='right',padx=(8,0))
    w.stats_panel=ttk.Frame(w,style='Card.TFrame',padding=(12,6))
    ttk.Label(w.stats_panel,textvariable=w.stats,style='Muted.TLabel',wraplength=1150).pack(anchor='w')
