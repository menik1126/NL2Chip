#!/usr/bin/env python3
"""Post-hoc PD for Direct Sparkle sim_pass only. 4 workers, NUM_CORES=8.

Does not touch the live CVDP precompile job or frozen table trees.
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

PROJECT = Path("/home/sgli/work/NL2Chip_openlux_repair_state_20260914")
FRONT = Path("/home/sgli/work/NL2Chip_rebuttal_artifacts/2026-09-20/sparkle_direct_noarchon_fourds")
OUT = Path("/home/sgli/work/NL2Chip_rebuttal_artifacts/2026-09-20/sparkle_direct_noarchon_backend_pd")
PYTHONPATH_ROOT = PROJECT
WORKERS = int(os.environ.get("PD_WORKERS", "4"))
DATASETS = ("verilogeval", "rtllm", "resbench", "cvdp")

jsonl_lock = threading.Lock()
print_lock = threading.Lock()

sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "agent"))
os.chdir(PROJECT)

from agent.dataset import Dataset  # noqa: E402
from agent.evaluator import Evaluator  # noqa: E402


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(msg: str) -> None:
    with print_lock:
        print(f"[{utc_now()}] {msg}", flush=True)


def last_wins(path: Path) -> dict[str, dict[str, Any]]:
    last: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return last
    for line in path.read_text().splitlines():
        if line.strip():
            row = json.loads(line)
            pid = row.get("prob_id")
            if pid:
                last[pid] = row
    return last


def collect() -> list[tuple[str, str, Path]]:
    jobs = []
    for ds in DATASETS:
        last = last_wins(FRONT / ds / "results.jsonl")
        for pid, row in sorted(last.items()):
            if row.get("sim_status") != "sim_pass":
                continue
            lean = FRONT / ds / "tasks" / pid / "candidate.lean"
            if not lean.exists():
                log(f"skip {ds}/{pid}: sim_pass but no candidate.lean")
                continue
            jobs.append((ds, pid, lean))
        log(f"{ds} sim_pass with lean: {sum(1 for d,_,__ in jobs if d==ds)}")
    return jobs


def seed_lean(pid: str, src: Path) -> None:
    dest = PROJECT / "Generated" / f"{pid}.lean"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)


def patch_num_cores() -> None:
    ev = PROJECT / "agent" / "evaluator.py"
    text = ev.read_text()
    old = 'f"export CORE_UTILIZATION = 5\\n"'
    new = 'f"export NUM_CORES = 8\\n"\n            f"export CORE_UTILIZATION = 5\\n"'
    if "export NUM_CORES = 8" in text:
        log("NUM_CORES already in evaluator")
        return
    if old not in text:
        log("WARN no CORE_UTILIZATION splice point")
        return
    ev.write_text(text.replace(old, new))
    log("patched NUM_CORES=8 into openlux evaluator config.mk")


def compact(row: dict[str, Any], ds: str, pid: str) -> dict[str, Any]:
    keep = {
        "prob_id", "compile_pass", "sim_status", "synth_pass", "pnr_pass",
        "drc_pass", "lvs_pass", "drc_violations", "lvs_violations",
        "gds_generated", "area_um2", "cell_count", "power_uw", "wns_ns",
        "gls_synth_status", "gls_pnr_status", "failure_stage", "detail",
    }
    out = {k: row.get(k) for k in keep if k in row}
    out["dataset"] = ds
    out["prob_id"] = pid
    out["backend_ts"] = utc_now()
    detail = out.get("detail")
    if isinstance(detail, str) and len(detail) > 4000:
        out["detail"] = detail[:4000] + "\n...[truncated]"
    return out


def main() -> int:
    os.environ["PATH"] = (
        "/home/sgli/.local/bin:/home/sgli/.elan/toolchains/"
        "leanprover--lean4---v4.28.0-rc1/bin:" + os.environ.get("PATH", "")
    )
    os.environ["CVDP_HARNESS_PROFILE"] = "race-safe-v1"
    patch_num_cores()
    jobs = collect()
    log(f"PD jobs={len(jobs)} workers={min(WORKERS, max(len(jobs), 1))}")
    if not jobs:
        return 0
    OUT.mkdir(parents=True, exist_ok=True)
    run_root = OUT / "run_direct_pd"
    run_root.mkdir(parents=True, exist_ok=True)
    datasets = {ds: Dataset(ds, PROJECT) for ds in DATASETS}
    n_done = 0
    n_lock = threading.Lock()

    def run_one(ds: str, pid: str, lean: Path) -> None:
        nonlocal n_done
        seed_lean(pid, lean)
        run_dir = run_root / "eval" / ds / pid
        run_dir.mkdir(parents=True, exist_ok=True)
        log(f"{ds} {pid} start")
        ev = Evaluator(
            project_root=PROJECT,
            enable_synth=True,
            enable_pnr=True,
            enable_drc=True,
            enable_lvs=True,
            enable_gls=True,
            dataset=ds,
            dataset_obj=datasets[ds],
        )
        try:
            info = datasets[ds].load_problem(pid)
            row = ev.evaluate(pid, run_dir, problem_info=info)
        except TypeError:
            row = ev.evaluate(pid, run_dir)
        except Exception as exc:
            row = {
                "synth_pass": False, "pnr_pass": False, "drc_pass": False,
                "lvs_pass": False, "failure_stage": "infrastructure",
                "detail": f"{type(exc).__name__}: {exc}\n{traceback.format_exc()[-1500:]}",
            }
        compact_row = compact(row, ds, pid)
        jsonl = run_root / f"{ds}.jsonl"
        with jsonl_lock:
            with jsonl.open("a") as fh:
                fh.write(json.dumps(compact_row, ensure_ascii=False) + "\n")
        with n_lock:
            n_done += 1
            k = n_done
        log(
            f"{ds} {pid} synth={compact_row.get('synth_pass')} "
            f"pnr={compact_row.get('pnr_pass')} drc={compact_row.get('drc_pass')} "
            f"lvs={compact_row.get('lvs_pass')} "
            f"glsS={compact_row.get('gls_synth_status')} "
            f"glsP={compact_row.get('gls_pnr_status')} ({k}/{len(jobs)})"
        )

    workers = min(WORKERS, len(jobs))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = [pool.submit(run_one, ds, pid, lean) for ds, pid, lean in jobs]
        for fut in as_completed(futs):
            fut.result()
    log("direct sparkle PD finished")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
