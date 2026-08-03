"""Preview the simplified campaign GUI without contacting a Jetson."""

import tkinter as tk

from coordinator.campaign_storage_cleanup import apply_campaign_storage_cleanup
from coordinator.campaign_ui_final import apply_campaign_ui_final
from coordinator.campaign_ui_polished import apply_campaign_ui_polished
from coordinator.campaign_ui_simple import apply_campaign_ui
from coordinator.transport import MockTransport
from coordinator.ui import TestCoordinatorApp


def main() -> None:
    root = tk.Tk()
    app = TestCoordinatorApp(master=root, transport=MockTransport())
    apply_campaign_ui(app)
    apply_campaign_ui_final(app)
    apply_campaign_ui_polished(app)
    apply_campaign_storage_cleanup(app)
    root.mainloop()


if __name__ == "__main__":
    main()
