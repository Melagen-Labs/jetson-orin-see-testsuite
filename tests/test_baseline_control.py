"""Discoverable unittest suite for the BASELINE_TEST path in the control receiver.

Run from the repository root:

    python -m unittest discover -s tests -v

Offline and board-free: no systemd, no network, no INA3221. The pieces exercised
here are the ones that decide whether a baseline run is accepted and whether its
CSV is produced -- request validation, run metadata, and the sampler's lifecycle.
The channel start/stop path itself is `systemctl`, which only exists on the DUT
and is covered by the on-hardware procedure in docs/DRYRUN_PIPELINE_TEST.md.
"""

import json
import os
import sys
import tempfile
import threading
import time
import unittest

REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(REPO, "jetson", "control"))
import control_receiver as cr  # noqa: E402

sys.path.insert(0, os.path.join(REPO, "tests"))
from test_current_logger import fake_hwmon  # noqa: E402


def baseline_msg(**overrides):
    msg = {
        "protocol_version": 1,
        "command": "BASELINE_TEST",
        "request_id": "baseline-req-01",
        "sent_at_utc": "2026-01-01T00:00:00.000Z",
    }
    msg.update(overrides)
    return msg


class ValidationTests(unittest.TestCase):

    def setUp(self):
        self.cfg = cr.load_config(None)

    def test_baseline_test_is_a_supported_command(self):
        self.assertIn("BASELINE_TEST", self.cfg["supported_commands"])

    def test_minimal_baseline_request_is_accepted(self):
        cmd, errors = cr.validate(baseline_msg(), self.cfg)
        self.assertEqual(cmd, "BASELINE_TEST")
        self.assertEqual(errors, [])

    def test_baseline_duration_is_validated(self):
        _, errors = cr.validate(baseline_msg(duration_s=3600), self.cfg)
        self.assertEqual(errors, [])

        for bad in (0, -5, True, "600", self.cfg["max_duration_s"] + 1):
            with self.subTest(duration_s=bad):
                _, errors = cr.validate(baseline_msg(duration_s=bad), self.cfg)
                self.assertTrue(any("duration_s" in e for e in errors), errors)

    def test_missing_envelope_field_is_rejected(self):
        msg = baseline_msg()
        del msg["sent_at_utc"]
        _, errors = cr.validate(msg, self.cfg)
        self.assertTrue(any("sent_at_utc" in e for e in errors), errors)

    def test_start_test_still_requires_its_beam_parameters(self):
        # Adding BASELINE_TEST must not loosen the beam-run contract.
        _, errors = cr.validate({
            "protocol_version": 1, "command": "START_TEST",
            "request_id": "r", "sent_at_utc": "2026-01-01T00:00:00.000Z",
        }, self.cfg)
        self.assertTrue(any("beam_energy_mev" in e for e in errors), errors)


class RunMetadataTests(unittest.TestCase):

    def test_baseline_metadata_says_no_beam(self):
        meta = cr.run_metadata(baseline_msg(), baseline=True)
        self.assertEqual(meta["run_id"], "baseline-req-01")
        self.assertEqual(meta["beam_energy"], "none")
        self.assertEqual(meta["shield_config"], "none")

    def test_beam_metadata_is_unchanged(self):
        meta = cr.run_metadata({
            "request_id": "r-9", "beam_energy_mev": 200,
            "shielding_material": "MLC1", "shielding_thickness_mm": 12,
        })
        self.assertEqual(meta["beam_energy"], "200MeV")
        self.assertEqual(meta["shield_config"], "MLC1_12mm")


class CurrentLoggerLifecycleTests(unittest.TestCase):
    """Start the real sampler against a fake sysfs tree and collect its summary."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.hwmon = fake_hwmon(os.path.join(self.tmp.name, "hwmon1"))
        self.state = {}
        self.lock = threading.Lock()

        self.cfg = cr.load_config(None)
        self.cfg["current_logger"] = dict(self.cfg["current_logger"], **{
            "script": os.path.join(REPO, "jetson", "power", "current_logger.py"),
            "python": sys.executable,
            "csv_dir": os.path.join(self.tmp.name, "power"),
            "jsonl": None,
            "hwmon": self.hwmon,
            "interval_s": 0.05,
            "rolling_window": 3,
            "stop_grace_s": 20.0,
        })

    def test_start_reports_the_csv_it_will_write(self):
        result, info = cr.start_current_logger(
            self.cfg, "run-a", 0.3, self.state, self.lock)
        self.addCleanup(cr.stop_current_logger, self.cfg, self.state, self.lock)

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["name"], "current")
        self.assertTrue(info["csv_name"].startswith("baseline_current_"))
        self.assertTrue(info["csv_name"].endswith(".csv"))
        self.assertEqual(info["expected_samples"], 6)   # 0.3 s / 0.05 s

    def test_stop_returns_the_summary_and_the_csv_exists(self):
        _, info = cr.start_current_logger(
            self.cfg, "run-b", 0.3, self.state, self.lock)
        summary = cr.stop_current_logger(self.cfg, self.state, self.lock)

        self.assertEqual(summary["run_id"], "run-b")
        self.assertEqual(summary["csv_name"], info["csv_name"])
        self.assertEqual(summary["sensor_failures"], 0)
        self.assertGreater(summary["samples"], 0)
        self.assertEqual(summary["current_ma"]["max"], 1900)
        self.assertEqual(summary["exit_code"], 0)

        self.assertTrue(os.path.isfile(info["csv"]))
        with open(info["csv"], "r", encoding="utf-8") as fp:
            header = fp.readline().strip().split(",")
        self.assertEqual(tuple(header), (
            "sequence", "recorded_at_utc", "current_ma", "voltage_mv", "power_mw",
            "rolling_average_ma", "rolling_window_count", "sensor_source",
            "data_quality_flags", "run_id", "jetson_id", "boot_id"))

    def test_stopping_a_long_run_returns_promptly(self):
        """A manual stop must not wait out the sampler's remaining duration.

        Regression for 2026-08-06: stop_current_logger waited stop_grace_s BEFORE
        signalling, so stopping a 60-minute baseline blocked the reply for 15 s and
        the coordinator (5 s command timeout) reported "STOP_TEST failed - test
        remains active" for a run that had actually stopped.
        """
        cr.start_current_logger(self.cfg, "run-long", 3600, self.state, self.lock)

        began = time.monotonic()
        summary = cr.stop_current_logger(self.cfg, self.state, self.lock)
        elapsed = time.monotonic() - began

        self.assertLess(elapsed, 4.0, "stop took %.1fs -- the GUI gives up at 5" % elapsed)
        # The sampler is gone either way; on POSIX it also finalized its summary.
        self.assertIsNotNone(summary)
        self.assertEqual(summary["csv_name"], os.path.basename(summary["csv"]))

    def test_stop_without_a_running_sampler_is_harmless(self):
        self.assertIsNone(cr.stop_current_logger(self.cfg, self.state, self.lock))

    def test_stop_replays_the_last_summary(self):
        # The coordinator's mirror STOP can arrive after the DUT-owned auto-stop
        # already reaped the sampler; it must still get the baseline block.
        cr.start_current_logger(self.cfg, "run-c", 0.2, self.state, self.lock)
        first = cr.stop_current_logger(self.cfg, self.state, self.lock)
        second = cr.stop_current_logger(self.cfg, self.state, self.lock)
        self.assertEqual(first, second)

    def test_a_missing_script_fails_loudly(self):
        # On a baseline run the CSV is the whole deliverable, so a sampler that
        # cannot start must reject the command rather than run a silent no-op.
        self.cfg["current_logger"]["script"] = os.path.join(self.tmp.name, "gone.py")
        result, info = cr.start_current_logger(
            self.cfg, "run-d", 1.0, self.state, self.lock)
        self.assertFalse(result["ok"])
        self.assertIn("not found", result["detail"])
        self.assertIsNone(info)

    def test_disabled_logger_is_reported_not_silently_skipped(self):
        self.cfg["current_logger"]["enabled"] = False
        result, info = cr.start_current_logger(
            self.cfg, "run-e", 1.0, self.state, self.lock)
        self.assertTrue(result["ok"])
        self.assertEqual(result["detail"], "disabled in config")
        self.assertIsNone(info)


class StopRaceTests(unittest.TestCase):
    """Two stops racing must still stop the services.

    Regression for 2026-08-06 on orin-nano-01: the DUT-owned auto-stop and the
    coordinator's mirror STOP fire at the same duration_s, so both disarm at once.
    The loser's os.remove hit ENOENT, the exception escaped before `systemctl stop`
    ran, and mem_check_gpu was left running while the reply said the channel had
    failed to stop -- the operator had to stop it by hand.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.calls = []

        self.cfg = cr.load_config(None)
        self.cfg["channels"] = [{
            "name": "memory_gpu",
            "config": os.path.join(self.tmp.name, "cfg.json"),
            "armed_flag": os.path.join(self.tmp.name, "ARMED"),
            "service": "mem_check_gpu.service",
            "log": os.path.join(self.tmp.name, "mem.jsonl"),
        }]

        # systemctl does not exist off the DUT; record the calls instead.
        real = cr.systemctl
        self.addCleanup(setattr, cr, "systemctl", real)
        cr.systemctl = lambda action, service, cfg: (
            self.calls.append((action, service)) or (True, "%s ok" % action))

    def test_stop_succeeds_when_the_flag_is_already_gone(self):
        # No ARMED file at all == the other stop already won the race.
        results = cr.do_stop(self.cfg)

        self.assertEqual(self.calls, [("stop", "mem_check_gpu.service")],
                         "the service must be stopped even when disarm found nothing")
        self.assertTrue(results[0]["ok"], results[0])

    def test_stop_disarms_and_stops_when_the_flag_is_present(self):
        flag = self.cfg["channels"][0]["armed_flag"]
        open(flag, "w").close()

        results = cr.do_stop(self.cfg)

        self.assertFalse(os.path.exists(flag), "ARMED must be removed")
        self.assertEqual(self.calls, [("stop", "mem_check_gpu.service")])
        self.assertTrue(results[0]["ok"], results[0])


class ConfigFileTests(unittest.TestCase):
    """The shipped config must actually enable what the code expects."""

    def test_shipped_config_enables_baseline(self):
        path = os.path.join(REPO, "jetson", "control", "config", "test_control.json")
        with open(path, "r", encoding="utf-8") as fp:
            cfg = json.load(fp)
        self.assertIn("BASELINE_TEST", cfg["supported_commands"])
        self.assertTrue(cfg["current_logger"]["enabled"])
        self.assertTrue(cfg["current_logger"]["script"].endswith("current_logger.py"))
        # Beam runs must be unaffected until the campaign decides otherwise.
        self.assertFalse(cfg["current_logger"]["on_start_test"])


if __name__ == "__main__":
    unittest.main()
