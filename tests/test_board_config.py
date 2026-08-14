"""Tests for loading the board registry from config.json."""

import json
import tempfile
import unittest
from pathlib import Path

from coordinator.board_config import (
    DEFAULT_BOARDS,
    Board,
    load_board_config,
)


class TestLoadBoardConfig(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.config_path = Path(self._tmp.name) / "config.json"

    def write_config(self, payload: object) -> None:
        self.config_path.write_text(
            json.dumps(payload),
            encoding="utf-8",
        )

    def test_missing_file_returns_defaults(self) -> None:
        config = load_board_config(self.config_path)

        self.assertEqual(config.boards, list(DEFAULT_BOARDS))
        self.assertEqual(config.port, 6000)
        self.assertEqual(config.timeout_seconds, 5.0)
        self.assertEqual(config.warnings, [])

    def test_valid_config_parses_boards(self) -> None:
        self.write_config(
            {
                "port": 7000,
                "timeout_seconds": 2.5,
                "boards": [
                    {"name": "Direct Ethernet", "ip": "192.168.1.20"},
                    {"name": "orin-nano-01", "ip": "orin-nano-01"},
                ],
            }
        )

        config = load_board_config(self.config_path)

        self.assertEqual(
            config.boards,
            [
                Board("Direct Ethernet", "192.168.1.20"),
                Board("orin-nano-01", "orin-nano-01"),
            ],
        )
        self.assertEqual(config.port, 7000)
        self.assertEqual(config.timeout_seconds, 2.5)
        self.assertEqual(config.warnings, [])

    def test_host_key_is_accepted_as_alias_for_ip(self) -> None:
        self.write_config(
            {"boards": [{"name": "Board A", "host": "10.0.0.5"}]}
        )

        config = load_board_config(self.config_path)

        self.assertEqual(config.boards, [Board("Board A", "10.0.0.5")])

    def test_per_board_port_override(self) -> None:
        self.write_config(
            {"boards": [{"name": "Odd port", "ip": "10.0.0.5", "port": 6100}]}
        )

        config = load_board_config(self.config_path)

        self.assertEqual(config.boards[0].port, 6100)

    def test_malformed_json_falls_back_with_warning(self) -> None:
        self.config_path.write_text("{not json", encoding="utf-8")

        config = load_board_config(self.config_path)

        self.assertEqual(config.boards, list(DEFAULT_BOARDS))
        self.assertEqual(len(config.warnings), 1)
        self.assertIn("config.json", config.warnings[0])

    def test_board_without_ip_is_skipped_with_warning(self) -> None:
        self.write_config(
            {
                "boards": [
                    {"name": "Broken"},
                    {"name": "Good", "ip": "10.0.0.5"},
                ]
            }
        )

        config = load_board_config(self.config_path)

        self.assertEqual(config.boards, [Board("Good", "10.0.0.5")])
        self.assertEqual(len(config.warnings), 1)

    def test_empty_board_list_falls_back_to_defaults(self) -> None:
        self.write_config({"boards": []})

        config = load_board_config(self.config_path)

        self.assertEqual(config.boards, list(DEFAULT_BOARDS))
        self.assertEqual(len(config.warnings), 1)

    def test_board_name_defaults_to_ip_when_missing(self) -> None:
        self.write_config({"boards": [{"ip": "10.0.0.5"}]})

        config = load_board_config(self.config_path)

        self.assertEqual(config.boards, [Board("10.0.0.5", "10.0.0.5")])


if __name__ == "__main__":
    unittest.main()
