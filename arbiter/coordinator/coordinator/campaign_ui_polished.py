"""Final visual cleanup and authoritative runtime-based fluence behavior.

Applied after ``campaign_ui_simple.apply_campaign_ui``. This layer removes
operator beam ON/OFF controls and the facility-fluence field, then calculates
fluence from the interval in which the coordinator is actually ACTIVE. It also
tightens spacing and standardizes labels without changing the deployed DUT
protocol.
"""

from __future__ import annotations

import time
import tkinter as tk
from types import MethodType
from typing import Any
from tkinter import ttk

from coordinator.campaign_config import calculate_fluence, format_scientific

REFRESH_MS = 250


def _walk(widget: tk.Misc):
    for child in widget.winfo_children():
        yield child
        yield from _walk(child)


def _find_labelframe(app: Any, text: str) -> ttk.LabelFrame | None:
    for child in app.winfo_children():
        if isinstance(child, ttk.LabelFrame) and child.cget("text") == text:
            return child
    return None


def _find_label_by_text(parent: tk.Misc, text: str) -> ttk.Label | None:
    for child in _walk(parent):
        if isinstance(child, ttk.Label) and child.cget("text") == text:
            return child
    return None


def _hide_widget(widget: tk.Misc | None) -> None:
    if widget is None:
        return
    manager = widget.winfo_manager()
    if manager == "grid":
        widget.grid_remove()
    elif manager == "pack":
        widget.pack_forget()
    elif manager == "place":
        widget.place_forget()


def _label_for_variable(parent: tk.Misc, variable: tk.Variable) -> ttk.Label | None:
    variable_name = str(variable)
    for child in _walk(parent):
        if not isinstance(child, ttk.Label):
            continue
        try:
            if str(child.cget("textvariable")) == variable_name:
                return child
        except tk.TclError:
            continue
    return None


def apply_campaign_ui_polished(app: Any) -> None:
    """Apply the approved compact layout and authoritative runtime model."""

    beam_frame = _find_labelframe(app, "Beam Exposure")
    if beam_frame is not None:
        beam_frame.configure(text="Beam Parameters", padding=12)

        for text in (
            "Beam Status:",
            "Beam-on Time:",
            "Facility Fluence (optional):",
        ):
            _hide_widget(_find_label_by_text(beam_frame, text))

        if hasattr(app, "beam_status_var"):
            _hide_widget(_label_for_variable(beam_frame, app.beam_status_var))
        if hasattr(app, "beam_time_var"):
            _hide_widget(_label_for_variable(beam_frame, app.beam_time_var))
        if hasattr(app, "facility_fluence_var"):
            variable_name = str(app.facility_fluence_var)
            for child in _walk(beam_frame):
                try:
                    if str(child.cget("textvariable")) == variable_name:
                        _hide_widget(child)
                except (AttributeError, tk.TclError):
                    continue

        for child in _walk(beam_frame):
            if isinstance(child, ttk.Button) and child.cget("text") in {"Beam ON", "Beam OFF"}:
                _hide_widget(child)

        fluence_label = _find_label_by_text(beam_frame, "Estimated Fluence:")
        fluence_value = (
            _label_for_variable(beam_frame, app.calculated_fluence_var)
            if hasattr(app, "calculated_fluence_var")
            else None
        )
        if fluence_label is not None:
            fluence_label.configure(text="Fluence:")
            fluence_label.grid_configure(
                row=1,
                column=0,
                sticky="w",
                padx=(0, 8),
                pady=(8, 4),
            )
        if fluence_value is not None:
            fluence_value.grid_configure(
                row=1,
                column=1,
                sticky="w",
                pady=(8, 4),
            )

        ttk.Label(
            beam_frame,
            text="Calculated as: beam flux × active test runtime",
            font=("Segoe UI", 9),
        ).grid(row=2, column=0, columnspan=4, sticky="w", pady=(2, 0))

    for child in app.winfo_children():
        if isinstance(child, ttk.LabelFrame):
            try:
                child.configure(padding=12)
            except tk.TclError:
                pass

    for child in app.winfo_children():
        if isinstance(child, ttk.Label) and child.cget("text") == "Melagen Lab Test Coordinator":
            child.configure(font=("Segoe UI", 20, "bold"))
            child.grid_configure(pady=(0, 16))

    app.master.geometry("1080x860")
    app.master.minsize(940, 720)

    app._runtime_fluence_started_at: float | None = None
    app._runtime_fluence_after_id: str | None = None
    app.campaign_runtime_seconds = 0.0

    original_set_state = app._set_coordinator_state
    original_start = app._on_start_test
    original_stop = app._on_stop_test

    def cancel_tick() -> None:
        if app._runtime_fluence_after_id is not None:
            try:
                app.master.after_cancel(app._runtime_fluence_after_id)
            except tk.TclError:
                pass
            app._runtime_fluence_after_id = None

    def refresh_runtime_fluence() -> None:
        if app._runtime_fluence_started_at is None:
            return
        elapsed = max(0.0, time.monotonic() - app._runtime_fluence_started_at)
        app.campaign_runtime_seconds = elapsed
        # Older campaign layers consume this attribute when writing the single
        # CAMPAIGN_FLUENCE_FINAL event and CSV metadata.
        app.campaign_beam_seconds = elapsed
        app.campaign_calculated_fluence = calculate_fluence(
            app.campaign_selected_flux,
            elapsed,
        )
        app.calculated_fluence_var.set(
            f"{format_scientific(app.campaign_calculated_fluence)} p/cm²"
        )

    def tick() -> None:
        refresh_runtime_fluence()
        if app._runtime_fluence_started_at is not None:
            app._runtime_fluence_after_id = app.master.after(REFRESH_MS, tick)

    def authoritative_set_state(self: Any, new_state: Any) -> None:
        original_set_state(new_state)
        state_value = getattr(new_state, "value", str(new_state)).lower()
        if state_value == "active" and self._runtime_fluence_started_at is None:
            self.campaign_runtime_seconds = 0.0
            self.campaign_beam_seconds = 0.0
            self.campaign_calculated_fluence = 0.0
            self.calculated_fluence_var.set("0.000e+00 p/cm²")
            self._runtime_fluence_started_at = time.monotonic()
            cancel_tick()
            tick()
            self._append_log(
                "Fluence calculation started when the coordinator entered ACTIVE."
            )

    def polished_start(self: Any) -> None:
        # ACTIVE timing is captured by authoritative_set_state at the exact state
        # transition, before later metadata adapters return to this wrapper.
        original_start()

    def polished_stop(self: Any, automatic: bool = False) -> None:
        refresh_runtime_fluence()
        self._runtime_fluence_started_at = None
        cancel_tick()
        # The existing schema-cleanup layer writes the one authoritative
        # CAMPAIGN_FLUENCE_FINAL event using campaign_beam_seconds and
        # campaign_calculated_fluence. Do not write a duplicate event here.
        original_stop(automatic=automatic)

    app._set_coordinator_state = MethodType(authoritative_set_state, app)
    app._on_start_test = MethodType(polished_start, app)
    app._on_stop_test = MethodType(polished_stop, app)
    app.start_button.configure(command=app._on_start_test)
    app.stop_button.configure(command=app._on_stop_test)

    app._append_log(
        "Polished campaign layout loaded: compact beam parameters, ACTIVE-state "
        "fluence timing, and streamlined operator controls."
    )
