#!/usr/bin/env python3
"""Finish precompile-semantic PD: remaining VerilogEval + CVDP compilefb sim_pass.

RTLLM/ResBench already completed. Skips VE rows that already synth+pnr passed.
Uses the precompile Sparkle tree (not the broken lake backend_eval clone).
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

PROJECT = Path("/home/sgli/work/NL2Chip_sparkle_precompile_semantic_20260920")
ISO = Path("/home/sgli/work/nl2chip_precompile_semantic_private_20260920/agent_state")
FOURDS = Path("/home/sgli/work/NL2Chip_rebuttal_artifacts/2026-09-20/sparkle_precompile_semantic_fourds")
OLD_PD = Path(
    "/home/sgli/work/NL2Chip_rebuttal_artifacts/2026-09-20/"
    "sparkle_precompile_semantic_backend_pd/run_20260920_145128"
)
OUT = Path(
    "/home/sgli/work/NL2Chip_rebuttal_artifacts/2026-09-21/"
    "sparkle_precompile_semantic_backend_pd"
)
WORKERS = int(os.environ.get("PD_WORKERS", "2"))
DISK_MIN_GB = float(os.environ.get("PD_DISK_MIN_GB", "8"))

FRONT = {
    "verilogeval": FOURDS / "verilogeval_20260919_215633/cktarchon_run_20260920_055644/results.jsonl",
    "rtllm": FOURDS / "rtllm_20260919_215633/cktarchon_run_20260920_112728/results.jsonl",
    "resbench": FOURDS / "resbench_20260919_215633/cktarchon_run_20260920_133030/results.jsonl",
    "cvdp": FOURDS / "cvdp_20260920_compilefb/cktarchon_run_20260921_003512/results.jsonl",
}

jsonl_lock = threading.Lock()
print_lock = threading.Lock()
abort = threading.Event()

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


def free_gb(path: Path) -> float:
    st = os.statvfs(path)
    return st.f_bavail * st.f_frsize / (1024**3)


def patch_num_cores() -> None:
    ev = PROJECT / "agent" / "evaluator.py"
    text = ev.read_text()
    if "export NUM_CORES = 8" in text:
        log("NUM_CORES already in evaluator")
        return
    old = 'f"export CORE_UTILIZATION = 5\\n"'
    new = 'f"export NUM_CORES = 8\\n"\n            f"export CORE_UTILIZATION = 5\\n"'
    if old not in text:
        log("WARN no CORE_UTILIZATION splice")
        return
    ev.write_text(text.replace(old, new))
    log("patched NUM_CORES=8")


def find_lean(pid: str) -> Path | None:
    for p in (
        ISO / pid / "Generated" / f"{pid}.lean",
        PROJECT / "Generated" / f"{pid}.lean",
    ):
        if p.exists() and p.stat().st_size > 0:
            return p
    return None


def already_good(pid: str, ds: str) -> bool:
    old = last_wins(OLD_PD / f"{ds}.jsonl").get(pid)
    if not old:
        return False
    return old.get("synth_pass") is True and old.get("pnr_pass") is True


def collect() -> list[tuple[str, str, Path]]:
    jobs: list[tuple[str, str, Path]] = []
    # RTLLM/ResBench already finished; only VE remainder + CVDP.
    for ds in ("verilogeval", "cvdp"):
        last = last_wins(FRONT[ds])
        n_sim = 0
        n_skip_done = 0
        n_skip_lean = 0
        for pid, row in sorted(last.items()):
            if row.get("sim_status") != "sim_pass":
                continue
            n_sim += 1
            if already_good(pid, ds):
                n_skip_done += 1
                continue
            lean = find_lean(pid)
            if lean is None:
                n_skip_lean += 1
                log(f"skip {ds}/{pid}: sim_pass but lean missing")
                continue
            jobs.append((ds, pid, lean))
        log(f"{ds} sim_pass={n_sim} skip_done={n_skip_done} skip_lean={n_skip_lean} todo={sum(1 for d,_,__ in jobs if d==ds)}")
    return jobs


def seed_lean(pid: str, src: Path) -> None:
    dest = PROJECT / "Generated" / f"{pid}.lean"
    dest.parent.mkdir(parents=True, exist_ok=True)
    src_r = src.resolve()
    dest_r = dest.resolve() if dest.exists() else dest
    if dest.exists() and src_r == dest_r:
        return
    if dest.is_symlink() or dest.exists():
        dest.unlink()
    shutil.copy2(src, dest)


def compact(row: dict[str, Any], ds: str, pid: str) -> dict[str, Any]:
    keep = {
        "prob_id", "compile_pass", "sim_status", "synth_pass", "pnr_pass",
        "drc_pass", "lvs_pass", "drc_violations", "lvs_violations",
        "gds_generated", "area_um2", "cell_count", "power_uw", "wns_ns",
        "gls_synth_status", "gls_pnr_status", "failure_stage", "detail",
        "synth_attempted",
    }
    out = {k: row.get(k) for k in keep if k in row}
    out["dataset"] = ds
    out["prob_id"] = pid
    out["synth_attempted"] = True
    out["backend_ts"] = utc_now()
    detail = out.get("detail")
    if isinstance(detail, str) and len(detail) > 4000:
        out["detail"] = detail[:4000] + "\n...[truncated]"
    return out


def cleanup(run_dir: Path) -> None:
    for name in ("orfs_results", "orfs_logs"):
        for p in run_dir.rglob(name):
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
    for p in run_dir.rglob("*"):
        if p.is_file() and p.suffix.lower() in {".gds", ".oas", ".odb", ".def"}:
            try:
                p.unlink()
            except OSError:
                pass


def main() -> int:
    os.environ["PATH"] = (
        "/home/sgli/.local/bin:/home/sgli/.elan/toolchains/"
        "leanprover--lean4---v4.28.0-rc1/bin:" + os.environ.get("PATH", "")
    )
    os.environ["CVDP_HARNESS_PROFILE"] = "race-safe-v1"
    os.environ.pop("NL2CHIP_ISOLATION_ROOT", None)
    patch_num_cores()
    jobs = collect()
    log(f"disk {free_gb(Path('/home/sgli')):.1f}G workers={WORKERS} jobs={len(jobs)}")
    if not jobs:
        return 0
    OUT.mkdir(parents=True, exist_ok=True)
    run_root = OUT / "run_remaining"
    run_root.mkdir(parents=True, exist_ok=True)
    datasets = {ds: Dataset(ds, PROJECT) for ds in ("verilogeval", "cvdp")}
    n_done = 0
    n_lock = threading.Lock()

    def run_one(ds: str, pid: str, lean: Path) -> None:
        nonlocal n_done
        if abort.is_set():
            return
        if free_gb(Path("/home/sgli")) < DISK_MIN_GB:
            abort.set()
            log(f"ABORT disk {free_gb(Path('/home/sgli')):.1f}G")
            return
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
        cleanup(run_dir)
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

    with ThreadPoolExecutor(max_workers=min(WORKERS, len(jobs))) as pool:
        futs = [pool.submit(run_one, ds, pid, lean) for ds, pid, lean in jobs]
        for fut in as_completed(futs):
            fut.result()
            if abort.is_set():
                break
    log("precompile remaining PD finished" if not abort.is_set() else "precompile remaining PD aborted")
    return 2 if abort.is_set() else 0


if __name__ == "__main__":
    raise SystemExit(main())
