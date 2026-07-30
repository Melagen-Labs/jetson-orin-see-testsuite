"""Offline self-test for shared/event_log.py (schema v1). Run: python shared/test_event_log.py

Dependency-free; exercises envelope construction, per-channel validation,
round-trip write/read, and rejection of malformed records. Does not touch the
DUT or any real logs.
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import event_log as el  # noqa: E402

META = {"beam_energy": "64MeV", "fluence_source": "cyclotron-A", "shield_config": "2mm-Al"}


def sample_records():
    """One valid record per channel."""
    recs = []
    r = el.envelope("R-014", "orin-nano-01", "compute", "checksum", "ok", meta=META)
    r.update(iter=50, epoch=0, step=50, hash="836d5c79e3cfefa8",
             golden="836d5c79e3cfefa8", mismatch=False, finite=True,
             max_abs_pos=1.0, anomaly=False)
    recs.append(r)

    r = el.envelope("R-014", "orin-nano-01", "memory", "mismatch", "anomaly", meta=META)
    r.update(test="moving_inversion", address="0x3f8a0010", pattern="0xAA",
             expected="0xAA", actual="0xAB", xor="0x01")
    recs.append(r)

    r = el.envelope("R-014", "orin-nano-01", "heartbeat", "beat", "ok", meta=META)
    r.update(seq=1287, uptime_s=642.5)
    recs.append(r)

    r = el.envelope("R-014", "orin-nano-01", "boot", "boot", "info", meta=META)
    r.update(boot_id="b1c2...", uptime_s=3.1, reboot_count=2)
    recs.append(r)

    r = el.envelope("R-014", "orin-nano-01", "power", "sample", "tripped", meta=META)
    r.update(current_mA=1180, tripped=True)
    recs.append(r)
    return recs


def main():
    recs = sample_records()

    # 1. every sample record validates clean
    for r in recs:
        errs = el.validate(r)
        assert errs == [], f"{r['channel']} record should be valid, got {errs}"

    # 2. round-trip through a JSONL file
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "events.jsonl")
        with open(path, "w", encoding="utf-8") as fp:
            for r in recs:
                el.emit(fp, r)
        back = list(el.read_events(path))
    assert len(back) == len(recs), f"round-trip count {len(back)} != {len(recs)}"
    assert back[0]["hash"] == "836d5c79e3cfefa8"
    assert back[4]["status"] == "tripped"

    # 3. malformed records are rejected
    bad_missing = {"schema_version": 1, "ts": "x", "run_id": "R", "jetson_id": "j",
                   "channel": "compute", "event": "checksum"}  # no status
    assert el.validate(bad_missing), "record missing status must fail"

    bad_channel = el.envelope("R", "j", "compute", "e", meta=META)
    bad_channel["channel"] = "gpu"  # not an allowed channel
    assert el.validate(bad_channel), "record with bad channel must fail"

    bad_power = el.envelope("R", "j", "power", "sample", "ok", meta=META)  # no current_mA
    assert el.validate(bad_power), "power record without current_mA must fail"

    # 4. envelope() rejects bad enums at construction time
    for bad in (("compute", "NOPE"), ("nope", "ok")):
        try:
            el.envelope("R", "j", bad[0], "e", bad[1], meta=META)
        except ValueError:
            pass
        else:
            raise AssertionError(f"envelope should reject {bad}")

    print("OK: all event_log schema-v1 self-tests passed "
          f"({len(recs)} channels, round-trip + rejection checks).")


if __name__ == "__main__":
    main()
