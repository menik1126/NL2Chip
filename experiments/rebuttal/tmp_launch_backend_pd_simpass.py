#!/usr/bin/env python3
"""Post-hoc synth/P&R/DRC/LVS on unrepaired 416ec86 sim_pass rows.

Uses a dedicated Lake/Generated tree so live CVDP Codex bind-mounts on the
overlay Generated/ are not clobbered. Does not stop CVDP or PPA jobs.
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
FOURDS = Path(
    "/home/sgli/work/NL2Chip_rebuttal_artifacts/2026-09-19/"
    "sparkle_unrepaired_416ec86_fourds"
)
NL2CHIP = Path("/home/sgli/work/NL2Chip")
PYTHON = Path("/home/sgli/work/NL2Chip/.venv/bin/python")
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

# insert(0) last wins: overlay first, backend evaluator must shadow it.
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


def rsync_backend() -> None:
    BACKEND.mkdir(parents=True, exist_ok=True)
    PRIVATE.mkdir(parents=True, exist_ok=True)
    cmd = [
        "rsync", "-a",
        "--exclude", "Generated/",
        "--exclude", "results/",
        "--exclude", ".git/",
        "--exclude", "bak_*/",
        "--exclude", "cktarchon_work/",
        "--exclude", "a.out",
        str(OVERLAY) + "/",
        str(BACKEND) + "/",
    ]
    import subprocess
    subprocess.check_call(cmd)
    gen = BACKEND / "Generated"
    if gen.exists() or gen.is_symlink():
        if gen.is_dir() and not gen.is_symlink():
            pass
        else:
            gen.unlink()
    gen.mkdir(parents=True, exist_ok=True)
    for src, dest in (
        (NL2CHIP / "siliconcrew", BACKEND / "siliconcrew"),
        (NL2CHIP / "c_src", BACKEND / "c_src"),
        (
            Path.home() / "work" / "benchmarks" / "cvdp-benchmark-dataset",
            BACKEND / "cvdp-benchmark-dataset",
        ),
        (NL2CHIP / "verilog-eval", BACKEND / "verilog-eval"),
        (NL2CHIP / "RTLLM", BACKEND / "RTLLM"),
        (NL2CHIP / "ResBench", BACKEND / "ResBench"),
    ):
        if dest.exists() or dest.is_symlink():
            continue
        if src.exists():
            dest.symlink_to(src)
    overlay_root = OVERLAY / "Generated.lean"
    if overlay_root.exists():
        shutil.copy2(overlay_root, BACKEND / "Generated.lean")


def collect_sim_pass() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for ds in DATASETS:
        last: dict[str, dict[str, Any]] = {}
        for j in sorted(FOURDS.glob(f"{ds}_*/cktarchon_run_*/results.jsonl")):
            for line in j.read_text().splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                pid = row.get("prob_id") or row.get("problem_id")
                if pid:
                    last[pid] = row
        ids = []
        for pid, row in last.items():
            st = row.get("sim_status")
            if st not in ("sim_pass", "pass", "passed") and row.get("sim_pass") is not True:
                continue
            lean = ISO / pid / "Generated" / f"{pid}.lean"
            if not lean.exists():
                log(f"skip {ds}/{pid}: sim_pass but lean missing")
                continue
            ids.append(pid)
        ids.sort()
        out[ds] = ids
        (PRIVATE / f"{ds}_sim_pass.txt").write_text("\n".join(ids) + ("\n" if ids else ""))
        log(f"{ds} sim_pass with lean: {len(ids)}")
    return out


def load_done(path: Path) -> set[str]:
    """Last-wins. Retry infrastructure crashes; keep real PD rows."""
    last: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return set()
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        pid = row.get("prob_id")
        if pid:
            last[pid] = row
    done: set[str] = set()
    for pid, row in last.items():
        if row.get("failure_stage") == "infrastructure":
            continue
        if row.get("synth_attempted"):
            done.add(pid)
    return done


def seed_lean(pid: str) -> None:
    src = ISO / pid / "Generated" / f"{pid}.lean"
    dest = BACKEND / "Generated" / f"{pid}.lean"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)


def cleanup_run_dir(run_dir: Path) -> None:
    if not run_dir.exists():
        return
    for name in (
        "orfs_results",
        "orfs_logs",
        "objects",
        "logs",
        "results",
        "gds",
        "flow",
    ):
        for p in run_dir.rglob(name):
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
    # keep small reports; drop fat netlists after metrics are in jsonl
    for p in run_dir.rglob("*"):
        if p.is_file() and p.suffix.lower() in {".gds", ".oas", ".odb", ".def", ".lef"}:
            try:
                p.unlink()
            except OSError:
                pass


def compact_result(row: dict[str, Any]) -> dict[str, Any]:
    keep = {
        "prob_id", "dataset", "compile_pass", "sim_status", "sim_mismatches",
        "synth_attempted", "synth_pass", "pnr_pass", "drc_pass", "lvs_pass",
        "drc_violations", "lvs_violations", "gds_generated",
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
    return out


def environment() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(OVERLAY) + ":" + str(OVERLAY / "agent")
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
    os.chdir(BACKEND if BACKEND.exists() else OVERLAY)
    log(f"disk free {free_gb(Path('/home/sgli')):.1f}G workers={WORKERS}")
    if not (BACKEND / ".lake").exists() or os.environ.get("PD_RSYNC") == "1":
        rsync_backend()
    else:
        log(f"reuse backend tree {BACKEND}")
        (BACKEND / "Generated").mkdir(parents=True, exist_ok=True)
    os.chdir(BACKEND)
    wanted = collect_sim_pass()
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_root = OUT / f"run_{stamp}"
    # resume into newest existing run if present
    existing = sorted(OUT.glob("run_*"))
    if existing and os.environ.get("PD_FRESH") != "1":
        run_root = existing[-1]
        log(f"resume {run_root}")
    else:
        run_root.mkdir(parents=True, exist_ok=True)
        log(f"fresh {run_root}")

    datasets: dict[str, Dataset] = {ds: Dataset(ds, BACKEND) for ds in DATASETS}
    jobs: list[tuple[str, str]] = []
    for ds in DATASETS:
        ids = wanted[ds]
        jsonl = run_root / f"{ds}.jsonl"
        done = load_done(jsonl)
        remaining = [i for i in ids if i not in done]
        log(f"{ds}: {len(ids)} sim_pass, {len(done)} already PD, {len(remaining)} to run")
        jobs.extend((ds, pid) for pid in remaining)
    total = sum(len(v) for v in wanted.values())
    session_n = 0
    log(f"PD workers={WORKERS} remaining={len(jobs)} listed={total}")

    def run_one(ds: str, pid: str) -> dict[str, Any]:
        nonlocal session_n
        if abort.is_set():
            return {"prob_id": pid, "dataset": ds, "skipped": True}
        if free_gb(Path("/home/sgli")) < DISK_MIN_GB:
            abort.set()
            log(f"ABORT disk {free_gb(Path('/home/sgli')):.1f}G < {DISK_MIN_GB}G")
            return {"prob_id": pid, "dataset": ds, "skipped": True}
        seed_lean(pid)
        run_dir = run_root / "eval" / ds / pid
        run_dir.mkdir(parents=True, exist_ok=True)
        log(f"{ds} {pid} start disk={free_gb(Path('/home/sgli')):.1f}G")
        ev = Evaluator(
            project_root=BACKEND,
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
        except Exception as exc:
            row = {
                "prob_id": pid,
                "synth_attempted": True,
                "synth_pass": False,
                "pnr_pass": False,
                "drc_pass": False,
                "lvs_pass": False,
                "failure_stage": "infrastructure",
                "detail": f"{type(exc).__name__}: {exc}\n{traceback.format_exc()[-2000:]}",
            }
        row["dataset"] = ds
        row["prob_id"] = pid
        compact = compact_result(row)
        jsonl = run_root / f"{ds}.jsonl"
        with jsonl_lock:
            with jsonl.open("a") as fh:
                fh.write(json.dumps(compact, ensure_ascii=False) + "\n")
        cleanup_run_dir(run_dir)
        with done_lock:
            session_n += 1
            n = session_n
        log(
            f"{ds} {pid} synth={compact.get('synth_pass')} "
            f"pnr={compact.get('pnr_pass')} drc={compact.get('drc_pass')} "
            f"lvs={compact.get('lvs_pass')} "
            f"glsS={compact.get('gls_synth_status')} glsP={compact.get('gls_pnr_status')} "
            f"({n} this session / {len(jobs)} remaining-at-start / {total} listed)"
        )
        return compact

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futs = [pool.submit(run_one, ds, pid) for ds, pid in jobs]
        for fut in as_completed(futs):
            fut.result()
            if abort.is_set():
                break
    if abort.is_set():
        return 2
    log("backend PD finished listed sim_pass set")
    return 0


if __name__ == "__main__":
    sys.exit(main())
