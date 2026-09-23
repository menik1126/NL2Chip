#!/usr/bin/env python3
"""Wait for a four-ds frontend, then post-hoc PD with 4 workers and NUM_CORES=8.

Uses repaired Sparkle as the Lake tree and the GLS-renamed evaluator from the
416ec86 backend eval copy. Waits for the live unrepaired GLS retry to leave
ORFS before starting. Does not overwrite frozen PD trees.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

NAME = os.environ["BASELINE_NAME"]
FRONT = Path(os.environ["FRONT_OUT"])
ISO = Path(os.environ["ISO_STATE"])
PROJECT = Path(os.environ["SPARKLE_PROJECT"])
BACKEND = Path(os.environ["BACKEND_TREE"])
PRIVATE = Path(os.environ["BACKEND_PRIVATE"])
OUT = Path(os.environ["PD_OUT"])
PATCHED_EVAL = Path(
    "/home/sgli/work/NL2Chip_sparkle_416ec86_backend_eval_20260919/agent/evaluator.py"
)
LAUNCH_SRC = Path(
    "/home/sgli/work/nl2chip_sparkle_416ec86_backend_eval_private_20260919/launch_backend_pd.py"
)
EXPECTED = {
    "verilogeval": 156,
    "rtllm": 50,
    "resbench": 56,
    "cvdp": 168,
}
GLS_PID = 218165
LOCK = Path("/home/sgli/work/NL2Chip_rebuttal_artifacts/2026-09-20/pd.lock")


def log(msg: str) -> None:
    print(f"[{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}] {NAME} {msg}", flush=True)


def unique_n(jsonl: Path) -> int:
    last = {}
    if not jsonl.exists():
        return 0
    for line in jsonl.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        pid = row.get("prob_id") or row.get("problem_id")
        if pid:
            last[pid] = row
    return len(last)


def latest_jsonl(ds: str) -> Path | None:
    hits = sorted(FRONT.glob(f"{ds}_*/cktarchon_run_*/results.jsonl"))
    return hits[-1] if hits else None


def frontend_done() -> bool:
    for ds, n in EXPECTED.items():
        p = latest_jsonl(ds)
        if p is None:
            return False
        if unique_n(p) < n:
            return False
    return True


def orfs_busy() -> bool:
    if Path(f"/proc/{GLS_PID}").exists():
        return True
    try:
        out = subprocess.check_output(["pgrep", "-af", "launch_backend_pd.py"], text=True)
    except subprocess.CalledProcessError:
        out = ""
    for line in out.splitlines():
        if "pgrep" in line:
            continue
        if str(PRIVATE) in line or "launch_backend_pd.py" in line:
            # another PD including this one
            if str(os.getpid()) in line:
                continue
            return True
    try:
        n = subprocess.check_output(["pgrep", "-c", "-f", "openroad/orfs"], text=True).strip()
        return int(n or "0") > 0
    except subprocess.CalledProcessError:
        return False


def wait_frontend() -> None:
    log(f"waiting frontend {FRONT}")
    while not frontend_done():
        bits = []
        for ds, n in EXPECTED.items():
            p = latest_jsonl(ds)
            bits.append(f"{ds}={unique_n(p) if p else 0}/{n}")
        log("frontend " + " ".join(bits))
        time.sleep(60)


def wait_orfs() -> None:
    log("waiting for existing ORFS/PD to finish")
    while orfs_busy():
        log("ORFS/PD still busy")
        time.sleep(60)


def take_lock() -> None:
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    while True:
        try:
            fd = os.open(str(LOCK), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            os.write(fd, f"{os.getpid()} {NAME}\n".encode())
            os.close(fd)
            return
        except FileExistsError:
            try:
                owner = LOCK.read_text().strip()
            except OSError:
                owner = "?"
            log(f"pd.lock held by {owner}")
            time.sleep(30)


def drop_lock() -> None:
    try:
        LOCK.unlink()
    except FileNotFoundError:
        pass


def prepare_backend() -> None:
    BACKEND.mkdir(parents=True, exist_ok=True)
    PRIVATE.mkdir(parents=True, exist_ok=True)
    if not (BACKEND / ".lake").exists():
        log(f"rsync {PROJECT} -> {BACKEND}")
        subprocess.check_call([
            "rsync", "-a",
            "--exclude", "Generated/",
            "--exclude", "results/",
            "--exclude", ".git/",
            "--exclude", "cktarchon_work/",
            str(PROJECT) + "/",
            str(BACKEND) + "/",
        ])
    shutil.copy2(PATCHED_EVAL, BACKEND / "agent" / "evaluator.py")
    ev = BACKEND / "agent" / "evaluator.py"
    t = ev.read_text()
    old = '''            (synth_dir / "synth_stdout.txt").write_text(
                synth_result.get("stdout", "")
            )'''
    new = '''            stdout = synth_result.get("stdout", "") or ""
            if isinstance(stdout, (bytes, bytearray)):
                stdout = stdout.decode("utf-8", errors="replace")
            (synth_dir / "synth_stdout.txt").write_text(stdout)'''
    if old in t:
        t = t.replace(old, new)
        ev.write_text(t)
        log("patched synth_stdout bytes")
    (BACKEND / "Generated").mkdir(exist_ok=True)


def write_pd_launcher() -> Path:
    text = LAUNCH_SRC.read_text()
    repls = {
        'OVERLAY = Path("/home/sgli/work/NL2Chip_sparkle_416ec86_20260919")':
            f'OVERLAY = Path("{PROJECT}")',
        'BACKEND = Path("/home/sgli/work/NL2Chip_sparkle_416ec86_backend_eval_20260919")':
            f'BACKEND = Path("{BACKEND}")',
        'PRIVATE = Path("/home/sgli/work/nl2chip_sparkle_416ec86_backend_eval_private_20260919")':
            f'PRIVATE = Path("{PRIVATE}")',
        'ISO = Path("/home/sgli/work/nl2chip_sparkle_416ec86_private_20260919/agent_state")':
            f'ISO = Path("{ISO}")',
        'FOURDS = Path(\n    "/home/sgli/work/NL2Chip_rebuttal_artifacts/2026-09-19/"\n    "sparkle_unrepaired_416ec86_fourds"\n)':
            f'FOURDS = Path("{FRONT}")',
        'OUT = Path(\n    "/home/sgli/work/NL2Chip_rebuttal_artifacts/2026-09-19/"\n    "sparkle_unrepaired_416ec86_backend_pd"\n)':
            f'OUT = Path("{OUT}")',
    }
    for old, new in repls.items():
        if old not in text:
            raise SystemExit(f"pd launcher missing block: {old[:80]}")
        text = text.replace(old, new, 1)
    dest = PRIVATE / "launch_backend_pd.py"
    dest.write_text(text)
    return dest


def main() -> int:
    wait_frontend()
    wait_orfs()
    take_lock()
    try:
        wait_orfs()
        prepare_backend()
        launcher = write_pd_launcher()
        env = os.environ.copy()
        env["PD_WORKERS"] = env.get("PD_WORKERS", "4")
        env["PD_DATASETS"] = "verilogeval,rtllm,resbench,cvdp"
        env["PD_FRESH"] = env.get("PD_FRESH", "1")
        log(f"start PD {launcher}")
        return subprocess.call(
            ["/home/sgli/work/NL2Chip/.venv/bin/python", "-u", str(launcher)],
            cwd=str(PRIVATE),
            env=env,
        )
    finally:
        drop_lock()


if __name__ == "__main__":
    raise SystemExit(main())
