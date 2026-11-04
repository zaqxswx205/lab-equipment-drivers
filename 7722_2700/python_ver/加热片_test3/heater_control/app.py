"""7722制冷机交流驱动与2700 Pt100采集界面。"""
import argparse

from ui import App

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--smoke', action='store_true', help='构建界面后自动关闭，不连接硬件')
    args = parser.parse_args()
    app = App()
    if args.smoke:
        app.after(500, app.destroy)
    app.mainloop()
