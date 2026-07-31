"""Start the coordinator GUI wired to a TCP receiver (a real DUT, or local).

Point it at the board under test with --host:

    python app_local_tcp.py --host 192.168.1.20      # direct Ethernet
    python app_local_tcp.py --host orin-nano-03       # Tailscale MagicDNS name
    python app_local_tcp.py --host 100.x.y.z          # Tailscale IP
    python app_local_tcp.py --host 127.0.0.1          # a local test receiver

The DUT's test_control.service listens on TCP 6000 and returns the SEE summary
that the GUI then writes to results/test_<N>.csv. Unlike app.py (mock mode, which
never leaves the laptop), this sends real commands over the network.
"""

import argparse
import tkinter as tk

from coordinator.transport import TcpTransport
from coordinator.ui import TestCoordinatorApp


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Launch the Jetson Proton Test Coordinator GUI over TCP."
        ),
    )
    parser.add_argument(
        "--host",
        default="192.168.1.20",
        help=(
            "DUT address: 192.168.1.20 (direct Ethernet, the default), the "
            "board's Tailscale IP/name, or 127.0.0.1 for a local receiver."
        ),
    )
    parser.add_argument(
        "--port",
        type=int,
        default=6000,
        help="DUT control port (default: 6000).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=5.0,
        help="Per-command timeout in seconds (default: 5.0).",
    )
    args = parser.parse_args()

    root = tk.Tk()

    transport = TcpTransport(
        host=args.host,
        port=args.port,
        timeout_seconds=args.timeout,
    )

    TestCoordinatorApp(
        master=root,
        transport=transport,
    )

    root.mainloop()


if __name__ == "__main__":
    main()
