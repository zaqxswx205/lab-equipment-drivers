import unittest
from ui.notices import StatusCenter, error


class StatusCenterTests(unittest.TestCase):
    def test_all_device_states_share_one_display(self):
        center=StatusCenter()
        center.update('drive','交流驱动未运行')
        center.update('heater','加热未运行')
        text=center.update('acquisition','2700未采集')
        for item in ('交流驱动未运行','加热未运行','2700未采集'):
            self.assertIn(item,text)

    def test_error_is_not_erased_by_regular_sampling_or_scan(self):
        center=StatusCenter()
        center.update('notice','2700通信失败','error')
        center.update('drive','正在关断')
        center.update('notice','采集已停止')
        text=center.update('scan','已扫描串口COM3')
        self.assertIn('2700通信失败',text)
        self.assertIn('已扫描串口COM3',text)
        self.assertIn('正在关断',text)

    def test_child_validation_error_routes_to_application(self):
        class Owner:
            def __init__(self): self.calls=[]
            def report_status(self,*args): self.calls.append(args)
        class Child:
            def __init__(self,owner): self.master=owner
        owner=Owner()
        error(Child(owner),'配置错误','目标值无效')
        self.assertEqual(owner.calls,[('配置错误：目标值无效','error')])
