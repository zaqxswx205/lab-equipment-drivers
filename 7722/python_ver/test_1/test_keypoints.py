"""按设定电压触发关键点，与普通记录周期独立。"""
import csv
import tempfile
import unittest
from unittest.mock import Mock

from itech7722_config import AppConfig
from itech7722_device import ITech7722, SimTransport
from itech7722_engine import Engine
from itech7722_recorder import Recorder, Sample


class KeypointTests(unittest.TestCase):
    def test_setpoint_matches_even_when_measurement_differs(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = AppConfig()
            cfg.keypoints = {5: []}
            rec = Recorder(cfg, tmp)
            rec.start()
            sample = Sample("t", 0, 1, voltage=5.05, current=0.17, power=0.85, power_factor=0.99)
            self.assertTrue(rec.log(sample, set_voltage=5))
            self.assertFalse(rec.log(sample, set_voltage=5))
            self.assertFalse(rec.log(sample, set_voltage=5.01))
            self.assertTrue(rec.log(sample, set_voltage=5))
            with open(rec.key_path, encoding="utf-8-sig", newline="") as fh:
                rows = list(csv.reader(fh))
            self.assertEqual(len(rows), 3)
            self.assertEqual(rows[1][0], "5")
            self.assertEqual(rows[1][2], "5.050")

    def test_measurement_alone_does_not_trigger_nominal_keypoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = AppConfig()
            cfg.keypoints = {5: []}
            rec = Recorder(cfg, tmp)
            rec.start()
            self.assertFalse(rec.log(Sample("t", 0, 1, voltage=5)))
            self.assertFalse(rec.log(Sample("t", 0, 1, voltage=5), set_voltage=5.01))

    def test_runtime_rules_apply_on_samples_between_regular_log_ticks(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = AppConfig()
            rec = Recorder(cfg, tmp)
            rec.start()
            engine = Engine(Mock())
            engine.device = ITech7722(SimTransport())
            engine.device.setting["value"] = 5
            engine.running = True
            m = dict(voltage=5.05, current=0.17, power=0.85, power_factor=0.99)
            limits = dict(cfg.limits, mode="AC")

            def sample():
                engine._sample(rec, limits, 1, "", "CI", 0.5, measurements=m, write_record=False)

            engine.update_keypoints({5: []})
            sample()
            sample()
            engine.update_keypoints({})
            sample()
            engine.update_keypoints({5: []})
            sample()
            with open(rec.log_path, encoding="utf-8-sig", newline="") as fh:
                self.assertEqual(len(list(csv.reader(fh))), 1)
            with open(rec.key_path, encoding="utf-8-sig", newline="") as fh:
                rows = list(csv.reader(fh))
                self.assertEqual(len(rows), 3)
                self.assertEqual(rows[1][0], "5")

    def test_zero_voltage_keypoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = AppConfig()
            cfg.keypoints = {0: []}
            rec = Recorder(cfg, tmp)
            rec.start()
            self.assertTrue(rec.log(Sample("t", 0, 1, voltage=0.01), set_voltage=0))

    def test_keypoint_with_missing_voltage_does_not_hide_protection_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = AppConfig()
            cfg.keypoints = {5: []}
            rec = Recorder(cfg, tmp)
            rec.start()
            engine = Engine(Mock())
            engine.device = ITech7722(SimTransport())
            engine.device.setting["value"] = 5
            trip = engine._sample(rec, dict(cfg.limits, mode="AC"), 1, "", "CV", 5,
                                  measurements=dict(voltage=None, current=0, power=0, power_factor=None))
            self.assertIn("回读无效", trip)
            self.assertIn("回读无效", engine.last_error)
            self.assertFalse(engine.output_on)

    def test_rising_feedback_lands_on_keypoints_without_extending_voltage_limit(self):
        cfg = AppConfig()
        cfg.keypoints = {1: [], 2: [], 5: []}
        rec = Recorder(cfg)
        self.assertEqual(rec.next_rising_keypoint(0.9, 1.1), 1)
        self.assertEqual(rec.next_rising_keypoint(1, 2.1), 2)
        self.assertEqual(rec.next_rising_keypoint(2, 2.2), 2.2)
        self.assertEqual(rec.next_rising_keypoint(2, 1), 1)

    def test_step_arithmetic_can_reach_exact_integer_setpoints(self):
        from itech7722_feedback import VoltageFeedback
        cfg = AppConfig()
        controller = VoltageFeedback("CI", 0.5, cfg.limits, cfg.feedback)
        values = []
        for n in range(30):
            v = controller.voltage
            values.append(controller.update(dict(voltage=v, current=v / 30, power=v * v / 30), n * 0.2)[0])
        self.assertIn(1.0, values)
        self.assertTrue(all(v <= 5 for v in values))

    def test_adding_unrelated_rule_does_not_duplicate_active_keypoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = AppConfig()
            cfg.keypoints = {5: []}
            rec = Recorder(cfg, tmp)
            rec.start()
            sample = Sample("t", 0, 1, voltage=5.05)
            self.assertTrue(rec.log(sample, set_voltage=5))
            rec.update_keypoints({5: [], 6: []})
            self.assertFalse(rec.log(sample, set_voltage=5))


if __name__ == "__main__":
    unittest.main()
