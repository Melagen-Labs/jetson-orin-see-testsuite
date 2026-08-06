"""Tkinter interface for the Melagen Labs Proton Test Coordinator."""

from __future__ import annotations

import csv
import json
import os
import subprocess
import threading
import time
import tkinter as tk
from datetime import datetime
from enum import Enum
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Any

from coordinator.constants import (
    BEAM_ENERGIES_MEV,
    DEFAULT_BASELINE_MINUTES,
    DEFAULT_DURATION_S,
    MAX_DURATION_S,
    SHIELDING_MATERIALS,
    SHIELDING_THICKNESSES_MM,
)
from coordinator.event_logger import EventLogger
from coordinator.request import (
    BaselineTestRequest,
    StopTestRequest,
    TestRequest,
)
from coordinator.see_monitor import SeeLogTailer
from coordinator.transport import (
    MockTransport,
    Transport,
)


# Post-test results: the DUT (Jetson) returns a "summary" block in the STOP ack with
# SEE counts by type. These map the DUT's stable type keys to operator-facing labels.
RESULTS_DIR_NAME = "results"

SEE_TYPE_LABELS = {
    "cuda_golden_mismatch": "CUDA sim: golden-hash mismatch",
    "cuda_nonfinite": "CUDA sim: NaN/Inf output",
    "cuda_anomaly": "CUDA sim: value out of bounds",
    "cuda_shutdown": "CUDA sim: shut down / restarted",
    "gpu_mem_upset": "GPU RAM tester: bit upset",
    "mem_tester_restart": "GPU RAM tester: shut down / restarted",
    "fatal_error": "Fatal error record",
    # Live-panel-only marker (never appears in the DUT summary/CSV): whether the
    # SEE's epoch state dump (post-processing data for see_dump_triage.py) was saved.
    "see_dump_saved": "Post-processing dump",
}

# Live SEE panel (§6b): tail the pulled arbiter_logs/ tree every SEE_POLL_MS and
# append new SEE events. Near-real-time -- latency is the poll interval; this is the
# accepted tradeoff of log-tailing over a per-event push (see see_monitor.py).
SEE_POLL_MS = 2500
SEE_POLL_SECONDS = SEE_POLL_MS / 1000
DEFAULT_SEE_LOG_ROOT = "arbiter_logs"

# Dropdown entry that swaps the beam energy over to the free-text field beside it.
# Not a number, so it can never be confused with a preset.
CUSTOM_ENERGY_LABEL = "Custom..."

# Baseline runs: the DUT writes the current CSV into /var/log/radtest/power and the
# arbiter's pull loop mirrors it here, so the GUI reads it off the local disk rather
# than needing SSH itself. The pull is periodic, so the file can lag the STOP reply
# by a few seconds -- we poll for it instead of declaring it missing on first look.
BASELINE_LOG_SUBDIR = "power"
BASELINE_CSV_WAIT_S = 90
BASELINE_CSV_POLL_S = 3


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
        see_log_root: str | Path | None = None,
        pull_script: str | Path | None = None,
        pull_timeout_s: float = 900.0,
    ) -> None:
        super().__init__(master, padding=20)

        self.master = master
        self.transport = transport or MockTransport()

        # §6b live SEE panel: tail the local arbiter_logs/ mirror (populated by the
        # arbiter's radpull rsync). No network access here -- just file tailing.
        self._see_log_root = Path(
            see_log_root
            if see_log_root is not None
            else DEFAULT_SEE_LOG_ROOT
        )
        self._see_tailer = SeeLogTailer(self._see_log_root)
        self._see_after_id: str | None = None

        # End-of-run full log pull. The periodic arbiter pull runs PULL_MODE=live
        # (JSONL only) to keep the DUT light during a test; this fires PULL_MODE=full
        # once the test stops, fetching the ~10 MB per-SEE state dumps + golden table
        # that see_dump_triage.py needs. Requires the GUI to run on the arbiter box
        # (bash + rsync + the pull key). Opt-in: no --pull-script, no pull attempted.
        self._pull_script = str(pull_script) if pull_script else None
        self._pull_timeout_s = pull_timeout_s

        self.transport_mode = getattr(
            self.transport,
            "mode_name",
            "unknown",
        )

        # For a real network transport, surface WHERE it points so the operator
        # can confirm the GUI is aimed at the intended DUT (not mock, not the
        # wrong board). MockTransport has no host/port, so this stays blank.
        target_host = getattr(self.transport, "host", None)
        target_port = getattr(self.transport, "port", None)
        self.transport_target = (
            f" -> {target_host}:{target_port}"
            if target_host and target_port
            else ""
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

        self.energy_var = tk.StringVar(value="200")
        self.custom_energy_var = tk.StringVar(value="")
        self.material_var = tk.StringVar(value="MLC1")
        self.thickness_var = tk.StringVar(value="12")
        self.duration_var = tk.StringVar(value=str(DEFAULT_DURATION_S))
        self.baseline_minutes_var = tk.StringVar(
            value=str(DEFAULT_BASELINE_MINUTES)
        )
        self.summary_var = tk.StringVar()

        # True while the ACTIVE run was started by "Baseline Test" rather than
        # "Start Test", so the STOP path knows to also collect the current CSV.
        self.active_run_is_baseline = False

        # Pending "after" id for the coordinator-side auto-STOP timer. The DUT owns
        # the authoritative run timer; this mirror fires at the same duration so the
        # GUI retrieves the summary/CSV and returns to IDLE without an operator click.
        self._auto_stop_after_id: str | None = None

        self.status_var = tk.StringVar(
            value=(
                f"Ready - "
                f"{self.transport_mode} mode"
                f"{self.transport_target}"
            )
        )

        self.start_button: ttk.Button
        self.stop_button: ttk.Button
        self.baseline_button: ttk.Button
        self.activity_log: tk.Text

        self._configure_window()
        self._build_widgets()
        self._bind_events()
        self._update_summary()
        self._apply_control_state()

        self._append_log(
            "Application started in "
            f"{self.transport_mode.upper()} mode"
            f"{self.transport_target}."
        )
        self._append_log("STATE -> IDLE")

        self._record_event(
            "APPLICATION_STARTED",
            transport=self.transport_mode,
            coordinator_state=(
                self.coordinator_state.value
            ),
        )

        self._append_see(
            "Watching "
            f"{self._see_log_root} for SEE events "
            f"(every {SEE_POLL_SECONDS:g}s)..."
        )
        self._see_after_id = self.master.after(
            SEE_POLL_MS,
            self._poll_sees,
        )

    def _configure_window(self) -> None:
        """Configure the main application window."""

        self.master.title(
            "Melagen Labs Proton Test Coordinator"
        )
        # Taller than before: the baseline controls add a row between the
        # start/stop buttons and the status line.
        self.master.geometry("720x940")
        self.master.minsize(650, 860)

        self.master.columnconfigure(0, weight=1)
        self.master.rowconfigure(0, weight=1)

        self.grid(
            row=0,
            column=0,
            sticky="nsew",
        )

        self.columnconfigure(1, weight=1)
        # Activity log (row 9) and the live SEE panel (row 11) both expand.
        self.rowconfigure(9, weight=1)
        self.rowconfigure(11, weight=1)

    def _build_widgets(self) -> None:
        """Create and position interface controls."""

        title = ttk.Label(
            self,
            text="Melagen Labs Proton Test Coordinator",
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

        energy_frame = ttk.Frame(self)
        energy_frame.grid(
            row=1,
            column=1,
            sticky="ew",
            pady=6,
        )
        energy_frame.columnconfigure(0, weight=1)

        energy_box = ttk.Combobox(
            energy_frame,
            textvariable=self.energy_var,
            values=[
                str(value)
                for value in BEAM_ENERGIES_MEV
            ]
            + [CUSTOM_ENERGY_LABEL],
            state="readonly",
            width=16,
        )
        energy_box.grid(
            row=0,
            column=0,
            sticky="ew",
        )

        # Only meaningful when the dropdown is on "Custom"; kept disabled
        # otherwise so the value in force is never ambiguous.
        self.custom_energy_entry = ttk.Entry(
            energy_frame,
            textvariable=self.custom_energy_var,
            width=8,
            state="disabled",
        )
        self.custom_energy_entry.grid(
            row=0,
            column=1,
            sticky="w",
            padx=(8, 0),
        )

        ttk.Label(
            energy_frame,
            text="MeV",
        ).grid(
            row=0,
            column=2,
            sticky="w",
            padx=(4, 0),
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

        ttk.Label(
            self,
            text="Test Duration (s):",
        ).grid(
            row=4,
            column=0,
            sticky="w",
            padx=(0, 15),
            pady=6,
        )

        self.duration_entry = ttk.Entry(
            self,
            textvariable=self.duration_var,
            width=27,
        )
        self.duration_entry.grid(
            row=4,
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
            row=5,
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
            row=6,
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

        # Baseline run: same workloads as a beam test, beam off, plus a current
        # capture. Its own length field, in minutes -- baselines run for an hour,
        # not the ~100 s of a beam shot, and Stop Test ends either kind of run.
        baseline_frame = ttk.LabelFrame(
            button_frame,
            text="Baseline (no beam) - runs the full test stack + logs current",
            padding=10,
        )
        baseline_frame.grid(
            row=1,
            column=0,
            columnspan=2,
            sticky="ew",
            pady=(14, 0),
        )
        baseline_frame.columnconfigure(1, weight=1)

        ttk.Label(
            baseline_frame,
            text="Duration (minutes):",
        ).grid(
            row=0,
            column=0,
            sticky="w",
            padx=(0, 10),
        )

        self.baseline_entry = ttk.Entry(
            baseline_frame,
            textvariable=self.baseline_minutes_var,
            width=10,
        )
        self.baseline_entry.grid(
            row=0,
            column=1,
            sticky="w",
        )

        self.baseline_button = ttk.Button(
            baseline_frame,
            text="Baseline Test",
            command=self._on_baseline_test,
            width=18,
        )
        self.baseline_button.grid(
            row=0,
            column=2,
            padx=(12, 0),
        )

        status_frame = ttk.Frame(self)
        status_frame.grid(
            row=7,
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
            row=8,
            column=0,
            columnspan=2,
            sticky="w",
            pady=(0, 5),
        )

        log_frame = ttk.Frame(self)
        log_frame.grid(
            row=9,
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

        ttk.Label(
            self,
            text=(
                "Live SEEs  (near-real-time, polling every "
                f"{SEE_POLL_SECONDS:g}s from {self._see_log_root})"
            ),
            font=("Segoe UI", 10, "bold"),
        ).grid(
            row=10,
            column=0,
            columnspan=2,
            sticky="w",
            pady=(10, 5),
        )

        see_frame = ttk.Frame(self)
        see_frame.grid(
            row=11,
            column=0,
            columnspan=2,
            sticky="nsew",
        )
        see_frame.columnconfigure(0, weight=1)
        see_frame.rowconfigure(0, weight=1)

        self.see_log = tk.Text(
            see_frame,
            height=8,
            wrap="none",
            state="disabled",
            font=("Consolas", 9),
        )
        self.see_log.grid(
            row=0,
            column=0,
            sticky="nsew",
        )

        see_scrollbar = ttk.Scrollbar(
            see_frame,
            orient="vertical",
            command=self.see_log.yview,
        )
        see_scrollbar.grid(
            row=0,
            column=1,
            sticky="ns",
        )

        self.see_log.configure(
            yscrollcommand=see_scrollbar.set
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
        # Typing in the custom-energy box updates the summary line too, so what
        # the operator confirms is what they typed.
        self.custom_energy_var.trace_add(
            "write",
            lambda *_: self._update_summary(),
        )
        # Enable/disable the custom field from a trace on the variable itself,
        # not from _update_summary: the campaign UI layers replace that method
        # wholesale, which would leave the field permanently disabled. A variable
        # trace fires no matter who changes the selection.
        self.energy_var.trace_add(
            "write",
            lambda *_: self._sync_custom_energy_state(),
        )

    def _energy_is_custom(self) -> bool:
        """True when the operator picked the free-text energy."""

        return self.energy_var.get() == CUSTOM_ENERGY_LABEL

    def _resolve_beam_energy(self) -> tuple[float | int, str]:
        """Return (energy_mev, mode) from the dropdown + custom field.

        Raises ValueError with an operator-readable message when the custom field
        is empty or not a number -- silently falling back to a preset would run
        the beam at an energy nobody asked for and label it as planned.
        """

        if not self._energy_is_custom():
            return int(self.energy_var.get()), "preset"

        raw = self.custom_energy_var.get().strip()
        if not raw:
            raise ValueError(
                "Enter a custom beam energy in MeV, "
                "or pick one of the preset values."
            )

        try:
            energy = float(raw)
        except ValueError:
            raise ValueError(
                f"Custom beam energy {raw!r} is not a number."
            ) from None

        if energy <= 0:
            raise ValueError(
                "Custom beam energy must be greater than 0 MeV."
            )

        # Keep whole numbers as ints so the DUT records "100MeV", not "100.0MeV".
        return (int(energy) if energy.is_integer() else energy), "custom"

    def _sync_custom_energy_state(self) -> None:
        """Enable the custom-energy field only while 'Custom...' is selected."""

        editable = (
            self._energy_is_custom()
            and self.coordinator_state == CoordinatorState.IDLE
        )
        self.custom_energy_entry.configure(
            state="normal" if editable else "disabled"
        )

    def _update_summary(self) -> None:
        """Display the current operator selections."""

        self._sync_custom_energy_state()

        if self._energy_is_custom():
            entered = self.custom_energy_var.get().strip()
            energy_text = f"{entered or '?'} MeV (custom)"
        else:
            energy_text = f"{self.energy_var.get()} MeV"

        self.summary_var.set(
            f"{energy_text} | "
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
        # The custom-energy field is only editable at IDLE, and only when the
        # dropdown is on Custom -- both conditions live in one place.
        self._sync_custom_energy_state()

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
            self.baseline_button.configure(
                state="normal"
            )

            for widget in self._selection_widgets:
                widget.configure(
                    state="readonly"
                )
            # A free-text entry, not a dropdown -> editable (not "readonly") at idle.
            self.duration_entry.configure(state="normal")
            self.baseline_entry.configure(state="normal")

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
            # One run at a time: a baseline and a beam test would fight over the
            # same ARMED flags and services on the DUT.
            self.baseline_button.configure(
                state="disabled"
            )

            for widget in self._selection_widgets:
                widget.configure(
                    state="disabled"
                )
            self.duration_entry.configure(state="disabled")
            self.baseline_entry.configure(state="disabled")

        else:
            self.start_button.configure(
                state="disabled"
            )
            self.stop_button.configure(
                state="disabled"
            )
            self.baseline_button.configure(
                state="disabled"
            )

            for widget in self._selection_widgets:
                widget.configure(
                    state="disabled"
                )
            self.duration_entry.configure(state="disabled")
            self.baseline_entry.configure(state="disabled")

    def _validate_response(
        self,
        request: TestRequest | BaselineTestRequest | StopTestRequest,
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
        request: TestRequest | BaselineTestRequest | StopTestRequest,
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

    def _next_result_path(self, prefix: str = "test") -> Path:
        """Return results/<prefix>_<N>.csv with the next unused N (test_1, test_2, ...).

        Beam runs use the "test" prefix and baselines use "baseline", so the two
        kinds of result never share a numbering sequence and can't be confused.
        """

        results_dir = (
            Path(__file__).resolve().parent.parent
            / RESULTS_DIR_NAME
        )
        results_dir.mkdir(parents=True, exist_ok=True)

        number = 1
        while (results_dir / f"{prefix}_{number}.csv").exists():
            number += 1

        return results_dir / f"{prefix}_{number}.csv"

    def _save_result_csv(
        self,
        summary: dict[str, Any],
    ) -> Path | None:
        """Write one CSV per test (test_1.csv, test_2.csv, ...). Never raises into
        the GUI; returns the path written, or None on failure."""

        try:
            path = self._next_result_path()
            by_type = summary.get("by_type", {}) or {}

            with open(
                path,
                "w",
                newline="",
                encoding="utf-8",
            ) as handle:
                writer = csv.writer(handle)
                writer.writerow(["field", "value"])
                writer.writerow(["jetson_id", summary.get("jetson_id", "")])
                writer.writerow(["run_id", summary.get("run_id", "")])
                writer.writerow(
                    ["beam_energy", summary.get("beam_energy", "")]
                )
                writer.writerow(
                    ["shield_config", summary.get("shield_config", "")]
                )
                writer.writerow(
                    ["duration_s", summary.get("duration_s", "")]
                )
                writer.writerow(
                    ["total_sees", summary.get("total_sees", "")]
                )
                writer.writerow(
                    ["sees_per_s", summary.get("sees_per_s", "")]
                )
                writer.writerow([])
                writer.writerow(["see_type", "label", "count"])
                for key in sorted(by_type):
                    writer.writerow(
                        [
                            key,
                            SEE_TYPE_LABELS.get(key, key),
                            by_type[key],
                        ]
                    )

            self._append_log(f"Results saved: {path}")
            return path

        except OSError as error:
            self._append_log(
                f"Could not save results CSV: {error}"
            )
            return None

    def _csv_data_rows(self, path: Path) -> int:
        """Count data rows (everything but the header) in a CSV. -1 if unreadable."""

        try:
            with open(path, "r", encoding="utf-8", newline="") as handle:
                return max(0, sum(1 for _ in handle) - 1)
        except OSError:
            return -1

    def _copy_baseline_csv(
        self,
        csv_name: str,
        expected_rows: int | None = None,
    ) -> Path | None:
        """Copy the pulled current CSV into results/baseline_<N>.csv.

        Returns the saved path, or None if the arbiter has not mirrored the whole
        file yet. Never raises into the GUI.

        `expected_rows` is the DUT's own sample count from the STOP reply. It
        matters: the arbiter mirrors the DUT every few seconds, so at the moment a
        run stops the local copy is typically a sample or two short. Copying it
        blindly produced a results CSV missing the end of the run (observed
        2026-08-06: 58 rows saved for a 60-sample capture). The DUT's count is the
        authority, so wait until the mirror has caught up to it.
        """

        source = (
            self._see_log_root
            / BASELINE_LOG_SUBDIR
            / csv_name
        )

        if not source.is_file():
            return None

        if expected_rows:
            rows = self._csv_data_rows(source)
            # >= not ==: rows include any SENSOR_READ_FAILED rows, which the DUT's
            # `samples` count excludes.
            if rows < expected_rows:
                return None

        try:
            destination = self._next_result_path(
                prefix="baseline"
            )
            destination.write_bytes(
                source.read_bytes()
            )
            self._append_log(
                f"Baseline CSV saved: {destination}"
            )
            return destination

        except OSError as error:
            self._append_log(
                f"Could not save baseline CSV: {error}"
            )
            return None

    def _fetch_baseline_csv(
        self,
        baseline: dict[str, Any],
    ) -> Path | None:
        """Try once to collect the baseline CSV; if it hasn't arrived, keep trying
        in the background.

        The arbiter's pull loop mirrors the DUT's /var/log/radtest/power every few
        seconds, so right after STOP the file is usually there but occasionally a
        beat behind. Rather than reporting a missing CSV that is about to appear,
        we poll off the Tk thread and report into the activity log when it lands.
        """

        csv_name = baseline.get("csv_name")
        if not isinstance(csv_name, str) or not csv_name:
            return None

        samples = baseline.get("samples")
        expected = samples if isinstance(samples, int) and samples > 0 else None

        saved = self._copy_baseline_csv(csv_name, expected)
        if saved is not None:
            return saved

        self._append_log(
            f"Baseline CSV {csv_name} not complete locally yet "
            f"(DUT recorded {expected if expected else '?'} samples); waiting up "
            f"to {BASELINE_CSV_WAIT_S}s for the arbiter pull to catch up..."
        )

        def worker() -> None:
            deadline = time.monotonic() + BASELINE_CSV_WAIT_S
            while time.monotonic() < deadline:
                time.sleep(BASELINE_CSV_POLL_S)
                source = (
                    self._see_log_root
                    / BASELINE_LOG_SUBDIR
                    / csv_name
                )
                if not source.is_file():
                    continue
                if expected and self._csv_data_rows(source) < expected:
                    continue                    # still mid-transfer; keep waiting
                try:
                    self.master.after(
                        0,
                        lambda: self._copy_baseline_csv(csv_name, expected),
                    )
                except (tk.TclError, RuntimeError):
                    pass
                return

            # Out of time. Save whatever did arrive rather than nothing, but say
            # plainly that it is short of what the DUT recorded.
            def give_up() -> None:
                partial = self._copy_baseline_csv(csv_name)
                if partial is None:
                    self._append_log(
                        f"Baseline CSV {csv_name} never arrived. It is still on "
                        f"the DUT at {baseline.get('csv', '/var/log/radtest/power/')}"
                        " - pull it by hand (scp) if the arbiter pull loop is not "
                        "running."
                    )
                else:
                    self._append_log(
                        f"WARNING: saved {partial.name} but it is INCOMPLETE "
                        f"(fewer than the {expected} samples the DUT recorded). "
                        f"The full file is on the DUT at "
                        f"{baseline.get('csv', '/var/log/radtest/power/')}"
                    )

            try:
                self.master.after(0, give_up)
            except (tk.TclError, RuntimeError):
                pass

        threading.Thread(
            target=worker,
            daemon=True,
            name="baseline-csv-wait",
        ).start()

        return None

    def _format_baseline(
        self,
        baseline: dict[str, Any] | None,
        csv_path: Path | None,
    ) -> list[str]:
        """Build the baseline section of the post-test popup."""

        if not isinstance(baseline, dict):
            return [
                "",
                "No current baseline was returned by the DUT.",
            ]

        if baseline.get("error"):
            return [
                "",
                f"Current baseline error: {baseline['error']}",
            ]

        lines = [
            "",
            "Current baseline (VDD_IN):",
            f"  Samples: {baseline.get('samples', 0)} "
            f"(sensor failures: {baseline.get('sensor_failures', 0)})",
        ]

        current = baseline.get("current_ma")
        if isinstance(current, dict):
            lines.append(
                "  Current mA min/mean/max: "
                f"{current.get('min')} / "
                f"{current.get('mean')} / "
                f"{current.get('max')}"
            )

        power = baseline.get("power_mw")
        if isinstance(power, dict):
            lines.append(
                f"  Mean power: {power.get('mean')} mW"
            )

        if baseline.get("stopped_early"):
            lines.append(
                "  NOTE: stopped before the requested duration."
            )

        lines.append(
            f"  CSV: {csv_path.name if csv_path else baseline.get('csv_name', '?')}"
            + ("" if csv_path else "  (not mirrored yet - see activity log)")
        )

        return lines

    def _format_results(
        self,
        stopped_target_id: str,
        summary: dict[str, Any] | None,
        csv_path: Path | None,
    ) -> str:
        """Build the post-test results popup text."""

        lines = [
            "STOP_TEST accepted.",
            f"Stopped test ID: {stopped_target_id}",
            "",
        ]

        if (
            not isinstance(summary, dict)
            or "by_type" not in summary
        ):
            lines.append(
                "No run summary was returned "
                f"(transport: {self.transport_mode})."
            )
            return "\n".join(lines)

        if summary.get("error"):
            lines.append(
                f"Summary error: {summary['error']}"
            )
            return "\n".join(lines)

        lines += [
            f"Duration: {summary.get('duration_s', 0)} s",
            f"SEEs detected: {summary.get('total_sees', 0)}",
            f"SEEs/sec: {summary.get('sees_per_s', 0)}",
            "",
            "By type:",
        ]

        by_type = summary.get("by_type", {}) or {}
        for key in sorted(by_type):
            lines.append(
                f"  {SEE_TYPE_LABELS.get(key, key)}: "
                f"{by_type[key]}"
            )

        if csv_path is not None:
            lines += ["", f"Saved: {csv_path.name}"]

        return "\n".join(lines)

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

    def _trigger_full_pull(self, run_id: str) -> None:
        """After a test stops, pull the heavy post-processing data (SEE state dumps,
        pstore, golden table) that the in-run live pulls deliberately skipped. Runs
        off the Tk thread so a multi-minute rsync never freezes the GUI; all results
        are reported back into the activity log."""

        if not self._pull_script:
            return

        script = self._pull_script
        timeout = self._pull_timeout_s
        local_root = str(self._see_log_root)
        host = getattr(self.transport, "host", None)

        self._append_log(
            f"Fetching full post-processing data for {run_id[:8]} "
            "(PULL_MODE=full)..."
        )

        def worker() -> None:
            env = dict(os.environ)
            env["PULL_MODE"] = "full"
            env["LOCAL_LOG_DIR"] = local_root
            if host:
                env["DUT_HOST"] = str(host)
            try:
                result = subprocess.run(
                    ["bash", script],
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )
                if result.returncode == 0:
                    message = (
                        "Full log pull complete "
                        f"-> {local_root} (dumps ready for see_dump_triage.py)"
                    )
                else:
                    detail = (result.stderr or result.stdout or "").strip()
                    last = detail.splitlines()[-1] if detail else "no output"
                    message = (
                        "Full log pull FAILED "
                        f"(rc={result.returncode}): {last}"
                    )
            except FileNotFoundError:
                message = (
                    "Full log pull skipped: 'bash' not found. Run the GUI on the "
                    "arbiter box, or pull by hand: PULL_MODE=full bash pull_logs.sh"
                )
            except subprocess.TimeoutExpired:
                message = (
                    f"Full log pull timed out after {timeout:g}s "
                    "(many/large dumps?) - pull by hand to finish"
                )
            except Exception as error:  # noqa: BLE001 - never kill the GUI
                message = f"Full log pull error: {error}"

            # Marshal back onto the Tk thread; the GUI may be gone by now.
            try:
                self.master.after(0, lambda: self._append_log(message))
            except (tk.TclError, RuntimeError):
                pass

        threading.Thread(
            target=worker,
            daemon=True,
            name="full-log-pull",
        ).start()

    def _schedule_auto_stop(self, duration_s: int) -> None:
        """Arm the coordinator-side auto-STOP timer for `duration_s` seconds."""

        self._cancel_auto_stop()
        delay_ms = max(0, int(duration_s * 1000))
        self._auto_stop_after_id = self.master.after(
            delay_ms,
            self._auto_stop_fire,
        )

    def _cancel_auto_stop(self) -> None:
        """Cancel a pending auto-STOP timer, if one is armed."""

        if self._auto_stop_after_id is not None:
            try:
                self.master.after_cancel(
                    self._auto_stop_after_id
                )
            except tk.TclError:
                pass
            self._auto_stop_after_id = None

    def _auto_stop_fire(self) -> None:
        """Duration elapsed: run the normal STOP path without an operator click."""

        self._auto_stop_after_id = None
        if (
            self.coordinator_state
            == CoordinatorState.ACTIVE
        ):
            self._on_stop_test(automatic=True)

    def _on_start_test(self) -> None:
        """Validate, confirm, log and send START_TEST."""

        if self.coordinator_state != CoordinatorState.IDLE:
            self._append_log(
                "START_TEST ignored because the coordinator is not idle."
            )
            return

        try:
            beam_energy, energy_mode = self._resolve_beam_energy()
            duration_s = int(self.duration_var.get().strip())

            request_kwargs = getattr(
                self,
                "_start_request_kwargs",
                None,
            )
            self._start_request_kwargs = None

            if request_kwargs is None:
                request = TestRequest.create(
                    beam_energy_mev=beam_energy,
                    shielding_material=self.material_var.get(),
                    shielding_thickness_mm=int(
                        self.thickness_var.get()
                    ),
                    duration_s=duration_s,
                    beam_energy_mode=energy_mode,
                )
            else:
                # The campaign UI layers pre-build the shielding kwargs; they do
                # not set the energy mode, so supply it unless they already did.
                request_kwargs.setdefault(
                    "beam_energy_mode",
                    energy_mode,
                )
                request = TestRequest.create(
                    beam_energy_mev=beam_energy,
                    duration_s=duration_s,
                    **request_kwargs,
                )

        except (TypeError, ValueError) as error:
            self.status_var.set("Invalid configuration")
            self._append_log(f"Validation error: {error}")
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

        confirmation_lines = [
            "Confirm START_TEST",
            "",
            f"Beam energy: {request.beam_energy_mev} MeV",
            f"Shielding mode: {request.shielding_mode}",
            f"Shielding material: {request.shielding_material}",
        ]
        if request.shielding_reference_mm is not None:
            confirmation_lines.append(
                "Reference level: "
                f"{request.shielding_reference_mm:g} mm"
            )
        confirmation_lines.extend(
            [
                "Physical thickness: "
                f"{request.shielding_actual_thickness_mm:g} mm",
                "Configuration ID: "
                f"{request.shielding_configuration_id or 'legacy'}",
                f"Test duration: {request.duration_s} s",
                "",
                f"Transport: {self.transport_mode.upper()}",
                "",
                "Send this command?",
            ]
        )

        confirmed = messagebox.askyesno(
            "Confirm Start Test",
            "\n".join(confirmation_lines),
            parent=self.master,
        )

        if not confirmed:
            self.status_var.set("START_TEST cancelled")
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

        self._set_coordinator_state(CoordinatorState.STARTING)
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
                self._set_coordinator_state(CoordinatorState.IDLE)
                self.status_var.set(
                    "Logging failed - START_TEST not sent"
                )
                messagebox.showerror(
                    "Persistent Logging Failed",
                    "START_TEST was not sent because "
                    "its audit record could not be saved.",
                    parent=self.master,
                )
                return

            response = self.transport.send(request)
            self._validate_response(request, response)
            self._log_exchange(request, response)
            self.active_test_request_id = request.request_id
            self._set_coordinator_state(CoordinatorState.ACTIVE)
            self._schedule_auto_stop(request.duration_s)
            self._clear_see_panel()

            self._record_event(
                "START_TEST_ACCEPTED",
                transport=self.transport_mode,
                request=request.to_dict(),
                response=response,
            )

            self.status_var.set(
                "Test active - "
                f"{request.request_id[:8]} "
                f"(auto-stop in {request.duration_s}s)"
            )

            messagebox.showinfo(
                "Start Accepted",
                "START_TEST was accepted.\n\n"
                f"Request ID: {request.request_id}\n\n"
                f"The test will auto-stop after "
                f"{request.duration_s} s "
                "(or press Stop Test).",
                parent=self.master,
            )

        except Exception as error:
            self.active_test_request_id = None
            self._set_coordinator_state(CoordinatorState.IDLE)
            self.status_var.set("START_TEST failed")
            self._append_log(f"START_TEST error: {error}")
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

    def _on_baseline_test(self) -> None:
        """Validate, confirm, log and send BASELINE_TEST.

        Deliberately parallel to _on_start_test: a baseline is a normal run of the
        full DUT stack with the beam off, so it uses the same ACTIVE state, the
        same Stop Test button and the same auto-stop mirror. The only differences
        are the command, the length in minutes, and the current CSV at the end.
        """

        if (
            self.coordinator_state
            != CoordinatorState.IDLE
        ):
            self._append_log(
                "BASELINE_TEST ignored because "
                "the coordinator is not idle."
            )
            return

        try:
            minutes = int(
                self.baseline_minutes_var.get().strip()
            )

            request = BaselineTestRequest.create(
                duration_minutes=minutes,
            )

        except (TypeError, ValueError) as error:
            self.status_var.set(
                "Invalid baseline duration"
            )
            self._append_log(
                f"Validation error: {error}"
            )

            self._record_event(
                "BASELINE_TEST_VALIDATION_FAILED",
                transport=self.transport_mode,
                error=str(error),
            )

            messagebox.showerror(
                "Invalid Baseline Duration",
                str(error),
                parent=self.master,
            )
            return

        confirmation_message = (
            "Confirm BASELINE_TEST\n\n"
            "This runs the full test stack (CUDA particle sim + "
            "GPU RAM tester) with NO beam, and logs the board's "
            "input current to a CSV.\n\n"
            f"Duration: {request.duration_minutes} min "
            f"({request.duration_s} s)\n\n"
            f"Transport: "
            f"{self.transport_mode.upper()}\n\n"
            "Confirm the beam is OFF, then send this command?"
        )

        confirmed = messagebox.askyesno(
            "Confirm Baseline Test",
            confirmation_message,
            parent=self.master,
        )

        if not confirmed:
            self.status_var.set(
                "BASELINE_TEST cancelled"
            )
            self._append_log(
                "BASELINE_TEST cancelled before "
                f"submission: {request.request_id}"
            )

            self._record_event(
                "BASELINE_TEST_CANCELLED",
                transport=self.transport_mode,
                request=request.to_dict(),
            )
            return

        self._set_coordinator_state(
            CoordinatorState.STARTING
        )
        self.status_var.set(
            "Submitting BASELINE_TEST via "
            f"{self.transport_mode}..."
        )
        self.master.update_idletasks()

        try:
            event_saved = self._record_event(
                "BASELINE_TEST_SENT",
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
                    "BASELINE_TEST not sent"
                )

                messagebox.showerror(
                    "Persistent Logging Failed",
                    "BASELINE_TEST was not sent because "
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
            self.active_run_is_baseline = True

            self._set_coordinator_state(
                CoordinatorState.ACTIVE
            )

            # Same mirror timer as a beam run: the DUT owns the authoritative
            # stop, this one fetches the summary + CSV without an operator click.
            self._schedule_auto_stop(request.duration_s)

            self._record_event(
                "BASELINE_TEST_ACCEPTED",
                transport=self.transport_mode,
                request=request.to_dict(),
                response=response,
            )

            baseline = response.get("baseline")
            csv_name = (
                baseline.get("csv_name")
                if isinstance(baseline, dict)
                else None
            )

            self.status_var.set(
                "Baseline active - "
                f"{request.request_id[:8]} "
                f"(auto-stop in {request.duration_minutes} min)"
            )

            messagebox.showinfo(
                "Baseline Accepted",
                "BASELINE_TEST was accepted.\n\n"
                f"Request ID: "
                f"{request.request_id}\n\n"
                f"Logging current to:\n{csv_name or '(DUT did not name a CSV)'}\n\n"
                f"The run will auto-stop after "
                f"{request.duration_minutes} min "
                "(or press Stop Test).",
                parent=self.master,
            )

        except Exception as error:
            self.active_test_request_id = None
            self.active_run_is_baseline = False

            self._set_coordinator_state(
                CoordinatorState.IDLE
            )

            self.status_var.set(
                "BASELINE_TEST failed"
            )
            self._append_log(
                f"BASELINE_TEST error: {error}"
            )

            self._record_event(
                "BASELINE_TEST_FAILED",
                transport=self.transport_mode,
                request_id=request.request_id,
                error=str(error),
            )

            messagebox.showerror(
                "Baseline Failed",
                str(error),
                parent=self.master,
            )

    def _on_stop_test(self, automatic: bool = False) -> None:
        """Confirm, log and send STOP_TEST. When `automatic` (fired by the duration
        timer) the confirmation dialog is skipped."""

        if (
            self.coordinator_state
            != CoordinatorState.ACTIVE
        ):
            self._append_log(
                "STOP_TEST ignored because "
                "there is no active test."
            )
            return

        # A manual stop pre-empts the pending auto-stop timer; an automatic stop has
        # already cleared it. Either way, ensure no stale timer fires later.
        self._cancel_auto_stop()

        if automatic:
            self._append_log(
                "Auto-stop: test duration elapsed; "
                "sending STOP_TEST."
            )

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

        confirmed = (
            True
            if automatic
            else messagebox.askyesno(
                "Confirm Stop Test",
                confirmation_message,
                parent=self.master,
            )
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

            # Post-test results: the DUT returns a "summary" in the STOP ack
            # (duration, SEE counts by type, SEEs/sec). Show it and save a CSV.
            summary = response.get("summary")
            csv_path = None
            if (
                isinstance(summary, dict)
                and "by_type" in summary
            ):
                csv_path = self._save_result_csv(summary)

            # A baseline run additionally returns the current capture; collect the
            # CSV the DUT wrote (mirrored here by the arbiter's pull loop).
            was_baseline = self.active_run_is_baseline
            self.active_run_is_baseline = False
            baseline = response.get("baseline")
            baseline_csv_path = None
            if isinstance(baseline, dict):
                baseline_csv_path = self._fetch_baseline_csv(
                    baseline
                )

            # The run is over, so the heavy data is safe to move now.
            self._trigger_full_pull(stopped_target_id)

            results_text = self._format_results(
                stopped_target_id,
                summary,
                csv_path,
            )

            if was_baseline or isinstance(baseline, dict):
                results_text = "\n".join(
                    [results_text]
                    + self._format_baseline(
                        baseline,
                        baseline_csv_path,
                    )
                )

            messagebox.showinfo(
                "Baseline Complete" if was_baseline else "Test Complete",
                results_text,
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

    def _append_see(
        self,
        message: str,
    ) -> None:
        """Append one line to the live SEE panel."""

        timestamp = datetime.now().strftime(
            "%H:%M:%S"
        )

        self.see_log.configure(state="normal")
        self.see_log.insert(
            "end",
            f"[{timestamp}] {message}\n",
        )
        self.see_log.see("end")
        self.see_log.configure(state="disabled")

    def _clear_see_panel(self) -> None:
        """Empty the live SEE panel. Called on a new START so the panel shows only
        the current run's events -- the tailer already tracks new events by byte
        offset, so a clean run appends nothing; without this the previous run's rows
        stayed visible and a clean run looked like it still had SEEs (seen 2026-08-02)."""
        self.see_log.configure(state="normal")
        self.see_log.delete("1.0", "end")
        self.see_log.configure(state="disabled")

    def _poll_sees(self) -> None:
        """Tail the arbiter_logs/ mirror and append any new SEE events. Reschedules
        itself every SEE_POLL_MS. Never raises into the Tk loop."""

        try:
            for event in self._see_tailer.poll():
                label = SEE_TYPE_LABELS.get(
                    event["type_key"],
                    event["type_key"],
                )
                detail = event.get("detail")
                suffix = f"  ({detail})" if detail else ""
                self._append_see(
                    f"{event.get('ts', '')}  "
                    f"{event.get('jetson_id', '?')}  "
                    f"{label}{suffix}"
                )
        except Exception as error:  # noqa: BLE001 - never kill the poll loop
            self._append_see(
                f"[SEE tail error: {error}]"
            )
        finally:
            self._see_after_id = self.master.after(
                SEE_POLL_MS,
                self._poll_sees,
            )


def run() -> None:
    """Create and start the mock-mode application."""

    root = tk.Tk()
    TestCoordinatorApp(root)
    root.mainloop()