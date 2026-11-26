"""Viewport, GUI interaction, and process isolation tests; no instrument access."""
import os
import queue
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from plotting.model import bounds_for, validate_bounds, zoom, pan, clipped_lines
from plotting.process import PlotProcess
from plotting.window import CurveWindow
from ui.plots import PlotMixin
from runtime import RunControls


def snapshot(session=1):
    return dict(session_id=session,channels=[101,102],
        history={101:[(0,280,100),(1,281,101),(2,None,None),(3,283,103)],
                 102:[(0,285,102),(1,286,103)]},power=[(0,1),(1,2)],
        status='模拟采集中',readout='实测功率 2 W',stats='样本 3',
        stop_drive_allowed=True,stop_acquisition_allowed=True)


class ViewportTests(unittest.TestCase):
    def test_bounds_zoom_pan_and_invalid_limits(self):
        b=bounds_for([('101','blue',[(0,2),(1,None),(2,4)])],.5)
        self.assertEqual(b,(0,2,1.5,4.5))
        self.assertEqual(zoom((0,10,0,10),.5), (2.5,7.5,2.5,7.5))
        self.assertEqual(pan((0,10,0,10),2,-1),(2,12,-1,9))
        for values in ((1,1,0,1),(0,1,3,2),(0,float('inf'),0,1),(0,1,0,'nan')):
            with self.assertRaises(ValueError):validate_bounds(values)

    def test_clipping_and_invalid_data_do_not_bridge_gaps(self):
        lines=list(clipped_lines([(-1,5),(5,5),(11,5)],(0,10,0,10),(0,0,100,100)))
        self.assertEqual(lines,[[0,50,50,50,100,50]])
        lines=list(clipped_lines([(0,0),(1,1),(2,None),(3,3),(4,4)],(0,4,0,4),(0,0,100,100)))
        self.assertEqual(len(lines),2)
        self.assertEqual(lines[0], [0,100,25,75])
        self.assertEqual(lines[1], [75,25,100,0])


class WindowTests(unittest.TestCase):
    def test_visible_canvases_render(self):
        w=CurveWindow(queue.Queue(),queue.Queue(),queue.Queue())
        try:
            data=snapshot();data['alert']=True
            w.update_snapshot(data)
            w.update()
            w.poll()
            for key,canvas in w.canvases.items():
                self.assertGreater(len(canvas.find_all()),10)
                self.assertTrue(canvas.winfo_ismapped())
            w.visible['resistance'].set(False);w.arrange();w.update()
            self.assertFalse(w.canvases['resistance'].winfo_ismapped())
        finally:w.destroy()

    def test_click_selection_hover_and_empty_chart_recovery(self):
        w=CurveWindow(queue.Queue(),queue.Queue(),queue.Queue())
        try:
            w.update_snapshot(snapshot());w.update()
            self.assertEqual(str(w.channel_list.cget('selectmode')),'multiple')
            box=w.channel_list.bbox(0)
            w.channel_list.event_generate('<Button-1>',x=box[0]+8,y=box[1]+4)
            w.channel_list.event_generate('<ButtonRelease-1>',x=box[0]+8,y=box[1]+4)
            w.update()
            self.assertEqual(w.channel_list.curselection(),(1,))
            w.select_all();w.poll();w.update()
            from plotting.render import chart_rect
            left,top,right,bottom=chart_rect(w.canvases['temperature'])
            w.hover('temperature',SimpleNamespace(x=(left+right)/2,y=(top+bottom)/2))
            self.assertTrue(w.canvases['temperature'].find_withtag('cursor'))
            self.assertIn('101:',w.hint.get())
            w.leave_chart('temperature')
            self.assertFalse(w.canvases['temperature'].find_withtag('cursor'))
            for flag in w.visible.values():flag.set(False)
            w.arrange();w.update()
            self.assertTrue(w.empty_chart.winfo_ismapped())
            w.metric.set('温度 (K)');w.choose_metric();w.update()
            self.assertTrue(w.canvases['temperature'].winfo_ismapped())
        finally:w.destroy()

    def test_selection_manual_bounds_and_follow(self):
        inbox,commands,requests=queue.Queue(),queue.Queue(),queue.Queue()
        w=CurveWindow(inbox,commands,requests)
        w.withdraw()
        try:
            w.update_snapshot(snapshot());w.update_idletasks()
            self.assertEqual(len(w.series('temperature')),2)
            w.select_none();self.assertEqual(w.series('temperature'),[])
            w.channel_list.selection_set(1)
            self.assertEqual(w.series('temperature')[0][0],'102')
            self.assertEqual(len(w.entries),2)
            expected=(0,10,*w.current_bounds('temperature')[2:])
            for var,value in zip(w.entries,(0,10)):var.set(str(value))
            w.apply_bounds()
            self.assertFalse(w.follow.get())
            self.assertEqual(w.current_bounds('temperature'),expected)
            w.update_snapshot(snapshot())
            self.assertEqual(w.current_bounds('temperature'),expected)
            w.wheel('temperature',SimpleNamespace(delta=120,x=200,y=80))
            self.assertLess(w.current_bounds('temperature')[1]-w.current_bounds('temperature')[0],10)
            w.begin_drag('temperature',SimpleNamespace(x=200,y=80))
            old=w.current_bounds('temperature')
            w.drag('temperature',SimpleNamespace(x=220,y=90))
            self.assertNotEqual(w.current_bounds('temperature'),old)
            self.assertEqual(requests.get_nowait(),('opened',None))
            self.assertTrue(requests.empty())
            w.reset_view();self.assertTrue(w.follow.get())
            w.update_snapshot(snapshot(2));self.assertTrue(w.follow.get())
        finally:w.destroy()


class BridgeTests(unittest.TestCase):
    def test_stale_commands_and_sampling_interlock_are_enforced_by_main(self):
        class Host(PlotMixin):pass
        h=Host();h.plot_session_id=2;h.closing=False
        h.drive_busy=h.acquisition_busy=h.heater_busy=True
        h.controls=RunControls(True,True,heater=True)
        h.stop_drive=Mock();h.stop_acquisition=Mock()
        h.plot_process=SimpleNamespace(poll=lambda:[('stop_drive',1),('stop_acquisition',2),('stop_drive',2)],
                                      alive=True,last_sent=time.monotonic())
        h.poll_curves()
        h.stop_drive.assert_called_once_with()
        h.stop_acquisition.assert_not_called()

    def test_main_window_reopens_with_current_history(self):
        from unittest.mock import patch
        from ui.app import App
        class Viewer:
            def __init__(self):self.alive=True;self.sent=[];self.focused=False
            def publish(self,data):self.sent.append(data)
            def command(self,name):self.focused=name=='focus'
            def close(self):self.alive=False
        app=App();app.withdraw()
        app.history={101:[(1,280,100)]}
        try:
            with patch('ui.plots.PlotProcess',side_effect=Viewer):
                app.curves_button.invoke();old=app.plot_process
                self.assertEqual(old.sent[-1]['history'][101],[(1,280,100)])
                app.open_curves();self.assertIs(app.plot_process,old);self.assertTrue(old.focused)
                old.alive=False
                app.history[101].append((2,281,101))
                app.open_curves()
                self.assertIsNot(app.plot_process,old)
                self.assertEqual(len(app.plot_process.sent[-1]['history'][101]),2)
                self.assertFalse(app.controls.drive.is_set())
                self.assertFalse(app.controls.acquisition.is_set())
        finally:app.destroy()

    def test_closed_window_reopens_at_latest_600_samples(self):
        from unittest.mock import patch
        from ui.app import App
        class Viewer:
            def __init__(self):self.alive=True;self.closed=False;self.sent=[]
            def publish(self,data):self.sent.append(data)
            def poll(self):return []
            def command(self,name):pass
            def close(self):self.alive=False;self.closed=True
        app=App();app.withdraw()
        app.controls.start_acquisition();app.acquisition_busy=True
        try:
            with patch('ui.plots.PlotProcess',side_effect=Viewer):
                app.curves_button.invoke()
                app.plot_process.close()
                app.poll_curves()
                self.assertIsNone(app.plot_process)
                for i in range(700):
                    app.events.put(('sample',dict(temperatures={101:0.,102:1.},
                        resistances={101:100.,102:100.4},sample_elapsed_s={101:i,102:i+.1},
                        elapsed_min=i/60,power=1.,voltage=1.,current=1.,setting=1.)))
                app.poll()
                self.assertEqual(len(app.history[101]),200)
                # Drain across bounded UI ticks so a producer cannot starve Tk.
                while not app.events.empty():
                    app.poll()
                self.assertTrue(app.controls.acquisition.is_set())
                app.curves_button.invoke()
                latest=app.plot_process.sent[-1]
                for ch,offset in ((101,0),(102,.1)):
                    points=latest['history'][ch]
                    self.assertEqual(len(points),600)
                    self.assertAlmostEqual(points[0][0],100+offset)
                    self.assertAlmostEqual(points[-1][0],699+offset)
                self.assertEqual(len(latest['power']),600)
                self.assertEqual(latest['power'][0][0],100)
                self.assertEqual(latest['power'][-1][0],699)
        finally:app.destroy()

    def test_display_queue_stall_never_blocks_publisher(self):
        viewer=PlotProcess.__new__(PlotProcess)
        viewer.process=SimpleNamespace(is_alive=lambda:True)
        viewer.closed=False;viewer.snapshots=queue.Queue(maxsize=1);viewer.last_sent=0
        self.assertTrue(viewer.publish(snapshot()))
        started=time.monotonic()
        for _ in range(100):self.assertFalse(viewer.publish(snapshot()))
        self.assertLess(time.monotonic()-started,.5)
        self.assertEqual(viewer.snapshots.qsize(),1)


class ProcessTests(unittest.TestCase):
    def wait_open(self,viewer):
        deadline=time.monotonic()+8
        while time.monotonic()<deadline:
            if any(k=='opened' for k,v in viewer.poll()):return
            if not viewer.process.is_alive():self.fail('曲线进程提前退出')
            time.sleep(.02)
        self.fail('曲线窗口启动超时')

    def test_close_and_reopen_window_restores_history_without_stop_requests(self):
        for _ in range(2):
            viewer=PlotProcess()
            try:
                self.assertNotEqual(viewer.process.pid,os.getpid())
                self.wait_open(viewer)
                self.assertTrue(viewer.publish(snapshot()))
                viewer.command('focus');viewer.command('close')
                events=[];deadline=time.monotonic()+5
                while viewer.process.is_alive() and time.monotonic()<deadline:
                    events.extend(viewer.poll());time.sleep(.02)
                events.extend(viewer.poll())
                self.assertFalse(viewer.process.is_alive())
                self.assertFalse(any(k.startswith('stop_') for k,v in events))
            finally:viewer.close()


if __name__=='__main__':unittest.main()
