"""Final operator refinements for the campaign GUI.

Applied after ``campaign_ui_simple.apply_campaign_ui``.

Rules:
* Preset 8/12/16 selections use the approved material conversion table.
* ``Custom...`` is the exact physical thickness entered by the operator.
* Beam ON/OFF controls are removed.
* Estimated fluence is calculated from flux x active test runtime.
"""

from __future__ import annotations

import time
import tkinter as tk
from types import MethodType
from typing import Any
from tkinter import ttk

from coordinator.campaign_config import (
    calculate_fluence,
    format_scientific,
    get_shield_configuration,
)


REFRESH_MS = 250
CUSTOM_OPTION = "Custom..."
PRESET_LEVELS = {"8", "12", "16"}


def _find_beam_frame(app: Any) -> ttk.LabelFrame | None:
    for child in app.winfo_children():
        if isinstance(child, ttk.LabelFrame) and child.cget("text") == "Beam Exposure":
            return child
    return None


def _hide_beam_on_off_controls(beam_frame: ttk.LabelFrame) -> None:
    """Remove controls that require the operator to mark physical beam state."""

    for child in beam_frame.winfo_children():
        text = str(child.cget("text")) if "text" in child.keys() else ""
        info = child.grid_info()
        row = int(info.get("row", -1)) if info else -1
        column = int(info.get("column", -1)) if info else -1

        if text in {"Beam ON", "Beam OFF", "Beam Status:", "Beam-on Time:"}:
            child.grid_remove()
            continue

        # Variable-value labels paired with Beam Status and Beam-on Time.
        if (row == 0 and column == 3) or (row == 1 and column == 3):
            child.grid_remove()


def apply_campaign_ui_final(app: Any) -> None:
    """Apply the approved preset/custom and simplified fluence workflow."""

    beam_frame = _find_beam_frame(app)
    if beam_frame is not None:
        _hide_beam_on_off_controls(beam_frame)
        beam_frame.configure(text="Beam Exposure")

        # Make the remaining fields occupy the available width cleanly.
        for child in beam_frame.winfo_children():
            text = str(child.cget("text")) if "text" in child.keys() else ""
            info = child.grid_info()
            if not info:
                continue
            if text == "Estimated Fluence:":
                child.grid_configure(row=1, column=0, sticky="w")
            elif text == "Facility Fluence (optional):":
                child.grid_configure(row=1, column=2, sticky="w")

    app._test_exposure_started_at = None
    app._test_exposure_after_id = None

    original_update_summary = app._update_summary
    original_start = app._on_start_test
    original_stop = app._on_stop_test

    def selected_display_data() -> tuple[str, float, bool, float | None]:
        """Return display material, physical thickness, custom flag, reference."""

        material = self_material = app.material_var.get()
        selection = app.thickness_var.get()

        if material == "Bare":
            return "Bare", 0.0, False, 0.0

        if material == CUSTOM_OPTION:
            thickness = app.material_custom_thicknesses.get(CUSTOM_OPTION)
            if not app.custom_shield_name or thickness is None:
                raise ValueError("custom shield details are incomplete")
            return app.custom_shield_name, float(thickness), True, None

        if selection == CUSTOM_OPTION:
            thickness = app.material_custom_thicknesses.get(material)
            if thickness is None:
                raise ValueError(f"custom {material} thickness is incomplete")
            return material, float(thickness), True, None

        if selection not in PRESET_LEVELS:
            raise ValueError("invalid preset reference level")

        reference = int(selection)
        configuration = get_shield_configuration(self_material, reference)
        return material, float(configuration.actual_thickness_mm), False, float(reference)

    def final_update_summary(self: Any) -> None:
        try:
            material, thickness, custom_value, reference = selected_display_data()
            parts = [f"{self.energy_var.get()} MeV", material]
            if material != "Bare":
                if custom_value:
                    parts.append(f"{thickness:g} mm custom")
                else:
                    parts.append(f"preset {int(reference or 0)} → {thickness:.2f} mm")
        except (TypeError, ValueError):
            parts = [f"{self.energy_var.get()} MeV", "complete shield details"]

        dut = (
            self.custom_dut_name
            if self.dut_type_var.get() == CUSTOM_OPTION
            else self.dut_type_var.get()
        )
        parts.append(dut)
        serial = self.dut_serial_var.get().strip()
        if serial:
            parts.append(f"Serial: {serial}")
        self.summary_var.set(" | ".join(parts))

    def refresh_estimate() -> None:
        if app._test_exposure_started_at is None:
            return
        elapsed = max(0.0, time.monotonic() - app._test_exposure_started_at)
        app.campaign_beam_seconds = elapsed
        app.campaign_calculated_fluence = calculate_fluence(
            app.campaign_selected_flux,
            elapsed,
        )
        app.beam_time_var.set(f"{elapsed:.1f} s test runtime")
        app.calculated_fluence_var.set(
            f"{format_scientific(app.campaign_calculated_fluence)} p/cm²"
        )

    def schedule_tick() -> None:
        refresh_estimate()
        if app._test_exposure_started_at is not None:
            app._test_exposure_after_id = app.master.after(REFRESH_MS, schedule_tick)

    def cancel_tick() -> None:
        if app._test_exposure_after_id is not None:
            try:
                app.master.after_cancel(app._test_exposure_after_id)
            except tk.TclError:
                pass
            app._test_exposure_after_id = None

    def final_start(self: Any) -> None:
        original_start()
        if self.coordinator_state.value != "active":
            return

        try:
            material, thickness, custom_value, reference = selected_display_data()
        except (TypeError, ValueError):
            material = self.material_var.get()
            thickness = 0.0
            custom_value = False
            reference = None

        metadata = getattr(self, "campaign_run_metadata", {})
        metadata.update(
            {
                "shield_material": material,
                "shield_reference_level": reference,
                "shield_physical_thickness_mm": thickness,
                "shield_custom_value": custom_value,
                "fluence_basis": "flux_p_cm2_s * active_test_runtime_seconds",
            }
        )
        self.campaign_run_metadata = metadata

        self.campaign_beam_seconds = 0.0
        self.campaign_calculated_fluence = 0.0
        self.calculated_fluence_var.set("0.000e+00 p/cm²")
        self._test_exposure_started_at = time.monotonic()
        cancel_tick()
        schedule_tick()
        self._record_event(
            "CAMPAIGN_EXPOSURE_ESTIMATE_STARTED",
            active_request_id=self.active_test_request_id,
            **metadata,
        )
        self._append_log(
            "Estimated fluence calculation started with the active test timer."
        )

    def final_stop(self: Any, automatic: bool = False) -> None:
        refresh_estimate()
        cancel_tick()
        app._test_exposure_started_at = None
        original_stop(automatic=automatic)

    app._update_summary = MethodType(final_update_summary, app)
    app._on_start_test = MethodType(final_start, app)
    app._on_stop_test = MethodType(final_stop, app)
    app.start_button.configure(command=app._on_start_test)
    app.stop_button.configure(command=app._on_stop_test)

    app._update_summary()
    app._append_log(
        "Preset conversions enabled; custom thicknesses remain exact. "
        "Estimated fluence now uses active test runtime."
    )
