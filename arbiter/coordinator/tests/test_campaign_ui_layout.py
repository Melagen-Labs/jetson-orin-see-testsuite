"""Layout tests for the reorganized campaign GUI (campaign_ui_layout)."""

import tempfile
import unittest
from pathlib import Path

import tkinter as tk
from tkinter import ttk

from coordinator.campaign_storage_cleanup import apply_campaign_storage_cleanup
from coordinator.campaign_ui_final import apply_campaign_ui_final
from coordinator.campaign_ui_layout import apply_campaign_ui_layout
from coordinator.campaign_ui_polished import apply_campaign_ui_polished
from coordinator.campaign_ui_simple import apply_campaign_ui
from coordinator.event_logger import EventLogger
from coordinator.transport import MockTransport
from coordinator.ui import TestCoordinatorApp


def find_labelframe(app: TestCoordinatorApp, text: str) -> ttk.LabelFrame | None:
    for child in app.winfo_children():
        if isinstance(child, ttk.LabelFrame) and child.cget("text") == text:
            return child
    return None


def find_label(app: TestCoordinatorApp, text: str) -> ttk.Label | None:
    for child in app.winfo_children():
        if isinstance(child, ttk.Label) and child.cget("text") == text:
            return child
    return None


class CampaignLayoutTestCase(unittest.TestCase):
    """Build the layered app once per test; skip cleanly without a display."""

    def setUp(self) -> None:
        try:
            self.root = tk.Tk()
        except tk.TclError as error:  # pragma: no cover - headless environment
            self.skipTest(f"Tk display unavailable: {error}")
        self.root.withdraw()

        self._temp_dir = tempfile.TemporaryDirectory()
        temp_path = Path(self._temp_dir.name)

        self.app = TestCoordinatorApp(
            master=self.root,
            transport=MockTransport(),
            event_logger=EventLogger(temp_path / "events.jsonl"),
            see_log_root=temp_path / "arbiter_logs",
        )
        apply_campaign_ui(self.app)
        apply_campaign_ui_final(self.app)
        apply_campaign_ui_polished(self.app)
        apply_campaign_storage_cleanup(self.app)
        apply_campaign_ui_layout(self.app)

    def tearDown(self) -> None:
        self.root.destroy()
        self._temp_dir.cleanup()


class TestCampaignUiLayout(CampaignLayoutTestCase):

    def test_energy_box_joins_the_beam_flux_row(self) -> None:
        beam_frame = find_labelframe(self.app, "Beam Parameters")
        energy_box = self.app._selection_widgets[0]

        info = energy_box.grid_info()
        self.assertEqual(str(info["in"]), str(beam_frame))
        self.assertEqual(int(info["row"]), 0)

    def test_shielding_configuration_frame_holds_material_and_thickness(self) -> None:
        shield_frame = find_labelframe(self.app, "Shielding Configuration")
        self.assertIsNotNone(shield_frame)

        _, material_box, thickness_box = self.app._selection_widgets
        self.assertEqual(
            str(material_box.grid_info()["in"]),
            str(shield_frame),
        )
        self.assertEqual(
            str(thickness_box.grid_info()["in"]),
            str(shield_frame),
        )
        # Material and thickness share a row, like beam flux and energy.
        self.assertEqual(
            int(material_box.grid_info()["row"]),
            int(thickness_box.grid_info()["row"]),
        )

    def test_top_level_sections_are_in_the_requested_order(self) -> None:
        duration_row = int(self.app.duration_entry.grid_info()["row"])
        dut_row = int(
            find_labelframe(self.app, "DUT and Run Information").grid_info()["row"]
        )
        beam_row = int(
            find_labelframe(self.app, "Beam Parameters").grid_info()["row"]
        )
        shield_row = int(
            find_labelframe(self.app, "Shielding Configuration").grid_info()["row"]
        )
        button_row = int(self.app.start_button.master.grid_info()["row"])

        self.assertLess(duration_row, dut_row)
        self.assertLess(dut_row, beam_row)
        self.assertLess(beam_row, shield_row)
        self.assertLess(shield_row, button_row)

    def test_selected_configuration_section_is_removed(self) -> None:
        self.assertIsNone(find_labelframe(self.app, "Selected Configuration"))

    def test_preset_thickness_conversion_shown_in_shield_frame(self) -> None:
        self.app.material_var.set("MLC2")
        self.app.thickness_var.set("12")
        self.app._update_summary()
        self.assertEqual(
            self.app.shield_conversion_var.get(),
            "preset 12 → 10.83 mm",
        )

        self.app.material_var.set("MLC1")
        self.app.thickness_var.set("8")
        self.app._update_summary()
        self.assertEqual(
            self.app.shield_conversion_var.get(),
            "preset 8 → 8.00 mm",
        )

    def test_bare_shield_conversion_text(self) -> None:
        self.app.material_var.set("Bare")
        self.app.thickness_var.set("0")
        self.app._update_summary()
        self.assertEqual(
            self.app.shield_conversion_var.get(),
            "0.00 mm (no shield)",
        )

    def test_old_free_standing_selector_labels_are_gone(self) -> None:
        for text in ("Beam Energy:", "Shielding Material:", "Thickness (mm):"):
            self.assertIsNone(find_label(self.app, text), text)

    def test_summary_and_control_state_still_work(self) -> None:
        self.app._update_summary()
        self.assertIn("MeV", self.app.summary_var.get())

        # Must not raise; the state closures still target the moved widgets.
        self.app._apply_control_state()
        energy_box, material_box, thickness_box = self.app._selection_widgets
        self.assertEqual(str(energy_box.cget("state")), "readonly")
        self.assertEqual(str(material_box.cget("state")), "readonly")
        self.assertEqual(str(thickness_box.cget("state")), "readonly")


if __name__ == "__main__":
    unittest.main()
