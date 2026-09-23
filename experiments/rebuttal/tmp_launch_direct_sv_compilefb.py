#!/usr/bin/env python3
"""Direct Verilog + compile-only feedback (Codex exec, no Archon).

Same outer loop as Direct Sparkle compile-fb: dump one SV file, iverilog/eval
compile diagnostics back for up to DIRECT_FB_ITERS rewrites, stop on
compile_pass. Simulation is scored but not fed back.

Prompt is passed on stdin (`codex exec -`) so CVDP packets do not hit ARG_MAX.
Fresh tree. Does not clobber Direct Sparkle compile-fb or Direct SV oneshot.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

PROJECT = Path("/home/sgli/work/NL2Chip_openlux_repair_state_20260914")
PRIVATE = Path("/home/sgli/work/nl2chip_direct_sv_compilefb_private_20260923")
OUT = Path("/home/sgli/work/NL2Chip_rebuttal_artifacts/2026-09-23/direct_sv_compilefb_fourds")
WRAPPER = PRIVATE / "codex_chatgpt_socks.py"
WRAPPER_SRC = Path("/home/sgli/work/nl2chip_direct_compilefb_private_20260922/codex_chatgpt_socks.py")
CVDP168 = Path("/home/sgli/work/cvdp_mainline_168_problem_ids.txt")
PUBLIC = Path(
    "/home/sgli/work/NL2Chip_rebuttal_artifacts/2026-09-16/"
    "direct_sv_oneshot_gpt56sol_aligned/public"
)
CVDP_DATA = Path(
    "/home/sgli/work/benchmarks/cvdp-benchmark-dataset/"
    "cvdp_v1.1.0_nonagentic_code_generation_no_commercial.jsonl"
)

WORKERS = int(os.environ.get("DIRECT_WORKERS", "4"))
MAX_ITERS = int(os.environ.get("DIRECT_FB_ITERS", "9"))
DIRECT_ONLY = os.environ.get("DIRECT_ONLY")
LOCK_DIR = Path("/tmp/nl2chip_direct_sv_compilefb_locks")
DATASETS = (
    ("verilogeval", 156, None),
    ("rtllm", 50, None),
    ("resbench", 56, None),
    ("cvdp", 168, CVDP168),
)

eval_lock = threading.Lock()


@contextmanager
def generated_lock(prob_id: str):
    LOCK_DIR.mkdir(parents=True, exist_ok=True)
    fh = open(LOCK_DIR / f"{prob_id}.lock", "a")
    fcntl.flock(fh, fcntl.LOCK_EX)
    try:
        yield
    finally:
        fcntl.flock(fh, fcntl.LOCK_UN)
        fh.close()


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(msg: str) -> None:
    print(f"[{utc_now()}] {msg}", flush=True)


def environment() -> dict[str, str]:
    env = os.environ.copy()
    env["NL2CHIP_ISOLATION_ROOT"] = str(PRIVATE / "agent_state")
    env["PYTHONPATH"] = str(PROJECT) + ":" + str(PROJECT / "agent") + ":" + str(PROJECT / "experiments")
    env["CVDP_HARNESS_PROFILE"] = "race-safe-v1"
    env["CVDP_DATASET_FILE"] = str(CVDP_DATA)
    env["PATH"] = "/home/sgli/.local/bin:" + env.get("PATH", "")
    for name in (
        "OPENLUX_API_KEY", "OPENLUX_BASE_URL", "CODEX_GATEWAY_API_KEY",
        "OPENAI_API_KEY", "OPENAI_BASE_URL",
    ):
        env.pop(name, None)
    return env


def apply_eval_env() -> None:
    env = environment()
    os.environ["PATH"] = env["PATH"]
    os.environ["PYTHONPATH"] = env["PYTHONPATH"]
    os.environ["CVDP_HARNESS_PROFILE"] = env["CVDP_HARNESS_PROFILE"]
    os.environ["CVDP_DATASET_FILE"] = env["CVDP_DATASET_FILE"]


SYSTEM = """You are an expert hardware designer. Generate synthesizable SystemVerilog code.
Output ONLY one SystemVerilog file. No tools, no shell, no explanation.

RULES:
1. The module MUST be named `{name}` with EXACTLY the ports specified.
2. Output ONLY the SystemVerilog module code (module ... endmodule).
3. Use SystemVerilog-2012 syntax compatible with Icarus Verilog (-g2012).
4. For sequential logic, use `always_ff @(posedge clk)` when appropriate.
5. For combinational logic, use `assign` or `always_comb`.
"""


def user_prompt(nl: str, iface: str, name: str, dataset: str) -> str:
    extra = ""
    if dataset == "cvdp":
        extra = "\nUse only this public specification. There is no hidden testbench in the prompt.\n"
    return (
        f"Specification:\n{nl.strip()}\n\n"
        f"Reference port interface (your module MUST be named `{name}` with the same ports):\n"
        f"```\n{iface}\n```\n"
        f"{extra}\n"
        f"Generate the complete `{name}` implementation:"
    )


def extract_module(text: str, name: str, keep_all: bool) -> str | None:
    text = re.sub(r"```(?:systemverilog|verilog|sv)?\s*", "", text or "", flags=re.I)
    text = text.replace("```", "")
    modules = re.findall(r"module\s+\w+[\s\S]*?endmodule", text)
    if not modules:
        return None
    if keep_all:
        joined = "\n\n".join(modules)
        if re.search(rf"module\s+{re.escape(name)}\b", joined):
            return joined + "\n"
        return re.sub(r"module\s+\w+", f"module {name}", joined, count=1) + "\n"
    m = re.search(rf"(module\s+{re.escape(name)}\s*[\s\S]*?endmodule)", text)
    if m:
        return m.group(1) + "\n"
    code = modules[0]
    return re.sub(r"module\s+\w+", f"module {name}", code, count=1) + "\n"


def load_ds(name: str):
    os.environ["CVDP_DATASET_FILE"] = str(CVDP_DATA)
    os.environ["CVDP_HARNESS_PROFILE"] = "race-safe-v1"
    sys.path[:0] = [str(PROJECT), str(PROJECT / "agent"), str(PROJECT / "experiments")]
    from dataset import Dataset
    return Dataset(name, project_root=PROJECT)


def cohort(name: str, n: int, problem_file: Path | None) -> list[str]:
    if problem_file is not None:
        ids = [ln.strip() for ln in problem_file.read_text().splitlines() if ln.strip() and not ln.startswith("#")]
        if len(ids) != n:
            raise SystemExit(f"{name} problem file {len(ids)} != {n}")
        return ids
    ds = load_ds(name)
    ids = ds.discover_problems()
    if len(ids) != n:
        raise SystemExit(f"{name} discovered {len(ids)}, expected {n}")
    return ids


def spec_for(dataset: str, prob_id: str) -> dict:
    if dataset == "cvdp":
        packet = json.loads((PUBLIC / f"{prob_id}.json").read_text())
        expected = packet["top"]
        nl = ((packet.get("rules") or "") + "\n\n" + (packet.get("packet") or "")).strip()
        iface = packet.get("packet") or f"module {expected} ();"
        return {
            "prob_id": prob_id,
            "func_name": expected,
            "nl": nl,
            "iface": iface,
            "keep_all": True,
        }
    ds = load_ds(dataset)
    info = ds.load_problem(prob_id)
    expected = "TopModule" if dataset == "verilogeval" else info.design_name
    ref_code = info.ref_code or ""
    if dataset == "verilogeval":
        m = re.search(r"module\s+RefModule\s*\(([^)]*)\)", ref_code, re.DOTALL)
        ports = m.group(1).strip() if m else ""
        iface = f"module RefModule (\n{ports}\n);"
    else:
        m = re.search(r"module\s+\w+\s*(?:#\s*\([^;]*\))?\s*\(([^)]*)\)", ref_code, re.DOTALL)
        ports = m.group(1).strip() if m else ""
        iface = f"module {expected} (\n{ports}\n);"
    return {
        "prob_id": prob_id,
        "func_name": expected,
        "nl": info.prompt_text,
        "iface": iface,
        "keep_all": False,
    }


def generate_one(dataset: str, prob_id: str, task_dir: Path, prompt: str, log_name: str) -> dict:
    spec = spec_for(dataset, prob_id)
    name = spec["func_name"]
    last = task_dir / "last_message.txt"
    env = environment()
    env["NL2CHIP_TASK_ID"] = re.sub(r"[^A-Za-z0-9_-]", "_", f"sv_{dataset}_{prob_id}")[:80]
    logp = task_dir / log_name
    prompt_path = task_dir / log_name.replace(".log", ".prompt.txt")
    prompt_path.write_text(prompt)
    cmd = [
        str(WRAPPER),
        "exec",
        "--json",
        "--skip-git-repo-check",
        "--ignore-user-config",
        "-m", "gpt-5.6-sol",
        "-c", 'model_reasoning_effort="ultra"',
        "--sandbox", "read-only",
        "-o", str(last),
        "-",
    ]
    with logp.open("ab") as fh, prompt_path.open("rb") as pin:
        rc = subprocess.call(cmd, env=env, cwd="/tmp", stdin=pin, stdout=fh, stderr=subprocess.STDOUT)
    text = last.read_text(errors="replace") if last.exists() else ""
    sv = extract_module(text, name, spec["keep_all"])
    if sv:
        (task_dir / "candidate.sv").write_text(sv)
    return {
        "prob_id": prob_id,
        "dataset": dataset,
        "func_name": name,
        "generate_rc": rc,
        "candidate": bool(sv),
        "last_chars": len(text),
    }


def evaluate_one(dataset: str, prob_id: str, task_dir: Path, gen: dict, iter_idx: int) -> dict:
    apply_eval_env()
    sys.path[:0] = [str(PROJECT), str(PROJECT / "agent"), str(PROJECT / "experiments")]
    from dataset import Dataset
    from experiments.baseline_verilog_iterative import eval_direct_verilog

    row = dict(gen)
    row.update({
        "compile_pass": False,
        "sim_status": "not_run",
        "sim_mismatches": -1,
        "detail": "",
        "baseline": "direct_sv_compilefb",
        "timestamp": utc_now(),
    })
    cand = task_dir / "candidate.sv"
    if not cand.exists():
        row["sim_status"] = "gen_error"
        row["detail"] = "no SystemVerilog extracted"
        return row
    ds = Dataset(dataset, project_root=PROJECT)
    info = ds.load_problem(prob_id)
    if dataset == "cvdp":
        packet = json.loads((PUBLIC / f"{prob_id}.json").read_text())
        info.design_name = packet["top"]
    with generated_lock(prob_id):
        with eval_lock:
            assessed = eval_direct_verilog(
                dataset, ds, info, cand.read_text(),
                task_dir / "eval", iter_idx,
            )
    row.update({k: assessed.get(k) for k in (
        "compile_pass", "sim_status", "sim_mismatches", "detail",
    ) if k in assessed})
    return row


def run_dataset(dataset: str, expected: int, problem_file: Path | None) -> None:
    ids = cohort(dataset, expected, problem_file)
    ds_out = OUT / dataset
    ds_out.mkdir(parents=True, exist_ok=True)
    jsonl = ds_out / "results.jsonl"
    done = set()
    if jsonl.exists():
        for line in jsonl.read_text().splitlines():
            if line.strip():
                row = json.loads(line)
                if (
                    row.get("prob_id")
                    and row.get("candidate")
                    and row.get("sim_status") not in {"gen_error", "agent_error", "not_run"}
                    and row.get("baseline") == "direct_sv_compilefb"
                    and "compile_feedback_iters" in row
                ):
                    done.add(row["prob_id"])
    todo = [pid for pid in ids if pid not in done]
    log(f"{dataset} todo {len(todo)}/{expected}")
    lock = threading.Lock()

    def one(pid: str) -> dict:
        task_dir = ds_out / "tasks" / pid
        task_dir.mkdir(parents=True, exist_ok=True)
        try:
            spec = spec_for(dataset, pid)
            name = spec["func_name"]
            base_prompt = SYSTEM.format(name=name) + "\n\n" + user_prompt(
                spec["nl"], spec["iface"], name, dataset,
            )
            history = []
            gen: dict = {}
            row: dict = {}
            for it in range(1, MAX_ITERS + 1):
                if it == 1:
                    prompt = base_prompt
                else:
                    prev = (task_dir / "candidate.sv").read_text() if (task_dir / "candidate.sv").exists() else ""
                    diag = (row.get("detail") or "compile failed")[:4000]
                    prompt = (
                        base_prompt
                        + "\n\nThe previous SystemVerilog failed to compile. Fix the file. "
                        + "Output ONLY the complete SystemVerilog file.\n"
                        + f"### Compile diagnostics\n```\n{diag}\n```\n"
                        + f"### Previous SystemVerilog\n```systemverilog\n{prev[:12000]}\n```\n"
                    )
                gen = generate_one(dataset, pid, task_dir, prompt, log_name=f"generate_{it:02d}.log")
                row = evaluate_one(dataset, pid, task_dir, gen, it)
                history.append({
                    "iteration": it,
                    "compile_pass": row.get("compile_pass"),
                    "sim_status": row.get("sim_status"),
                    "detail": (row.get("detail") or "")[:500],
                    "candidate": row.get("candidate"),
                })
                if row.get("compile_pass"):
                    break
                if not row.get("candidate"):
                    break
            row["compile_feedback_iters"] = len(history)
            row["compile_feedback_history"] = history
            row["baseline"] = "direct_sv_compilefb"
            return row
        except Exception as exc:
            return {
                "prob_id": pid, "dataset": dataset, "worker_error": str(exc),
                "sim_status": "agent_error", "compile_pass": False,
            }

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futs = {pool.submit(one, pid): pid for pid in todo}
        for fut in as_completed(futs):
            row = fut.result()
            with lock:
                with jsonl.open("a") as fh:
                    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            log(f"{dataset} {row.get('prob_id')} C={row.get('compile_pass')} S={row.get('sim_status')} it={row.get('compile_feedback_iters')}")


def prepare_wrapper() -> None:
    PRIVATE.mkdir(parents=True, exist_ok=True)
    os.chmod(PRIVATE, 0o700)
    (PRIVATE / "agent_state").mkdir(exist_ok=True)
    text = WRAPPER_SRC.read_text()
    text = text.replace(
        'ISOLATION_PARENT = Path("/home/sgli/work/nl2chip_direct_compilefb_private_20260922/agent_state")',
        f'ISOLATION_PARENT = Path("{PRIVATE / "agent_state"}")',
    )
    text = text.replace(
        'PRIVATE = Path("/home/sgli/work/nl2chip_direct_compilefb_private_20260922")',
        f'PRIVATE = Path("{PRIVATE}")',
    )
    WRAPPER.write_text(text)
    os.chmod(WRAPPER, 0o700)
    if "ISOLATION_PARENT" in WRAPPER.read_text():
        assert str(PRIVATE / "agent_state") in WRAPPER.read_text()


def main() -> int:
    apply_eval_env()
    subprocess.check_call(["bash", "/home/sgli/work/codex_jing_chatgpt_probe/ensure_socks.sh"])
    OUT.mkdir(parents=True, exist_ok=True)
    prepare_wrapper()
    (OUT / "protocol.json").write_text(json.dumps({
        "label": "direct_sv_compilefb",
        "model": "gpt-5.6-sol",
        "reasoning": "ultra",
        "generation": "Codex exec, no tools, stdin prompt, compile-only outer loop",
        "max_iters": MAX_ITERS,
        "workers": WORKERS,
        "hidden_sim_feedback": False,
        "eval": "experiments.baseline_verilog_iterative.eval_direct_verilog",
        "does_not_clobber": [
            str(Path("/home/sgli/work/NL2Chip_rebuttal_artifacts/2026-09-22/sparkle_direct_compilefb_fourds")),
            str(Path("/home/sgli/work/NL2Chip_rebuttal_artifacts/2026-09-16/direct_sv_oneshot_gpt56sol_aligned")),
        ],
        "created": utc_now(),
    }, indent=2) + "\n")
    log(f"direct sv compile-fb start workers={WORKERS} iters={MAX_ITERS}")
    for dataset, n, pf in DATASETS:
        if DIRECT_ONLY and dataset != DIRECT_ONLY:
            continue
        subprocess.check_call(["bash", "/home/sgli/work/codex_jing_chatgpt_probe/ensure_socks.sh"])
        run_dataset(dataset, n, pf)
    log("direct sv compile-fb finished")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
