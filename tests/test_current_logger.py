"""Discoverable unittest suite for jetson/power/current_logger.py (channel 5).

Run from the repository root:

    python -m unittest discover -s tests -v

Dependency-free and offline. There is no INA3221 here, so the sysfs tree is
faked in a temp directory -- which also proves the sampler is driven purely by
the hwmon file layout and can be exercised on a laptop before it ever reaches a
board.
"""

import csv
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "jetson", "power"))
import current_logger as cl  # noqa: E402


def _write(path, text):
    with open(path, "w", encoding="utf-8") as fp:
        fp.write(text)


def fake_hwmon(root, labels=("VDD_IN", "VDD_CPU_GPU_CV", "VDD_SOC"),
               currents=(1900, 400, 300), voltages=(4968, 4900, 4800)):
    """Build a directory that looks like the Orin's INA3221 hwmon node."""
    os.makedirs(root, exist_ok=True)
    _write(os.path.join(root, "name"), "ina3221\n")
    for idx, label in enumerate(labels, start=1):
        _write(os.path.join(root, "in%d_label" % idx), label + "\n")
        _write(os.path.join(root, "curr%d_input" % idx), "%d\n" % currents[idx - 1])
        _write(os.path.join(root, "in%d_input" % idx), "%d\n" % voltages[idx - 1])
    return root


class ChannelSelectionTests(unittest.TestCase):
    """The rail is found by device-tree label, not a hardcoded index."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_finds_vdd_in_by_label(self):
        hwmon = fake_hwmon(os.path.join(self.tmp.name, "hwmon1"))
        self.assertEqual(cl.find_channel(hwmon, "VDD_IN"), 1)

    def test_label_order_does_not_matter(self):
        hwmon = fake_hwmon(os.path.join(self.tmp.name, "hwmon2"),
                           labels=("VDD_SOC", "VDD_CPU_GPU_CV", "VDD_IN"),
                           currents=(300, 400, 1900))
        self.assertEqual(cl.find_channel(hwmon, "VDD_IN"), 3)

    def test_missing_labels_fall_back_to_channel_1(self):
        # Some BSP builds ship the sensor without in<N>_label; channel 1 is
        # VDD_IN on every Orin Nano devkit, so sampling must still work.
        hwmon = os.path.join(self.tmp.name, "hwmon3")
        os.makedirs(hwmon)
        _write(os.path.join(hwmon, "name"), "ina3221\n")
        _write(os.path.join(hwmon, "curr1_input"), "1900\n")
        _write(os.path.join(hwmon, "in1_input"), "4968\n")
        self.assertEqual(cl.find_channel(hwmon, "VDD_IN"), 1)

    def test_explicit_hwmon_must_exist(self):
        self.assertIsNone(cl.find_hwmon(os.path.join(self.tmp.name, "nope")))
        hwmon = fake_hwmon(os.path.join(self.tmp.name, "hwmon4"))
        self.assertEqual(cl.find_hwmon(hwmon), hwmon)


class ReadingTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.hwmon = fake_hwmon(os.path.join(self.tmp.name, "hwmon1"))

    def test_reads_current_and_voltage(self):
        self.assertEqual(cl.read_sample(self.hwmon, 1), (1900, 4968))

    def test_unreadable_sensor_reports_no_sample(self):
        # A transient I2C failure must be reported, never guessed at.
        os.remove(os.path.join(self.hwmon, "curr1_input"))
        self.assertEqual(cl.read_sample(self.hwmon, 1), (None, None))

    def test_power_is_derived_the_documented_way(self):
        # Matches the 2026-08-01 reference capture: 2040 mA @ 4968 mV -> 10135 mW.
        self.assertEqual(cl.derive_power_mw(2040, 4968), 10135)
        self.assertEqual(cl.derive_power_mw(1944, 4968), 9658)


class CaptureTests(unittest.TestCase):
    """End-to-end: a short run must produce the reference CSV shape + a summary."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.hwmon = fake_hwmon(os.path.join(self.tmp.name, "hwmon1"))
        self.csv_path = os.path.join(self.tmp.name, "baseline.csv")

    def _run(self, duration_s=0.25, interval_s=0.05, window=3, extra=None):
        argv = ["--out", self.csv_path, "--hwmon", self.hwmon,
                "--duration-s", str(duration_s), "--interval-s", str(interval_s),
                "--rolling-window", str(window), "--run-id", "baseline-unit-01",
                "--jetson-id", "orin-nano-test"]
        return cl.main(argv + (extra or []))

    def _rows(self):
        with open(self.csv_path, "r", encoding="utf-8", newline="") as fp:
            return list(csv.DictReader(fp))

    def test_header_matches_the_reference_capture(self):
        self._run()
        with open(self.csv_path, "r", encoding="utf-8", newline="") as fp:
            header = next(csv.reader(fp))
        self.assertEqual(tuple(header), cl.CSV_COLUMNS)

    def test_rows_carry_readings_and_run_identity(self):
        self.assertEqual(self._run(), 0)
        rows = self._rows()
        self.assertGreaterEqual(len(rows), 3)
        first = rows[0]
        self.assertEqual(first["current_ma"], "1900")
        self.assertEqual(first["voltage_mv"], "4968")
        self.assertEqual(first["power_mw"], "9439")
        self.assertEqual(first["run_id"], "baseline-unit-01")
        self.assertEqual(first["jetson_id"], "orin-nano-test")
        self.assertEqual(first["data_quality_flags"], cl.FLAG_DERIVED_POWER)
        self.assertEqual(first["sequence"], "1")
        self.assertTrue(first["recorded_at_utc"].endswith("Z"))

    def test_rolling_average_stays_blank_until_the_window_is_full(self):
        self._run(duration_s=0.3, interval_s=0.05, window=3)
        rows = self._rows()
        self.assertEqual(rows[0]["rolling_average_ma"], "")
        self.assertEqual(rows[1]["rolling_average_ma"], "")
        self.assertEqual(rows[2]["rolling_average_ma"], "1900.0")
        self.assertEqual(rows[2]["rolling_window_count"], "3")

    def test_sensor_failure_is_recorded_not_hidden(self):
        os.remove(os.path.join(self.hwmon, "curr1_input"))
        # No usable readings at all -> non-zero exit, but the rows still exist so
        # the gap is visible in the data.
        self.assertEqual(self._run(), 4)
        rows = self._rows()
        self.assertTrue(rows)
        self.assertEqual(rows[0]["current_ma"], "")
        self.assertEqual(rows[0]["data_quality_flags"], cl.FLAG_READ_FAILED)

        with open(self.csv_path + ".summary.json", encoding="utf-8") as fp:
            summary = json.load(fp)
        self.assertEqual(summary["samples"], 0)
        self.assertEqual(summary["sensor_failures"], len(rows))
        self.assertIsNone(summary["current_ma"])

    def test_summary_sidecar_reports_the_run(self):
        self._run()
        with open(self.csv_path + ".summary.json", encoding="utf-8") as fp:
            summary = json.load(fp)
        self.assertEqual(summary["run_id"], "baseline-unit-01")
        self.assertEqual(summary["jetson_id"], "orin-nano-test")
        self.assertEqual(summary["csv_name"], "baseline.csv")
        self.assertEqual(summary["sensor_failures"], 0)
        self.assertEqual(summary["samples"], len(self._rows()))
        self.assertEqual(summary["current_ma"]["min"], 1900)
        self.assertEqual(summary["current_ma"]["max"], 1900)
        self.assertFalse(summary["stopped_early"])

    def test_jsonl_records_are_schema_v1_power_records(self):
        jsonl = os.path.join(self.tmp.name, "current.jsonl")
        self._run(extra=["--jsonl", jsonl])

        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                        "..", "shared"))
        import event_log as el  # noqa: PLC0415 - located relative to the repo

        records = list(el.read_events(jsonl))
        self.assertTrue(records)
        for rec in records:
            self.assertEqual(el.validate(rec), [])
            self.assertEqual(rec["channel"], "power")
        self.assertEqual(records[0]["event"], "start")
        self.assertEqual(records[-1]["event"], "stop")
        self.assertTrue(
            any(r["event"] == "current_sample" and r["current_mA"] == 1900
                for r in records))


if __name__ == "__main__":
    unittest.main()
