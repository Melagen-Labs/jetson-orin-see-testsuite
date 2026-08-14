"""Board registry loaded from the operator's config.json."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_PORT = 6000
DEFAULT_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True)
class Board:
    """One selectable DUT target."""

    name: str
    host: str
    port: int | None = None


DEFAULT_BOARDS: tuple[Board, ...] = (
    Board("Direct Ethernet", "192.168.1.20"),
    Board("Local receiver", "127.0.0.1"),
)


@dataclass
class BoardConfig:
    """Boards plus shared connection settings."""

    boards: list[Board]
    port: int = DEFAULT_PORT
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    warnings: list[str] = field(default_factory=list)


CLI_OVERRIDE_NAME = "CLI override"


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


def _parse_board(
    entry: object,
    warnings: list[str],
) -> Board | None:
    if not isinstance(entry, dict):
        warnings.append(
            f"config.json: ignoring non-object board entry {entry!r}"
        )
        return None

    host = entry.get("ip") or entry.get("host")
    if not isinstance(host, str) or not host.strip():
        warnings.append(
            "config.json: ignoring board entry without an "
            f"'ip' (or 'host') value: {entry!r}"
        )
        return None

    host = host.strip()

    name = entry.get("name")
    if not isinstance(name, str) or not name.strip():
        name = host

    port = entry.get("port")
    if port is not None and (
        type(port) is not int or not 1 <= port <= 65_535
    ):
        warnings.append(
            f"config.json: ignoring invalid port {port!r} "
            f"for board {name!r}"
        )
        port = None

    return Board(name.strip(), host, port)


def load_board_config(path: str | Path) -> BoardConfig:
    """Load config.json, falling back to defaults on any problem."""

    path = Path(path)
    warnings: list[str] = []

    if not path.exists():
        return BoardConfig(boards=list(DEFAULT_BOARDS))

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("top-level value must be a JSON object")
    except (OSError, ValueError) as error:
        warnings.append(
            f"config.json could not be read ({error}); "
            "using built-in board list"
        )
        return BoardConfig(
            boards=list(DEFAULT_BOARDS),
            warnings=warnings,
        )

    port = payload.get("port", DEFAULT_PORT)
    if type(port) is not int or not 1 <= port <= 65_535:
        warnings.append(
            f"config.json: invalid port {port!r}; using {DEFAULT_PORT}"
        )
        port = DEFAULT_PORT

    timeout = payload.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
    if not isinstance(timeout, (int, float)) or timeout <= 0:
        warnings.append(
            f"config.json: invalid timeout_seconds {timeout!r}; "
            f"using {DEFAULT_TIMEOUT_SECONDS}"
        )
        timeout = DEFAULT_TIMEOUT_SECONDS

    boards = [
        board
        for entry in payload.get("boards", [])
        if (board := _parse_board(entry, warnings)) is not None
    ]

    if not boards:
        warnings.append(
            "config.json: no usable boards listed; "
            "using built-in board list"
        )
        boards = list(DEFAULT_BOARDS)

    return BoardConfig(
        boards=boards,
        port=port,
        timeout_seconds=float(timeout),
        warnings=warnings,
    )
