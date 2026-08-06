"""Second-stage campaign UX refinements.

This module is applied after ``campaign_ui.apply_campaign_ui``. It keeps the
existing protocol-v1 boundary while making the operator workflow explicit:

* MLC typed values are always MLC1-equivalent references.
* The calculated physical coupon thickness is read-only.
* Selecting Custom prompts for shield name and thickness.
* Fluence accumulates only while the operator marks Beam ON.
* Facility-reported fluence is stored alongside calculated fluence.
* Activity Log and Live SEEs are arranged side by side.
"""

from __future__ import annotations

import csv
import math
import time
import tkinter as tk
from types import MethodType
from typing import Any

from tkinter import messagebox, simpledialog, ttk

from coordinator.campaign_config import (
    CUSTOM_SHIELD_MATERIAL,
    MLC1_REFERENCE_LEVELS_MM,
    calculate_fluence,
    format_scientific,
)


BEAM_REFRESH_MS = 250


def _find_label(app: Any, text: str) -> ttk.Label | None:
    for child in app.winfo_children():
        if isinstance(child, ttk.Label) and child.cget("text") == text:
            return child
    return None


def _find_labelframe(app: Any, text: str) -> ttk.LabelFrame | None:
    for child in app.winfo_children():
        if isinstance(child, ttk.LabelFrame) and child.cget("text") == text:
            return child
    return None


def _find_text(frame: tk.Misc) -> tk.Text | None:
    for child in frame.winfo_children():
        if isinstance(child, tk.Text):
            return child
    return None


def _shift_top_level_rows(app: Any, first_row: int, offset: int) -> None:
    for child in app.winfo_children():
        info = child.grid_info()
        if not info:
            continue
        row = int(info["row"])
        if row >= first_row:
            child.grid_configure(row=row + offset)


def apply_campaign_ui_v2(app: Any) -> None:
    """Apply the approved Option-A campaign workflow."""

    energy_box, material_box, reference_box = app._selection_widgets

    # Make room for a clear, read-only physical-thickness line.
    _shift_top_level_rows(app, first_row=4, offset=1)
    actual_label = ttk.Label(app, text="Calculated Physical Thickness:")
    actual_label.grid(row=4, column=0, sticky="w", padx=(0, 15), pady=6)
    app.actual_thickness_display_var = tk.StringVar(value="—")
    ttk.Label(
        app,
        textvariable=app.actual_thickness_display_var,
        font=("Segoe UI", 10, "bold"),
    ).grid(row=4, column=1, sticky="w", pady=6)

    reference_label = _find_label(app, "MLC1 / MLC2 Reference Level (mm):")
    if reference_label is None:
        reference_label = _find_label(
            app,
            "MLC1 / MLC2 Reference Level (mm) — select or type:",
        )

    details_frame = _find_labelframe(app, "DUT and Run Information")
    custom_frame = _find_labelframe(app, "Custom Shield Details")
    beam_frame = _find_labelframe(app, "Beam Flux and Calculated Fluence")
    comments_text = _find_text(details_frame) if details_frame is not None else None

    # Custom frame remains visible only for Custom, but now selection prompts the
    # operator immediately, as requested.
    previous_material = {"value": app.material_var.get()}
    handling_material = {"active": False}

    def prompt_custom_shield() -> bool:
        name = simpledialog.askstring(
            "Custom Shield",
            "Enter the shielding material name:",
            initialvalue=app.custom_shield_name_var.get(),
            parent=app.master,
        )
        if name is None:
            return False
        name = name.strip()
        if not name:
            messagebox.showerror(
                "Invalid Custom Shield",
                "The shielding material name cannot be blank.",
                parent=app.master,
            )
            return False

        thickness = simpledialog.askstring(
            "Custom Shield",
            "Enter the physical thickness in millimetres:",
            initialvalue=app.custom_shield_thickness_var.get(),
            parent=app.master,
        )
        if thickness is None:
            return False
        try:
            value = float(thickness)
        except ValueError:
            messagebox.showerror(
                "Invalid Custom Shield",
                "Thickness must be a number greater than zero.",
                parent=app.master,
            )
            return False
        if not math.isfinite(value) or value <= 0:
            messagebox.showerror(
                "Invalid Custom Shield",
                "Thickness must be a number greater than zero.",
                parent=app.master,
            )
            return False

        app.custom_shield_name_var.set(name)
        app.custom_shield_thickness_var.set(f"{value:g}")
        return True

    def on_material_selected(_event: Any = None) -> None:
        if handling_material["active"]:
            return
        selected = app.material_var.get()
        if selected == CUSTOM_SHIELD_MATERIAL:
            handling_material["active"] = True
            try:
                if not prompt_custom_shield():
                    app.material_var.set(previous_material["value"])
                else:
                    previous_material["value"] = selected
            finally:
                handling_material["active"] = False
        else:
            previous_material["value"] = selected
        app._update_summary()

    material_box.bind("<<ComboboxSelected>>", on_material_selected, add="+")

    # Reframe the beam controls: exact flux is authoritative; slider is a helper;
    # fluence accrues only while Beam ON is active.
    app.facility_fluence_var = tk.StringVar()
    app.beam_status_var = tk.StringVar(value="OFF")
    app.beam_on_elapsed_var = tk.StringVar(value="0.0 s")
    app._beam_segment_started_at = None
    app._beam_accumulated_seconds = 0.0
    app._beam_after_id = None

    if beam_frame is not None:
        next_row = max(
            (int(child.grid_info().get("row", 0)) for child in beam_frame.winfo_children()),
            default=0,
        ) + 1

        ttk.Separator(beam_frame, orient="horizontal").grid(
            row=next_row,
            column=0,
            columnspan=3,
            sticky="ew",
            pady=(10, 8),
        )
        ttk.Label(beam_frame, text="Beam Status:").grid(
            row=next_row + 1, column=0, sticky="w", padx=(0, 10), pady=4
        )
        status_label = ttk.Label(
            beam_frame,
            textvariable=app.beam_status_var,
            font=("Segoe UI", 10, "bold"),
        )
        status_label.grid(row=next_row + 1, column=1, sticky="w", pady=4)

        button_frame = ttk.Frame(beam_frame)
        button_frame.grid(row=next_row + 1, column=2, sticky="e")
        beam_on_button = ttk.Button(button_frame, text="Beam ON", width=12)
        beam_off_button = ttk.Button(button_frame, text="Beam OFF", width=12)
        beam_on_button.grid(row=0, column=0, padx=(0, 6))
        beam_off_button.grid(row=0, column=1)

        ttk.Label(beam_frame, text="Beam-on Time:").grid(
            row=next_row + 2, column=0, sticky="w", padx=(0, 10), pady=4
        )
        ttk.Label(beam_frame, textvariable=app.beam_on_elapsed_var).grid(
            row=next_row + 2, column=1, sticky="w", pady=4
        )

        ttk.Label(beam_frame, text="Facility-Reported Fluence:").grid(
            row=next_row + 3, column=0, sticky="w", padx=(0, 10), pady=4
        )
        facility_entry = ttk.Entry(
            beam_frame,
            textvariable=app.facility_fluence_var,
            width=20,
        )
        facility_entry.grid(row=next_row + 3, column=1, sticky="w", pady=4)
        ttk.Label(beam_frame, text="p/cm² (optional, entered after run)").grid(
            row=next_row + 3, column=2, sticky="e", padx=(10, 0), pady=4
        )
    else:
        beam_on_button = None
        beam_off_button = None
        facility_entry = None

    def current_comments() -> str:
        if comments_text is None:
            return ""
        return comments_text.get("1.0", "end-1c").strip()

    def current_beam_seconds() -> float:
        total = app._beam_accumulated_seconds
        if app._beam_segment_started_at is not None:
            total += max(0.0, time.monotonic() - app._beam_segment_started_at)
        return total

    def cancel_beam_tick() -> None:
        if app._beam_after_id is not None:
            try:
                app.master.after_cancel(app._beam_after_id)
            except tk.TclError:
                pass
            app._beam_after_id = None

    def refresh_beam() -> None:
        seconds = current_beam_seconds()
        app.campaign_elapsed_seconds = seconds
        app.campaign_accumulated_fluence = calculate_fluence(
            app.campaign_selected_flux,
            seconds,
        )
        app.beam_on_elapsed_var.set(f"{seconds:.1f} s")
        app.elapsed_display_var.set(f"{seconds:.1f} s beam-on")
        app.fluence_display_var.set(
            f"{format_scientific(app.campaign_accumulated_fluence)} p/cm²"
        )

    def schedule_beam_tick() -> None:
        refresh_beam()
        if app._beam_segment_started_at is not None:
            app._beam_after_id = app.master.after(BEAM_REFRESH_MS, schedule_beam_tick)

    def beam_on() -> None:
        if app.coordinator_state.value != "active":
            messagebox.showwarning(
                "Test Not Active",
                "Start the test before marking the beam ON.",
                parent=app.master,
            )
            return
        if app._beam_segment_started_at is not None:
            return
        app._beam_segment_started_at = time.monotonic()
        app.beam_status_var.set("ON")
        cancel_beam_tick()
        schedule_beam_tick()
        app._record_event(
            "CAMPAIGN_BEAM_ON",
            active_request_id=app.active_test_request_id,
            flux_p_cm2_s=app.campaign_selected_flux,
            accumulated_beam_seconds=app._beam_accumulated_seconds,
        )
        app._append_log(
            "Beam marked ON; calculated fluence accumulation started."
        )

    def beam_off(*, record: bool = True) -> None:
        if app._beam_segment_started_at is None:
            app.beam_status_var.set("OFF")
            return
        app._beam_accumulated_seconds = current_beam_seconds()
        app._beam_segment_started_at = None
        cancel_beam_tick()
        refresh_beam()
        app.beam_status_var.set("OFF")
        if record:
            app._record_event(
                "CAMPAIGN_BEAM_OFF",
                active_request_id=app.active_test_request_id,
                beam_on_seconds=app._beam_accumulated_seconds,
                calculated_fluence_p_cm2=app.campaign_accumulated_fluence,
            )
            app._append_log(
                "Beam marked OFF; calculated fluence accumulation paused."
            )

    if beam_on_button is not None:
        beam_on_button.configure(command=beam_on)
    if beam_off_button is not None:
        beam_off_button.configure(command=beam_off)

    # Preserve the current campaign methods, then add the approved workflow.
    original_update_summary = app._update_summary
    original_apply_state = app._apply_control_state
    original_start = app._on_start_test
    original_stop = app._on_stop_test
    original_save_csv = app._save_result_csv
    original_format_results = app._format_results

    def refined_update_summary(self: Any) -> None:
        original_update_summary()
        configuration = getattr(self, "campaign_shield_configuration", None)
        material = self.material_var.get()

        if reference_label is not None:
            if material in ("MLC1", "MLC2"):
                reference_label.configure(
                    text="MLC1-Equivalent Reference (mm) — preset or type:"
                )
            elif material == "Aluminium":
                reference_label.configure(text="Aluminium Reference (preset):")
            else:
                reference_label.configure(text="Reference Level:")

        if configuration is None:
            self.actual_thickness_display_var.set("—")
        elif material == "Bare":
            self.actual_thickness_display_var.set("0.00 mm (bare control)")
        else:
            self.actual_thickness_display_var.set(
                f"{float(configuration.actual_thickness_mm):.2f} mm — read only"
            )

    def refined_apply_state(self: Any) -> None:
        original_apply_state()
        active = self.coordinator_state.value == "active"
        if beam_on_button is not None:
            beam_on_button.configure(state="normal" if active else "disabled")
        if beam_off_button is not None:
            beam_off_button.configure(state="normal" if active else "disabled")
        if facility_entry is not None:
            facility_entry.configure(state="normal")

    def refined_start(self: Any) -> None:
        comments = current_comments()
        serial = self.dut_serial_var.get().strip() or "not entered"
        configuration = getattr(self, "campaign_shield_configuration", None)
        shield_text = (
            getattr(configuration, "configuration_id", "not resolved")
            if configuration is not None
            else "not resolved"
        )
        approved = messagebox.askyesno(
            "Review Campaign Run",
            "Review campaign metadata before START_TEST\n\n"
            f"DUT: {self.dut_type_var.get()}\n"
            f"Serial: {serial}\n"
            f"Shield: {shield_text}\n"
            f"Comments: {comments or 'none'}\n"
            f"Flux: {format_scientific(self.campaign_selected_flux)} p/cm²/s\n\n"
            "Continue to the command confirmation?",
            parent=self.master,
        )
        if not approved:
            return

        original_start()
        if self.coordinator_state.value == "active":
            # The previous adapter starts fluence with test-active time. Stop that
            # timer immediately; Option A uses explicit Beam ON/OFF time instead.
            if self._campaign_fluence_after_id is not None:
                try:
                    self.master.after_cancel(self._campaign_fluence_after_id)
                except tk.TclError:
                    pass
                self._campaign_fluence_after_id = None
            self._campaign_fluence_started_at = None
            self._beam_segment_started_at = None
            self._beam_accumulated_seconds = 0.0
            self.campaign_elapsed_seconds = 0.0
            self.campaign_accumulated_fluence = 0.0
            self.beam_status_var.set("OFF")
            self.beam_on_elapsed_var.set("0.0 s")
            self.elapsed_display_var.set("0.0 s beam-on")
            self.fluence_display_var.set("0.000e+00 p/cm²")
            self._append_log(
                f"Run comments: {comments or 'none'}"
            )

    def refined_stop(self: Any, automatic: bool = False) -> None:
        beam_off(record=True)
        metadata = getattr(self, "campaign_run_metadata", {})
        facility_raw = self.facility_fluence_var.get().strip()
        facility_value: float | None = None
        if facility_raw:
            try:
                facility_value = float(facility_raw)
                if not math.isfinite(facility_value) or facility_value < 0:
                    raise ValueError
            except ValueError:
                messagebox.showerror(
                    "Invalid Facility Fluence",
                    "Facility-reported fluence must be a non-negative number.",
                    parent=self.master,
                )
                return
        metadata["beam_on_seconds"] = self._beam_accumulated_seconds
        metadata["calculated_fluence_p_cm2"] = self.campaign_accumulated_fluence
        metadata["facility_reported_fluence_p_cm2"] = facility_value
        metadata["operator_comments"] = current_comments()
        self.campaign_run_metadata = metadata
        original_stop(automatic=automatic)

    def refined_save_csv(self: Any, summary: dict[str, Any]) -> Any:
        path = original_save_csv(summary)
        if path is None:
            return None
        metadata = getattr(self, "campaign_run_metadata", {})
        try:
            with open(path, "a", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(["beam_on_seconds", metadata.get("beam_on_seconds", "")])
                writer.writerow(
                    [
                        "calculated_fluence_p_cm2",
                        metadata.get("calculated_fluence_p_cm2", ""),
                    ]
                )
                writer.writerow(
                    [
                        "facility_reported_fluence_p_cm2",
                        metadata.get("facility_reported_fluence_p_cm2", ""),
                    ]
                )
        except OSError as error:
            self._append_log(f"Could not append beam metadata to CSV: {error}")
        return path

    def refined_format_results(
        self: Any,
        stopped_target_id: str,
        summary: dict[str, Any] | None,
        csv_path: Any,
    ) -> str:
        base = original_format_results(stopped_target_id, summary, csv_path)
        metadata = getattr(self, "campaign_run_metadata", {})
        comments = metadata.get("operator_comments", "") or "none"
        facility = metadata.get("facility_reported_fluence_p_cm2")
        facility_text = "not entered" if facility is None else format_scientific(facility)
        return (
            f"{base}\n\nCampaign exposure:\n"
            f"  Beam-on time: {metadata.get('beam_on_seconds', 0):.1f} s\n"
            f"  Calculated fluence: "
            f"{format_scientific(metadata.get('calculated_fluence_p_cm2', 0))} p/cm²\n"
            f"  Facility fluence: {facility_text} p/cm²\n"
            f"  Comments: {comments}"
        )

    app._update_summary = MethodType(refined_update_summary, app)
    app._apply_control_state = MethodType(refined_apply_state, app)
    app._on_start_test = MethodType(refined_start, app)
    app._on_stop_test = MethodType(refined_stop, app)
    app._save_result_csv = MethodType(refined_save_csv, app)
    app._format_results = MethodType(refined_format_results, app)
    app.start_button.configure(command=app._on_start_test)
    app.stop_button.configure(command=app._on_stop_test)

    # Side-by-side operational panels.
    activity_label = _find_label(app, "Activity Log")
    live_label = None
    for child in app.winfo_children():
        if isinstance(child, ttk.Label) and str(child.cget("text")).startswith("Live SEEs"):
            live_label = child
            break

    activity_frame = app.activity_log.master
    live_frame = app.see_log.master
    if activity_label is not None and live_label is not None:
        row = min(
            int(activity_label.grid_info()["row"]),
            int(live_label.grid_info()["row"]),
        )
        activity_label.grid_configure(row=row, column=0, columnspan=1, sticky="w")
        live_label.grid_configure(row=row, column=1, columnspan=1, sticky="w")
        activity_frame.grid_configure(
            row=row + 1, column=0, columnspan=1, sticky="nsew", padx=(0, 6)
        )
        live_frame.grid_configure(
            row=row + 1, column=1, columnspan=1, sticky="nsew", padx=(6, 0)
        )
        app.columnconfigure(0, weight=1)
        app.columnconfigure(1, weight=1)
        app.rowconfigure(row + 1, weight=1)

    app.master.geometry("1100x900")
    app.master.minsize(920, 760)
    app._update_summary()
    app._apply_control_state()
    app._append_log(
        "Option-A workflow loaded: MLC1-equivalent input, read-only physical "
        "thickness, custom-shield prompt, Beam ON/OFF fluence, and side-by-side logs."
    )
