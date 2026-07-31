# ⛔ arbiter/ — NOT OWNED BY THIS REPO. DO NOT USE OR DEPLOY.

**The arbiter (host) side is a teammate's responsibility, maintained in a
separate repository. Nothing in this `arbiter/` directory is used, built, or
deployed by this project.**

Everything here (`arbiter_main.py`, `heartbeat_listener.py`, `power_reader.py`,
`pull_logs.sh`, `requirements.txt`, `dashboard/`) is **reference/scaffolding
only** — an early sketch of what the arbiter might do, kept so the DUT-side
contracts (event schema, transports, log layout) have something to point at. It
is **not authoritative** and may be stale or wrong. Do not run it, extend it, or
treat it as the arbiter implementation.

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
