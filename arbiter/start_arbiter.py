#!/usr/bin/env python3
"""start_arbiter.py -- one-command arbiter launcher.

Brings the whole arbiter console up with a single command:

    python arbiter/start_arbiter.py                    # DUT on the cable (192.168.1.20)
    python arbiter/start_arbiter.py --host orin-nano-03   # a Tailscale name/IP

It starts, in order:
  1. the heartbeat listener (this repo's arbiter/heartbeat_listener.py) in its own
     console window, so you can watch `seq` climb,
  2. a live log-pull loop in a background thread (scp -> <coordinator>/arbiter_logs,
     feeds the GUI's live SEE panel; scp ships with Windows OpenSSH -- no rsync),
  3. the coordinator GUI (app_local_tcp.py) in the foreground.

Layout it assumes (all clones side-by-side in one folder), e.g.:

    radtest-arbiter/
      jetson-orin-see-testsuite/   <- this repo (has this launcher + the listener)
      melagen-test-coordinator/    <- the GUI (app_local_tcp.py)

If the coordinator repo is somewhere else, pass --coordinator-dir. Closing the GUI
stops the pull loop; close the heartbeat window yourself.
"""
import argparse
import os
import subprocess
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))          # <base>/jetson-orin-see-testsuite/arbiter
DUT_REPO = os.path.dirname(HERE)                            # jetson-orin-see-testsuite
BASE = os.path.dirname(DUT_REPO)                            # parent folder holding the sibling repos
HB_LISTENER = os.path.join(HERE, "heartbeat_listener.py")  # this repo's own listener
DEFAULT_COORD = os.path.join(BASE, "melagen-test-coordinator")
DUT_LOG_DIR = "/var/log/radtest"


# Per-channel file patterns to pull. power/ carries the baseline current CSV and
# its summary sidecar as well as the JSONL -- that CSV is the deliverable of a
# BASELINE_TEST, and the GUI copies it out of here into results/.
PULL_PATTERNS = {
    "compute": ("*.jsonl",),
    "memory": ("*.jsonl",),
    "boot_state": ("*.jsonl",),
    "power": ("*.jsonl", "*.csv", "*.summary.json"),
}


def pull_loop(dut_host, dut_user, ssh_key, logs, stop):
    """Every 3 s, scp the DUT's event logs (and baseline CSVs) into <logs>/<channel>/."""
    ssh_opts = ["-i", ssh_key, "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
                "-o", "StrictHostKeyChecking=accept-new"]
    while not stop.is_set():
        for sub, patterns in PULL_PATTERNS.items():
            dest = os.path.join(logs, sub)
            os.makedirs(dest, exist_ok=True)
            for pattern in patterns:
                # The glob is expanded on the DUT by its shell (scp passes it through).
                subprocess.run(
                    ["scp", "-q", *ssh_opts,
                     f"{dut_user}@{dut_host}:{DUT_LOG_DIR}/{sub}/{pattern}", dest + os.sep],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
        stop.wait(3)


def main():
    ap = argparse.ArgumentParser(description="One-command arbiter launcher.")
    ap.add_argument("--host", default="192.168.1.20",
                    help="DUT address (default: the direct-cable 192.168.1.20).")
    ap.add_argument("--coordinator-dir", default=DEFAULT_COORD,
                    help="Path to the melagen-test-coordinator repo (default: sibling of this repo).")
    ap.add_argument("--dut-user", default="radpull", help="Low-priv log-pull user.")
    ap.add_argument("--ssh-key", default=os.path.expanduser("~/.ssh/id_ed25519"),
                    help="SSH private key authorized for radpull on the DUT.")
    args = ap.parse_args()
    py = sys.executable

    gui = os.path.join(args.coordinator_dir, "app_local_tcp.py")
    logs = os.path.join(args.coordinator_dir, "arbiter_logs")
    if not os.path.isfile(gui):
        sys.exit(f"[arbiter] can't find the GUI at {gui}\n"
                 f"          clone melagen-test-coordinator beside this repo, or pass --coordinator-dir.")
    if not os.path.isfile(HB_LISTENER):
        sys.exit(f"[arbiter] can't find the heartbeat listener at {HB_LISTENER}")
    os.makedirs(logs, exist_ok=True)

    print(f"[arbiter] target DUT: {args.host}")

    # 1. Heartbeat listener -- its own window so the operator can watch seq climb.
    new_console = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)  # Windows; 0 elsewhere
    print("[arbiter] starting heartbeat listener (UDP 5555) in a new window...")
    subprocess.Popen([py, HB_LISTENER, "--port", "5555", "--timeout", "5"],
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
