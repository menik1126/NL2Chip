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


def print_summary_table(stats: dict, elapsed: float, synth_enabled: bool = False, pnr_enabled: bool = False, drc_enabled: bool = False, lvs_enabled: bool = False) -> None:
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
    table.add_row("Tokens", f"{stats['agent_tokens']['input']}+{stats['agent_tokens']['output']}")
    table.add_row("Time", elapsed_str)
    table.add_row("Model", stats.get("model", ""))

    console.print()
    console.print(table)


def main():
    args = parse_args()
    t0 = time.monotonic()

    # Discover problems
    problems = discover_problems(limit=args.limit, filter_re=args.filter)
    if not problems:
        console.print("[red]No problems found matching criteria.[/red]")
        sys.exit(1)

    synth_str = ", [magenta]synth+PPA[/magenta]" if args.synth else ""
    pnr_str = ""
    if args.pnr or args.drc or args.lvs:
        parts = ["P&R"]
        if args.drc:
            parts.append("DRC")
        if args.lvs:
            parts.append("LVS")
        pnr_str = f", [cyan]{'+'.join(parts)}[/cyan]"
    console.print(f"Found [cyan]{len(problems)}[/cyan] problems, model: [cyan]{args.model}[/cyan]{synth_str}{pnr_str}\n")

    # Set up results directory
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = RESULTS_DIR / f"agent_run_{timestamp}"
    run_dir.mkdir(parents=True, exist_ok=True)

    # Ensure Generated/ exists
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)

    # Load skill prompt
    skill = load_skill()

    # Create evaluator
    evaluator = Evaluator(project_root=PROJECT_ROOT, enable_synth=args.synth, enable_pnr=args.pnr, enable_drc=args.drc, enable_lvs=args.lvs)

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
            log_event(run_dir, {
                "prob_id": prob_id,
                "agent_turns": agent_stats.get("turns", 0) if agent_stats else 0,
                "agent_input_tokens": agent_stats.get("input_tokens", 0) if agent_stats else 0,
                "agent_output_tokens": agent_stats.get("output_tokens", 0) if agent_stats else 0,
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
        "agent_tokens": stats["agent_tokens"],
        "elapsed_seconds": int(elapsed),
        "model": args.model,
        "synth_enabled": args.synth,
        "pnr_enabled": args.pnr or args.drc or args.lvs,
        "drc_enabled": args.drc,
        "lvs_enabled": args.lvs,
    }
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2))

    print_summary_table(stats, elapsed, synth_enabled=args.synth, pnr_enabled=args.pnr or args.drc or args.lvs, drc_enabled=args.drc, lvs_enabled=args.lvs)

    # Auto-generate HTML report
    report_path = generate_report(run_dir)
    console.print(f"\nReport: [cyan]{report_path}[/cyan]")
    console.print(f"Results: [cyan]{run_dir}[/cyan]")


if __name__ == "__main__":
    main()
