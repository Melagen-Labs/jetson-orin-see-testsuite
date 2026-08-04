# arbiter/ — the arbiter-side host code (in use)

> **This header used to say the directory was unowned scaffolding. That is no
> longer true** — as of 2026-08-02 the arbiter runs from this repo.

Start everything with one command:

```bash
python arbiter/start_arbiter.py
```

That brings up the heartbeat listener, the log-pull loop, and the coordinator GUI
together. Proven end-to-end on 2026-08-02 (chaos run → live SEE panel → results
CSV, then a clean run confirming 0 SEEs).

**Used:** `start_arbiter.py` (launcher), `heartbeat_listener.py` (UDP 5555 — this
superseded the deprecated standalone heartbeat repo), `pull_logs.sh` (scp/rsync
log pull), `arbiter_main.py` (correlator), `requirements.txt`.

**Not used yet:** `power_reader.py` parses a current/status stream, but its serial
transport is retired along with the power-monitor firmware board; it awaits
retarget to the pulled INA3221 records. See its module docstring.

**Still a teammate's repo:** the coordinator GUI itself
(`melagen-test-coordinator`) — start/stop buttons, beam/shielding selection, live
SEE panel, results CSV. This repo owns the DUT side plus the arbiter plumbing
above.

## Who owns what

| Side | Owner | Where |
|---|---|---|
| **DUT** (Jetson Orin Nano test channels, control receiver, heartbeat sender) | **this repo** | `jetson/`, `shared/`, `scripts/`, `docs/` |
| **Arbiter** (host: command sender, listeners, log pull, dashboard) | **teammate (Ansh)** | *separate repo* |

## The DUT↔arbiter contracts we DO own (implement/uphold on our side)

- **Test-control** (arbiter → DUT): **TCP**, JSON command. See
  [`docs/CONTROL_INTERFACE.md`](../docs/CONTROL_INTERFACE.md). Receiver:
  `jetson/control/test_control.py`.
- **Heartbeat** (DUT → arbiter): **UDP** liveness. Sender:
  `jetson/heartbeat/heartbeat_sender.py`.
- **Event log schema** (what the arbiter parses): [`docs/EVENT_SCHEMA.md`](../docs/EVENT_SCHEMA.md),
  emitted via `shared/event_log.py`.
- **Log transfer**: the arbiter **pulls** the DUT's local logs (rsync/SSH). The
  DUT just writes them locally; see `pull_logs.sh` here as the reference the
  teammate builds from.

If you're looking for something to change on the arbiter, it's in the other repo,
not here.
