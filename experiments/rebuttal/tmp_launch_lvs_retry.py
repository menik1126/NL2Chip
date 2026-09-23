#!/usr/bin/env python3
"""Retry LVS only for unrepaired Sparkle PD rows with P&R pass and LVS not True.

Keeps existing GLS numbers. 4 workers, NUM_CORES=8. Does not overwrite frozen trees.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

OVERLAY = Path("/home/sgli/work/NL2Chip_sparkle_416ec86_20260919")
BACKEND = Path("/home/sgli/work/NL2Chip_sparkle_416ec86_backend_eval_20260919")
PRIVATE = Path("/home/sgli/work/nl2chip_sparkle_416ec86_backend_eval_private_20260919")
ISO = Path("/home/sgli/work/nl2chip_sparkle_416ec86_private_20260919/agent_state")
NL2CHIP = Path("/home/sgli/work/NL2Chip")
OUT = Path(
    "/home/sgli/work/NL2Chip_rebuttal_artifacts/2026-09-19/"
    "sparkle_unrepaired_416ec86_backend_pd"
)
DISK_MIN_GB = float(os.environ.get("PD_DISK_MIN_GB", "8"))
WORKERS = int(os.environ.get("PD_WORKERS", "4"))
DATASETS = tuple(
    x.strip()
    for x in os.environ.get("PD_DATASETS", "verilogeval,rtllm,resbench,cvdp").split(",")
    if x.strip()
)
jsonl_lock = threading.Lock()
print_lock = threading.Lock()
done_lock = threading.Lock()
abort = threading.Event()

sys.path.insert(0, str(OVERLAY))
sys.path.insert(0, str(OVERLAY / "agent"))
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "agent"))

from agent.dataset import Dataset  # noqa: E402
from agent.evaluator import Evaluator  # noqa: E402


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(msg: str) -> None:
    with print_lock:
        print(f"[{utc_now()}] {msg}", flush=True)


def free_gb(path: Path) -> float:
    st = os.statvfs(path)
    return st.f_bavail * st.f_frsize / (1024**3)


def lastwins(path: Path) -> dict[str, dict[str, Any]]:
    last: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return last
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        pid = row.get("prob_id")
        if pid:
            last[pid] = row
    return last


def seed_lean(pid: str) -> None:
    src = ISO / pid / "Generated" / f"{pid}.lean"
    dest = BACKEND / "Generated" / f"{pid}.lean"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)


def compact_result(row: dict[str, Any]) -> dict[str, Any]:
    keep = {
        "prob_id", "dataset", "compile_pass", "sim_status", "sim_mismatches",
        "synth_attempted", "synth_pass", "pnr_pass", "drc_pass", "lvs_pass",
        "drc_violations", "lvs_violations", "lvs_error", "gds_generated",
        "area_um2", "cell_count", "power_w", "wns", "tns",
        "gls_synth_status", "gls_pnr_status",
        "failure_stage", "failure_category", "failure_family",
        "elapsed_seconds", "eval_elapsed_seconds", "detail",
        "ppa_status", "ppa_coverage",
    }
    out = {k: row.get(k) for k in keep if k in row}
    out["backend_ts"] = utc_now()
    detail = out.get("detail")
    if isinstance(detail, str) and len(detail) > 4000:
        out["detail"] = detail[:4000] + "\n...[truncated]"
    err = out.get("lvs_error")
    if isinstance(err, str) and len(err) > 800:
        out["lvs_error"] = err[:800] + "...[truncated]"
    return out


def environment() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(BACKEND) + ":" + str(BACKEND / "agent")
    env["PATH"] = (
        "/home/sgli/.local/bin:/home/sgli/.elan/toolchains/"
        "leanprover--lean4---v4.28.0-rc1/bin:" + env.get("PATH", "")
    )
    env["CVDP_HARNESS_PROFILE"] = "race-safe-v1"
    env.pop("NL2CHIP_ISOLATION_ROOT", None)
    return env


def main() -> int:
    os.environ.update({k: v for k, v in environment().items() if k in (
        "PYTHONPATH", "PATH", "CVDP_HARNESS_PROFILE",
    )})
    os.chdir(BACKEND)
    log(f"LVS retry disk {free_gb(Path('/home/sgli')):.1f}G workers={WORKERS}")
    (BACKEND / "Generated").mkdir(parents=True, exist_ok=True)
    existing = sorted(OUT.glob("run_*"))
    if not existing:
        raise SystemExit("no existing PD run to resume")
    run_root = existing[-1]
    log(f"resume {run_root}")
    datasets = {ds: Dataset(ds, BACKEND) for ds in DATASETS}
    jobs: list[tuple[str, str, dict[str, Any]]] = []
    for ds in DATASETS:
        jsonl = run_root / f"{ds}.jsonl"
        last = lastwins(jsonl)
        n = 0
        for pid, row in sorted(last.items()):
            if row.get("pnr_pass") is not True:
                continue
            if row.get("lvs_pass") is True:
                continue
            jobs.append((ds, pid, row))
            n += 1
        log(f"{ds}: {len(last)} rows, {n} LVS retry")
    log(f"LVS workers={WORKERS} remaining={len(jobs)}")
    session_n = 0

    def run_one(ds: str, pid: str, prev: dict[str, Any]) -> dict[str, Any]:
        nonlocal session_n
        if abort.is_set():
            return {"prob_id": pid, "dataset": ds, "skipped": True}
        if free_gb(Path("/home/sgli")) < DISK_MIN_GB:
            abort.set()
            log(f"ABORT disk {free_gb(Path('/home/sgli')):.1f}G")
            return {"prob_id": pid, "dataset": ds, "skipped": True}
        seed_lean(pid)
        run_dir = run_root / "eval" / ds / pid
        run_dir.mkdir(parents=True, exist_ok=True)
        log(f"{ds} {pid} LVS start disk={free_gb(Path('/home/sgli')):.1f}G")
        ev = Evaluator(
            project_root=BACKEND,
            enable_synth=True,
            enable_pnr=True,
            enable_drc=True,
            enable_lvs=True,
            enable_gls=False,
            dataset=ds,
            dataset_obj=datasets[ds],
        )
        try:
            info = datasets[ds].load_problem(pid)
            row = ev.evaluate(pid, run_dir, problem_info=info)
        except Exception as exc:
            row = {
                "prob_id": pid,
                "synth_attempted": True,
                "synth_pass": prev.get("synth_pass"),
                "pnr_pass": prev.get("pnr_pass"),
                "drc_pass": prev.get("drc_pass"),
                "lvs_pass": False,
                "failure_stage": "infrastructure",
                "detail": f"{type(exc).__name__}: {exc}\n{traceback.format_exc()[-2000:]}",
            }
        merged = dict(prev)
        merged.update({k: row.get(k) for k in (
            "synth_pass", "pnr_pass", "drc_pass", "drc_violations",
            "lvs_pass", "lvs_error", "lvs_violations", "gds_generated",
            "area_um2", "cell_count", "failure_stage", "detail",
        ) if k in row})
        # keep prior GLS
        for k in ("gls_synth_status", "gls_pnr_status"):
            if k in prev:
                merged[k] = prev[k]
        merged["dataset"] = ds
        merged["prob_id"] = pid
        compact = compact_result(merged)
        jsonl = run_root / f"{ds}.jsonl"
        with jsonl_lock:
            with jsonl.open("a") as fh:
                fh.write(json.dumps(compact, ensure_ascii=False) + "\n")
        with done_lock:
            session_n += 1
            n = session_n
        log(
            f"{ds} {pid} synth={compact.get('synth_pass')} "
            f"pnr={compact.get('pnr_pass')} drc={compact.get('drc_pass')} "
            f"lvs={compact.get('lvs_pass')} "
            f"({n} this session / {len(jobs)} remaining-at-start)"
        )
        return compact

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futs = [pool.submit(run_one, ds, pid, prev) for ds, pid, prev in jobs]
        for fut in as_completed(futs):
            fut.result()
    log("LVS retry finished")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
