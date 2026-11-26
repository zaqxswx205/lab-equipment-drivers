"""Use the original system palette; keep only typography/layout style aliases."""
from tkinter import ttk


def apply_theme(root):
    style=ttk.Style(root)
    if 'vista' in style.theme_names():style.theme_use('vista')
    background=style.lookup('TFrame','background') or root.cget('background')
    foreground=style.lookup('TLabel','foreground') or 'black'
    root.configure(background=background)
    for name in ('Surface.TFrame','Card.TFrame'):
        style.configure(name,background=background)
    style.configure('Card.TLabel',background=background,foreground=foreground)
    style.configure('Muted.TLabel',background=background,foreground='#475569')
    style.configure('Heading.TLabel',background=background,foreground=foreground,font=('Microsoft YaHei UI',10,'bold'))
    style.configure('Footer.TLabel',background=background,foreground='#475569',font=('Microsoft YaHei UI',8))
    style.configure('Primary.TButton',padding=(8,5))
    style.configure('Card.TCheckbutton',background=background,padding=(0,2))
