"""Start the simplified campaign coordinator GUI over TCP.

Boards are listed in config.json (see config.example.json) and picked
from the Target Board dropdown in the GUI. --host still works and
overrides the initial selection:

    python app_local_tcp.py
    python app_local_tcp.py --host 192.168.1.20
    python app_local_tcp.py --host orin-nano-03
    python app_local_tcp.py --host 127.0.0.1
"""

import argparse
import tkinter as tk
from pathlib import Path

from coordinator.board_config import load_board_config
from coordinator.board_selector import (
    apply_board_selector,
    resolve_initial_board,
)
from coordinator.campaign_storage_cleanup import apply_campaign_storage_cleanup
from coordinator.campaign_ui_final import apply_campaign_ui_final
from coordinator.campaign_ui_polished import apply_campaign_ui_polished
from coordinator.campaign_ui_simple import apply_campaign_ui
from coordinator.transport import TcpTransport
from coordinator.ui import TestCoordinatorApp

CONFIG_PATH = Path(__file__).resolve().parent / "config.json"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Launch the Melagen Lab Test Coordinator GUI over TCP.",
    )
    parser.add_argument(
        "--host",
        default=None,
        help=(
            "Optional DUT address override. Without it, the first board "
            "in config.json is selected; switch boards in the GUI."
        ),
    )
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--timeout", type=float, default=None)
    parser.add_argument("--see-log-root", default="arbiter_logs")
    parser.add_argument("--pull-script", default=None)
    parser.add_argument("--pull-timeout", type=float, default=900.0)
    args = parser.parse_args()

    config = load_board_config(CONFIG_PATH)
    if args.timeout is not None:
        config.timeout_seconds = args.timeout
    for warning in config.warnings:
        print(f"WARNING: {warning}")

    _, initial_board = resolve_initial_board(
        config,
        args.host,
        args.port,
    )

    root = tk.Tk()
    transport = TcpTransport(
        host=initial_board.host,
        port=initial_board.port or args.port or config.port,
        timeout_seconds=config.timeout_seconds,
    )
    app = TestCoordinatorApp(
        master=root,
        transport=transport,
        see_log_root=args.see_log_root,
        pull_script=args.pull_script,
        pull_timeout_s=args.pull_timeout,
    )
    apply_campaign_ui(app)
    apply_campaign_ui_final(app)
    apply_campaign_ui_polished(app)
    apply_campaign_storage_cleanup(app)
    apply_board_selector(app, config)
    root.mainloop()


if __name__ == "__main__":
    main()
