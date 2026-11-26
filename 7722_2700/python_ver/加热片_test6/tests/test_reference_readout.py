"""Reference-channel summary uses the active config and only its own samples."""
from dataclasses import replace
import unittest
from ui.app import App


class ReferenceReadoutTests(unittest.TestCase):
    def setUp(self):
        self.app=App();self.app.withdraw()
        self.app.cfg=replace(self.app.cfg,reference_channel=102,heater_trigger_temperature=-3.15)
        self.app.update_reference_readout()

    def tearDown(self):self.app.destroy()

    def values(self):
        return tuple(self.app.reference_readout_table.item('latest','values'))

    def sample(self,temperatures):
        self.app.events.put(('sample',dict(temperatures=temperatures,
            resistances={ch:100. for ch in temperatures},
            sample_elapsed_s={ch:1. for ch in temperatures},elapsed_min=1/60,power=None)))
        self.app.poll()

    def test_summary_uses_reference_sample_and_active_configuration(self):
        self.assertEqual(self.values(),('102','270.000','--'))
        # Editing inputs must not relabel the active session's sampled channel.
        self.app.variables['reference_channel'][0].set('101')
        self.app.variables['heater_trigger_temperature'][0].set('300')
        self.sample({101:10.,102:20.})
        self.assertEqual(self.values(),('102','270.000','293.150'))
        self.sample({101:-10.})
        self.assertEqual(self.values(),('102','270.000','293.150'))
        for i in range(100):self.app.update_reference_readout(i/10)
        self.assertEqual(self.app.reference_readout_table.get_children(),('latest',))
        self.assertEqual(self.values(),('102','270.000','283.050'))

    def test_invalid_fault_and_stop_do_not_leave_a_valid_looking_temperature(self):
        self.sample({102:None})
        self.assertEqual(self.values()[2],'无效/超范围')
        self.app.events.put(('measurement_fault','2700通信故障'));self.app.poll()
        self.assertEqual(self.values(),('102','270.000','采集失效'))
        self.sample({102:0.})
        self.assertEqual(self.values()[2],'273.150')
        self.app.events.put(('acquisition_done',{'error':''}));self.app.poll()
        self.assertEqual(self.values(),('102','270.000','--'))


if __name__=='__main__':unittest.main()
