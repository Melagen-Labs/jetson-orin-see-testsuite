# Melagen Test Coordinator

Laptop-side operator interface and TCP receiver prototype for preparing, transmitting, validating, and logging Jetson proton-test control commands.

## Current capabilities

- Tkinter operator GUI with controlled parameter selections
- Beam energy options: 53, 100, and 200 MeV
- Shielding materials: Aluminium, MLC1, and MLC2
- Shielding thicknesses: 8, 12, and 16 mm
- `START_TEST` and `STOP_TEST` commands
- Coordinator states: `IDLE`, `STARTING`, `ACTIVE`, and `STOPPING`
- Operator confirmation before command transmission
- Input validation and protocol-version checks
- Unique UUID request identifiers and UTC timestamps
- Mock transport for GUI-only testing
- TCP transport for laptop-to-receiver communication
- Stateful receiver that permits one active test at a time
- Start/Stop correlation through `request_id` and `target_request_id`
- Structured JSONL event logging on both coordinator and receiver sides
- Automated unit tests for requests, transports, receiver behavior, and event logging

## Current status and limitations

The coordinator can prepare, validate, send, acknowledge, and log test-control requests in mock mode or through the included TCP receiver.

The receiver currently validates commands and updates an in-memory active-test state. It does **not** start or stop a CUDA workload, execute arbitrary shell commands, reboot the Jetson, control beam hardware, or initiate a physical proton test.

Communication with the Jetson over direct Ethernet or Tailscale has not yet been validated in this repository. Receiver state is also not restored after a receiver restart.

## Protocol overview

The GUI sends one newline-delimited UTF-8 JSON object over TCP. The receiver returns one JSON acknowledgment and closes the connection.

Default local receiver settings:

```text
Host: 127.0.0.1
Port: 6000
Timeout: 5 seconds
```

Example `START_TEST` request:

```json
{
  "protocol_version": 1,
  "command": "START_TEST",
  "request_id": "<uuid>",
  "beam_energy_mev": 100,
  "shielding_material": "MLC1",
  "shielding_thickness_mm": 12,
  "sent_at_utc": "<utc-timestamp>"
}
```

Example `STOP_TEST` request:

```json
{
  "protocol_version": 1,
  "command": "STOP_TEST",
  "request_id": "<uuid>",
  "target_request_id": "<accepted-start-request-id>",
  "sent_at_utc": "<utc-timestamp>"
}
```

Further protocol details are documented in [`docs/protocol.md`](docs/protocol.md).

## Event logging

Runtime events are stored as JSONL, with one JSON object per line.

```text
logs/coordinator_events.jsonl
logs/receiver_events.jsonl
```

The coordinator log records operator-side command attempts and responses. The receiver log records commands received, accepted, rejected, and receiver lifecycle events. Matching request identifiers correlate records between the two files.

Runtime `.jsonl` files are excluded from Git. The `logs/.gitkeep` file preserves the log directory in the repository.

## Project structure

```text
melagen-test-coordinator/
├── app.py
├── app_local_tcp.py
├── config.example.json
├── coordinator/
│   ├── __init__.py
│   ├── constants.py
│   ├── event_logger.py
│   ├── request.py
│   ├── transport.py
│   └── ui.py
├── receiver/
│   ├── __init__.py
│   └── test_receiver.py
├── tests/
│   ├── test_event_logger.py
│   ├── test_receiver.py
│   ├── test_request.py
│   └── test_transport.py
├── docs/
│   └── protocol.md
├── logs/
│   └── .gitkeep
├── README.md
└── .gitignore
```

## Run the automated tests

From the repository root:

```powershell
& "C:\msys64\ucrt64\bin\python.exe" `
  -m unittest discover `
  -s tests `
  -v
```

## Run the GUI in mock mode

```powershell
& "C:\msys64\ucrt64\bin\python.exe" ".\app.py"
```

Mock mode validates the GUI, request construction, state transitions, and coordinator logging without opening a network connection.

## Run the local TCP demonstration

Use two PowerShell windows from the repository root.

### Window 1: start the receiver

```powershell
& "C:\msys64\ucrt64\bin\python.exe" `
  -m receiver.test_receiver `
  --host 127.0.0.1 `
  --port 6000 `
  --timeout 5
```

The receiver remains active while waiting for connections. Stop it with `Ctrl+C` after testing.

### Window 2: start the TCP-connected GUI

`app_local_tcp.py` defaults to `--host 192.168.1.20` (a real DUT over direct
Ethernet), so for the **local** receiver you must point it at localhost explicitly:

```powershell
& "C:\msys64\ucrt64\bin\python.exe" ".\app_local_tcp.py" --host 127.0.0.1
```

Perform one confirmed Start/Stop cycle. The GUI should return to `IDLE`, and both coordinator and receiver JSONL files should contain correlated records.

## Run against a Jetson board (one or many)

The GUI targets a DUT with `--host`; the board-side receiver (`test_control.service`,
in the `jetson-orin-see-testsuite` repo) listens on TCP `6000`. Point it at each
board in turn — nothing else changes per board:

```powershell
# Direct Ethernet — one board on the cable at a time (every board uses this same IP):
& "C:\msys64\ucrt64\bin\python.exe" ".\app_local_tcp.py" --host 192.168.1.20

# A specific board over Tailscale (MagicDNS name or its 100.x.y.z):
& "C:\msys64\ucrt64\bin\python.exe" ".\app_local_tcp.py" --host orin-nano-03
```

The GUI's status line shows the live target host:port — confirm it names the board
you intend before clicking **Start**. The `START_TEST`/`STOP_TEST` contract the DUT
receiver implements is documented in [`docs/protocol.md`](docs/protocol.md).

## Planned work

- Validate direct Ethernet communication between the laptop and Jetson
- Deploy the receiver package to the Jetson
- Connect accepted commands to a defined CUDA workload interface
- Implement safe process startup, shutdown, completion reporting, and failure handling
- Define persistent recovery behavior after receiver restarts
- Integrate the separate heartbeat system with the test-coordinator workflow
