#!/usr/bin/env python3
"""
汇总所有实验结果，生成 LaTeX 表格和对比分析。
用于论文 experiment section 的数据。

用法:
    python3 experiments/summarize_results.py
    python3 experiments/summarize_results.py --latex    # 输出 LaTeX 表格
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

RESULTS_DIR = Path(__file__).parent.parent / "results"


def load_summaries() -> list[dict]:
    """加载所有 agent_run 的 summary.json"""
    summaries = []
    for d in sorted(RESULTS_DIR.iterdir()):
        if d.is_dir() and d.name.startswith("agent_run_"):
            summary_file = d / "summary.json"
            if summary_file.exists():
                with open(summary_file) as f:
                    data = json.load(f)
                    data["run_dir"] = d.name
                    summaries.append(data)
    return summaries


def load_baseline_summaries() -> list[dict]:
    """加载 baseline 实验结果"""
    summaries = []
    for d in sorted(RESULTS_DIR.iterdir()):
        if d.is_dir() and d.name.startswith("exp05_baseline"):
            summary_file = d / "summary.json"
            if summary_file.exists():
                with open(summary_file) as f:
                    data = json.load(f)
                    data["run_dir"] = d.name
                    summaries.append(data)
    return summaries


def print_summary_table(summaries: list[dict]):
    """打印汇总表"""
    print("\n" + "=" * 90)
    print("  Sparkle Agent 实验结果汇总")
    print("=" * 90)
    print(f"{'Run':<40s} {'Total':>6s} {'Compile':>10s} {'Sim':>10s} {'Synth':>6s} {'Model':<30s}")
    print("-" * 90)

    for s in summaries:
        print(
            f"{s.get('run_dir', '?'):<40s} "
            f"{s.get('total', '?'):>6} "
            f"{s.get('compile_rate', '?'):>10s} "
            f"{s.get('sim_rate', '?'):>10s} "
            f"{s.get('synth_pass', '-'):>6} "
            f"{s.get('model', '?'):<30s}"
        )
    print("-" * 90)


def print_baseline_comparison(sparkle: list[dict], baseline: list[dict]):
    """打印 Sparkle vs Baseline 对比"""
    if not baseline:
        print("\n⚠ 未找到 baseline 结果 (实验 05)")
        return

    print("\n" + "=" * 70)
    print("  Sparkle HDL vs 直接 Verilog 对比")
    print("=" * 70)

    # 取最新的全量 run
    full_runs = [s for s in sparkle if s.get("total", 0) >= 100]
    if not full_runs:
        full_runs = sparkle[-1:] if sparkle else []

    for bl in baseline:
        bl_total = bl.get("total", 0)
        bl_compile = bl.get("compile_pass", 0)
        bl_sim = bl.get("sim_pass", 0)
        print(f"\n  Baseline (直接 Verilog):")
        print(f"    总题数:   {bl_total}")
        print(f"    编译通过: {bl_compile} ({bl_compile/bl_total*100:.1f}%)" if bl_total else "    编译通过: N/A")
        print(f"    仿真通过: {bl_sim} ({bl_sim/bl_total*100:.1f}%)" if bl_total else "    仿真通过: N/A")

    if full_runs:
        sp = full_runs[-1]
        sp_total = sp.get("total", 0)
        print(f"\n  Sparkle HDL ({sp.get('model', '?')}):")
        print(f"    总题数:   {sp_total}")
        print(f"    编译通过: {sp.get('compile_rate', '?')}")
        print(f"    仿真通过: {sp.get('sim_rate', '?')}")


def generate_latex_table(summaries: list[dict], baseline: list[dict]) -> str:
    """生成 LaTeX 表格"""
    lines = []
    lines.append(r"\begin{table}[t]")
    lines.append(r"\centering")
    lines.append(r"\caption{VerilogEval benchmark results. Sparkle HDL vs.\ direct Verilog generation.}")
    lines.append(r"\label{tab:main_results}")
    lines.append(r"\begin{tabular}{lcccc}")
    lines.append(r"\toprule")
    lines.append(r"Method & Model & Total & Compile (\%) & Sim Pass (\%) \\")
    lines.append(r"\midrule")

    # Baseline rows
    for bl in baseline:
        bl_total = bl.get("total", 0)
        bl_compile = bl.get("compile_pass", 0)
        bl_sim = bl.get("sim_pass", 0)
        compile_pct = f"{bl_compile/bl_total*100:.1f}" if bl_total else "N/A"
        sim_pct = f"{bl_sim/bl_total*100:.1f}" if bl_total else "N/A"
        model = bl.get("model", "?").replace("claude-", "").replace("-20250514", "")
        lines.append(f"Direct Verilog & {model} & {bl_total} & {compile_pct} & {sim_pct} \\\\")

    lines.append(r"\midrule")

    # Sparkle rows (取全量 run)
    for s in summaries:
        if s.get("total", 0) >= 50:  # 只显示有意义的 run
            model = s.get("model", "?").replace("claude-", "").replace("-20250514", "").replace("-20251001", "")
            lines.append(
                f"Sparkle HDL & {model} & {s.get('total', '?')} & "
                f"{s.get('compile_rate', '?')} & {s.get('sim_rate', '?')} \\\\"
            )

    lines.append(r"\bottomrule")
    lines.append(r"\end{tabular}")
    lines.append(r"\end{table}")
    return "\n".join(lines)


def generate_ppa_latex(summaries: list[dict]) -> str:
    """生成 PPA 相关的 LaTeX 表格"""
    ppa_runs = [s for s in summaries if s.get("ppa_optimized", 0) > 0]
    if not ppa_runs:
        return "% 未找到 PPA 优化数据"

    lines = []
    lines.append(r"\begin{table}[t]")
    lines.append(r"\centering")
    lines.append(r"\caption{PPA optimization results. The agent iteratively refines hardware architectures using synthesis feedback.}")
    lines.append(r"\label{tab:ppa_results}")
    lines.append(r"\begin{tabular}{lccc}")
    lines.append(r"\toprule")
    lines.append(r"Run & Designs Optimized & Total Iterations & Model \\")
    lines.append(r"\midrule")

    for s in ppa_runs:
        model = s.get("model", "?").replace("claude-", "").replace("-20250514", "")
        lines.append(
            f"{s.get('run_dir', '?')[:25]} & {s.get('ppa_optimized', 0)} & "
            f"{s.get('ppa_iterations_total', 0)} & {model} \\\\"
        )

    lines.append(r"\bottomrule")
    lines.append(r"\end{tabular}")
    lines.append(r"\end{table}")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--latex", action="store_true", help="输出 LaTeX 表格")
    args = parser.parse_args()

    summaries = load_summaries()
    baseline = load_baseline_summaries()

    if not summaries and not baseline:
        print("未找到任何实验结果。请先运行实验脚本。")
        sys.exit(1)

    print_summary_table(summaries)
    print_baseline_comparison(summaries, baseline)

    if args.latex:
        print("\n\n% ========== LaTeX 表格 ==========\n")
        print(generate_latex_table(summaries, baseline))
        print()
        print(generate_ppa_latex(summaries))


if __name__ == "__main__":
    main()
