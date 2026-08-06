"""Apply 2026 campaign controls to the existing Tk coordinator GUI."""

from __future__ import annotations

import csv
import math
import time
import tkinter as tk
from types import MethodType
from typing import Any

from tkinter import messagebox, ttk

from coordinator.campaign_config import (
    CAMPAIGN_BEAM_ENERGIES_MEV,
    CAMPAIGN_DUT_TYPES,
    CAMPAIGN_SHIELDING_MATERIALS,
    CUSTOM_SHIELD_MATERIAL,
    DEFAULT_FLUX_P_CM2_S,
    MLC1_REFERENCE_LEVELS_MM,
    calculate_fluence,
    create_custom_shield_configuration,
    create_reference_shield_configuration,
    format_campaign_summary,
    format_scientific,
    get_shield_configuration,
    reference_levels_for,
    validate_flux,
)


FLUENCE_REFRESH_MS = 250


def _find_top_level_label(app: Any, text: str) -> ttk.Label | None:
    """Find one top-level form label by its current text."""

    for child in app.winfo_children():
        if isinstance(child, ttk.Label) and child.cget("text") == text:
            return child
    return None


def _shift_rows(app: Any, first_row: int, offset: int) -> None:
    """Move existing top-level grid children down to make room for new frames."""

    for child in app.winfo_children():
        grid_info = child.grid_info()
        if not grid_info:
            continue
        row = int(grid_info["row"])
        if row >= first_row:
            child.grid_configure(row=row + offset)


def apply_campaign_ui(app: Any) -> None:
    """Add campaign metadata, shield conversion, flux, and live fluence controls.

    The deployed protocol-v1 request remains unchanged. Preset shielding choices
    still transmit their existing reference level. DUT metadata, selected flux,
    calculated fluence, comments, and shield details are recorded coordinator-side.
    Custom shields and non-preset MLC references remain preview-only until the DUT
    request protocol is extended to accept them.
    """

    energy_box, material_box, thickness_box = app._selection_widgets

    energy_box.configure(
        values=[str(value) for value in CAMPAIGN_BEAM_ENERGIES_MEV]
    )
    material_box.configure(values=CAMPAIGN_SHIELDING_MATERIALS)
    thickness_box.configure(
        values=[str(value) for value in MLC1_REFERENCE_LEVELS_MM]
    )

    if app.energy_var.get() not in {
        str(value) for value in CAMPAIGN_BEAM_ENERGIES_MEV
    }:
        app.energy_var.set("125")

    if app.material_var.get() not in CAMPAIGN_SHIELDING_MATERIALS:
        app.material_var.set("MLC1")

    if not app.thickness_var.get().strip():
        app.thickness_var.set("12")

    title_label = _find_top_level_label(
        app,
        "Jetson Proton Test Coordinator",
    )
    if title_label is not None:
        title_label.configure(text="Melagen Lab Test Coordinator")

    reference_label = _find_top_level_label(
        app,
        "Shielding Thickness:",
    )
    if reference_label is not None:
        reference_label.configure(
            text="MLC1 / MLC2 Reference Level (mm):"
        )

    # Add three campaign frames before the existing summary frame.
    _shift_rows(app, first_row=5, offset=3)
    app.rowconfigure(9, weight=0)
    app.rowconfigure(11, weight=0)
    app.rowconfigure(12, weight=1)
    app.rowconfigure(14, weight=1)

    app.dut_type_var = tk.StringVar(value=CAMPAIGN_DUT_TYPES[0])
    app.dut_serial_var = tk.StringVar()
    app.custom_shield_name_var = tk.StringVar()
    app.custom_shield_thickness_var = tk.StringVar()
    app.flux_exponent_var = tk.DoubleVar(value=math.log10(DEFAULT_FLUX_P_CM2_S))
    app.flux_entry_var = tk.StringVar(value=format_scientific(DEFAULT_FLUX_P_CM2_S))
    app.flux_display_var = tk.StringVar()
    app.fluence_display_var = tk.StringVar(value="0.000e+00 p/cm²")
    app.elapsed_display_var = tk.StringVar(value="0.0 s")

    app.campaign_accumulated_fluence = 0.0
    app.campaign_elapsed_seconds = 0.0
    app.campaign_selected_flux = DEFAULT_FLUX_P_CM2_S
    app._campaign_fluence_started_at = None
    app._campaign_fluence_after_id = None

    details_frame = ttk.LabelFrame(
        app,
        text="DUT and Run Information",
        padding=12,
    )
    details_frame.grid(
        row=5,
        column=0,
        columnspan=2,
        sticky="ew",
        pady=(14, 8),
    )
    details_frame.columnconfigure(1, weight=1)
    details_frame.columnconfigure(3, weight=1)

    ttk.Label(details_frame, text="DUT:").grid(
        row=0, column=0, sticky="w", padx=(0, 8), pady=4
    )
    dut_box = ttk.Combobox(
        details_frame,
        textvariable=app.dut_type_var,
        values=CAMPAIGN_DUT_TYPES,
        state="readonly",
        width=23,
    )
    dut_box.grid(row=0, column=1, sticky="ew", padx=(0, 16), pady=4)

    ttk.Label(details_frame, text="DUT Serial Number:").grid(
        row=0, column=2, sticky="w", padx=(0, 8), pady=4
    )
    serial_entry = ttk.Entry(
        details_frame,
        textvariable=app.dut_serial_var,
        width=24,
    )
    serial_entry.grid(row=0, column=3, sticky="ew", pady=4)

    ttk.Label(details_frame, text="Comments / Run Notes:").grid(
        row=1, column=0, sticky="nw", padx=(0, 8), pady=4
    )
    comments_text = tk.Text(
        details_frame,
        height=3,
        wrap="word",
        font=("Segoe UI", 9),
    )
    comments_text.grid(
        row=1,
        column=1,
        columnspan=3,
        sticky="ew",
        pady=4,
    )

    # A dedicated frame makes the custom-name and thickness fields unambiguous.
    custom_frame = ttk.LabelFrame(
        app,
        text="Custom Shield Details",
        padding=12,
    )
    custom_frame.grid(
        row=6,
        column=0,
        columnspan=2,
        sticky="ew",
        pady=(0, 8),
    )
    custom_frame.columnconfigure(1, weight=1)
    custom_frame.columnconfigure(3, weight=1)

    ttk.Label(custom_frame, text="Shield Name:").grid(
        row=0, column=0, sticky="w", padx=(0, 8), pady=4
    )
    custom_name_entry = ttk.Entry(
        custom_frame,
        textvariable=app.custom_shield_name_var,
    )
    custom_name_entry.grid(
        row=0, column=1, sticky="ew", padx=(0, 16), pady=4
    )

    ttk.Label(custom_frame, text="Physical Thickness (mm):").grid(
        row=0, column=2, sticky="w", padx=(0, 8), pady=4
    )
    custom_thickness_entry = ttk.Entry(
        custom_frame,
        textvariable=app.custom_shield_thickness_var,
        width=16,
    )
    custom_thickness_entry.grid(row=0, column=3, sticky="ew", pady=4)

    ttk.Label(
        custom_frame,
        text=(
            "Custom shield details are saved as coordinator metadata. "
            "START_TEST remains blocked until the DUT protocol supports them."
        ),
        wraplength=720,
    ).grid(row=1, column=0, columnspan=4, sticky="w", pady=(4, 0))
    custom_frame.grid_remove()

    beam_frame = ttk.LabelFrame(
        app,
        text="Beam Flux and Calculated Fluence",
        padding=12,
    )
    beam_frame.grid(
        row=7,
        column=0,
        columnspan=2,
        sticky="ew",
        pady=(0, 8),
    )
    beam_frame.columnconfigure(1, weight=1)

    ttk.Label(beam_frame, text="Exact Flux (p/cm²/s):").grid(
        row=0, column=0, sticky="w", padx=(0, 10), pady=4
    )
    flux_entry = ttk.Entry(
        beam_frame,
        textvariable=app.flux_entry_var,
        width=18,
    )
    flux_entry.grid(row=0, column=1, sticky="w", pady=4)
    flux_apply_button = ttk.Button(
        beam_frame,
        text="Apply Exact Value",
        width=18,
    )
    flux_apply_button.grid(row=0, column=2, sticky="e", padx=(10, 0), pady=4)

    ttk.Label(beam_frame, text="Log Flux Slider:").grid(
        row=1, column=0, sticky="w", padx=(0, 10)
    )
    flux_scale = tk.Scale(
        beam_frame,
        from_=3.0,
        to=8.0,
        resolution=0.01,
        orient="horizontal",
        showvalue=False,
        variable=app.flux_exponent_var,
        highlightthickness=0,
        length=390,
    )
    flux_scale.grid(row=1, column=1, sticky="ew")
    ttk.Label(
        beam_frame,
        textvariable=app.flux_display_var,
        width=22,
        anchor="e",
    ).grid(row=1, column=2, sticky="e", padx=(10, 0))

    ttk.Label(beam_frame, text="10³").grid(row=2, column=1, sticky="w")
    ttk.Label(beam_frame, text="10⁸").grid(row=2, column=1, sticky="e")

    ttk.Label(beam_frame, text="Active-test elapsed:").grid(
        row=3, column=0, sticky="w", padx=(0, 10), pady=(8, 0)
    )
    ttk.Label(
        beam_frame,
        textvariable=app.elapsed_display_var,
    ).grid(row=3, column=1, sticky="w", pady=(8, 0))

    ttk.Label(beam_frame, text="Calculated Fluence:").grid(
        row=4, column=0, sticky="w", padx=(0, 10), pady=(6, 0)
    )
    ttk.Label(
        beam_frame,
        textvariable=app.fluence_display_var,
        font=("Segoe UI", 10, "bold"),
    ).grid(row=4, column=1, sticky="w", pady=(6, 0))
    ttk.Label(
        beam_frame,
        text="fluence = selected flux × active-test seconds",
    ).grid(row=4, column=2, sticky="e", padx=(10, 0), pady=(6, 0))

    original_apply_control_state = app._apply_control_state
    original_on_start_test = app._on_start_test
    original_on_stop_test = app._on_stop_test
    original_save_result_csv = app._save_result_csv

    syncing_flux = False

    def get_comments() -> str:
        return comments_text.get("1.0", "end-1c").strip()

    def selected_flux() -> float:
        return validate_flux(float(app.flux_entry_var.get().strip()))

    def set_flux(flux: float, *, update_slider: bool) -> None:
        nonlocal syncing_flux
        validated = validate_flux(flux)
        syncing_flux = True
        try:
            app.campaign_selected_flux = validated
            app.flux_entry_var.set(format_scientific(validated))
            app.flux_display_var.set(
                f"{format_scientific(validated)} p/cm²/s"
            )
            if update_slider:
                app.flux_exponent_var.set(math.log10(validated))
        finally:
            syncing_flux = False
        app._update_summary()

    def update_flux_from_slider(*_args: Any) -> None:
        if syncing_flux:
            return
        exponent = float(app.flux_exponent_var.get())
        set_flux(10.0 ** exponent, update_slider=False)

    def apply_exact_flux(*_args: Any) -> None:
        try:
            set_flux(float(app.flux_entry_var.get().strip()), update_slider=True)
        except (TypeError, ValueError) as error:
            messagebox.showerror(
                "Invalid Beam Flux",
                str(error),
                parent=app.master,
            )
            app.flux_entry_var.set(format_scientific(app.campaign_selected_flux))

    flux_apply_button.configure(command=apply_exact_flux)
    flux_entry.bind("<Return>", apply_exact_flux)
    flux_entry.bind("<FocusOut>", apply_exact_flux)

    def resolve_configuration() -> Any:
        material = app.material_var.get()

        if material == CUSTOM_SHIELD_MATERIAL:
            raw_thickness = app.custom_shield_thickness_var.get().strip()
            if not raw_thickness:
                raise ValueError("custom shield thickness is required")
            try:
                thickness = float(raw_thickness)
            except ValueError as error:
                raise ValueError(
                    "custom shield thickness must be numeric"
                ) from error
            return create_custom_shield_configuration(
                app.custom_shield_name_var.get(),
                thickness,
            )

        if material in ("MLC1", "MLC2"):
            raw_reference = app.thickness_var.get().strip()
            if not raw_reference:
                raise ValueError("MLC reference level is required")
            try:
                reference = float(raw_reference)
            except ValueError as error:
                raise ValueError("MLC reference level must be numeric") from error
            return create_reference_shield_configuration(material, reference)

        allowed = reference_levels_for(material)
        try:
            reference_mm = int(app.thickness_var.get())
        except ValueError as error:
            raise ValueError(
                f"{material} reference level must be one of {allowed}"
            ) from error
        return get_shield_configuration(material, reference_mm)

    def is_nonpreset_mlc_reference(configuration: Any) -> bool:
        if configuration.material not in ("MLC1", "MLC2"):
            return False
        return not any(
            math.isclose(
                float(configuration.reference_mm),
                float(preset),
                rel_tol=0.0,
                abs_tol=1e-9,
            )
            for preset in MLC1_REFERENCE_LEVELS_MM
        )

    def set_reference_control_state() -> None:
        idle = app.coordinator_state.value == "idle"
        material = app.material_var.get()
        if not idle or material in ("Bare", CUSTOM_SHIELD_MATERIAL):
            thickness_box.configure(state="disabled")
        elif material in ("MLC1", "MLC2"):
            # Editable combobox: choose 8/12/16 or type any positive number.
            thickness_box.configure(state="normal")
        else:
            # Aluminium remains limited to the approved preset references.
            thickness_box.configure(state="readonly")

    def campaign_update_summary(self: Any) -> None:
        material = self.material_var.get()
        is_custom = material == CUSTOM_SHIELD_MATERIAL

        if is_custom:
            custom_frame.grid()
            thickness_box.configure(values=())
            if reference_label is not None:
                reference_label.configure(text="Reference Level:")
        else:
            custom_frame.grid_remove()
            allowed = reference_levels_for(material)
            thickness_box.configure(values=[str(value) for value in allowed])
            if reference_label is not None:
                if material in ("MLC1", "MLC2"):
                    reference_label.configure(
                        text="MLC1 / MLC2 Reference Level (mm) — select or type:"
                    )
                elif material == "Aluminium":
                    reference_label.configure(
                        text="Aluminium Reference Level (preset):"
                    )
                else:
                    reference_label.configure(text="Reference Level:")

        try:
            configuration = resolve_configuration()
            self.campaign_shield_configuration = configuration
            self.campaign_configuration_id = configuration.configuration_id
            self.campaign_actual_thickness_mm = configuration.actual_thickness_mm
            shield_summary = format_campaign_summary(
                int(self.energy_var.get()),
                configuration,
            )
            if is_custom or is_nonpreset_mlc_reference(configuration):
                shield_summary += " | preview only"
        except (TypeError, ValueError):
            self.campaign_shield_configuration = None
            self.campaign_configuration_id = ""
            self.campaign_actual_thickness_mm = None
            if is_custom:
                shield_summary = (
                    f"{self.energy_var.get()} MeV | Custom shield: "
                    "enter a name and physical thickness"
                )
            else:
                shield_summary = (
                    f"{self.energy_var.get()} MeV | {material}: "
                    "enter a valid reference level"
                )

        serial = self.dut_serial_var.get().strip() or "not entered"
        self.summary_var.set(
            f"{shield_summary}\n"
            f"DUT: {self.dut_type_var.get()} | Serial: {serial} | "
            f"Flux: {format_scientific(self.campaign_selected_flux)} p/cm²/s"
        )
        set_reference_control_state()

    def refresh_fluence() -> None:
        if app._campaign_fluence_started_at is None:
            return
        elapsed = max(0.0, time.monotonic() - app._campaign_fluence_started_at)
        app.campaign_elapsed_seconds = elapsed
        app.campaign_accumulated_fluence = calculate_fluence(
            app.campaign_selected_flux,
            elapsed,
        )
        app.elapsed_display_var.set(f"{elapsed:.1f} s")
        app.fluence_display_var.set(
            f"{format_scientific(app.campaign_accumulated_fluence)} p/cm²"
        )

    def schedule_fluence_tick() -> None:
        refresh_fluence()
        if app.coordinator_state.value == "active":
            app._campaign_fluence_after_id = app.master.after(
                FLUENCE_REFRESH_MS,
                schedule_fluence_tick,
            )

    def cancel_fluence_tick() -> None:
        if app._campaign_fluence_after_id is not None:
            try:
                app.master.after_cancel(app._campaign_fluence_after_id)
            except tk.TclError:
                pass
            app._campaign_fluence_after_id = None

    def freeze_fluence() -> None:
        refresh_fluence()
        cancel_fluence_tick()
        app._campaign_fluence_started_at = None

    def campaign_apply_control_state(self: Any) -> None:
        original_apply_control_state()
        idle = self.coordinator_state.value == "idle"
        if idle and self._campaign_fluence_started_at is not None:
            freeze_fluence()

        dut_box.configure(state="readonly" if idle else "disabled")
        serial_entry.configure(state="normal" if idle else "disabled")
        comments_text.configure(state="normal" if idle else "disabled")
        flux_scale.configure(state="normal" if idle else "disabled")
        flux_entry.configure(state="normal" if idle else "disabled")
        flux_apply_button.configure(state="normal" if idle else "disabled")

        custom_state = (
            "normal"
            if idle and self.material_var.get() == CUSTOM_SHIELD_MATERIAL
            else "disabled"
        )
        custom_name_entry.configure(state=custom_state)
        custom_thickness_entry.configure(state=custom_state)
        set_reference_control_state()

    def campaign_on_start_test(self: Any) -> None:
        try:
            configuration = resolve_configuration()
            apply_exact_flux()
            selected = selected_flux()
        except (TypeError, ValueError) as error:
            messagebox.showerror(
                "Invalid Campaign Configuration",
                str(error),
                parent=self.master,
            )
            return

        if self.material_var.get() == CUSTOM_SHIELD_MATERIAL:
            messagebox.showinfo(
                "Custom Shield Preview",
                "The custom shield name and thickness are visible and can be "
                "recorded, but START_TEST is blocked until the Jetson request "
                "protocol supports custom materials and decimal thicknesses.",
                parent=self.master,
            )
            return

        if is_nonpreset_mlc_reference(configuration):
            messagebox.showinfo(
                "Custom MLC Reference Preview",
                "The typed MLC reference and calculated physical thickness are "
                "visible, but START_TEST is blocked until the Jetson request "
                "protocol accepts non-preset reference levels.",
                parent=self.master,
            )
            return

        # Normalize a typed preset such as 12.0 to the integer expected by protocol v1.
        if configuration.material in ("MLC1", "MLC2"):
            self.thickness_var.set(str(int(configuration.reference_mm)))

        self.campaign_selected_flux = selected
        self.campaign_run_metadata = {
            "dut_type": self.dut_type_var.get(),
            "dut_serial": self.dut_serial_var.get().strip(),
            "operator_comments": get_comments(),
            "flux_p_cm2_s": self.campaign_selected_flux,
            "fluence_formula": "flux_p_cm2_s * active_test_seconds",
            "shield_material": configuration.material,
            "shield_reference_mm": configuration.reference_mm,
            "shield_configuration_id": configuration.configuration_id,
            "shield_actual_thickness_mm": configuration.actual_thickness_mm,
        }

        original_on_start_test()

        if self.coordinator_state.value == "active":
            self.campaign_accumulated_fluence = 0.0
            self.campaign_elapsed_seconds = 0.0
            self.fluence_display_var.set("0.000e+00 p/cm²")
            self.elapsed_display_var.set("0.0 s")
            self._campaign_fluence_started_at = time.monotonic()
            cancel_fluence_tick()
            schedule_fluence_tick()
            self._record_event(
                "CAMPAIGN_RUN_METADATA",
                active_request_id=self.active_test_request_id,
                **self.campaign_run_metadata,
            )
            self._append_log(
                "Campaign metadata saved: "
                f"DUT={self.campaign_run_metadata['dut_type']} "
                f"serial={self.campaign_run_metadata['dut_serial'] or 'not entered'} "
                f"flux={format_scientific(self.campaign_selected_flux)} p/cm²/s"
            )

    def campaign_on_stop_test(
        self: Any,
        automatic: bool = False,
    ) -> None:
        refresh_fluence()
        original_on_stop_test(automatic=automatic)

        if self.coordinator_state.value == "idle":
            freeze_fluence()
            metadata = getattr(self, "campaign_run_metadata", {})
            self._record_event(
                "CAMPAIGN_FLUENCE_FINAL",
                **metadata,
                elapsed_active_seconds=self.campaign_elapsed_seconds,
                accumulated_fluence_p_cm2=self.campaign_accumulated_fluence,
            )
            self._append_log(
                "Final calculated fluence saved: "
                f"{format_scientific(self.campaign_accumulated_fluence)} p/cm² "
                f"over {self.campaign_elapsed_seconds:.1f} s"
            )

    def campaign_save_result_csv(
        self: Any,
        summary: dict[str, Any],
    ) -> Any:
        path = original_save_result_csv(summary)
        if path is None:
            return None

        metadata = getattr(self, "campaign_run_metadata", {})
        try:
            with open(path, "a", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow([])
                writer.writerow(["campaign_field", "value"])
                writer.writerow(["dut_type", metadata.get("dut_type", "")])
                writer.writerow(["dut_serial", metadata.get("dut_serial", "")])
                writer.writerow(
                    ["operator_comments", metadata.get("operator_comments", "")]
                )
                writer.writerow(
                    ["flux_p_cm2_s", metadata.get("flux_p_cm2_s", "")]
                )
                writer.writerow(
                    ["fluence_formula", metadata.get("fluence_formula", "")]
                )
                writer.writerow(
                    ["elapsed_active_seconds", self.campaign_elapsed_seconds]
                )
                writer.writerow(
                    [
                        "accumulated_fluence_p_cm2",
                        self.campaign_accumulated_fluence,
                    ]
                )
                writer.writerow(
                    ["shield_material", metadata.get("shield_material", "")]
                )
                writer.writerow(
                    [
                        "shield_reference_mm",
                        metadata.get("shield_reference_mm", ""),
                    ]
                )
                writer.writerow(
                    [
                        "shield_configuration_id",
                        metadata.get("shield_configuration_id", ""),
                    ]
                )
                writer.writerow(
                    [
                        "shield_actual_thickness_mm",
                        metadata.get("shield_actual_thickness_mm", ""),
                    ]
                )
        except OSError as error:
            self._append_log(f"Could not append campaign metadata to CSV: {error}")

        return path

    app._update_summary = MethodType(campaign_update_summary, app)
    app._apply_control_state = MethodType(campaign_apply_control_state, app)
    app._on_start_test = MethodType(campaign_on_start_test, app)
    app._on_stop_test = MethodType(campaign_on_stop_test, app)
    app._save_result_csv = MethodType(campaign_save_result_csv, app)

    app.start_button.configure(command=app._on_start_test)
    app.stop_button.configure(command=app._on_stop_test)

    app.flux_exponent_var.trace_add("write", update_flux_from_slider)
    app.dut_type_var.trace_add("write", lambda *_args: app._update_summary())
    app.dut_serial_var.trace_add("write", lambda *_args: app._update_summary())
    app.custom_shield_name_var.trace_add(
        "write", lambda *_args: app._update_summary()
    )
    app.custom_shield_thickness_var.trace_add(
        "write", lambda *_args: app._update_summary()
    )
    app.thickness_var.trace_add("write", lambda *_args: app._update_summary())

    app.master.title("Melagen Lab Test Coordinator")
    app.master.geometry("900x1120")
    app.master.minsize(800, 940)

    set_flux(DEFAULT_FLUX_P_CM2_S, update_slider=True)
    app._update_summary()
    app._apply_control_state()
    app._append_log(
        "2026 campaign controls loaded: DUT metadata, visible custom-shield "
        "details, editable MLC references, exact/log flux controls, and saved "
        "calculated fluence."
    )
