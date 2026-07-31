"""Tkinter interface for the Jetson Proton Test Coordinator."""

from __future__ import annotations

import json
import tkinter as tk
from datetime import datetime
from enum import Enum
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Any

from coordinator.constants import (
    BEAM_ENERGIES_MEV,
    SHIELDING_MATERIALS,
    SHIELDING_THICKNESSES_MM,
)
from coordinator.event_logger import EventLogger
from coordinator.request import (
    StopTestRequest,
    TestRequest,
)
from coordinator.transport import (
    MockTransport,
    Transport,
)


class CoordinatorState(Enum):
    """Possible Test Coordinator interface states."""

    IDLE = "idle"
    STARTING = "starting"
    ACTIVE = "active"
    STOPPING = "stopping"


class TestCoordinatorApp(ttk.Frame):
    """Operator interface for sending test-control commands."""

    def __init__(
        self,
        master: tk.Tk,
        transport: Transport | None = None,
        event_logger: EventLogger | None = None,
    ) -> None:
        super().__init__(master, padding=20)

        self.master = master
        self.transport = transport or MockTransport()

        self.transport_mode = getattr(
            self.transport,
            "mode_name",
            "unknown",
        )

        project_root = (
            Path(__file__)
            .resolve()
            .parent
            .parent
        )

        self.event_logger = (
            event_logger
            or EventLogger(
                project_root
                / "logs"
                / "coordinator_events.jsonl"
            )
        )

        self.coordinator_state = CoordinatorState.IDLE
        self.active_test_request_id: str | None = None

        self.energy_var = tk.StringVar(value="100")
        self.material_var = tk.StringVar(value="MLC1")
        self.thickness_var = tk.StringVar(value="12")
        self.summary_var = tk.StringVar()

        self.status_var = tk.StringVar(
            value=(
                f"Ready - "
                f"{self.transport_mode} mode"
            )
        )

        self.start_button: ttk.Button
        self.stop_button: ttk.Button
        self.activity_log: tk.Text

        self._configure_window()
        self._build_widgets()
        self._bind_events()
        self._update_summary()
        self._apply_control_state()

        self._append_log(
            "Application started in "
            f"{self.transport_mode.upper()} mode."
        )
        self._append_log("STATE -> IDLE")

        self._record_event(
            "APPLICATION_STARTED",
            transport=self.transport_mode,
            coordinator_state=(
                self.coordinator_state.value
            ),
        )

    def _configure_window(self) -> None:
        """Configure the main application window."""

        self.master.title(
            "Jetson Proton Test Coordinator"
        )
        self.master.geometry("720x660")
        self.master.minsize(650, 600)

        self.master.columnconfigure(0, weight=1)
        self.master.rowconfigure(0, weight=1)

        self.grid(
            row=0,
            column=0,
            sticky="nsew",
        )

        self.columnconfigure(1, weight=1)
        self.rowconfigure(8, weight=1)

    def _build_widgets(self) -> None:
        """Create and position interface controls."""

        title = ttk.Label(
            self,
            text="Jetson Proton Test Coordinator",
            font=("Segoe UI", 18, "bold"),
        )
        title.grid(
            row=0,
            column=0,
            columnspan=2,
            sticky="w",
            pady=(0, 20),
        )

        ttk.Label(
            self,
            text="Beam Energy:",
        ).grid(
            row=1,
            column=0,
            sticky="w",
            padx=(0, 15),
            pady=6,
        )

        energy_box = ttk.Combobox(
            self,
            textvariable=self.energy_var,
            values=[
                str(value)
                for value in BEAM_ENERGIES_MEV
            ],
            state="readonly",
            width=25,
        )
        energy_box.grid(
            row=1,
            column=1,
            sticky="ew",
            pady=6,
        )

        ttk.Label(
            self,
            text="Shielding Material:",
        ).grid(
            row=2,
            column=0,
            sticky="w",
            padx=(0, 15),
            pady=6,
        )

        material_box = ttk.Combobox(
            self,
            textvariable=self.material_var,
            values=SHIELDING_MATERIALS,
            state="readonly",
            width=25,
        )
        material_box.grid(
            row=2,
            column=1,
            sticky="ew",
            pady=6,
        )

        ttk.Label(
            self,
            text="Shielding Thickness:",
        ).grid(
            row=3,
            column=0,
            sticky="w",
            padx=(0, 15),
            pady=6,
        )

        thickness_box = ttk.Combobox(
            self,
            textvariable=self.thickness_var,
            values=[
                str(value)
                for value
                in SHIELDING_THICKNESSES_MM
            ],
            state="readonly",
            width=25,
        )
        thickness_box.grid(
            row=3,
            column=1,
            sticky="ew",
            pady=6,
        )

        summary_frame = ttk.LabelFrame(
            self,
            text="Selected Configuration",
            padding=12,
        )
        summary_frame.grid(
            row=4,
            column=0,
            columnspan=2,
            sticky="ew",
            pady=(18, 12),
        )
        summary_frame.columnconfigure(
            0,
            weight=1,
        )

        ttk.Label(
            summary_frame,
            textvariable=self.summary_var,
            font=("Segoe UI", 11, "bold"),
        ).grid(
            row=0,
            column=0,
            sticky="w",
        )

        button_frame = ttk.Frame(self)
        button_frame.grid(
            row=5,
            column=0,
            columnspan=2,
            pady=12,
        )

        self.start_button = ttk.Button(
            button_frame,
            text="Start Test",
            command=self._on_start_test,
            width=18,
        )
        self.start_button.grid(
            row=0,
            column=0,
            padx=(0, 8),
        )

        self.stop_button = ttk.Button(
            button_frame,
            text="Stop Test",
            command=self._on_stop_test,
            width=18,
        )
        self.stop_button.grid(
            row=0,
            column=1,
            padx=(8, 0),
        )

        status_frame = ttk.Frame(self)
        status_frame.grid(
            row=6,
            column=0,
            columnspan=2,
            sticky="ew",
            pady=(5, 15),
        )
        status_frame.columnconfigure(
            1,
            weight=1,
        )

        ttk.Label(
            status_frame,
            text="Status:",
            font=("Segoe UI", 10, "bold"),
        ).grid(
            row=0,
            column=0,
            sticky="w",
            padx=(0, 8),
        )

        ttk.Label(
            status_frame,
            textvariable=self.status_var,
        ).grid(
            row=0,
            column=1,
            sticky="w",
        )

        ttk.Label(
            self,
            text="Activity Log",
            font=("Segoe UI", 10, "bold"),
        ).grid(
            row=7,
            column=0,
            columnspan=2,
            sticky="w",
            pady=(0, 5),
        )

        log_frame = ttk.Frame(self)
        log_frame.grid(
            row=8,
            column=0,
            columnspan=2,
            sticky="nsew",
        )
        log_frame.columnconfigure(
            0,
            weight=1,
        )
        log_frame.rowconfigure(
            0,
            weight=1,
        )

        self.activity_log = tk.Text(
            log_frame,
            height=15,
            wrap="word",
            state="disabled",
            font=("Consolas", 9),
        )
        self.activity_log.grid(
            row=0,
            column=0,
            sticky="nsew",
        )

        scrollbar = ttk.Scrollbar(
            log_frame,
            orient="vertical",
            command=self.activity_log.yview,
        )
        scrollbar.grid(
            row=0,
            column=1,
            sticky="ns",
        )

        self.activity_log.configure(
            yscrollcommand=scrollbar.set
        )

        self._selection_widgets = (
            energy_box,
            material_box,
            thickness_box,
        )

    def _bind_events(self) -> None:
        """Update the summary when selections change."""

        for widget in self._selection_widgets:
            widget.bind(
                "<<ComboboxSelected>>",
                lambda _event: self._update_summary(),
            )

    def _update_summary(self) -> None:
        """Display the current operator selections."""

        self.summary_var.set(
            f"{self.energy_var.get()} MeV | "
            f"{self.material_var.get()} | "
            f"{self.thickness_var.get()} mm"
        )

    def _set_coordinator_state(
        self,
        new_state: CoordinatorState,
    ) -> None:
        """Change state and update all related controls."""

        self.coordinator_state = new_state
        self._apply_control_state()

        self._append_log(
            f"STATE -> {new_state.name}"
        )

    def _apply_control_state(self) -> None:
        """Enable controls allowed in the current state."""

        if self.coordinator_state == CoordinatorState.IDLE:
            self.start_button.configure(
                state="normal"
            )
            self.stop_button.configure(
                state="disabled"
            )

            for widget in self._selection_widgets:
                widget.configure(
                    state="readonly"
                )

        elif (
            self.coordinator_state
            == CoordinatorState.ACTIVE
        ):
            self.start_button.configure(
                state="disabled"
            )
            self.stop_button.configure(
                state="normal"
            )

            for widget in self._selection_widgets:
                widget.configure(
                    state="disabled"
                )

        else:
            self.start_button.configure(
                state="disabled"
            )
            self.stop_button.configure(
                state="disabled"
            )

            for widget in self._selection_widgets:
                widget.configure(
                    state="disabled"
                )

    def _validate_response(
        self,
        request: TestRequest | StopTestRequest,
        response: dict[str, Any],
    ) -> None:
        """Validate a receiver acknowledgment."""

        if (
            response.get("request_id")
            != request.request_id
        ):
            raise RuntimeError(
                "Response request_id does not match "
                "the submitted command"
            )

        if response.get("status") != "ACCEPTED":
            error_message = response.get(
                "error",
                "Receiver rejected the command",
            )

            raise RuntimeError(
                str(error_message)
            )

    def _log_exchange(
        self,
        request: TestRequest | StopTestRequest,
        response: dict[str, Any],
    ) -> None:
        """Display one command and response exchange."""

        self._append_log("REQUEST")
        self._append_log(
            json.dumps(
                request.to_dict(),
                indent=2,
            )
        )

        self._append_log("RESPONSE")
        self._append_log(
            json.dumps(
                response,
                indent=2,
            )
        )

    def _record_event(
        self,
        event: str,
        **fields: Any,
    ) -> bool:
        """Save one persistent event without crashing the GUI."""

        try:
            self.event_logger.append(
                event,
                **fields,
            )
            return True

        except (
            OSError,
            TypeError,
            ValueError,
        ) as error:
            self._append_log(
                "Persistent logging error: "
                f"{error}"
            )
            return False

    def _on_start_test(self) -> None:
        """Validate, confirm, log and send START_TEST."""

        if (
            self.coordinator_state
            != CoordinatorState.IDLE
        ):
            self._append_log(
                "START_TEST ignored because "
                "the coordinator is not idle."
            )
            return

        try:
            beam_energy = int(
                self.energy_var.get()
            )
            material = self.material_var.get()
            thickness = int(
                self.thickness_var.get()
            )

            request = TestRequest.create(
                beam_energy_mev=beam_energy,
                shielding_material=material,
                shielding_thickness_mm=thickness,
            )

        except (TypeError, ValueError) as error:
            self.status_var.set(
                "Invalid configuration"
            )
            self._append_log(
                f"Validation error: {error}"
            )

            self._record_event(
                "START_TEST_VALIDATION_FAILED",
                transport=self.transport_mode,
                error=str(error),
            )

            messagebox.showerror(
                "Invalid Configuration",
                str(error),
                parent=self.master,
            )
            return

        confirmation_message = (
            "Confirm START_TEST\n\n"
            f"Beam energy: "
            f"{request.beam_energy_mev} MeV\n"
            f"Shielding material: "
            f"{request.shielding_material}\n"
            f"Shielding thickness: "
            f"{request.shielding_thickness_mm} mm\n\n"
            f"Transport: "
            f"{self.transport_mode.upper()}\n\n"
            "Send this command?"
        )

        confirmed = messagebox.askyesno(
            "Confirm Start Test",
            confirmation_message,
            parent=self.master,
        )

        if not confirmed:
            self.status_var.set(
                "START_TEST cancelled"
            )
            self._append_log(
                "START_TEST cancelled before "
                f"submission: {request.request_id}"
            )

            self._record_event(
                "START_TEST_CANCELLED",
                transport=self.transport_mode,
                request=request.to_dict(),
            )
            return

        self._set_coordinator_state(
            CoordinatorState.STARTING
        )
        self.status_var.set(
            "Submitting START_TEST via "
            f"{self.transport_mode}..."
        )
        self.master.update_idletasks()

        try:
            event_saved = self._record_event(
                "START_TEST_SENT",
                transport=self.transport_mode,
                request=request.to_dict(),
            )

            if not event_saved:
                self.active_test_request_id = None

                self._set_coordinator_state(
                    CoordinatorState.IDLE
                )

                self.status_var.set(
                    "Logging failed - "
                    "START_TEST not sent"
                )

                messagebox.showerror(
                    "Persistent Logging Failed",
                    "START_TEST was not sent because "
                    "its audit record could not be saved.",
                    parent=self.master,
                )
                return

            response = self.transport.send(
                request
            )

            self._validate_response(
                request,
                response,
            )
            self._log_exchange(
                request,
                response,
            )

            self.active_test_request_id = (
                request.request_id
            )

            self._set_coordinator_state(
                CoordinatorState.ACTIVE
            )

            self._record_event(
                "START_TEST_ACCEPTED",
                transport=self.transport_mode,
                request=request.to_dict(),
                response=response,
            )

            self.status_var.set(
                "Test active - "
                f"{request.request_id[:8]}"
            )

            messagebox.showinfo(
                "Start Accepted",
                "START_TEST was accepted.\n\n"
                f"Request ID: "
                f"{request.request_id}",
                parent=self.master,
            )

        except Exception as error:
            self.active_test_request_id = None

            self._set_coordinator_state(
                CoordinatorState.IDLE
            )

            self.status_var.set(
                "START_TEST failed"
            )
            self._append_log(
                f"START_TEST error: {error}"
            )

            self._record_event(
                "START_TEST_FAILED",
                transport=self.transport_mode,
                request_id=request.request_id,
                error=str(error),
            )

            messagebox.showerror(
                "Start Failed",
                str(error),
                parent=self.master,
            )

    def _on_stop_test(self) -> None:
        """Confirm, log and send STOP_TEST."""

        if (
            self.coordinator_state
            != CoordinatorState.ACTIVE
        ):
            self._append_log(
                "STOP_TEST ignored because "
                "there is no active test."
            )
            return

        if self.active_test_request_id is None:
            self._append_log(
                "STOP_TEST blocked because "
                "the active request ID is missing."
            )
            self.status_var.set(
                "Internal state error"
            )

            self._record_event(
                "STOP_TEST_STATE_ERROR",
                transport=self.transport_mode,
                error=(
                    "active_test_request_id is missing"
                ),
            )
            return

        target_request_id = (
            self.active_test_request_id
        )

        confirmation_message = (
            "Confirm STOP_TEST\n\n"
            "Active test request ID:\n"
            f"{target_request_id}\n\n"
            f"Transport: "
            f"{self.transport_mode.upper()}\n\n"
            "Send the stop command?"
        )

        confirmed = messagebox.askyesno(
            "Confirm Stop Test",
            confirmation_message,
            parent=self.master,
        )

        if not confirmed:
            self.status_var.set(
                "STOP_TEST cancelled - "
                "test remains active"
            )
            self._append_log(
                "STOP_TEST cancelled for "
                f"{target_request_id}"
            )

            self._record_event(
                "STOP_TEST_CANCELLED",
                transport=self.transport_mode,
                target_request_id=target_request_id,
            )
            return

        try:
            request = StopTestRequest.create(
                target_request_id=(
                    target_request_id
                )
            )

        except (TypeError, ValueError) as error:
            self.status_var.set(
                "Could not create STOP_TEST"
            )
            self._append_log(
                "STOP_TEST validation error: "
                f"{error}"
            )

            self._record_event(
                "STOP_TEST_VALIDATION_FAILED",
                transport=self.transport_mode,
                target_request_id=target_request_id,
                error=str(error),
            )

            messagebox.showerror(
                "Stop Request Error",
                str(error),
                parent=self.master,
            )
            return

        self._set_coordinator_state(
            CoordinatorState.STOPPING
        )
        self.status_var.set(
            "Submitting STOP_TEST via "
            f"{self.transport_mode}..."
        )
        self.master.update_idletasks()

        try:
            event_saved = self._record_event(
                "STOP_TEST_SENT",
                transport=self.transport_mode,
                request=request.to_dict(),
            )

            if not event_saved:
                self._set_coordinator_state(
                    CoordinatorState.ACTIVE
                )

                self.status_var.set(
                    "Logging failed - "
                    "STOP_TEST not sent"
                )

                messagebox.showerror(
                    "Persistent Logging Failed",
                    "STOP_TEST was not sent because "
                    "its audit record could not be saved.",
                    parent=self.master,
                )
                return

            response = self.transport.send(
                request
            )

            self._validate_response(
                request,
                response,
            )
            self._log_exchange(
                request,
                response,
            )

            stopped_target_id = (
                request.target_request_id
            )

            self.active_test_request_id = None

            self._set_coordinator_state(
                CoordinatorState.IDLE
            )

            self._record_event(
                "STOP_TEST_ACCEPTED",
                transport=self.transport_mode,
                request=request.to_dict(),
                response=response,
            )

            self.status_var.set(
                "Test stopped - "
                f"{stopped_target_id[:8]}"
            )

            messagebox.showinfo(
                "Stop Accepted",
                "STOP_TEST was accepted.\n\n"
                "Stopped test ID: "
                f"{stopped_target_id}\n\n"
                "Stop command ID: "
                f"{request.request_id}",
                parent=self.master,
            )

        except Exception as error:
            # The original test remains active when
            # STOP_TEST fails or is rejected.
            self._set_coordinator_state(
                CoordinatorState.ACTIVE
            )

            self.status_var.set(
                "STOP_TEST failed - "
                "test remains active"
            )
            self._append_log(
                f"STOP_TEST error: {error}"
            )

            self._record_event(
                "STOP_TEST_FAILED",
                transport=self.transport_mode,
                request_id=request.request_id,
                target_request_id=(
                    request.target_request_id
                ),
                error=str(error),
            )

            messagebox.showerror(
                "Stop Failed",
                str(error),
                parent=self.master,
            )

    def _append_log(
        self,
        message: str,
    ) -> None:
        """Append a timestamped message to the GUI activity log."""

        timestamp = datetime.now().strftime(
            "%H:%M:%S"
        )

        self.activity_log.configure(
            state="normal"
        )
        self.activity_log.insert(
            "end",
            f"[{timestamp}] {message}\n",
        )
        self.activity_log.see("end")
        self.activity_log.configure(
            state="disabled"
        )


def run() -> None:
    """Create and start the mock-mode application."""

    root = tk.Tk()
    TestCoordinatorApp(root)
    root.mainloop()