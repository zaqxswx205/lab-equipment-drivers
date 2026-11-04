"""闭环控制的确定性负载测试，不连接硬件。"""
import math
import os
import tempfile
import unittest
import time

from itech7722_config import AppConfig, load_config, save_config, validate_run
from itech7722_device import DeviceError, ITech7722, SimTransport
from itech7722_feedback import VoltageFeedback
from itech7722_engine import Engine


class FeedbackTests(unittest.TestCase):
    def regulator(self, control="CI", target=0.5, **options):
        cfg = AppConfig()
        cfg.feedback.update(max_voltage=10, **options)
        return VoltageFeedback(control, target, cfg.limits, cfg.feedback)

    def run_load(self, controller, resistance, start=0, count=100):
        history = []
        for n in range(start, start + count):
            v = controller.voltage
            m = dict(voltage=v, current=v / resistance, power=v * v / resistance)
            value, status = controller.update(m, n * 0.2)
            history.append((value, status))
        return history

    def test_current_converges_and_holds(self):
        regulator = self.regulator()
        history = self.run_load(regulator, 10)
        self.assertAlmostEqual(regulator.voltage / 10, 0.5, delta=0.005)
        self.assertIn("目标已达到", history[-1][1])
        previous = 0
        for voltage, _ in history:
            self.assertLessEqual(voltage - previous, 0.200001)
            previous = voltage

    def test_values_below_target_never_count_as_reached(self):
        for control, target, readings in (("CP", 3, (2.9, 2.99, 2.999999)),
                                           ("CI", 0.5, (0.49, 0.499, 0.499999))):
            for actual in readings:
                with self.subTest(control=control, actual=actual):
                    regulator = self.regulator(control, target)
                    regulator.voltage = 5
                    before = regulator.voltage
                    m = dict(voltage=5, current=actual if control == "CI" else actual / 5,
                             power=actual if control == "CP" else 5 * actual)
                    voltage, status = regulator.update(m, 0)
                    self.assertNotIn("目标已达到", status)
                    self.assertGreater(voltage, before)
                    self.assertLessEqual(voltage - before, regulator.step + 1e-6)

    def test_reached_band_is_only_above_target(self):
        for control, target in (("CP", 3), ("CI", 0.5)):
            regulator = self.regulator(control, target)
            regulator.voltage = 5
            for actual in (target, target + regulator.tolerance * 0.5):
                m = dict(voltage=5, current=actual if control == "CI" else actual / 5,
                         power=actual if control == "CP" else actual * 5)
                _, status = regulator.update(m, 0)
                self.assertIn("目标已达到", status)

    def test_reported_two_watt_upper_ripple_keeps_voltage_unchanged(self):
        regulator = self.regulator("CP", 2)
        regulator.voltage = 7.58
        for n, power in enumerate((2.013, 2.039, 2.036, 2.008, 2.038, 2.015)):
            voltage, status = regulator.update(dict(voltage=7.58, current=0.26, power=power), n * 0.4)
            self.assertEqual(voltage, 7.58)
            self.assertIn("保持电压", status)

    def test_small_upper_spike_is_confirmed_before_leaving_hold(self):
        regulator = self.regulator("CP", 2)
        regulator.voltage = 7.58
        regulator.update(dict(voltage=7.58, current=0.26, power=2.013), 0)
        for now in (0.1, 0.5):
            voltage, status = regulator.update(dict(voltage=7.58, current=0.29, power=2.2), now)
            self.assertEqual(voltage, 7.58)
            self.assertIn("保持电压", status)
        voltage, _ = regulator.update(dict(voltage=7.58, current=0.29, power=2.2), 1.2)
        self.assertLess(voltage, 7.58)

    def test_drop_below_target_exits_hold_and_increases_voltage(self):
        regulator = self.regulator("CP", 2)
        regulator.voltage = 7.58
        regulator.update(dict(voltage=7.58, current=0.26, power=2.013), 0)
        voltage, status = regulator.update(dict(voltage=7.58, current=0.25, power=1.98), 1)
        self.assertGreater(voltage, 7.58)
        self.assertNotIn("目标已达到", status)

    def test_three_watts_converges_from_below_and_tracks_load_without_low_success(self):
        regulator = self.regulator("CP", 3)
        for resistance, start in ((10, 0), (20, 100), (5, 200)):
            for n in range(start, start + 100):
                v = regulator.voltage
                power = v * v / resistance
                _, status = regulator.update(dict(voltage=v, current=v / resistance, power=power), n * 0.2)
                if "目标已达到" in status:
                    self.assertGreaterEqual(power, 3)
                    self.assertLessEqual(power, 3 + regulator.tolerance)
            self.assertGreaterEqual(regulator.voltage ** 2 / resistance, 3)
            self.assertLessEqual(regulator.voltage ** 2 / resistance, 3 + regulator.tolerance)

    def test_zero_voltage_with_real_instrument_offsets_can_start_ramping(self):
        for control, target in (("CI", 0.5), ("CP", 2)):
            for v in (0, 0.001):
                regulator = self.regulator(control, target)
                voltage, status = regulator.update(
                    dict(voltage=v, current=0.0017, power=0.0001), 0)
                self.assertGreater(voltage, 0)
                self.assertLessEqual(voltage, 0.1)
                self.assertIn("升压", status)

    def test_reported_five_volts_point_seventeen_amps_load_reaches_half_amp(self):
        cfg = AppConfig()
        cfg.feedback["max_voltage"] = 20
        regulator = VoltageFeedback("CI", 0.5, cfg.limits, cfg.feedback)
        history = []
        resistance = 5 / 0.17
        for n in range(150):
            voltage = regulator.voltage
            current = voltage / resistance if voltage else 0.0017
            next_v, status = regulator.update(
                dict(voltage=voltage, current=current, power=voltage * current), n * 0.2)
            history.append(next_v)
        self.assertGreater(regulator.voltage, 14)
        self.assertLess(regulator.voltage, 15)
        self.assertAlmostEqual(regulator.voltage / resistance, 0.5, delta=0.005)
        self.assertLessEqual(max(history), 20)

    def test_boundary_is_a_control_error_not_a_communication_error(self):
        from itech7722_feedback import FeedbackError
        regulator = self.regulator()
        with self.assertRaises(FeedbackError):
            self.run_load(regulator, 1e12, count=200)

    def test_current_tracks_lower_and_higher_resistance(self):
        regulator = self.regulator()
        self.run_load(regulator, 10)
        old = regulator.voltage
        history = self.run_load(regulator, 5, start=100)
        self.assertLess(history[0][0], old)
        self.assertAlmostEqual(regulator.voltage / 5, 0.5, delta=0.005)
        self.run_load(regulator, 15, start=200)
        self.assertAlmostEqual(regulator.voltage / 15, 0.5, delta=0.005)

    def test_power_tracks_load_changes(self):
        regulator = self.regulator("CP", 2)
        self.run_load(regulator, 10)
        self.assertAlmostEqual(regulator.voltage ** 2 / 10, 2, delta=0.02)
        self.run_load(regulator, 20, start=100)
        self.assertAlmostEqual(regulator.voltage ** 2 / 20, 2, delta=0.02)
        self.run_load(regulator, 5, start=200)
        self.assertAlmostEqual(regulator.voltage ** 2 / 5, 2, delta=0.02)

    def test_open_load_stops_at_ceiling(self):
        regulator = self.regulator()
        with self.assertRaisesRegex(DeviceError, "边界"):
            self.run_load(regulator, 1e12, count=200)
        self.assertLessEqual(regulator.voltage, 10)

    def test_delayed_voltage_response_does_not_accumulate_ramp(self):
        regulator = self.regulator()
        regulator.voltage = 2.0  # 超过1V低压回读容差的严重偏差仍必须等待/停止
        value, _ = regulator.update(dict(voltage=0, current=0, power=0), 0)
        for n in range(1, 10):
            new, _ = regulator.update(dict(voltage=0, current=0, power=0), n * 0.2)
            self.assertEqual(new, value)
        with self.assertRaisesRegex(DeviceError, "持续偏离"):
            regulator.update(dict(voltage=0, current=0, power=0), 4)

    def test_reported_low_voltage_readback_does_not_freeze_ramp(self):
        cfg = AppConfig()
        cfg.feedback["max_voltage"] = 20
        regulator = VoltageFeedback("CI", 0.5, cfg.limits, cfg.feedback)
        for n in range(150):
            command = regulator.voltage
            # 重现现场：0.1V设定回读约0.7V、电流1.7mA。
            if command == 0:
                m = dict(voltage=0, current=0.0017, power=0)
            elif command < 1:
                m = dict(voltage=0.70 + (n % 4) * 0.01, current=0.0017, power=0)
            else:
                current = command / (5 / 0.17)
                m = dict(voltage=command + 0.05, current=current, power=command * current)
            regulator.update(m, n * 0.4)
        self.assertGreater(regulator.voltage, 14)
        self.assertLessEqual(regulator.voltage, 20)
        self.assertAlmostEqual(regulator.voltage / (5 / 0.17), 0.5, delta=0.005)

    def test_slow_or_unreachable_target_times_out(self):
        regulator = self.regulator(timeout_s=0.1)
        regulator.update(dict(voltage=0, current=0, power=0), 0)
        with self.assertRaisesRegex(DeviceError, "超时"):
            regulator.update(dict(voltage=0.1, current=0.001, power=0.0001), 1)

    def test_feedback_has_no_integral_windup_after_load_change(self):
        regulator = self.regulator()
        self.run_load(regulator, 10)
        regulator.update(dict(voltage=5, current=1, power=5), 21)
        self.assertLess(regulator.voltage, 2.6)

    def test_other_protection_limit_forces_voltage_reduction(self):
        regulator = self.regulator("CP", 100)
        regulator.voltage = 5
        regulator.limits = dict(max_voltage=300, max_current=0.6, max_power=600)
        voltage, status = regulator.update(dict(voltage=5, current=0.59, power=2.95), 0)
        self.assertLess(voltage, 5)
        self.assertIn("保护", status)

    def test_voltage_ceiling_also_respects_global_voltage_protection(self):
        regulator = self.regulator()
        regulator.limits["max_voltage"] = 2
        other = VoltageFeedback("CI", 0.5, regulator.limits, regulator.options)
        self.assertAlmostEqual(other.ceiling, 1.9)

    def test_zero_target_keeps_zero_voltage(self):
        for control in ("CI", "CP"):
            regulator = self.regulator(control, 0)
            self.assertEqual(regulator.update(dict(voltage=0, current=0, power=0), 0)[0], 0)

    def test_invalid_and_reverse_measurements_fail(self):
        for bad in (None, math.nan, math.inf):
            with self.subTest(bad=bad), self.assertRaises(DeviceError):
                self.regulator().update(dict(voltage=0, current=bad, power=0), 0)
        with self.assertRaisesRegex(DeviceError, "反向"):
            self.regulator().update(dict(voltage=1, current=-0.5, power=-0.5), 0)

    def test_strict_feedback_protection_has_no_extra_tolerance(self):
        engine = Engine(None)
        limits = dict(max_voltage=5, max_current=0.6, max_power=3, strict=True)
        for m in (dict(voltage=5.001, current=0, power=0),
                  dict(voltage=0, current=0.6001, power=0),
                  dict(voltage=0, current=0, power=3.001)):
            self.assertTrue(engine._check_protection(m, limits))

    def test_feedback_config_roundtrip(self):
        cfg = AppConfig()
        cfg.feedback.update(max_voltage=12, step_voltage=0.15, interval_ms=300)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "config.ini")
            save_config(path, cfg)
            self.assertEqual(load_config(path).feedback, cfg.feedback)

    def test_feedback_input_validation(self):
        cfg = AppConfig()
        cfg.setting.update(control="CI", value=0.5)
        for key, value in (("max_voltage", 0), ("step_voltage", -1), ("interval_ms", 10),
                           ("timeout_s", math.inf), ("tolerance_rel", 0.5)):
            bad = cfg.copy()
            bad.feedback[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_run(bad, [])
        cfg.setting["value"] = 6
        with self.assertRaisesRegex(ValueError, "95%"):
            validate_run(cfg, [])

    def test_voltage_update_only_writes_voltage(self):
        dev = ITech7722(SimTransport())
        dev.set_protection_limits(300, 6, 600)
        dev.tp.commands.clear()
        dev.set_voltage(1)
        self.assertEqual(dev.tp.commands, ["SOUR:NORM:VOLT:AC 1"])
        with self.assertRaises(DeviceError):
            dev.set_voltage(301)

    def test_tiny_positive_targets_do_not_count_zero_as_reached(self):
        for control in ("CI", "CP"):
            voltage, status = self.regulator(control, 0.001).update(
                dict(voltage=0, current=0, power=0), 0)
            self.assertGreater(voltage, 0)
            self.assertNotIn("达到", status)

    def test_engine_power_feedback_tracks_measured_power_without_power_setting(self):
        class UI:
            def __init__(self):
                self.samples = []

            def log_scpi(self, *args):
                pass

            def post(self, kind, value):
                if kind == "sample":
                    self.samples.append(value[0])

        from itech7722_recorder import Recorder
        cfg = AppConfig()
        cfg.connection["type"] = "sim"
        cfg.setting.update(control="CP", value=0.1)
        cfg.recording["enabled"] = False
        cfg.feedback.update(step_voltage=0.5, interval_ms=50)
        ui = UI()
        engine = Engine(ui)
        engine.connect(cfg)
        try:
            engine.start_run(cfg, [], Recorder(cfg))
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                if any(abs(s.power - 0.1) <= 0.01 for s in ui.samples):
                    break
                time.sleep(0.01)
            else:
                self.fail(f"功率闭环未跟踪到目标: {engine.last_error}")
            self.assertFalse(any(c.startswith("SOUR:POW") for c in engine.device.tp.commands))
            self.assertIn("SOUR:NORM:VOLT:AC 0", engine.device.tp.commands)
        finally:
            engine.disconnect()

    def test_engine_feedback_overlimit_closes_output_before_recording(self):
        from itech7722_recorder import Recorder
        from unittest.mock import Mock
        cfg = AppConfig()
        cfg.connection["type"] = "sim"
        cfg.setting.update(control="CI", value=0.5)
        cfg.recording["enabled"] = False
        ui = Mock()
        engine = Engine(ui)
        engine.connect(cfg)
        recorder = Recorder(cfg)
        engine.device.measure = lambda mode: dict(voltage=1, current=6.001, power=6.001)
        old_log = recorder.log

        def log(*args, **kw):
            self.assertFalse(engine.device.tp.state["output"])
            return old_log(*args, **kw)

        recorder.log = log
        try:
            engine.start_run(cfg, [], recorder)
            engine.thread.join(2)
            self.assertFalse(engine.running)
            self.assertIn("过流", engine.last_error)
            self.assertFalse(engine.output_on)
        finally:
            engine.disconnect()

    def test_missing_hardware_protection_rejects_feedback_before_output(self):
        from itech7722_recorder import Recorder
        from unittest.mock import Mock
        cfg = AppConfig()
        cfg.connection["type"] = "sim"
        cfg.setting.update(control="CI", value=0.5)
        cfg.scpi["ocp"] = ""
        engine = Engine(Mock())
        engine.connect(cfg)
        try:
            with self.assertRaisesRegex(DeviceError, "ocp"):
                engine.start_run(cfg, [], Recorder(cfg))
            self.assertNotIn("OUTP:STAT ON", engine.device.tp.commands)
        finally:
            engine.disconnect()


if __name__ == "__main__":
    unittest.main()
