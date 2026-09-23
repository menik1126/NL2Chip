#!/usr/bin/env python3
"""Post-hoc synth/P&R/DRC/LVS/GLS for Direct SV compile-fb sim_pass.

Reuses the frozen 2026-09-16 native-SV backend driver (no LLM).
Stages candidate.sv into a source-run the driver already understands.
Starts VE/RTLLM/ResBench immediately; waits for CVDP frontend 168 before PD.
Does not clobber Direct Sparkle / oneshot / PPA trees. Leaves CVDP gen running.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

FRONT = Path("/home/sgli/work/NL2Chip_rebuttal_artifacts/2026-09-23/direct_sv_compilefb_fourds")
OUT = Path("/home/sgli/work/NL2Chip_rebuttal_artifacts/2026-09-23/direct_sv_compilefb_backend_pd")
PROJECT = Path("/home/sgli/work/NL2Chip_openlux_repair_state_20260914")
PYTHON = Path("/home/sgli/work/NL2Chip/.venv/bin/python")
DRIVER = Path(
    "/home/sgli/work/NL2Chip_rebuttal_artifacts/2026-09-16/"
    "backend_posthoc_four_datasets/cvdp_backend_posthoc.py"
)
FRONT_PID = FRONT / "launcher.pid"
WORKERS = int(os.environ.get("PD_WORKERS", "4"))
DATASETS_NOW = ("verilogeval", "rtllm", "resbench")
CVDP_N = 168


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(msg: str) -> None:
    print(f"[{utc_now()}] {msg}", flush=True)


def last_wins(path: Path) -> dict:
    last = {}
    if not path.exists():
        return last
    for line in path.read_text().splitlines():
        if line.strip():
            row = json.loads(line)
            pid = row.get("prob_id")
            if pid:
                last[pid] = row
    return last


def unique_n(path: Path) -> int:
    return len(last_wins(path))


def stage_dataset(dataset: str) -> Path | None:
    src = FRONT / dataset
    jsonl = src / "results.jsonl"
    if not jsonl.exists():
        log(f"{dataset} no frontend jsonl")
        return None
    last = last_wins(jsonl)
    staged = OUT / "source" / dataset
    sv_dir = staged / "sv"
    sv_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    n_sv = 0
    for pid, row in last.items():
        if row.get("sim_status") != "sim_pass":
            continue
        cand = src / "tasks" / pid / "candidate.sv"
        if not cand.exists():
            log(f"skip {dataset}/{pid}: sim_pass but no candidate.sv")
            continue
        dest = sv_dir / f"{pid}.sv"
        shutil.copy2(cand, dest)
        n_sv += 1
        rows.append(row)
    (staged / "results.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    )
    log(f"{dataset} staged sim_pass with sv={n_sv}")
    return staged if n_sv else None


def cleanup_orfs(run_parent: Path) -> None:
    if not run_parent.exists():
        return
    n = 0
    for d in run_parent.rglob("orfs_objects"):
        if d.is_dir():
            shutil.rmtree(d, ignore_errors=True)
            n += 1
    for d in run_parent.rglob("orfs_logs"):
        if d.is_dir():
            shutil.rmtree(d, ignore_errors=True)
            n += 1
    log(f"cleaned {n} orfs_objects/logs trees under {run_parent}")


def run_pd(dataset: str, staged: Path, extra: list[str] | None = None) -> int:
    results = OUT / dataset
    results.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["NL2CHIP_PROJECT_ROOT"] = str(PROJECT)
    env["PYTHONPATH"] = str(PROJECT) + ":" + str(PROJECT / "agent") + ":" + env.get("PYTHONPATH", "")
    env["PATH"] = "/home/sgli/.local/bin:" + env.get("PATH", "")
    env["CVDP_HARNESS_PROFILE"] = "race-safe-v1"
    env["CVDP_DATASET_FILE"] = (
        "/home/sgli/work/benchmarks/cvdp-benchmark-dataset/"
        "cvdp_v1.1.0_nonagentic_code_generation_no_commercial.jsonl"
    )
    cmd = [
        str(PYTHON), "-u", str(DRIVER),
        "--dataset", dataset,
        "--source-run", str(staged),
        "--results-dir", str(results),
        "--workers", str(WORKERS),
        "--resume",
    ]
    if extra:
        cmd.extend(extra)
    log("exec " + " ".join(cmd))
    rc = subprocess.call(cmd, env=env, cwd=str(results))
    cleanup_orfs(results)
    log(f"{dataset} pd exit={rc}")
    return rc


def frontend_alive() -> bool:
    if not FRONT_PID.exists():
        return False
    try:
        pid = int(FRONT_PID.read_text().strip())
    except ValueError:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def wait_cvdp() -> None:
    jsonl = FRONT / "cvdp" / "results.jsonl"
    while True:
        n = unique_n(jsonl)
        alive = frontend_alive()
        log(f"cvdp frontend unique={n}/{CVDP_N} launcher_alive={alive}")
        if not alive:
            return
        if n >= CVDP_N and (FRONT / "launch.log").exists():
            tail = FRONT.joinpath("launch.log").read_text(errors="replace")[-800:]
            if "direct sv compile-fb finished" in tail:
                return
        time.sleep(60)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "protocol.json").write_text(json.dumps({
        "label": "direct_sv_compilefb_backend_pd",
        "frontend": str(FRONT),
        "driver": str(DRIVER),
        "workers": WORKERS,
        "orfs_num_cores": 8,
        "eligible": "sim_pass only",
        "created": utc_now(),
    }, indent=2) + "\n")
    log(f"start PD workers={WORKERS}")
    for ds in DATASETS_NOW:
        staged = stage_dataset(ds)
        if staged is None:
            continue
        rc = run_pd(ds, staged)
        if rc != 0:
            log(f"WARN {ds} pd rc={rc}, continue")
    wait_cvdp()
    staged = stage_dataset("cvdp")
    if staged is not None:
        run_pd(
            "cvdp",
            staged,
            extra=[
                "--canonical-results", str(staged / "results.jsonl"),
                "--artifact-root", str(FRONT / "cvdp" / "tasks"),
            ],
        )
    log("direct sv compile-fb PD finished")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
