"""Tests for persistent JSONL event logging."""

import json
import tempfile
import unittest
from pathlib import Path

from coordinator.event_logger import EventLogger


class TestEventLogger(unittest.TestCase):
    def test_append_creates_jsonl_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log_path = (
                Path(directory)
                / "logs"
                / "events.jsonl"
            )

            logger = EventLogger(log_path)

            record = logger.append(
                "APPLICATION_STARTED",
                transport="mock",
            )

            self.assertTrue(log_path.exists())
            self.assertEqual(
                record["event"],
                "APPLICATION_STARTED",
            )
            self.assertTrue(
                record["recorded_at_utc"].endswith("Z")
            )

            saved_record = json.loads(
                log_path.read_text(
                    encoding="utf-8"
                ).strip()
            )

            self.assertEqual(
                saved_record,
                record,
            )

    def test_multiple_events_are_appended(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log_path = (
                Path(directory)
                / "events.jsonl"
            )

            logger = EventLogger(log_path)

            logger.append(
                "START_TEST_SENT",
                request_id="start-123",
            )

            logger.append(
                "START_TEST_ACCEPTED",
                request_id="start-123",
            )

            lines = log_path.read_text(
                encoding="utf-8"
            ).splitlines()

            self.assertEqual(len(lines), 2)

            first = json.loads(lines[0])
            second = json.loads(lines[1])

            self.assertEqual(
                first["event"],
                "START_TEST_SENT",
            )
            self.assertEqual(
                second["event"],
                "START_TEST_ACCEPTED",
            )

    def test_empty_event_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            logger = EventLogger(
                Path(directory) / "events.jsonl"
            )

            with self.assertRaises(ValueError):
                logger.append("")

    def test_event_type_is_checked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            logger = EventLogger(
                Path(directory) / "events.jsonl"
            )

            with self.assertRaises(TypeError):
                logger.append(123)

    def test_reserved_fields_cannot_be_replaced(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            logger = EventLogger(
                Path(directory) / "events.jsonl"
            )

            with self.assertRaises(ValueError):
                logger.append(
                    "TEST_EVENT",
                    recorded_at_utc="fake-time",
                )

    def test_directory_cannot_be_used_as_file(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                EventLogger(Path(directory))


if __name__ == "__main__":
    unittest.main()