#!/usr/bin/env python3
"""
Equiv Check: 对 baseline 仿真通过的设计做 Yosys 形式化等价验证。
对比 baseline 生成的 TopModule 和 VerilogEval 的 RefModule。

如果 equiv_check fail → 仿真通过但功能不等价（假阳性）。

用法:
    python3 experiments/equiv_check.py                                    # 自动找最新 baseline 结果
    python3 experiments/equiv_check.py --baseline-dir results/baseline_verilog_20260410_215301
    python3 experiments/equiv_check.py --prob Prob005_notgate             # 只跑一题
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)

PROJECT_ROOT = Path(__file__).parent.parent.resolve()
DATASET_DIR = PROJECT_ROOT / "verilog-eval" / "dataset_spec-to-rtl"
RESULTS_DIR = PROJECT_ROOT / "results"
DOCKER_IMAGE = "openroad/orfs:latest"

console = Console(force_terminal=True)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Equiv check: Yosys formal verification on baseline or agent_run results")
    p.add_argument("--baseline-dir", type=str, default=None, help="Baseline results directory")
    p.add_argument("--agent-run", type=str, default=None, help="agent_run results directory (Sparkle HDL)")
    p.add_argument("--prob", type=str, default=None, help="Only check this problem ID")
    p.add_argument("--timeout", type=int, default=120, help="Yosys timeout per problem (seconds)")
    return p.parse_args()


def find_latest_baseline() -> Path | None:
    dirs = sorted(RESULTS_DIR.glob("baseline_verilog_*/"), reverse=True)
    return dirs[0] if dirs else None


def find_latest_agent_run() -> Path | None:
    """Find the latest agent_run directory that has sim results."""
    candidates = []
    for d in sorted(RESULTS_DIR.iterdir(), reverse=True):
        if d.is_dir() and d.name.startswith("agent_run_") and (d / "sim").exists():
            candidates.append(d)
    return candidates[0] if candidates else None


def get_sim_passing(baseline_dir: Path) -> list[str]:
    """Get list of prob_ids that passed simulation."""
    results_file = baseline_dir / "results.jsonl"
    if not results_file.exists():
        return []
    passing = []
    for line in results_file.read_text().splitlines():
        try:
            r = json.loads(line)
            if r.get("sim_status") == "sim_pass":
                passing.append(r["prob_id"])
        except Exception:
            pass
    return passing


def get_dut_sv_for_baseline(prob_id: str, baseline_dir: Path) -> Path | None:
    """Get the DUT SV file for a baseline problem."""
    sv = baseline_dir / f"{prob_id}.sv"
    return sv if sv.exists() else None


def get_dut_sv_for_agent_run(prob_id: str, agent_run_dir: Path, work_dir: Path) -> Path | None:
    """
    Get the DUT SV file for an agent_run problem.
    Combines sparkle_dut.sv + wrapper.sv into a single file with TopModule.
    """
    sim_dir = agent_run_dir / "sim" / prob_id
    dut_sv = sim_dir / "sparkle_dut.sv"
    wrapper_sv = sim_dir / "wrapper.sv"

    if not dut_sv.exists() or not wrapper_sv.exists():
        return None

    # Combine into a single file
    work_dir.mkdir(parents=True, exist_ok=True)
    combined = work_dir / "dut.sv"
    combined.write_text(dut_sv.read_text() + "\n" + wrapper_sv.read_text())
    return combined


def run_equiv_check(
    prob_id: str,
    baseline_sv: Path,
    ref_sv: Path,
    work_dir: Path,
    timeout: int = 120,
) -> dict:
    """
    Run Yosys equiv_check via Docker.
    Compares TopModule (baseline) against RefModule (reference).
    """
    result = {
        "prob_id": prob_id,
        "equiv_status": "error",
        "detail": "",
    }

    if not baseline_sv.exists():
        result["detail"] = "Baseline SV not found"
        return result
    if not ref_sv.exists():
        result["detail"] = "Reference SV not found"
        return result

    work_dir.mkdir(parents=True, exist_ok=True)

    # Read both files
    baseline_code = baseline_sv.read_text()
    ref_code = ref_sv.read_text()

    # Check if the design has sequential logic (clk port)
    has_clk = bool(re.search(r'\binput\b.*\bclk\b', baseline_code))

    # Yosys equiv_check works best on combinational logic.
    # For sequential circuits, we use equiv_make + equiv_simple + equiv_induct.
    # Write a Yosys script that:
    # 1. Reads RefModule as "gold"
    # 2. Reads TopModule as "gate"
    # 3. Runs equiv_check

    # Write files to work_dir
    (work_dir / "ref.sv").write_text(ref_code)
    (work_dir / "dut.sv").write_text(baseline_code)

    # Manual proc sub-steps, skipping proc_dlatch to avoid latch inference errors
    # from incomplete case/if in RefModule (e.g. always_comb without default)
    proc_steps = (
        "proc_clean; proc_rmdead; proc_prune; proc_init; proc_arst; "
        "proc_rom; proc_mux; proc_dff; proc_memwr; proc_clean; "
        "opt_expr -keepdc; "
    )

    # Common preamble: read gold & gate, handle async FFs and latches
    preamble = (
        "read_verilog -sv /workspace/ref.sv; "
        "rename RefModule equiv_gold; "
        "hierarchy -top equiv_gold; "
        + proc_steps +
        "async2sync; "          # Convert async resets to sync (fixes $adff errors)
        "memory; "              # Convert memory blocks to FFs (fixes 'contains memories')
        "flatten; opt; "
        "design -stash gold; "
        "read_verilog -sv /workspace/dut.sv; "
        "rename TopModule equiv_gate; "
        "hierarchy -top equiv_gate; "
        + proc_steps +
        "async2sync; "
        "memory; "
        "flatten; opt; "
        "design -stash gate; "
        "design -copy-from gold -as equiv_gold equiv_gold; "
        "design -copy-from gate -as equiv_gate equiv_gate; "
        "equiv_make equiv_gold equiv_gate equiv; "
        "hierarchy -top equiv; "
        "clean -purge; "
    )

    if has_clk:
        # Sequential: equiv_simple → equiv_induct with deeper unrolling
        yosys_script = (
            preamble +
            "equiv_simple -seq 10; "     # Allow up to 10 clock cycles
            "equiv_induct -seq 10; "     # Deeper induction for complex FSMs
            "equiv_status -assert"
        )
    else:
        # Combinational: equiv_simple is usually enough
        yosys_script = (
            preamble +
            "equiv_simple; "
            "equiv_induct; "            # Fallback for any remaining cells
            "equiv_status -assert"
        )

    # Run via Docker
    docker_cmd = [
        "docker", "run", "--rm",
        "-v", f"{work_dir}:/workspace",
        DOCKER_IMAGE,
        "yosys", "-p", yosys_script,
    ]

    try:
        proc = subprocess.run(
            docker_cmd,
            capture_output=True, text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        result["equiv_status"] = "timeout"
        result["detail"] = f"Yosys timeout ({timeout}s)"
        return result
    except Exception as e:
        result["detail"] = str(e)
        return result

    stdout = proc.stdout + proc.stderr
    (work_dir / "yosys_output.txt").write_text(stdout)

    if proc.returncode == 0:
        result["equiv_status"] = "pass"
        result["detail"] = "Formally equivalent"
    else:
        # Parse equiv_status output for unproven/disproven cells
        unproven_m = re.search(r"Found\s+(\d+)\s+unproven", stdout)
        disproven_m = re.search(r"Equivalence\s+disproven", stdout)

        if disproven_m or ("equiv_status" in stdout and "NOT" in stdout):
            result["equiv_status"] = "fail"
            result["detail"] = "NOT equivalent (false positive in simulation)"
        elif unproven_m:
            n = int(unproven_m.group(1))
            result["equiv_status"] = "error"
            result["detail"] = f"Inconclusive: {n} unproven equiv cells"
        elif "ERROR" in stdout:
            err_lines = [l for l in stdout.splitlines() if "ERROR" in l]
            result["equiv_status"] = "error"
            result["detail"] = err_lines[0][:200] if err_lines else "Yosys error"
        else:
            result["equiv_status"] = "fail"
            result["detail"] = stdout[-300:]

    return result


def main():
    args = parse_args()

    # Determine mode: agent_run or baseline
    is_agent_run = False
    if args.agent_run:
        source_dir = Path(args.agent_run)
        if not source_dir.is_absolute():
            source_dir = PROJECT_ROOT / source_dir
        is_agent_run = True
    elif args.baseline_dir:
        source_dir = Path(args.baseline_dir)
        if not source_dir.is_absolute():
            source_dir = PROJECT_ROOT / source_dir
    else:
        # Auto-detect: prefer agent_run if no baseline specified
        source_dir = find_latest_baseline()

    if not source_dir or not source_dir.exists():
        console.print("[red]未找到结果目录[/red]")
        console.print("用法:")
        console.print("  --baseline-dir results/baseline_verilog_XXXX  (baseline 模式)")
        console.print("  --agent-run results/agent_run_XXXX            (Sparkle HDL 模式)")
        sys.exit(1)

    mode_label = "Sparkle HDL (agent_run)" if is_agent_run else "Baseline (直接 Verilog)"
    console.print(f"模式: [cyan]{mode_label}[/cyan]")
    console.print(f"结果目录: [cyan]{source_dir}[/cyan]")

    # Get sim-passing problems
    if args.prob:
        passing = [args.prob]
    else:
        passing = get_sim_passing(source_dir)

    if not passing:
        console.print("[red]没有仿真通过的题目[/red]")
        sys.exit(1)

    console.print(f"仿真通过: [cyan]{len(passing)}[/cyan] 题，开始 equiv_check...\n")

    # Output directory
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    prefix = "equiv_sparkle" if is_agent_run else "equiv_check"
    equiv_dir = source_dir / f"{prefix}_{timestamp}"
    equiv_dir.mkdir(parents=True, exist_ok=True)

    # Stats
    stats = {"total": len(passing), "pass": 0, "fail": 0, "error": 0, "timeout": 0}
    all_results = []

    progress = Progress(
        SpinnerColumn(), MofNCompleteColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(), TimeElapsedColumn(),
    )

    with progress:
        task = progress.add_task("Equiv checking...", total=len(passing))

        for prob_id in passing:
            ref_sv = DATASET_DIR / f"{prob_id}_ref.sv"
            work_dir = equiv_dir / prob_id

            # Get DUT SV based on mode
            if is_agent_run:
                baseline_sv = get_dut_sv_for_agent_run(prob_id, source_dir, work_dir)
            else:
                baseline_sv = get_dut_sv_for_baseline(prob_id, source_dir)

            if baseline_sv is None:
                all_results.append({
                    "prob_id": prob_id,
                    "equiv_status": "error",
                    "detail": "DUT SV file not found",
                })
                stats["error"] += 1
                progress.advance(task)
                continue

            result = run_equiv_check(
                prob_id, baseline_sv, ref_sv, work_dir,
                timeout=args.timeout,
            )
            all_results.append(result)

            status = result["equiv_status"]
            stats[status] = stats.get(status, 0) + 1

            icon = {
                "pass": "[green]✓[/green]",
                "fail": "[red]✗ FALSE POSITIVE[/red]",
                "error": "[yellow]![/yellow]",
                "timeout": "[yellow]T[/yellow]",
            }.get(status, "?")

            progress.update(
                task,
                description=(
                    f"EQ: [green]{stats['pass']}[/green] "
                    f"FP: [red]{stats['fail']}[/red] "
                    f"Err: {stats['error']}"
                ),
            )
            progress.advance(task)

            # Print false positives immediately
            if status == "fail":
                progress.console.print(
                    f"  {icon} {prob_id}: {result['detail']}"
                )

    # Save results
    results_file = equiv_dir / "equiv_results.jsonl"
    with open(results_file, "w") as f:
        for r in all_results:
            r["timestamp"] = datetime.now().isoformat()
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    summary = {
        "mode": "sparkle" if is_agent_run else "baseline",
        "source_dir": str(source_dir),
        "total_checked": stats["total"],
        "equiv_pass": stats["pass"],
        "equiv_fail_false_positive": stats["fail"],
        "equiv_error": stats["error"],
        "equiv_timeout": stats["timeout"],
        "false_positive_rate": f"{stats['fail']/stats['total']*100:.1f}%" if stats["total"] else "N/A",
        "timestamp": timestamp,
    }
    (equiv_dir / "summary.json").write_text(json.dumps(summary, indent=2))

    # Print summary
    console.print()
    console.print("=" * 60)
    console.print("  Equiv Check 汇总")
    console.print("=" * 60)
    console.print(f"  仿真通过题数:     {stats['total']}")
    console.print(f"  形式化等价:       [green]{stats['pass']}[/green]")
    console.print(f"  假阳性 (仿真通过但不等价): [red]{stats['fail']}[/red]")
    console.print(f"  错误/超时:        {stats['error'] + stats['timeout']}")
    fp_rate = f"{stats['fail']/stats['total']*100:.1f}%" if stats["total"] else "N/A"
    console.print(f"  假阳性率:         [red]{fp_rate}[/red]")
    console.print(f"  结果目录:         {equiv_dir}")
    console.print("=" * 60)

    # List false positives
    false_positives = [r for r in all_results if r["equiv_status"] == "fail"]
    if false_positives:
        console.print(f"\n[red]假阳性列表 ({len(false_positives)} 个):[/red]")
        for r in false_positives:
            console.print(f"  - {r['prob_id']}: {r['detail'][:80]}")


if __name__ == "__main__":
    main()
