import unittest
from stability import Stability


class StabilityTests(unittest.TestCase):
    def test_duration_starts_after_power_ready_and_resets_on_loss(self):
        check = Stability('duration', duration_s=10)
        self.assertFalse(check.update(0, 100, False))
        self.assertFalse(check.update(10, 100))
        self.assertFalse(check.update(19, 100))
        self.assertTrue(check.update(20, 100))
        self.assertFalse(check.update(21, 100, False))
        self.assertFalse(check.update(30, 100))
        self.assertTrue(check.update(40, 100))

    def test_peak_to_peak_requires_full_window_and_old_excursion_expires(self):
        check = Stability('peak_to_peak', window_s=10, peak_to_peak_k=.2)
        for at, t in ((0,100), (5,101), (10,100), (15,100.1)):
            self.assertFalse(check.update(at, t))
        self.assertTrue(check.update(20,100))

    def test_single_sample_and_manual_never_auto_complete(self):
        self.assertFalse(Stability('peak_to_peak').update(10000,100))
        check = Stability('manual')
        self.assertFalse(check.update(0,100))
        self.assertFalse(check.update(10000,100))

    def test_invalid_configuration_rejected(self):
        for kwargs in ({'window_s':0}, {'peak_to_peak_k':float('nan')}, {'mode':'pid'}):
            with self.assertRaises(ValueError):
                Stability(**kwargs)
