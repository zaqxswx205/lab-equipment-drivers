"""Manual target selection, independent of the on-disk drive profile."""
import tkinter as tk
from tkinter import ttk
from drive_profile import Profile, Step


def ask_manual_drive(owner, cfg):
    win = tk.Toplevel(owner)
    win.title('开始手动驱动')
    win.transient(owner)
    win.resizable(False, False)
    body = ttk.Frame(win, padding=16)
    body.pack(fill='both', expand=True)
    mode = tk.StringVar(win, value='恒定电压(V)')
    target = tk.StringVar(win, value='0')
    result = []
    ttk.Label(body, text='手动驱动保持所填目标，不执行驱动方案或性能测试。').grid(row=0,column=0,columnspan=2,pady=(0,12))
    ttk.Label(body,text='控制方式').grid(row=1,column=0,sticky='w')
    ttk.Combobox(body,textvariable=mode,values=('恒定电压(V)','恒定功率(W)'),state='readonly',width=18).grid(row=1,column=1,pady=5)
    ttk.Label(body,text='目标值').grid(row=2,column=0,sticky='w')
    entry = ttk.Entry(body,textvariable=target,width=20)
    entry.grid(row=2,column=1,pady=5)
    message = tk.StringVar(win)
    ttk.Label(body,textvariable=message,foreground='#b42318',wraplength=400).grid(row=3,column=0,columnspan=2,pady=5)
    def accept():
        try:
            profile = Profile('time',[Step(0,'voltage' if mode.get()=='恒定电压(V)' else 'power',float(target.get()))])
            profile.validate(cfg)
        except ValueError as exc:
            message.set(str(exc))
            return
        result.append(profile)
        win.destroy()
    ttk.Button(body,text='取消',command=win.destroy).grid(row=4,column=0,pady=(10,0))
    ttk.Button(body,text='下一步：复核保护参数',command=accept).grid(row=4,column=1,pady=(10,0))
    win.grab_set()
    entry.focus_set()
    owner.wait_window(win)
    return result[0] if result else None
