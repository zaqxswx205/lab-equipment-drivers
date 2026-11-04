"""统一记录字段、功率因子和中文序列兼容性。仅使用离线设备。"""
import csv
import tempfile
import unittest
from pathlib import Path

from itech7722_config import AppConfig, PARAMS
from itech7722_device import ITech7722, SimTransport
from itech7722_recorder import Recorder, Sample
from itech7722_sequence import load_sequence


class UnifiedRecordingTests(unittest.TestCase):
    def test_demo_has_no_record_column_and_excel_utf8_marker(self):
        path = Path(__file__).parent / "sequence_demo.csv"
        self.assertTrue(path.read_bytes().startswith(b"\xef\xbb\xbf"))
        with path.open(encoding="utf-8-sig", newline="") as fh:
            self.assertEqual(next(csv.reader(fh)), ["duration", "control", "value", "frequency"])
        steps = load_sequence(str(path))
        self.assertEqual(steps[-1].value, 0)
        self.assertEqual(steps[0].value, 1)

    def test_chinese_ansi_sequence_keeps_notes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ansi.csv"
            path.write_bytes('duration,control,value,frequency,desc\n1,CV,5,50,"升压，保持"\n'.encode("gb18030"))
            self.assertEqual(load_sequence(str(path))[0].desc, "")

    def test_headerless_five_columns_and_optional_note(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "s.csv"
            path.write_text("1,CV,5,50,回落\n1,CV,0,50\n", encoding="utf-8")
            steps = load_sequence(str(path))
            self.assertEqual([s.desc for s in steps], ["", ""])

    def test_legacy_none_record_does_not_disable_full_recording(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "old.csv"
            path.write_text("duration,control,value,frequency,record,desc\n1,CV,5,50,none,旧文件\n", encoding="utf-8")
            step = load_sequence(str(path))[0]
            cfg = AppConfig()
            cfg.recording["params"] = ["voltage"]
            recorder = Recorder(cfg, tmp)
            recorder.start()
            recorder.log(Sample("t", 0, 1, voltage=5, current=0.17, power=0.8,
                                power_factor=0.94), params=[])
            with open(recorder.log_path, encoding="utf-8-sig", newline="") as fh:
                header, row = list(csv.reader(fh))
            self.assertIsNone(step.record)
            self.assertEqual(header[2:6], list(PARAMS))
            self.assertNotIn("desc", header)
            self.assertNotIn("elapsed_s", header)
            self.assertNotIn("status", header)
            self.assertEqual(len(header), len(row))
            self.assertEqual(row[2:6], ["5.000", "0.1700", "0.800", "0.9400"])

    def test_power_factor_is_queried_instead_of_using_power_reply(self):
        dev = ITech7722(SimTransport())
        dev.set_protection_limits(300, 6, 600)
        dev.apply_setting("CV", 5, 50)
        dev.output_on()
        m = dev.measure()
        self.assertEqual(m["power_factor"], 1)
        self.assertIn("MEAS:POW:PFAC?", dev.tp.commands)
        sample = Sample.now(0, 1, m)
        self.assertEqual(sample.power_factor, 1)

    def test_invalid_power_factor_is_missing_not_fabricated(self):
        class Transport(SimTransport):
            def query(self, cmd):
                return "1.7" if "PFAC" in cmd else super().query(cmd)

        self.assertIsNone(ITech7722(Transport()).measure()["power_factor"])

    def test_keypoint_records_all_four_fields_and_preserves_old_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = AppConfig()
            cfg.keypoints = {5: ["voltage"]}
            recorder = Recorder(cfg, tmp)
            original = b"voltage,frequency\n5,50\n"
            Path(recorder.key_path).write_bytes(original)
            recorder.start()
            self.assertTrue(recorder.log(Sample("t", 0, 1, voltage=5, current=0.17,
                                                power=0.8, power_factor=0.94), set_voltage=5))
            backup = list(Path(tmp).glob("keypoint_log.csv.backup_*"))
            self.assertEqual(backup[0].read_bytes(), original)
            with open(recorder.key_path, encoding="utf-8-sig", newline="") as fh:
                header, row = list(csv.reader(fh))
            self.assertEqual(header[2:], list(PARAMS))
            self.assertEqual(row[2:], ["5.000", "0.1700", "0.800", "0.9400"])


if __name__ == "__main__":
    unittest.main()
