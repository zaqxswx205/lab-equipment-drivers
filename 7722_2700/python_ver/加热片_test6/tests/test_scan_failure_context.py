import queue
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from ui.scan import ScanMixin, selected_device_description
from panel_identification import PanelIdentificationError


class Variable:
    def __init__(self,value=''): self.value=value
    def get(self): return self.value
    def set(self,value): self.value=value


class ScanFailureTests(unittest.TestCase):
    def test_two_identical_models_are_distinguished_by_port_serial_and_purpose(self):
        one=('COM7','7722电源','ITECH,IT-M7722,SN_A,1',{'baud':9600})
        two=('COM8','7722电源','ITECH,IT-M7722,SN_B,1',{'baud':19200})
        a=selected_device_description(one,'power_port')
        b=selected_device_description(two,'heater_port')
        self.assertIn('COM7',a)
        self.assertIn('SN_A',a)
        self.assertIn('交流驱动（AC）',a)
        self.assertIn('COM8',b)
        self.assertIn('SN_B',b)
        self.assertIn('加热负载（DC）',b)
        self.assertIn('19200 baud',b)

    def test_worker_failure_retains_original_device_in_all_status_destinations(self):
        for confirmed in (True,False):
            with self.subTest(confirmed=confirmed):
                record=('COM8','7722电源','ITECH,IT-M7722,SN_B,1',{'baud':9600})
                class Host(ScanMixin):
                    scanning=False
                    closing=False
                    off_failed=False
                    run_finished=True
                    def idle(self): return True
                    def refresh_run_buttons(self): pass
                    def append(self,text): self.logged=text
                    def report_status(self,text,level,**kwargs): self.alert=text
                host=Host()
                host.scan_table=SimpleNamespace(selection=lambda:('selected',))
                host.scan_results={'selected':record}
                host.variables={'timeout':(Variable('3'),float)}
                host.scan_status=Variable()
                host.events=queue.Queue()
                class Job:
                    def __init__(self,kind,emit,**kwargs): self.emit,self.options=emit,kwargs
                    def start(self):
                        self.emit('scan_panel_error',dict(key=self.options['key'], record=self.options['record'],
                                  mode=self.options['mode'], error='模式回读失败', uncertain=not confirmed))
                with patch('ui.scan.DiscoveryJob',Job):
                    host.apply_scan_selection('heater_port')
                # Changing selection after the worker finishes must not relabel it.
                host.scan_results['selected']=('COM9','7722电源','OTHER',{'baud':9600})
                host.handle_scan_event(*host.events.get_nowait())
                for message in (host.scan_status.get(),host.logged,host.alert):
                    self.assertIn('COM8',message)
                    self.assertIn('SN_B',message)
                    self.assertIn('加热负载（DC）',message)
                    self.assertIn('模式回读失败',message)
                    self.assertNotIn('COM9',message)
                self.assertEqual(host.off_failed,not confirmed)
                if not confirmed:
                    self.assertIn('COM8输出关闭未确认',host.alert)
