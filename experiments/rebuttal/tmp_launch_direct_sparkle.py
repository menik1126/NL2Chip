#!/usr/bin/env python3
"""Direct Sparkle: single Codex exec, no Archon / no CktArchon repair.

Analog of Direct SV (one generation, no tools loop) but the model writes Lean.
gpt-5.6-sol ultra via jing SOCKS. Fresh tree. Does not clobber frozen runs.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

PROJECT = Path("/home/sgli/work/NL2Chip_openlux_repair_state_20260914")
PRIVATE = Path("/home/sgli/work/nl2chip_direct_sparkle_private_20260920")
PYTHON = Path("/home/sgli/work/NL2Chip/.venv/bin/python")
OUT = Path("/home/sgli/work/NL2Chip_rebuttal_artifacts/2026-09-20/sparkle_direct_noarchon_fourds")
WRAPPER = PRIVATE / "codex_chatgpt_socks.py"
CVDP168 = Path("/home/sgli/work/cvdp_mainline_168_problem_ids.txt")
PUBLIC = Path(
    "/home/sgli/work/NL2Chip_rebuttal_artifacts/2026-09-16/"
    "direct_sv_oneshot_gpt56sol_aligned/public"
)
CVDP_DATA = Path(
    "/home/sgli/work/benchmarks/cvdp-benchmark-dataset/"
    "cvdp_v1.1.0_nonagentic_code_generation_no_commercial.jsonl"
)

WORKERS = int(os.environ.get("DIRECT_WORKERS", "2"))
DIRECT_ONLY = os.environ.get("DIRECT_ONLY")
DATASETS = (
    ("verilogeval", 156, None),
    ("rtllm", 50, None),
    ("resbench", 56, None),
    ("cvdp", 168, CVDP168),
)

eval_lock = threading.Lock()


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(msg: str) -> None:
    print(f"[{utc_now()}] {msg}", flush=True)


def environment() -> dict[str, str]:
    env = os.environ.copy()
    env["NL2CHIP_ISOLATION_ROOT"] = str(PRIVATE / "agent_state")
    env["PYTHONPATH"] = str(PROJECT) + ":" + str(PROJECT / "agent")
    env["CVDP_HARNESS_PROFILE"] = "race-safe-v1"
    env["CVDP_DATASET_FILE"] = str(CVDP_DATA)
    env["PATH"] = (
        "/home/sgli/.local/bin:/home/sgli/.elan/toolchains/"
        "leanprover--lean4---v4.28.0-rc1/bin:" + env.get("PATH", "")
    )
    for name in (
        "OPENLUX_API_KEY", "OPENLUX_BASE_URL", "CODEX_GATEWAY_API_KEY",
        "OPENAI_API_KEY", "OPENAI_BASE_URL",
    ):
        env.pop(name, None)
    return env


SYSTEM = """You are an expert hardware designer writing Sparkle HDL (Lean 4).
Output ONLY one Lean file. No tools, no shell, no explanation.

RULES:
1. Start with:
import Sparkle
import Sparkle.Compiler.Elab
open Sparkle.Core.Domain
open Sparkle.Core.Signal
open Sparkle.Core.Circuit
open Sparkle.Library.RTL
2. The synthesizable implementation MUST be named `{name}`.
3. End with `#synthesizeVerilog {name}`.
4. Use Sparkle RTL/Signal idioms, not raw Verilog inside Lean.
"""


def ports_block(ref_code: str, expected: str, dataset: str) -> str:
    ref_code = ref_code or ""
    if dataset == "verilogeval":
        m = re.search(r"module\s+RefModule\s*\(([^)]*)\)", ref_code, re.DOTALL)
    else:
        m = re.search(r"module\s+\w+\s*(?:#\s*\([^;]*\))?\s*\(([^)]*)\)", ref_code, re.DOTALL)
    ports = m.group(1).strip() if m else ""
    return f"module {expected} (\n{ports}\n);"


def user_prompt(nl: str, iface: str, name: str, dataset: str) -> str:
    extra = ""
    if dataset == "cvdp":
        extra = "\nUse only this public specification. There is no hidden testbench in the prompt.\n"
    return (
        f"Specification:\n{nl.strip()}\n\n"
        f"Reference port interface (implementation function MUST be named `{name}`):\n"
        f"```\n{iface}\n```\n"
        f"{extra}\n"
        f"Generate the complete Sparkle Lean file for `{name}`:"
    )


def extract_lean(text: str, name: str) -> str | None:
    if not text:
        return None
    blocks = re.findall(r"```(?:lean)?\s*([\s\S]*?)```", text, flags=re.I)
    for block in blocks:
        if "import Sparkle" in block or f"def {name}" in block:
            return block.strip() + "\n"
    if "import Sparkle" in text:
        start = text.find("import Sparkle")
        return text[start:].strip() + "\n"
    return None


def load_ds(name: str):
    os.environ["CVDP_DATASET_FILE"] = str(CVDP_DATA)
    os.environ["CVDP_HARNESS_PROFILE"] = "race-safe-v1"
    sys.path[:0] = [str(PROJECT), str(PROJECT / "agent")]
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
        return {
            "prob_id": prob_id,
            "func_name": expected,
            "nl": nl,
            "iface": f"// public CVDP top `{expected}`\n{packet.get('packet') or ''}",
        }
    ds = load_ds(dataset)
    info = ds.load_problem(prob_id)
    expected = "TopModule" if dataset == "verilogeval" else info.design_name
    return {
        "prob_id": prob_id,
        "func_name": expected,
        "nl": info.prompt_text,
        "iface": ports_block(info.ref_code, expected, dataset),
    }


def apply_eval_env() -> None:
    env = environment()
    os.environ["PATH"] = env["PATH"]
    os.environ["PYTHONPATH"] = env["PYTHONPATH"]
    os.environ.setdefault("CVDP_HARNESS_PROFILE", "race-safe-v1")
    os.environ.setdefault("CVDP_DATASET_FILE", str(CVDP_DATA))


def generate_one(dataset: str, prob_id: str, task_dir: Path) -> dict:
    spec = spec_for(dataset, prob_id)
    name = spec["func_name"]
    prompt = SYSTEM.format(name=name) + "\n\n" + user_prompt(spec["nl"], spec["iface"], name, dataset)
    last = task_dir / "last_message.txt"
    env = environment()
    env["NL2CHIP_TASK_ID"] = re.sub(r"[^A-Za-z0-9_-]", "_", f"{dataset}_{prob_id}")
    logp = task_dir / "generate.log"
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
        prompt,
    ]
    with logp.open("ab") as fh:
        rc = subprocess.call(cmd, env=env, cwd="/tmp", stdout=fh, stderr=subprocess.STDOUT)
    text = last.read_text(errors="replace") if last.exists() else ""
    lean = extract_lean(text, name)
    if lean:
        (task_dir / "candidate.lean").write_text(lean)
    return {
        "prob_id": prob_id,
        "dataset": dataset,
        "func_name": name,
        "generate_rc": rc,
        "candidate": bool(lean),
        "last_chars": len(text),
    }


def evaluate_one(dataset: str, prob_id: str, task_dir: Path, gen: dict) -> dict:
    apply_eval_env()
    sys.path[:0] = [str(PROJECT), str(PROJECT / "agent")]
    from evaluator import Evaluator
    row = dict(gen)
    row.update({
        "compile_pass": False,
        "sv_extracted": False,
        "sim_status": "not_run",
        "sim_mismatches": -1,
        "detail": "",
        "baseline": "direct_sparkle_noarchon",
        "timestamp": utc_now(),
    })
    cand = task_dir / "candidate.lean"
    if not cand.exists():
        row["sim_status"] = "gen_error"
        row["detail"] = "no Lean extracted"
        return row
    dest = PROJECT / "Generated" / f"{prob_id}.lean"
    with eval_lock:
        dest.parent.mkdir(exist_ok=True)
        dest.write_text(cand.read_text())
        ds = load_ds(dataset)
        evaluator = Evaluator(
            project_root=PROJECT, enable_synth=False, enable_pnr=False,
            dataset=dataset, dataset_obj=ds,
        )
        result = evaluator.evaluate(prob_id, task_dir / "eval")
    row.update({k: result.get(k) for k in (
        "compile_pass", "sv_extracted", "lint_pass", "sim_status",
        "sim_mismatches", "detail", "failure_stage",
    ) if k in result})
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
                ):
                    done.add(row["prob_id"])
    todo = [pid for pid in ids if pid not in done]
    log(f"{dataset} todo {len(todo)}/{expected}")
    lock = threading.Lock()

    def one(pid: str) -> dict:
        task_dir = ds_out / "tasks" / pid
        task_dir.mkdir(parents=True, exist_ok=True)
        try:
            cand = task_dir / "candidate.lean"
            last = task_dir / "last_message.txt"
            if cand.exists() or (last.exists() and last.stat().st_size > 0):
                spec = spec_for(dataset, pid)
                if not cand.exists() and last.exists():
                    lean = extract_lean(last.read_text(errors="replace"), spec["func_name"])
                    if lean:
                        cand.write_text(lean)
                gen = {
                    "prob_id": pid,
                    "dataset": dataset,
                    "func_name": spec["func_name"],
                    "generate_rc": 0,
                    "candidate": cand.exists(),
                    "last_chars": last.stat().st_size if last.exists() else 0,
                    "reused_generation": True,
                }
            else:
                gen = generate_one(dataset, pid, task_dir)
            return evaluate_one(dataset, pid, task_dir, gen)
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
            log(f"{dataset} {row.get('prob_id')} C={row.get('compile_pass')} S={row.get('sim_status')}")


def main() -> int:
    apply_eval_env()
    subprocess.check_call(["bash", "/home/sgli/work/codex_jing_chatgpt_probe/ensure_socks.sh"])
    PRIVATE.mkdir(parents=True, exist_ok=True)
    os.chmod(PRIVATE, 0o700)
    (PRIVATE / "agent_state").mkdir(exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    (PROJECT / "Generated").mkdir(exist_ok=True)
    src = Path("/home/sgli/work/nl2chip_direct_sparkle_private_20260920/codex_chatgpt_socks.py")
    if not WRAPPER.exists() and src.exists():
        pass
    for dataset, n, pf in DATASETS:
        if DIRECT_ONLY and dataset != DIRECT_ONLY:
            continue
        subprocess.check_call(["bash", "/home/sgli/work/codex_jing_chatgpt_probe/ensure_socks.sh"])
        run_dataset(dataset, n, pf)
    log("direct sparkle finished")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
