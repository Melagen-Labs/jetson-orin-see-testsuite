"""Start the coordinator using the local TCP receiver."""

import tkinter as tk

from coordinator.transport import TcpTransport
from coordinator.ui import TestCoordinatorApp


def main() -> None:
    root = tk.Tk()

    transport = TcpTransport(
        host="127.0.0.1",
        port=6000,
        timeout_seconds=5.0,
    )

    TestCoordinatorApp(
        master=root,
        transport=transport,
    )

    root.mainloop()


if __name__ == "__main__":
    main()