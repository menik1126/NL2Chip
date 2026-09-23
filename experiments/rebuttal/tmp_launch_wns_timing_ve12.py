#!/usr/bin/env python3
"""Timing-guided optimization: freeze Tclk, require slack improvement.

Three arms (A area / T numeric timing / P critical path) on 12 sequential VE
seeds. Does not overwrite frozen table trees or the CVDP compile-feedback job.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import traceback
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

PROJECT = Path("/home/sgli/work/NL2Chip_openlux_repair_state_20260914")
PRIVATE = Path("/home/sgli/work/nl2chip_wns_timing_private_20260921")
SEED_ISO = Path("/home/sgli/work/nl2chip_chatgpt_socks_private_20260918/agent_state")
SEM_JSONL = Path(
    "/home/sgli/work/NL2Chip_rebuttal_artifacts/2026-09-19/"
    "sparkle_lean_semantic_chatgpt_socks_fourds/verilogeval_20260918_171939/"
    "cktarchon_run_20260919_021704/results.jsonl"
)
PYTHON = Path("/home/sgli/work/NL2Chip/.venv/bin/python")
OUT = Path("/home/sgli/work/NL2Chip_rebuttal_artifacts/2026-09-21/sparkle_wns_timing_ve12")
ARCHON_SRC = Path("/home/sgli/work/archon-official/src")
WRAPPER = PRIVATE / "codex_chatgpt_socks.py"
LOCK_DIR = Path("/tmp/nl2chip_generated_locks")

WORKERS = int(os.environ.get("WNS_WORKERS", "1"))
WNS_ITERS = int(os.environ.get("WNS_ITERS", "5"))
WNS_TURNS = int(os.environ.get("WNS_TURNS", "40"))
DELTA_T = 0.05
EPS = 0.20
LOOSE_PERIOD = 10.0
ARMS = tuple(a.strip() for a in os.environ.get("WNS_ARMS", "A,T,P").split(",") if a.strip())

# Sequential / FSM / arithmetic-with-clk subset of the VE-63 PPA filter.
WNS_IDS = [
    "Prob079_fsm3onehot",
    "Prob080_timer",
    "Prob082_lfsr32",
    "Prob095_review2015_fsmshift",
    "Prob096_review2015_fsmseq",
    "Prob107_fsm1s",
    "Prob109_fsm1",
    "Prob119_fsm3",
    "Prob127_lemmings1",
    "Prob128_fsm_ps2",
    "Prob137_fsm_serial",
    "Prob140_fsm_hdlc",
]

sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "agent"))
os.chdir(PROJECT)

from agent.dataset import Dataset  # noqa: E402
from agent.evaluator import Evaluator  # noqa: E402
from agent.search import extract_ppa  # noqa: E402
from cktarchon.run import make_runner  # noqa: E402


@contextmanager
def generated_lock(prob_id: str):
    LOCK_DIR.mkdir(parents=True, exist_ok=True)
    fh = open(LOCK_DIR / f"{prob_id}.lock", "a")
    fcntl.flock(fh, fcntl.LOCK_EX)
    try:
        yield
    finally:
        fcntl.flock(fh, fcntl.LOCK_UN)
        fh.close()


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


def install_clock_hook() -> None:
    orig = Evaluator._run_pnr

    def wrapped(self, prob_id, sv_code, top_module, run_dir):
        period = float(getattr(self, "_clock_period_ns", LOOSE_PERIOD))
        sdc = run_dir / "synth" / prob_id / "constraints.sdc"
        if sdc.exists() and "create_clock" in sdc.read_text():
            sdc.write_text(f"create_clock -period {period:.4f} [get_ports clk]\n")
        cfg = run_dir / "synth" / prob_id / "config.mk"
        # NUM_CORES is written later by orig; rewrite after orig starts is too late,
        # so patch config immediately before orig overwrites it is useless.
        return orig(self, prob_id, sv_code, top_module, run_dir)

    Evaluator._run_pnr = wrapped  # type: ignore[method-assign]


def has_clk(sv_code: str) -> bool:
    return bool(re.search(r"\binput\b.*\bclk\b", sv_code))


def extract_path_text(run_dir: Path, prob_id: str, limit: int = 1800) -> str:
    synth = run_dir / "synth" / prob_id
    chunks: list[str] = []
    for pattern in ("6_finish.rpt", "*sta*_out.txt", "pnr_stdout.txt"):
        for path in synth.rglob(pattern) if "*" in pattern else ([synth / "orfs_reports"] and list(synth.rglob(pattern))):
            try:
                text = path.read_text(errors="replace")
            except OSError:
                continue
            if "Startpoint" in text or "startpoint" in text or "slack" in text.lower():
                chunks.append(text)
    blob = "\n".join(chunks)
    # keep the last report_checks / startpoint block
    idx = max(blob.lower().rfind("startpoint"), blob.lower().rfind("beginpath"))
    if idx >= 0:
        blob = blob[idx:]
    return blob[:limit]


def enrich_timing(result: dict[str, Any], run_dir: Path, prob_id: str) -> dict[str, Any]:
    ppa = extract_ppa(result)
    slack = result.get("wns_ns")
    path = extract_path_text(run_dir, prob_id)
    m = re.search(r"slack\s+\((?:MET|VIOLATED)\)\s+([0-9.eE+-]+)", path)
    if m:
        slack = float(m.group(1))
    return {
        **ppa,
        "worst_setup_slack_ns": slack,
        "wns_ns": result.get("wns_ns"),
        "sim_status": result.get("sim_status"),
        "compile_pass": result.get("compile_pass"),
        "synth_pass": result.get("synth_pass"),
        "pnr_pass": result.get("pnr_pass"),
        "critical_path": path,
        "clock_period_ns": getattr(result, "_unused", None),
    }


def freeze_tclk(loose_slack: float | None, loose_period: float = LOOSE_PERIOD) -> float:
    if loose_slack is None:
        tcrit = loose_period
    else:
        tcrit = loose_period - loose_slack
        if tcrit <= 0.2:
            tcrit = loose_period
    return max(0.85 * tcrit, 0.5)


def degraded(old: dict[str, Any], new: dict[str, Any], key: str) -> bool:
    ov, nv = old.get(key), new.get(key)
    if ov is None or nv is None or ov == 0:
        return False
    return (nv - ov) / abs(ov) > EPS


def slack_accept(old: dict[str, Any], new: dict[str, Any]) -> bool:
    s0 = old.get("worst_setup_slack_ns")
    s1 = new.get("worst_setup_slack_ns")
    if s0 is None or s1 is None:
        return False
    if (s1 - s0) <= DELTA_T:
        return False
    if degraded(old, new, "area_um2"):
        return False
    if degraded(old, new, "power_uw"):
        return False
    if degraded(old, new, "cell_count"):
        return False
    return True


def fmt(value: Any, nd: int = 3) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, float):
        return f"{value:.{nd}f}"
    return str(value)


def arm_feedback(arm: str, prob_id: str, metrics: dict[str, Any], iteration: int, tclk: float, history: list[dict[str, Any]]) -> str:
    lines = [
        f"## Timing-Guided Optimization — Arm {arm} — Iteration {iteration + 1}/{WNS_ITERS}",
        "",
        f"Clock is FROZEN at `create_clock -period {tclk:.4f}` on `clk`. Do not relax the period.",
        "Do not add pipeline stages, change ports, reset, or latency.",
        "Keep `#synthesizeVerilog` on the implementation. Simulation must still pass.",
        "",
        f"Current post-route metrics (sky130hd, Tclk={tclk:.4f} ns):",
        f"- Area: {fmt(metrics.get('area_um2'))} μm²",
        f"- Cell count: {fmt(metrics.get('cell_count'), 0)}",
        f"- Power: {fmt(metrics.get('power_uw'), 4)} μW",
        f"- WNS: {fmt(metrics.get('wns_ns'))} ns",
        f"- Worst setup slack: {fmt(metrics.get('worst_setup_slack_ns'))} ns",
    ]
    if arm == "A":
        lines += [
            "",
            "### Objective (area control)",
            "Optimize area and cell count only. Timing numbers above are for your information; do not target them.",
        ]
    elif arm == "T":
        lines += [
            "",
            "### Objective (numeric timing)",
            "Improve worst setup slack (more positive). Area/power/cells may move at most 20%.",
            "Do not accept an area-only win if slack does not improve.",
        ]
    else:
        path = metrics.get("critical_path") or "(no path report)"
        lines += [
            "",
            "### Objective (critical-path rewrite)",
            "Improve worst setup slack using the path below. Shorten/balance that path in Lean.",
            "Do not add extra pipeline registers. Area/power/cells may move at most 20%.",
            "",
            "### Critical path (truncated STA)",
            "```",
            str(path)[:1600],
            "```",
        ]
    if history:
        lines += ["", "| Iter | Slack | Area | Cells |", "|------|-------|------|-------|"]
        for idx, row in enumerate(history):
            label = "baseline" if idx == 0 else str(idx)
            lines.append(
                f"| {label} | {fmt(row.get('worst_setup_slack_ns'))} | "
                f"{fmt(row.get('area_um2'))} | {fmt(row.get('cell_count'), 0)} |"
            )
    lines += [
        "",
        f"1. Read `Generated/{prob_id}.lean`.",
        "2. Rewrite logic on the critical path (mux depth, arithmetic, FSM encoding) without changing I/O timing in cycles.",
        "3. Lean-check until Verilog is emitted, then stop. Outer loop will re-sim and re-P&R at the frozen clock.",
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
        max_turns=WNS_TURNS,
        total_turn_budget=None,
        generation_turn_cap=WNS_TURNS,
    )


def run_agent(prob_id: str, info: Any, skill: str, prompt: str, log_base: Path, role: str) -> dict[str, Any]:
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
    stats = runner.run(prompt, max_turns=WNS_TURNS)
    return {
        "turns": getattr(stats, "turns", 0),
        "input_tokens": getattr(stats, "input_tokens", 0),
        "output_tokens": getattr(stats, "output_tokens", 0),
    }


def evaluate(evaluator: Evaluator, prob_id: str, run_dir: Path, period: float) -> dict[str, Any]:
    evaluator._clock_period_ns = period
    result = evaluator.evaluate(prob_id, run_dir)
    metrics = enrich_timing(result, run_dir, prob_id)
    metrics["clock_period_ns"] = period
    synth = run_dir / "synth" / prob_id
    for name in ("orfs_results", "orfs_logs"):
        path = synth / name
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)
    return metrics


def process_one(prob_id: str, *, seed_status: str, ds: Dataset, skill: str, run_dir: Path, evaluator: Evaluator) -> dict[str, Any]:
    info = ds.load_problem(prob_id)
    dest = seed_lean(prob_id)
    baseline_code = dest.read_text()
    write_lean(prob_id, baseline_code)
    out: dict[str, Any] = {
        "prob_id": prob_id,
        "seed_sim_status": seed_status,
        "timestamp": utc_now(),
        "experiment": "wns_timing_ve12",
        "model": "gpt-5.6-sol",
        "arms": {},
    }
    if seed_status != "sim_pass":
        out["status"] = "skip_not_sim_pass"
        return out

    loose = evaluate(evaluator, prob_id, run_dir, LOOSE_PERIOD)
    out["loose"] = {k: v for k, v in loose.items() if k != "critical_path"}
    out["loose"]["critical_path_head"] = (loose.get("critical_path") or "")[:400]
    sv_files = list((run_dir / "synth" / prob_id).glob("*.sv"))
    sv = sv_files[0].read_text() if sv_files else ""
    sdc_path = run_dir / "synth" / prob_id / "constraints.sdc"
    sdc_text = sdc_path.read_text() if sdc_path.exists() else ""
    if not has_clk(sv) and "create_clock" not in sdc_text:
        out["status"] = "skip_no_clk"
        return out
    if not loose.get("synth_pass"):
        out["status"] = "skip_no_synth"
        return out

    tclk = freeze_tclk(loose.get("worst_setup_slack_ns"))
    out["tclk_ns"] = tclk
    write_lean(prob_id, baseline_code)
    frozen_base = evaluate(evaluator, prob_id, run_dir, tclk)
    out["frozen_baseline"] = {k: v for k, v in frozen_base.items() if k != "critical_path"}
    if frozen_base.get("sim_status") != "sim_pass" or not frozen_base.get("pnr_pass"):
        out["status"] = "skip_frozen_pnr_fail"
        out["frozen_baseline"]["critical_path_head"] = (frozen_base.get("critical_path") or "")[:400]
        return out

    for arm in ARMS:
        write_lean(prob_id, baseline_code)
        best_code = baseline_code
        best = dict(frozen_base)
        history = [frozen_base]
        rows = []
        for it in range(WNS_ITERS):
            backup = dest.read_text()
            prompt = arm_feedback(arm, prob_id, best, it, tclk, history)
            try:
                stats = run_agent(
                    prob_id, info, skill, prompt,
                    run_dir / "logs" / prob_id / f"arm{arm}_{it+1:02d}",
                    f"wns-{arm}",
                )
            except Exception as exc:
                rows.append({"iteration": it + 1, "status": "agent_error", "error": str(exc)})
                break
            new = evaluate(evaluator, prob_id, run_dir, tclk)
            if new.get("sim_status") != "sim_pass":
                write_lean(prob_id, backup)
                rows.append({"iteration": it + 1, "status": "rollback_sim_fail", **{k: new.get(k) for k in ("area_um2", "cell_count", "power_uw", "wns_ns", "worst_setup_slack_ns", "pnr_pass")}, **stats})
                continue
            accepted = slack_accept(best, new)
            status = "accepted" if accepted else "rejected_no_slack"
            if arm == "A":
                # area arm uses original PPA accept (any metric), but still record slack
                from agent.search import ppa_improved
                accepted = ppa_improved(
                    {"area_um2": best.get("area_um2"), "cell_count": best.get("cell_count"), "wns_ns": best.get("wns_ns"), "power_uw": best.get("power_uw")},
                    {"area_um2": new.get("area_um2"), "cell_count": new.get("cell_count"), "wns_ns": new.get("wns_ns"), "power_uw": new.get("power_uw")},
                )
                status = "accepted_area" if accepted else "converged_area"
            rows.append({
                "iteration": it + 1,
                "status": status,
                "area_um2": new.get("area_um2"),
                "cell_count": new.get("cell_count"),
                "power_uw": new.get("power_uw"),
                "wns_ns": new.get("wns_ns"),
                "worst_setup_slack_ns": new.get("worst_setup_slack_ns"),
                "pnr_pass": new.get("pnr_pass"),
                "delta_slack": None if best.get("worst_setup_slack_ns") is None or new.get("worst_setup_slack_ns") is None else new["worst_setup_slack_ns"] - best["worst_setup_slack_ns"],
                **stats,
            })
            history.append(new)
            if accepted:
                best = new
                best_code = dest.read_text()
            else:
                write_lean(prob_id, best_code)
        write_lean(prob_id, best_code)
        s0 = frozen_base.get("worst_setup_slack_ns")
        s1 = best.get("worst_setup_slack_ns")
        out["arms"][arm] = {
            "history": rows,
            "baseline_slack": s0,
            "best_slack": s1,
            "delta_slack": None if s0 is None or s1 is None else s1 - s0,
            "baseline_area": frozen_base.get("area_um2"),
            "best_area": best.get("area_um2"),
            "wns_closed": bool(s1 is not None and s1 >= 0),
        }
    out["status"] = "done"
    return out


def main() -> int:
    os.environ.update(environment())
    subprocess.check_call(["bash", "/home/sgli/work/codex_jing_chatgpt_probe/ensure_socks.sh"])
    install_clock_hook()
    OUT.mkdir(parents=True, exist_ok=True)
    (PRIVATE / "agent_state").mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = OUT / f"run_{stamp}"
    run_dir.mkdir(parents=True, exist_ok=True)
    results_path = run_dir / "results.jsonl"
    skill = (PROJECT / "agent" / "skill.txt").read_text()
    ds = Dataset("verilogeval", PROJECT)
    seed_rows = load_last(SEM_JSONL)
    ids = list(WNS_IDS)
    extra = os.environ.get("WNS_LIMIT")
    if extra:
        ids = ids[: int(extra)]
    print(f"WNS timing start {stamp} workers={WORKERS} arms={ARMS} n={len(ids)}", flush=True)

    def _one(prob_id: str) -> dict[str, Any]:
        evaluator = Evaluator(
            project_root=PROJECT,
            enable_synth=True,
            enable_pnr=True,
            enable_drc=False,
            enable_lvs=False,
            enable_gls=False,
            dataset="verilogeval",
            dataset_obj=ds,
        )
        with generated_lock(prob_id):
            return process_one(
                prob_id,
                seed_status=seed_rows.get(prob_id, {}).get("sim_status", "missing"),
                ds=ds,
                skill=skill,
                run_dir=run_dir,
                evaluator=evaluator,
            )

    from concurrent.futures import ThreadPoolExecutor, as_completed
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
                    f"DONE {pid} status={row.get('status')} tclk={row.get('tclk_ns')} "
                    f"arms={list((row.get('arms') or {}).keys())}",
                    flush=True,
                )
            append_jsonl(results_path, row)
    print(f"wrote {results_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
