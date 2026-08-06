#!/usr/bin/env python3
"""flash-monitor.py -- live web dashboard for Jetson flash progress.

Serves http://<laptop>:8080 with a phase indicator, progress meters, and the
latest flash log tail. Observe-only: it never touches the flash tooling, so it
works for any run (l4t_initrd_flash or l4t_backup_restore, any board) by
watching the system state the flash produces:

  * image-build progress  = allocated bytes of bootloader/system.img vs the
                            expected rootfs payload
  * transfer progress     = NFS bytes served to the board this session vs the
                            built image's allocated size
  * board state           = NVIDIA APX (recovery) device presence on USB
  * outcome               = success/error markers in the newest flash log

Run as root (systemd unit flash-monitor.service; a copy of this file lives on
the JETSON_BACKUP drive). Stdlib only.
"""

import json
import os
import re
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

L4T = "/media/ubuntu/JETSON_BACKUP/Linux_for_Tegra"
SYSTEM_IMG = os.path.join(L4T, "bootloader", "system.img")
INITRDLOG = os.path.join(L4T, "initrdlog")
PORT = 8080
POLL_S = 2.0
# Expected rootfs payload (drives the build meter). Overridable without
# editing code:  FLASH_MONITOR_ROOTFS_GB=25 python3 flash-monitor.py
EXPECTED_ROOTFS_BYTES = float(os.environ.get("FLASH_MONITOR_ROOTFS_GB", 19)) * 1e9

FLASH_PROC_RE = "l4t_initrd_flash|l4t_backup_restore|nvrestore_partitions|flash\\.sh"
OK_MARKERS = ("Flash is successful", "Flashing success", "successfully")
BAD_MARKERS = ("Error:", "ERROR", "failed", "Failed")

state_lock = threading.Lock()
state = {
    "phase": "STARTING",
    "phase_icon": "●",
    "detail": "monitor starting",
    "board_in_recovery": False,
    "flash_running": False,
    "build_pct": None, "build_done_gb": None, "build_total_gb": None,
    "xfer_pct": None, "xfer_done_gb": None, "xfer_total_gb": None,
    "xfer_rate_mbs": None,
    "elapsed_s": None,
    "log_name": "", "log_tail": [],
    "updated": "",
}

def sh(cmd):
    try:
        return subprocess.run(cmd, shell=True, capture_output=True, text=True,
                              timeout=10).stdout
    except Exception:
        return ""

def nfs_read_bytes():
    try:
        with open("/proc/net/rpc/nfsd") as f:
            for line in f:
                if line.startswith("io "):
                    return int(line.split()[1])
    except OSError:
        pass
    return None

def img_alloc_bytes():
    try:
        st = os.stat(SYSTEM_IMG)
        return st.st_blocks * 512, st.st_mtime
    except OSError:
        return None, None

def newest_log():
    try:
        logs = [os.path.join(INITRDLOG, f) for f in os.listdir(INITRDLOG)]
        logs = [p for p in logs if os.path.isfile(p)]
        if not logs:
            return "", []
        p = max(logs, key=os.path.getmtime)
        with open(p, errors="replace") as f:
            tail = [ln.rstrip() for ln in f.readlines()[-12:]]
        return os.path.basename(p), tail
    except OSError:
        return "", []

def sampler():
    session_start = None
    nfs_base = None
    img_at_build_end = None
    last_nfs = None
    last_nfs_t = None
    last_result = ("IDLE", "●", "no flash running")

    while True:
        procs = sh(f"pgrep -af '{FLASH_PROC_RE}'")
        running = bool([l for l in procs.splitlines() if "pgrep" not in l and l.strip()])
        apx = "0955:7523" in sh("lsusb")
        alloc, img_mtime = img_alloc_bytes()
        nfs = nfs_read_bytes()
        now = time.time()
        log_name, log_tail = newest_log()

        rate = None
        if nfs is not None and last_nfs is not None and now > last_nfs_t:
            rate = max(0.0, (nfs - last_nfs) / (now - last_nfs_t) / 1e6)
        last_nfs, last_nfs_t = nfs, now

        with state_lock:
            s = state
            s["board_in_recovery"] = apx
            s["flash_running"] = running
            s["log_name"], s["log_tail"] = log_name, log_tail
            s["updated"] = time.strftime("%H:%M:%S")

            if running:
                if session_start is None:
                    session_start = now
                    nfs_base = nfs
                    img_at_build_end = None
                s["elapsed_s"] = int(now - session_start)

                building = img_mtime is not None and (now - img_mtime) < 15
                transferring = rate is not None and rate > 0.5
                if building and not transferring:
                    s["phase"], s["phase_icon"] = "BUILDING IMAGE", "▶"
                    s["detail"] = "packing rootfs into system.img on the laptop"
                    s["build_done_gb"] = round(alloc / 1e9, 1) if alloc else None
                    s["build_total_gb"] = round(EXPECTED_ROOTFS_BYTES / 1e9, 1)
                    if alloc:
                        s["build_pct"] = min(99, int(100 * alloc / EXPECTED_ROOTFS_BYTES))
                elif transferring:
                    if img_at_build_end is None and alloc:
                        img_at_build_end = alloc
                        s["build_pct"] = 100
                    s["phase"], s["phase_icon"] = "WRITING TO BOARD", "▶"
                    s["detail"] = "streaming images to the Jetson over USB"
                    done = (nfs - nfs_base) if (nfs is not None and nfs_base is not None) else None
                    total = img_at_build_end or alloc
                    s["xfer_done_gb"] = round(done / 1e9, 1) if done else None
                    s["xfer_total_gb"] = round(total / 1e9, 1) if total else None
                    s["xfer_rate_mbs"] = round(rate, 1) if rate else None
                    if done and total:
                        s["xfer_pct"] = min(99, int(100 * done / total))
                elif apx:
                    s["phase"], s["phase_icon"] = "BOARD IN RECOVERY", "▶"
                    s["detail"] = "flash process active; board awaiting initrd boot"
                else:
                    s["phase"], s["phase_icon"] = "WORKING", "▶"
                    s["detail"] = "flash process active (boot chain / initrd stage)"
            else:
                if session_start is not None:
                    text = "\n".join(log_tail)
                    if any(m in text for m in OK_MARKERS) and not any(
                            m in text for m in BAD_MARKERS):
                        last_result = ("DONE — SUCCESS", "✔",
                                       "last flash completed successfully")
                        s["build_pct"] = s["xfer_pct"] = 100
                    else:
                        last_result = ("STOPPED — CHECK LOG", "✖",
                                       "flash process exited; see log tail")
                    session_start = None
                s["phase"], s["phase_icon"], s["detail"] = last_result
                s["elapsed_s"] = None
                s["xfer_rate_mbs"] = None
        time.sleep(POLL_S)

PAGE = """<!doctype html><html><head><meta charset="utf-8">
<title>Jetson Flash Monitor</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
 body{background:#111417;color:#e6e9ec;font:15px/1.5 system-ui,sans-serif;
      max-width:720px;margin:2rem auto;padding:0 1rem}
 h1{font-size:1.1rem;letter-spacing:.06em;color:#9aa3ad;text-transform:uppercase}
 .phase{font-size:1.6rem;font-weight:600;margin:.4rem 0}
 .phase.run{color:#7ab8ff}.phase.ok{color:#5ecb8b}.phase.bad{color:#ff8f7a}
 .detail,.meta{color:#9aa3ad;font-size:.9rem}
 .meterwrap{margin:1.1rem 0}
 .label{display:flex;justify-content:space-between;font-size:.85rem;color:#c4cad1}
 .meter{background:#22272c;border-radius:4px;height:14px;overflow:hidden;margin-top:4px}
 .fill{background:#7ab8ff;height:100%;width:0%;border-radius:4px;
       transition:width .8s ease}
 .fill.ok{background:#5ecb8b}
 pre{background:#181c20;border:1px solid #22272c;border-radius:6px;padding:.8rem;
     font-size:.75rem;overflow-x:auto;color:#aeb6bf;max-height:16rem}
 .row{display:flex;gap:1.4rem;flex-wrap:wrap;margin:.6rem 0}
 .kv b{color:#e6e9ec;font-weight:600}
</style></head><body>
<h1>Jetson Flash Monitor</h1>
<div class="phase" id="phase">&hellip;</div>
<div class="detail" id="detail"></div>
<div class="row meta">
 <span class="kv">board in recovery: <b id="apx"></b></span>
 <span class="kv">elapsed: <b id="elapsed"></b></span>
 <span class="kv">rate: <b id="rate"></b></span>
 <span class="kv">updated: <b id="updated"></b></span>
</div>
<div class="meterwrap"><div class="label"><span>1 &middot; Image build (laptop)</span>
 <span id="bval"></span></div>
 <div class="meter"><div class="fill" id="bfill"></div></div></div>
<div class="meterwrap"><div class="label"><span>2 &middot; Write to board (USB)</span>
 <span id="xval"></span></div>
 <div class="meter"><div class="fill" id="xfill"></div></div></div>
<div class="detail" id="logname"></div>
<pre id="log"></pre>
<script>
function fmt(s){if(s==null)return "—";
 const m=Math.floor(s/60);return m+"m "+(s%60)+"s";}
async function tick(){
 try{
  const r=await fetch('/status.json');const s=await r.json();
  const ph=document.getElementById('phase');
  ph.textContent=s.phase_icon+" "+s.phase;
  ph.className='phase '+(s.phase.startsWith('DONE')?'ok':
                         s.phase.startsWith('STOPPED')?'bad':'run');
  document.getElementById('detail').textContent=s.detail;
  document.getElementById('apx').textContent=s.board_in_recovery?'yes':'no';
  document.getElementById('elapsed').textContent=fmt(s.elapsed_s);
  document.getElementById('rate').textContent=
      s.xfer_rate_mbs!=null?s.xfer_rate_mbs+' MB/s':'—';
  document.getElementById('updated').textContent=s.updated;
  const b=document.getElementById('bfill'),x=document.getElementById('xfill');
  b.style.width=(s.build_pct??0)+'%';
  b.className='fill'+(s.build_pct===100?' ok':'');
  x.style.width=(s.xfer_pct??0)+'%';
  x.className='fill'+(s.xfer_pct===100?' ok':'');
  document.getElementById('bval').textContent=
      s.build_pct!=null?`${s.build_pct}%  (${s.build_done_gb??'?'} / ${s.build_total_gb??'?'} GB)`:'—';
  document.getElementById('xval').textContent=
      s.xfer_pct!=null?`${s.xfer_pct}%  (${s.xfer_done_gb??'?'} / ${s.xfer_total_gb??'?'} GB)`:'—';
  document.getElementById('logname').textContent=
      s.log_name?('log: '+s.log_name):'';
  document.getElementById('log').textContent=(s.log_tail||[]).join('\\n');
 }catch(e){document.getElementById('detail').textContent='monitor unreachable';}
}
tick();setInterval(tick,2000);
</script></body></html>"""

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/status.json":
            with state_lock:
                body = json.dumps(state).encode()
            ctype = "application/json"
        else:
            body = PAGE.encode()
            ctype = "text/html; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

def main():
    threading.Thread(target=sampler, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()

if __name__ == "__main__":
    main()
