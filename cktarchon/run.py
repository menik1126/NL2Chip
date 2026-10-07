from __future__ import annotations

import argparse
import copy
import hashlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
import json
import os
import re
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from .env import ensure_runtime_env, load_env_file, model_alias
from .harness import AnthropicHarnessRunner, AnthropicTextRunner
from .logs import AgentStats, append_jsonl, parse_agent_log
from .search_strategy import (
    CandidateTracker,
    SelfTestGuide,
    SelfTestResult,
    TurnBudget,
    build_self_test_planner_prompt,
    format_self_test_guidance,
    parse_self_test_guide,
    run_generated_self_test,
    validate_self_test_guide,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
from . import public_prompt
ARCHON_SRC = Path(os.environ.get("ARCHON_SRC") or PROJECT_ROOT.parent / "archon-official" / "src")
CVDP_HARNESS_PROFILES = ("official", "race-safe-v1")
CVDP_HARNESS_PROFILE_DEFAULT = "race-safe-v1"

COMPACT_SPARKLE_GENERATION_SKILL = """You are an expert hardware engineer translating natural-language RTL specifications into Sparkle HDL, a Lean 4 hardware DSL.

## Goal
Produce one Lean file that compiles, synthesizes SystemVerilog with the synthesis command required by the parameter contract, and is behaviorally faithful to the benchmark spec.

## File Template
```lean
import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal
open Sparkle.Core.Circuit
open Sparkle.Library.RTL

/-- <one-line description> -/
def <target_module> {dom : DomainConfig}
    (<inputs>) : <output_type> :=
  <implementation>

#synthesizeVerilog <target_module>  -- use the parameterized form for retained Nat dimensions
```

## Core Types
- `Signal dom (BitVec N)` is an N-bit hardware signal.
- `Signal dom Bool` is a hardware condition signal.
- Clock/reset are implicit in `DomainConfig`; do not add clock/reset ports unless the benchmark spec has explicit user-visible ports.
- Multi-output Sparkle functions return tuple signals, e.g. `Signal dom (BitVec 8 × BitVec 1)` with `bundle2`.

## First-Class Multi-Domain Circuits
- If the specification has two or more physical clocks, define one concrete `DomainConfig` per clock. Set `clockName`, `resetName`, `activeEdge`, and `resetKind` to the benchmark contract. Do not pass clocks as ordinary `Signal` arguments and do not collapse them into one domain.
- Return `Circuit` when public outputs belong to different domains. Name outputs with `Circuit.ofOutputs`, `.bool`, and `.bits`; these names become SystemVerilog ports.
- Use audited crossings only: `Signal.synchronizeLevel`, `Signal.synchronizePulse`, `Signal.synchronizePulseVector`, and `Signal.resetSynchronizer`.
- For a power-of-two asynchronous FIFO, use `Circuit.asyncFifo DEPTH "w_full" "r_data" "r_empty" w_inc w_data r_inc`; it lowers to Gray pointers, two-stage pointer CDC, and dual-domain FWFT storage.
- For retained parameters, use `#synthesizeParameterizedVerilog <target> [WIDTH := <legal-default>, DEPTH := <legal-default>]`. Do not specialize a parameter sweep to one fixed width.

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
    p.add_argument(
        "--cvdp-harness-profile",
        choices=CVDP_HARNESS_PROFILES,
        default=CVDP_HARNESS_PROFILE_DEFAULT,
        help=(
            "Versioned CVDP testbench scheduling profile. Use `official` only "
            "for the unchanged-harness ablation."
        ),
    )
    p.add_argument("--problem-file", type=str, default=None)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--filter", type=str, default=None)
    p.add_argument("--model", default="claude-sonnet-4.5")
    p.add_argument("--max-turns", type=int, default=80)
    p.add_argument(
        "--total-turn-budget",
        type=int,
        default=None,
        help=(
            "Total turns shared by ordinary initial generation and simulation-feedback "
            "repairs. Requires --generation-turn-cap and cannot be combined with "
            "--sim-feedback-turn-budget."
        ),
    )
    p.add_argument(
        "--generation-turn-cap",
        type=int,
        default=None,
        help=(
            "Maximum initial-generation turns under --total-turn-budget; every unused "
            "generation turn remains available to simulation-feedback repairs."
        ),
    )
    p.add_argument("--max-tokens", type=int, default=16384)
    p.add_argument("--interface-prompt-policy", choices=["legacy", public_prompt.POLICY], default="legacy")
    p.add_argument(
        "--disable-public-structural-repair",
        action="store_true",
        help=(
            "Disable the public-spec structural checker and its repair trigger. "
            "This preserves public prompts and compile-only feedback for ablations."
        ),
    )
    p.add_argument(
        "--prompt-profile",
        choices=["compact", "cvdp-skill-fewshot"],
        default="compact",
        help=(
            "Lean generation prompt profile. `compact` preserves the historical "
            "prompt; `cvdp-skill-fewshot` adds the curated Sparkle skill pack and "
            "non-evaluation few-shot examples."
        ),
    )
    p.add_argument("--results-dir", type=str, required=True)
    p.add_argument("--harness", default="anthropic-api", choices=["anthropic-api", "codex-agent", "archon-native"])
    p.add_argument("--key-env", default=str(PROJECT_ROOT / "key.env"))
    p.add_argument("--resume", action="store_true")
    p.add_argument("--resume-mode", choices=["passed", "completed"], default="completed")
    p.add_argument("--no-repl", action="store_true")
    p.add_argument("--eval-only", action="store_true", help="Skip agent generation and only evaluate existing Generated/<prob_id>.lean files.")
    p.add_argument(
        "--hide-cvdp-harness-from-agent",
        action="store_true",
        help=(
            "Expose only CVDP input.prompt/input.context to generation and repair agents. "
            "The evaluator still receives the complete benchmark row and hidden harness."
        ),
    )
    parameter_mode = p.add_mutually_exclusive_group()
    parameter_mode.add_argument(
        "--finite-parameter-specialization",
        action="store_true",
        help=(
            "Enable the P0 CVDP path: synthesize every finite public parameter "
            "combination as a concrete Sparkle module and generate a selector wrapper."
        ),
    )
    parameter_mode.add_argument(
        "--native-parameter-sweep",
        action="store_true",
        help=(
            "Enable the P3 CVDP path: emit one native generic SystemVerilog DUT, "
            "verify parameter propagation, and elaborate every public sweep case."
        ),
    )
    p.add_argument(
        "--native-formal-policy",
        choices=["off", "auto", "generic", "per_configuration"],
        default="auto",
        help=(
            "Formal coverage policy for a native parameter family. `auto` uses an "
            "explicit formal_parameter_contract when available and otherwise reports "
            "unsupported without treating Lean elaboration as a functional proof."
        ),
    )
    p.add_argument(
        "--native-cppsim-policy",
        choices=["off", "per_configuration"],
        default="per_configuration",
        help=(
            "CppSim policy for native parameter families. The supported mode "
            "specializes, compiles, and smoke-runs one concrete C++ model per public case."
        ),
    )
    p.add_argument(
        "--require-native-cppsim",
        action="store_true",
        help="Fail evaluation when CppSim cannot cover every public parameter case.",
    )
    p.add_argument(
        "--native-ppa-policy",
        choices=["off", "per_configuration"],
        default="per_configuration",
        help=(
            "PPA policy used with --synth for native parameter families. "
            "The supported mode binds and synthesizes every public configuration independently."
        ),
    )
    p.add_argument(
        "--require-native-ppa",
        action="store_true",
        help="Fail evaluation unless every requested PPA stage covers every public case.",
    )
    p.add_argument("--synth", action="store_true", help="Run ORFS synthesis and collect per-configuration area/cell metrics.")
    p.add_argument("--pnr", action="store_true", help="Run per-configuration place and route; implies synthesis.")
    p.add_argument("--drc", action="store_true", help="Run per-configuration DRC; implies place and route.")
    p.add_argument("--lvs", action="store_true", help="Run per-configuration LVS; implies place and route.")
    p.add_argument("--corners", action="store_true", help="Run multi-corner STA for every routed configuration; implies place and route.")
    p.add_argument("--workers", type=int, default=1, help="Concurrent problem workers; each receives an isolated Lean REPL.")
    p.add_argument("--archon-src", default=str(ARCHON_SRC), help="Official Archon src directory for codex-agent/archon-native harnesses.")
    p.add_argument("--codex-bin", default=None, help="Optional absolute path to the codex CLI for --harness codex-agent.")
    p.add_argument("--codex-effort", default=None, help="Optional model_reasoning_effort passed to codex exec.")
    p.add_argument("--codex-sandbox", default="danger-full-access", help="Codex sandbox mode.")
    p.add_argument(
        "--codex-agent-user",
        default=None,
        help="Optional unprivileged OS user used to execute Codex tool calls.",
    )
    p.add_argument("--codex-idle-timeout", type=float, default=900.0, help="Seconds of no JSONL activity before Archon restarts codex.")
    p.add_argument("--codex-max-attempts", type=int, default=3, help="Archon CodexAgent retry attempts after idle timeouts.")
    p.add_argument("--codex-base-url-env", default=None, help="Env var containing a Codex-compatible gateway base URL.")
    p.add_argument("--codex-key-env", default=None, help="Env var containing the Codex-compatible gateway API key.")
    p.add_argument("--codex-wire-api", default="responses", choices=["responses", "chat"], help="Codex custom-provider wire API.")
    p.add_argument("--no-codex-chat-proxy", action="store_true", help="Disable the local Responses-to-Chat proxy used when no Codex gateway is configured.")
    p.add_argument("--api-timeout", type=float, default=300.0, help="Per-request timeout for --harness anthropic-api.")
    p.add_argument(
        "--pre-sim-semantic-repair",
        action="store_true",
        help=(
            "After compile/extract, compare generated Lean to the public problem spec "
            "and repair Lean on clear deviation. Shares leftover turns with compile-only "
            "feedback; pass through when already aligned. Hidden testbenches are not used."
        ),
    )
    p.add_argument("--sim-feedback", action="store_true", help="Repair Lean after RTL compile/simulation failures using compact simulator feedback.")
    p.add_argument(
        "--feedback-mode",
        choices=["compile-sim", "compile-only"],
        default="compile-sim",
        help="Choose whether repair may consume simulation diagnostics or only pre-simulation compile/elaboration diagnostics.",
    )
    p.add_argument("--sim-feedback-max-iters", type=int, default=3, help="Max simulation-feedback repair attempts per problem.")
    p.add_argument("--sim-feedback-turn-budget", type=int, default=None, help="Total extra agent turns available for simulation-feedback repairs per problem. Defaults to the old shared remaining budget.")
    p.add_argument("--sim-feedback-turns-per-iter", type=int, default=None, help="Max agent turns for each individual simulation-feedback repair attempt.")
    p.add_argument("--sim-feedback-patience", type=int, default=2, help="Stop after this many non-improving feedback repairs; set 0 to disable.")
    p.add_argument(
        "--sim-feedback-rewrite-patience",
        type=int,
        default=0,
        help=(
            "After this many non-improving ordinary feedback repairs, clear the active "
            "Lean candidate and start a fresh architecture. Set 0 to disable rewrites."
        ),
    )
    p.add_argument(
        "--sim-feedback-max-candidates",
        type=int,
        default=3,
        help="Maximum independent candidate lineages used by ordinary simulation feedback.",
    )
    p.add_argument(
        "--guided-search",
        action="store_true",
        help=(
            "Enable public-spec self-test guidance plus stagnation-triggered fresh candidates. "
            "All planner/generation/repair calls share one turn budget."
        ),
    )
    p.add_argument(
        "--search-total-turn-budget",
        type=int,
        default=None,
        help=(
            "Total turns shared by self-test planning, initial generation, repairs, and fresh candidates. "
            "Defaults to max-turns plus an explicit sim-feedback-turn-budget."
        ),
    )
    p.add_argument("--self-test-planner-turns", type=int, default=1, help="Turns reserved from the shared budget for public-spec self-test planning.")
    p.add_argument(
        "--disable-guided-self-test",
        action="store_true",
        help="Disable public-spec test planning, advisory TB execution, and self-test feedback while retaining guided candidate search.",
    )
    p.add_argument("--candidate-search-max", type=int, default=3, help="Maximum independent Lean candidate lineages in guided search.")
    p.add_argument("--guided-self-test-mode", choices=["guidance", "execute"], default="guidance", help="Use one public-spec TB as prompt guidance only (default), or run the legacy advisory-TB execution ablation.")
    p.add_argument("--candidate-stagnation-patience", type=int, default=2, help="Start a fresh candidate after this many non-improving attempts.")
    args = p.parse_args()
    if args.total_turn_budget is not None:
        if not args.sim_feedback:
            p.error("--total-turn-budget requires --sim-feedback")
        if args.generation_turn_cap is None:
            p.error("--total-turn-budget requires --generation-turn-cap")
        if args.sim_feedback_turn_budget is not None:
            p.error("--total-turn-budget cannot be combined with --sim-feedback-turn-budget")
        if args.guided_search:
            p.error("--total-turn-budget cannot be combined with --guided-search; use --search-total-turn-budget")
        if args.total_turn_budget < 0:
            p.error("--total-turn-budget must be non-negative")
        if args.generation_turn_cap < 0:
            p.error("--generation-turn-cap must be non-negative")
        if args.generation_turn_cap > args.total_turn_budget:
            p.error("--generation-turn-cap cannot exceed --total-turn-budget")
    elif args.generation_turn_cap is not None:
        p.error("--generation-turn-cap requires --total-turn-budget")
    if args.require_native_ppa and not args.native_parameter_sweep:
        p.error("--require-native-ppa requires --native-parameter-sweep")
    if args.require_native_ppa and args.native_ppa_policy == "off":
        p.error("--require-native-ppa is incompatible with --native-ppa-policy off")
    if args.require_native_ppa and not any(
        (args.synth, args.pnr, args.drc, args.lvs, args.corners)
    ):
        p.error("--require-native-ppa requires --synth or a later physical-design stage")
    if args.sim_feedback_rewrite_patience < 0:
        p.error("--sim-feedback-rewrite-patience must be non-negative")
    if args.sim_feedback_rewrite_patience > 0:
        if not args.sim_feedback:
            p.error("--sim-feedback-rewrite-patience requires --sim-feedback")
        if not args.guided_search:
            args.guided_search = True
            args.disable_guided_self_test = True
            args.candidate_stagnation_patience = args.sim_feedback_rewrite_patience
            args.candidate_search_max = args.sim_feedback_max_candidates
    return args


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


def agent_visible_problem_info(info: Any, *, hide_cvdp_harness: bool) -> Any:
    """Return a public-only CVDP view for every model-facing prompt."""
    metadata = dict(getattr(info, "metadata", {}) or {})
    if not hide_cvdp_harness or metadata.get("dataset") != "cvdp":
        return info

    public_info = copy.copy(info)
    input_context = metadata.get("input_context_files", {}) or {}
    public_ref_chunks = []
    if isinstance(input_context, dict):
        for rel_path, code in input_context.items():
            if str(code).strip():
                public_ref_chunks.append(f"// Context file: {rel_path}\n{code}")
    public_info.ref_code = (
        "\n\n".join(public_ref_chunks)
        if public_ref_chunks
        else "(no public reference Verilog available)"
    )
    for private_key in ("harness_files", "cvdp_row", "verilog_sources"):
        metadata.pop(private_key, None)
    metadata["agent_input_policy"] = "cvdp-public-only"
    public_info.metadata = metadata
    return public_info


def evaluate_with_infrastructure_retries(
    evaluator: Any,
    prob_id: str,
    run_dir: Path,
    info: Any,
    *,
    max_attempts: int = 3,
    skip_sim: bool = False,
) -> dict[str, Any]:
    """Retry evaluator-only infrastructure failures without spending model turns."""
    result: dict[str, Any] = {}
    attempts = max(1, int(max_attempts))
    for attempt in range(1, attempts + 1):
        result = evaluator.evaluate(
            prob_id, run_dir, problem_info=info, skip_sim=skip_sim
        )
        result["infrastructure_eval_attempts"] = attempt
        if result.get("failure_stage") != "infrastructure":
            return result
        append_jsonl(run_dir / "events.jsonl", {
            "prob_id": prob_id,
            "event": "evaluator_infrastructure_retry",
            "attempt": attempt,
            "max_attempts": attempts,
            "detail": str(result.get("detail") or "")[:1000],
        })
        if attempt < attempts:
            time.sleep(0.5 * attempt)
    return result


def build_system_prompt(
    skill: str,
    prob_id: str,
    info: Any | None = None,
    prompt_profile: str = "compact",
) -> str:
    public_spec = public_prompt.enabled(info)
    design_name = getattr(info, "design_name", None) if info is not None else None
    specialization_plan = (
        (getattr(info, "metadata", {}) or {}).get("finite_parameter_plan")
        if info is not None else None
    )
    native_plan = (
        (getattr(info, "metadata", {}) or {}).get("native_parameter_sweep_plan")
        if info is not None else None
    )
    design_rule = ""
    if specialization_plan:
        module_names = [row["module_name"] for row in specialization_plan.get("cases", [])]
        design_rule = (
            f"- The output file is `Generated/{prob_id}.lean`. P0 finite specialization is active.\n"
            f"- Reserve benchmark top name `{design_name}` for the evaluator-generated selector; do not define or synthesize it in Lean.\n"
            f"- Define and synthesize every concrete module listed in the P0 contract ({len(module_names)} total).\n"
            "- A Lean check is complete only when generated Verilog contains every required concrete module.\n"
        )
    elif native_plan:
        parameter_names = native_plan.get("parameter_names", [])
        derived_expressions = dict(
            native_plan.get("derived_parameter_expressions") or {}
        )
        derived_rule = ""
        if derived_expressions:
            rendered = ", ".join(
                f"{name} = {expression}"
                for name, expression in derived_expressions.items()
            )
            derived_rule = (
                f"- Derived interface dimensions ({rendered}) must remain symbolic "
                "expressions of retained parameters; they are not separate sweep binders.\n"
            )
        design_rule = (
            f"- The output file is `Generated/{prob_id}.lean`, and one generic Lean function/top module must be `{design_name}`.\n"
            f"- Retain these Nat binders as native SystemVerilog parameters: {', '.join(parameter_names)}.\n"
            f"- Use `#synthesizeParameterizedVerilog {design_name} [...]` exactly once; do not enumerate concrete aliases.\n"
            f"{derived_rule}"
            "- A Lean check is complete only when generated Verilog declares and uses every required parameter.\n"
        )
    elif design_name:
        design_rule = (
            f"- The output file is `Generated/{prob_id}.lean`, but the Lean function/top module must be `{design_name}`.\n"
            f"- Use `#synthesizeVerilog {design_name}` when there are no retained public dimensions. If the Benchmark Interface Contract lists public build parameters used in widths/depths, keep direct top-level Nat binders and use `#synthesizeParameterizedVerilog` (or its Design form for named child modules) instead. Do not name the synthesized function `{prob_id}` unless the problem explicitly says that is the target module.\n"
        )
    skill_section = ""
    if prompt_profile == "cvdp-skill-fewshot":
        skill_section = (
            "\n## Curated Sparkle Skill and Few-Shot Reference\n"
            "The following compact reference has been verified on non-evaluation "
            "examples. Apply its patterns, but implement the current contract "
            "rather than copying a mismatched interface.\n\n"
            + skill.strip()
            + "\n"
        )
    contract_rules = (
        public_prompt.PUBLIC_SPEC_RULES + "\n"
        if public_spec else
        "- Treat the user's `Benchmark Interface Contract` as authoritative over guesses from examples or file names.\n"
        "- For CVDP parameters, follow the Benchmark Interface Contract and the active P0/P3 parameter contract exactly.\n"
    )
    if public_spec:
        design_rule = (
            f"- Write `Generated/{prob_id}.lean`; use evaluation top-module label `{design_name}`.\n"
            "- Preserve every public width/depth/configuration parameter. Use top-level Nat binders and "
            "#synthesizeParameterizedVerilog (or its Design form for hierarchy) for parameterized designs; "
            "use #synthesizeVerilog for non-parameterized designs. Derive defaults only from public input.\n"
        )
    generation_skill = COMPACT_SPARKLE_GENERATION_SKILL
    if public_spec:
        generation_skill = generation_skill.replace("parameter contract", "public parameter requirements").replace(
            "benchmark contract", "public specification"
        ).replace(
            "Read at most two small examples", "Read at most three small examples"
        )
    compiler_and_search_rules = (
        "- Use the prescribed Lean compiler command on the complete candidate file, including the appropriate synthesis command. A check is successful only when it also returns `Generated Verilog`. The harness automatically saves the latest such compile-safe candidate.\n"
        "- Keep repository exploration short: read at most three examples, then write a complete candidate and iterate from compiler feedback.\n"
        "- Use only the search and compiler commands available in the execution notes. Do not search other tasks' artifacts.\n"
        if public_spec else
        "- Use `lean_check` frequently; it uses the persistent Lean REPL when available. Every inline `code` check must include the complete module body and the contract-appropriate synthesis command; a check is usable only when it also returns `Generated Verilog`. The harness automatically saves the latest such compile-safe candidate.\n"
        "- Keep repository exploration short: read at most three examples, then write a complete candidate and iterate from compiler feedback.\n"
        "- Use the guarded `grep`, `glob`, and `list_directory` tools for repository searches; shell file-discovery commands are blocked so concurrent tasks cannot see each other's artifacts.\n"
        "- If you need a directory listing, use `list_directory`; do not call `read_file` on directories.\n"
    )
    return (
        generation_skill.rstrip()
        + skill_section
        + "\n\n## CktArchon harness rules\n"
        + f"- You are running under the Archon-style CktArchon harness for `{prob_id}`.\n"
        + (f"- Write only `Generated/{prob_id}.lean`.\n" if public_spec else
           f"- Write only `Generated/{prob_id}.lean` using the `write_file`/`edit_file` tools.\n")
        + design_rule
        + contract_rules
        + "- Match benchmark output names exactly. If you must return a packed output internally, construct an explicit named MSB-to-LSB concat so the CVDP wrapper can recover each output field.\n"
        + "- Preserve benchmark clock, reset polarity, and cycle latency exactly; the cocotb harness checks protocol timing, not just combinational truth tables.\n"
        + compiler_and_search_rules
        + "- The key Sparkle synthesis rules are included below. Do not `cat` all of `docs/Troubleshooting_Synthesis.md`; if you need more detail, use `grep` for a narrow pattern.\n"
        + "- Prefer simple `Signal dom ... -> Signal dom ...` combinational helpers. Avoid pure helper functions with `match`, `if`, tuples, or recursion when their result depends on hardware signals.\n"
        + "- Use `Signal.mux` for all data-dependent choices. Sparkle cannot synthesize Lean `if`/`match`/`ite` over signal-derived values or inside `Signal.map` lambdas.\n"
        + "- Use supported binary Signal operators directly (`+`, `-`, `*`, `&&&`, `|||`, `^^^`, `===`, `++`, `<<<`, `>>>`). Avoid multi-argument lambdas such as `(fun a b => ...) <$> x <*> y`.\n"
        + "- For Signal shifts, the shift amount must be a same-width `BitVec` literal or Signal, e.g. `x >>> 1#8` for `Signal dom (BitVec 8)` and `x <<< 2#16` for `Signal dom (BitVec 16)`. Never write `x >>> 1` or an undersized amount such as `x >>> 1#3`.\n"
        + "- For comparisons other than equality, use Sparkle helpers such as `Signal.ult` or `Signal.slt` with same-width operands; do not use Lean `<`, `>`, `<=`, or `>=` on `Signal` values.\n"
        + "- For `Signal dom Bool`, avoid Lean `||` and `&&` over signals. Implement OR as `Signal.mux a (Signal.pure true) b` and AND as `Signal.mux a b (Signal.pure false)` unless a checked local example shows a better pattern.\n"
        + "- For tuple-valued signals, project with `.fst`/`.snd` or `projN!`; do not destructure with `let (a, b) := ...` in synthesizable code.\n"
        + "- `dff init next` takes a plain initial payload, never a `Signal`: use `dff 0#W next` or `dff (0#A, 0#B) next`, not `dff (Signal.pure ...) next`.\n"
        + "- Lean product types associate to the right. For a result declared `Signal dom (A x B x C)`, build `bundle2 a (bundle2 b c)` (or `bundleAll! [a, b, c]`); `bundle2 (bundle2 a b) c` has the wrong type.\n"
        + "- For packed `BitVec` outputs, prefer explicit `++` concatenation of sized Signal operands. Do not use `bundleAll!` to build a packed bit-vector result.\n"
        + "- For native parameterized pointer or address widths, use clog2 DEPTH from Sparkle.Library.RTL in BitVec types; it has ceiling-log2 semantics and emits SystemVerilog $clog2(DEPTH). Do not use Lean Nat.log2 for a SystemVerilog address width.\n"

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
        "archon-native harness requested, but this host has no `claude`/`codex` CLI. "
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
    plan_payload = (
        (getattr(info, "metadata", {}) or {}).get("finite_parameter_plan")
        if info is not None else None
    )
    native_payload = (
        (getattr(info, "metadata", {}) or {}).get("native_parameter_sweep_plan")
        if info is not None else None
    )
    if plan_payload:
        required_modules = tuple(
            row["module_name"] for row in plan_payload.get("cases", [])
        )
    elif native_payload and getattr(info, "design_name", None):
        required_modules = (str(info.design_name),)
    else:
        required_modules = ()
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
            system_prompt=build_system_prompt(skill, prob_id, info, args.prompt_profile),
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
            required_verilog_modules=required_modules,
            public_only=bool(
                args.hide_cvdp_harness_from_agent and args.dataset == "cvdp"
            ),
            execution_user=args.codex_agent_user,
            interface_prompt_policy=getattr(args, "interface_prompt_policy", "legacy"),
        )
    return AnthropicHarnessRunner(
        project_root=PROJECT_ROOT,
        prob_id=prob_id,
        model=model_alias(args.model),
        role=role,
        log_base=log_base,
        system_prompt=build_system_prompt(skill, prob_id, info, args.prompt_profile),
        max_tokens=args.max_tokens,
        lean_repl=repl,
        api_timeout=args.api_timeout,
        allow_bash_tool=not bool(
            args.hide_cvdp_harness_from_agent and args.dataset == "cvdp"
        ),
        required_verilog_modules=required_modules,
    )


def merge_agent_stats(total: AgentStats, extra: AgentStats) -> None:
    total.input_tokens += extra.input_tokens
    total.uncached_input_tokens += extra.uncached_input_tokens
    total.cache_creation_input_tokens += extra.cache_creation_input_tokens
    total.cache_read_input_tokens += extra.cache_read_input_tokens
    total.output_tokens += extra.output_tokens
    total.turns += extra.turns
    total.compile_checks += extra.compile_checks
    total.token_accounting_complete = (
        total.token_accounting_complete and extra.token_accounting_complete
    )
    total.recovered_usage_sessions += extra.recovered_usage_sessions
    for name, count in extra.tool_counts.items():
        total.tool_counts[name] = total.tool_counts.get(name, 0) + count


def search_total_turn_budget(args: argparse.Namespace) -> int:
    if args.search_total_turn_budget is not None:
        return max(0, int(args.search_total_turn_budget))
    feedback = args.sim_feedback_turn_budget
    return max(0, int(args.max_turns)) + (max(0, int(feedback)) if feedback is not None else 0)


def generation_turn_limit(args: argparse.Namespace) -> int:
    """Return the initial-session cap for ordinary generation/repair runs."""
    if args.total_turn_budget is not None:
        return max(0, int(args.generation_turn_cap))
    return max(0, int(args.max_turns))


def feedback_turn_budget_after_generation(
    args: argparse.Namespace,
    generation_turns: int,
) -> int:
    """Compute repair turns after the initial session has consumed its actual turns."""
    if args.total_turn_budget is not None:
        return max(0, int(args.total_turn_budget) - max(0, int(generation_turns)))
    if args.sim_feedback_turn_budget is None:
        return max(0, int(args.max_turns) - max(0, int(generation_turns)))
    return max(0, int(args.sim_feedback_turn_budget))


def runner_turn_limit(args: argparse.Namespace, requested_turns: int) -> int:
    """Return a conservative native-Codex session cap for strict shared budgets.

    Codex has no server-side action cap. Its local event watcher observes output
    asynchronously, so leave one event of headroom for a shared-budget run and
    never start a one-turn session that could overshoot the global ledger.
    """
    requested = max(0, int(requested_turns))
    if args.total_turn_budget is not None and args.harness == "codex-agent":
        return max(0, requested - 1)
    return requested


COMPILE_FEEDBACK_STAGES = frozenset({
    "lean_elaboration",
    "symbolic_dimension_lowering",
    "verilog_extraction",
    "parameter_contract",
    "verilog_elaboration",
})


def is_compile_feedback_failure(result: dict[str, Any]) -> bool:
    """Return whether a result failed before behavioral simulation."""
    stage = str(result.get("failure_stage") or "")
    if stage in COMPILE_FEEDBACK_STAGES:
        return True
    if stage in {"infrastructure", "simulation_mismatch", "simulation_error"}:
        return False
    return not bool(result.get("compile_pass")) or not bool(result.get("sv_extracted"))


def is_feedback_repairable(result: dict[str, Any], mode: str) -> bool:
    """Filter evaluator failures according to the evidence allowed to repair."""
    if result.get("sim_status") == "sim_pass":
        return False
    if result.get("failure_stage") == "infrastructure":
        return False
    if mode == "compile-sim":
        return True
    return is_compile_feedback_failure(result)


SEMANTIC_GATE_TURNS = 8


SEMANTIC_GATE_FEWSHOTS = """
### Worked examples (NOT the current problem)
Restating a Lean docstring is not a judgment. Compile/extract is not ALIGNED.

#### Example A — bug-fix mux (DEVIATION)
Public spec: "The snippet `assign out = (~sel & a) | (sel & b)` is buggy. Fix the 8-bit 2-to-1 mux."
Do not treat the buggy formula as the truth table. The intended mux is: sel=1 selects a, sel=0 selects b; out is 8 bits.
Wrong Lean (compiles):
```lean
def FixMux {dom : DomainConfig}
    (sel : Signal dom Bool) (a b : Signal dom (BitVec 8)) : Signal dom (BitVec 8) :=
  Signal.mux sel b a
```
Mismatch: `Signal.mux sel b a` takes b when sel is true. Spec takes a when sel is true. Width is already 8, polarity is inverted.
Gate: DEVIATION. Repair Lean (do not write ALIGNED):
```lean
def FixMux {dom : DomainConfig}
    (sel : Signal dom Bool) (a b : Signal dom (BitVec 8)) : Signal dom (BitVec 8) :=
  Signal.mux sel a b
```

#### Example B — register init / first beat (DEVIATION)
Public spec: 8-bit register, posedge clk, ports d and q, no reset pin. q should be 0 before the first clock.
Wrong Lean uses a nonzero seed or omits the zero init:
```lean
def InitReg {dom : DomainConfig} (d : Signal dom (BitVec 8)) : Signal dom (BitVec 8) :=
  dff 1#8 d
```
Mismatch: seed 1#8 is not the specified startup value 0.
Gate: DEVIATION. Repair:
```lean
def InitReg {dom : DomainConfig} (d : Signal dom (BitVec 8)) : Signal dom (BitVec 8) :=
  dff 0#8 d
```

#### Example C — sticky falling-edge capture vs reset (DEVIATION)
Public spec: each bit captures 1→0 across consecutive posedges; out stays 1 until active-high synchronous reset. On a reset cycle, out becomes 0 and must not OR in a new capture the same cycle.
Wrong Lean captures with the current input as "previous", or ORs falling edges after reset in the same beat.
Gate: DEVIATION. Repair pattern: `previous := dff 0#n inp`, `falling := previous &&& (~~~inp)`, then `dff 0#n (resetHigh 0#n reset sticky)` so reset wins that cycle.

#### Example D — true ALIGNED
Public spec: combinational 8-bit bitwise AND of a and b, no clock.
Lean:
```lean
def And2 {dom : DomainConfig}
    (a b : Signal dom (BitVec 8)) : Signal dom (BitVec 8) :=
  a &&& b
```
Polarity, width, and combinational vs sequential all match. Gate: ALIGNED. Do not edit Lean.
"""


def semantic_gate_path(prob_id: str) -> Path:
    return PROJECT_ROOT / "Generated" / f"{prob_id}.semantic_gate"


def read_semantic_gate(prob_id: str) -> str:
    path = semantic_gate_path(prob_id)
    if not path.exists():
        return ""
    raw = path.read_text(errors="replace").strip().strip("`").strip()
    if not raw:
        return ""
    token = raw.split()[0].upper().strip(".,;:")
    if token in {"ALIGNED", "DEVIATION"}:
        return token
    return ""


def build_semantic_feedback(
    *,
    search: Any,
    prob_id: str,
    result: dict[str, Any],
    iteration: int,
    history: list[dict[str, Any]],
    run_dir: Path,
    info: Any,
    gate_only: bool,
    current_lean: str = "",
) -> str:
    prompt_text = getattr(info, "prompt_text", None) or "(no public specification available)"
    lean_code = current_lean
    if not lean_code:
        lean_path = PROJECT_ROOT / "Generated" / f"{prob_id}.lean"
        lean_code = lean_path.read_text(errors="replace") if lean_path.exists() else ""
    compile_ok = bool(result.get("compile_pass") and result.get("sv_extracted"))
    lines = [
        f"## Pre-Sim Semantic Check - Session {iteration + 1}",
        "",
        f"Judge whether `Generated/{prob_id}.lean` matches the public problem semantics.",
        "Do not treat compiled SystemVerilog as the object of this check.",
        "RTL simulation has not been run yet. Hidden testbenches and gold RTL are not allowed.",
        "This pass shares leftover turns with compile-only repair under the same total-turn-budget.",
        "If you repair, edit Lean only; never write a Verilog patch.",
        "",
        "### Compile / Extract Status",
        search.summarize_eval_result(result),
        "(Compile/extract is a prerequisite, not the semantic criterion.)",
        "",
        "### Public Specification",
        search.truncate_text(prompt_text, 8000, keep="head"),
        "",
        "### Judge checklist (find mismatches; do not rewrite a working spec as a tutorial)",
        "- Compiled / extracted Verilog is not evidence of alignment.",
        "- Check select polarity (`mux` / `sel`: which input is taken when sel is 0 vs 1).",
        "- Check clock edge (posedge vs negedge) and whether the spec is combinational.",
        "- Check reset polarity and sync vs async; do not invent reset if the spec has none.",
        "- Check enable, sticky/capture, shift direction, bit-width, and cycle latency.",
        "- A plausible Sparkle sketch that inverts any of the above is DEVIATION.",
        "- If repairing, keep the public port interface; edit Lean only.",
        "- Do not paste Sparkle library source. At most skim `Benchmark/RTLIdioms.lean`.",
        "",
        SEMANTIC_GATE_FEWSHOTS.strip(),
        "",
        "### Generated Lean (the artifact to judge and, if needed, repair)",
    ]
    if lean_code.strip():
        lines.extend([
            "```lean",
            search.truncate_text(lean_code, 12000, keep="middle"),
            "```",
        ])
    else:
        lines.append(f"`Generated/{prob_id}.lean` is missing. Restore a Lean candidate first.")
    if history:
        lines.extend(["", "### Previous Semantic Sessions", search.summarize_recent_attempts(history)])
    gate_file = f"Generated/{prob_id}.semantic_gate"
    lines.extend([
        "",
        "### Decision Rule",
        f"Write exactly one token to `{gate_file}`: `ALIGNED` or `DEVIATION`. Both tokens are valid session outcomes.",
        f"- `ALIGNED` only if Lean matches the spec on polarity, clock edge, reset, enable/capture, widths, and latency. Then leave `Generated/{prob_id}.lean` unchanged.",
        "- `DEVIATION` if Lean compiles but any of those are wrong, or if meaning is uncertain. Repair `Generated/{prob_id}.lean` in the same session.",
        "- Compile/extract success is not ALIGNED. A story that restates the spec is not ALIGNED.",
        "- When unsure, choose DEVIATION and repair; do not write ALIGNED to finish faster.",
        "- Do not compare or patch compiled Verilog in this session.",
        "- Port-list similarity, style, or micro-optimizations are not deviations.",
        "- Keep the public interface stable.",
    ])
    if history:
        last = history[-1]
        last_gate = (last.get("gate") or "") if isinstance(last, dict) else ""
        if not last_gate:
            lines.extend([
                "",
                "### Previous session was invalid",
                f"No `{gate_file}` token was recorded. Silent no-op is not a pass.",
                "This session must write ALIGNED or DEVIATION before ending.",
            ])
        elif last_gate == "DEVIATION" and not last.get("lean_changed"):
            lines.extend([
                "",
                "### Previous session was incomplete",
                "You wrote DEVIATION but did not edit the Lean file. Repair Generated Lean now, then keep DEVIATION or rewrite ALIGNED only after the Lean meaning matches.",
            ])
    if not compile_ok:
        lines.extend(["", "### This session must restore compile", "Fix Lean compile/extract first, then write ALIGNED or DEVIATION."])
    else:
        lines.extend([
            "",
            "### This session is a forced gate",
            f"You must write `{gate_file}` with ALIGNED or DEVIATION. Either token is a completed gate.",
            "A no-op that writes neither token is a failed session.",
            "DEVIATION plus a Lean edit is success for this session; the harness will keep iterating if needed.",
            "Do not write ALIGNED merely to satisfy the harness or to skip repair.",
            "Do not grep repository Python sources for this protocol; follow the decision rule above.",
        ])
    return "\n".join(lines)


def configure_finite_parameter_specialization(
    info: Any,
    *,
    enabled: bool,
) -> Any:
    return configure_parameter_mode(
        info,
        finite_enabled=enabled,
        native_enabled=False,
    )


def configure_parameter_mode(
    info: Any,
    *,
    finite_enabled: bool,
    native_enabled: bool,
) -> Any:
    if finite_enabled and native_enabled:
        raise ValueError(
            "finite parameter specialization and native parameter sweep are mutually exclusive"
        )
    metadata = dict(getattr(info, "metadata", {}) or {})
    metadata.pop("finite_parameter_plan", None)
    metadata.pop("native_parameter_sweep_plan", None)
    mode = (
        "finite_parameter_specialization"
        if finite_enabled
        else "native_parameter_sweep" if native_enabled else None
    )
    if mode is None:
        info.metadata = metadata
        return info
    if metadata.get("dataset") != "cvdp":
        raise ValueError("CVDP parameter modes are supported only for CVDP")
    _add_legacy_agent_path()
    from cvdp_specialization import discover_finite_parameter_plan
    from cvdp_native_parameters import native_plan_to_dict
    from evaluator import _public_derived_parameter_expression
    import search

    plan = discover_finite_parameter_plan(
        design_name=info.design_name,
        harness_files=metadata.get("harness_files", {}) or {},
    )
    if not plan.supported:
        raise ValueError(
            f"{mode.replace('_', ' ')} contract unavailable: "
            + "; ".join(plan.diagnostics)
        )
    payload = (
        plan.to_dict()
        if mode == "finite_parameter_specialization"
        else native_plan_to_dict(plan)
    )
    metadata_key = (
        "finite_parameter_plan"
        if mode == "finite_parameter_specialization"
        else "native_parameter_sweep_plan"
    )
    metadata[metadata_key] = payload
    info.metadata = metadata
    prompt_parameters = search._p0_parse_parameters_from_prompt(
        info.prompt_text or ""
    )
    expected_ports = search._p0_benchmark_expected_ports(info)
    public_type_text = "\n".join(typ for _, typ, _ in expected_ports)
    derived_parameters = sorted(
        name
        for name in prompt_parameters - set(plan.parameter_names)
        if re.search(rf"\b{re.escape(name)}\b", public_type_text)
    )

    payload["expected_ports"] = expected_ports
    payload["derived_parameter_names"] = derived_parameters
    public_text = (
        str(info.ref_code or "")
        + "\n"
        + str(info.prompt_text or "")
        + "\n"
        + "\n".join(
            str(content)
            for path, content in (metadata.get("harness_files", {}) or {}).items()
            if str(path).endswith(".py")
        )
    )
    payload["derived_parameter_expressions"] = {
        name: expression
        for name in derived_parameters
        if (
            expression := _public_derived_parameter_expression(
                parameter_name=name,
                public_text=public_text,
            )
        ) is not None
    }
    reset_names = [
        name for direction, _, name in expected_ports
        if direction == "input" and search._port_kind(name) == "reset"
    ]
    payload["reset_polarities"] = search._infer_cvdp_reset_polarities(
        metadata.get("harness_files", {}) or {},
        reset_names,
    )
    metadata[metadata_key] = payload
    info.metadata = metadata
    return info


def save_self_test_guide(run_dir: Path, prob_id: str, guide: SelfTestGuide) -> None:
    guide_dir = run_dir / "self_test_guides" / prob_id
    guide_dir.mkdir(parents=True, exist_ok=True)
    (guide_dir / "test_plan.md").write_text(guide.test_plan, encoding="utf-8")
    if guide.testbench_sv:
        (guide_dir / "self_test.sv").write_text(guide.testbench_sv, encoding="utf-8")
    (guide_dir / "planner_response.txt").write_text(guide.raw_response, encoding="utf-8")


def self_test_feedback(result: SelfTestResult | None) -> str:
    if result is None or result.status in {"not_run", "unavailable", "invalid", "guidance_only"}:
        return ""
    return (
        "### Latest Advisory Self-Test Result\n\n"
        f"- Status: {result.status}\n"
        "- This test was generated only from the public specification. The benchmark evaluator remains authoritative.\n"
        "```text\n"
        f"{result.detail[-8000:]}\n"
        "```"
    )


def candidate_delivery_guard(prob_id: str, turn_limit: int) -> str:
    """Front-load artifact delivery when a session starts without source code."""
    return (
        "## Mandatory Candidate Delivery\n\n"
        f"You have at most {turn_limit} turns in this session. Your first tool action "
        f"MUST create `Generated/{prob_id}.lean` with a complete draft, including the "
        "synthesis command. Use the file-edit tool available in this harness "
        "(`write_file`, `apply_patch`, or the equivalent); do not call `glob`, `grep`, "
        "`read_file`, `list_directory`, or a shell command before that first write. "
        "The prompt already contains the interface and supported Sparkle APIs. Use the "
        "remaining turns for `lean_check`, focused inspection, and edits. A session "
        "that ends without creating the target file is discarded."
    )


def _public_state_text(value: Any, limit: int = 2400) -> str:
    text = str(value or "").replace("\r\n", "\n").strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "\n... [truncated]"


def write_public_repair_state(
    *,
    prob_id: str,
    iteration: int,
    current_code: str,
    result: dict[str, Any],
    feedback: str,
    recent_attempts: list[dict[str, Any]],
    structural_issues: list[dict[str, Any]],
) -> str:
    """Persist and return a public-safe per-problem repair state summary."""
    state_dir = PROJECT_ROOT / "cktarchon_work" / prob_id
    state_dir.mkdir(parents=True, exist_ok=True)
    target = f"Generated/{prob_id}.lean"
    candidate_exists = bool(current_code.strip())
    lines = [
        "## Public Repair State",
        "",
        f"- Problem: `{prob_id}`",
        f"- Iteration: {iteration}",
        f"- Required target: `{target}`",
        f"- Target candidate exists: {'yes' if candidate_exists else 'no'}",
        f"- Current candidate size: {len(current_code)} characters",
        f"- Current result: sim_status={result.get('sim_status') or 'unknown'}, "
        f"compile_pass={bool(result.get('compile_pass'))}, "
        f"sv_extracted={bool(result.get('sv_extracted'))}, "
        f"lint_pass={bool(result.get('lint_pass'))}",
        f"- Failure stage/category/family: {result.get('failure_stage') or 'unknown'} / "
        f"{result.get('failure_category') or 'unknown'} / {result.get('failure_family') or 'unknown'}",
        "",
        "### Next Action",
    ]
    if not candidate_exists:
        lines.extend([
            f"Create `{target}` immediately with a complete draft and synthesis command.",
            "Do not repeat broad API/example discovery before the first write.",
        ])
    elif not result.get("compile_pass") or not result.get("sv_extracted"):
        lines.extend([
            "Edit the existing target file using the compiler diagnostics below.",
            "Do not restart from the public spec unless the current file is structurally unusable.",
        ])
    else:
        lines.extend([
            "Keep the existing compiled candidate and repair only the public structural mismatch described below.",
            "Do not use hidden simulation traces or private evaluator files.",
        ])
    lines.extend([
        "",
        "### Latest Public Compile/Structure Feedback",
        "",
        "```text",
        _public_state_text(feedback, 3600),
        "```",
    ])
    if structural_issues:
        lines.extend(["", "### Public Structural Issues", ""])
        for issue in structural_issues[:8]:
            if isinstance(issue, dict):
                kind = _public_state_text(issue.get("kind") or issue.get("type") or "issue", 120)
                msg = _public_state_text(issue.get("message") or issue.get("detail") or issue, 500)
                lines.append(f"- {kind}: {msg}")
            else:
                lines.append(f"- {_public_state_text(issue, 500)}")
    if recent_attempts:
        lines.extend(["", "### Recent Repair Outcomes", ""])
        for attempt in recent_attempts[-5:]:
            if not isinstance(attempt, dict):
                continue
            bits = [
                f"iter={attempt.get('iteration') or '?'}",
                f"trigger={attempt.get('repair_trigger') or attempt.get('phase') or '?'}",
                f"accepted={attempt.get('accepted_candidate')}",
                f"improved={attempt.get('improved_best')}",
                f"turns={attempt.get('repair_turns') or '?'}",
            ]
            summary = _public_state_text(attempt.get("result_summary") or attempt.get("note") or "", 700)
            lines.append("- " + ", ".join(bits) + (f": {summary}" if summary else ""))
    lines.extend([
        "",
        "### Reuse Guidance",
        "",
        "Use this state as the continuation context for this candidate lineage.",
        "Avoid re-reading generic Sparkle API documentation unless a compiler diagnostic names a specific unknown API or type.",
    ])
    text = "\n".join(lines).rstrip() + "\n"
    (state_dir / "repair_state.md").write_text(text, encoding="utf-8")
    return text


def build_fresh_candidate_prompt(
    *,
    search: Any,
    prob_id: str,
    info: Any,
    dataset_name: str,
    has_repl: bool,
    candidate_id: int,
    guide_text: str,
    prior_result: dict | None,
    prior_feedback: str,
    recent_attempts: list[dict[str, Any]],
    latest_self_test: SelfTestResult | None,
    turn_limit: int,
) -> str:
    base = search.build_user_message(
        prob_id,
        has_repl=has_repl,
        info=info,
        dataset_name=dataset_name,
        condition_sv=None,
    )
    prior_summary = search.summarize_eval_result(prior_result)
    failure_evidence = (
        search.compact_repair_feedback(prior_feedback)
        if str(prior_feedback or "").strip()
        else "No structured failure evidence was captured."
    )
    attempts = search.summarize_recent_attempts(recent_attempts)
    advisory = self_test_feedback(latest_self_test)
    return (
        candidate_delivery_guard(prob_id, turn_limit)
        + "\n\n"
        + base
        + "\n\n"
        + guide_text
        + "\n\n## Fresh Candidate Search\n\n"
        + f"This is independent candidate `{candidate_id}`. `Generated/{prob_id}.lean` has been cleared. "
        + "Design a complete implementation from the original specification instead of reconstructing or locally patching the previous Lean architecture. "
        + "Use a materially different state representation, pipeline/latency structure, or combinational decomposition where appropriate.\n\n"
        + "### Constraints Learned From Earlier Candidates\n\n"
        + prior_summary
        + "\n\n### Failure Evidence To Avoid\n\n"
        + failure_evidence
        + "\n\n### Earlier Candidate Summaries\n\n"
        + attempts
        + ("\n\n" + advisory if advisory else "")
        + "\n\nLean-check the complete fresh candidate including the contract-appropriate synthesis command. Stop only when the check also returns generated Verilog; the harness will save that compile-safe candidate."
    )


def finalize_guided_candidate(
    tracker: CandidateTracker,
    generated_target: Path,
    *,
    last_evaluated_code: str | None,
    evaluate_selected: Any,
) -> tuple[dict | None, bool]:
    """Restore and, when needed, re-evaluate the selected global-best source.

    Evaluator artifacts are mutable per-problem paths.  If search ends while a
    different candidate was evaluated last, merely restoring global_best.lean
    leaves sv/, diagnostics/, and cvdp_sim/ describing the wrong candidate.
    """

    tracker.restore_global(generated_target)
    selected_result = tracker.global_best_result
    selected_code = tracker.global_best_code
    if selected_result is None:
        return None, False
    if selected_code is None or selected_code == last_evaluated_code:
        return dict(selected_result), False
    return evaluate_selected(), True


def process_problem_guided(
    prob_id: str,
    *,
    args: argparse.Namespace,
    ds: Any,
    evaluator: Any,
    run_dir: Path,
    skill: str,
    repl: Any | None,
) -> dict[str, Any]:
    """Run public-spec-guided, multi-candidate search under one turn ledger."""

    _add_legacy_agent_path()
    import search

    problem_t0 = time.monotonic()
    info = configure_parameter_mode(
        ds.load_problem(prob_id),
        finite_enabled=bool(getattr(args, "finite_parameter_specialization", False)),
        native_enabled=bool(getattr(args, "native_parameter_sweep", False)),
    )
    structural_feedback = ""
    structural_issues: list[dict[str, str]] = []
    agent_info = agent_visible_problem_info(
        info,
        hide_cvdp_harness=bool(args.hide_cvdp_harness_from_agent),
    )
    has_repl = repl is not None
    generated_target = PROJECT_ROOT / "Generated" / f"{prob_id}.lean"
    preexisting_generated_backup = clear_generated_target(PROJECT_ROOT, run_dir, prob_id)
    budget = TurnBudget(search_total_turn_budget(args))
    planner_stats = AgentStats()
    generation_stats = AgentStats()
    repair_stats_total = AgentStats()
    agent_elapsed = 0.0
    eval_elapsed = 0.0
    agent_errors: list[str] = []
    search_history: list[dict[str, Any]] = []
    self_test_history: list[dict[str, Any]] = []
    sim_feedback_iterations = 0
    sim_feedback_success = False

    interface_contract = search.format_benchmark_interface_contract(agent_info)
    public_context = search.format_context_files(agent_info)
    fallback_plan = (
        "Derive expected behavior only from the natural-language specification and the interface contract. "
        "Check reset polarity, cycle latency, boundary values, state transitions, output ordering, and every listed parameter setting."
    )
    guide = SelfTestGuide(test_plan=fallback_plan, testbench_sv="")
    planner_error: str | None = None
    self_test_validation_error: str | None = None
    self_test_enabled = not args.disable_guided_self_test
    self_test_mode = args.guided_self_test_mode if self_test_enabled else "disabled"
    planner_limit = budget.session_limit(args.self_test_planner_turns) if self_test_enabled else 0
    if planner_limit > 0 and args.harness == "anthropic-api":
        planner_prompt = build_self_test_planner_prompt(
            prob_id=prob_id,
            design_name=info.design_name,
            spec=info.prompt_text,
            interface_contract=interface_contract,
            public_context=public_context,
        )
        planner = AnthropicTextRunner(
            model=model_alias(args.model),
            role="ckt-public-self-test-planner",
            log_base=run_dir / "logs" / prob_id / "self_test_planner",
            system_prompt=(
                "You are a hardware verification planner. Use only the specification and public interface "
                "metadata in the user message. Never request, search for, or reconstruct hidden benchmark files."
            ),
            max_tokens=min(args.max_tokens, 8192),
            api_timeout=args.api_timeout,
        )
        planner_t0 = time.monotonic()
        try:
            response_text, planner_stats = planner.run(planner_prompt, max_turns=planner_limit)
            budget.consume(planner_stats.turns)
            parsed = parse_self_test_guide(response_text)
            if parsed.test_plan:
                guide, self_test_validation_error = validate_self_test_guide(
                    parsed,
                    design_name=info.design_name,
                    interface_contract=interface_contract,
                )
                if not guide.test_plan:
                    guide = SelfTestGuide(
                        test_plan=fallback_plan,
                        testbench_sv="",
                        raw_response=guide.raw_response,
                    )
        except Exception as exc:
            planner_error = f"{type(exc).__name__}: {exc}"
            parsed_stats = parse_agent_log(planner.log_path)
            if parsed_stats.turns or parsed_stats.input_tokens or parsed_stats.output_tokens:
                planner_stats = parsed_stats
                budget.consume(planner_stats.turns)
        agent_elapsed += time.monotonic() - planner_t0
    elif args.harness != "anthropic-api":
        planner_error = "LLM self-test planner skipped because guided planning currently uses the Anthropic API harness."
    if self_test_enabled:
        save_self_test_guide(run_dir, prob_id, guide)

    full_guide_text = format_self_test_guidance(guide, include_testbench=True) if self_test_enabled else ""
    plan_only_text = format_self_test_guidance(guide, include_testbench=False) if self_test_enabled else ""
    base_user_message = search.build_user_message(
        prob_id,
        has_repl=has_repl,
        info=agent_info,
        dataset_name=evaluator.dataset_name,
        condition_sv=None,
    ) + (("\n\n" + full_guide_text) if full_guide_text else "")

    generation_limit = budget.session_limit(args.max_turns)
    user_message = (
        candidate_delivery_guard(prob_id, generation_limit)
        + "\n\n"
        + base_user_message
        if generation_limit > 0
        else base_user_message
    )
    generation_error: str | None = None
    if generation_limit > 0:
        log_base = run_dir / "logs" / prob_id / "generate"
        runner = make_runner(
            args=args,
            prob_id=prob_id,
            role="ckt-generator-candidate-1",
            log_base=log_base,
            skill=skill,
            info=agent_info,
            repl=repl,
        )
        generation_t0 = time.monotonic()
        try:
            generation_stats = runner.run(user_message, max_turns=generation_limit)
        except Exception as exc:
            generation_error = f"{type(exc).__name__}: {exc}"
            agent_errors.append(f"initial generation: {generation_error}")
            parsed_stats = parse_agent_log(Path(str(log_base) + ".jsonl"))
            if parsed_stats.turns or parsed_stats.input_tokens or parsed_stats.output_tokens or parsed_stats.tool_counts:
                generation_stats = parsed_stats
        budget.consume(generation_stats.turns)
        agent_elapsed += time.monotonic() - generation_t0

    eval_t0 = time.monotonic()
    if generated_target.exists():
        result = evaluate_with_infrastructure_retries(
            evaluator, prob_id, run_dir, info
        )
        if generation_error:
            result["generation_error"] = generation_error
    else:
        result = {
            "prob_id": prob_id,
            "compile_pass": False,
            "sv_extracted": False,
            "lint_pass": False,
            "sim_status": "not_run",
            "sim_mismatches": -1,
            "detail": generation_error or "Initial generation did not create a Lean candidate.",
        }
    eval_elapsed += time.monotonic() - eval_t0

    code = generated_target.read_text(errors="replace") if generated_target.exists() else None
    last_evaluated_code = code
    tracker = CandidateTracker(
        snapshot_root=run_dir / "candidates",
        prob_id=prob_id,
        progress_key=search.eval_progress_key,
        max_candidates=args.candidate_search_max,
        patience=args.candidate_stagnation_patience,
    )
    initial_observation = tracker.start_candidate(result, code, reason="initial generation")
    search_history.append({
        "phase": "generation",
        "iteration": 0,
        **initial_observation.__dict__,
        "result_summary": search.summarize_eval_result(result),
        "turns": generation_stats.turns,
        "remaining_turns": budget.remaining,
    })

    def run_self_test_for(current_result: dict, candidate_id: int, attempt: int) -> SelfTestResult:
        if not self_test_enabled:
            return SelfTestResult("disabled", "Guided self-test is disabled for this run.")
        if self_test_mode != "execute":
            return SelfTestResult("guidance_only", "Public-spec TB guidance is injected into prompts but is not executed or used as an oracle.")
        if not current_result.get("sv_extracted") or not guide.testbench_sv:
            return SelfTestResult("not_run", "Self-test requires extracted SystemVerilog and a generated advisory TB.")
        return run_generated_self_test(
            prob_id=prob_id,
            run_dir=run_dir,
            verilog_sources=list(info.metadata.get("verilog_sources") or []),
            testbench_sv=guide.testbench_sv,
            candidate_id=candidate_id,
            attempt=attempt,
        )

    latest_self_test = run_self_test_for(result, tracker.candidate_id, 0)
    active_feedback = search.build_sim_feedback(
        prob_id=prob_id,
        result=result,
        iteration=0,
        history=search_history,
        run_dir=run_dir,
        info=info,
    )
    if self_test_enabled:
        self_test_history.append({
            "candidate_id": tracker.candidate_id,
            "attempt": 0,
            **latest_self_test.__dict__,
        })

    while (
        args.sim_feedback
        and (is_feedback_repairable(result, args.feedback_mode) or bool(structural_issues))
        and budget.remaining > 0
        and sim_feedback_iterations < max(0, args.sim_feedback_max_iters)
    ):
        sim_feedback_iterations += 1
        fresh_candidate = tracker.is_stagnant and tracker.can_restart
        attempt_limit = args.sim_feedback_turns_per_iter
        if attempt_limit is None:
            attempt_limit = args.max_turns
        turn_limit = budget.session_limit(attempt_limit)
        if turn_limit <= 0:
            break

        if fresh_candidate:
            previous_candidate = tracker.candidate_id
            previous_result = tracker.active_best_result or result
            tracker.restore_active(generated_target)
            generated_target.unlink(missing_ok=True)
            next_candidate = tracker.candidate_id + 1
            prompt = build_fresh_candidate_prompt(
                search=search,
                prob_id=prob_id,
                info=agent_info,
                dataset_name=evaluator.dataset_name,
                has_repl=has_repl,
                candidate_id=next_candidate,
                guide_text=full_guide_text,
                prior_result=previous_result,
                prior_feedback=active_feedback,
                recent_attempts=search_history,
                latest_self_test=latest_self_test,
                turn_limit=turn_limit,
            )
            phase = "fresh_candidate"
            role = f"ckt-generator-candidate-{next_candidate}"
            log_name = f"candidate_{next_candidate}_generate"
            search_history.append({
                "phase": "candidate_restart",
                "iteration": sim_feedback_iterations,
                "candidate_id": previous_candidate,
                "next_candidate_id": next_candidate,
                "note": (
                    f"Started a fresh candidate after {tracker.stagnation_count} non-improving attempts; "
                    "the previous candidate remains available as the global-best snapshot."
                ),
                "remaining_turns": budget.remaining,
            })
            current_feedback_for_attempt = ""
        else:
            tracker.restore_active(generated_target)
            current_code = generated_target.read_text(errors="replace") if generated_target.exists() else ""
            active_result = tracker.active_best_result or result
            feedback = active_feedback
            if full_guide_text:
                feedback += "\n\n" + full_guide_text
            advisory = self_test_feedback(latest_self_test) if self_test_enabled else ""
            if advisory:
                feedback += "\n\n" + advisory
            continuing_generation = is_compile_feedback_failure(active_result)
            delivery_guard = ""
            if not current_code.strip():
                repair_instruction = "Create the complete Lean source before checking it. "
                delivery_guard = candidate_delivery_guard(prob_id, turn_limit)
            elif continuing_generation:
                repair_instruction = "Continue the Lean implementation and fix its compile diagnostics. "
            else:
                repair_instruction = "Use the evaluator diagnostics and the public-spec test plan/TB guidance to repair the Lean source. "
            prompt = search.build_compact_repair_prompt(
                prob_id=prob_id,
                info=agent_info,
                dataset_name=evaluator.dataset_name,
                has_repl=has_repl,
                phase=("Lean generation/compile repair" if continuing_generation else "guided semantic repair"),
                iteration=sim_feedback_iterations,
                current_lean=current_code,
                latest_feedback=feedback,
                recent_attempts=search_history,
                extra_constraints=(
                    repair_instruction
                    + "Remain within the current candidate architecture unless a fresh-candidate restart is explicitly requested. "
                    + "Before ending, Lean-check the complete candidate including the contract-appropriate synthesis command and require generated Verilog; the outer evaluator will rerun simulation."
                ),
            )
            if delivery_guard:
                prompt = delivery_guard + "\n\n" + prompt
            phase = "guided_repair"
            role = f"ckt-repair-candidate-{tracker.candidate_id}"
            log_name = f"candidate_{tracker.candidate_id}_repair_{sim_feedback_iterations}"

        log_base = run_dir / "logs" / prob_id / log_name
        attempt_runner = make_runner(
            args=args,
            prob_id=prob_id,
            role=role,
            log_base=log_base,
            skill=skill,
            info=agent_info,
            repl=repl,
        )
        attempt_error: str | None = None
        attempt_t0 = time.monotonic()
        attempt_stats = AgentStats()
        try:
            attempt_stats = attempt_runner.run(prompt, max_turns=turn_limit)
        except Exception as exc:
            attempt_error = f"{type(exc).__name__}: {exc}"
            agent_errors.append(f"{phase} {sim_feedback_iterations}: {attempt_error}")
            parsed_stats = parse_agent_log(Path(str(log_base) + ".jsonl"))
            if parsed_stats.turns or parsed_stats.input_tokens or parsed_stats.output_tokens or parsed_stats.tool_counts:
                attempt_stats = parsed_stats
        merge_agent_stats(repair_stats_total, attempt_stats)
        budget.consume(attempt_stats.turns)
        agent_elapsed += time.monotonic() - attempt_t0

        attempt_eval_t0 = time.monotonic()
        if generated_target.exists():
            new_result = evaluate_with_infrastructure_retries(
                evaluator, prob_id, run_dir, info
            )
            if attempt_error:
                new_result["generation_error"] = attempt_error
        else:
            new_result = {
                "prob_id": prob_id,
                "compile_pass": False,
                "sv_extracted": False,
                "lint_pass": False,
                "sim_status": "not_run",
                "sim_mismatches": -1,
                "detail": attempt_error or "Agent attempt did not create a Lean candidate.",
            }
        eval_elapsed += time.monotonic() - attempt_eval_t0
        new_code = generated_target.read_text(errors="replace") if generated_target.exists() else None
        last_evaluated_code = new_code

        if fresh_candidate:
            observation = tracker.start_candidate(
                new_result,
                new_code,
                reason=f"stagnation restart at outer iteration {sim_feedback_iterations}",
            )
        else:
            observation = tracker.observe(
                new_result,
                new_code,
                reason=f"guided repair at outer iteration {sim_feedback_iterations}",
            )
            if not observation.accepted:
                tracker.restore_active(generated_target)

        result = tracker.active_best_result or new_result
        if observation.accepted or fresh_candidate:
            latest_self_test = run_self_test_for(
                new_result,
                tracker.candidate_id,
                observation.attempt,
            )
            active_feedback = search.build_sim_feedback(
                prob_id=prob_id,
                result=new_result,
                iteration=sim_feedback_iterations,
                history=search_history,
                run_dir=run_dir,
                info=agent_info,
            )
            if self_test_enabled:
                self_test_history.append({
                    "candidate_id": tracker.candidate_id,
                    "attempt": observation.attempt,
                    **latest_self_test.__dict__,
                })
        recorded_self_test_status = (
            "disabled"
            if not self_test_enabled
            else latest_self_test.status if observation.accepted or fresh_candidate else "not_run_rejected"
        )
        history_row = {
            "phase": phase,
            "iteration": sim_feedback_iterations,
            **observation.__dict__,
            "result_summary": search.summarize_eval_result(new_result),
            "repair_turns": attempt_stats.turns,
            "repair_turn_limit": turn_limit,
            "remaining_turns": budget.remaining,
            "repair_input_tokens": attempt_stats.input_tokens,
            "repair_uncached_input_tokens": attempt_stats.uncached_input_tokens,
            "repair_cache_creation_input_tokens": attempt_stats.cache_creation_input_tokens,
            "repair_cache_read_input_tokens": attempt_stats.cache_read_input_tokens,
            "repair_output_tokens": attempt_stats.output_tokens,
            "repair_compile_checks": attempt_stats.compile_checks,
            "self_test_status": recorded_self_test_status,
        }
        if attempt_error:
            history_row["agent_error"] = attempt_error
        search_history.append(history_row)
        append_jsonl(run_dir / "events.jsonl", {
            "prob_id": prob_id,
            "event": phase,
            "iteration": sim_feedback_iterations,
            "candidate_id": observation.candidate_id,
            "sim_status": new_result.get("sim_status"),
            "compile_pass": new_result.get("compile_pass"),
            "remaining_turns": budget.remaining,
            "improved_candidate": observation.improved_candidate,
            "improved_global": observation.improved_global,
            "stagnation_count": observation.stagnation_count,
            "self_test_status": recorded_self_test_status,
        })

        if result.get("sim_status") == "sim_pass":
            sim_feedback_success = True
            break

    final_eval_t0 = time.monotonic()
    selected_result, global_best_replayed = finalize_guided_candidate(
        tracker,
        generated_target,
        last_evaluated_code=last_evaluated_code,
        evaluate_selected=lambda: evaluate_with_infrastructure_retries(
            evaluator, prob_id, run_dir, info
        ),
    )
    if global_best_replayed:
        eval_elapsed += time.monotonic() - final_eval_t0
        result = selected_result or result
        search_history.append({
            "phase": "final_global_best_replay",
            "iteration": sim_feedback_iterations,
            "candidate_id": tracker.candidate_id,
            "note": (
                "Re-evaluated restored global-best Lean so final RTL, simulator "
                "artifacts, and score all describe the selected candidate."
            ),
            "result_summary": search.summarize_eval_result(result),
            "remaining_turns": budget.remaining,
        })
        append_jsonl(run_dir / "events.jsonl", {
            "prob_id": prob_id,
            "event": "final_global_best_replay",
            "iteration": sim_feedback_iterations,
            "sim_status": result.get("sim_status"),
            "compile_pass": result.get("compile_pass"),
            "remaining_turns": budget.remaining,
        })
    elif selected_result is not None:
        result = selected_result

    all_stats = AgentStats()
    merge_agent_stats(all_stats, planner_stats)
    merge_agent_stats(all_stats, generation_stats)
    merge_agent_stats(all_stats, repair_stats_total)
    record = {
        "prob_id": prob_id,
        "agent_turns": generation_stats.turns,
        "agent_input_tokens": all_stats.input_tokens,
        "agent_uncached_input_tokens": all_stats.uncached_input_tokens,
        "agent_cache_creation_input_tokens": all_stats.cache_creation_input_tokens,
        "agent_cache_read_input_tokens": all_stats.cache_read_input_tokens,
        "agent_output_tokens": all_stats.output_tokens,
        "agent_token_accounting_complete": all_stats.token_accounting_complete,
        "agent_token_usage_recovered_sessions": all_stats.recovered_usage_sessions,
        "agent_compile_checks": all_stats.compile_checks,
        "agent_tool_counts": all_stats.tool_counts,
        "agent_turn_budget": generation_turn_limit(args),
        "agent_generation_runner_turn_cap": runner_turn_limit(
            args, generation_turn_limit(args)
        ),
        "total_turn_budget": args.total_turn_budget,
        "generation_turn_cap": (
            args.generation_turn_cap
            if args.total_turn_budget is not None
            else None
        ),
        "turn_budget_policy": (
            "shared_total_with_generation_cap"
            if args.total_turn_budget is not None
            else "legacy"
        ),
        "prompt_profile": args.prompt_profile,
        "cvdp_harness_profile": args.cvdp_harness_profile,
        "agent_generation_turns": generation_stats.turns,
        "agent_turns_total": all_stats.turns,
        "search_total_turn_budget": budget.total,
        "guided_self_test_mode": self_test_mode,
        "search_turns_remaining": budget.remaining,
        "guided_search_enabled": True,
        "guided_self_test_enabled": self_test_enabled,
        "self_test_planner_turns": planner_stats.turns,
        "self_test_planner_input_tokens": planner_stats.input_tokens,
        "self_test_planner_uncached_input_tokens": planner_stats.uncached_input_tokens,
        "self_test_planner_cache_creation_input_tokens": planner_stats.cache_creation_input_tokens,
        "self_test_planner_cache_read_input_tokens": planner_stats.cache_read_input_tokens,
        "self_test_planner_output_tokens": planner_stats.output_tokens,
        "self_test_planner_error": planner_error,
        "self_test_validation_error": self_test_validation_error,
        "self_test_generated": bool(guide.testbench_sv),
        "self_test_history": self_test_history,
        "candidate_count": tracker.candidate_id,
        "candidate_max": tracker.max_candidates,
        "candidate_stagnation_patience": tracker.patience,
        "candidate_search_history": search_history,
        "global_best_replayed": global_best_replayed,
        "agent_errors": agent_errors,
        "sim_feedback_enabled": bool(args.sim_feedback),
        "feedback_mode": args.feedback_mode,
        "sim_feedback_iterations": sim_feedback_iterations,
        "sim_feedback_success": sim_feedback_success,
        "sim_feedback_turn_budget": max(0, budget.total - planner_stats.turns - generation_stats.turns),
        "sim_feedback_turns_remaining": budget.remaining,
        "agent_elapsed_seconds": round(agent_elapsed, 3),
        "eval_elapsed_seconds": round(eval_elapsed, 3),
        "elapsed_seconds": round(time.monotonic() - problem_t0, 3),
        "harness": args.harness,
        "model": model_alias(args.model),
        "agent_input_policy": (
            "cvdp-public-only"
            if args.hide_cvdp_harness_from_agent and evaluator.dataset_name == "cvdp"
            else "legacy"
        ),
        "cvdp_harness_hidden_from_agent": bool(
            args.hide_cvdp_harness_from_agent and evaluator.dataset_name == "cvdp"
        ),
        "cvdp_output_context_hidden_from_agent": bool(
            args.hide_cvdp_harness_from_agent and evaluator.dataset_name == "cvdp"
        ),
        "agent_bash_tool_enabled": not bool(
            args.hide_cvdp_harness_from_agent and evaluator.dataset_name == "cvdp"
        ),
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


def check_candidate_public_structure(result, run_dir, prob_id, agent_info, search):
    from structural_checker import check_public_structure
    sv_path = run_dir / "sv" / f"{prob_id}.sv"
    issues = []
    if result.get("sv_extracted") and sv_path.exists():
        issues = check_public_structure(
            sv=sv_path.read_text(errors="replace"), info=agent_info, search_module=search
        )
        append_jsonl(run_dir / "events.jsonl", {
            "prob_id": prob_id, "event": "public_structural_check",
            "issues": issues, "sv_sha256": hashlib.sha256(sv_path.read_bytes()).hexdigest(),
        })
    return issues


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
    use_public_prompt = getattr(args, "interface_prompt_policy", "legacy") == public_prompt.POLICY
    if use_public_prompt and (getattr(args, "guided_search", False) or args.feedback_mode != "compile-only"):
        raise ValueError("public-spec-v2 supports ordinary compile-only generation/repair, not guided search")
    if use_public_prompt and (getattr(args, "finite_parameter_specialization", False) or not args.hide_cvdp_harness_from_agent):
        raise ValueError("public-spec-v2 requires hidden harnesses and does not support finite specialization")
    if getattr(args, "guided_search", False) and not args.eval_only:
        return process_problem_guided(
            prob_id,
            args=args,
            ds=ds,
            evaluator=evaluator,
            run_dir=run_dir,
            skill=skill,
            repl=repl,
        )
    _add_legacy_agent_path()
    import search

    problem_t0 = time.monotonic()
    info = configure_parameter_mode(
        ds.load_problem(prob_id),
        finite_enabled=bool(getattr(args, "finite_parameter_specialization", False)),
        native_enabled=bool(getattr(args, "native_parameter_sweep", False)),
    )
    structural_feedback = ""
    structural_issues: list[dict[str, str]] = []
    agent_info = agent_visible_problem_info(
        info,
        hide_cvdp_harness=bool(args.hide_cvdp_harness_from_agent),
    )
    if use_public_prompt:
        agent_info = public_prompt.public_problem_info(info)
    has_repl = repl is not None
    from structural_checker import format_structural_feedback, public_progress_key, repair_eligible
    structural_used = False
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
    semantic_history: list[dict[str, Any]] = []
    skip_sim = bool(getattr(args, "pre_sim_semantic_repair", False))

    def eval_candidate(*, skip: bool = skip_sim) -> dict[str, Any]:
        return evaluate_with_infrastructure_retries(
            evaluator, prob_id, run_dir, info, skip_sim=skip
        )

    if not args.eval_only:
        preexisting_generated_backup = clear_generated_target(PROJECT_ROOT, run_dir, prob_id)
        generation_limit = runner_turn_limit(args, generation_turn_limit(args))
        user_message = (
            candidate_delivery_guard(prob_id, generation_limit)
            + "\n\n"
            + public_prompt.generation_prompt(prob_id, agent_info, "lean")
        ) if use_public_prompt else search.build_user_message(
            prob_id,
            has_repl=has_repl,
            info=agent_info,
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
            info=agent_info,
            repl=repl,
        )
        if use_public_prompt:
            public_prompt.save_prompt_receipt(run_dir, prob_id, "generate", agent_info,
                                             build_system_prompt(skill, prob_id, agent_info, args.prompt_profile), user_message)
        try:
            t0 = time.monotonic()
            agent_stats = runner.run(
                user_message,
                max_turns=generation_limit,
            )
            agent_elapsed = time.monotonic() - t0
        except Exception as exc:
            agent_error = f"{type(exc).__name__}: {exc}"
            parsed_stats = parse_agent_log(Path(str(log_base) + ".jsonl"))
            if parsed_stats.turns or parsed_stats.input_tokens or parsed_stats.output_tokens or parsed_stats.tool_counts:
                agent_stats = parsed_stats

    eval_t0 = time.monotonic()
    generated_target = PROJECT_ROOT / "Generated" / f"{prob_id}.lean"
    if agent_error and generated_target.exists():
        result = eval_candidate()
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
        result = eval_candidate()
    if use_public_prompt and not args.disable_public_structural_repair:
        structural_issues = check_candidate_public_structure(result, run_dir, prob_id, agent_info, search)
    eval_elapsed = time.monotonic() - eval_t0

    if not args.eval_only and not agent_error:
        sim_feedback_turn_budget = feedback_turn_budget_after_generation(
            args, agent_stats.turns
        )
        sim_feedback_turns_remaining = sim_feedback_turn_budget

    if (
        args.sim_feedback
        and not args.eval_only
        and not agent_error
        and repair_eligible(result, structural_issues, structural_used, is_feedback_repairable(result, args.feedback_mode))
    ):
        best_result = result
        best_code = generated_target.read_text(errors="replace") if generated_target.exists() else None
        selected_artifacts_current = True
        non_improving_repairs = 0
        sim_iter = 0
        while (
            repair_eligible(result, structural_issues, structural_used, is_feedback_repairable(result, args.feedback_mode))
            and sim_feedback_turns_remaining > 0
            and sim_iter < max(0, args.sim_feedback_max_iters)
        ):
            sim_iter += 1
            sim_feedback_iterations = sim_iter
            current_code = generated_target.read_text(errors="replace") if generated_target.exists() else ""
            structural_attempt = bool(structural_issues) and not structural_used
            structural_feedback = format_structural_feedback(structural_issues) if structural_attempt else ""
            feedback = (
                public_prompt.compile_feedback(result) if is_feedback_repairable(result, args.feedback_mode) else ""
            ) if use_public_prompt else search.build_sim_feedback(
                prob_id=prob_id,
                result=result,
                iteration=sim_iter - 1,
                history=sim_feedback_history,
                run_dir=run_dir,
                info=agent_info,
            )
            if structural_feedback:
                feedback += "\n\n" + structural_feedback
            if structural_attempt:
                structural_used = True
                append_jsonl(run_dir / "events.jsonl", {
                    "prob_id": prob_id, "event": "public_structural_repair",
                    "iteration": sim_iter, "remaining_turns": sim_feedback_turns_remaining,
                    "issues": structural_issues,
                })
            continuing_generation = is_compile_feedback_failure(result)
            if not current_code.strip():
                repair_instruction = (
                    f"No Lean candidate exists yet. Your first tool action MUST create "
                    f"`Generated/{prob_id}.lean` with a complete draft and synthesis command. "
                    "Do not inspect examples or run compiler checks before creating that file. "
                )
            elif continuing_generation:
                repair_instruction = (
                    "Continue generating the Lean source and use the compile diagnostics to make it compile. "
                )
            else:
                repair_instruction = "Use the Verilog simulation diagnostics to repair the Lean source. "
            if use_public_prompt:
                repair_state = write_public_repair_state(
                    prob_id=prob_id,
                    iteration=sim_iter,
                    current_code=current_code,
                    result=result,
                    feedback=feedback,
                    recent_attempts=sim_feedback_history,
                    structural_issues=structural_issues,
                )
                append_jsonl(run_dir / "events.jsonl", {
                    "prob_id": prob_id,
                    "event": "public_repair_state",
                    "iteration": sim_iter,
                    "path": f"cktarchon_work/{prob_id}/repair_state.md",
                })
                repair_instruction = repair_instruction + "\n\n" + repair_state
            compact_prompt = public_prompt.repair_prompt(
                prob_id, agent_info, "lean", current_code, feedback,
                extra_instruction=repair_instruction,
            ) if use_public_prompt else search.build_compact_repair_prompt(
                prob_id=prob_id,
                info=agent_info,
                dataset_name=evaluator.dataset_name,
                has_repl=has_repl,
                phase=("Lean generation/compile repair" if continuing_generation else "RTL simulation feedback"),
                iteration=sim_iter,
                current_lean=current_code,
                latest_feedback=feedback,
                recent_attempts=sim_feedback_history,
                extra_constraints=repair_instruction + (
                    "Before ending, ensure the final Lean file compiles; "
                    "the outer evaluator will rerun RTL simulation."
                ),
            )
            repair_log_base = run_dir / "logs" / prob_id / f"sim_feedback_iter_{sim_iter}"
            repair_runner = make_runner(
                args=args,
                prob_id=prob_id,
                role="ckt-sim-repair",
                log_base=repair_log_base,
                skill=skill,
                info=agent_info,
                repl=repl,
            )
            if use_public_prompt:
                public_prompt.save_prompt_receipt(run_dir, prob_id, f"repair_{sim_iter}", agent_info,
                                                 build_system_prompt(skill, prob_id, agent_info, args.prompt_profile), compact_prompt)
            try:
                repair_t0 = time.monotonic()
                repair_turn_limit = sim_feedback_turns_remaining
                if args.sim_feedback_turns_per_iter is not None:
                    repair_turn_limit = min(repair_turn_limit, max(0, args.sim_feedback_turns_per_iter))
                repair_runner_limit = runner_turn_limit(args, repair_turn_limit)
                if repair_runner_limit <= 0:
                    break
                repair_stats = repair_runner.run(
                    compact_prompt, max_turns=repair_runner_limit
                )
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
            new_result = eval_candidate()
            eval_elapsed += time.monotonic() - repair_eval_t0
            new_issues = check_candidate_public_structure(new_result, run_dir, prob_id, agent_info, search) if (use_public_prompt and not args.disable_public_structural_repair) else []
            new_key = public_progress_key(new_result, new_issues) if use_public_prompt else search.eval_progress_key(new_result)
            best_key = public_progress_key(best_result, structural_issues) if use_public_prompt else search.eval_progress_key(best_result)
            candidate_exists = generated_target.exists()
            improved = new_key > best_key
            generation_incomplete = not bool(best_result.get("compile_pass"))
            accepted = candidate_exists and (generation_incomplete or new_key >= best_key)
            sim_feedback_history.append({
                "phase": "sim_feedback",
                "iteration": sim_iter,
                "note": "Candidate result after RTL simulation feedback repair.",
                "result_summary": search.summarize_eval_result(new_result),
                "improved_best": improved,
                "accepted_candidate": accepted,
                "repair_turns": repair_stats.turns,
                "repair_trigger": "public_structure" if structural_attempt else "compile",
                "repair_turn_limit": repair_turn_limit,
                "repair_runner_turn_limit": repair_runner_limit,
                "remaining_turns": sim_feedback_turns_remaining,
                "repair_input_tokens": repair_stats.input_tokens,
                "repair_uncached_input_tokens": repair_stats.uncached_input_tokens,
                "repair_cache_creation_input_tokens": repair_stats.cache_creation_input_tokens,
                "repair_cache_read_input_tokens": repair_stats.cache_read_input_tokens,
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
                "accepted_candidate": accepted,
            })

            if accepted:
                selected_artifacts_current = True
                best_result = new_result
                structural_issues = new_issues
                best_code = generated_target.read_text(errors="replace") if candidate_exists else None
                result = new_result
                non_improving_repairs = 0 if improved else non_improving_repairs + 1
            else:
                selected_artifacts_current = False
                if best_code is not None:
                    generated_target.write_text(best_code, encoding="utf-8")
                result = best_result
                non_improving_repairs += 1

            if not repair_eligible(result, structural_issues, structural_used, is_feedback_repairable(result, args.feedback_mode)):
                sim_feedback_success = result.get("sim_status") == "sim_pass"
                break
            if args.sim_feedback_patience > 0 and non_improving_repairs >= args.sim_feedback_patience:
                sim_feedback_history.append({
                    "phase": "sim_feedback",
                    "iteration": sim_iter,
                    "note": f"Stopped: {args.sim_feedback_patience} consecutive simulation-feedback repairs did not improve the best candidate.",
                    "best_result_summary": search.summarize_eval_result(best_result),
                })
                break

        if best_code is not None:
            generated_target.write_text(best_code, encoding="utf-8")
            result = best_result
            if use_public_prompt and not selected_artifacts_current:
                # Restore evaluator artifacts to the publicly selected candidate.
                final_eval_t0 = time.monotonic()
                result = evaluate_with_infrastructure_retries(
                    evaluator, prob_id, run_dir, info, skip_sim=skip_sim
                )
                eval_elapsed += time.monotonic() - final_eval_t0

    if skip_sim and not args.eval_only and not agent_error and sim_feedback_turns_remaining > 0:
        lean_file = PROJECT_ROOT / "Generated" / f"{prob_id}.lean"
        best_code = lean_file.read_text(errors="replace") if lean_file.exists() else ""
        best_result = result
        max_semantic_iters = None
        sem_iter = 0
        non_improving_semantic = 0
        while sim_feedback_turns_remaining > 0:
            compile_ok = bool(result.get("compile_pass") and result.get("sv_extracted"))
            gate_only = True
            turns_this = min(sim_feedback_turns_remaining, SEMANTIC_GATE_TURNS)
            sem_iter += 1
            current_code = lean_file.read_text(errors="replace") if lean_file.exists() else ""
            feedback = build_semantic_feedback(
                search=search,
                prob_id=prob_id,
                result=result,
                iteration=sem_iter - 1,
                history=semantic_history,
                run_dir=run_dir,
                info=agent_info,
                gate_only=gate_only,
                current_lean=current_code,
            )
            compact_prompt = (
                feedback
                + "\n\n### Required actions\n"
                + f"1. Write exactly one token to `Generated/{prob_id}.semantic_gate`: ALIGNED or DEVIATION. Either is valid.\n"
                + f"2. If polarity, edge, reset, mux select, enable/capture, width, or latency is wrong or uncertain: write DEVIATION and repair `Generated/{prob_id}.lean`.\n"
                + f"3. Write ALIGNED only when those details match the public spec; then do not edit Lean.\n"
                + "4. Do not emit Verilog patches. Do not write ALIGNED just to end the session.\n"
            )
            repair_log_base = run_dir / "logs" / prob_id / f"pre_sim_semantic_iter_{sem_iter}"
            repair_runner = make_runner(
                args=args,
                prob_id=prob_id,
                role="ckt-semantic-gate",
                log_base=repair_log_base,
                skill=skill,
                info=agent_info,
                repl=repl,
            )
            try:
                repair_t0 = time.monotonic()
                repair_runner_limit = runner_turn_limit(args, turns_this)
                if repair_runner_limit <= 0:
                    break
                repair_stats = repair_runner.run(compact_prompt, max_turns=repair_runner_limit)
                agent_elapsed += time.monotonic() - repair_t0
                merge_agent_stats(repair_stats_total, repair_stats)
                sim_feedback_turns_remaining = max(0, sim_feedback_turns_remaining - repair_stats.turns)
                sem_repair_turns = repair_stats.turns
            except Exception as exc:
                semantic_history.append({
                    "phase": "pre_sim_semantic",
                    "iteration": sem_iter,
                    "note": f"Agent error: {type(exc).__name__}: {exc}",
                })
                break
            after_code = lean_file.read_text(errors="replace") if lean_file.exists() else ""
            lean_changed = after_code != current_code
            gate = read_semantic_gate(prob_id)
            repair_eval_t0 = time.monotonic()
            new_result = eval_candidate(skip=True)
            eval_elapsed += time.monotonic() - repair_eval_t0
            compile_ok_after = bool(new_result.get("compile_pass") and new_result.get("sv_extracted"))
            pass_through = compile_ok_after and gate == "ALIGNED"
            missing_gate = not gate
            deviation_without_repair = gate == "DEVIATION" and not lean_changed
            semantic_history.append({
                "phase": "pre_sim_semantic",
                "iteration": sem_iter,
                "gate": gate or None,
                "lean_changed": lean_changed,
                "pass_through": pass_through,
                "gate_only": gate_only,
                "missing_gate": missing_gate,
                "deviation_without_repair": deviation_without_repair,
                "result_summary": search.summarize_eval_result(new_result),
                "repair_turns": sem_repair_turns,
                "remaining_turns": sim_feedback_turns_remaining,
            })
            append_jsonl(run_dir / "events.jsonl", {
                "prob_id": prob_id,
                "event": "pre_sim_semantic",
                "iteration": sem_iter,
                "semantic_gate": gate or None,
                "lean_changed": lean_changed,
                "pass_through": pass_through,
                "missing_gate": missing_gate,
                "deviation_without_repair": deviation_without_repair,
                "remaining_turns": sim_feedback_turns_remaining,
            })
            result = new_result
            if compile_ok_after and after_code:
                best_result = new_result
                best_code = after_code
            if pass_through:
                break
            if missing_gate or deviation_without_repair:
                non_improving_semantic += 1
            elif gate == "DEVIATION" and lean_changed:
                non_improving_semantic = 0
            if not compile_ok_after:
                continue
            if non_improving_semantic >= 3:
                semantic_history.append({
                    "phase": "pre_sim_semantic",
                    "iteration": sem_iter,
                    "note": "Early stop: no ALIGNED gate after repeated sessions (empty gate or DEVIATION without Lean repair).",
                })
                break
            if max_semantic_iters is not None and sem_iter >= max_semantic_iters:
                break
        if lean_file.exists() and best_code:
            lean_file.write_text(best_code, encoding="utf-8")
            result = eval_candidate(skip=True)

    if skip_sim and not args.eval_only:
        sim_eval_t0 = time.monotonic()
        result = eval_candidate(skip=False)
        eval_elapsed += time.monotonic() - sim_eval_t0

    record = {
        "prob_id": prob_id,
        "agent_turns": agent_stats.turns,
        "agent_input_tokens": agent_stats.input_tokens + repair_stats_total.input_tokens,
        "agent_uncached_input_tokens": (
            agent_stats.uncached_input_tokens + repair_stats_total.uncached_input_tokens
        ),
        "agent_cache_creation_input_tokens": (
            agent_stats.cache_creation_input_tokens + repair_stats_total.cache_creation_input_tokens
        ),
        "agent_cache_read_input_tokens": (
            agent_stats.cache_read_input_tokens + repair_stats_total.cache_read_input_tokens
        ),
        "agent_output_tokens": agent_stats.output_tokens + repair_stats_total.output_tokens,
        "agent_token_accounting_complete": (
            agent_stats.token_accounting_complete
            and repair_stats_total.token_accounting_complete
        ),
        "agent_token_usage_recovered_sessions": (
            agent_stats.recovered_usage_sessions
            + repair_stats_total.recovered_usage_sessions
        ),
        "agent_compile_checks": agent_stats.compile_checks + repair_stats_total.compile_checks,
        "agent_tool_counts": {
            **agent_stats.tool_counts,
            **{
                name: agent_stats.tool_counts.get(name, 0) + count
                for name, count in repair_stats_total.tool_counts.items()
            },
        },
        "agent_turn_budget": generation_turn_limit(args),
        "total_turn_budget": args.total_turn_budget,
        "generation_turn_cap": (
            args.generation_turn_cap
            if args.total_turn_budget is not None
            else None
        ),
        "turn_budget_policy": (
            "shared_total_with_generation_cap"
            if args.total_turn_budget is not None
            else "legacy"
        ),
        "prompt_profile": args.prompt_profile,
        "cvdp_harness_profile": args.cvdp_harness_profile,
        "agent_generation_turns": agent_stats.turns,
        "interface_prompt_policy": getattr(args, "interface_prompt_policy", "legacy"),
        "agent_turns_total": agent_stats.turns + repair_stats_total.turns,
        "sim_feedback_enabled": bool(args.sim_feedback),
        "feedback_mode": args.feedback_mode,
        "sim_feedback_iterations": sim_feedback_iterations,
        "sim_feedback_success": sim_feedback_success,
        "sim_feedback_turn_budget": sim_feedback_turn_budget,
        "sim_feedback_turns_remaining": sim_feedback_turns_remaining,
        "sim_feedback_history": sim_feedback_history,
        "pre_sim_semantic_repair": bool(getattr(args, "pre_sim_semantic_repair", False)),
        "semantic_history": semantic_history,
        "public_structural_issues": structural_issues,
        "public_structural_check_count": len(structural_issues),
        "public_structural_repair_used": structural_used,
        "public_structural_repair_disabled": bool(args.disable_public_structural_repair),
        "agent_elapsed_seconds": round(agent_elapsed, 3),
        "eval_elapsed_seconds": round(eval_elapsed, 3),
        "elapsed_seconds": round(time.monotonic() - problem_t0, 3),
        "harness": args.harness,
        "model": model_alias(args.model),
        "agent_input_policy": (
            "cvdp-public-only"
            if args.hide_cvdp_harness_from_agent and evaluator.dataset_name == "cvdp"
            else "legacy"
        ),
        "cvdp_harness_hidden_from_agent": bool(
            args.hide_cvdp_harness_from_agent and evaluator.dataset_name == "cvdp"
        ),
        "cvdp_output_context_hidden_from_agent": bool(
            args.hide_cvdp_harness_from_agent and evaluator.dataset_name == "cvdp"
        ),
        "agent_bash_tool_enabled": not bool(
            args.hide_cvdp_harness_from_agent and evaluator.dataset_name == "cvdp"
        ),
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
    codex_public_only_sandbox = (
        args.codex_sandbox == "workspace-write"
        or (
            args.codex_sandbox == "danger-full-access"
            and bool(args.codex_agent_user)
        )
    )
    if (
        args.hide_cvdp_harness_from_agent
        and args.harness == "codex-agent"
        and not codex_public_only_sandbox
    ):
        raise SystemExit(
            "Codex public-only CVDP runs require --codex-sandbox workspace-write "
            "or --codex-sandbox danger-full-access with --codex-agent-user"
        )
    if args.hide_cvdp_harness_from_agent and args.harness not in {
        "anthropic-api",
        "codex-agent",
    }:
        raise SystemExit(
            "--hide-cvdp-harness-from-agent requires --harness anthropic-api or codex-agent"
        )
    os.environ["CVDP_HARNESS_PROFILE"] = args.cvdp_harness_profile
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

    num_workers = max(1, args.workers)
    print(
        f"[cktarchon] problems={len(problems)} model={model_alias(args.model)} "
        f"harness={args.harness} cvdp_harness_profile={args.cvdp_harness_profile} "
        f"workers={num_workers} run_dir={run_dir}"
    )

    if not args.no_repl:
        try:
            from lean_repl import LeanREPLPool
            pool = LeanREPLPool(size=num_workers, project_dir=PROJECT_ROOT)
        except Exception as exc:
            print(f"[cktarchon] Lean REPL unavailable, falling back to lake build: {exc}")
            pool = None
    else:
        pool = None

    skill = search.load_skill()
    summary = {
        "total": len(problems),
        "skipped": 0,
        "compile_pass": 0,
        "sim_pass": 0,
        "sim_fail": 0,
        "sim_error": 0,
        "agent_error": 0,
        "sim_feedback_attempts": 0,
        "sim_feedback_success": 0,
        "tokens_in": 0,
        "tokens_in_uncached": 0,
        "tokens_in_cache_creation": 0,
        "tokens_in_cache_read": 0,
        "tokens_out": 0,
        "turns": 0,
        "token_accounting_complete": True,
        "token_accounting_incomplete_tasks": 0,
        "token_usage_recovered_sessions": 0,
        "cvdp_harness_profile": args.cvdp_harness_profile,
        "feedback_mode": args.feedback_mode,
    }
    summary_lock = Lock()

    indexed_problems: list[tuple[int, str]] = []
    for idx, prob_id in enumerate(problems, 1):
        if args.resume and already_done(results_base, prob_id, args.resume_mode):
            summary["skipped"] += 1
            print(f"[{idx}/{len(problems)}] skip {prob_id}")
        else:
            indexed_problems.append((idx, prob_id))

    def _run_one(idx: int, prob_id: str) -> tuple[int, str, dict[str, Any]]:
        repl = None
        if pool is not None:
            repl = pool.acquire()
        try:
            evaluator = Evaluator(
                project_root=PROJECT_ROOT,
                enable_synth=args.synth,
                enable_pnr=args.pnr or args.corners,
                enable_drc=args.drc,
                enable_lvs=args.lvs,
                enable_corners=args.corners,
                dataset=args.dataset,
                dataset_obj=ds,
                lean_repl=repl,
                parameter_formal_policy=args.native_formal_policy,
                parameter_cppsim_policy=args.native_cppsim_policy,
                parameter_cppsim_required=args.require_native_cppsim,
                parameter_ppa_policy=args.native_ppa_policy,
                parameter_ppa_required=args.require_native_ppa,
            )
            print(f"[{idx}/{len(problems)}] run {prob_id}")
            record = process_problem(prob_id, args=args, ds=ds, evaluator=evaluator, run_dir=run_dir, skill=skill, repl=repl)
            return idx, prob_id, record
        finally:
            if pool is not None and repl is not None:
                pool.release(repl)

    try:
        with ThreadPoolExecutor(max_workers=num_workers) as executor:
            future_map = {executor.submit(_run_one, idx, prob_id): (idx, prob_id) for idx, prob_id in indexed_problems}
            for future in as_completed(future_map):
                idx, prob_id = future_map[future]
                try:
                    _, _, record = future.result()
                except Exception as exc:
                    record = {
                        "prob_id": prob_id,
                        "compile_pass": False,
                        "lint_pass": False,
                        "sim_status": "agent_error",
                        "agent_error": f"{type(exc).__name__}: {exc}",
                        "cvdp_harness_profile": args.cvdp_harness_profile,
                        "agent_token_accounting_complete": False,
                    }
                    append_jsonl(run_dir / "results.jsonl", record)
                with summary_lock:
                    if record.get("compile_pass"):
                        summary["compile_pass"] += 1
                    summary["sim_feedback_attempts"] += int(record.get("sim_feedback_iterations") or 0)
                    if record.get("sim_feedback_success"):
                        summary["sim_feedback_success"] += 1
                    summary["tokens_in"] += int(record.get("agent_input_tokens") or 0)
                    summary["tokens_in_uncached"] += int(record.get("agent_uncached_input_tokens") or 0)
                    summary["tokens_in_cache_creation"] += int(record.get("agent_cache_creation_input_tokens") or 0)
                    summary["tokens_in_cache_read"] += int(record.get("agent_cache_read_input_tokens") or 0)
                    summary["tokens_out"] += int(record.get("agent_output_tokens") or 0)
                    summary["turns"] += int(record.get("agent_turns_total") or 0)
                    accounting_complete = bool(
                        record.get("agent_token_accounting_complete", True)
                    )
                    if not accounting_complete:
                        summary["token_accounting_complete"] = False
                        summary["token_accounting_incomplete_tasks"] += 1
                    summary["token_usage_recovered_sessions"] += int(
                        record.get("agent_token_usage_recovered_sessions") or 0
                    )
                    status = record.get("sim_status")
                    if status == "sim_pass":
                        summary["sim_pass"] += 1
                    elif status == "sim_fail":
                        summary["sim_fail"] += 1
                    elif status == "agent_error":
                        summary["agent_error"] += 1
                    else:
                        summary["sim_error"] += 1
                print(f"[{idx}/{len(problems)}] done {prob_id}: compile={record.get('compile_pass')} lint={record.get('lint_pass')} sim={record.get('sim_status')} turns={record.get('agent_turns_total')} tok={record.get('agent_input_tokens')}+{record.get('agent_output_tokens')}")
    finally:
        if pool is not None:
            pool.close_all()

    attempted = max(1, len(indexed_problems))
    summary["avg_total_tokens"] = round(
        (summary["tokens_in"] + summary["tokens_out"]) / attempted,
        2,
    )
    summary["avg_turns"] = round(summary["turns"] / attempted, 2)
    summary.update({
        "dataset": args.dataset,
        "model": model_alias(args.model),
        "harness": args.harness,
        "cvdp_harness_profile": args.cvdp_harness_profile,
        "agent_input_policy": (
            "cvdp-public-only"
            if args.hide_cvdp_harness_from_agent and args.dataset == "cvdp"
            else "legacy"
        ),
        "run_dir": str(run_dir),
    })
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
