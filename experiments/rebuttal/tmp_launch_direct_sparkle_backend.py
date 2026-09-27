#!/usr/bin/env python3
"""Wait for Direct Sparkle frontend, then post-hoc PD 4w x NUM_CORES=8."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

FRONT = Path("/home/sgli/work/NL2Chip_rebuttal_artifacts/2026-09-20/sparkle_direct_noarchon_fourds")
ISO = Path("/home/sgli/work/nl2chip_direct_sparkle_private_20260920/agent_state")
PROJECT = Path("/home/sgli/work/NL2Chip_openlux_repair_state_20260914")
BACKEND = Path("/home/sgli/work/NL2Chip_sparkle_416ec86_backend_eval_20260919")
PRIVATE = Path("/home/sgli/work/nl2chip_sparkle_416ec86_backend_eval_private_20260919")
OUT = Path("/home/sgli/work/NL2Chip_rebuttal_artifacts/2026-09-20/sparkle_direct_noarchon_backend_pd")
LAUNCH_SRC = PRIVATE / "launch_backend_pd.py"
LOCK = Path("/home/sgli/work/NL2Chip_rebuttal_artifacts/2026-09-20/pd.lock")
EXPECTED = {"verilogeval": 156, "rtllm": 50, "resbench": 56, "cvdp": 168}


def log(msg: str) -> None:
    print(f"[{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}] direct_pd {msg}", flush=True)


def unique_n(jsonl: Path) -> int:
    last = {}
    if not jsonl.exists():
        return 0
    for line in jsonl.read_text().splitlines():
        if line.strip():
            row = json.loads(line)
            pid = row.get("prob_id")
            if pid:
                last[pid] = row
    return len(last)


def frontend_done() -> bool:
    for ds, n in EXPECTED.items():
        p = FRONT / ds / "results.jsonl"
        if unique_n(p) < n:
            return False
    return True


def orfs_busy() -> bool:
    try:
        out = subprocess.check_output(["pgrep", "-af", "docker run --rm"], text=True)
    except subprocess.CalledProcessError:
        out = ""
    return "openroad" in out.lower() or "orfs" in out.lower()


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    while not frontend_done():
        parts = []
        for ds, n in EXPECTED.items():
            parts.append(f"{ds}={unique_n(FRONT / ds / 'results.jsonl')}/{n}")
        log("frontend " + " ".join(parts))
        time.sleep(60)
    log("frontend complete; waiting for ORFS/pd.lock")
    while True:
        if LOCK.exists() or orfs_busy():
            log("ORFS/lock busy")
            time.sleep(30)
            continue
        break
    LOCK.write_text(str(os.getpid()))
    try:
        env = os.environ.copy()
        env["PD_WORKERS"] = "4"
        env["NUM_CORES"] = "8"
        env["PD_OUT"] = str(OUT)
        env["PD_PROJECT"] = str(PROJECT)
        env["PD_ISO"] = str(ISO)
        env["PD_FRONT"] = str(FRONT)
        log(f"start PD via {LAUNCH_SRC}")
        # Reuse existing launcher if it accepts env; else record and skip if missing.
        if not LAUNCH_SRC.exists():
            log("missing launch_backend_pd.py")
            return 1
        return subprocess.call(
            ["/home/sgli/work/NL2Chip/.venv/bin/python", "-u", str(LAUNCH_SRC)],
            env=env,
        )
    finally:
        if LOCK.exists() and LOCK.read_text().strip() == str(os.getpid()):
            LOCK.unlink()


if __name__ == "__main__":
    raise SystemExit(main())
