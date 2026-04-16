#!/usr/bin/env python3
"""
实验 7: DRC + LVS 物理验证 — 复用已有 agent_run 结果。

对已有 synth/ 目录中的设计补跑 P&R → DRC → LVS，不重新生成代码。

用法:
    python3 experiments/drc_lvs_reuse.py
    python3 experiments/drc_lvs_reuse.py --run-dir results/agent_run_20260411_023642
    python3 experiments/drc_lvs_reuse.py --prob Prob005_notgate
    python3 experiments/drc_lvs_reuse.py --drc-only
    python3 experiments/drc_lvs_reuse.py --lvs-only
    python3 experiments/drc_lvs_reuse.py --skip-pnr   # 已有 P&R 结果时跳过，只补 DRC/LVS
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "agent"))

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
RESULTS_DIR = PROJECT_ROOT / "results"
DOCKER_IMAGE = "openroad/orfs:latest"
MIN_DIE_SIDE_UM = 50
PNR_TIMEOUT = 900
DRC_TIMEOUT = 300
LVS_TIMEOUT = 300

console = Console(force_terminal=True)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Exp 07: DRC + LVS on existing agent_run results")
    p.add_argument("--run-dir", type=str, default=None, help="agent_run results directory to reuse")
    p.add_argument("--prob", type=str, default=None, help="Only process this problem ID")
    p.add_argument("--drc-only", action="store_true", help="Only run DRC, skip LVS")
    p.add_argument("--lvs-only", action="store_true", help="Only run LVS, skip DRC")
    p.add_argument("--skip-pnr", action="store_true", help="Skip P&R if already done (6_final.gds exists)")
    p.add_argument("--filter", "-f", type=str, default=None, help="Regex filter on problem IDs")
    return p.parse_args()


def find_latest_agent_run() -> Path | None:
    """Find the latest agent_run directory that has synth results."""
    candidates = []
    for d in sorted(RESULTS_DIR.iterdir(), reverse=True):
        if d.is_dir() and d.name.startswith("agent_run_") and (d / "synth").exists():
            candidates.append(d)
    return candidates[0] if candidates else None


def get_eligible_problems(run_dir: Path, prob_filter: str | None = None) -> list[str]:
    """Get problems that have synth directories (i.e., passed synthesis)."""
    synth_dir = run_dir / "synth"
    if not synth_dir.exists():
        return []

    pattern = re.compile(prob_filter) if prob_filter else None
    probs = []
    for d in sorted(synth_dir.iterdir()):
        if not d.is_dir():
            continue
        # Must have a .sv file and config.mk (valid synth output)
        sv_files = list(d.glob("*.sv"))
        if not sv_files or not (d / "config.mk").exists():
            continue
        if pattern and not pattern.search(d.name):
            continue
        probs.append(d.name)
    return probs


def has_pnr_done(synth_dir: Path) -> bool:
    """Check if P&R was already completed (6_final.gds exists)."""
    return len(list((synth_dir / "orfs_results").rglob("6_final.gds"))) > 0


def run_docker(command: str, synth_dir: Path, volumes: list[str], timeout: int) -> dict:
    """Run a command in the ORFS Docker container."""
    import subprocess

    vol_args = []
    for v in volumes:
        vol_args += ["-v", v]

    docker_cmd = [
        "docker", "run", "--rm",
        "-v", f"{synth_dir}:/workspace",
        *vol_args,
        "-w", "/OpenROAD-flow-scripts/flow",
        DOCKER_IMAGE,
        "bash", "-c", command,
    ]

    try:
        proc = subprocess.run(docker_cmd, capture_output=True, text=True, timeout=timeout)
        return {
            "success": proc.returncode == 0,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "stdout": "", "stderr": f"Timeout ({timeout}s)"}
    except Exception as e:
        return {"success": False, "stdout": "", "stderr": str(e)}


def make_volumes(synth_dir: Path) -> list[str]:
    """Build Docker volume mounts for ORFS results/logs/reports."""
    return [
        f"{synth_dir / 'orfs_results'}:/OpenROAD-flow-scripts/flow/results",
        f"{synth_dir / 'orfs_logs'}:/OpenROAD-flow-scripts/flow/logs",
        f"{synth_dir / 'orfs_reports'}:/OpenROAD-flow-scripts/flow/reports",
    ]


def run_pnr(prob_id: str, synth_dir: Path) -> dict:
    """Run P&R on an existing synth directory."""
    result = {"pnr_pass": False, "gds_generated": False}

    # Read SV to determine die area
    sv_files = list(synth_dir.glob("*.sv"))
    if not sv_files:
        result["pnr_error"] = "No .sv file in synth dir"
        return result
    sv_code = sv_files[0].read_text()

    # Calculate DIE_AREA from synth cell area
    cell_area = None
    try:
        for sf in (synth_dir / "orfs_reports").rglob("synth_stat.txt"):
            m = re.search(r"Chip area.*?:\s*([0-9.]+)", sf.read_text())
            if m:
                cell_area = float(m.group(1))
                break
    except Exception:
        pass

    if cell_area and cell_area > 0:
        core_side = math.sqrt(cell_area / 0.3)
        die_side = max(core_side + 4, MIN_DIE_SIDE_UM)
    else:
        die_side = MIN_DIE_SIDE_UM

    die_side = math.ceil(die_side)
    margin = 2
    core_side_val = die_side - 2 * margin

    # Detect top module name from config.mk
    config_mk = synth_dir / "config.mk"
    top_module = prob_id  # fallback
    if config_mk.exists():
        m = re.search(r"DESIGN_NAME\s*=\s*(\S+)", config_mk.read_text())
        if m:
            top_module = m.group(1)

    # Update config.mk with DIE_AREA for P&R
    has_clk = bool(re.search(r'\binput\b.*\bclk\b', sv_code))
    container_sv = f"/workspace/{sv_files[0].name}"
    config_content = (
        f"export DESIGN_NAME = {top_module}\n"
        f"export PLATFORM = sky130hd\n"
        f"export VERILOG_FILES = {container_sv}\n"
        f"export SDC_FILE = /workspace/constraints.sdc\n"
        f"export DIE_AREA = 0 0 {die_side} {die_side}\n"
        f"export CORE_AREA = {margin} {margin} {core_side_val} {core_side_val}\n"
        f"export PLACE_DENSITY = 0.15\n"
    )
    config_mk.write_text(config_content)

    # Ensure SDC exists
    sdc_file = synth_dir / "constraints.sdc"
    if not sdc_file.exists():
        if not has_clk:
            sdc_file.write_text("# Combinational design\n")
        else:
            sdc_file.write_text("create_clock -period 10 [get_ports clk]\n")

    volumes = make_volumes(synth_dir)

    pnr_result = run_docker(
        "make DESIGN_CONFIG=/workspace/config.mk finish",
        synth_dir, volumes, PNR_TIMEOUT,
    )
    (synth_dir / "pnr_stdout.txt").write_text(pnr_result.get("stdout", ""))
    (synth_dir / "pnr_stderr.txt").write_text(pnr_result.get("stderr", ""))

    if not pnr_result["success"]:
        result["pnr_error"] = pnr_result["stderr"][-300:]
        return result

    result["pnr_pass"] = True
    gds_files = list((synth_dir / "orfs_results").rglob("6_final.gds"))
    result["gds_generated"] = len(gds_files) > 0
    return result


def run_drc(synth_dir: Path) -> dict:
    """Run DRC on an existing P&R result."""
    result = {"drc_pass": None, "drc_violations": None}
    volumes = make_volumes(synth_dir)

    drc_result = run_docker(
        "make DESIGN_CONFIG=/workspace/config.mk drc",
        synth_dir, volumes, DRC_TIMEOUT,
    )

    if not drc_result["success"]:
        result["drc_error"] = f"DRC failed: {drc_result['stderr'][-200:]}"
        return result

    try:
        for f in (synth_dir / "orfs_reports").rglob("6_drc_count.rpt"):
            count_str = f.read_text().strip()
            violations = int(count_str) if count_str.isdigit() else -1
            result["drc_violations"] = violations
            result["drc_pass"] = violations == 0
            break
    except Exception:
        result["drc_pass"] = False

    return result


def run_lvs(synth_dir: Path) -> dict:
    """Run LVS on an existing P&R result."""
    result = {"lvs_pass": None, "lvs_error": None}
    volumes = make_volumes(synth_dir)

    lvs_cmd = (
        "sed -i -e '/ short$/d' -e 's| / | |g' "
        "/OpenROAD-flow-scripts/flow/platforms/sky130hd/cdl/sky130hd.cdl && "
        "make DESIGN_CONFIG=/workspace/config.mk lvs"
    )

    lvs_result = run_docker(lvs_cmd, synth_dir, volumes, LVS_TIMEOUT)

    if lvs_result["success"]:
        result["lvs_pass"] = True
    else:
        stderr = lvs_result.get("stderr", "")
        if "Can't find a value for a R, C or L device" in stderr:
            result["lvs_error"] = "KLayout CDL parse error (platform issue)"
        else:
            result["lvs_pass"] = False
            result["lvs_error"] = stderr[-200:]

    return result


# === PLACEHOLDER_MAIN ===


def main():
    args = parse_args()

    # Find run directory
    if args.run_dir:
        run_dir = Path(args.run_dir)
        if not run_dir.is_absolute():
            run_dir = PROJECT_ROOT / run_dir
    else:
        run_dir = find_latest_agent_run()

    if not run_dir or not run_dir.exists():
        console.print("[red]未找到 agent_run 结果目录[/red]")
        console.print("用法: ./experiments/07_drc_lvs.sh --run-dir results/agent_run_XXXXXXXX_XXXXXX")
        sys.exit(1)

    # Determine what to run
    do_drc = not args.lvs_only
    do_lvs = not args.drc_only

    console.print("╭──────────────────────────────────────────────╮")
    console.print("│  实验 7: DRC + LVS 物理验证 (复用已有结果)    │")
    console.print("╰──────────────────────────────────────────────╯")
    console.print(f"  结果目录: [cyan]{run_dir}[/cyan]")
    console.print(f"  DRC: {'✓' if do_drc else '跳过'}  LVS: {'✓' if do_lvs else '跳过'}  Skip P&R: {args.skip_pnr}")
    console.print()

    # Get eligible problems
    if args.prob:
        problems = [args.prob]
    else:
        problems = get_eligible_problems(run_dir, args.filter)

    if not problems:
        console.print("[red]没有可用的 synth 结果[/red]")
        sys.exit(1)

    console.print(f"  找到 [cyan]{len(problems)}[/cyan] 个已综合的设计\n")

    # Output
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = RESULTS_DIR / f"exp07_drc_lvs_{timestamp}"
    out_dir.mkdir(parents=True, exist_ok=True)

    stats = {
        "total": len(problems),
        "pnr_pass": 0, "pnr_skip": 0, "pnr_fail": 0,
        "drc_pass": 0, "drc_fail": 0, "drc_skip": 0,
        "lvs_pass": 0, "lvs_fail": 0, "lvs_skip": 0,
    }
    all_results = []

    progress = Progress(
        SpinnerColumn(), MofNCompleteColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(), TimeElapsedColumn(),
    )

    with progress:
        task = progress.add_task("DRC/LVS...", total=len(problems))

        for prob_id in problems:
            synth_dir = run_dir / "synth" / prob_id
            r = {"prob_id": prob_id}
            t0 = time.time()

            # ── Step 1: P&R (if needed) ──
            pnr_exists = has_pnr_done(synth_dir)
            if pnr_exists and args.skip_pnr:
                r["pnr_pass"] = True
                r["pnr_skipped"] = True
                stats["pnr_skip"] += 1
            elif pnr_exists:
                # Re-run P&R anyway (might have updated config)
                pnr_r = run_pnr(prob_id, synth_dir)
                r.update(pnr_r)
                if pnr_r.get("pnr_pass"):
                    stats["pnr_pass"] += 1
                else:
                    stats["pnr_fail"] += 1
            else:
                pnr_r = run_pnr(prob_id, synth_dir)
                r.update(pnr_r)
                if pnr_r.get("pnr_pass"):
                    stats["pnr_pass"] += 1
                else:
                    stats["pnr_fail"] += 1

            # Need GDS for DRC/LVS
            gds_ok = has_pnr_done(synth_dir)

            # ── Step 2: DRC ──
            if do_drc and gds_ok:
                drc_r = run_drc(synth_dir)
                r.update(drc_r)
                if drc_r.get("drc_pass"):
                    stats["drc_pass"] += 1
                else:
                    stats["drc_fail"] += 1
            else:
                stats["drc_skip"] += 1

            # ── Step 3: LVS ──
            if do_lvs and gds_ok:
                lvs_r = run_lvs(synth_dir)
                r.update(lvs_r)
                if lvs_r.get("lvs_pass"):
                    stats["lvs_pass"] += 1
                else:
                    stats["lvs_fail"] += 1
            else:
                stats["lvs_skip"] += 1

            r["elapsed_s"] = round(time.time() - t0, 1)
            all_results.append(r)

            # Update progress
            desc = (
                f"PNR:[green]{stats['pnr_pass']}[/green] "
                f"DRC:[green]{stats['drc_pass']}[/green]/[red]{stats['drc_fail']}[/red] "
                f"LVS:[green]{stats['lvs_pass']}[/green]/[red]{stats['lvs_fail']}[/red]"
            )
            progress.update(task, description=desc)
            progress.advance(task)

    # ── Save results ──
    results_file = out_dir / "results.jsonl"
    with open(results_file, "w") as f:
        for r in all_results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    # === PLACEHOLDER_SUMMARY ===

    summary = {
        "experiment": "07_drc_lvs",
        "source_run": str(run_dir),
        "timestamp": timestamp,
        "total": stats["total"],
        "pnr_pass": stats["pnr_pass"],
        "pnr_skip": stats["pnr_skip"],
        "pnr_fail": stats["pnr_fail"],
        "drc_pass": stats["drc_pass"],
        "drc_fail": stats["drc_fail"],
        "drc_skip": stats["drc_skip"],
        "lvs_pass": stats["lvs_pass"],
        "lvs_fail": stats["lvs_fail"],
        "lvs_skip": stats["lvs_skip"],
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False))

    # ── Print summary ──
    console.print()
    console.print("━" * 60)
    console.print("  实验 7: DRC + LVS 物理验证 — 结果汇总")
    console.print("━" * 60)
    console.print(f"  来源:       {run_dir.name}")
    console.print(f"  设计总数:   {stats['total']}")
    console.print()
    console.print(f"  P&R 通过:   [green]{stats['pnr_pass']}[/green]  跳过: {stats['pnr_skip']}  失败: [red]{stats['pnr_fail']}[/red]")
    if do_drc:
        console.print(f"  DRC 通过:   [green]{stats['drc_pass']}[/green]  失败: [red]{stats['drc_fail']}[/red]")
    if do_lvs:
        console.print(f"  LVS 通过:   [green]{stats['lvs_pass']}[/green]  失败: [red]{stats['lvs_fail']}[/red]")
    console.print()
    console.print(f"  结果目录:   {out_dir}")
    console.print("━" * 60)

    # List DRC failures
    drc_fails = [r for r in all_results if r.get("drc_pass") is False]
    if drc_fails:
        console.print(f"\n[red]DRC 失败 ({len(drc_fails)} 个):[/red]")
        for r in drc_fails[:20]:
            v = r.get("drc_violations", "?")
            console.print(f"  - {r['prob_id']}: {v} violations")

    # List LVS failures
    lvs_fails = [r for r in all_results if r.get("lvs_pass") is False]
    if lvs_fails:
        console.print(f"\n[red]LVS 失败 ({len(lvs_fails)} 个):[/red]")
        for r in lvs_fails[:20]:
            console.print(f"  - {r['prob_id']}: {r.get('lvs_error', '?')[:80]}")


if __name__ == "__main__":
    main()
