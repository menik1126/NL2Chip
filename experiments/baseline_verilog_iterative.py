#!/usr/bin/env python3
"""
Stronger baseline: direct SystemVerilog generation with compile/sim feedback.

This addresses the fairness concern that a single-call Verilog baseline is weaker
than an iterative Lean/Sparkle agent. The loop repeatedly asks the same model to
repair its SystemVerilog using evaluator feedback, while keeping the target HDL
as direct Verilog.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import anthropic

PROJECT_ROOT = Path(__file__).parent.parent.resolve()
RESULTS_DIR = PROJECT_ROOT / "results"

sys.path.insert(0, str(PROJECT_ROOT / "agent"))
from dataset import Dataset, ProblemInfo  # noqa: E402
from evaluator import Evaluator, parse_module_ports  # noqa: E402

_log_lock = threading.Lock()
_stats_lock = threading.Lock()


SYSTEM_PROMPT = """\
You are an expert hardware designer. Generate synthesizable SystemVerilog.

Rules:
1. Output only complete SystemVerilog code, with module declarations and endmodule.
2. Use the target top-module name and ports exactly as specified.
3. Use SystemVerilog-2012 syntax compatible with Icarus Verilog/Verilator.
4. For sequential logic, prefer always_ff/always_comb when appropriate.
5. When given compiler or simulation feedback, repair the full module and output the complete corrected code.
"""


def load_env(path: Path) -> dict[str, str]:
    env = {}
    if not path.exists():
        return env
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip("'\"")
    return env


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Baseline: direct Verilog with iterative compile-fix")
    p.add_argument("--dataset", "-d", choices=["verilogeval", "rtllm", "resbench", "cvdp", "realbench"], default="verilogeval")
    p.add_argument("--limit", "-l", type=int, default=None)
    p.add_argument("--filter", "-f", type=str, default=None)
    p.add_argument("--model", "-m", type=str, default="claude-sonnet-4-5-20250929")
    p.add_argument("--max-tokens", type=int, default=8192)
    p.add_argument("--max-iters", type=int, default=5, help="Max generate/fix iterations per problem")
    p.add_argument("--feedback-mode", choices=["compile-sim", "compile-only"], default="compile-sim",
                   help="compile-sim gives compile and simulation feedback; compile-only only repairs compile/build failures.")
    p.add_argument("--problem-file", type=str, default=None,
                   help="Optional newline-separated problem IDs to run instead of dataset discovery.")
    p.add_argument("--workers", "-w", type=int, default=1)
    p.add_argument("--temperature", "-t", type=float, default=0.0)
    p.add_argument("--api-timeout", type=float, default=300.0, help="Per-request Anthropic client timeout in seconds.")
    p.add_argument("--resume", "-r", action="store_true", help="Skip problems already present in previous iterative-baseline runs")
    p.add_argument("--results-dir", type=str, default=None)
    return p.parse_args()


def extract_module(text: str) -> str | None:
    text = re.sub(r"```(?:systemverilog|verilog|sv)?\s*", "", text)
    text = text.replace("```", "")
    modules = re.findall(r"module\s+\w+[\s\S]*?endmodule", text)
    if modules:
        return "\n\n".join(modules)
    return None


def rename_first_module(code: str, module_name: str) -> str:
    return re.sub(r"\bmodule\s+\w+\b", f"module {module_name}", code, count=1)


def build_prompt(info: ProblemInfo, dataset_name: str) -> str:
    ref_section = ""
    if dataset_name == "verilogeval" and info.ref_code:
        ref_match = re.search(r"module\s+RefModule\s*\(([^)]*)\)", info.ref_code, re.DOTALL)
        ports_str = ref_match.group(1).strip() if ref_match else ""
        ref_section = (
            "\nReference port interface only. Your module MUST be named `TopModule` with the same ports:\n"
            f"```systemverilog\nmodule RefModule (\n{ports_str}\n);\n```\n"
        )
    elif dataset_name == "resbench" and info.ref_code:
        ref_section = (
            "\nRequired module header:\n"
            f"```systemverilog\n{info.ref_code}\n```\n"
        )
    elif dataset_name == "rtllm":
        ref_section = "\nUse the module name and interface implied by the specification/testbench.\n"
    elif info.ref_code and not info.ref_code.startswith("(no public reference") and dataset_name not in {"realbench", "cvdp"}:
        ref_section = (
            "\nReference Verilog is provided only to clarify the required interface and behavior; "
            "write your own implementation.\n"
            f"```systemverilog\n{info.ref_code[:20000]}\n```\n"
        )
    return (
        f"Problem ID: {info.prob_id}\n"
        f"Dataset: {dataset_name}\n"
        f"Target top module: `{info.design_name}`\n\n"
        f"Specification:\n{info.prompt_text[:30000]}\n"
        f"{ref_section}\n"
        "Generate the complete SystemVerilog implementation now."
    )


def eval_verilogeval_direct(info: ProblemInfo, code: str, run_dir: Path, iter_idx: int) -> dict:
    """Evaluate direct SystemVerilog on VerilogEval without the Sparkle wrapper."""
    prob_dir = run_dir / "direct_sv" / info.prob_id
    prob_dir.mkdir(parents=True, exist_ok=True)
    code = rename_first_module(code, "TopModule")
    sv_path = prob_dir / f"iter_{iter_idx}.sv"
    sv_path.write_text(code)

    result = {
        "compile_pass": False,
        "sim_status": "not_run",
        "sim_mismatches": -1,
        "detail": "",
    }
    if not info.ref_path or not info.ref_path.exists() or not info.testbench_path.exists():
        result["sim_status"] = "sim_error"
        result["detail"] = "Missing VerilogEval ref/testbench files"
        return result

    sim_dir = run_dir / "sim" / info.prob_id
    sim_dir.mkdir(parents=True, exist_ok=True)
    vvp_path = sim_dir / f"iter_{iter_idx}.vvp"
    tb_path = sim_dir / "testbench.sv"
    tb_text = info.testbench_path.read_text(errors="replace")
    # Icarus 13 is stricter about the waveform-only tb_mismatch reference
    # appearing before its declaration in VerilogEval testbenches.
    tb_text = re.sub(r"\btb_mismatch\s*,\s*", "", tb_text)
    tb_path.write_text(tb_text)
    try:
        comp = subprocess.run(
            [
                "iverilog", "-g2012", "-o", str(vvp_path),
                str(info.ref_path), str(sv_path), str(tb_path),
            ],
            capture_output=True, text=True, timeout=60,
        )
    except subprocess.TimeoutExpired:
        result["sim_status"] = "compile_fail"
        result["detail"] = "iverilog compile timeout"
        return result
    except FileNotFoundError:
        result["sim_status"] = "sim_error"
        result["detail"] = "iverilog not found"
        return result

    if comp.returncode != 0:
        (sim_dir / f"compile_error_iter_{iter_idx}.txt").write_text(comp.stderr)
        result["sim_status"] = "compile_fail"
        result["detail"] = f"iverilog compile failed:\n{comp.stderr[:1200]}"
        return result

    result["compile_pass"] = True
    try:
        sim = subprocess.run(
            ["vvp", str(vvp_path)],
            capture_output=True, text=True, timeout=90,
        )
    except subprocess.TimeoutExpired:
        result["sim_status"] = "sim_error"
        result["detail"] = "vvp timeout"
        return result

    output = sim.stdout + sim.stderr
    (sim_dir / f"sim_output_iter_{iter_idx}.txt").write_text(output)
    mm = re.search(r"Mismatches:\s*(\d+)", output)
    if mm:
        mismatches = int(mm.group(1))
        result["sim_mismatches"] = mismatches
        if mismatches == 0:
            result["sim_status"] = "sim_pass"
            result["detail"] = "Mismatches: 0"
        else:
            result["sim_status"] = "sim_fail"
            result["detail"] = f"Mismatches: {mismatches}\n{output[-1000:]}"
        return result

    result["sim_status"] = "sim_error"
    result["detail"] = f"Could not parse sim output:\n{output[-1000:]}"
    return result


def call_model(
    client: anthropic.Anthropic,
    model: str,
    max_tokens: int,
    temperature: float,
    messages: list[dict],
) -> tuple[str, int, int]:
    for retry in range(8):
        try:
            resp = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                system=SYSTEM_PROMPT,
                messages=messages,
            )
            text = "\n".join(block.text for block in resp.content if block.type == "text")
            return text, resp.usage.input_tokens, resp.usage.output_tokens
        except (anthropic.RateLimitError, anthropic.APIStatusError, anthropic.APIConnectionError) as e:
            if isinstance(e, anthropic.APIStatusError) and e.status_code < 500 and e.status_code != 429:
                raise
            if retry == 7:
                raise
            time.sleep(min(20 * (2 ** retry), 180))
    raise RuntimeError("unreachable")


def is_compile_feedback_failure(result: dict) -> bool:
    status = str(result.get("sim_status") or "")
    detail = str(result.get("detail") or "").lower()
    if status in {"gen_error", "compile_fail"}:
        return True
    if not result.get("compile_pass"):
        return True
    compile_markers = (
        "compile failed",
        "iverilog",
        "syntax error",
        "elaboration",
        "unable to bind",
        "unknown module",
        "module not found",
        "no module...endmodule",
        "failed to build",
    )
    return status == "sim_error" and any(marker in detail for marker in compile_markers)


def eval_direct_verilog(
    dataset_name: str,
    ds: Dataset,
    info: ProblemInfo,
    code: str,
    run_dir: Path,
    iter_idx: int,
) -> dict:
    prob_dir = run_dir / "direct_sv" / info.prob_id
    prob_dir.mkdir(parents=True, exist_ok=True)
    sv_path = prob_dir / f"iter_{iter_idx}.sv"
    sv_path.write_text(code)

    result = {
        "compile_pass": False,
        "sim_status": "not_run",
        "sim_mismatches": -1,
        "detail": "",
    }

    if dataset_name == "realbench":
        try:
            comp = subprocess.run(
                ["iverilog", "-g2012", "-t", "null", str(sv_path)],
                capture_output=True, text=True, timeout=60,
            )
            result["compile_pass"] = comp.returncode == 0
            result["sim_status"] = "not_run"
            result["detail"] = "iverilog lint passed" if comp.returncode == 0 else comp.stderr[:1200]
            return result
        except Exception as e:
            result["detail"] = str(e)
            result["sim_status"] = "sim_error"
            return result

    if dataset_name == "verilogeval":
        return eval_verilogeval_direct(info, code, run_dir, iter_idx)

    evaluator = Evaluator(project_root=PROJECT_ROOT, dataset=dataset_name, dataset_obj=ds)
    mod_name, ports = parse_module_ports(code, module_name=info.design_name)
    if not mod_name:
        mod_name, ports = parse_module_ports(code)
    if not mod_name:
        result["detail"] = "Could not parse generated module name"
        result["sim_status"] = "gen_error"
        return result

    try:
        if dataset_name == "rtllm":
            sim_status, mismatches, detail = evaluator._run_sim_rtllm(
                info.prob_id, code, mod_name, ports, run_dir
            )
        elif dataset_name == "cvdp":
            sim_status, mismatches, detail = evaluator._run_sim_cvdp(
                info.prob_id, code, mod_name, ports, run_dir
            )
        elif dataset_name == "resbench":
            sim_status, mismatches, detail = evaluator._run_sim_resbench(
                info.prob_id, code, mod_name, run_dir
            )
        else:
            sim_status, mismatches, detail = "not_run", -1, "Unsupported dataset"
    except Exception as e:
        sim_status, mismatches, detail = "sim_error", -1, str(e)

    result["sim_status"] = sim_status
    result["sim_mismatches"] = mismatches
    result["detail"] = detail
    result["compile_pass"] = sim_status not in ("gen_error", "compile_fail", "sim_error") or "failed" not in detail.lower()
    return result


def log_result(results_file: Path, result: dict) -> None:
    result["timestamp"] = datetime.now().isoformat()
    with _log_lock:
        with open(results_file, "a") as f:
            f.write(json.dumps(result, ensure_ascii=False) + "\n")


def run_one(
    prob_id: str,
    ds: Dataset,
    args: argparse.Namespace,
    client_kwargs: dict,
    run_dir: Path,
) -> dict:
    t0 = time.monotonic()
    info = ds.load_problem(prob_id)
    client = anthropic.Anthropic(**client_kwargs)
    messages = [{"role": "user", "content": build_prompt(info, args.dataset)}]

    total_in = 0
    total_out = 0
    best_code = None
    final_eval = None

    for iter_idx in range(1, args.max_iters + 1):
        text, tok_in, tok_out = call_model(
            client, args.model, args.max_tokens, args.temperature, messages
        )
        total_in += tok_in
        total_out += tok_out
        code = extract_module(text)
        messages.append({"role": "assistant", "content": text})

        if not code:
            final_eval = {
                "compile_pass": False,
                "sim_status": "gen_error",
                "sim_mismatches": -1,
                "detail": "No module...endmodule block found in model output",
            }
        else:
            first_mod, _ = parse_module_ports(code)
            if args.dataset == "verilogeval":
                if first_mod:
                    code = rename_first_module(code, "TopModule")
            elif first_mod and first_mod != info.design_name:
                code = rename_first_module(code, info.design_name)
            best_code = code
            final_eval = eval_direct_verilog(args.dataset, ds, info, code, run_dir, iter_idx)

        if final_eval["sim_status"] == "sim_pass" or (
            args.dataset == "realbench" and final_eval["compile_pass"]
        ):
            break
        if args.feedback_mode == "compile-only" and not is_compile_feedback_failure(final_eval):
            break

        feedback = (
            "The previous SystemVerilog candidate failed evaluation.\n"
            f"Status: {final_eval['sim_status']}\n"
            f"Feedback:\n{final_eval['detail'][:4000]}\n\n"
            "Repair the design and output the complete corrected SystemVerilog code only."
        )
        messages.append({"role": "user", "content": feedback})

    if best_code:
        out_dir = run_dir / "sv"
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"{prob_id}.sv").write_text(best_code)

    elapsed = time.monotonic() - t0
    return {
        "prob_id": prob_id,
        "dataset": args.dataset,
        "model": args.model,
        "feedback_mode": args.feedback_mode,
        "iterations": iter_idx,
        "compile_fix_count": max(iter_idx - 1, 0),
        "tokens_in": total_in,
        "tokens_out": total_out,
        "elapsed_seconds": round(elapsed, 3),
        **(final_eval or {}),
    }


def main() -> None:
    args = parse_args()
    started = time.monotonic()

    env = load_env(PROJECT_ROOT / "key.env")
    api_key = env.get("ANTHROPIC_API_KEY", os.environ.get("ANTHROPIC_API_KEY", ""))
    base_url = env.get("ANTHROPIC_BASE_URL", os.environ.get("ANTHROPIC_BASE_URL"))
    client_kwargs = {"api_key": api_key}
    if base_url:
        client_kwargs["base_url"] = base_url
    if args.api_timeout:
        client_kwargs["timeout"] = args.api_timeout

    ds = Dataset(args.dataset, PROJECT_ROOT)
    if args.problem_file:
        problems = [
            line.strip()
            for line in Path(args.problem_file).read_text().splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        if args.filter:
            pat = re.compile(args.filter)
            problems = [pid for pid in problems if pat.search(pid)]
        if args.limit:
            problems = problems[: args.limit]
    else:
        problems = ds.discover_problems(limit=args.limit, filter_re=args.filter)
    results_base = Path(args.results_dir).resolve() if args.results_dir else RESULTS_DIR
    run_dir = results_base / f"baseline_verilog_iterative_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    run_dir.mkdir(parents=True, exist_ok=True)
    results_file = run_dir / "results.jsonl"

    done = set()
    if args.resume:
        for prev in results_base.glob("baseline_verilog_iterative_*/results.jsonl"):
            for line in prev.read_text().splitlines():
                try:
                    done.add(json.loads(line)["prob_id"])
                except Exception:
                    pass

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
        "iterations": 0,
        "elapsed_sum": 0.0,
    }

    def worker(pid: str):
        if args.resume and pid in done:
            with _stats_lock:
                stats["skipped"] += 1
            return
        try:
            result = run_one(pid, ds, args, client_kwargs, run_dir)
        except Exception as e:
            result = {
                "prob_id": pid,
                "dataset": args.dataset,
                "model": args.model,
                "iterations": 0,
                "compile_fix_count": 0,
                "tokens_in": 0,
                "tokens_out": 0,
                "elapsed_seconds": 0,
                "compile_pass": False,
                "sim_status": "agent_error",
                "sim_mismatches": -1,
                "detail": str(e),
            }
        log_result(results_file, result)
        with _stats_lock:
            stats["attempted"] += 1
            stats["tokens_in"] += result.get("tokens_in", 0)
            stats["tokens_out"] += result.get("tokens_out", 0)
            stats["iterations"] += result.get("iterations", 0)
            stats["elapsed_sum"] += result.get("elapsed_seconds", 0.0)
            if result.get("compile_pass"):
                stats["compile_pass"] += 1
            if result.get("sim_status") == "sim_pass":
                stats["sim_pass"] += 1
            elif result.get("sim_status") == "sim_fail":
                stats["sim_fail"] += 1
            elif result.get("sim_status") not in ("not_run",):
                stats["sim_error"] += 1

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = [pool.submit(worker, pid) for pid in problems]
        for fut in as_completed(futures):
            fut.result()
            with _stats_lock:
                print(f"[{stats['attempted'] + stats['skipped']}/{stats['total']}] done", flush=True)

    attempted = stats["attempted"]
    elapsed = time.monotonic() - started
    summary = {
        "experiment": "baseline_verilog_iterative",
        "dataset": args.dataset,
        "filter": args.filter,
        "model": args.model,
        "feedback_mode": args.feedback_mode,
        "temperature": args.temperature,
        "max_iters": args.max_iters,
        "workers": args.workers,
        "total": stats["total"],
        "attempted": attempted,
        "skipped": stats["skipped"],
        "compile_pass": stats["compile_pass"],
        "compile_rate": f"{stats['compile_pass']/attempted*100:.1f}%" if attempted else "N/A",
        "sim_pass": stats["sim_pass"],
        "sim_fail": stats["sim_fail"],
        "sim_error": stats["sim_error"],
        "sim_rate": f"{stats['sim_pass']/attempted*100:.1f}%" if attempted else "N/A",
        "tokens_in": stats["tokens_in"],
        "tokens_out": stats["tokens_out"],
        "avg_total_tokens": round((stats["tokens_in"] + stats["tokens_out"]) / attempted, 2) if attempted else 0,
        "avg_iterations": round(stats["iterations"] / attempted, 2) if attempted else 0,
        "avg_compile_fix_count": round((stats["iterations"] - attempted) / attempted, 2) if attempted else 0,
        "avg_elapsed_seconds": round(stats["elapsed_sum"] / attempted, 2) if attempted else 0,
        "elapsed_seconds": int(elapsed),
    }
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))
    print(f"Results: {run_dir}")


if __name__ == "__main__":
    main()
