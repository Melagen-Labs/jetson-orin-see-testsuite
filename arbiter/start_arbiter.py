#!/usr/bin/env python3
"""start_arbiter.py -- one-command arbiter launcher.

Brings the whole arbiter console up with a single command:

    python arbiter/start_arbiter.py                    # DUT on the cable (192.168.1.20)
    python arbiter/start_arbiter.py --host orin-nano-03   # a Tailscale name/IP

It starts, in order:
  1. the heartbeat listener (this repo's arbiter/heartbeat_listener.py) in its own
     console window, so you can watch `seq` climb,
  2. a live log-pull loop in a background thread (tar-over-ssh mirror of
     /var/log/radtest -> <coordinator>/arbiter_logs, feeds the GUI's live SEE
     panel; ssh + tar ship with Windows -- no rsync),
  3. the coordinator GUI (app_local_tcp.py) in the foreground.

Everything lives in this one repo: the GUI was imported to `arbiter/coordinator/`
on 2026-08-06 (previously a separate `melagen-test-coordinator` clone that had to
sit beside this one). One clone, one `git pull`, one version of the DUT<->arbiter
contract -- the skew between the two repos was a live source of bugs.

Pass --coordinator-dir only to run against a different checkout of the GUI.
Closing the GUI stops the pull loop; close the heartbeat window yourself.
"""
import argparse
import csv
import json
import os
import subprocess
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))          # <base>/jetson-orin-see-testsuite/arbiter
DUT_REPO = os.path.dirname(HERE)                            # jetson-orin-see-testsuite
BASE = os.path.dirname(DUT_REPO)                            # parent folder holding the sibling repos
HB_LISTENER = os.path.join(HERE, "heartbeat_listener.py")  # this repo's own listener
DEFAULT_COORD = os.path.join(HERE, "coordinator")          # the GUI, in-repo since 2026-08-06
DUT_LOG_DIR = "/var/log/radtest"


def jsonl_to_csv(logs):
    """Give every mirrored .jsonl a sibling .csv so the operator can open any
    log as a spreadsheet. Regenerated only when the .jsonl is newer, so each
    pull tick converts just what changed. Columns are the union of keys across
    records (first-appearance order); nested values are JSON-encoded. The
    .jsonl stays the machine format the tailer/summarizer read."""
    for root, _dirs, files in os.walk(logs):
        for name in files:
            if not name.endswith(".jsonl"):
                continue
            src = os.path.join(root, name)
            dst = src[:-6] + ".csv"
            try:
                if os.path.exists(dst) and os.path.getmtime(dst) >= os.path.getmtime(src):
                    continue
                records, columns = [], []
                with open(src, encoding="utf-8", errors="replace") as handle:
                    for line in handle:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            record = json.loads(line)
                        except ValueError:
                            continue
                        if not isinstance(record, dict):
                            continue
                        records.append(record)
                        for key in record:
                            if key not in columns:
                                columns.append(key)
                if not records:
                    continue
                # The DUT authors run-dir current_samples.csv itself (with the
                # extra rails). A CSV whose header doesn't match this converter's
                # first column is board-authored -- never overwrite it.
                if os.path.exists(dst):
                    with open(dst, encoding="utf-8", errors="replace") as handle:
                        first = handle.readline().split(",", 1)[0].strip()
                    if first != columns[0]:
                        continue
                with open(dst, "w", newline="", encoding="utf-8") as handle:
                    writer = csv.writer(handle)
                    writer.writerow(columns)
                    for record in records:
                        writer.writerow(
                            ["" if record.get(col) is None
                             else record.get(col) if isinstance(record.get(col), (str, int, float, bool))
                             else json.dumps(record.get(col))
                             for col in columns]
                        )
            except OSError:
                continue  # skip locked/vanishing files; retried next tick


def pull_loop(dut_host, dut_user, ssh_key, logs, stop):
    """Every 3 s, mirror the DUT's whole /var/log/radtest tree into <logs> via
    tar-over-ssh. One connection brings the per-run folders (compute/memory/power
    all nest <channel>/<run_id>/ now) across with their structure intact --
    per-file scp globs can't do that. Only the ~10 MB-each see_dumps are
    excluded; they arrive in the end-of-run PULL_MODE=full pull. Total moved per
    tick is KBs, so the 3 s cadence costs the DUT nothing."""
    ssh_opts = ["-i", ssh_key, "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
                "-o", "StrictHostKeyChecking=accept-new"]
    remote_tar = f"tar -C {DUT_LOG_DIR} -cz --exclude=see_dumps ."
    while not stop.is_set():
        # The GUI's DUT selector writes active_board.json; mirror into that
        # board's folder so each DUT's logs stay separated locally.
        dest = logs
        try:
            with open(os.path.join(logs, "active_board.json"),
                      encoding="utf-8") as handle:
                board = json.load(handle).get("name", "")
            if board:
                dest = os.path.join(logs, board)
        except (OSError, ValueError):
            pass
        try:
            os.makedirs(dest, exist_ok=True)
            src = subprocess.Popen(
                ["ssh", *ssh_opts, f"{dut_user}@{dut_host}", remote_tar],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            )
            subprocess.run(["tar", "-xz", "-C", dest], stdin=src.stdout,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            src.wait()
        except OSError:
            pass  # ssh/tar missing or DUT down -- try again next tick
        jsonl_to_csv(logs)
        stop.wait(3)


def main():
    ap = argparse.ArgumentParser(description="One-command arbiter launcher.")
    ap.add_argument("--host", default="192.168.1.20",
                    help="DUT address (default: the direct-cable 192.168.1.20).")
    ap.add_argument("--coordinator-dir", default=DEFAULT_COORD,
                    help="Path to the coordinator GUI (default: arbiter/coordinator, in-repo).")
    ap.add_argument("--dut-user", default="radpull", help="Low-priv log-pull user.")
    ap.add_argument("--ssh-key", default=os.path.expanduser("~/.ssh/radtest_pull"),
                    help="SSH private key authorized for radpull on the DUT "
                         "(same default as pull_logs.sh).")
    args = ap.parse_args()
    py = sys.executable

    gui = os.path.join(args.coordinator_dir, "app_local_tcp.py")
    logs = os.path.join(args.coordinator_dir, "arbiter_logs")
    if not os.path.isfile(gui):
        sys.exit(f"[arbiter] can't find the GUI at {gui}\n"
                 f"          it ships in this repo at arbiter/coordinator -- try `git pull`, "
                 f"or pass --coordinator-dir.")
    if not os.path.isfile(HB_LISTENER):
        sys.exit(f"[arbiter] can't find the heartbeat listener at {HB_LISTENER}")
    os.makedirs(logs, exist_ok=True)

    print(f"[arbiter] target DUT: {args.host}")

    # 1. Heartbeat listener -- its own window so the operator can watch seq climb.
    # The listener now persists events to a JSONL log; anchor it inside the
    # coordinator's arbiter_logs tree (its default is CWD-relative, which would
    # scatter logs wherever this launcher happened to be invoked from).
    hb_log = os.path.join(logs, "heartbeat", "heartbeat_log.jsonl")
    new_console = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)  # Windows; 0 elsewhere
    print(f"[arbiter] starting heartbeat listener (UDP 5555, log: {hb_log})...")
    subprocess.Popen([py, HB_LISTENER, "--port", "5555", "--timeout", "5",
                      "--log-file", hb_log,
                      "--active-board-file",
                      os.path.join(logs, "active_board.json")],
                     creationflags=new_console)

    # 2. Live log-pull loop -- background thread.
    print(f"[arbiter] starting live log-pull loop (scp -> {logs})...")
    stop = threading.Event()
    threading.Thread(target=pull_loop,
                     args=(args.host, args.dut_user, args.ssh_key, logs, stop),
                     daemon=True).start()

    # 3. The GUI -- foreground; blocks until the window is closed.
    print("[arbiter] launching GUI. Pick beam+shield, Start Test, Stop Test.")
    try:
        subprocess.run([py, gui, "--host", args.host, "--see-log-root", "./arbiter_logs"],
                       cwd=args.coordinator_dir)
    finally:
        stop.set()
        print("[arbiter] GUI closed; pull loop stopped. (Close the heartbeat window yourself.)")


if __name__ == "__main__":
    main()
