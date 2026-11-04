"""界面分区回归：启动/停止归入“输出设定”，打开记录文件归入“数据记录”，且 grid 不得叠格。

只构建窗口，不连接任何硬件（配置走 sim 分支）。
"""
import unittest
import tkinter as tk
from tkinter import ttk

from itech7722_config import AppConfig
from itech7722_driver import App


def walk(widget):
    yield widget
    for child in widget.winfo_children():
        yield from walk(child)


def grid_cells(widget):
    """把父容器的 grid 子控件按 rowspan/columnspan 展开成 {(row, col): [控件]}。"""
    cells = {}
    for child in widget.grid_slaves():
        info = child.grid_info()
        if not info:
            continue
        row, column = int(info["row"]), int(info["column"])
        for r in range(row, row + int(info.get("rowspan", 1))):
            for c in range(column, column + int(info.get("columnspan", 1))):
                cells.setdefault((r, c), []).append(child)
    return cells


class UILayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()
        cfg = AppConfig()
        cfg.connection["type"] = "sim"
        cls.app = App(cls.root, cfg)
        cls.root.update_idletasks()
        cls.root.update()

    @classmethod
    def tearDownClass(cls):
        cls.app.shutdown()
        cls.root.destroy()

    def group(self, text):
        for widget in walk(self.root):
            if isinstance(widget, ttk.LabelFrame) and widget.cget("text") == text:
                return widget
        self.fail(f"未找到分组框: {text}")

    def containers(self, widget):
        out = []
        parent = widget.master
        while parent is not None:
            out.append(parent)
            parent = parent.master
        return out

    def buttons(self, text):
        return [w for w in walk(self.root)
                if isinstance(w, ttk.Button) and w.cget("text") == text]

    def test_start_stop_belong_to_output_setting_group(self):
        box = self.group("输出设定")
        for text in ("启动", "停止"):
            with self.subTest(text=text):
                found = self.buttons(text)
                self.assertEqual(len(found), 1, f"{text} 按钮应只有一个，实际 {len(found)} 个")
                self.assertIn(box, self.containers(found[0]), f"{text} 不在“输出设定”内")

    def test_open_logs_belongs_to_recording_group(self):
        box = self.group("数据记录")
        found = self.buttons("打开记录文件")
        self.assertEqual(len(found), 1)
        self.assertIn(box, self.containers(found[0]))

    def test_top_row_action_bar_is_gone(self):
        """原整宽按钮条是 top 上 columnspan=3 的子控件；分区后不应再有跨三行的顶层条。"""
        paned = next(w for w in self.root.winfo_children() if isinstance(w, ttk.Panedwindow))
        top = paned.winfo_children()[0]
        spans = [int(child.grid_info().get("columnspan", 1)) for child in top.grid_slaves()]
        self.assertEqual(sorted(spans), [1, 1, 1], f"顶层应只剩三个分区面板，实际 columnspan={spans}")

    def test_no_grid_cell_collisions_anywhere(self):
        for widget in walk(self.root):
            if not isinstance(widget, (ttk.Frame, ttk.LabelFrame)):
                continue
            for cell, occupants in grid_cells(widget).items():
                if len(occupants) > 1:
                    names = ", ".join(f"{type(o).__name__}(text={o.cget('text')!r})"
                                      if "text" in o.keys() else type(o).__name__
                                      for o in occupants)
                    self.fail(f"{widget.winfo_class()} 的单元格 {cell} 被多个控件占用: {names}")

    def test_run_buttons_toggle_with_state(self):
        """启动/停止仍在 _run_buttons 控制下，迁移父容器后状态依旧可切换。"""
        self.assertEqual(str(self.app.btn_stop["state"]), "disabled")
        self.app._run_buttons(True)
        self.root.update_idletasks()
        self.assertEqual(str(self.app.btn_start["state"]), "disabled")
        self.assertEqual(str(self.app.btn_stop["state"]), "normal")
        self.app._run_buttons(False)
        self.root.update_idletasks()
        self.assertEqual(str(self.app.btn_start["state"]), "normal")
        self.assertEqual(str(self.app.btn_stop["state"]), "disabled")

    def test_group_panels_are_not_clipped(self):
        """分区面板必须真的按内容撑开，避免控件被裁掉（历史问题：文字/按钮叠格）。"""
        for text in ("设备连接", "输出设定", "数据记录"):
            box = self.group(text)
            with self.subTest(group=text):
                self.assertGreater(box.winfo_reqwidth(), 50)
                self.assertGreater(box.winfo_reqheight(), 50)
                for child in box.grid_slaves():
                    self.assertGreaterEqual(child.winfo_reqheight(), 1)

    def test_output_mode_switch_still_hides_panels_inside_group(self):
        """序列/定值切换要真的改变显示状态，因此这里必须把窗口映射出来再断言。"""
        self.root.deiconify()
        self.addCleanup(self.root.withdraw)
        box = self.group("输出设定")
        self.app.var_outmode.set("seq")
        self.app._on_outmode_change()
        self.root.update()
        self.assertFalse(self.app.hold_box.winfo_ismapped())
        self.assertTrue(self.app.seq_box.winfo_ismapped())
        self.assertTrue(self.app.btn_start.winfo_ismapped())
        self.assertTrue(self.app.btn_open_logs.winfo_ismapped())
        for widget in box.grid_slaves():
            self.assertIn(int(widget.grid_info()["row"]), {0, 1, 2, 3, 4, 5},
                          f"意外的行号 {widget.grid_info()}")
        self.app.var_outmode.set("hold")
        self.app._on_outmode_change()
        self.root.update()
        self.assertTrue(self.app.hold_box.winfo_ismapped())
        self.assertFalse(self.app.seq_box.winfo_ismapped())

    def test_moved_buttons_are_not_clipped(self):
        """按钮迁移父容器后要按自身需求高度完整显示，防止被分区裁掉。"""
        self.root.deiconify()
        self.addCleanup(self.root.withdraw)
        self.root.update()
        for widget in (self.app.btn_start, self.app.btn_stop, self.app.btn_open_logs):
            with self.subTest(text=widget.cget("text")):
                self.assertTrue(widget.winfo_ismapped())
                self.assertGreaterEqual(widget.winfo_height(), widget.winfo_reqheight())
                self.assertGreaterEqual(widget.winfo_width(), 40)


if __name__ == "__main__":
    unittest.main()
