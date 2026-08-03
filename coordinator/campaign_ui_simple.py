"""Simplified operator-facing campaign controls.

This module intentionally leaves the deployed START_TEST wire protocol unchanged.
Only approved preset shields are transmitted. Custom DUTs, custom shields, and
custom per-material thicknesses are captured as coordinator metadata and blocked
from transmission until the DUT receiver schema is extended.
"""

from __future__ import annotations

import csv
import time
import tkinter as tk
from types import MethodType
from typing import Any
from tkinter import messagebox, simpledialog, ttk

from coordinator.campaign_config import (
    CAMPAIGN_BEAM_ENERGIES_MEV,
    DEFAULT_FLUX_P_CM2_S,
    calculate_fluence,
    format_scientific,
    get_shield_configuration,
    validate_flux,
)

CUSTOM_OPTION = "Custom..."
PRESET_THICKNESSES = (8, 12, 16)
DUT_OPTIONS = ("Jetson Orin Nano", CUSTOM_OPTION)
SHIELD_OPTIONS = ("Bare", "MLC1", "MLC2", "Aluminium", CUSTOM_OPTION)
FLUX_PRESETS = (1e3, 1e4, 1e5, 1e6, 1e7, 1e8)
REFRESH_MS = 250


def _top_label(app: Any, text: str) -> ttk.Label | None:
    for child in app.winfo_children():
        if isinstance(child, ttk.Label) and child.cget("text") == text:
            return child
    return None


def _shift_rows(app: Any, first_row: int, offset: int) -> None:
    for child in app.winfo_children():
        info = child.grid_info()
        if info and int(info["row"]) >= first_row:
            child.grid_configure(row=int(info["row"]) + offset)


def _find_grid_child(app: Any, row: int, widget_type: type) -> Any | None:
    for child in app.winfo_children():
        info = child.grid_info()
        if info and int(info["row"]) == row and isinstance(child, widget_type):
            return child
    return None


def apply_campaign_ui(app: Any) -> None:
    """Apply the approved simplified campaign interface."""

    energy_box, material_box, thickness_box = app._selection_widgets
    energy_box.configure(values=[str(v) for v in CAMPAIGN_BEAM_ENERGIES_MEV])
    material_box.configure(values=SHIELD_OPTIONS)
    thickness_box.configure(values=[str(v) for v in PRESET_THICKNESSES] + [CUSTOM_OPTION])

    if app.energy_var.get() not in {str(v) for v in CAMPAIGN_BEAM_ENERGIES_MEV}:
        app.energy_var.set("125")
    if app.material_var.get() not in SHIELD_OPTIONS:
        app.material_var.set("MLC1")
    if app.thickness_var.get() not in {"8", "12", "16", CUSTOM_OPTION}:
        app.thickness_var.set("12")

    title = _top_label(app, "Jetson Proton Test Coordinator")
    if title is not None:
        title.configure(text="Melagen Lab Test Coordinator")
    thickness_label = _top_label(app, "Shielding Thickness:")
    if thickness_label is not None:
        thickness_label.configure(text="Thickness (mm):")

    # Insert compact DUT and beam sections before Selected Configuration.
    _shift_rows(app, first_row=5, offset=2)

    app.dut_type_var = tk.StringVar(value="Jetson Orin Nano")
    app.dut_serial_var = tk.StringVar()
    app.custom_dut_name = ""
    app.custom_shield_name = ""
    app.material_custom_thicknesses: dict[str, float] = {}
    app.flux_var = tk.StringVar(value=format_scientific(DEFAULT_FLUX_P_CM2_S))
    app.facility_fluence_var = tk.StringVar()
    app.beam_status_var = tk.StringVar(value="OFF")
    app.beam_time_var = tk.StringVar(value="0.0 s")
    app.calculated_fluence_var = tk.StringVar(value="0.000e+00 p/cm²")
    app.campaign_selected_flux = DEFAULT_FLUX_P_CM2_S
    app.campaign_beam_seconds = 0.0
    app.campaign_calculated_fluence = 0.0
    app._beam_on_started_at: float | None = None
    app._beam_after_id: str | None = None

    dut_frame = ttk.LabelFrame(app, text="DUT and Run Information", padding=10)
    dut_frame.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(14, 8))
    dut_frame.columnconfigure(1, weight=1)
    dut_frame.columnconfigure(3, weight=2)

    ttk.Label(dut_frame, text="DUT:").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=4)
    dut_box = ttk.Combobox(
        dut_frame,
        textvariable=app.dut_type_var,
        values=DUT_OPTIONS,
        state="readonly",
        width=24,
    )
    dut_box.grid(row=0, column=1, sticky="ew", padx=(0, 18), pady=4)

    ttk.Label(dut_frame, text="Run Notes / Comments:").grid(
        row=0, column=2, sticky="nw", padx=(0, 8), pady=4
    )
    comments_text = tk.Text(dut_frame, height=4, wrap="word", font=("Segoe UI", 9))
    comments_text.grid(row=0, column=3, rowspan=2, sticky="nsew", pady=4)

    ttk.Label(dut_frame, text="Serial Number:").grid(
        row=1, column=0, sticky="w", padx=(0, 8), pady=4
    )
    serial_entry = ttk.Entry(dut_frame, textvariable=app.dut_serial_var)
    serial_entry.grid(row=1, column=1, sticky="ew", padx=(0, 18), pady=4)

    beam_frame = ttk.LabelFrame(app, text="Beam Exposure", padding=10)
    beam_frame.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(0, 8))
    beam_frame.columnconfigure(1, weight=1)

    ttk.Label(beam_frame, text="Beam Flux (p/cm²/s):").grid(
        row=0, column=0, sticky="w", padx=(0, 8), pady=4
    )
    flux_box = ttk.Combobox(
        beam_frame,
        textvariable=app.flux_var,
        values=[format_scientific(v) for v in FLUX_PRESETS],
        state="normal",
        width=20,
    )
    flux_box.grid(row=0, column=1, sticky="w", pady=4)

    ttk.Label(beam_frame, text="Beam Status:").grid(
        row=0, column=2, sticky="w", padx=(20, 8), pady=4
    )
    ttk.Label(beam_frame, textvariable=app.beam_status_var, font=("Segoe UI", 10, "bold")).grid(
        row=0, column=3, sticky="w", pady=4
    )

    beam_on_button = ttk.Button(beam_frame, text="Beam ON", width=12)
    beam_off_button = ttk.Button(beam_frame, text="Beam OFF", width=12)
    beam_on_button.grid(row=1, column=0, sticky="w", pady=4)
    beam_off_button.grid(row=1, column=1, sticky="w", pady=4)

    ttk.Label(beam_frame, text="Beam-on Time:").grid(
        row=1, column=2, sticky="w", padx=(20, 8), pady=4
    )
    ttk.Label(beam_frame, textvariable=app.beam_time_var).grid(row=1, column=3, sticky="w", pady=4)

    ttk.Label(beam_frame, text="Estimated Fluence:").grid(
        row=2, column=0, sticky="w", padx=(0, 8), pady=4
    )
    ttk.Label(
        beam_frame,
        textvariable=app.calculated_fluence_var,
        font=("Segoe UI", 10, "bold"),
    ).grid(row=2, column=1, sticky="w", pady=4)

    ttk.Label(beam_frame, text="Facility Fluence (optional):").grid(
        row=2, column=2, sticky="w", padx=(20, 8), pady=4
    )
    facility_entry = ttk.Entry(beam_frame, textvariable=app.facility_fluence_var, width=20)
    facility_entry.grid(row=2, column=3, sticky="w", pady=4)

    original_apply_control_state = app._apply_control_state
    original_on_start_test = app._on_start_test
    original_on_stop_test = app._on_stop_test
    original_save_result_csv = app._save_result_csv

    def comments() -> str:
        return comments_text.get("1.0", "end-1c").strip()

    def effective_dut() -> str:
        return app.custom_dut_name if app.dut_type_var.get() == CUSTOM_OPTION else app.dut_type_var.get()

    def prompt_custom_dut(*_args: Any) -> None:
        if app.dut_type_var.get() != CUSTOM_OPTION:
            app._update_summary()
            return
        name = simpledialog.askstring(
            "Custom DUT",
            "Enter the DUT name:",
            parent=app.master,
            initialvalue=app.custom_dut_name,
        )
        if name and name.strip():
            app.custom_dut_name = name.strip()
        else:
            app.dut_type_var.set("Jetson Orin Nano")
        app._update_summary()

    def prompt_custom_shield() -> bool:
        name = simpledialog.askstring(
            "Custom Shield",
            "Enter the shielding material name:",
            parent=app.master,
            initialvalue=app.custom_shield_name,
        )
        if not name or not name.strip():
            return False
        thickness = simpledialog.askfloat(
            "Custom Shield",
            "Enter the physical thickness in millimetres:",
            parent=app.master,
            minvalue=0.000001,
        )
        if thickness is None:
            return False
        app.custom_shield_name = name.strip()
        app.material_custom_thicknesses[CUSTOM_OPTION] = float(thickness)
        return True

    def prompt_material_thickness(material: str) -> bool:
        value = simpledialog.askfloat(
            f"Custom {material} Thickness",
            f"Enter the {material} physical thickness in millimetres.\n"
            "This value is used directly; no conversion is applied:",
            parent=app.master,
            minvalue=0.000001,
            initialvalue=app.material_custom_thicknesses.get(material),
        )
        if value is None:
            return False
        app.material_custom_thicknesses[material] = float(value)
        return True

    def material_changed(*_args: Any) -> None:
        material = app.material_var.get()
        if material == CUSTOM_OPTION:
            if not prompt_custom_shield():
                app.material_var.set("Bare")
                app.thickness_var.set("0")
        elif material == "Bare":
            app.thickness_var.set("0")
        elif app.thickness_var.get() not in {"8", "12", "16", CUSTOM_OPTION}:
            app.thickness_var.set("12")
        app._update_summary()
        app._apply_control_state()

    def thickness_changed(*_args: Any) -> None:
        material = app.material_var.get()
        if app.thickness_var.get() == CUSTOM_OPTION and material in {"MLC1", "MLC2", "Aluminium"}:
            if not prompt_material_thickness(material):
                app.thickness_var.set("12")
        app._update_summary()

    def shield_data() -> tuple[str, float, bool]:
        material = app.material_var.get()
        if material == "Bare":
            return "Bare", 0.0, False
        if material == CUSTOM_OPTION:
            thickness = app.material_custom_thicknesses.get(CUSTOM_OPTION)
            if not app.custom_shield_name or thickness is None:
                raise ValueError("custom shield name and thickness are required")
            return app.custom_shield_name, thickness, True
        if app.thickness_var.get() == CUSTOM_OPTION:
            thickness = app.material_custom_thicknesses.get(material)
            if thickness is None:
                raise ValueError(f"custom {material} thickness is required")
            return material, thickness, True
        thickness = float(app.thickness_var.get())
        return material, thickness, False

    def campaign_update_summary(self: Any) -> None:
        try:
            material, thickness, custom_value = shield_data()
            parts = [f"{self.energy_var.get()} MeV", material]
            if material != "Bare":
                parts.append(f"{thickness:g} mm")
            if custom_value:
                parts.append("custom value")
        except (TypeError, ValueError):
            parts = [f"{self.energy_var.get()} MeV", "complete shield details"]

        parts.append(effective_dut())
        serial = self.dut_serial_var.get().strip()
        if serial:
            parts.append(f"Serial: {serial}")
        self.summary_var.set(" | ".join(parts))

    def selected_flux() -> float:
        try:
            value = float(app.flux_var.get().strip())
        except ValueError as error:
            raise ValueError("Beam flux must be numeric, for example 1e7") from error
        return validate_flux(value)

    def refresh_beam() -> None:
        if app._beam_on_started_at is None:
            return
        app.campaign_beam_seconds = max(0.0, time.monotonic() - app._beam_on_started_at)
        app.campaign_calculated_fluence = calculate_fluence(
            app.campaign_selected_flux,
            app.campaign_beam_seconds,
        )
        app.beam_time_var.set(f"{app.campaign_beam_seconds:.1f} s")
        app.calculated_fluence_var.set(
            f"{format_scientific(app.campaign_calculated_fluence)} p/cm²"
        )

    def beam_tick() -> None:
        refresh_beam()
        if app._beam_on_started_at is not None:
            app._beam_after_id = app.master.after(REFRESH_MS, beam_tick)

    def beam_on() -> None:
        if app.coordinator_state.value != "active":
            messagebox.showinfo("Beam Control", "Start the test before marking Beam ON.", parent=app.master)
            return
        if app._beam_on_started_at is not None:
            return
        try:
            app.campaign_selected_flux = selected_flux()
        except ValueError as error:
            messagebox.showerror("Invalid Beam Flux", str(error), parent=app.master)
            return
        app._beam_on_started_at = time.monotonic()
        app.beam_status_var.set("ON")
        app._record_event("BEAM_ON", flux_p_cm2_s=app.campaign_selected_flux)
        app._append_log(f"Beam ON at {format_scientific(app.campaign_selected_flux)} p/cm²/s")
        beam_tick()

    def beam_off() -> None:
        if app._beam_on_started_at is None:
            return
        refresh_beam()
        if app._beam_after_id is not None:
            try:
                app.master.after_cancel(app._beam_after_id)
            except tk.TclError:
                pass
            app._beam_after_id = None
        app._beam_on_started_at = None
        app.beam_status_var.set("OFF")
        app._record_event(
            "BEAM_OFF",
            beam_on_seconds=app.campaign_beam_seconds,
            calculated_fluence_p_cm2=app.campaign_calculated_fluence,
        )
        app._append_log(
            f"Beam OFF; estimated fluence {format_scientific(app.campaign_calculated_fluence)} p/cm²"
        )

    beam_on_button.configure(command=beam_on)
    beam_off_button.configure(command=beam_off)

    def campaign_apply_control_state(self: Any) -> None:
        original_apply_control_state()
        idle = self.coordinator_state.value == "idle"
        dut_box.configure(state="readonly" if idle else "disabled")
        serial_entry.configure(state="normal" if idle else "disabled")
        comments_text.configure(state="normal" if idle else "disabled")
        flux_box.configure(state="normal" if idle or self.coordinator_state.value == "active" else "disabled")
        facility_entry.configure(state="normal" if idle or self.coordinator_state.value == "active" else "disabled")
        if idle:
            material_box.configure(state="readonly")
            if self.material_var.get() == "Bare" or self.material_var.get() == CUSTOM_OPTION:
                thickness_box.configure(state="disabled")
            else:
                thickness_box.configure(state="readonly")
        beam_on_button.configure(state="normal" if self.coordinator_state.value == "active" else "disabled")
        beam_off_button.configure(
            state="normal" if self.coordinator_state.value == "active" and self._beam_on_started_at is not None else "disabled"
        )

    def campaign_on_start_test(self: Any) -> None:
        try:
            material, thickness, custom_value = shield_data()
            flux = selected_flux()
        except (TypeError, ValueError) as error:
            messagebox.showerror("Invalid Campaign Configuration", str(error), parent=self.master)
            return

        if custom_value:
            messagebox.showinfo(
                "Custom Value Preview",
                "Custom shield or thickness values are saved as coordinator metadata, "
                "but START_TEST is blocked until the Jetson receiver protocol supports them.",
                parent=self.master,
            )
            return

        notes = comments()
        review = [
            f"DUT: {effective_dut()}",
            f"Energy: {self.energy_var.get()} MeV",
            f"Shield: {material}" + ("" if material == "Bare" else f", {thickness:g} mm"),
            f"Flux: {format_scientific(flux)} p/cm²/s",
        ]
        serial = self.dut_serial_var.get().strip()
        if serial:
            review.append(f"Serial: {serial}")
        if notes:
            review.append(f"Comments: {notes}")
        if not messagebox.askokcancel("Review Test Configuration", "\n".join(review), parent=self.master):
            return

        # Protocol v1 accepts only integer preset thicknesses.
        if material == "Bare":
            self.material_var.set("Bare")
            self.thickness_var.set("0")
        else:
            self.material_var.set(material)
            self.thickness_var.set(str(int(thickness)))

        self.campaign_selected_flux = flux
        self.campaign_beam_seconds = 0.0
        self.campaign_calculated_fluence = 0.0
        self.beam_time_var.set("0.0 s")
        self.calculated_fluence_var.set("0.000e+00 p/cm²")
        self.campaign_run_metadata = {
            "dut_type": effective_dut(),
            "dut_serial": serial,
            "operator_comments": notes,
            "flux_p_cm2_s": flux,
            "shield_material": material,
            "shield_thickness_mm": thickness,
        }
        original_on_start_test()
        if self.coordinator_state.value == "active":
            self._record_event(
                "CAMPAIGN_RUN_METADATA",
                active_request_id=self.active_test_request_id,
                **self.campaign_run_metadata,
            )
            if notes:
                self._append_log(f"Run comments: {notes}")

    def campaign_on_stop_test(self: Any, automatic: bool = False) -> None:
        beam_off()
        original_on_stop_test(automatic=automatic)
        if self.coordinator_state.value == "idle":
            facility_raw = self.facility_fluence_var.get().strip()
            facility_value: float | None = None
            if facility_raw:
                try:
                    facility_value = float(facility_raw)
                except ValueError:
                    self._append_log("Facility fluence was not saved: invalid numeric value")
            metadata = getattr(self, "campaign_run_metadata", {})
            self._record_event(
                "CAMPAIGN_FLUENCE_FINAL",
                **metadata,
                beam_on_seconds=self.campaign_beam_seconds,
                calculated_fluence_p_cm2=self.campaign_calculated_fluence,
                facility_reported_fluence_p_cm2=facility_value,
            )
            self._append_log(
                f"Final estimated fluence saved: {format_scientific(self.campaign_calculated_fluence)} p/cm²"
            )
            if metadata.get("operator_comments"):
                self._append_log(f"Completed run comments: {metadata['operator_comments']}")

    def campaign_save_result_csv(self: Any, summary: dict[str, Any]) -> Any:
        path = original_save_result_csv(summary)
        if path is None:
            return None
        metadata = getattr(self, "campaign_run_metadata", {})
        with open(path, "a", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow([])
            writer.writerow(["campaign_field", "value"])
            for key in (
                "dut_type",
                "dut_serial",
                "operator_comments",
                "flux_p_cm2_s",
                "shield_material",
                "shield_thickness_mm",
            ):
                writer.writerow([key, metadata.get(key, "")])
            writer.writerow(["beam_on_seconds", self.campaign_beam_seconds])
            writer.writerow(["calculated_fluence_p_cm2", self.campaign_calculated_fluence])
            writer.writerow(["facility_reported_fluence_p_cm2", self.facility_fluence_var.get().strip()])
        return path

    app._update_summary = MethodType(campaign_update_summary, app)
    app._apply_control_state = MethodType(campaign_apply_control_state, app)
    app._on_start_test = MethodType(campaign_on_start_test, app)
    app._on_stop_test = MethodType(campaign_on_stop_test, app)
    app._save_result_csv = MethodType(campaign_save_result_csv, app)
    app.start_button.configure(command=app._on_start_test)
    app.stop_button.configure(command=app._on_stop_test)

    material_box.bind("<<ComboboxSelected>>", material_changed, add="+")
    thickness_box.bind("<<ComboboxSelected>>", thickness_changed, add="+")
    dut_box.bind("<<ComboboxSelected>>", prompt_custom_dut)
    app.dut_serial_var.trace_add("write", lambda *_args: app._update_summary())

    # Arrange Activity Log and Live SEEs side by side using their existing widgets.
    activity_label = _top_label(app, "Activity Log")
    live_label = next(
        (
            child for child in app.winfo_children()
            if isinstance(child, ttk.Label) and str(child.cget("text")).startswith("Live SEEs")
        ),
        None,
    )
    activity_frame = _find_grid_child(app, 11, ttk.Frame)
    live_frame = _find_grid_child(app, 13, ttk.Frame)
    if activity_label is not None and live_label is not None and activity_frame is not None and live_frame is not None:
        activity_label.grid_configure(row=10, column=0, columnspan=1, sticky="w", padx=(0, 8))
        live_label.grid_configure(row=10, column=1, columnspan=1, sticky="w", padx=(8, 0))
        activity_frame.grid_configure(row=11, column=0, columnspan=1, sticky="nsew", padx=(0, 6))
        live_frame.grid_configure(row=11, column=1, columnspan=1, sticky="nsew", padx=(6, 0))
        app.rowconfigure(11, weight=1)
        app.rowconfigure(13, weight=0)

    app.master.title("Melagen Lab Test Coordinator")
    app.master.geometry("1040x900")
    app.master.minsize(900, 760)
    app._update_summary()
    app._apply_control_state()
    app._append_log("Simplified campaign workflow loaded.")
