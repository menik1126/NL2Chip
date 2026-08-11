from __future__ import annotations

import argparse
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
ARCHON_SRC = Path("/home/sgli/work/archon-official/src")

COMPACT_SPARKLE_GENERATION_SKILL = """You are an expert hardware engineer translating natural-language RTL specifications into Sparkle HDL, a Lean 4 hardware DSL.

## Goal
Produce one Lean file that compiles, synthesizes SystemVerilog with `#synthesizeVerilog`, and is behaviorally faithful to the benchmark spec.

## File Template
```lean
import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal
open Sparkle.Library.RTL

/-- <one-line description> -/
def <target_module> {dom : DomainConfig}
    (<inputs>) : <output_type> :=
  <implementation>

#synthesizeVerilog <target_module>
```

## Core Types
- `Signal dom (BitVec N)` is an N-bit hardware signal.
- `Signal dom Bool` is a hardware condition signal.
- Clock/reset are implicit in `DomainConfig`; do not add clock/reset ports unless the benchmark spec has explicit user-visible ports.
- Multi-output Sparkle functions return tuple signals, e.g. `Signal dom (BitVec 8 × BitVec 1)` with `bundle2`.

## Stable Sparkle Operators
- Bitwise/arithmetic: `~~~a`, `a &&& b`, `a ||| b`, `a ^^^ b`, `a + b`, `a - b`, `a * b`.
- Equality: `a === b` returns `Signal dom Bool`.
- Concatenation: `a ++ b`; first operand becomes the high bits.
- Shifts: `a <<< n`, `a >>> n`, where `n` is a same-width `BitVec` literal or signal.
- Mux: `Signal.mux cond trueValue falseValue`.
- Constants: use `N#W` directly with Signal operators or `Signal.pure (N#W)`.

## RTL Helpers
Use helpers from `Sparkle.Library.RTL` when they match the task:
- `slice x lo` with a type annotation for the result width.
- `trunc x`, `zext x`.
- `bit x i`, `bitBool x i`.
- `boolToBV1 b`, `bv1ToBool x`.
- `isZero x`, `nonZero x`, `allOnes x`.
- `dff`, `dffe`, `resetHigh`, `resetLow`.
- `syncRam1R1W`, `regFile1R1W`.
- `reverseBits8/16/32`, `popCount8/16/32`, `priorityEncodeLsb8/16/32`.

## Workflow
1. Read at most two small examples, preferably `Benchmark/RTLIdioms.lean` plus one similar `Benchmark/*.lean`.
2. Write a complete candidate to the required `Generated/<prob_id>.lean` file.
3. Run the harness-provided Lean check command.
4. Fix compiler/synthesis errors from that output.
5. Stop immediately after the Lean check reports success; the outer evaluator will run lint and simulation.
"""


def _add_legacy_agent_path() -> None:
    agent_dir = PROJECT_ROOT / "agent"
    if str(agent_dir) not in sys.path:
        sys.path.insert(0, str(agent_dir))


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="CktArchon: Archon-style NL2Chip benchmark runner")
    p.add_argument("--dataset", default="cvdp", choices=["verilogeval", "rtllm", "resbench", "cvdp", "realbench"])
    p.add_argument("--problem-file", type=str, default=None)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--filter", type=str, default=None)
    p.add_argument("--model", default="claude-sonnet-4.5")
    p.add_argument("--max-turns", type=int, default=80)
    p.add_argument("--max-tokens", type=int, default=16384)
    p.add_argument("--results-dir", type=str, required=True)
    p.add_argument("--harness", default="anthropic-api", choices=["anthropic-api", "codex-agent", "archon-native"])
    p.add_argument("--key-env", default=str(PROJECT_ROOT / "key.env"))
    p.add_argument("--resume", action="store_true")
    p.add_argument("--resume-mode", choices=["passed", "completed"], default="completed")
    p.add_argument("--no-repl", action="store_true")
    p.add_argument("--eval-only", action="store_true", help="Skip agent generation and only evaluate existing Generated/<prob_id>.lean files.")
    p.add_argument("--workers", type=int, default=1, help="Reserved for compatibility; current runner executes serially for resource isolation.")
    p.add_argument("--archon-src", default=str(ARCHON_SRC), help="Official Archon src directory for codex-agent/archon-native harnesses.")
    p.add_argument("--codex-bin", default=None, help="Optional absolute path to the codex CLI for --harness codex-agent.")
    p.add_argument("--codex-effort", default=None, help="Optional model_reasoning_effort passed to codex exec.")
    p.add_argument("--codex-sandbox", default="danger-full-access", help="Codex sandbox mode.")
    p.add_argument("--codex-idle-timeout", type=float, default=900.0, help="Seconds of no JSONL activity before Archon restarts codex.")
    p.add_argument("--codex-max-attempts", type=int, default=3, help="Archon CodexAgent retry attempts after idle timeouts.")
    p.add_argument("--codex-base-url-env", default=None, help="Env var containing a Codex-compatible gateway base URL.")
    p.add_argument("--codex-key-env", default=None, help="Env var containing the Codex-compatible gateway API key.")
    p.add_argument("--codex-wire-api", default="responses", choices=["responses", "chat"], help="Codex custom-provider wire API.")
    p.add_argument("--no-codex-chat-proxy", action="store_true", help="Disable the local Responses-to-Chat proxy used when no Codex gateway is configured.")
    p.add_argument("--api-timeout", type=float, default=300.0, help="Per-request timeout for --harness anthropic-api.")
    p.add_argument("--sim-feedback", action="store_true", help="Repair Lean after RTL compile/simulation failures using compact simulator feedback.")
    p.add_argument("--sim-feedback-max-iters", type=int, default=3, help="Max simulation-feedback repair attempts per problem.")
    p.add_argument("--sim-feedback-turn-budget", type=int, default=None, help="Total extra agent turns available for simulation-feedback repairs per problem. Defaults to the old shared remaining budget.")
    p.add_argument("--sim-feedback-turns-per-iter", type=int, default=None, help="Max agent turns for each individual simulation-feedback repair attempt.")
    p.add_argument("--sim-feedback-patience", type=int, default=2, help="Stop after this many non-improving feedback repairs; set 0 to disable.")
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
                if mode == "completed" and (not agent_error or "max-turns budget" in agent_error):
                    return True
        except Exception:
            continue
    return False


def clear_generated_target(project_root: Path, run_dir: Path, prob_id: str) -> str | None:
    target = project_root / "Generated" / f"{prob_id}.lean"
    if not target.exists():
        return None
    backup_dir = run_dir / "preexisting_generated"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / f"{prob_id}.lean"
    if backup.exists():
        backup = backup_dir / f"{prob_id}_{time.time_ns()}.lean"
    shutil.copy2(target, backup)
    target.unlink()
    return str(backup)


def build_system_prompt(skill: str, prob_id: str, info: Any | None = None) -> str:
    design_name = getattr(info, "design_name", None) if info is not None else None
    design_rule = ""
    if design_name:
        design_rule = (
            f"- The output file is `Generated/{prob_id}.lean`, but the Lean function/top module must be `{design_name}`.\n"
            f"- Use `#synthesizeVerilog {design_name}`. Do not name the synthesized function `{prob_id}` unless the problem explicitly says that is the target module.\n"
        )
    return (
        COMPACT_SPARKLE_GENERATION_SKILL.rstrip()
        + "\n\n## CktArchon harness rules\n"
        + f"- You are running under the Archon-style CktArchon harness for `{prob_id}`.\n"
        + f"- Write only `Generated/{prob_id}.lean` using the `write_file`/`edit_file` tools.\n"
        + design_rule
        + "- Treat the user's `Benchmark Interface Contract` as authoritative over guesses from examples or file names.\n"
        + "- For CVDP parameters, inspect the listed sweep values and make the implementation work across those values; do not create fake Verilog parameters around a fixed-width Sparkle core.\n"
        + "- Match benchmark output names exactly. If you must return a packed output internally, construct an explicit named MSB-to-LSB concat so the CVDP wrapper can recover each output field.\n"
        + "- Preserve benchmark clock, reset polarity, and cycle latency exactly; the cocotb harness checks protocol timing, not just combinational truth tables.\n"
        + "- Use `lean_check` frequently; it uses the persistent Lean REPL when available.\n"
        + "- Keep repository exploration short: read at most three examples, then write a complete candidate and iterate from compiler feedback.\n"
        + "- This H20 host may not have `rg`; use `grep` and `find` for repository searches.\n"
        + "- If you need a directory listing, use `list_directory`; do not call `read_file` on directories.\n"
        + "- The key Sparkle synthesis rules are included below. Do not `cat` all of `docs/Troubleshooting_Synthesis.md`; if you need more detail, use `grep` for a narrow pattern.\n"
        + "- Prefer simple `Signal dom ... -> Signal dom ...` combinational helpers. Avoid pure helper functions with `match`, `if`, tuples, or recursion when their result depends on hardware signals.\n"
        + "- Use `Signal.mux` for all data-dependent choices. Sparkle cannot synthesize Lean `if`/`match`/`ite` over signal-derived values or inside `Signal.map` lambdas.\n"
        + "- Use supported binary Signal operators directly (`+`, `-`, `*`, `&&&`, `|||`, `^^^`, `===`, `++`, `<<<`, `>>>`). Avoid multi-argument lambdas such as `(fun a b => ...) <$> x <*> y`.\n"
        + "- For Signal shifts, the shift amount must be a same-width `BitVec` literal or Signal, e.g. `x >>> 1#8` for `Signal dom (BitVec 8)` and `x <<< 2#16` for `Signal dom (BitVec 16)`. Never write `x >>> 1` or an undersized amount such as `x >>> 1#3`.\n"
        + "- For comparisons other than equality, use Sparkle helpers such as `Signal.ult` or `Signal.slt` with same-width operands; do not use Lean `<`, `>`, `<=`, or `>=` on `Signal` values.\n"
        + "- For `Signal dom Bool`, avoid Lean `||` and `&&` over signals. Implement OR as `Signal.mux a (Signal.pure true) b` and AND as `Signal.mux a b (Signal.pure false)` unless a checked local example shows a better pattern.\n"
        + "- For tuple-valued signals, project with `.fst`/`.snd` or `projN!`; do not destructure with `let (a, b) := ...` in synthesizable code.\n"
        + "- For packed `BitVec` outputs, prefer explicit `++` concatenation of sized Signal operands. Do not use `bundleAll!` to build a packed bit-vector result.\n"
        + "- For RTL bit manipulation, use `Sparkle.Library.RTL` helpers such as `bit`, `slice`, `zext`, and `trunc` when a local checked example confirms the expected type.\n"
        + "- Do not leave placeholders such as `sorry`, `admit`, or dummy zero outputs in the synthesized implementation.\n"
        + "- Do not modify benchmark sources, Sparkle library code, or other Generated files.\n"
        + "- Stop once the generated Lean file compiles; the CktArchon evaluator will run Verilog extraction, lint, and simulation.\n"
    )


def run_archon_native_unavailable() -> None:
    # The shape is explicit so future CLI-backed Archon runners can plug in here.
    if str(ARCHON_SRC) not in sys.path:
        sys.path.insert(0, str(ARCHON_SRC))
    try:
        from archon.agent import build_runner  # noqa: F401
    except Exception as exc:
        raise RuntimeError(f"Official Archon import failed: {exc}") from exc
    raise RuntimeError(
        "archon-native harness requested, but this H20 image has no `claude`/`codex` CLI. "
        "Use --harness anthropic-api now, or install the native CLI and wire this adapter."
    )


def make_runner(
    *,
    args: argparse.Namespace,
    prob_id: str,
    role: str,
    log_base: Path,
    skill: str,
    info: Any | None,
    repl: Any | None,
) -> Any:
    if args.harness == "archon-native":
        run_archon_native_unavailable()
    if args.harness == "codex-agent":
        from .codex_runner import CodexAgentHarnessRunner

        return CodexAgentHarnessRunner(
            project_root=PROJECT_ROOT,
            prob_id=prob_id,
            model=model_alias(args.model),
            role=role,
            log_base=log_base,
            system_prompt=build_system_prompt(skill, prob_id, info),
            archon_src=Path(args.archon_src),
            codex_bin=args.codex_bin,
            effort=args.codex_effort,
            sandbox=args.codex_sandbox,
            idle_timeout_s=args.codex_idle_timeout,
            max_attempts=args.codex_max_attempts,
            base_url_env=args.codex_base_url_env,
            key_env=args.codex_key_env,
            wire_api=args.codex_wire_api,
            auto_chat_proxy=not args.no_codex_chat_proxy,
            chat_proxy_timeout_s=args.api_timeout,
        )
    return AnthropicHarnessRunner(
        project_root=PROJECT_ROOT,
        prob_id=prob_id,
        model=model_alias(args.model),
        role=role,
        log_base=log_base,
        system_prompt=build_system_prompt(skill, prob_id, info),
        max_tokens=args.max_tokens,
        lean_repl=repl,
        api_timeout=args.api_timeout,
    )


def merge_agent_stats(total: AgentStats, extra: AgentStats) -> None:
    total.input_tokens += extra.input_tokens
    total.output_tokens += extra.output_tokens
    total.turns += extra.turns
    total.compile_checks += extra.compile_checks
    for name, count in extra.tool_counts.items():
        total.tool_counts[name] = total.tool_counts.get(name, 0) + count


def process_problem(
    prob_id: str,
    *,
    args: argparse.Namespace,
    ds: Any,
    evaluator: Any,
    run_dir: Path,
    skill: str,
    repl: Any | None,
) -> dict[str, Any]:
    _add_legacy_agent_path()
    import search

    problem_t0 = time.monotonic()
    info = ds.load_problem(prob_id)
    has_repl = repl is not None
    agent_stats = AgentStats()
    repair_stats_total = AgentStats()
    agent_elapsed = 0.0
    agent_error: str | None = None
    preexisting_generated_backup: str | None = None
    sim_feedback_history: list[dict[str, Any]] = []
    sim_feedback_turn_budget = 0
    sim_feedback_turns_remaining = 0
    sim_feedback_success = False
    sim_feedback_iterations = 0

    if not args.eval_only:
        preexisting_generated_backup = clear_generated_target(PROJECT_ROOT, run_dir, prob_id)
        user_message = search.build_user_message(
            prob_id,
            has_repl=has_repl,
            info=info,
            dataset_name=evaluator.dataset_name,
            condition_sv=None,
        )
        log_base = run_dir / "logs" / prob_id / "generate"
        runner = make_runner(
            args=args,
            prob_id=prob_id,
            role="ckt-generator",
            log_base=log_base,
            skill=skill,
            info=info,
            repl=repl,
        )
        try:
            t0 = time.monotonic()
            agent_stats = runner.run(user_message, max_turns=args.max_turns)
            agent_elapsed = time.monotonic() - t0
        except Exception as exc:
            agent_error = f"{type(exc).__name__}: {exc}"
            parsed_stats = parse_agent_log(Path(str(log_base) + ".jsonl"))
            if parsed_stats.turns or parsed_stats.input_tokens or parsed_stats.output_tokens or parsed_stats.tool_counts:
                agent_stats = parsed_stats

    eval_t0 = time.monotonic()
    generated_target = PROJECT_ROOT / "Generated" / f"{prob_id}.lean"
    if agent_error and generated_target.exists():
        result = evaluator.evaluate(prob_id, run_dir)
        result["agent_error"] = agent_error
        if result.get("detail"):
            result["detail"] = f"Agent ended with {agent_error}; evaluated generated file anyway.\n{result['detail']}"
        else:
            result["detail"] = f"Agent ended with {agent_error}; evaluated generated file anyway."
    elif agent_error:
        result = {
            "prob_id": prob_id,
            "compile_pass": False,
            "sv_extracted": False,
            "lint_pass": False,
            "sim_status": "agent_error",
            "sim_mismatches": -1,
            "detail": agent_error,
            "agent_error": agent_error,
        }
    else:
        result = evaluator.evaluate(prob_id, run_dir)
    eval_elapsed = time.monotonic() - eval_t0

    if (
        args.sim_feedback
        and not args.eval_only
        and not agent_error
        and result.get("sim_status") != "sim_pass"
        and generated_target.exists()
    ):
        best_result = result
        best_code = generated_target.read_text(errors="replace")
        if args.sim_feedback_turn_budget is None:
            sim_feedback_turn_budget = max(0, args.max_turns - agent_stats.turns)
        else:
            sim_feedback_turn_budget = max(0, args.sim_feedback_turn_budget)
        sim_feedback_turns_remaining = sim_feedback_turn_budget
        non_improving_repairs = 0
        sim_iter = 0
        while (
            result.get("sim_status") != "sim_pass"
            and sim_feedback_turns_remaining > 0
            and sim_iter < max(0, args.sim_feedback_max_iters)
        ):
            sim_iter += 1
            sim_feedback_iterations = sim_iter
            current_code = generated_target.read_text(errors="replace")
            feedback = search.build_sim_feedback(
                prob_id=prob_id,
                result=result,
                iteration=sim_iter - 1,
                history=sim_feedback_history,
                run_dir=run_dir,
                info=info,
            )
            compact_prompt = search.build_compact_repair_prompt(
                prob_id=prob_id,
                info=info,
                dataset_name=evaluator.dataset_name,
                has_repl=has_repl,
                phase="RTL simulation feedback",
                iteration=sim_iter,
                current_lean=current_code,
                latest_feedback=feedback,
                recent_attempts=sim_feedback_history,
                extra_constraints=(
                    "Use the Verilog compile/simulation diagnostics to repair the Lean source. "
                    "Before ending, ensure the final Lean file compiles; the outer evaluator will rerun RTL simulation."
                ),
            )
            repair_log_base = run_dir / "logs" / prob_id / f"sim_feedback_iter_{sim_iter}"
            repair_runner = make_runner(
                args=args,
                prob_id=prob_id,
                role="ckt-sim-repair",
                log_base=repair_log_base,
                skill=skill,
                info=info,
                repl=repl,
            )
            try:
                repair_t0 = time.monotonic()
                repair_turn_limit = sim_feedback_turns_remaining
                if args.sim_feedback_turns_per_iter is not None:
                    repair_turn_limit = min(repair_turn_limit, max(0, args.sim_feedback_turns_per_iter))
                if repair_turn_limit <= 0:
                    break
                repair_stats = repair_runner.run(compact_prompt, max_turns=repair_turn_limit)
                repair_elapsed = time.monotonic() - repair_t0
                merge_agent_stats(repair_stats_total, repair_stats)
                agent_elapsed += repair_elapsed
                sim_feedback_turns_remaining = max(0, sim_feedback_turns_remaining - repair_stats.turns)
            except Exception as exc:
                sim_feedback_history.append({
                    "phase": "sim_feedback",
                    "iteration": sim_iter,
                    "note": f"Agent error during simulation repair: {type(exc).__name__}: {exc}",
                    "remaining_turns": sim_feedback_turns_remaining,
                })
                break

            repair_eval_t0 = time.monotonic()
            new_result = evaluator.evaluate(prob_id, run_dir)
            eval_elapsed += time.monotonic() - repair_eval_t0
            new_key = search.eval_progress_key(new_result)
            best_key = search.eval_progress_key(best_result)
            improved = new_key > best_key
            sim_feedback_history.append({
                "phase": "sim_feedback",
                "iteration": sim_iter,
                "note": "Candidate result after RTL simulation feedback repair.",
                "result_summary": search.summarize_eval_result(new_result),
                "improved_best": improved,
                "repair_turns": repair_stats.turns,
                "repair_turn_limit": repair_turn_limit,
                "remaining_turns": sim_feedback_turns_remaining,
                "repair_input_tokens": repair_stats.input_tokens,
                "repair_output_tokens": repair_stats.output_tokens,
                "repair_compile_checks": repair_stats.compile_checks,
            })
            append_jsonl(run_dir / "events.jsonl", {
                "prob_id": prob_id,
                "event": "sim_feedback",
                "iteration": sim_iter,
                "sim_status": new_result.get("sim_status"),
                "compile_pass": new_result.get("compile_pass"),
                "lint_pass": new_result.get("lint_pass"),
                "remaining_turns": sim_feedback_turns_remaining,
                "improved_best": improved,
            })

            if improved:
                best_result = new_result
                best_code = generated_target.read_text(errors="replace")
                result = new_result
                non_improving_repairs = 0
            else:
                generated_target.write_text(best_code, encoding="utf-8")
                result = best_result
                non_improving_repairs += 1

            if result.get("sim_status") == "sim_pass":
                sim_feedback_success = True
                break
            if args.sim_feedback_patience > 0 and non_improving_repairs >= args.sim_feedback_patience:
                sim_feedback_history.append({
                    "phase": "sim_feedback",
                    "iteration": sim_iter,
                    "note": f"Stopped: {args.sim_feedback_patience} consecutive simulation-feedback repairs did not improve the best candidate.",
                    "best_result_summary": search.summarize_eval_result(best_result),
                })
                break

        if generated_target.exists() and search.eval_progress_key(best_result) >= search.eval_progress_key(result):
            generated_target.write_text(best_code, encoding="utf-8")
            result = best_result

    record = {
        "prob_id": prob_id,
        "agent_turns": agent_stats.turns,
        "agent_input_tokens": agent_stats.input_tokens + repair_stats_total.input_tokens,
        "agent_output_tokens": agent_stats.output_tokens + repair_stats_total.output_tokens,
        "agent_compile_checks": agent_stats.compile_checks + repair_stats_total.compile_checks,
        "agent_tool_counts": {
            **agent_stats.tool_counts,
            **{
                name: agent_stats.tool_counts.get(name, 0) + count
                for name, count in repair_stats_total.tool_counts.items()
            },
        },
        "agent_turn_budget": args.max_turns,
        "agent_generation_turns": agent_stats.turns,
        "agent_turns_total": agent_stats.turns + repair_stats_total.turns,
        "sim_feedback_enabled": bool(args.sim_feedback),
        "sim_feedback_iterations": sim_feedback_iterations,
        "sim_feedback_success": sim_feedback_success,
        "sim_feedback_turn_budget": sim_feedback_turn_budget,
        "sim_feedback_turns_remaining": sim_feedback_turns_remaining,
        "sim_feedback_history": sim_feedback_history,
        "agent_elapsed_seconds": round(agent_elapsed, 3),
        "eval_elapsed_seconds": round(eval_elapsed, 3),
        "elapsed_seconds": round(time.monotonic() - problem_t0, 3),
        "harness": args.harness,
        "model": model_alias(args.model),
        "timestamp": datetime.now().isoformat(),
    }
    if preexisting_generated_backup:
        record["preexisting_generated_backup"] = preexisting_generated_backup
    try:
        record.update(search.classify_failure_record(result))
    except Exception:
        pass
    record.update(result)
    append_jsonl(run_dir / "results.jsonl", record)
    return record


def main() -> None:
    args = parse_args()
    load_env_file(Path(args.key_env))
    ensure_runtime_env()
    _add_legacy_agent_path()
    from dataset import Dataset
    from evaluator import Evaluator
    import search

    ds = Dataset(args.dataset, project_root=PROJECT_ROOT)
    problems = discover_problems(args, ds)
    if not problems:
        raise SystemExit("No problems selected")

    results_base = Path(args.results_dir).resolve()
    run_dir = results_base / f"cktarchon_run_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    run_dir.mkdir(parents=True, exist_ok=True)
    (PROJECT_ROOT / "Generated").mkdir(exist_ok=True)

    if args.workers != 1:
        print("[cktarchon] workers>1 requested; running serial in this first Archon-harness adapter.")
    print(f"[cktarchon] problems={len(problems)} model={model_alias(args.model)} harness={args.harness} run_dir={run_dir}")

    repl = None
    if not args.no_repl:
        try:
            from lean_repl import LeanREPLPool
            pool = LeanREPLPool(size=1, project_dir=PROJECT_ROOT)
            repl = pool.acquire()
        except Exception as exc:
            print(f"[cktarchon] Lean REPL unavailable, falling back to lake build: {exc}")
            pool = None
    else:
        pool = None

    evaluator = Evaluator(project_root=PROJECT_ROOT, dataset=args.dataset, dataset_obj=ds, lean_repl=repl)
    skill = search.load_skill()
    summary = {"total": len(problems), "skipped": 0, "compile_pass": 0, "sim_pass": 0, "sim_fail": 0, "sim_error": 0, "agent_error": 0, "sim_feedback_attempts": 0, "sim_feedback_success": 0}
    try:
        for idx, prob_id in enumerate(problems, 1):
            if args.resume and already_done(results_base, prob_id, args.resume_mode):
                summary["skipped"] += 1
                print(f"[{idx}/{len(problems)}] skip {prob_id}")
                continue
            print(f"[{idx}/{len(problems)}] run {prob_id}")
            record = process_problem(prob_id, args=args, ds=ds, evaluator=evaluator, run_dir=run_dir, skill=skill, repl=repl)
            if record.get("compile_pass"):
                summary["compile_pass"] += 1
            summary["sim_feedback_attempts"] += int(record.get("sim_feedback_iterations") or 0)
            if record.get("sim_feedback_success"):
                summary["sim_feedback_success"] += 1
            status = record.get("sim_status")
            if status == "sim_pass":
                summary["sim_pass"] += 1
            elif status == "sim_fail":
                summary["sim_fail"] += 1
            elif status == "agent_error":
                summary["agent_error"] += 1
            else:
                summary["sim_error"] += 1
            print(f"    compile={record.get('compile_pass')} lint={record.get('lint_pass')} sim={status} turns={record.get('agent_turns_total')} tok={record.get('agent_input_tokens')}+{record.get('agent_output_tokens')}")
    finally:
        if pool is not None and repl is not None:
            pool.release(repl)
            pool.close_all()

    summary.update({"dataset": args.dataset, "model": model_alias(args.model), "harness": args.harness, "run_dir": str(run_dir)})
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
