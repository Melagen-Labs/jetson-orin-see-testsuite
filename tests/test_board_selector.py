"""Tests for the target-board selector logic (transport swapping)."""

import unittest

from coordinator.board_config import Board, BoardConfig
from coordinator.board_selector import (
    resolve_initial_board,
    select_board,
)
from coordinator.transport import TcpTransport
from coordinator.ui import CoordinatorState


class FakeVar:
    """Duck-typed stand-in for tk.StringVar (no Tk root needed)."""

    def __init__(self, value: str = "") -> None:
        self.value = value

    def set(self, value: str) -> None:
        self.value = value

    def get(self) -> str:
        return self.value


class FakeApp:
    """Minimal stand-in exposing the attributes select_board touches."""

    def __init__(self) -> None:
        self.coordinator_state = CoordinatorState.IDLE
        self.transport = TcpTransport("127.0.0.1", 6000)
        self.transport_mode = "tcp"
        self.transport_target = " -> 127.0.0.1:6000"
        self.status_var = FakeVar("Ready - tcp mode -> 127.0.0.1:6000")
        self.log_lines: list[str] = []
        self.events: list[tuple[str, dict]] = []

    def _append_log(self, message: str) -> None:
        self.log_lines.append(message)

    def _record_event(self, event_type: str, **fields: object) -> None:
        self.events.append((event_type, fields))


def make_config() -> BoardConfig:
    return BoardConfig(
        boards=[
            Board("Direct Ethernet", "192.168.1.20"),
            Board("orin-nano-01", "orin-nano-01"),
        ],
        port=6000,
        timeout_seconds=5.0,
    )


class TestSelectBoard(unittest.TestCase):
    def test_swaps_transport_to_selected_board(self) -> None:
        app = FakeApp()

        changed = select_board(
            app,
            Board("Direct Ethernet", "192.168.1.20"),
            make_config(),
        )

        self.assertTrue(changed)
        self.assertEqual(app.transport.host, "192.168.1.20")
        self.assertEqual(app.transport.port, 6000)

    def test_board_port_overrides_config_port(self) -> None:
        app = FakeApp()

        select_board(
            app,
            Board("Odd port", "10.0.0.5", port=6100),
            make_config(),
        )

        self.assertEqual(app.transport.port, 6100)

    def test_updates_status_and_target_text(self) -> None:
        app = FakeApp()

        select_board(
            app,
            Board("Direct Ethernet", "192.168.1.20"),
            make_config(),
        )

        self.assertEqual(
            app.transport_target,
            " -> 192.168.1.20:6000",
        )
        self.assertIn("192.168.1.20:6000", app.status_var.get())

    def test_refuses_swap_outside_idle(self) -> None:
        app = FakeApp()
        app.coordinator_state = CoordinatorState.ACTIVE
        original = app.transport

        changed = select_board(
            app,
            Board("Direct Ethernet", "192.168.1.20"),
            make_config(),
        )

        self.assertFalse(changed)
        self.assertIs(app.transport, original)

    def test_records_board_selected_event(self) -> None:
        app = FakeApp()

        select_board(
            app,
            Board("Direct Ethernet", "192.168.1.20"),
            make_config(),
        )

        event_types = [event for event, _ in app.events]
        self.assertIn("BOARD_SELECTED", event_types)


class TestResolveInitialBoard(unittest.TestCase):
    def test_no_cli_host_selects_first_board(self) -> None:
        boards, selected = resolve_initial_board(make_config(), None)

        self.assertEqual(selected, make_config().boards[0])
        self.assertEqual(boards, make_config().boards)

    def test_cli_host_matching_known_board_selects_it(self) -> None:
        boards, selected = resolve_initial_board(
            make_config(),
            "orin-nano-01",
        )

        self.assertEqual(selected.name, "orin-nano-01")
        self.assertEqual(boards, make_config().boards)

    def test_unknown_cli_host_is_added_as_override(self) -> None:
        boards, selected = resolve_initial_board(
            make_config(),
            "127.0.0.1",
        )

        self.assertEqual(selected.host, "127.0.0.1")
        self.assertIn(selected, boards)
        self.assertEqual(len(boards), 3)


if __name__ == "__main__":
    unittest.main()
