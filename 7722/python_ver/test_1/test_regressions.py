"""异常路径回归：仅使用仿真和假设备，不打开真实硬件。"""
import csv
import os
import tempfile
import threading
import unittest
from unittest.mock import patch

from itech7722_config import AppConfig, load_config, validate_run
from itech7722_device import DeviceError, ITech7722, SimTransport
from itech7722_engine import Engine
from itech7722_recorder import Recorder, Sample
from itech7722_sequence import SequenceError, Step, load_sequence


def sim_config():
    cfg = AppConfig()
    cfg.connection["type"] = "sim"
    return cfg


class UI:
    def __init__(self):
        self.events = []

    def post(self, kind, value):
        self.events.append((kind, value))

    def log_scpi(self, *args):
        pass


class RegressionTests(unittest.TestCase):
    def engine(self, cfg):
        engine = Engine(UI())
        engine.connect(cfg)
        self.addCleanup(engine.disconnect)
        return engine

    def finish(self, engine):
        engine.thread.join(2)
        self.assertFalse(engine.thread.is_alive())
        self.assertFalse(engine.running)

    def test_invalid_plans_never_write_to_device(self):
        cfg = sim_config()
        engine = self.engine(cfg)
        cases = [("value", float("nan")), ("value", float("inf")),
                 ("value", -1), ("value", 301), ("control", "typo"),
                 ("frequency", 0), ("mode", "typo")]
        for key, value in cases:
            with self.subTest(key=key, value=value):
                invalid = cfg.copy()
                invalid.setting[key] = value
                before = list(engine.device.tp.commands)
                with self.assertRaises(ValueError):
                    engine.start_run(invalid, [], Recorder(invalid))
                self.assertEqual(before, engine.device.tp.commands)
                self.assertFalse(engine.running)

    def test_recording_failure_closes_output_and_reports_error(self):
        cfg = sim_config()
        engine = self.engine(cfg)
        recorder = Recorder(cfg)
        with patch.object(recorder, "start", side_effect=OSError("disk full")):
            engine.start_run(cfg, [], recorder)
            self.finish(engine)
        self.assertFalse(engine.device.tp.state["output"])
        self.assertNotIn("OUTP:STAT ON", engine.device.tp.commands)
        self.assertIn("disk full", engine.last_error)
        self.assertIn("disk full", engine.ui.events[-1][1])

    def test_setup_failure_recovers_state(self):
        cfg = sim_config()
        cfg.recording["enabled"] = False
        engine = self.engine(cfg)
        with patch.object(engine.device, "set_function_mode", side_effect=DeviceError("bad mode")):
            engine.start_run(cfg, [], Recorder(cfg))
            self.finish(engine)
        self.assertIn("bad mode", engine.last_error)
        self.assertFalse(engine.output_on)

    def test_first_command_turns_output_off_before_changing_mode(self):
        cfg = sim_config()
        cfg.recording["enabled"] = False
        engine = self.engine(cfg)
        engine.device.tp.commands.clear()
        engine.start_run(cfg, [], Recorder(cfg))
        engine.stop()
        writes = [c for c in engine.device.tp.commands if not c.endswith("?")]
        self.assertEqual(writes[:2], ["SYST:REM", "OUTP:STAT OFF"])

    def test_stop_timeout_preserves_thread_and_forbids_restart(self):
        cfg = sim_config()
        cfg.recording["enabled"] = False
        engine = self.engine(cfg)
        entered, release = threading.Event(), threading.Event()

        def blocked_measure(mode):
            entered.set()
            release.wait(5)
            return {"voltage": 0, "current": 0, "power": 0, "frequency": 50}

        engine.device.measure = blocked_measure
        engine.start_run(cfg, [], Recorder(cfg))
        try:
            self.assertTrue(entered.wait(2))
            thread = engine.thread
            self.assertFalse(engine.stop(timeout=0.01))
            self.assertTrue(engine.running)
            self.assertIs(thread, engine.thread)
            with self.assertRaises(DeviceError):
                engine.start_run(cfg, [], Recorder(cfg))
        finally:
            release.set()
            self.assertTrue(engine.stop())
        self.assertFalse(engine.output_on)

    def test_stop_during_setting_never_enables_output(self):
        cfg = sim_config()
        cfg.recording["enabled"] = False
        engine = self.engine(cfg)
        entered, release = threading.Event(), threading.Event()

        def setting(*args):
            entered.set()
            release.wait(5)

        engine.device.apply_setting = setting
        engine.start_run(cfg, [], Recorder(cfg))
        try:
            self.assertTrue(entered.wait(2))
            self.assertFalse(engine.stop(timeout=0.01))
        finally:
            release.set()
            engine.stop()
        self.assertNotIn("OUTP:STAT ON", engine.device.tp.commands)

    def test_invalid_measurement_trips_before_log_failure(self):
        cfg = sim_config()
        cfg.recording["enabled"] = False
        engine = self.engine(cfg)
        engine.device.measure = lambda mode: {"voltage": float("nan"), "current": 0, "power": 0}
        recorder = Recorder(cfg)

        def log(*args, **kwargs):
            self.assertFalse(engine.device.tp.state["output"])
            raise OSError("log failed")

        recorder.log = log
        engine.start_run(cfg, [], recorder)
        self.finish(engine)
        self.assertFalse(engine.output_on)

    def test_close_output_failure_is_not_reported_as_success(self):
        cfg = sim_config()
        cfg.recording["enabled"] = False
        engine = self.engine(cfg)
        with patch.object(engine.device, "output_off", side_effect=DeviceError("link lost")):
            engine.start_run(cfg, [], Recorder(cfg))
            self.finish(engine)
        self.assertIn("关闭输出失败", engine.ui.events[-1][1])

    def test_connect_exception_releases_transport(self):
        transport = SimTransport()
        with patch.object(transport, "query", side_effect=OSError("lost")), \
             patch.object(transport, "close") as close, \
             patch("itech7722_engine.build_transport", return_value=transport):
            engine = Engine(UI())
            with self.assertRaises(OSError):
                engine.connect(sim_config())
            self.assertIsNone(engine.device)
            close.assert_called_once()

    def test_sequence_frequency_inherits_global(self):
        cfg = sim_config()
        cfg.setting.update(output_mode="seq", frequency=60)
        cfg.recording["enabled"] = False
        engine = self.engine(cfg)
        engine.start_run(cfg, [Step(0, 0.02, value=10)], Recorder(cfg))
        self.finish(engine)
        self.assertIn("SOUR:NORM:FREQ 60", engine.device.tp.commands)

    def test_sequence_rejects_bad_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "seq.csv")
            for row in ("0,CV,1,50,all", "-1,CV,1,50,all", "nan,CV,1,50,all",
                        "1,typo,1,50,all", "1,CV,nan,50,all", "1,CV,1,inf,all",
                        "1,CV,oops,50,all"):
                with self.subTest(row=row):
                    with open(path, "w", encoding="utf-8") as fh:
                        fh.write("duration,control,value,frequency,record\n" + row)
                    with self.assertRaises(SequenceError):
                        load_sequence(path)

    def test_empty_sequence_never_falls_back_to_hold(self):
        cfg = sim_config()
        cfg.setting["output_mode"] = "seq"
        with self.assertRaises(ValueError):
            validate_run(cfg, [])

    def test_switching_keypoints_rearms_previous_point(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = sim_config()
            cfg.keypoints = {100: ["voltage"], 200: ["power"]}
            rec = Recorder(cfg, tmp)
            rec.start()
            for voltage in (100, 200, 100):
                self.assertTrue(rec.log(Sample("t", 0, 1, voltage=voltage), set_voltage=voltage))

    def test_old_log_schema_is_backed_up_before_new_header(self):
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            cfg = sim_config()
            rec = Recorder(cfg, tmp)
            original = b"time,voltage,current,power,frequency\nold,5,0.17,0.85,50\n"
            Path(rec.log_path).write_bytes(original)
            rec.start()
            backups = list(Path(tmp).glob("data_log.csv.backup_*"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_bytes(), original)
            with open(rec.log_path, encoding="utf-8-sig", newline="") as fh:
                header = next(csv.reader(fh))
            self.assertIn("power_factor", header)
            self.assertNotIn("frequency", header)

    def test_log_creates_parent_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = sim_config()
            cfg.recording["log_file"] = "nested/data.csv"
            rec = Recorder(cfg, tmp)
            rec.start()
            self.assertTrue(os.path.isfile(rec.log_path))

    def test_config_copy_does_not_share_keypoint_lists(self):
        cfg = sim_config()
        cfg.keypoints = {100: ["voltage"]}
        copied = cfg.copy()
        copied.keypoints[100].append("power")
        self.assertEqual(cfg.keypoints[100], ["voltage"])

    def test_config_paths_are_relative_to_config_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "c.ini")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("[Sequence]\nfile = steps.csv\n[Recording]\nlog_file = logs/data.csv\n")
            cfg = load_config(path)
            self.assertEqual(cfg.sequence["file"], os.path.join(tmp, "steps.csv"))
            self.assertEqual(cfg.recording["log_file"], os.path.join(tmp, "logs", "data.csv"))

    def test_bad_config_numeric_values_raise_clear_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            for text in ("[Limits]\nmax_voltage = typo\n", "[Keypoints]\nwrong = voltage\n"):
                path = os.path.join(tmp, "c.ini")
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(text)
                with self.assertRaises(ValueError):
                    load_config(path)

    def test_cp_to_ci_replaces_stale_power_limit(self):
        dev = ITech7722(SimTransport())
        limits = dict(max_voltage=300, max_current=10, max_power=600, mode="AC")
        dev.apply_setting("CP", 20, 50, limits)
        dev.apply_setting("CI", 2, 50, limits)
        self.assertEqual(dev.tp.state["power_limit"], 600)

    def test_cp_updates_mode_specific_voltage_limit(self):
        dev = ITech7722(SimTransport())
        limits = dict(max_voltage=300, max_current=10, max_power=600, mode="AC")
        dev.apply_setting("CV", 10, 50, limits)
        dev.apply_setting("CP", 200, 50, limits)
        self.assertEqual(dev.tp.state["volt_ac"], 300)

    def test_missing_output_command_is_error(self):
        dev = ITech7722(SimTransport(), {"out_off": ""})
        with self.assertRaises(DeviceError):
            dev.output_off()

    def test_rejected_setting_never_enables_output(self):
        cfg = sim_config()
        cfg.recording["enabled"] = False
        cfg.scpi["func_ac"] = "BOGUS:MODE AC"
        engine = self.engine(cfg)
        engine.start_run(cfg, [], Recorder(cfg))
        self.finish(engine)
        self.assertNotIn("OUTP:STAT ON", engine.device.tp.commands)
        self.assertIn("-113", engine.last_error)

    def test_ac_power_is_not_fabricated_from_rms_voltage_and_current(self):
        dev = ITech7722(SimTransport(), {"meas_power": ""})
        self.assertIsNone(dev.measure("AC")["power"])
        self.assertEqual(dev.measure("DC")["power"], 0)

    def test_zero_limits_are_rejected_instead_of_reusing_device_settings(self):
        cfg = sim_config()
        cfg.limits["max_power"] = 0
        with self.assertRaises(ValueError):
            validate_run(cfg, [])

    def test_changed_connection_is_not_silently_ignored(self):
        cfg = sim_config()
        engine = self.engine(cfg)
        cfg.connection["type"] = "serial"
        with self.assertRaisesRegex(DeviceError, "连接参数"):
            engine.start_run(cfg, [], Recorder(cfg))

    def test_tcp_handles_cr_and_split_crlf_without_merging_replies(self):
        from itech7722_device import TcpTransport

        class Socket:
            def __init__(self):
                self.chunks = [b"first\r", b"\nsecond\nthird\r\n"]

            def settimeout(self, timeout):
                pass

            def recv(self, size):
                return self.chunks.pop(0)

            def close(self):
                pass

        with patch("itech7722_device.socket.create_connection", return_value=Socket()):
            transport = TcpTransport("fake")
        self.assertEqual(transport.read(), "first")
        self.assertEqual(transport.read(), "second")
        self.assertEqual(transport.read(), "third")

    def test_checked_write_reports_exact_command_and_stops_at_first_error(self):
        cfg = sim_config()
        cfg.scpi["func_ac"] = "INVALID:MODE AC"
        cfg.recording["enabled"] = False
        engine = self.engine(cfg)
        engine.start_run(cfg, [], Recorder(cfg))
        self.finish(engine)
        self.assertIn("func_ac [INVALID:MODE AC]", engine.last_error)
        self.assertIn("-113", engine.last_error)
        self.assertFalse(any(c.startswith("PROT:RMS:VOLT ") for c in engine.device.tp.commands))
        self.assertNotIn("OUTP:STAT ON", engine.device.tp.commands)

    def test_more_than_twenty_old_errors_are_drained_before_new_commands(self):
        cfg = sim_config()
        cfg.recording["enabled"] = False
        engine = self.engine(cfg)
        engine.device.tp.errors = ['-113,"Undefined header"'] * 20 + ['-350,"Queue overflow"']
        engine.start_run(cfg, [ ] , Recorder(cfg))
        engine.stop()
        self.assertNotIn("设备拒绝设定", engine.last_error)
        self.assertFalse(engine.device.tp.errors)

    def test_ac_cv_does_not_send_generic_voltage_current_or_power_headers(self):
        dev = ITech7722(SimTransport())
        dev.prepare_run()
        dev.set_function_mode("AC")
        dev.apply_setting("CV", 10, 50, dict(max_voltage=300, max_current=6, max_power=600))
        self.assertIn("SOUR:NORM:VOLT:AC 10", dev.tp.commands)
        self.assertIn("PROT:MAX:CURR:LIM 6", dev.tp.commands)
        for header in ("SOUR:VOLT ", "SOUR:CURR ", "SOUR:POW ", "SOUR:FUNC "):
            self.assertFalse(any(c.startswith(header) for c in dev.tp.commands))

    def test_known_bad_old_defaults_are_migrated_but_custom_templates_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "old.ini")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("[SCPI]\nfunc_ac = SOUR:FUNC AC\nvolt = SOUR:VOLT {v}\n"
                         "power = SOUR:POW {v}\nfunc_dc = NORM:MODE DC\n")
            cfg = load_config(path)
            self.assertEqual(cfg.scpi["func_ac"], "SOUR:NORM:MODE AC")
            self.assertEqual(cfg.scpi["volt"], "")
            self.assertEqual(cfg.scpi["power"], "")
            self.assertEqual(cfg.scpi["func_dc"], "NORM:MODE DC")

    def test_hardware_ci_uses_voltage_feedback_instead_of_current_target_command(self):
        cfg = sim_config()
        cfg.setting.update(control="CI", value=0.02)
        cfg.recording["enabled"] = False
        cfg.feedback.update(interval_ms=50, step_voltage=0.5)
        engine = self.engine(cfg)
        engine.device.tp.name = "serial"  # 假传输，走与真机相同的闭环分支
        engine.start_run(cfg, [], Recorder(cfg))
        import time
        deadline = time.monotonic() + 3
        try:
            while time.monotonic() < deadline:
                samples = [v[0] for k, v in engine.ui.events if k == "sample"]
                if any(abs(s.current - 0.02) <= 0.002 for s in samples):
                    break
                time.sleep(0.01)
            else:
                self.fail(f"未跟踪到目标，错误={engine.last_error}")
        finally:
            engine.stop()
        self.assertNotIn("PROT:MAX:CURR:LIM 0.02", engine.device.tp.commands)
        self.assertIn("SOUR:NORM:VOLT:AC 0", engine.device.tp.commands)
        self.assertTrue(any(c.startswith("SOUR:NORM:VOLT:AC 0.") for c in engine.device.tp.commands))
        self.assertFalse(engine.output_on)

    def test_legacy_invalid_header_is_no_longer_accepted_by_simulator(self):
        dev = ITech7722(SimTransport())
        self.assertTrue(any("-113" in e for e in dev.probe("SOUR:FUNC AC")))

    def test_defaults_are_five_volts_and_six_amp_ocp(self):
        from pathlib import Path
        configs = [AppConfig()] + [load_config(str(Path(__file__).parent / name))
                                  for name in ("sample_config.ini",)]
        for cfg in configs:
            self.assertEqual(cfg.setting["value"], 5)
            self.assertEqual(cfg.setting["frequency"], 50)
            self.assertEqual(cfg.recording["interval_ms"], 1000)
            self.assertEqual(cfg.limits["max_current"], 6)
            self.assertEqual(cfg.setting["output_mode"], "hold")

    def test_it_m7722_rejects_ten_amp_limit_before_hardware_writes(self):
        cfg = sim_config()
        engine = self.engine(cfg)
        engine.device.tp.name = "serial"
        engine.model = "IT-M7722"
        cfg.limits["max_current"] = 10
        before = list(engine.device.tp.commands)
        with self.assertRaisesRegex(DeviceError, "max_current.*6"):
            engine.start_run(cfg, [], Recorder(cfg))
        self.assertEqual(before, engine.device.tp.commands)

    def test_shipped_sequence_never_exceeds_five_volts(self):
        from pathlib import Path
        steps = load_sequence(str(Path(__file__).parent / "sequence_demo.csv"))
        self.assertTrue(all(s.control == "CV" and 0 <= s.value <= 5 for s in steps))

    def test_saved_usb_interface_is_migrated_to_serial(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "old.ini")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("[Connection]\ntype = usb\nport = COM6\nusb_resource = USB0::OLD\n")
            cfg = load_config(path)
            self.assertEqual(cfg.connection["type"], "serial")
            self.assertEqual(cfg.connection["port"], "COM6")
            self.assertNotIn("usb_resource", cfg.connection)


if __name__ == "__main__":
    unittest.main()
