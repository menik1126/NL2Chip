#!/usr/bin/env python3
"""
Baseline: 用 LLM 直接生成 Verilog (Pass@1, 单次生成, 不迭代修复)。
完全遵循 VerilogEval 原始论文评测方式。

用法:
    python3 experiments/baseline_verilog.py                              # 全量 156 题
    python3 experiments/baseline_verilog.py -l 10                        # 前 10 题
    python3 experiments/baseline_verilog.py -w 4                         # 4 并发
    python3 experiments/baseline_verilog.py -m claude-sonnet-4-20250514  # 指定模型
    python3 experiments/baseline_verilog.py -r                           # 续跑 (跳过已完成)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import anthropic
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

# ── Paths ──
PROJECT_ROOT = Path(__file__).parent.parent.resolve()
DATASET_DIR = PROJECT_ROOT / "verilog-eval" / "dataset_spec-to-rtl"
RESULTS_DIR = PROJECT_ROOT / "results"

# Add agent/ to path for Evaluator
sys.path.insert(0, str(PROJECT_ROOT / "agent"))

console = Console(force_terminal=True)
stats_lock = threading.Lock()


def load_env(path: Path) -> dict:
    env = {}
    if not path.exists():
        return env
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        env[k.strip()] = v.strip().strip("'\"")
    return env


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Baseline: LLM → Verilog (Pass@1)")
    p.add_argument("--limit", "-l", type=int, default=None)
    p.add_argument("--filter", "-f", type=str, default=None, help="Regex filter on problem IDs")
    p.add_argument("--model", "-m", type=str, default="claude-sonnet-4-5-20250929")
    p.add_argument("--max-tokens", type=int, default=8192)
    p.add_argument("--workers", "-w", type=int, default=1, help="Concurrent workers (default: 1)")
    p.add_argument("--resume", "-r", action="store_true", help="Skip already-completed problems")
    p.add_argument("--temperature", "-t", type=float, default=0.0, help="Sampling temperature (default: 0)")
    p.add_argument("--synth", action="store_true", help="Run synthesis + PPA on passing designs (requires Docker)")
    p.add_argument("--pnr", action="store_true", help="Run full P&R after synthesis (implies --synth)")
    return p.parse_args()


def discover_problems(limit: int | None = None, filter_re: str | None = None) -> list[str]:
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


SYSTEM_PROMPT = """\
You are an expert hardware designer. Generate synthesizable SystemVerilog code.

RULES:
1. The module MUST be named `TopModule` with EXACTLY the ports specified.
2. Output ONLY the SystemVerilog module code (module ... endmodule). No explanation.
3. Use SystemVerilog-2012 syntax compatible with Icarus Verilog (-g2012).
4. For sequential logic, use `always_ff @(posedge clk)`.
5. For combinational logic, use `assign` or `always_comb`.
"""


def build_prompt(prob_id: str) -> str:
    prompt_file = DATASET_DIR / f"{prob_id}_prompt.txt"
    ref_file = DATASET_DIR / f"{prob_id}_ref.sv"

    nl_spec = prompt_file.read_text().strip()
    ref_code = ref_file.read_text().strip()

    # Extract port interface from RefModule
    ref_match = re.search(r"module\s+RefModule\s*\(([^)]*)\)", ref_code, re.DOTALL)
    ports_str = ref_match.group(1).strip() if ref_match else ""

    return (
        f"Specification:\n{nl_spec}\n\n"
        f"Reference port interface (your module MUST be named TopModule with the same ports):\n"
        f"```\nmodule RefModule (\n{ports_str}\n);\n```\n\n"
        f"Generate the complete `TopModule` implementation:"
    )


def extract_module(text: str) -> str | None:
    """Extract module...endmodule from LLM output."""
    text = re.sub(r"```(?:systemverilog|verilog|sv)?\s*", "", text)
    text = text.replace("```", "")
    m = re.search(r"(module\s+TopModule\s*[\s\S]*?endmodule)", text)
    if m:
        return m.group(1)
    m = re.search(r"(module\s+\w+\s*[\s\S]*?endmodule)", text)
    if m:
        code = m.group(1)
        code = re.sub(r"module\s+\w+", "module TopModule", code, count=1)
        return code
    return None


def sim_check(sv_path: Path, prob_id: str, sim_dir: Path) -> tuple[str, str]:
    """Compile + simulate with iverilog. Returns (status, detail)."""
    ref_sv = DATASET_DIR / f"{prob_id}_ref.sv"
    test_sv = DATASET_DIR / f"{prob_id}_test.sv"

    if not ref_sv.exists() or not test_sv.exists():
        return "sim_error", "Missing ref/test files"

    sim_dir.mkdir(parents=True, exist_ok=True)
    vvp_path = sim_dir / "sim.vvp"

    # Compile
    try:
        comp = subprocess.run(
            ["iverilog", "-g2012", "-o", str(vvp_path),
             str(ref_sv), str(sv_path), str(test_sv)],
            capture_output=True, text=True, timeout=30,
        )
        if comp.returncode != 0:
            return "compile_fail", f"iverilog:\n{comp.stderr[:1000]}"
    except subprocess.TimeoutExpired:
        return "compile_fail", "iverilog timeout"
    except Exception as e:
        return "sim_error", str(e)

    # Simulate
    try:
        sim = subprocess.run(
            ["vvp", str(vvp_path)],
            capture_output=True, text=True, timeout=60,
        )
        output = sim.stdout + sim.stderr
        if re.search(r"Mismatches:\s*0\s", output):
            return "sim_pass", "Mismatches: 0"
        mm = re.search(r"Mismatches:\s*(\d+)", output)
        if mm:
            return "sim_fail", f"Mismatches: {mm.group(1)}"
        if "TIMEOUT" in output:
            return "sim_error", "Simulation timeout in testbench"
        return "sim_error", f"No mismatch info:\n{output[:500]}"
    except subprocess.TimeoutExpired:
        return "sim_error", "vvp timeout"
    except Exception as e:
        return "sim_error", str(e)


def run_one_problem(
    prob_id: str,
    client: anthropic.Anthropic,
    model: str,
    max_tokens: int,
    temperature: float,
    output_dir: Path,
) -> dict:
    """Single-shot: generate Verilog once, compile, simulate."""
    result = {
        "prob_id": prob_id,
        "compile_pass": False,
        "sim_status": "not_run",
        "detail": "",
        "tokens_in": 0,
        "tokens_out": 0,
    }

    user_prompt = build_prompt(prob_id)

    # Call LLM (with retry for transient errors)
    for retry in range(5):
        try:
            resp = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
            )
            break
        except (anthropic.RateLimitError, anthropic.APIStatusError, anthropic.APIConnectionError) as e:
            if isinstance(e, anthropic.APIStatusError) and e.status_code < 500 and e.status_code != 429:
                result["detail"] = f"API error: {e}"
                result["sim_status"] = "api_error"
                return result
            if retry == 4:
                result["detail"] = f"API error after retries: {e}"
                result["sim_status"] = "api_error"
                return result
            time.sleep(min(30 * (2 ** retry), 300))

    result["tokens_in"] = resp.usage.input_tokens
    result["tokens_out"] = resp.usage.output_tokens

    text = resp.content[0].text if resp.content else ""
    code = extract_module(text)

    if not code:
        result["detail"] = "No valid module in LLM output"
        result["sim_status"] = "gen_error"
        return result

    # Save generated code
    sv_path = output_dir / f"{prob_id}.sv"
    sv_path.write_text(code)

    # Compile + simulate
    sim_dir = output_dir / f"sim_{prob_id}"
    sim_status, sim_detail = sim_check(sv_path, prob_id, sim_dir)

    result["sim_status"] = sim_status
    result["detail"] = sim_detail
    if sim_status != "compile_fail":
        result["compile_pass"] = True

    return result


def main():
    args = parse_args()
    t0 = time.monotonic()

    # Load API config
    env = load_env(PROJECT_ROOT / "key.env")
    api_key = env.get("ANTHROPIC_API_KEY", os.environ.get("ANTHROPIC_API_KEY", ""))
    base_url = env.get("ANTHROPIC_BASE_URL", os.environ.get("ANTHROPIC_BASE_URL"))
    client_kwargs = {"api_key": api_key}
    if base_url:
        client_kwargs["base_url"] = base_url

    problems = discover_problems(limit=args.limit, filter_re=args.filter)
    if not problems:
        console.print("[red]No problems found.[/red]")
        sys.exit(1)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = RESULTS_DIR / f"baseline_verilog_{timestamp}"
    output_dir.mkdir(parents=True, exist_ok=True)

    console.print("╭──────────────────────────────────────────────────╮")
    console.print(f"│  Baseline: LLM → Verilog (Pass@1, 单次生成)      │")
    console.print(f"│  模型: [cyan]{args.model}[/cyan]")
    console.print(f"│  题数: [cyan]{len(problems)}[/cyan]  并发: [cyan]{args.workers}[/cyan]  温度: [cyan]{args.temperature}[/cyan]")
    console.print("╰──────────────────────────────────────────────────╯\n")

    # Resume: load existing results
    existing_done = set()
    if args.resume:
        for prev_dir in sorted(RESULTS_DIR.iterdir()):
            if prev_dir.is_dir() and prev_dir.name.startswith("baseline_verilog_"):
                rf = prev_dir / "results.jsonl"
                if rf.exists():
                    for line in rf.read_text().splitlines():
                        try:
                            r = json.loads(line)
                            existing_done.add(r["prob_id"])
                        except Exception:
                            pass
        if existing_done:
            console.print(f"[yellow]Resume: 跳过 {len(existing_done)} 个已完成的题[/yellow]\n")

    stats = {
        "total": len(problems),
        "attempted": 0,
        "skipped": 0,
        "compile_pass": 0,
        "sim_pass": 0,
        "sim_fail": 0,
        "sim_error": 0,
        "tokens_in": 0,
        "tokens_out": 0,
    }

    results_file = output_dir / "results.jsonl"
    file_lock = threading.Lock()

    def log_result(result: dict):
        result["timestamp"] = datetime.now().isoformat()
        with file_lock:
            with open(results_file, "a") as f:
                f.write(json.dumps(result, ensure_ascii=False) + "\n")

    # Progress
    overall_progress = Progress(
        SpinnerColumn(), MofNCompleteColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(), TimeElapsedColumn(), TimeRemainingColumn(),
    )
    current_progress = Progress(
        SpinnerColumn(), TimeElapsedColumn(),
        TextColumn("[progress.description]{task.description}"),
    )

    def update_overall(overall_task):
        a = stats["attempted"]
        rate = f"{stats['sim_pass']/a*100:.0f}%" if a > 0 else "-"
        overall_progress.update(
            overall_task,
            description=f"Pass: [green]{stats['sim_pass']}[/green]/{a}  ({rate})",
        )

    def process_problem(prob_id: str, overall_task):
        if args.resume and prob_id in existing_done:
            with stats_lock:
                stats["skipped"] += 1
                overall_progress.advance(overall_task)
                update_overall(overall_task)
            return

        task = current_progress.add_task(f"[cyan]{prob_id}[/cyan]  Generating...")
        client = anthropic.Anthropic(**client_kwargs)

        try:
            result = run_one_problem(
                prob_id, client, args.model, args.max_tokens,
                args.temperature, output_dir,
            )
        except Exception as e:
            result = {
                "prob_id": prob_id, "compile_pass": False,
                "sim_status": "error", "detail": str(e),
                "tokens_in": 0, "tokens_out": 0,
            }

        with stats_lock:
            stats["attempted"] += 1
            stats["tokens_in"] += result.get("tokens_in", 0)
            stats["tokens_out"] += result.get("tokens_out", 0)
            if result["compile_pass"]:
                stats["compile_pass"] += 1
            if result["sim_status"] == "sim_pass":
                stats["sim_pass"] += 1
            elif result["sim_status"] == "sim_fail":
                stats["sim_fail"] += 1
            else:
                stats["sim_error"] += 1
            overall_progress.advance(overall_task)
            update_overall(overall_task)

        icon = {"sim_pass": "[green]✓[/green]", "sim_fail": "[red]✗[/red]"}.get(
            result["sim_status"], "[yellow]![/yellow]"
        )
        c = "[green]C✓[/green]" if result["compile_pass"] else "[red]C✗[/red]"
        current_progress.update(
            task,
            description=f"{icon} [cyan]{prob_id}[/cyan]  {c}  {result['sim_status']}  {result['detail'][:40]}",
        )
        log_result(result)

    # ── Run ──
    with Live(Group(overall_progress, current_progress), console=console, refresh_per_second=4):
        overall_task = overall_progress.add_task("Starting...", total=len(problems))

        if args.workers <= 1:
            for prob_id in problems:
                process_problem(prob_id, overall_task)
        else:
            with ThreadPoolExecutor(max_workers=args.workers) as pool:
                futures = {
                    pool.submit(process_problem, prob_id, overall_task): prob_id
                    for prob_id in problems
                }
                for future in as_completed(futures):
                    try:
                        future.result()
                    except Exception as e:
                        console.print(f"[red]Error: {e}[/red]")

    # ── Summary ──
    elapsed = time.monotonic() - t0
    attempted = stats["attempted"]
    compile_rate = f"{stats['compile_pass']/attempted*100:.1f}%" if attempted else "N/A"
    sim_rate = f"{stats['sim_pass']/attempted*100:.1f}%" if attempted else "N/A"

    # ── Synthesis + PPA (serial, on sim-passing designs) ──
    if args.pnr:
        args.synth = True

    synth_results = {}
    if args.synth:
        from evaluator import Evaluator as _Eval

        # Collect sim-passing problems from results.jsonl
        passing_probs = []
        if results_file.exists():
            for line in results_file.read_text().splitlines():
                try:
                    r = json.loads(line)
                    if r.get("sim_status") == "sim_pass":
                        passing_probs.append(r["prob_id"])
                except Exception:
                    pass

        if passing_probs:
            console.print(f"\n[magenta]Running synthesis on {len(passing_probs)} passing designs...[/magenta]\n")

            # We reuse Evaluator's _run_synthesis and _run_pnr directly
            evaluator = _Eval(
                project_root=PROJECT_ROOT,
                enable_synth=True,
                enable_pnr=args.pnr,
            )

            synth_pass = 0
            pnr_pass = 0
            for i, prob_id in enumerate(passing_probs, 1):
                sv_path = output_dir / f"{prob_id}.sv"
                if not sv_path.exists():
                    continue

                console.print(f"  [{i}/{len(passing_probs)}] {prob_id}  ", end="")
                sv_code = sv_path.read_text()

                sr = evaluator._run_synthesis(prob_id, sv_code, "TopModule", output_dir)
                if sr.get("synth_pass"):
                    synth_pass += 1
                    ppa_parts = []
                    if sr.get("area_um2") is not None:
                        ppa_parts.append(f"A={sr['area_um2']:.0f}")
                    if sr.get("cell_count") is not None:
                        ppa_parts.append(f"C={sr['cell_count']}")
                    console.print(f"[green]S✓[/green] {' '.join(ppa_parts)}", end="")

                    if args.pnr:
                        pr = evaluator._run_pnr(prob_id, sv_code, "TopModule", output_dir)
                        sr.update(pr)
                        if pr.get("pnr_pass"):
                            pnr_pass += 1
                            wns = pr.get("wns_ns")
                            pwr = pr.get("power_uw")
                            console.print(f"  [green]P✓[/green]" +
                                (f" WNS={wns:.2f}" if wns is not None else "") +
                                (f" Pwr={pwr:.2f}μW" if pwr is not None else ""), end="")
                        else:
                            console.print(f"  [red]P✗[/red]", end="")
                else:
                    console.print(f"[red]S✗[/red]", end="")

                console.print()  # newline
                synth_results[prob_id] = sr

            stats["synth_pass"] = synth_pass
            stats["pnr_pass"] = pnr_pass

            # Append synth results to results.jsonl
            for prob_id, sr in synth_results.items():
                log_result({"prob_id": prob_id, "phase": "synth", **sr})

    summary = {
        "experiment": "baseline_verilog_direct",
        "model": args.model,
        "temperature": args.temperature,
        "total": stats["total"],
        "attempted": attempted,
        "skipped": stats["skipped"],
        "compile_pass": stats["compile_pass"],
        "compile_rate": compile_rate,
        "sim_pass": stats["sim_pass"],
        "sim_fail": stats["sim_fail"],
        "sim_error": stats["sim_error"],
        "sim_rate": sim_rate,
        "synth_pass": stats.get("synth_pass", 0),
        "pnr_pass": stats.get("pnr_pass", 0),
        "synth_enabled": args.synth,
        "pnr_enabled": args.pnr,
        "tokens_in": stats["tokens_in"],
        "tokens_out": stats["tokens_out"],
        "elapsed_seconds": int(elapsed),
    }
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2))

    console.print()
    console.print("=" * 60)
    console.print("  Baseline (直接 Verilog, Pass@1) 汇总")
    console.print("=" * 60)
    console.print(f"  模型:       {args.model}")
    console.print(f"  温度:       {args.temperature}")
    console.print(f"  总题数:     {stats['total']}")
    console.print(f"  已跑:       {attempted}")
    console.print(f"  编译通过:   [green]{stats['compile_pass']}[/green] ({compile_rate})")
    console.print(f"  仿真通过:   [green]{stats['sim_pass']}[/green] ({sim_rate})")
    console.print(f"  仿真失败:   [red]{stats['sim_fail']}[/red]")
    console.print(f"  错误:       {stats['sim_error']}")
    if args.synth:
        console.print(f"  综合通过:   [green]{stats.get('synth_pass', 0)}[/green]")
    if args.pnr:
        console.print(f"  P&R 通过:   [green]{stats.get('pnr_pass', 0)}[/green]")
    h, rem = divmod(int(elapsed), 3600)
    m, s = divmod(rem, 60)
    console.print(f"  耗时:       {h}:{m:02d}:{s:02d}")
    console.print(f"  结果目录:   {output_dir}")
    console.print("=" * 60)


if __name__ == "__main__":
    main()
