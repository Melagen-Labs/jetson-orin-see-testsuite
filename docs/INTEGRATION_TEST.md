# Integration test — DUT ↔ arbiter over Ethernet

End-to-end validation of the DUT (Jetson Orin Nano) against the arbiter across a
direct Ethernet cable. The three DUT↔arbiter interfaces are exercised:

| Interface | Direction | Transport / port |
|---|---|---|
| Test-control (start/stop) | arbiter → DUT | **TCP 6000** |
| Heartbeat (liveness) | DUT → arbiter | **UDP 5555** |
| Log pull | arbiter → DUT | **SSH/rsync** (user `radpull`) |

The arbiter host code is a teammate's (see [`arbiter/README.md`](../arbiter/README.md)).
For self-testing the DUT side, **any laptop can stand in as the arbiter** using the
Python snippets below; swap in the real arbiter later. This procedure was run with
a **Windows** laptop as the stand-in arbiter, so it includes the Windows quirks.

## Topology / addresses

```
Jetson (enP8p1s0)  192.168.1.20/24  <--- Ethernet cable --->  Arbiter  192.168.1.10/24
```
A direct cable has no DHCP, so both ends get a static IP on the same /24. Modern
NICs auto-MDIX, so a standard cable works. Interface names are examples — check
yours (`ip -br addr` on the Jetson, `Get-NetAdapter` on Windows).

---

## Phase 0 — DUT prep (one-time)

On the Jetson, install the control receiver if not already running:
```bash
sudo cp ~/see-testsuite/jetson/control/test_control.service /etc/systemd/system/test_control.service
sudo systemctl daemon-reload
sudo systemctl enable --now test_control.service
systemctl is-active test_control.service   # expect: active
```
The test channels (`mem_check_gpu`, `cuda_particles`) and the `radpull` user +
`/var/log/radtest` tree are assumed already set up (see `docs/SERVICES.md`).

## Phase 1 — Ethernet link + static IPs

### Jetson side (persistent, via NetworkManager)
NetworkManager manages the wired port, so a manual `ip addr add` gets wiped and
its default DHCP profile ("Wired connection 1") spams "connection failed" popups
on a DHCP-less direct link. Use a static profile instead:
```bash
sudo nmcli con modify "Wired connection 1" connection.autoconnect no
sudo nmcli con down "Wired connection 1" 2>/dev/null
sudo nmcli con add type ethernet ifname enP8p1s0 con-name radtest-eth ip4 192.168.1.20/24
sudo nmcli con modify radtest-eth ipv4.method manual ipv6.method disabled ipv4.never-default yes
sudo nmcli con up radtest-eth
ip -br addr show enP8p1s0        # expect: 192.168.1.20/24
```
`ipv4.never-default yes` keeps this private link off the default route (internet /
Tailscale stay on WiFi).

### Arbiter side — Windows (PowerShell **as Administrator**)
`Get-NetAdapter` to find the **physical** wired adapter (a USB-GbE dongle shows as
e.g. `Ethernet 2`; ignore VirtualBox "Host-Only" virtual adapters). Then:
```powershell
New-NetIPAddress -InterfaceAlias "Ethernet 2" -IPAddress 192.168.1.10 -PrefixLength 24
```
(If it says the address already exists: `Set-NetIPInterface -InterfaceAlias "Ethernet 2" -Dhcp Disabled` first.)

### Arbiter side — Linux
```bash
sudo ip addr add 192.168.1.10/24 dev <ethX>
sudo ip link set <ethX> up
```

### Verify the link (both ways)
```bash
# Jetson:            ping -c3 192.168.1.10
# Arbiter (Win):     ping 192.168.1.20
```
Both must reply before continuing.

## Phase 2 — Test-control (arbiter → DUT, TCP 6000)

> **Windows quoting gotcha:** `python -c '...'` in PowerShell strips the double
> quotes out of the JSON. Write the script to a file instead (the closing `'@`
> must be at column 0):

```powershell
@'
import socket, json
msg = {
    "protocol_version": 1,
    "command": "START_TEST",          # change to "STOP_TEST" to stop
    "request_id": "itest-001",
    "beam_energy_mev": 100,            # 53 | 100 | 200
    "shielding_material": "MLC1",      # Aluminium | MLC1 | MLC2
    "shielding_thickness_mm": 12,      # 8 | 12 | 16
    "sent_at_utc": "2026-07-31T12:00:00.000Z",
}
s = socket.create_connection(("192.168.1.20", 6000), timeout=5)
s.sendall(json.dumps(msg).encode())
print("REPLY:", s.recv(4096).decode())
'@ | Set-Content -Encoding ascii send_test.py
python send_test.py
```
(Linux/real arbiter: same code via `python3 - <<'EOF' ... EOF` or a file.)

**Expect:** `REPLY: {... "status":"ok", "detail":"started", channels[...]"ok":true ...}`.
For `STOP_TEST` only `protocol_version/command/request_id/sent_at_utc` are needed.

**Verify on the Jetson** the beam metadata reached the logs:
```bash
grep '"event":"start"' /var/log/radtest/memory/mem_check_gpu.jsonl | tail -1
# expect: "run_id":"itest-001","beam_energy":"100MeV","shield_config":"MLC1_12mm"
systemctl is-active mem_check_gpu.service cuda_particles.service   # both active after START
```
After a `STOP_TEST`: both services `inactive` and the `ARMED` flags removed.

## Phase 3 — Heartbeat (DUT → arbiter, UDP 5555)

**Arbiter listener** (Windows; click **Allow** on the firewall prompt for Python):
```powershell
@'
import socket
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.bind(("0.0.0.0", 5555))
print("listening on udp/5555 ...")
while True: print(s.recvfrom(2048)[0].decode())
'@ | Set-Content -Encoding ascii hb_listen.py
python hb_listen.py
```
**Jetson sender:**
```bash
python3 ~/see-testsuite/jetson/heartbeat/heartbeat_sender.py --arbiter-ip 192.168.1.10
```
**Expect:** one `{"boot_id":...,"seq":N,"ts":...}` per second, `seq` climbing.
Stop either side with **Ctrl+C** (neither is a service). **Loss test:** unplug the
cable → datagrams stop; replug → they resume (the real monitor flags
`HEARTBEAT_LOST` after ~5 s). This is the exact format Madhav's monitor consumes.

## Phase 4 — Log pull (arbiter → DUT, SSH as `radpull`)

One-time: install the arbiter's **public** key on the DUT:
```bash
echo 'PASTE_ARBITER_PUBLIC_KEY' | sudo tee -a /home/radpull/.ssh/authorized_keys
sudo chown radpull:radpull /home/radpull/.ssh/authorized_keys && sudo chmod 600 /home/radpull/.ssh/authorized_keys
```
Then from the arbiter, pull over the Ethernet link:
```bash
rsync -az -e ssh radpull@192.168.1.20:/var/log/radtest/ ./pulled_logs/
ls -R ./pulled_logs      # expect memory/ compute/ with .jsonl files (+ see_dumps/)
```
(Ansh's `arbiter/pull_logs.sh` does the same with `DUT_HOST=192.168.1.20 DUT_USER=radpull`.)

## Phase 5 — Full dry run

1. Heartbeat sender running → arbiter monitor shows alive.
2. Arbiter sends **START_TEST** → both channels log with beam metadata.
3. Run ~1 min; arbiter **pulls logs** → confirm fresh records with `run_id/beam_energy/shield_config`.
4. Arbiter sends **STOP_TEST** → channels stop, heartbeat still alive.

All four passing = DUT↔arbiter wiring validated.

## Results log

| Phase | Result |
|---|---|
| 0 install test_control | ✅ 2026-07-31 |
| 1 link + static IPs (ping both ways) | ✅ 2026-07-31 |
| 2 START_TEST over Ethernet (metadata in logs, both channels restart) | ✅ 2026-07-31 |
| 3 heartbeat (1 Hz, boot_id + climbing seq received) | ✅ 2026-07-31 |
| 4 log pull | pending (needs arbiter pubkey) |
| 5 full dry run | pending |

## Teardown (return the arbiter laptop to normal)

Windows:
```powershell
Remove-NetIPAddress -InterfaceAlias "Ethernet 2" -IPAddress 192.168.1.10 -Confirm:$false
Set-NetIPInterface -InterfaceAlias "Ethernet 2" -Dhcp Enabled
```
Jetson (only if reverting the wired port): `sudo nmcli con down radtest-eth`. The
`radtest-eth` static profile is harmless to leave in place for future runs.

## Open items with teammates
- ~~Confirm control port~~ — **confirmed TCP 6000** from the coordinator repo
  (`melagen-test-coordinator`, `jetson_port`). DUT now listens on 6000.
- Have Madhav point the coordinator's `jetson_host` at the DUT's Ethernet IP
  (`192.168.1.20`) — its `config.example.json` currently uses the Tailscale IP —
  and press **START_TEST** / **STOP_TEST** for the live Phase 5 run.
- Set the DUT heartbeat `--arbiter-ip` to the real arbiter's Ethernet IP. (The
  coordinator repo itself does not implement heartbeat — that's Madhav's separate
  `melagen-jetson-heartbeat` monitor.)
- Phase 4 log-pull is **not** part of the coordinator repo — it's Ansh's
  `arbiter/pull_logs.sh`. Still needs the **arbiter's SSH public key** in `radpull`.
