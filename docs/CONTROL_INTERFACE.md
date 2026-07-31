# Test-control interface (arbiter → DUT)

The arbiter has a **start/stop-test button**; pressing it sends a JSON command to
each DUT over Ethernet. The arbiter (sender) lives in a teammate's separate repo;
this repo implements only the **DUT-side receiver**, `jetson/control/test_control.py`,
run as the `test_control.service` systemd unit.

## Wire contract

**Transport:** TCP. The DUT listens on `listen_port` (default **5599**); the
arbiter opens a connection, sends one JSON object, reads one JSON reply, closes.
Pretty-printed or compact JSON both work (the receiver reads until one complete
object has arrived).

> **Transport confirmed: TCP** (the test coordinator uses TCP; the heartbeat
> monitor uses UDP). The **port** (5599) is still ours to align with the arbiter
> author — confirm the sender connects to the same port.

### Request (arbiter → DUT)

```json
{
  "protocol_version": 1,
  "command": "START_TEST",
  "request_id": "unique-request-id",
  "beam_energy_mev": 100,
  "shielding_material": "MLC1",
  "shielding_thickness_mm": 12,
  "sent_at_utc": "2026-07-31T15:00:00.000Z"
}
```

Validated against (mirrors the sender's spec, held in `config/test_control.json`):

| Field | Rule |
|---|---|
| `protocol_version` | must equal `1` |
| `command` | `START_TEST` (or `STOP_TEST`, see below) |
| `beam_energy_mev` | one of `53, 100, 200` |
| `shielding_material` | one of `Aluminium, MLC1, MLC2` |
| `shielding_thickness_mm` | one of `8, 12, 16` |
| `request_id`, `sent_at_utc` | required (present) |

A request failing any rule is **rejected** (no action taken) with an `error` reply.

### Reply (DUT → arbiter)

```json
{
  "protocol_version": 1,
  "request_id": "unique-request-id",
  "status": "ok",
  "detail": "started",
  "jetson_id": "orin-nano-03",
  "applied": {"run_id": "unique-request-id", "beam_energy": "100MeV", "shield_config": "MLC1_12mm"},
  "channels": [{"name": "compute", "service": "cuda_particles.service", "ok": true, "detail": "restart ok"}],
  "handled_at_utc": "2026-07-31T15:00:00.123Z"
}
```

`status` is `error` if the request was invalid or any channel failed to start/stop
(`detail`/`channels` say which).

## What START_TEST does on the DUT

For each configured channel (compute + GPU memory), in order:

1. **Writes the run metadata into the channel's JSON config** so every emitted
   event record carries the beam/shield context:
   - `run_id` ← `request_id`
   - `beam_energy` ← `"<beam_energy_mev>MeV"` (e.g. `"100MeV"`)
   - `shield_config` ← `"<material>_<thickness>mm"` (e.g. `"MLC1_12mm"`)
2. **Touches the channel's `ARMED` flag** (so the test also survives a reboot —
   see `docs/SERVICES.md`).
3. **`systemctl restart`s the channel service** (restart, not start, so a new
   START with different beam params re-applies them even if a test is running).

**Idempotency:** a repeated `request_id` is acknowledged `ok` without re-acting,
so an arbiter retry can't double-start.

> **Note:** the arbiter host code is a teammate's, in a separate repo — this repo
> owns only the DUT receiver. See [`arbiter/README.md`](../arbiter/README.md).

## STOP_TEST — our forward-compatible extension

The arbiter's published `SUPPORTED_COMMAND` is currently only `START_TEST`. Since
the button is start/**stop**, the receiver also accepts `STOP_TEST` (needs only
`protocol_version`, `command`, `request_id`, `sent_at_utc`): it **removes each
`ARMED` flag and `systemctl stop`s each channel**. For the stop button to reach
us, the arbiter author just needs to send a `STOP_TEST` message in the same shape.

## Metadata note (campaign vs dev)

START_TEST rewrites the tracked channel config files (to inject the beam metadata),
so during a campaign the git clone will show those configs as modified — expected.
This matches the frozen-image campaign model (you don't `git pull` mid-campaign).
On a dev board, `git checkout -- <config>` or STOP first if you need a clean pull.

## Install (on each DUT)

Standard-library only — no extra deps. Runs as **root** (it must control services
and write flags):

```bash
sudo cp ~/see-testsuite/jetson/control/test_control.service /etc/systemd/system/test_control.service
sudo systemctl daemon-reload
sudo systemctl enable --now test_control.service
systemctl status test_control.service --no-pager
```

Verify it's listening and watch commands arrive:

```bash
sudo ss -ltnp | grep 5599
tail -f ~/see-testsuite/jetson/control/logs/test_control.jsonl
```

Restrict who can command the board by setting `allowed_peers` (arbiter IPs) in
`config/test_control.json`; `[]` accepts any source.
