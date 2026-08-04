"""Discoverable unittest suite for shared/event_log.py (frozen schema v1).

Run from the repository root:

    python -m unittest discover -s tests -v

Dependency-free and offline: touches no DUT, no real logs, no network. Replaces
the old `shared/test_event_log.py` script, which passed when run directly but was
invisible to `unittest discover` / `pytest`.
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "shared"))
import event_log as el  # noqa: E402

META = {"beam_energy": "64MeV", "fluence_source": "cyclotron-A", "shield_config": "2mm-Al"}


def _records():
    """One representative valid record per channel."""
    recs = []

    r = el.envelope("R-014", "orin-nano-01", "compute", "checksum", "ok", meta=META)
    r.update(iter=50, epoch=0, step=50, hash="836d5c79e3cfefa8",
             golden="836d5c79e3cfefa8", mismatch=False, finite=True,
             max_abs_pos=1.0, anomaly=False)
    recs.append(r)

    r = el.envelope("R-014", "orin-nano-01", "memory", "mismatch", "anomaly", meta=META)
    r.update(test="moving_inversion", address="0x3f8a0010", pattern="0xAA",
             expected="0xAA", actual="0xAB", xor="0x01")
    recs.append(r)

    r = el.envelope("R-014", "orin-nano-01", "heartbeat", "beat", "ok", meta=META)
    r.update(seq=1287, uptime_s=642.5)
    recs.append(r)

    r = el.envelope("R-014", "orin-nano-01", "boot", "boot", "info", meta=META)
    r.update(boot_id="b1c2...", uptime_s=3.1, reboot_count=2)
    recs.append(r)

    r = el.envelope("R-014", "orin-nano-01", "power", "sample", "tripped", meta=META)
    r.update(current_mA=1180, tripped=True)
    recs.append(r)

    return recs


class ValidRecords(unittest.TestCase):
    """Every channel's representative record must pass validation."""

    def test_each_channel_validates_clean(self):
        for rec in _records():
            with self.subTest(channel=rec["channel"]):
                self.assertEqual(el.validate(rec), [])

    def test_envelope_carries_beam_metadata(self):
        # Beam/shield metadata must ride on every record -- a result CSV that
        # cannot say which beam energy produced an SEE is not usable evidence.
        rec = el.envelope("R-014", "orin-nano-01", "compute", "checksum", meta=META)
        for field in ("beam_energy", "fluence_source", "shield_config"):
            self.assertEqual(rec[field], META[field])

    def test_timestamp_is_iso8601_utc_millis(self):
        ts = el.iso_now()
        self.assertRegex(ts, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")


class RoundTrip(unittest.TestCase):
    """Records must survive a write/read cycle through a JSONL file unchanged."""

    def test_jsonl_round_trip_preserves_every_record(self):
        recs = _records()
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "events.jsonl")
            with open(path, "w", encoding="utf-8") as handle:
                for rec in recs:
                    el.emit(handle, rec)
            back = list(el.read_events(path))

        self.assertEqual(len(back), len(recs))
        self.assertEqual(back[0]["hash"], "836d5c79e3cfefa8")
        self.assertEqual(back[4]["status"], "tripped")

    def test_each_line_is_one_complete_json_object(self):
        # The arbiter tails these files while they are being written, so a record
        # must never span lines.
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "events.jsonl")
            with open(path, "w", encoding="utf-8") as handle:
                for rec in _records():
                    el.emit(handle, rec)
            with open(path, encoding="utf-8") as handle:
                lines = [ln for ln in handle.read().splitlines() if ln.strip()]

        self.assertEqual(len(lines), 5)
        for line in lines:
            self.assertIsInstance(json.loads(line), dict)


class MalformedRecords(unittest.TestCase):
    """Bad records must be rejected, not silently logged."""

    def test_missing_required_envelope_field_fails(self):
        bad = {"schema_version": 1, "ts": "x", "run_id": "R", "jetson_id": "j",
               "channel": "compute", "event": "checksum"}  # no status
        self.assertTrue(el.validate(bad))

    def test_unknown_channel_fails(self):
        bad = el.envelope("R", "j", "compute", "e", meta=META)
        bad["channel"] = "gpu"
        self.assertTrue(el.validate(bad))

    def test_power_record_without_current_fails(self):
        bad = el.envelope("R", "j", "power", "sample", "ok", meta=META)
        self.assertTrue(el.validate(bad))

    def test_envelope_rejects_bad_enums_at_construction(self):
        for channel, status in (("compute", "NOPE"), ("nope", "ok")):
            with self.subTest(channel=channel, status=status):
                with self.assertRaises(ValueError):
                    el.envelope("R", "j", channel, "e", status, meta=META)


if __name__ == "__main__":
    unittest.main()
