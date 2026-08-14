"""Reorganized operator layout for the campaign GUI.

Applied LAST, after every other campaign layer -- earlier layers'
row-shifting must not run over widgets this layer re-homes into frames.

Section order after this layer:

1. DUT and Run Information
2. Beam Parameters -- beam energy AND test duration join the flux row, so the
   three per-run values sit together; the free-standing Beam Energy and Test
   Duration rows at the top are removed
3. Shielding Configuration -- new frame owning material, thickness, and
   the preset-to-physical thickness conversion readout

The Selected Configuration summary section is removed; its shield
conversion detail now lives directly in Shielding Configuration.

The existing comboboxes are moved with ``grid(in_=...)`` rather than
recreated so every binding and state-gating closure from the earlier
layers keeps pointing at the live widgets.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from types import MethodType
from typing import Any

from coordinator.campaign_config import get_shield_configuration

CUSTOM_OPTION = "Custom..."


def _find_labelframe(app: Any, text: str) -> ttk.LabelFrame | None:
    for child in app.winfo_children():
        if isinstance(child, ttk.LabelFrame) and child.cget("text") == text:
            return child
    return None


def _find_label(app: Any, text: str) -> ttk.Label | None:
    for child in app.winfo_children():
        if isinstance(child, ttk.Label) and child.cget("text") == text:
            return child
    return None


def _shield_conversion_text(app: Any) -> str:
    """Describe the physical thickness the current shield selection maps to."""

    material = app.material_var.get()
    selection = app.thickness_var.get()

    if material == "Bare":
        return "0.00 mm (no shield)"

    if material == CUSTOM_OPTION:
        thickness = app.material_custom_thicknesses.get(CUSTOM_OPTION)
        if not app.custom_shield_name or thickness is None:
            return "enter custom shield details"
        return f"{thickness:g} mm ({app.custom_shield_name}, custom)"

    if selection == CUSTOM_OPTION:
        thickness = app.material_custom_thicknesses.get(material)
        if thickness is None:
            return f"enter custom {material} thickness"
        return f"{thickness:g} mm (custom, used exactly)"

    try:
        configuration = get_shield_configuration(material, int(selection))
    except (TypeError, ValueError):
        return "select shield details"
    return (
        f"preset {selection} → "
        f"{configuration.actual_thickness_mm:.2f} mm"
    )


def apply_campaign_ui_layout(app: Any) -> None:
    """Apply the reorganized section order and widget grouping."""

    energy_box, material_box, thickness_box = app._selection_widgets

    dut_frame = _find_labelframe(app, "DUT and Run Information")
    beam_frame = _find_labelframe(app, "Beam Parameters")
    summary_frame = _find_labelframe(app, "Selected Configuration")
    duration_label = _find_label(app, "Test Duration (s):")
    if None in (dut_frame, beam_frame, summary_frame, duration_label):
        raise RuntimeError(
            "Campaign layout requires the campaign UI layers to be applied first"
        )

    # The free-standing selector labels are superseded by in-frame labels.
    for text in ("Beam Energy:", "Shielding Material:", "Thickness (mm):"):
        label = _find_label(app, text)
        if label is not None:
            label.destroy()

    # Beam energy joins the flux row inside Beam Parameters.
    ttk.Label(beam_frame, text="Beam Energy (MeV):").grid(
        row=0, column=2, sticky="w", padx=(24, 8), pady=4
    )
    energy_box.configure(width=12)
    # This repo's UI wraps the energy dropdown together with its custom-value
    # entry in a frame (the "Custom..." beam energy option). Move that wrapper,
    # not the bare dropdown: tkinter refuses to re-grid a widget into a frame
    # outside its own parent, and the custom field has to travel with the
    # dropdown anyway. Falls back to the dropdown itself when it is unwrapped.
    energy_widget = energy_box.master if energy_box.master is not app else energy_box
    energy_widget.grid(in_=beam_frame, row=0, column=3, sticky="w", pady=4)
    # Created before the frame, so raise it above the frame surface.
    energy_widget.lift()
    # Test duration joins the same row, so flux, energy, and duration -- the
    # three values an operator sets per run -- are read and edited together.
    duration_label.grid(
        in_=beam_frame, row=0, column=4, sticky="w", padx=(24, 8), pady=4
    )
    app.duration_entry.configure(width=10)
    app.duration_entry.grid(in_=beam_frame, row=0, column=5, sticky="w", pady=4)
    # Both were created before the frame, so raise them above its surface.
    duration_label.lift()
    app.duration_entry.lift()

    # Keep flux, energy, and duration adjacent on the left; a trailing spacer
    # absorbs the leftover width.
    beam_frame.columnconfigure(1, weight=0)
    beam_frame.columnconfigure(6, weight=1)

    # Material and thickness share one row, mirroring the flux/energy row of
    # Beam Parameters; a trailing spacer column absorbs the leftover width.
    shield_frame = ttk.LabelFrame(app, text="Shielding Configuration", padding=12)
    shield_frame.columnconfigure(4, weight=1)

    ttk.Label(shield_frame, text="Shielding Material:").grid(
        row=0, column=0, sticky="w", padx=(0, 8), pady=4
    )
    material_box.configure(width=20)
    material_box.grid(in_=shield_frame, row=0, column=1, sticky="w", pady=4)
    material_box.lift()

    ttk.Label(shield_frame, text="Thickness (mm):").grid(
        row=0, column=2, sticky="w", padx=(24, 8), pady=4
    )
    thickness_box.configure(width=12)
    thickness_box.grid(in_=shield_frame, row=0, column=3, sticky="w", pady=4)
    thickness_box.lift()

    # The preset-to-physical conversion, previously only visible in the
    # Selected Configuration summary, now lives with the selectors it explains.
    app.shield_conversion_var = tk.StringVar()
    ttk.Label(shield_frame, text="Physical Thickness:").grid(
        row=1, column=0, sticky="w", padx=(0, 8), pady=(8, 0)
    )
    ttk.Label(
        shield_frame,
        textvariable=app.shield_conversion_var,
        font=("Segoe UI", 10, "bold"),
    ).grid(row=1, column=1, columnspan=3, sticky="w", pady=(8, 0))

    # Every selection-change path already funnels through _update_summary,
    # so hook the conversion readout there. Applied last, this wraps the
    # full summary chain from the earlier layers.
    original_update_summary = app._update_summary

    def layout_update_summary(self: Any) -> None:
        original_update_summary()
        self.shield_conversion_var.set(_shield_conversion_text(self))

    app._update_summary = MethodType(layout_update_summary, app)

    button_frame = app.start_button.master
    activity_frame = app.activity_log.master
    see_frame = app.see_log.master
    status_frame = next(
        child
        for child in app.winfo_children()
        if isinstance(child, ttk.Frame)
        and child not in (button_frame, activity_frame, see_frame)
    )

    activity_label = _find_label(app, "Activity Log")
    live_label = next(
        (
            child
            for child in app.winfo_children()
            if isinstance(child, ttk.Label)
            and str(child.cget("text")).startswith("Live SEEs")
        ),
        None,
    )

    row = 1

    # Test duration is no longer a top-level row -- it moved into the beam row
    # above, beside flux and energy.

    dut_frame.grid_configure(row=row, pady=(14, 8))
    row += 1

    beam_frame.grid_configure(row=row, pady=(0, 8))
    row += 1

    shield_frame.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(0, 8))
    row += 1

    # The summary section is redundant now that the conversion readout sits in
    # Shielding Configuration. summary_var stays alive for the earlier layers
    # that still write to it; it simply has no visible widget.
    summary_frame.destroy()

    button_frame.grid_configure(row=row)
    row += 1

    status_frame.grid_configure(row=row)
    row += 1

    if activity_label is not None:
        activity_label.grid_configure(row=row)
    if live_label is not None:
        live_label.grid_configure(row=row)
    row += 1

    activity_frame.grid_configure(row=row)
    see_frame.grid_configure(row=row)

    # Only the logs row stretches; clear the weights earlier layers left behind.
    for index in range(row + 4):
        app.rowconfigure(index, weight=0)
    app.rowconfigure(row, weight=1)

    app._update_summary()

    app._append_log(
        "Layout reorganized: test duration and DUT info first, beam parameters "
        "with energy selection, and shielding configuration with its physical-"
        "thickness conversion."
    )
