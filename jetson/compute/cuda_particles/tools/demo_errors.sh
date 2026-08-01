#!/usr/bin/env bash
# demo_errors.sh -- generate SEE events on demand for the live-panel demo (TEST ONLY).
#
# Writes to a THROWAWAY log dir (/tmp/see_demo) and reads the real per-board golden
# table read-only. It never writes to the real logs (/var/log/radtest) or modifies
# the golden table, so it can't pollute or break a real run. Every event it produces
# is tagged "injected"/"chaos":true with a loud synthetic_run marker (see the binary).
#
# It exists mainly to sidestep shell-quoting pain: generating the demo config inline
# over ssh from PowerShell mangles the nested quotes. Here the config is built by a
# heredoc, so you invoke it with one clean command and no escaping:
#
#   ssh melagen@<board> "~/see-testsuite/jetson/compute/cuda_particles/tools/demo_errors.sh chaos 0.05"
#
# Usage:
#   demo_errors.sh chaos [prob]           continuous random bit-flips (default prob 0.05)
#   demo_errors.sh bitflip|nan|oob [at]   one injected upset at iter <at> (default 500)
#
# Ctrl-C to stop chaos (it runs until stopped). Nothing persists but /tmp/see_demo,
# which you can delete anytime.
set -euo pipefail

cd "$(dirname "$0")/.."                 # -> the cuda_particles dir (build/, config/, data/)

MODE="${1:-chaos}"
LOGDIR="/tmp/see_demo"
CONF="/tmp/see_demo.json"

mkdir -p "$LOGDIR"
python3 - "$LOGDIR" "$CONF" "$MODE" <<'PY'
import json, sys
logdir, conf, mode = sys.argv[1], sys.argv[2], sys.argv[3]
c = json.load(open("config/particles.json"))
c["log_dir"] = logdir
c["heartbeat_path"] = logdir + "/hb.txt"
c["save_see_epochs"] = False            # keep the throwaway dir tiny (no 10 MB dumps)
c["iterations"] = 0 if mode == "chaos" else 1000   # chaos=forever; inject=one epoch
json.dump(c, open(conf, "w"))
PY

case "$MODE" in
  chaos)
    PROB="${2:-0.05}"
    echo "[demo] chaos prob=$PROB -> $LOGDIR (Ctrl-C to stop)" >&2
    exec ./build/cuda_particles --config "$CONF" --chaos --chaos-prob "$PROB"
    ;;
  bitflip|nan|oob)
    AT="${2:-500}"
    echo "[demo] inject $MODE at iter $AT -> $LOGDIR" >&2
    exec ./build/cuda_particles --config "$CONF" --inject "$MODE" --inject-at "$AT"
    ;;
  *)
    echo "usage: demo_errors.sh chaos [prob] | bitflip|nan|oob [at]" >&2
    exit 1
    ;;
esac
