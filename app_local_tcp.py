"""Start the simplified campaign coordinator GUI over TCP.

Point it at the board under test with --host:

    python app_local_tcp.py --host 192.168.1.20
    python app_local_tcp.py --host orin-nano-03
    python app_local_tcp.py --host 100.x.y.z
    python app_local_tcp.py --host 127.0.0.1
"""

import argparse
import tkinter as tk

from coordinator.campaign_storage_cleanup import apply_campaign_storage_cleanup
from coordinator.campaign_ui_final import apply_campaign_ui_final
from coordinator.campaign_ui_polished import apply_campaign_ui_polished
from coordinator.campaign_ui_simple import apply_campaign_ui
from coordinator.transport import TcpTransport
from coordinator.ui import TestCoordinatorApp


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Launch the Melagen Lab Test Coordinator GUI over TCP.",
    )
    parser.add_argument(
        "--host",
        default="192.168.1.20",
        help=(
            "DUT address: 192.168.1.20 for direct Ethernet, a Tailscale "
            "name/IP, or 127.0.0.1 for the local receiver."
        ),
    )
    parser.add_argument("--port", type=int, default=6000)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--see-log-root", default="arbiter_logs")
    parser.add_argument("--pull-script", default=None)
    parser.add_argument("--pull-timeout", type=float, default=900.0)
    args = parser.parse_args()

    root = tk.Tk()
    transport = TcpTransport(
        host=args.host,
        port=args.port,
        timeout_seconds=args.timeout,
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
    root.mainloop()


if __name__ == "__main__":
    main()
