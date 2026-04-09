#!/usr/bin/env python3
"""
Sparkle Agent Search — run coding agent over VerilogEval problems.

For each problem: agent reads NL description + reference Verilog, writes Sparkle
Lean code, iteratively fixes compilation errors, then evaluator scores the result
(compile → extract SV → lint → sim).

Usage:
    python agent/search.py --limit 5
    python agent/search.py --resume
    python agent/search.py --filter "mux|counter"
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# Add agent/ to path for local imports
sys.path.insert(0, str(Path(__file__).parent))

from coding_agent import CodingAgent
from evaluator import Evaluator
from report import generate_report

from rich.console import Console, Group
from rich.live import Live
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.table import Table

PROJECT_ROOT = Path(__file__).parent.parent.resolve()
DATASET_DIR = PROJECT_ROOT / "verilog-eval" / "dataset_spec-to-rtl"
GENERATED_DIR = PROJECT_ROOT / "Generated"
RESULTS_DIR = PROJECT_ROOT / "results"

console = Console()


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Sparkle Agent: NL → Lean → Verilog")
    p.add_argument("--limit", "-l", type=int, default=None, help="Max problems to process")
    p.add_argument("--resume", "-r", action="store_true", help="Skip already-passed problems")
    p.add_argument("--filter", "-f", type=str, default=None, help="Regex filter on problem IDs")
    p.add_argument("--model", "-m", type=str, default="claude-sonnet-4-20250514")
    p.add_argument("--max-tokens", type=int, default=16384)
    p.add_argument("--max-turns", type=int, default=80)
    p.add_argument("--synth", action="store_true", help="Run synthesis + PPA via siliconcrew/ORFS Docker")
    p.add_argument("--pnr", action="store_true", help="Full P&R (implies --synth)")
    p.add_argument("--drc", action="store_true", help="Run DRC after P&R (implies --pnr --synth)")
    p.add_argument("--lvs", action="store_true", help="Run LVS after P&R (implies --pnr --synth)")
    p.add_argument("--ppa-opt", action="store_true", help="[DEPRECATED] Use --arch-explore instead. Enable PPA optimization feedback loop (implies --synth)")
    p.add_argument("--ppa-iters", type=int, default=3, help="Max PPA optimization iterations (default: 3)")
    p.add_argument("--ppa-turns", type=int, default=30, help="Max agent turns per PPA iteration (default: 30)")
    p.add_argument("--arch-explore", action="store_true", help="Enable architecture exploration mode (implies --synth)")
    p.add_argument("--arch-candidates", type=int, default=3, help="Max architecture candidates to explore (default: 3)")
    p.add_argument("--arch-turns", type=int, default=40, help="Max agent turns per architecture candidate (default: 40)")
    p.add_argument("--area-budget", type=float, default=None, help="Area constraint in μm² (optional)")
    p.add_argument("--latency-budget", type=int, default=None, help="Latency constraint in cycles (optional)")
    p.add_argument("--corners", action="store_true", help="Run multi-corner PVT STA after P&R (implies --pnr --synth)")
    p.add_argument("--quiet", "-q", action="store_true", help="Minimal output (progress bar only)")
    return p.parse_args()


def discover_problems(
    limit: int | None = None,
    filter_re: str | None = None,
) -> list[str]:
    """Discover VerilogEval problem IDs from dataset directory."""
    pattern = re.compile(filter_re) if filter_re else None
    prob_ids = set()
    for f in sorted(DATASET_DIR.iterdir()):
        m = re.match(r"(Prob\d+_\w+)_prompt\.txt$", f.name)
        if not m:
            continue
        pid = m.group(1)
        if pattern and not pattern.search(pid):
            continue
        prob_ids.add(pid)
    result = sorted(prob_ids)
    if limit:
        result = result[:limit]
    return result


def load_skill() -> str:
    """Load the Sparkle skill prompt."""
    skill_path = Path(__file__).parent / "skill.txt"
    return skill_path.read_text()


def build_user_message(prob_id: str) -> str:
    """Build the user message for the agent, including NL description and reference Verilog."""
    prompt_file = DATASET_DIR / f"{prob_id}_prompt.txt"
    ref_file = DATASET_DIR / f"{prob_id}_ref.sv"

    nl_desc = prompt_file.read_text() if prompt_file.exists() else "(no description available)"
    ref_sv = ref_file.read_text() if ref_file.exists() else "(no reference Verilog available)"

    return (
        f"## Problem: {prob_id}\n\n"
        f"### Natural Language Description\n\n{nl_desc}\n\n"
        f"### Reference Verilog (for understanding, NOT for copying)\n\n"
        f"```systemverilog\n{ref_sv}\n```\n\n"
        f"### Your Task\n\n"
        f"Write a Sparkle HDL (Lean 4) implementation for this problem.\n\n"
        f"1. Start by reading a few Benchmark/*.lean examples to see working patterns\n"
        f"2. Write your solution to `Generated/{prob_id}.lean`\n"
        f"3. Run `lake build Generated.{prob_id}` to compile\n"
        f"4. Fix any errors iteratively until it compiles and generates correct Verilog\n"
        f"5. Check the generated Verilog looks correct\n\n"
        f"The file must follow this exact structure:\n"
        f"```lean\n"
        f"import Sparkle\n"
        f"import Sparkle.Compiler.Elab\n\n"
        f"open Sparkle.Core.Domain\n"
        f"open Sparkle.Core.Signal\n\n"
        f"/-- <description> -/\n"
        f"def {prob_id.lower()} {{dom : DomainConfig}}\n"
        f"    (<inputs>) : <output_type> :=\n"
        f"  <implementation>\n\n"
        f"#synthesizeVerilog {prob_id.lower()}\n"
        f"```\n"
    )


def log_event(run_dir: Path, event: dict) -> None:
    """Append an event to results.jsonl."""
    event["timestamp"] = datetime.now().isoformat()
    with open(run_dir / "results.jsonl", "a") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


# ── PPA optimization helpers ────────────────────────────────────


def extract_ppa(result: dict) -> dict:
    """Extract PPA metrics from an evaluator result."""
    return {
        "area_um2": result.get("area_um2"),
        "cell_count": result.get("cell_count"),
        "wns_ns": result.get("wns_ns"),
        "power_uw": result.get("power_uw"),
    }


def ppa_improved(old: dict, new: dict) -> bool:
    """Check if PPA improved: any metric >5% better, none >20% worse."""
    dominated_metrics = {"area_um2": -1, "cell_count": -1, "wns_ns": -1, "power_uw": -1}
    any_improved = False
    for key, direction in dominated_metrics.items():
        ov, nv = old.get(key), new.get(key)
        if ov is None or nv is None or ov == 0:
            continue
        # direction=-1 means lower is better
        change = (nv - ov) / abs(ov) * direction
        if change > 0.05:
            any_improved = True
        if change < -0.20:
            return False  # regressed too much
    return any_improved


def build_ppa_feedback(prob_id: str, result: dict, iteration: int, history: list[dict]) -> str:
    """Build a PPA feedback message for the agent."""
    ppa = extract_ppa(result)
    lines = [
        f"## PPA Optimization Feedback — Iteration {iteration + 1}",
        "",
        f"Your implementation for `{prob_id}` passed functional simulation and synthesis.",
        "Now optimize the design for better PPA (Power, Performance, Area).",
        "",
        "### Current PPA Metrics",
        f"- Area: {ppa['area_um2']:.2f} μm²" if ppa["area_um2"] is not None else "- Area: N/A",
        f"- Cell count: {ppa['cell_count']}" if ppa["cell_count"] is not None else "- Cell count: N/A",
        f"- WNS (worst negative slack): {ppa['wns_ns']:.2f} ns" if ppa["wns_ns"] is not None else "- WNS: N/A",
        f"- Power: {ppa['power_uw']:.4f} μW" if ppa["power_uw"] is not None else "- Power: N/A",
    ]

    if len(history) > 1:
        lines.append("")
        lines.append("### PPA History")
        lines.append("| Iter | Area (μm²) | Cells | WNS (ns) | Power (μW) |")
        lines.append("|------|-----------|-------|----------|------------|")
        for idx, h in enumerate(history):
            a = f"{h['area_um2']:.2f}" if h["area_um2"] is not None else "N/A"
            c = str(h["cell_count"]) if h["cell_count"] is not None else "N/A"
            w = f"{h['wns_ns']:.2f}" if h["wns_ns"] is not None else "N/A"
            p = f"{h['power_uw']:.4f}" if h["power_uw"] is not None else "N/A"
            label = "baseline" if idx == 0 else str(idx)
            lines.append(f"| {label} | {a} | {c} | {w} | {p} |")

    # Suggest focus area
    lines.append("")
    lines.append("### Optimization Focus")
    if ppa["wns_ns"] is not None and ppa["wns_ns"] < 0:
        lines.append("- **CRITICAL**: WNS is negative — timing violation. Focus on breaking the critical path.")
    elif ppa["area_um2"] is not None and ppa["cell_count"] is not None:
        lines.append("- Focus on reducing area and cell count through logic simplification.")
    lines.append("")
    lines.append("### Instructions (Verified Optimization)")
    lines.append(f"You MUST prove functional equivalence when optimizing. Follow these steps:")
    lines.append(f"1. Read your current `Generated/{prob_id}.lean`")
    lines.append(f"2. Rename your current working implementation to `{prob_id.lower()}_spec` (copy it verbatim)")
    lines.append(f"3. Write the optimized version as `{prob_id.lower()}`")
    lines.append(f"4. Write a theorem `{prob_id.lower()}_equiv` proving the optimized version equals `_spec`")
    lines.append(f"5. The proof MUST compile without `sorry` — use `unfold`/`rfl`/`simp`/`ext t` tactics")
    lines.append(f"6. If you cannot prove equivalence, do NOT optimize — keep the original unchanged")
    lines.append(f"7. Re-compile with `lake build Generated.{prob_id}` — check no errors AND no sorry warnings")
    lines.append(f"8. `#synthesizeVerilog` must reference the optimized `{prob_id.lower()}`, not `_spec`")

    return "\n".join(lines)


# ── Architecture exploration helpers ─────────────────────────────


@dataclass
class ArchCandidate:
    index: int
    code: str
    ppa: dict
    sim_pass: bool
    verified: bool
    description: str


def pareto_dominant(a: dict, b: dict) -> bool:
    """Return True if a Pareto-dominates b (all metrics no worse, at least one better)."""
    keys = ["area_um2", "cell_count", "power_uw"]
    any_better = False
    for k in keys:
        av, bv = a.get(k), b.get(k)
        if av is None or bv is None:
            continue
        if av > bv:
            return False
        if av < bv:
            any_better = True
    # WNS: closer to 0 is better (less negative = better)
    aw, bw = a.get("wns_ns"), b.get("wns_ns")
    if aw is not None and bw is not None:
        if aw < bw:
            return False
        if aw > bw:
            any_better = True
    return any_better


def select_best_candidate(
    candidates: list[ArchCandidate],
    constraints: dict,
) -> ArchCandidate:
    """Select best candidate: must pass sim, prefer verified, then smallest area."""
    valid = [c for c in candidates if c.sim_pass]
    if not valid:
        return candidates[0]  # fallback to initial

    # Filter by constraints if specified
    budget = constraints.get("area_budget")
    if budget is not None:
        within = [c for c in valid if c.ppa.get("area_um2") is not None and c.ppa["area_um2"] <= budget]
        if within:
            valid = within

    # Sort: verified first, then by area (smallest), then by cell count
    valid.sort(key=lambda c: (
        not c.verified,  # verified=True → 0 (first)
        c.ppa.get("area_um2") or float("inf"),
        c.ppa.get("cell_count") or float("inf"),
    ))
    return valid[0]


def build_arch_feedback(
    prob_id: str,
    candidates: list[ArchCandidate],
    iteration: int,
    constraints: dict,
) -> str:
    """Build architecture exploration feedback with PPA comparison table."""
    func_name = prob_id.lower()
    lines = [
        f"## Architecture Exploration — Candidate {iteration + 2}",
        "",
        f"Your task: write a COMPLETELY DIFFERENT architecture for `{prob_id}`.",
        "Do NOT tweak the existing implementation — write a new one from scratch.",
        "",
        "### Candidates So Far",
        "| # | Description | Sim | Verified | Area (μm²) | Cells | WNS (ns) | Power (μW) |",
        "|---|-------------|-----|----------|-----------|-------|----------|------------|",
    ]
    for c in candidates:
        sim = "Pass" if c.sim_pass else "FAIL"
        v = "Yes" if c.verified else "No"
        a = f"{c.ppa['area_um2']:.1f}" if c.ppa.get("area_um2") is not None else "N/A"
        cells = str(c.ppa["cell_count"]) if c.ppa.get("cell_count") is not None else "N/A"
        w = f"{c.ppa['wns_ns']:.3f}" if c.ppa.get("wns_ns") is not None else "N/A"
        p = f"{c.ppa['power_uw']:.4f}" if c.ppa.get("power_uw") is not None else "N/A"
        lines.append(f"| v{c.index} | {c.description} | {sim} | {v} | {a} | {cells} | {w} | {p} |")

    lines.append("")

    # Constraints
    budget = constraints.get("area_budget")
    latency = constraints.get("latency_budget")
    if budget or latency:
        lines.append("### Constraints")
        if budget:
            lines.append(f"- Area budget: {budget:.1f} μm²")
        if latency:
            lines.append(f"- Latency budget: {latency} cycles")
        lines.append("")

    # Suggestions based on what's been tried
    lines.append("### Suggestions")
    lines.append("Try a fundamentally different approach:")
    if len(candidates) == 1:
        lines.append("- If the initial design is combinational, try a pipelined or sequential version")
        lines.append("- If it uses a flat mux tree, try a hierarchical or encoded approach")
    else:
        tried = ", ".join(f"v{c.index} ({c.description})" for c in candidates)
        lines.append(f"- Already tried: {tried}")
        lines.append("- Explore a different point in the parallelism/pipeline/resource-sharing space")

    lines.append("")
    lines.append("### Instructions (Verified Architecture)")
    lines.append(f"1. Read your current `Generated/{prob_id}.lean`")
    lines.append(f"2. Keep the `{func_name}_spec` function as-is (or copy your original working implementation into `{func_name}_spec` if it doesn't exist yet)")
    lines.append(f"3. Write a completely new architecture as `{func_name}`")
    lines.append(f"4. Write a theorem `{func_name}_equiv` proving `{func_name} = {func_name}_spec`")
    lines.append(f"5. Use `unfold`/`rfl`/`simp`/`ext t`/`bv_omega` tactics — see `Sparkle.Verification.SignalDSLProps` for available @[simp] lemmas")
    lines.append(f"6. If you CANNOT prove equivalence, still write the new architecture — use `sorry` as a placeholder. It will be marked as unverified but still evaluated via simulation")
    lines.append(f"7. Compile with `lake build Generated.{prob_id}` — check for errors")
    lines.append(f"8. `#synthesizeVerilog` must reference `{func_name}`, not `{func_name}_spec`")

    return "\n".join(lines)


def print_summary_table(stats: dict, elapsed: float, synth_enabled: bool = False, pnr_enabled: bool = False, drc_enabled: bool = False, lvs_enabled: bool = False, ppa_opt_enabled: bool = False, arch_explore_enabled: bool = False) -> None:
    """Print a rich summary table."""
    attempted = stats["total"] - stats["skipped"]
    sim_rate = f"{stats['sim_pass']/attempted*100:.1f}%" if attempted > 0 else "N/A"
    compile_rate = f"{stats['compile_pass']/attempted*100:.1f}%" if attempted > 0 else "N/A"

    h, rem = divmod(int(elapsed), 3600)
    m, s = divmod(rem, 60)
    elapsed_str = f"{h}:{m:02d}:{s:02d}"

    table = Table(title="Sparkle Agent Summary", show_header=False, border_style="cyan")
    table.add_column("Key", style="bold")
    table.add_column("Value")

    table.add_row("Problems", f"{attempted} attempted / {stats['total']} total")
    if stats["skipped"]:
        table.add_row("Skipped", str(stats["skipped"]))
    table.add_row("Compile pass", f"[green]{stats['compile_pass']}[/green] ({compile_rate})")
    table.add_row("Sim pass", f"[green]{stats['sim_pass']}[/green] ({sim_rate})")
    table.add_row("Sim fail", f"[red]{stats['sim_fail']}[/red]" if stats["sim_fail"] else "0")
    table.add_row("Sim error", f"[yellow]{stats['sim_error']}[/yellow]" if stats["sim_error"] else "0")
    if synth_enabled:
        synth_rate = f"{stats['synth_pass']/attempted*100:.1f}%" if attempted > 0 else "N/A"
        table.add_row("Synth pass", f"[green]{stats['synth_pass']}[/green] ({synth_rate})")
    if pnr_enabled:
        pnr_rate = f"{stats['pnr_pass']/attempted*100:.1f}%" if attempted > 0 else "N/A"
        table.add_row("P&R pass", f"[green]{stats['pnr_pass']}[/green] ({pnr_rate})")
    if drc_enabled:
        drc_rate = f"{stats['drc_pass']/attempted*100:.1f}%" if attempted > 0 else "N/A"
        table.add_row("DRC pass", f"[green]{stats['drc_pass']}[/green] ({drc_rate})")
    if lvs_enabled:
        lvs_rate = f"{stats['lvs_pass']/attempted*100:.1f}%" if attempted > 0 else "N/A"
        table.add_row("LVS pass", f"[green]{stats['lvs_pass']}[/green] ({lvs_rate})")
    if ppa_opt_enabled:
        table.add_row("PPA optimized", f"[yellow]{stats['ppa_optimized']}[/yellow] ({stats['ppa_iterations_total']} iters)")
    if arch_explore_enabled:
        table.add_row("Arch explored", f"[blue]{stats['arch_explored']}[/blue] ({stats['arch_candidates_total']} candidates)")
    table.add_row("Tokens", f"{stats['agent_tokens']['input']}+{stats['agent_tokens']['output']}")
    table.add_row("Time", elapsed_str)
    table.add_row("Model", stats.get("model", ""))

    console.print()
    console.print(table)


def main():
    args = parse_args()
    t0 = time.monotonic()

    # --ppa-opt / --arch-explore implies --synth
    if args.ppa_opt or args.arch_explore:
        args.synth = True

    # Discover problems
    problems = discover_problems(limit=args.limit, filter_re=args.filter)
    if not problems:
        console.print("[red]No problems found matching criteria.[/red]")
        sys.exit(1)

    synth_str = ", [magenta]synth+PPA[/magenta]" if args.synth else ""
    ppa_opt_str = f", [yellow]PPA-opt({args.ppa_iters}x)[/yellow]" if args.ppa_opt else ""
    arch_str = f", [blue]arch-explore({args.arch_candidates}x)[/blue]" if args.arch_explore else ""
    pnr_str = ""
    if args.pnr or args.drc or args.lvs:
        parts = ["P&R"]
        if args.drc:
            parts.append("DRC")
        if args.lvs:
            parts.append("LVS")
        pnr_str = f", [cyan]{'+'.join(parts)}[/cyan]"
    console.print(f"Found [cyan]{len(problems)}[/cyan] problems, model: [cyan]{args.model}[/cyan]{synth_str}{ppa_opt_str}{arch_str}{pnr_str}\n")

    # Set up results directory
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = RESULTS_DIR / f"agent_run_{timestamp}"
    run_dir.mkdir(parents=True, exist_ok=True)

    # Ensure Generated/ exists
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)

    # Load skill prompt
    skill = load_skill()

    # Create evaluator
    evaluator = Evaluator(project_root=PROJECT_ROOT, enable_synth=args.synth, enable_pnr=args.pnr, enable_drc=args.drc, enable_lvs=args.lvs, enable_corners=args.corners)

    # Track stats
    stats = {
        "total": len(problems),
        "compile_pass": 0,
        "sim_pass": 0,
        "sim_fail": 0,
        "sim_error": 0,
        "synth_pass": 0,
        "pnr_pass": 0,
        "drc_pass": 0,
        "lvs_pass": 0,
        "ppa_optimized": 0,
        "ppa_iterations_total": 0,
        "arch_explored": 0,
        "arch_candidates_total": 0,
        "skipped": 0,
        "agent_tokens": {"input": 0, "output": 0},
        "model": args.model,
    }

    # ── Progress bars ────────────────────────────────────────────
    overall_progress = Progress(
        SpinnerColumn(),
        MofNCompleteColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
    )

    current_progress = Progress(
        SpinnerColumn(),
        TimeElapsedColumn(),
        TextColumn("[progress.description]{task.description}"),
    )

    with Live(Group(overall_progress, current_progress), console=console, refresh_per_second=4):
        overall_task = overall_progress.add_task(
            "Running trials...", total=len(problems)
        )

        for i, prob_id in enumerate(problems, 1):
            attempted = i - stats["skipped"]
            sim_rate = f"{stats['sim_pass']/attempted*100:.0f}%" if attempted > 0 else "-"

            overall_progress.update(
                overall_task,
                description=(
                    f"Pass: [green]{stats['sim_pass']}[/green]/{attempted}  "
                    f"({sim_rate})"
                ),
            )

            # Check resume
            if args.resume:
                lean_file = GENERATED_DIR / f"{prob_id}.lean"
                if lean_file.exists():
                    prev_results = list(run_dir.parent.glob("*/results.jsonl"))
                    already_passed = False
                    for prev in prev_results:
                        try:
                            for line in prev.read_text().splitlines():
                                r = json.loads(line)
                                if r.get("prob_id") == prob_id and r.get("sim_status") == "sim_pass":
                                    already_passed = True
                                    break
                        except Exception:
                            pass
                        if already_passed:
                            break

                    if already_passed:
                        stats["skipped"] += 1
                        overall_progress.advance(overall_task)
                        continue

            # Phase 1: Run coding agent
            agent_task = current_progress.add_task(
                f"[cyan]{prob_id}[/cyan]  Agent running...",
            )

            agent = CodingAgent(
                model=args.model,
                max_tokens=args.max_tokens,
                project_root=PROJECT_ROOT,
                log_dir=run_dir / "logs" / prob_id,
            )

            user_msg = build_user_message(prob_id)
            agent_stats = None
            try:
                agent_stats = agent.run(
                    system_prompt=skill,
                    user_message=user_msg,
                    max_turns=args.max_turns,
                )
                stats["agent_tokens"]["input"] += agent_stats["input_tokens"]
                stats["agent_tokens"]["output"] += agent_stats["output_tokens"]

                current_progress.update(
                    agent_task,
                    description=(
                        f"[cyan]{prob_id}[/cyan]  Agent done "
                        f"({agent_stats['turns']}t, "
                        f"{agent_stats['input_tokens']}+{agent_stats['output_tokens']} tok)  "
                        f"Evaluating..."
                    ),
                )
            except Exception as e:
                current_progress.update(
                    agent_task,
                    description=f"[red]{prob_id}[/red]  Agent error: {e}",
                )
                log_event(run_dir, {
                    "prob_id": prob_id,
                    "agent_error": str(e),
                    "sim_status": "agent_error",
                })
                current_progress.remove_task(agent_task)
                overall_progress.advance(overall_task)
                continue

            # Phase 2: Evaluate
            result = evaluator.evaluate(prob_id, run_dir)

            # Phase 3: PPA optimization loop (only with --ppa-opt)
            ppa_history = []
            if (
                args.ppa_opt
                and result["sim_status"] == "sim_pass"
                and result.get("synth_pass")
            ):
                ppa_history.append(extract_ppa(result))
                lean_file = GENERATED_DIR / f"{prob_id}.lean"
                messages = agent_stats.get("messages", [])
                best_code = lean_file.read_text()
                best_ppa = ppa_history[0]
                best_result = result

                for ppa_iter in range(args.ppa_iters):
                    current_progress.update(
                        agent_task,
                        description=(
                            f"[cyan]{prob_id}[/cyan]  "
                            f"[yellow]PPA opt {ppa_iter + 1}/{args.ppa_iters}[/yellow]..."
                        ),
                    )

                    # Backup current Lean file for rollback
                    backup_code = lean_file.read_text()

                    # Build feedback and resume agent
                    feedback = build_ppa_feedback(prob_id, result, ppa_iter, ppa_history)
                    try:
                        opt_stats = agent.resume(
                            system_prompt=skill,
                            messages=messages,
                            feedback_message=feedback,
                            max_turns=args.ppa_turns,
                        )
                        messages = opt_stats["messages"]
                        stats["agent_tokens"]["input"] += opt_stats["input_tokens"]
                        stats["agent_tokens"]["output"] += opt_stats["output_tokens"]
                    except Exception as e:
                        log_event(run_dir, {
                            "prob_id": prob_id,
                            "ppa_iteration": ppa_iter + 1,
                            "ppa_error": str(e),
                        })
                        break

                    # Re-evaluate
                    new_result = evaluator.evaluate(prob_id, run_dir)

                    # Check verification status (no sorry = formally verified)
                    verified = not new_result.get("has_sorry", True)

                    # Check: functional correctness preserved?
                    if new_result["sim_status"] != "sim_pass":
                        # Rollback to pre-iteration code and continue
                        lean_file.write_text(backup_code)
                        log_event(run_dir, {
                            "prob_id": prob_id,
                            "ppa_iteration": ppa_iter + 1,
                            "ppa_verified": verified,
                            "ppa_status": "rollback_sim_fail",
                            **extract_ppa(new_result),
                        })
                        continue

                    # Check: PPA improved vs global best?
                    new_ppa = extract_ppa(new_result)
                    improved = ppa_improved(best_ppa, new_ppa)
                    status = "verified_improved" if verified and improved else \
                             "unverified_improved" if improved else \
                             "verified_converged" if verified else "converged"
                    log_event(run_dir, {
                        "prob_id": prob_id,
                        "ppa_iteration": ppa_iter + 1,
                        "ppa_verified": verified,
                        "ppa_status": status,
                        **new_ppa,
                    })

                    ppa_history.append(new_ppa)
                    stats["ppa_iterations_total"] += 1
                    if improved:
                        best_ppa = new_ppa
                        best_code = lean_file.read_text()
                        best_result = new_result

                # Restore global best code at the end
                lean_file.write_text(best_code)
                result = best_result

                if len(ppa_history) > 1:
                    stats["ppa_optimized"] += 1

            # Phase 3b: Architecture exploration loop (only with --arch-explore)
            arch_candidates = []
            if (
                args.arch_explore
                and not args.ppa_opt  # don't run both
                and result["sim_status"] == "sim_pass"
                and result.get("synth_pass")
            ):
                lean_file = GENERATED_DIR / f"{prob_id}.lean"
                messages = agent_stats.get("messages", [])
                constraints = {
                    "area_budget": args.area_budget,
                    "latency_budget": args.latency_budget,
                }

                # Baseline candidate
                arch_candidates.append(ArchCandidate(
                    index=0,
                    code=lean_file.read_text(),
                    ppa=extract_ppa(result),
                    sim_pass=True,
                    verified=not result.get("has_sorry", True),
                    description="initial",
                ))

                for arch_iter in range(args.arch_candidates):
                    current_progress.update(
                        agent_task,
                        description=(
                            f"[cyan]{prob_id}[/cyan]  "
                            f"[blue]Arch {arch_iter + 1}/{args.arch_candidates}[/blue]..."
                        ),
                    )

                    feedback = build_arch_feedback(prob_id, arch_candidates, arch_iter, constraints)
                    try:
                        opt_stats = agent.resume(
                            system_prompt=skill,
                            messages=messages,
                            feedback_message=feedback,
                            max_turns=args.arch_turns,
                        )
                        messages = opt_stats["messages"]
                        stats["agent_tokens"]["input"] += opt_stats["input_tokens"]
                        stats["agent_tokens"]["output"] += opt_stats["output_tokens"]
                    except Exception as e:
                        log_event(run_dir, {
                            "prob_id": prob_id,
                            "arch_iteration": arch_iter + 1,
                            "arch_error": str(e),
                        })
                        break

                    new_result = evaluator.evaluate(prob_id, run_dir)
                    new_candidate = ArchCandidate(
                        index=arch_iter + 1,
                        code=lean_file.read_text(),
                        ppa=extract_ppa(new_result),
                        sim_pass=new_result["sim_status"] == "sim_pass",
                        verified=not new_result.get("has_sorry", True),
                        description=f"candidate_{arch_iter + 1}",
                    )
                    arch_candidates.append(new_candidate)

                    log_event(run_dir, {
                        "prob_id": prob_id,
                        "arch_iteration": arch_iter + 1,
                        "arch_sim_pass": new_candidate.sim_pass,
                        "arch_verified": new_candidate.verified,
                        **new_candidate.ppa,
                    })

                # Select best and restore
                best = select_best_candidate(arch_candidates, constraints)
                if best.index != arch_candidates[-1].index:
                    lean_file.write_text(best.code)
                    result = evaluator.evaluate(prob_id, run_dir)
                else:
                    result = new_result  # already the latest

                stats["arch_explored"] += 1
                stats["arch_candidates_total"] += len(arch_candidates)

            status_icon = {
                "sim_pass": "[green]✓[/green]",
                "sim_fail": "[red]✗[/red]",
                "sim_error": "[yellow]![/yellow]",
                "not_run": "–",
            }.get(result["sim_status"], "?")

            compile_str = "[green]C✓[/green]" if result["compile_pass"] else "[red]C✗[/red]"
            lint_str = "[green]L✓[/green]" if result["lint_pass"] else "[red]L✗[/red]"
            detail_short = result["detail"][:50]

            # Build synth/PPA suffix
            synth_str = ""
            if args.synth and result.get("synth_pass"):
                stats["synth_pass"] += 1
                ppa_parts = []
                if result.get("area_um2") is not None:
                    ppa_parts.append(f"A={result['area_um2']:.0f}")
                if result.get("cell_count") is not None:
                    ppa_parts.append(f"C={result['cell_count']}")
                if result.get("wns_ns") is not None:
                    ppa_parts.append(f"WNS={result['wns_ns']:.2f}")
                synth_str = "  [green]S✓[/green]" + (f" {' '.join(ppa_parts)}" if ppa_parts else "")
            elif args.synth and result.get("sv_extracted"):
                synth_str = "  [red]S✗[/red]"

            # Build PNR/DRC/LVS suffix
            pnr_str = ""
            if args.pnr or args.drc or args.lvs:
                if result.get("pnr_pass"):
                    stats["pnr_pass"] += 1
                    pnr_str += "  [green]P✓[/green]"
                elif result.get("synth_pass"):
                    pnr_str += "  [red]P✗[/red]"
                if args.drc:
                    if result.get("drc_pass"):
                        stats["drc_pass"] += 1
                        drc_v = result.get("drc_violations", 0)
                        pnr_str += f"  [green]D✓({drc_v})[/green]"
                    elif result.get("pnr_pass"):
                        pnr_str += "  [red]D✗[/red]"
                if args.lvs:
                    if result.get("lvs_pass"):
                        stats["lvs_pass"] += 1
                        pnr_str += "  [green]V✓[/green]"
                    elif result.get("lvs_error"):
                        pnr_str += f"  [yellow]V?[/yellow]"
                    elif result.get("pnr_pass"):
                        pnr_str += "  [red]V✗[/red]"

            current_progress.update(
                agent_task,
                description=(
                    f"{status_icon} [cyan]{prob_id}[/cyan]  "
                    f"{compile_str} {lint_str}  "
                    f"{result['sim_status']}  {detail_short}{synth_str}{pnr_str}"
                ),
            )

            if result["compile_pass"]:
                stats["compile_pass"] += 1
            if result["sim_status"] == "sim_pass":
                stats["sim_pass"] += 1
            elif result["sim_status"] == "sim_fail":
                stats["sim_fail"] += 1
            else:
                stats["sim_error"] += 1

            # Log result
            arch_history = None
            if arch_candidates:
                arch_history = [
                    {
                        "index": c.index,
                        "description": c.description,
                        "sim_pass": c.sim_pass,
                        "verified": c.verified,
                        **c.ppa,
                    }
                    for c in arch_candidates
                ]
                # Mark which candidate was selected
                best = select_best_candidate(arch_candidates, {
                    "area_budget": args.area_budget,
                    "latency_budget": args.latency_budget,
                })
                arch_history_with_best = {
                    "candidates": arch_history,
                    "selected": best.index,
                }
            log_event(run_dir, {
                "prob_id": prob_id,
                "agent_turns": agent_stats.get("turns", 0) if agent_stats else 0,
                "agent_input_tokens": agent_stats.get("input_tokens", 0) if agent_stats else 0,
                "agent_output_tokens": agent_stats.get("output_tokens", 0) if agent_stats else 0,
                "ppa_history": ppa_history if ppa_history else None,
                "arch_history": arch_history_with_best if arch_candidates else None,
                **result,
            })

            overall_progress.advance(overall_task)

    # ── Summary ──────────────────────────────────────────────────
    elapsed = time.monotonic() - t0
    attempted = stats["total"] - stats["skipped"]
    sim_rate = f"{stats['sim_pass']/attempted*100:.1f}%" if attempted > 0 else "N/A"
    compile_rate = f"{stats['compile_pass']/attempted*100:.1f}%" if attempted > 0 else "N/A"

    summary = {
        "total": stats["total"],
        "attempted": attempted,
        "skipped": stats["skipped"],
        "compile_pass": stats["compile_pass"],
        "compile_rate": compile_rate,
        "sim_pass": stats["sim_pass"],
        "sim_fail": stats["sim_fail"],
        "sim_error": stats["sim_error"],
        "sim_rate": sim_rate,
        "synth_pass": stats["synth_pass"],
        "pnr_pass": stats["pnr_pass"],
        "drc_pass": stats["drc_pass"],
        "lvs_pass": stats["lvs_pass"],
        "ppa_optimized": stats["ppa_optimized"],
        "ppa_iterations_total": stats["ppa_iterations_total"],
        "agent_tokens": stats["agent_tokens"],
        "elapsed_seconds": int(elapsed),
        "model": args.model,
        "synth_enabled": args.synth,
        "pnr_enabled": args.pnr or args.drc or args.lvs,
        "drc_enabled": args.drc,
        "lvs_enabled": args.lvs,
        "ppa_opt_enabled": args.ppa_opt,
        "arch_explore_enabled": args.arch_explore,
        "arch_explored": stats["arch_explored"],
        "arch_candidates_total": stats["arch_candidates_total"],
    }
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2))

    print_summary_table(stats, elapsed, synth_enabled=args.synth, pnr_enabled=args.pnr or args.drc or args.lvs, drc_enabled=args.drc, lvs_enabled=args.lvs, ppa_opt_enabled=args.ppa_opt, arch_explore_enabled=args.arch_explore)

    # Auto-generate HTML report
    report_path = generate_report(run_dir)
    console.print(f"\nReport: [cyan]{report_path}[/cyan]")
    console.print(f"Results: [cyan]{run_dir}[/cyan]")


if __name__ == "__main__":
    main()
