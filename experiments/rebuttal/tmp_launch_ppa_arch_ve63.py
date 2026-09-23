#!/usr/bin/env python3
"""PPA (a) K=5 and architecture exploration (b) on current Sparkle VE-63 seeds.

Seeds sim-passing Lean from the 2026-09-19 semantic four-ds VerilogEval run.
Does not overwrite frozen table trees or the unrepaired 416ec86 job.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

PROJECT = Path("/home/sgli/work/NL2Chip_openlux_repair_state_20260914")
PRIVATE = Path("/home/sgli/work/nl2chip_ppa_ve63_private_20260919")
SEED_ISO = Path("/home/sgli/work/nl2chip_chatgpt_socks_private_20260918/agent_state")
SEM_JSONL = Path(
    "/home/sgli/work/NL2Chip_rebuttal_artifacts/2026-09-19/"
    "sparkle_lean_semantic_chatgpt_socks_fourds/verilogeval_20260918_171939/"
    "cktarchon_run_20260919_021704/results.jsonl"
)
PYTHON = Path("/home/sgli/work/NL2Chip/.venv/bin/python")
OUT = Path("/home/sgli/work/NL2Chip_rebuttal_artifacts/2026-09-19/sparkle_ppa_arch_ve63")
ARCHON_SRC = Path("/home/sgli/work/archon-official/src")
WRAPPER = PRIVATE / "codex_chatgpt_socks.py"
DUMMY_KEY = Path("/home/sgli/work/nl2chip_chatgpt_socks_private_20260918/dummy.key.env")
IDS_FILE = PRIVATE / "ve63_ids.txt"

WORKERS = int(os.environ.get("PPA_WORKERS", "2"))
PPA_ITERS = int(os.environ.get("PPA_ITERS", "5"))
ARCH_CANDIDATES = int(os.environ.get("ARCH_CANDIDATES", "3"))
PPA_TURNS = int(os.environ.get("PPA_TURNS", "40"))
ARCH_TURNS = int(os.environ.get("ARCH_TURNS", "40"))
PHASES = os.environ.get("PPA_PHASES", "baseline,ppa,arch")

sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "agent"))
os.chdir(PROJECT)

from agent.dataset import Dataset  # noqa: E402
from agent.evaluator import Evaluator  # noqa: E402
from agent.lean_repl import LeanREPLPool  # noqa: E402
from agent.search import ppa_improved, extract_ppa  # noqa: E402
from cktarchon.run import build_system_prompt, make_runner  # noqa: E402


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def environment() -> dict[str, str]:
    env = os.environ.copy()
    env["NL2CHIP_ISOLATION_ROOT"] = str(PRIVATE / "agent_state")
    env["PYTHONPATH"] = str(PROJECT) + ":" + str(PROJECT / "agent")
    env["PATH"] = (
        "/home/sgli/.local/bin:/home/sgli/.elan/toolchains/"
        "leanprover--lean4---v4.28.0-rc1/bin:" + env.get("PATH", "")
    )
    for name in (
        "OPENLUX_API_KEY", "OPENLUX_BASE_URL", "CODEX_GATEWAY_API_KEY",
        "OPENAI_API_KEY", "OPENAI_BASE_URL",
    ):
        env.pop(name, None)
    return env


def load_last(path: Path) -> dict[str, dict[str, Any]]:
    last: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return last
    for line in path.read_text().splitlines():
        if line.strip():
            row = json.loads(line)
            last[row["prob_id"]] = row
    return last


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def seed_lean(prob_id: str) -> Path:
    src = SEED_ISO / prob_id / "Generated" / f"{prob_id}.lean"
    if not src.exists():
        raise FileNotFoundError(src)
    iso = PRIVATE / "agent_state" / prob_id / "Generated"
    iso.mkdir(parents=True, exist_ok=True)
    dest = iso / f"{prob_id}.lean"
    if not dest.exists():
        shutil.copyfile(src, dest)
    host = PROJECT / "Generated" / f"{prob_id}.lean"
    if host.exists() or host.is_symlink():
        host.unlink()
    host.symlink_to(dest)
    return dest


def write_lean(prob_id: str, code: str) -> None:
    dest = PRIVATE / "agent_state" / prob_id / "Generated" / f"{prob_id}.lean"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(code)
    host = PROJECT / "Generated" / f"{prob_id}.lean"
    if host.exists() or host.is_symlink():
        if not host.is_symlink() or host.readlink() != dest:
            host.unlink()
            host.symlink_to(dest)
    else:
        host.symlink_to(dest)


def cleanup_orfs(run_dir: Path, prob_id: str) -> None:
    synth = run_dir / "synth" / prob_id
    for name in ("orfs_results", "orfs_logs"):
        path = synth / name
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)


def fmt_metric(value: Any, nd: int = 2) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, float):
        return f"{value:.{nd}f}"
    return str(value)


def ppa_feedback(prob_id: str, result: dict[str, Any], iteration: int, history: list[dict[str, Any]]) -> str:
    ppa = extract_ppa(result)
    lines = [
        f"## PPA Optimization Feedback — Iteration {iteration + 1}/{PPA_ITERS}",
        "",
        f"`Generated/{prob_id}.lean` already compiles and passed RTL simulation.",
        "Optimize area and cell count in Lean. Keep the same ports, latency, reset, and clocking.",
        "Do not rewrite from scratch unless a local rewrite is required for a smaller architecture.",
        "",
        "### Current PPA (Yosys / sky130hd, synth-only)",
        f"- Area: {fmt_metric(ppa.get('area_um2'))} μm²",
        f"- Cell count: {fmt_metric(ppa.get('cell_count'), 0)}",
        f"- WNS: {fmt_metric(ppa.get('wns_ns'))} ns",
        f"- Power: {fmt_metric(ppa.get('power_uw'), 4)} μW",
    ]
    if history:
        lines += ["", "### History", "| Iter | Area | Cells |", "|------|------|-------|"]
        for idx, row in enumerate(history):
            label = "baseline" if idx == 0 else str(idx)
            lines.append(
                f"| {label} | {fmt_metric(row.get('area_um2'))} | {fmt_metric(row.get('cell_count'), 0)} |"
            )
    lines += [
        "",
        "### Instructions",
        f"1. Read `Generated/{prob_id}.lean`.",
        "2. Simplify muxes, drop unused registers, and rewrite arithmetic if that reduces area.",
        "3. Keep `#synthesizeVerilog` on the implementation that should be emitted.",
        "4. Run the harness Lean check until it returns Generated Verilog.",
        "5. Stop after a successful Lean check. The outer evaluator will re-simulate and re-synthesize.",
        "6. If you cannot improve area without breaking behavior, leave the file unchanged.",
    ]
    return "\n".join(lines)


def arch_feedback(prob_id: str, candidates: list[dict[str, Any]], iteration: int) -> str:
    lines = [
        f"## Architecture Exploration — Candidate {iteration + 1}/{ARCH_CANDIDATES}",
        "",
        f"Write a COMPLETELY DIFFERENT microarchitecture for `{prob_id}` in `Generated/{prob_id}.lean`.",
        "Do not tweak the existing implementation. Keep the public interface and latency.",
        "",
        "### Candidates so far",
        "| # | Desc | Sim | Area | Cells |",
        "|---|------|-----|------|-------|",
    ]
    for cand in candidates:
        sim = "Pass" if cand["sim_pass"] else "FAIL"
        lines.append(
            f"| v{cand['index']} | {cand['description']} | {sim} | "
            f"{fmt_metric(cand['ppa'].get('area_um2'))} | {fmt_metric(cand['ppa'].get('cell_count'), 0)} |"
        )
    lines += [
        "",
        "Try a different point in the parallelism / pipeline / resource-sharing space.",
        f"Write the new architecture to `Generated/{prob_id}.lean`, Lean-check until Verilog is emitted, then stop.",
    ]
    return "\n".join(lines)


def make_args() -> SimpleNamespace:
    return SimpleNamespace(
        harness="codex-agent",
        model="gpt-5.6-sol",
        prompt_profile="compact",
        archon_src=str(ARCHON_SRC),
        codex_bin=str(WRAPPER),
        codex_effort="ultra",
        codex_sandbox="workspace-write",
        codex_idle_timeout=900.0,
        codex_max_attempts=3,
        codex_base_url_env=None,
        codex_key_env=None,
        codex_wire_api="responses",
        no_codex_chat_proxy=True,
        api_timeout=300.0,
        hide_cvdp_harness_from_agent=False,
        dataset="verilogeval",
        codex_agent_user=None,
        interface_prompt_policy="legacy",
        max_tokens=16384,
        max_turns=PPA_TURNS,
        total_turn_budget=None,
        generation_turn_cap=PPA_TURNS,
    )


def run_agent(prob_id: str, info: Any, skill: str, prompt: str, log_base: Path, max_turns: int, role: str) -> dict[str, Any]:
    args = make_args()
    runner = make_runner(
        args=args,
        prob_id=prob_id,
        role=role,
        log_base=log_base,
        skill=skill,
        info=info,
        repl=None,
    )
    stats = runner.run(prompt, max_turns=max_turns)
    return {
        "turns": getattr(stats, "turns", 0),
        "input_tokens": getattr(stats, "input_tokens", 0),
        "output_tokens": getattr(stats, "output_tokens", 0),
    }


def evaluate(evaluator: Evaluator, prob_id: str, run_dir: Path) -> dict[str, Any]:
    result = evaluator.evaluate(prob_id, run_dir)
    cleanup_orfs(run_dir, prob_id)
    return result


def already_done(path: Path, prob_id: str) -> bool:
    last = load_last(path)
    row = last.get(prob_id)
    return bool(row) and not row.get("agent_error")


def process_one(
    prob_id: str,
    *,
    seed_status: str,
    ds: Dataset,
    skill: str,
    run_dir: Path,
    evaluator: Evaluator,
    phases: set[str],
) -> dict[str, Any]:
    info = ds.get_problem(prob_id) if hasattr(ds, "get_problem") else ds.problems[prob_id]
    out: dict[str, Any] = {
        "prob_id": prob_id,
        "seed_sim_status": seed_status,
        "timestamp": utc_now(),
        "experiment": "ppa_arch_ve63",
        "model": "gpt-5.6-sol",
    }
    if seed_status != "sim_pass":
        out["ppa_status"] = "skip_not_sim_pass"
        out["arch_status"] = "skip_not_sim_pass"
        return out

    dest = seed_lean(prob_id)
    baseline_code = dest.read_text()
    write_lean(prob_id, baseline_code)
    baseline = evaluate(evaluator, prob_id, run_dir)
    out["baseline"] = {
        "sim_status": baseline.get("sim_status"),
        "compile_pass": baseline.get("compile_pass"),
        "synth_pass": baseline.get("synth_pass"),
        **extract_ppa(baseline),
        "synth_error": baseline.get("synth_error") or baseline.get("detail", "")[:400],
    }
    if baseline.get("sim_status") != "sim_pass" or not baseline.get("synth_pass"):
        out["ppa_status"] = "skip_no_synth"
        out["arch_status"] = "skip_no_synth"
        return out

    if "ppa" in phases:
        history = [extract_ppa(baseline)]
        best_code = baseline_code
        best_ppa = history[0]
        best_result = baseline
        ppa_rows = []
        for it in range(PPA_ITERS):
            write_lean(prob_id, best_code if it == 0 else dest.read_text())
            backup = dest.read_text()
            prompt = ppa_feedback(prob_id, best_result, it, history)
            try:
                stats = run_agent(
                    prob_id, info, skill, prompt,
                    run_dir / "logs" / prob_id / f"ppa_{it+1:02d}",
                    PPA_TURNS, "ppa-opt",
                )
            except Exception as exc:
                ppa_rows.append({"iteration": it + 1, "ppa_status": "agent_error", "error": str(exc)})
                break
            new_result = evaluate(evaluator, prob_id, run_dir)
            new_ppa = extract_ppa(new_result)
            if new_result.get("sim_status") != "sim_pass":
                write_lean(prob_id, backup)
                ppa_rows.append({
                    "iteration": it + 1,
                    "ppa_status": "rollback_sim_fail",
                    **new_ppa,
                    **stats,
                })
                continue
            improved = ppa_improved(best_ppa, new_ppa)
            status = "improved" if improved else "converged"
            ppa_rows.append({"iteration": it + 1, "ppa_status": status, **new_ppa, **stats})
            history.append(new_ppa)
            if improved:
                best_ppa = new_ppa
                best_code = dest.read_text()
                best_result = new_result
        write_lean(prob_id, best_code)
        out["ppa"] = {
            "entered": True,
            "history": ppa_rows,
            "best": best_ppa,
            "baseline": history[0],
        }
        if best_ppa.get("area_um2") and history[0].get("area_um2"):
            out["ppa"]["area_reduction_pct"] = (
                (history[0]["area_um2"] - best_ppa["area_um2"]) / history[0]["area_um2"] * 100.0
            )

    if "arch" in phases:
        write_lean(prob_id, baseline_code)
        candidates = [{
            "index": 0,
            "code": baseline_code,
            "ppa": extract_ppa(baseline),
            "sim_pass": True,
            "description": "initial",
        }]
        arch_rows = []
        for it in range(ARCH_CANDIDATES):
            prompt = arch_feedback(prob_id, candidates, it)
            try:
                stats = run_agent(
                    prob_id, info, skill, prompt,
                    run_dir / "logs" / prob_id / f"arch_{it+1:02d}",
                    ARCH_TURNS, "arch-explore",
                )
            except Exception as exc:
                arch_rows.append({"iteration": it + 1, "arch_status": "agent_error", "error": str(exc)})
                break
            new_result = evaluate(evaluator, prob_id, run_dir)
            code = dest.read_text()
            cand = {
                "index": it + 1,
                "code": code,
                "ppa": extract_ppa(new_result),
                "sim_pass": new_result.get("sim_status") == "sim_pass",
                "description": f"candidate_{it+1}",
            }
            candidates.append(cand)
            arch_rows.append({
                "iteration": it + 1,
                "arch_sim_pass": cand["sim_pass"],
                "synth_pass": new_result.get("synth_pass"),
                **cand["ppa"],
                **stats,
            })
        valid = [c for c in candidates if c["sim_pass"] and c["ppa"].get("area_um2") is not None]
        if not valid:
            valid = [candidates[0]]
        best = min(valid, key=lambda c: c["ppa"].get("area_um2") or 1e30)
        write_lean(prob_id, best["code"])
        out["arch"] = {
            "entered": True,
            "candidates": arch_rows,
            "best_index": best["index"],
            "best": best["ppa"],
            "baseline": candidates[0]["ppa"],
        }
        b0 = candidates[0]["ppa"].get("area_um2")
        bb = best["ppa"].get("area_um2")
        if b0 and bb:
            out["arch"]["area_reduction_pct"] = (b0 - bb) / b0 * 100.0
    return out


def get_problem_compat(ds: Dataset, prob_id: str):
    return ds.load_problem(prob_id)


def main() -> int:
    os.environ.update(environment())
    subprocess.check_call(["bash", "/home/sgli/work/codex_jing_chatgpt_probe/ensure_socks.sh"])
    OUT.mkdir(parents=True, exist_ok=True)
    (PRIVATE / "agent_state").mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = OUT / f"run_{stamp}"
    run_dir.mkdir(parents=True, exist_ok=True)
    results_path = run_dir / "results.jsonl"
    phases = {p.strip() for p in PHASES.split(",") if p.strip()}
    ids = [line.strip() for line in IDS_FILE.read_text().splitlines() if line.strip()]
    limit = os.environ.get("PPA_LIMIT")
    if limit:
        ids = ids[: int(limit)]
    seed_rows = load_last(SEM_JSONL)
    skill = (PROJECT / "agent" / "skill.txt").read_text()
    ds = Dataset("verilogeval", PROJECT)

    print(f"PPA/arch VE63 start {stamp} workers={WORKERS} phases={sorted(phases)} n={len(ids)}", flush=True)
    pool = None
    try:
        pool = LeanREPLPool(size=WORKERS, project_dir=PROJECT)
    except Exception as exc:
        print(f"REPL pool unavailable: {exc}", flush=True)

    def _one(prob_id: str) -> dict[str, Any]:
        repl = pool.acquire() if pool is not None else None
        try:
            evaluator = Evaluator(
                project_root=PROJECT,
                enable_synth=True,
                enable_pnr=False,
                dataset="verilogeval",
                dataset_obj=ds,
                lean_repl=repl,
            )
            seed_status = seed_rows.get(prob_id, {}).get("sim_status", "missing")
            return process_one(
                prob_id,
                seed_status=seed_status,
                ds=ds,
                skill=skill,
                run_dir=run_dir,
                evaluator=evaluator,
                phases=phases,
            )
        finally:
            if pool is not None and repl is not None:
                pool.release(repl)

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(_one, pid): pid for pid in ids}
        for fut in as_completed(futs):
            pid = futs[fut]
            try:
                row = fut.result()
            except Exception as exc:
                row = {
                    "prob_id": pid,
                    "agent_error": f"{type(exc).__name__}: {exc}",
                    "trace": traceback.format_exc()[-2000:],
                    "timestamp": utc_now(),
                }
                print(f"FAIL {pid}: {exc}", flush=True)
            else:
                print(
                    f"DONE {pid} seed={row.get('seed_sim_status')} "
                    f"base_synth={row.get('baseline', {}).get('synth_pass')} "
                    f"ppa={bool(row.get('ppa'))} arch={bool(row.get('arch'))}",
                    flush=True,
                )
            append_jsonl(results_path, row)
    if pool is not None:
        pool.close_all()
    print(f"wrote {results_path}", flush=True)
    return 0


if __name__ == "__main__":
    # rewrite process_one info lookup robustly by editing the function at runtime
    _impl = process_one

    def process_one(prob_id, *, seed_status, ds, skill, run_dir, evaluator, phases):  # type: ignore
        info = get_problem_compat(ds, prob_id)
        # inject into closure by calling a local copy
        return _impl_with_info(prob_id, seed_status, ds, skill, run_dir, evaluator, phases, info)

    def _impl_with_info(prob_id, seed_status, ds, skill, run_dir, evaluator, phases, info):
        # duplicate body start: replace ds.get
        out: dict[str, Any] = {
            "prob_id": prob_id,
            "seed_sim_status": seed_status,
            "timestamp": utc_now(),
            "experiment": "ppa_arch_ve63",
            "model": "gpt-5.6-sol",
        }
        if seed_status != "sim_pass":
            out["ppa_status"] = "skip_not_sim_pass"
            out["arch_status"] = "skip_not_sim_pass"
            return out
        dest = seed_lean(prob_id)
        baseline_code = dest.read_text()
        write_lean(prob_id, baseline_code)
        baseline = evaluate(evaluator, prob_id, run_dir)
        out["baseline"] = {
            "sim_status": baseline.get("sim_status"),
            "compile_pass": baseline.get("compile_pass"),
            "synth_pass": baseline.get("synth_pass"),
            **extract_ppa(baseline),
            "synth_error": (baseline.get("synth_error") or baseline.get("detail") or "")[:400],
        }
        if baseline.get("sim_status") != "sim_pass" or not baseline.get("synth_pass"):
            out["ppa_status"] = "skip_no_synth"
            out["arch_status"] = "skip_no_synth"
            return out
        if "ppa" in phases:
            history = [extract_ppa(baseline)]
            best_code = baseline_code
            best_ppa = history[0]
            best_result = baseline
            ppa_rows = []
            write_lean(prob_id, best_code)
            for it in range(PPA_ITERS):
                backup = dest.read_text()
                prompt = ppa_feedback(prob_id, best_result, it, history)
                try:
                    stats = run_agent(
                        prob_id, info, skill, prompt,
                        run_dir / "logs" / prob_id / f"ppa_{it+1:02d}",
                        PPA_TURNS, "ppa-opt",
                    )
                except Exception as exc:
                    ppa_rows.append({"iteration": it + 1, "ppa_status": "agent_error", "error": str(exc)})
                    break
                new_result = evaluate(evaluator, prob_id, run_dir)
                new_ppa = extract_ppa(new_result)
                if new_result.get("sim_status") != "sim_pass":
                    write_lean(prob_id, backup)
                    ppa_rows.append({"iteration": it + 1, "ppa_status": "rollback_sim_fail", **new_ppa, **stats})
                    continue
                improved = ppa_improved(best_ppa, new_ppa)
                status = "improved" if improved else "converged"
                ppa_rows.append({"iteration": it + 1, "ppa_status": status, **new_ppa, **stats})
                history.append(new_ppa)
                if improved:
                    best_ppa = new_ppa
                    best_code = dest.read_text()
                    best_result = new_result
                else:
                    write_lean(prob_id, best_code)
            write_lean(prob_id, best_code)
            out["ppa"] = {"entered": True, "history": ppa_rows, "best": best_ppa, "baseline": history[0]}
            if best_ppa.get("area_um2") and history[0].get("area_um2"):
                out["ppa"]["area_reduction_pct"] = (
                    (history[0]["area_um2"] - best_ppa["area_um2"]) / history[0]["area_um2"] * 100.0
                )
        if "arch" in phases:
            write_lean(prob_id, baseline_code)
            candidates = [{
                "index": 0, "code": baseline_code, "ppa": extract_ppa(baseline),
                "sim_pass": True, "description": "initial",
            }]
            arch_rows = []
            for it in range(ARCH_CANDIDATES):
                prompt = arch_feedback(prob_id, candidates, it)
                try:
                    stats = run_agent(
                        prob_id, info, skill, prompt,
                        run_dir / "logs" / prob_id / f"arch_{it+1:02d}",
                        ARCH_TURNS, "arch-explore",
                    )
                except Exception as exc:
                    arch_rows.append({"iteration": it + 1, "arch_status": "agent_error", "error": str(exc)})
                    break
                new_result = evaluate(evaluator, prob_id, run_dir)
                cand = {
                    "index": it + 1,
                    "code": dest.read_text(),
                    "ppa": extract_ppa(new_result),
                    "sim_pass": new_result.get("sim_status") == "sim_pass",
                    "description": f"candidate_{it+1}",
                }
                candidates.append(cand)
                arch_rows.append({
                    "iteration": it + 1, "arch_sim_pass": cand["sim_pass"],
                    "synth_pass": new_result.get("synth_pass"), **cand["ppa"], **stats,
                })
            valid = [c for c in candidates if c["sim_pass"] and c["ppa"].get("area_um2") is not None] or [candidates[0]]
            best = min(valid, key=lambda c: c["ppa"].get("area_um2") or 1e30)
            write_lean(prob_id, best["code"])
            out["arch"] = {
                "entered": True, "candidates": arch_rows, "best_index": best["index"],
                "best": best["ppa"], "baseline": candidates[0]["ppa"],
            }
            b0 = candidates[0]["ppa"].get("area_um2")
            bb = best["ppa"].get("area_um2")
            if b0 and bb:
                out["arch"]["area_reduction_pct"] = (b0 - bb) / b0 * 100.0
        return out

    raise SystemExit(main())
