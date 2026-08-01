"""Live SEE monitor: tail the pulled DUT logs for new single-event-effect records.

The arbiter keeps a local mirror of each DUT's structured JSONL logs under an
``arbiter_logs/`` tree (populated by the radiation-sheilding repo's
``arbiter/pull_logs.sh`` / ``arbiter_main.py`` on a timer -- the "radpull"
channel). This module tails those files: on each :meth:`SeeLogTailer.poll` it
returns only the SEE records that appeared since the previous call (tracked by a
per-file byte offset, so nothing is re-emitted), classified into the same stable
type keys the DUT summariser uses -- so the live panel and the post-test CSV agree.

It never pulls over the network and never blocks: unreadable or rotated files are
skipped, and a shrunk file (log rotation) resets that file's offset.

Known caveat (accepted, per the §6b spec): this is *near*-real-time. Latency is the
poll interval, and an SEE that crashes the board may not be flushed+pulled until
after reboot (reconstruct from pstore/boot logs then). Fine for operator monitoring
at a few-seconds cadence.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# Subdirectories under the arbiter_logs root that pull_logs.sh populates with
# per-channel JSONL. We scan these for SEE-bearing records.
SEE_LOG_SUBDIRS = ("compute", "memory")


def classify_see(record: dict[str, Any]) -> tuple[str, str] | None:
    """Map one event record to ``(see_type_key, detail)``, or ``None`` if it is not
    an SEE. The keys match :data:`coordinator.ui.SEE_TYPE_LABELS`.

    Attribution keys on record FIELDS (not file names), mirroring the DUT's
    ``summarize_run`` so the live tally agrees with ``test_N.csv``:

    * ``status == "error"``                       -> ``fatal_error``
    * ``event == "mem_upset"``                    -> ``gpu_mem_upset`` (one flipped byte)
    * ``event == "sim_fault"``                    -> ``cuda_shutdown`` (CUDA crash/restart)
    * ``event == "see_event"``                    -> ``see_dump_saved`` — the epoch's
      state-dump marker. Not an extra SEE count (the paired anomalous ``checksum``
      already counted it); it tells the operator whether post-processing data
      (``see_dumps/epoch_*.bin``, for ``see_dump_triage.py``) was saved for that SEE.
    * anomalous final-checkpoint ``checksum``     -> ``cuda_golden_mismatch`` /
      ``cuda_nonfinite`` / ``cuda_anomaly`` by its ``mismatch`` / ``finite`` /
      ``anomaly`` flags. There is exactly one such record per affected epoch, so
      keying on it (rather than the paired ``see_event`` marker) counts each epoch
      once while still recovering the subtype.
    """
    status = record.get("status")
    event = record.get("event")

    if status == "error":
        return "fatal_error", str(
            record.get("detail") or record.get("error") or ""
        )

    if event == "mem_upset":
        address = record.get("address")
        detail = f"addr {address}" if address is not None else ""
        return "gpu_mem_upset", detail

    if event == "sim_fault":
        detail = str(record.get("error") or "CUDA fault")
        dump = record.get("dump")
        detail += f"; dump: {dump}" if dump else "; no dump saved"
        return "cuda_shutdown", detail

    if event == "see_event":
        epoch = record.get("epoch")
        where = f"epoch {epoch}" if epoch is not None else "epoch ?"
        dump = record.get("dump")
        if dump:
            return "see_dump_saved", f"{where} -> {dump}"
        return "see_dump_saved", f"{where} -> NO dump saved"

    if event == "checksum" and (
        status == "anomaly" or record.get("anomaly") is True
    ):
        epoch = record.get("epoch")
        where = f"epoch {epoch}" if epoch is not None else ""
        if record.get("mismatch") is True:
            return "cuda_golden_mismatch", where
        if record.get("finite") is False:
            return "cuda_nonfinite", where
        return "cuda_anomaly", where

    return None


class SeeLogTailer:
    """Track per-file read offsets under an ``arbiter_logs`` root and yield only the
    NEW SEE events on each :meth:`poll`. Safe to call repeatedly on a GUI timer."""

    def __init__(self, root: str | Path, from_start: bool = False) -> None:
        self.root = Path(root)
        # Absolute file path -> number of bytes already consumed.
        self._offsets: dict[str, int] = {}
        # A live monitor should show what happens FROM NOW ON. Without this, the
        # first poll replays every historical SEE in the mirror (previous runs,
        # or leftover hand-seeded demo lines) as though they just occurred. So on
        # the first poll we fast-forward existing files to their current end and
        # emit nothing; files that appear later are genuinely new and get read
        # whole. Pass from_start=True to replay history instead (tests/forensics).
        self._primed = from_start

    def poll(self) -> list[dict[str, Any]]:
        """Return SEE events (dicts with ts / jetson_id / type_key / detail) that
        appeared since the previous poll, oldest first. Missing root/dirs -> []."""
        if not self._primed:
            self._prime()
            return []
        events: list[dict[str, Any]] = []
        for sub in SEE_LOG_SUBDIRS:
            directory = self.root / sub
            if not directory.is_dir():
                continue
            for path in sorted(directory.glob("*.jsonl")):
                events.extend(self._read_new(path))
        return events

    def _prime(self) -> None:
        """Fast-forward every existing log to its current end, so only events
        appended after the monitor started are reported."""
        for sub in SEE_LOG_SUBDIRS:
            directory = self.root / sub
            if not directory.is_dir():
                continue
            for path in sorted(directory.glob("*.jsonl")):
                try:
                    self._offsets[str(path)] = path.stat().st_size
                except OSError:
                    continue
        self._primed = True

    def _read_new(self, path: Path) -> list[dict[str, Any]]:
        """Read bytes appended to one file since last time, up to the last complete
        line, and return the SEE events among them. Partial trailing lines are left
        for the next poll so a mid-append record is never split."""
        key = str(path)
        out: list[dict[str, Any]] = []
        try:
            with open(path, "rb") as handle:
                handle.seek(0, 2)
                size = handle.tell()
                start = self._offsets.get(key, 0)
                if size < start:  # file rotated/truncated -> re-read from the top
                    start = 0
                if size == start:
                    return out
                handle.seek(start)
                data = handle.read()
        except OSError:
            return out

        newline = data.rfind(b"\n")
        if newline == -1:
            # No complete line yet; leave the offset so we retry these bytes later.
            self._offsets[key] = start
            return out
        consumed = newline + 1
        self._offsets[key] = start + consumed

        for raw in data[:consumed].decode("utf-8", "replace").splitlines():
            raw = raw.strip()
            if not raw:
                continue
            try:
                record = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(record, dict):
                continue
            classified = classify_see(record)
            if classified is None:
                continue
            type_key, detail = classified
            out.append(
                {
                    "ts": record.get("ts", ""),
                    "jetson_id": record.get("jetson_id", "?"),
                    "type_key": type_key,
                    "detail": detail,
                }
            )
        return out
