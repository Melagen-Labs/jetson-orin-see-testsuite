"""Tests for the live SEE log tailer (§6b)."""

import tempfile
import unittest
from pathlib import Path

from coordinator.see_monitor import (
    SeeLogTailer,
    classify_see,
)


class TestClassifySee(unittest.TestCase):
    def test_golden_mismatch(self) -> None:
        record = {
            "channel": "compute",
            "event": "checksum",
            "status": "anomaly",
            "mismatch": True,
            "finite": True,
            "anomaly": True,
            "epoch": 7,
        }
        self.assertEqual(
            classify_see(record),
            ("cuda_golden_mismatch", "epoch 7"),
        )

    def test_nonfinite(self) -> None:
        record = {
            "event": "checksum",
            "status": "anomaly",
            "mismatch": False,
            "finite": False,
            "anomaly": True,
            "epoch": 3,
        }
        key, _ = classify_see(record)
        self.assertEqual(key, "cuda_nonfinite")

    def test_out_of_bounds_anomaly(self) -> None:
        record = {
            "event": "checksum",
            "status": "anomaly",
            "mismatch": False,
            "finite": True,
            "anomaly": True,
            "epoch": 5,
        }
        key, _ = classify_see(record)
        self.assertEqual(key, "cuda_anomaly")

    def test_clean_checksum_is_not_a_see(self) -> None:
        record = {
            "event": "checksum",
            "status": "ok",
            "mismatch": False,
            "finite": True,
            "anomaly": False,
        }
        self.assertIsNone(classify_see(record))

    def test_sim_fault(self) -> None:
        record = {
            "event": "sim_fault",
            "status": "crash",
            "error": "unspecified launch failure",
        }
        key, detail = classify_see(record)
        self.assertEqual(key, "cuda_shutdown")
        self.assertIn("launch failure", detail)

    def test_mem_upset(self) -> None:
        record = {
            "event": "mem_upset",
            "status": "ok",
            "address": "0x1234",
        }
        key, detail = classify_see(record)
        self.assertEqual(key, "gpu_mem_upset")
        self.assertIn("0x1234", detail)

    def test_fatal_error(self) -> None:
        record = {"event": "whatever", "status": "error", "detail": "boom"}
        key, detail = classify_see(record)
        self.assertEqual(key, "fatal_error")
        self.assertEqual(detail, "boom")

    def test_heartbeat_is_not_a_see(self) -> None:
        self.assertIsNone(
            classify_see({"event": "heartbeat", "status": "ok"})
        )

    def test_see_event_with_dump_is_flagged(self) -> None:
        record = {
            "event": "see_event",
            "status": "anomaly",
            "epoch": 4,
            "dump": "see_dumps/epoch_4_iter_5000.bin",
        }
        key, detail = classify_see(record)
        self.assertEqual(key, "see_dump_saved")
        self.assertIn("epoch 4", detail)
        self.assertIn("epoch_4_iter_5000.bin", detail)

    def test_see_event_without_dump_is_flagged(self) -> None:
        record = {
            "event": "see_event",
            "status": "anomaly",
            "epoch": 6,
            "dump": "",
        }
        key, detail = classify_see(record)
        self.assertEqual(key, "see_dump_saved")
        self.assertIn("NO dump", detail)

    def test_sim_fault_reports_dump(self) -> None:
        record = {
            "event": "sim_fault",
            "status": "crash",
            "error": "launch failed",
            "dump": "see_dumps/epoch_2_iter_2100_fault.bin",
        }
        key, detail = classify_see(record)
        self.assertEqual(key, "cuda_shutdown")
        self.assertIn("epoch_2_iter_2100_fault.bin", detail)


class TestSeeLogTailer(unittest.TestCase):
    def _write_line(self, path: Path, text: str) -> None:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(text + "\n")

    def test_only_new_events_are_returned(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            compute = root / "compute"
            compute.mkdir()
            log = compute / "cuda_particles.jsonl"

            # A clean record and one SEE.
            self._write_line(
                log,
                '{"ts":"t1","jetson_id":"nano-01","event":"checksum",'
                '"status":"ok","anomaly":false}',
            )
            self._write_line(
                log,
                '{"ts":"t2","jetson_id":"nano-01","event":"checksum",'
                '"status":"anomaly","mismatch":true,"finite":true,'
                '"anomaly":true,"epoch":2}',
            )

            tailer = SeeLogTailer(root, from_start=True)
            first = tailer.poll()
            self.assertEqual(len(first), 1)
            self.assertEqual(first[0]["type_key"], "cuda_golden_mismatch")
            self.assertEqual(first[0]["jetson_id"], "nano-01")

            # No new lines -> nothing re-emitted.
            self.assertEqual(tailer.poll(), [])

            # Append another SEE -> only that one comes back.
            self._write_line(
                log,
                '{"ts":"t3","jetson_id":"nano-01","event":"mem_upset",'
                '"status":"ok","address":"0xABCD"}',
            )
            second = tailer.poll()
            self.assertEqual(len(second), 1)
            self.assertEqual(second[0]["type_key"], "gpu_mem_upset")

    def test_partial_line_is_not_consumed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            compute = root / "compute"
            compute.mkdir()
            log = compute / "cuda_particles.jsonl"

            # Write a line WITHOUT a trailing newline (mid-append).
            with open(log, "a", encoding="utf-8") as handle:
                handle.write(
                    '{"ts":"t1","jetson_id":"n","event":"mem_upset",'
                    '"status":"ok","address":"0x1"}'
                )

            tailer = SeeLogTailer(root, from_start=True)
            self.assertEqual(tailer.poll(), [])  # incomplete line held back

            # Finish the line -> now it is delivered exactly once.
            with open(log, "a", encoding="utf-8") as handle:
                handle.write("\n")
            events = tailer.poll()
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["type_key"], "gpu_mem_upset")

    def test_missing_root_is_safe(self) -> None:
        tailer = SeeLogTailer(Path("does-not-exist-xyz"), from_start=True)
        self.assertEqual(tailer.poll(), [])

    def test_history_is_not_replayed_by_default(self) -> None:
        """A live monitor must not report pre-existing events (previous runs, or
        hand-seeded demo lines) as if they just happened."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            compute = root / "compute"
            compute.mkdir()
            log = compute / "cuda_particles.jsonl"

            # Pre-existing history, written BEFORE the monitor starts.
            self._write_line(
                log,
                '{"ts":"old","jetson_id":"n","event":"mem_upset",'
                '"status":"ok","address":"0xOLD"}',
            )

            tailer = SeeLogTailer(root)          # default: from now on
            self.assertEqual(tailer.poll(), [])  # history skipped

            # Something that happens after start IS reported.
            self._write_line(
                log,
                '{"ts":"new","jetson_id":"n","event":"mem_upset",'
                '"status":"ok","address":"0xNEW"}',
            )
            events = tailer.poll()
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["ts"], "new")


if __name__ == "__main__":
    unittest.main()
