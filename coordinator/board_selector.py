"""Target-board selector: swap the GUI's TCP transport between DUTs."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Any

from coordinator.board_config import Board, BoardConfig
from coordinator.transport import TcpTransport
from coordinator.ui import CoordinatorState

CLI_OVERRIDE_NAME = "CLI override"


def board_label(board: Board, config: BoardConfig) -> str:
    """Human-readable combobox entry for one board."""

    port = board.port or config.port
    return f"{board.name} — {board.host}:{port}"


def resolve_initial_board(
    config: BoardConfig,
    cli_host: str | None,
    cli_port: int | None = None,
) -> tuple[list[Board], Board]:
    """Determine the selectable boards and the initially selected one.

    A --host that matches a configured board selects it; an unknown
    --host is appended as an extra "CLI override" entry so existing
    command lines keep working alongside config.json.
    """

    boards = list(config.boards)

    if cli_host is None:
        return boards, boards[0]

    for board in boards:
        if board.host == cli_host and (
            cli_port is None or (board.port or config.port) == cli_port
        ):
            return boards, board

    override = Board(CLI_OVERRIDE_NAME, cli_host, cli_port)
    boards.append(override)
    return boards, override


def select_board(
    app: Any,
    board: Board,
    config: BoardConfig,
) -> bool:
    """Point the app's transport at `board`. Only allowed while IDLE."""

    if app.coordinator_state != CoordinatorState.IDLE:
        app._append_log(
            "Target board can only be changed while IDLE "
            f"(currently {app.coordinator_state.name})."
        )
        return False

    port = board.port or config.port
    transport = TcpTransport(
        host=board.host,
        port=port,
        timeout_seconds=config.timeout_seconds,
    )

    app.transport = transport
    app.transport_mode = transport.mode_name
    app.transport_target = f" -> {transport.host}:{transport.port}"
    app.status_var.set(
        f"Ready - {app.transport_mode} mode{app.transport_target}"
    )
    app._append_log(
        f"Target board -> {board.name} "
        f"({transport.host}:{transport.port})"
    )
    app._record_event(
        "BOARD_SELECTED",
        board=board.name,
        host=transport.host,
        port=transport.port,
    )
    return True


def _shift_rows(app: Any, first_row: int, offset: int) -> None:
    for child in app.winfo_children():
        info = child.grid_info()
        if info and int(info["row"]) >= first_row:
            child.grid_configure(row=int(info["row"]) + offset)


def apply_board_selector(app: Any, config: BoardConfig) -> None:
    """Add a Target Board dropdown under the title row."""

    boards, selected = resolve_initial_board(
        config,
        getattr(app.transport, "host", None),
        getattr(app.transport, "port", None),
    )
    labels = [board_label(board, config) for board in boards]

    _shift_rows(app, first_row=1, offset=1)

    label = ttk.Label(app, text="Target Board:")
    label.grid(row=1, column=0, sticky="w", padx=(0, 15), pady=6)

    app.board_var = tk.StringVar(
        value=board_label(selected, config)
    )
    board_box = ttk.Combobox(
        app,
        textvariable=app.board_var,
        values=labels,
        state="readonly",
        width=25,
    )
    board_box.grid(row=1, column=1, sticky="ew", pady=6)
    app.board_selector = board_box

    def on_selected(_event: object = None) -> None:
        choice = app.board_var.get()
        board = boards[labels.index(choice)]
        if not select_board(app, board, config):
            # Revert the visible choice to the still-active target.
            current_host = getattr(app.transport, "host", None)
            for candidate in boards:
                if candidate.host == current_host:
                    app.board_var.set(board_label(candidate, config))
                    break

    board_box.bind("<<ComboboxSelected>>", on_selected)

    # Mirror Start/Stop gating: selector is only usable while IDLE.
    original_apply = app._apply_control_state

    def apply_with_selector() -> None:
        original_apply()
        board_box.configure(
            state=(
                "readonly"
                if app.coordinator_state == CoordinatorState.IDLE
                else "disabled"
            )
        )

    app._apply_control_state = apply_with_selector
    apply_with_selector()

    for warning in config.warnings:
        app._append_log(f"WARNING: {warning}")
