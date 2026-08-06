#!/usr/bin/env bash
# flash-status.sh -- lightweight terminal view of the flash-monitor dashboard.
# Reads http://localhost:8080/status.json every 2 s and renders ASCII meters.
# Run from any shell (bench terminal, SSH session): bash flash-status.sh
set -u
URL="${1:-http://localhost:8080/status.json}"

render() {
python3 - "$URL" <<'PY'
import json, sys, urllib.request
try:
    s = json.load(urllib.request.urlopen(sys.argv[1], timeout=3))
except Exception as e:
    print("monitor unreachable:", e); sys.exit(0)

def bar(pct, width=40):
    if pct is None: return "[" + " " * width + "]  --"
    fill = int(width * pct / 100)
    return "[" + "#" * fill + "-" * (width - fill) + f"] {pct:3d}%"

def gb(a, b):
    return f"({a if a is not None else '?'} / {b if b is not None else '?'} GB)"

el = s.get("elapsed_s")
el = f"{el//60}m {el%60}s" if el is not None else "--"
print("=" * 62)
print(f"  JETSON FLASH MONITOR          updated {s.get('updated','')}")
print("=" * 62)
print(f"  {s.get('phase_icon','')} {s.get('phase','')}")
print(f"    {s.get('detail','')}")
print()
print(f"  board in recovery: {'yes' if s.get('board_in_recovery') else 'no':3}"
      f"    elapsed: {el:10}  rate: "
      f"{str(s.get('xfer_rate_mbs') or '--'):>6} MB/s")
print()
print(f"  1. image build   {bar(s.get('build_pct'))} {gb(s.get('build_done_gb'), s.get('build_total_gb'))}")
print(f"  2. write to board{bar(s.get('xfer_pct'))} {gb(s.get('xfer_done_gb'), s.get('xfer_total_gb'))}")
print()
print(f"  log: {s.get('log_name','')}")
for ln in (s.get("log_tail") or [])[-6:]:
    print("   |", ln[:100])
PY
}

trap 'printf "\033[?25h"; exit 0' INT TERM
printf "\033[?25l"
while true; do
    out=$(render)
    printf "\033[H\033[2J%s\n" "$out"
    sleep 2
done
