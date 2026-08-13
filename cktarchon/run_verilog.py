from __future__ import annotations

import argparse
import ast
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import re
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from .env import ensure_runtime_env, load_env_file, model_alias
from .harness import AnthropicHarnessRunner
from .logs import AgentStats, append_jsonl, parse_agent_log

PROJECT_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_MAGE_PROMPTS = Path(
    "/home/sgli/work/external_baselines/"
    "MAGE-A-Multi-Agent-Engine-for-Automated-RTL-Code-Generation/"
    "src/mage/prompts.py"
)

MAGE_ALIGNED_RTL_RULES = """## MAGE-Aligned RTL Generation Guidance
- Think through circuit behavior, cycle latency, reset semantics, and parameterization before writing RTL.
- The module interface must exactly match the benchmark interface contract.
- Declare ports and internal signals as logic.
- Use localparam, reg, or logic for state; do not use state_t parameters.
- Use always @(*) for combinational always blocks.
- Do not use reverse part-selects, inside, unique, or unique0.
- Return a complete synthesizable SystemVerilog module in the required candidate file.
"""


VERILOG_ARCHON_SKILL = """You are an expert hardware designer using an Archon-style agent harness to implement RTL directly in SystemVerilog.

## Goal
Generate one synthesizable SystemVerilog module that matches the benchmark contract and passes the external compile/simulation harness.

## Output File
- Write the candidate to `cktarchon_work/<prob_id>/candidate.sv` with the `write_file` or `edit_file` tool.
- The file must contain complete SystemVerilog source with `module ... endmodule`.
- Do not write Lean or Sparkle. This is a direct-SystemVerilog baseline.

## RTL Rules
- Use SystemVerilog-2012 syntax compatible with Icarus Verilog/cocotb.
- Match the required top module name, port names, port directions, widths, parameters, and clock/reset behavior exactly.
- Preserve required cycle latency. Sequential outputs should update on the benchmark clock edge unless the specification clearly says otherwise.
- For parameterized tasks, implement real Verilog parameters/generate logic rather than hard-coding only the default width.
- Keep the design self-contained unless the task explicitly provides context modules that must be instantiated.
- Avoid unsynthesizable constructs in the DUT: no delays, no initial blocks for behavior, no file I/O, no force/release.

## Workflow
1. Read the problem and benchmark interface contract.
2. Optionally inspect small context files mentioned in the prompt.
3. Write a complete candidate to `cktarchon_work/<prob_id>/candidate.sv`.
4. If feedback is provided, repair the same file using the simulator/compile diagnostics.
5. Stop after the candidate file is written.
"""


def _add_agent_paths() -> None:
    for rel in ("agent", "experiments"):
        path = PROJECT_ROOT / rel
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="CktArchon baseline: Archon-style direct SystemVerilog generation with sim feedback")
    p.add_argument("--dataset", default="cvdp", choices=["verilogeval", "rtllm", "resbench", "cvdp", "realbench"])
    p.add_argument("--problem-file", type=str, default=None)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--filter", type=str, default=None)
    p.add_argument("--model", default="claude-sonnet-4-5-20250929")
    p.add_argument("--max-turns", type=int, default=80)
    p.add_argument("--max-tokens", type=int, default=16384)
    p.add_argument("--results-dir", type=str, required=True)
    p.add_argument("--key-env", default=str(PROJECT_ROOT / "key.env"))
    p.add_argument("--resume", action="store_true")
    p.add_argument("--resume-mode", choices=["passed", "completed"], default="completed")
    p.add_argument("--workers", type=int, default=1, help="Number of problems to run concurrently.")
    p.add_argument("--api-timeout", type=float, default=300.0)
    p.add_argument("--sim-feedback", action="store_true")
    p.add_argument("--sim-feedback-max-iters", type=int, default=3)
    p.add_argument("--sim-feedback-turn-budget", type=int, default=None)
    p.add_argument("--sim-feedback-turns-per-iter", type=int, default=None)
    p.add_argument("--sim-feedback-patience", type=int, default=2)
    p.add_argument("--feedback-mode", choices=["compile-sim", "compile-only"], default="compile-sim")
    p.add_argument(
        "--prompt-profile",
        choices=["archon", "mage-aligned"],
        default="archon",
        help="Prompt-only ablation; mage-aligned adds MAGE RTL rules and 4-shot examples.",
    )
    p.add_argument("--mage-prompts-file", default=str(DEFAULT_MAGE_PROMPTS))
    return p.parse_args()


def discover_problems(args: argparse.Namespace, ds: Any) -> list[str]:
    if args.problem_file:
        path = Path(args.problem_file)
        problems = [line.strip() for line in path.read_text().splitlines() if line.strip() and not line.lstrip().startswith("#")]
        if args.filter:
            pattern = re.compile(args.filter)
            problems = [pid for pid in problems if pattern.search(pid)]
        if args.limit:
            problems = problems[: args.limit]
        return problems
    return ds.discover_problems(limit=args.limit, filter_re=args.filter)


def already_done(run_parent: Path, prob_id: str, mode: str) -> bool:
    for results in run_parent.glob("*/results.jsonl"):
        try:
            for line in results.read_text(errors="replace").splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                if row.get("prob_id") != prob_id:
                    continue
                if mode == "passed" and row.get("sim_status") == "sim_pass":
                    return True
                agent_error = str(row.get("agent_error") or "")
                if mode == "completed" and not agent_error:
                    return True
        except Exception:
            continue
    return False


def truncate(text: str, limit: int, keep: str = "tail") -> str:
    text = str(text or "")
    if len(text) <= limit:
        return text
    if keep == "head":
        return text[:limit] + f"\n... [truncated, {len(text)} chars total]"
    if keep == "middle":
        half = max(1, limit // 2)
        return text[:half] + f"\n... [truncated, {len(text)} chars total]\n" + text[-half:]
    return f"... [truncated, {len(text)} chars total]\n" + text[-limit:]


def load_mage_rtl_examples(path: Path) -> str:
    tree = ast.parse(path.read_text(errors="replace"), filename=str(path))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "RTL_4_SHOT_EXAMPLES" for target in node.targets):
            continue
        value = ast.literal_eval(node.value)
        if isinstance(value, str):
            return value.strip()
    raise RuntimeError(f"RTL_4_SHOT_EXAMPLES not found in {path}")


def build_system_prompt(prob_id: str, info: Any, args: argparse.Namespace) -> str:
    prompt = (
        VERILOG_ARCHON_SKILL.rstrip()
        + "\n\n## Harness Rules\n"
        + f"- Problem ID: `{prob_id}`.\n"
        + f"- Required candidate path: `cktarchon_work/{prob_id}/candidate.sv`.\n"
        + f"- Required top module name: `{info.design_name}`.\n"
        + "- Treat the Benchmark Interface Contract as authoritative over guesses from examples or file names.\n"
        + "- The outer evaluator will run the official compile/simulation harness after you stop.\n"
        + "- This H20 host may not have `rg`; use `grep` and `find` if you need searches.\n"
    )
    if args.prompt_profile == "mage-aligned":
        examples = load_mage_rtl_examples(Path(args.mage_prompts_file))
        prompt += f"\n{MAGE_ALIGNED_RTL_RULES}\n## MAGE RTL Demonstrations\n{examples}\n"
    return prompt


def build_initial_prompt(prob_id: str, info: Any, dataset_name: str) -> str:
    import search

    contract = search.format_benchmark_interface_contract(info)
    contract_section = f"### Benchmark Interface Contract\n\n{contract}\n\n" if contract else ""
    return (
        f"## Problem: {prob_id}\n\n"
        f"### Dataset\n\n{dataset_name}\n\n"
        f"### Target Top Module\n\n`{info.design_name}`\n\n"
        f"### Natural Language Specification\n\n{truncate(info.prompt_text, 30000, keep='head')}\n\n"
        f"{contract_section}"
        f"### Task\n\n"
        f"Generate the complete direct SystemVerilog implementation. "
        f"Write it to `cktarchon_work/{prob_id}/candidate.sv` and then stop.\n"
    )


def build_feedback_prompt(
    prob_id: str,
    info: Any,
    dataset_name: str,
    iteration: int,
    structured_feedback: str,
) -> str:
    return (
        f"## Direct SystemVerilog Simulation Feedback - Iteration {iteration}\n\n"
        f"The current candidate for `{prob_id}` failed evaluation. Repair the SystemVerilog directly.\n\n"
        f"### Dataset / Top Module\n\n- Dataset: `{dataset_name}`\n- Top module: `{info.design_name}`\n\n"
        f"### Natural Language Specification\n\n{truncate(info.prompt_text, 30000, keep='head')}\n\n"
        f"### Structured Simulator / Compiler Feedback\n\n{structured_feedback}\n\n"
        f"### Required Action\n\n"
        f"Overwrite `cktarchon_work/{prob_id}/candidate.sv` with the complete corrected SystemVerilog module. "
        f"Do not output Lean or Sparkle.\n"
    )


def read_candidate_from_log(log_path: Path) -> str | None:
    if not log_path.exists():
        return None
    text_blocks: list[str] = []
    for line in log_path.read_text(errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("event") != "assistant":
            continue
        for block in row.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "text":
                text_blocks.append(str(block.get("text") or ""))
    for text in reversed(text_blocks):
        code = extract_module_from_text(text)
        if code:
            return code
    return None


def extract_module_from_text(text: str) -> str | None:
    from baseline_verilog_iterative import extract_module

    return extract_module(text)


def normalize_sv(info: Any, code: str, dataset_name: str) -> str:
    from baseline_verilog_iterative import rename_first_module
    from evaluator import parse_module_ports

    target = "TopModule" if dataset_name == "verilogeval" else info.design_name
    target_mod_name, _ = parse_module_ports(code, module_name=target)
    if target_mod_name == target:
        return code
    mod_name, _ = parse_module_ports(code)
    if mod_name and mod_name != target:
        return rename_first_module(code, target)
    return code


def candidate_path(prob_id: str) -> Path:
    return PROJECT_ROOT / "cktarchon_work" / prob_id / "candidate.sv"


def load_candidate(prob_id: str, info: Any, dataset_name: str, log_path: Path) -> tuple[str | None, str]:
    path = candidate_path(prob_id)
    source = ""
    code: str | None = None
    if path.exists():
        code = path.read_text(errors="replace")
        source = str(path.relative_to(PROJECT_ROOT))
    if not code or "endmodule" not in code:
        fallback = read_candidate_from_log(log_path)
        if fallback:
            code = fallback
            source = "assistant_log_fallback"
    if code:
        code = normalize_sv(info, code, dataset_name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(code)
    return code, source


def merge_stats(total: AgentStats, extra: AgentStats) -> None:
    total.input_tokens += extra.input_tokens
    total.output_tokens += extra.output_tokens
    total.turns += extra.turns
    total.compile_checks += extra.compile_checks
    for name, count in extra.tool_counts.items():
        total.tool_counts[name] = total.tool_counts.get(name, 0) + count


def eval_candidate(dataset_name: str, ds: Any, info: Any, code: str | None, run_dir: Path, iter_idx: int) -> dict:
    from baseline_verilog_iterative import eval_direct_verilog

    if not code:
        return {
            "compile_pass": False,
            "sim_status": "gen_error",
            "sim_mismatches": -1,
            "detail": "No module...endmodule candidate found in cktarchon_work file or assistant log.",
        }
    return eval_direct_verilog(dataset_name, ds, info, code, run_dir, iter_idx)


def is_feedback_repairable(result: dict, mode: str) -> bool:
    if mode == "compile-sim":
        return True
    status = str(result.get("sim_status") or "")
    detail = str(result.get("detail") or "").lower()
    if status in {"gen_error", "compile_fail"}:
        return True
    return result.get("compile_pass") is False and any(
        word in detail for word in ["syntax", "compile", "iverilog", "verilator", "parse", "elaboration"]
    )


def make_runner(args: argparse.Namespace, prob_id: str, role: str, log_base: Path, info: Any) -> AnthropicHarnessRunner:
    return AnthropicHarnessRunner(
        project_root=PROJECT_ROOT,
        prob_id=prob_id,
        model=model_alias(args.model),
        role=role,
        log_base=log_base,
        system_prompt=build_system_prompt(prob_id, info, args),
        max_tokens=args.max_tokens,
        lean_repl=None,
        api_timeout=args.api_timeout,
        extra_write_globs=(f"cktarchon_work/{prob_id}/candidate.sv",),
    )


def process_problem(prob_id: str, *, args: argparse.Namespace, ds: Any, run_dir: Path) -> dict[str, Any]:
    import search

    info = ds.load_problem(prob_id)
    t_problem = time.monotonic()
    work_path = candidate_path(prob_id)
    if work_path.exists():
        work_path.unlink()

    agent_stats = AgentStats()
    agent_error: str | None = None
    agent_elapsed = 0.0
    sim_feedback_history: list[dict[str, Any]] = []
    sim_feedback_success = False
    sim_feedback_iterations = 0

    log_base = run_dir / "logs" / prob_id / "generate"
    try:
        runner = make_runner(args, prob_id, "verilog-generator", log_base, info)
        t0 = time.monotonic()
        stats = runner.run(build_initial_prompt(prob_id, info, args.dataset), max_turns=args.max_turns)
        agent_elapsed += time.monotonic() - t0
        merge_stats(agent_stats, stats)
    except Exception as exc:
        agent_error = f"{type(exc).__name__}: {exc}"
        merge_stats(agent_stats, parse_agent_log(Path(str(log_base) + ".jsonl")))

    code, source = load_candidate(prob_id, info, args.dataset, Path(str(log_base) + ".jsonl"))
    result = eval_candidate(args.dataset, ds, info, code, run_dir, 0)
    best_result = dict(result)
    best_code = code

    if args.sim_feedback:
        if args.sim_feedback_turn_budget is None:
            feedback_turn_budget = max(0, args.max_turns - agent_stats.turns)
        else:
            feedback_turn_budget = max(0, args.sim_feedback_turn_budget)
    else:
        feedback_turn_budget = 0
    feedback_turns_remaining = feedback_turn_budget
    non_improving = 0

    while (
        args.sim_feedback
        and result.get("sim_status") != "sim_pass"
        and feedback_turns_remaining > 0
        and sim_feedback_iterations < max(0, args.sim_feedback_max_iters)
        and is_feedback_repairable(result, args.feedback_mode)
    ):
        sim_feedback_iterations += 1
        current_sv = code or ""
        structured_feedback = search.build_sim_feedback(
            prob_id=prob_id,
            result=result,
            iteration=sim_feedback_iterations - 1,
            history=sim_feedback_history,
            run_dir=run_dir,
            info=info,
            repair_target="verilog",
            current_sv=current_sv,
        )
        prompt = build_feedback_prompt(
            prob_id,
            info,
            args.dataset,
            sim_feedback_iterations,
            structured_feedback,
        )
        repair_log_base = run_dir / "logs" / prob_id / f"sim_feedback_iter_{sim_feedback_iterations}"
        repair_turns = feedback_turns_remaining
        if args.sim_feedback_turns_per_iter is not None:
            repair_turns = min(repair_turns, max(0, args.sim_feedback_turns_per_iter))
        try:
            runner = make_runner(args, prob_id, f"verilog-sim-feedback-{sim_feedback_iterations}", repair_log_base, info)
            t0 = time.monotonic()
            stats = runner.run(prompt, max_turns=repair_turns)
            agent_elapsed += time.monotonic() - t0
            merge_stats(agent_stats, stats)
            feedback_turns_remaining = max(0, feedback_turns_remaining - stats.turns)
        except Exception as exc:
            sim_feedback_history.append({
                "iteration": sim_feedback_iterations,
                "agent_error": f"{type(exc).__name__}: {exc}",
                "remaining_turns": feedback_turns_remaining,
            })
            merge_stats(agent_stats, parse_agent_log(Path(str(repair_log_base) + ".jsonl")))
            break

        code, source = load_candidate(prob_id, info, args.dataset, Path(str(repair_log_base) + ".jsonl"))
        new_result = eval_candidate(args.dataset, ds, info, code, run_dir, sim_feedback_iterations)
        hist = {
            "iteration": sim_feedback_iterations,
            "compile_pass": new_result.get("compile_pass"),
            "sim_status": new_result.get("sim_status"),
            "sim_mismatches": new_result.get("sim_mismatches"),
            "detail": truncate(str(new_result.get("detail") or ""), 1000, keep="tail"),
            "remaining_turns": feedback_turns_remaining,
        }
        sim_feedback_history.append(hist)

        if search.eval_progress_key(new_result) > search.eval_progress_key(best_result):
            best_result = dict(new_result)
            best_code = code
            non_improving = 0
        else:
            non_improving += 1
        result = new_result
        if result.get("sim_status") == "sim_pass":
            sim_feedback_success = True
            best_result = dict(result)
            best_code = code
            break
        if args.sim_feedback_patience > 0 and non_improving >= args.sim_feedback_patience:
            sim_feedback_history.append({
                "iteration": sim_feedback_iterations,
                "early_stop": f"{args.sim_feedback_patience} consecutive non-improving repairs",
            })
            break

    if best_code and best_result is not result:
        result = best_result
        code = best_code
        work_path.parent.mkdir(parents=True, exist_ok=True)
        work_path.write_text(best_code)

    sv_dir = run_dir / "sv"
    sv_dir.mkdir(parents=True, exist_ok=True)
    if code:
        (sv_dir / f"{prob_id}.sv").write_text(code)

    record = {
        "prob_id": prob_id,
        "dataset": args.dataset,
        "model": model_alias(args.model),
        "harness": "cktarchon-anthropic-verilog",
        "feedback_mode": args.feedback_mode,
        "agent_error": agent_error,
        "agent_turns_total": agent_stats.turns,
        "agent_input_tokens": agent_stats.input_tokens,
        "agent_output_tokens": agent_stats.output_tokens,
        "agent_tool_counts": agent_stats.tool_counts,
        "agent_compile_checks": agent_stats.compile_checks,
        "agent_turn_budget": args.max_turns,
        "candidate_source": source,
        "sim_feedback_enabled": bool(args.sim_feedback),
        "sim_feedback_iterations": sim_feedback_iterations,
        "sim_feedback_success": sim_feedback_success,
        "sim_feedback_turn_budget": feedback_turn_budget,
        "sim_feedback_turns_remaining": feedback_turns_remaining,
        "sim_feedback_history": sim_feedback_history,
        "agent_elapsed_seconds": round(agent_elapsed, 3),
        "elapsed_seconds": round(time.monotonic() - t_problem, 3),
        "timestamp": datetime.now().isoformat(),
        **result,
    }
    return record


def update_summary(run_dir: Path, total: int, records: list[dict[str, Any]], skipped: int = 0) -> dict[str, Any]:
    summary = {
        "total": total,
        "completed": len(records),
        "skipped": skipped,
        "compile_pass": 0,
        "sim_pass": 0,
        "sim_fail": 0,
        "sim_error": 0,
        "agent_error": 0,
        "sim_feedback_attempts": 0,
        "sim_feedback_success": 0,
        "tokens_in": 0,
        "tokens_out": 0,
        "turns": 0,
    }
    for row in records:
        if row.get("compile_pass"):
            summary["compile_pass"] += 1
        status = row.get("sim_status")
        if status == "sim_pass":
            summary["sim_pass"] += 1
        elif status == "sim_fail":
            summary["sim_fail"] += 1
        elif status in {"sim_error", "compile_fail", "gen_error"}:
            summary["sim_error"] += 1
        if row.get("agent_error"):
            summary["agent_error"] += 1
        summary["sim_feedback_attempts"] += int(row.get("sim_feedback_iterations") or 0)
        if row.get("sim_feedback_success"):
            summary["sim_feedback_success"] += 1
        summary["tokens_in"] += int(row.get("agent_input_tokens") or 0)
        summary["tokens_out"] += int(row.get("agent_output_tokens") or 0)
        summary["turns"] += int(row.get("agent_turns_total") or 0)
    attempted = max(1, len(records))
    summary["avg_total_tokens"] = round((summary["tokens_in"] + summary["tokens_out"]) / attempted, 2)
    summary["avg_turns"] = round(summary["turns"] / attempted, 2)
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    return summary


def main() -> None:
    args = parse_args()
    ensure_runtime_env()
    load_env_file(Path(args.key_env))
    _add_agent_paths()

    from dataset import Dataset

    ds = Dataset(args.dataset, project_root=PROJECT_ROOT)
    problems = discover_problems(args, ds)
    run_parent = Path(args.results_dir).resolve()
    run_parent.mkdir(parents=True, exist_ok=True)
    run_dir = run_parent / f"archon_verilog_run_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    run_dir.mkdir(parents=True, exist_ok=True)

    records: list[dict[str, Any]] = []
    skipped = 0
    results_path = run_dir / "results.jsonl"
    num_workers = max(1, args.workers)
    print(
        f"CktArchon-Verilog: {len(problems)} problems, model={model_alias(args.model)}, "
        f"workers={num_workers}, run_dir={run_dir}",
        flush=True,
    )

    indexed_problems: list[tuple[int, str]] = []
    for idx, prob_id in enumerate(problems, 1):
        if args.resume and already_done(run_parent, prob_id, args.resume_mode):
            skipped += 1
            print(f"[{idx}/{len(problems)}] {prob_id}: skipped", flush=True)
        else:
            indexed_problems.append((idx, prob_id))

    def _run_one(idx: int, prob_id: str) -> tuple[int, str, dict[str, Any]]:
        print(f"[{idx}/{len(problems)}] {prob_id}: start", flush=True)
        try:
            record = process_problem(prob_id, args=args, ds=ds, run_dir=run_dir)
        except Exception as exc:
            record = {
                "prob_id": prob_id,
                "dataset": args.dataset,
                "model": model_alias(args.model),
                "harness": "cktarchon-anthropic-verilog",
                "agent_error": f"{type(exc).__name__}: {exc}",
                "compile_pass": False,
                "sim_status": "sim_error",
                "sim_mismatches": -1,
                "detail": str(exc),
                "timestamp": datetime.now().isoformat(),
            }
        return idx, prob_id, record

    try:
        with ThreadPoolExecutor(max_workers=num_workers) as executor:
            future_map = {
                executor.submit(_run_one, idx, prob_id): (idx, prob_id)
                for idx, prob_id in indexed_problems
            }
            for future in as_completed(future_map):
                idx, prob_id = future_map[future]
                _, _, record = future.result()
                records.append(record)
                append_jsonl(results_path, record)
                update_summary(run_dir, len(problems), records, skipped)
                print(
                    f"[{idx}/{len(problems)}] {prob_id}: done "
                    f"compile={record.get('compile_pass')} sim={record.get('sim_status')} "
                    f"feedback={record.get('sim_feedback_iterations')} "
                    f"tok={record.get('agent_input_tokens', 0)}+{record.get('agent_output_tokens', 0)}",
                    flush=True,
                )
    finally:
        summary = update_summary(run_dir, len(problems), records, skipped)
        print(json.dumps(summary, indent=2, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
