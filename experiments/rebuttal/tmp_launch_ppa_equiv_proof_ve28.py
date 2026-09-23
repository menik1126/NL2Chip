#!/usr/bin/env python3
"""Prove PPA-optimized TopModule = frozen original on the 28 >5% area pairs.

Right-hand side is system-injected (namespace PPAOrig), not a model-written spec.
One-shot vs stepwise. Does not overwrite frozen table/PPA trees.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

PROJECT = Path("/home/sgli/work/NL2Chip_openlux_repair_state_20260914")
PRIVATE = Path("/home/sgli/work/nl2chip_ppa_equiv_proof_private_20260921")
SEED = Path("/home/sgli/work/nl2chip_chatgpt_socks_private_20260918/agent_state")
PPA_ISO = Path("/home/sgli/work/nl2chip_ppa_opt_ve63_private_20260921/agent_state")
PPA_JSONL = Path(
    "/home/sgli/work/NL2Chip_rebuttal_artifacts/2026-09-21/"
    "sparkle_ppa_opt_ve63/run_20260921_040442/results.jsonl"
)
PYTHON = Path("/home/sgli/work/NL2Chip/.venv/bin/python")
OUT = Path("/home/sgli/work/NL2Chip_rebuttal_artifacts/2026-09-21/sparkle_ppa_equiv_proof_ve28")
ARCHON_SRC = Path("/home/sgli/work/archon-official/src")
WRAPPER = PRIVATE / "codex_chatgpt_socks.py"
STEP_BIN = PRIVATE / "lean_proof_step.py"
DUMMY_KEY = Path("/home/sgli/work/nl2chip_chatgpt_socks_private_20260918/dummy.key.env")
SOCKS_SRC = Path("/home/sgli/work/nl2chip_chatgpt_socks_private_20260918/codex_chatgpt_socks.py")
LOCK_DIR = Path("/tmp/nl2chip_generated_locks")

WORKERS = int(os.environ.get("PROOF_WORKERS", "2"))
MAX_TURNS = int(os.environ.get("PROOF_TURNS", "80"))
REPL_BASE_PORT = int(os.environ.get("PROOF_REPL_BASE_PORT", "18110"))

PROBLEMS = [
    "Prob138_2012_q2fsm",
    "Prob119_fsm3",
    "Prob133_2014_q3fsm",
    "Prob121_2014_q3bfsm",
    "Prob120_fsm3s",
    "Prob140_fsm_hdlc",
    "Prob152_lemmings3",
    "Prob111_fsm2s",
    "Prob096_review2015_fsmseq",
    "Prob114_bugs_case",
    "Prob095_review2015_fsmshift",
    "Prob128_fsm_ps2",
    "Prob144_conwaylife",
    "Prob141_count_clock",
    "Prob155_lemmings4",
    "Prob146_fsm_serialdata",
    "Prob082_lfsr32",
    "Prob124_rule110",
    "Prob154_fsm_ps2data",
    "Prob097_mux9to1v",
    "Prob068_countbcd",
    "Prob067_countslow",
    "Prob115_shift18",
    "Prob075_counter_2bc",
    "Prob148_2013_q2afsm",
    "Prob142_lemmings2",
    "Prob080_timer",
    "Prob063_review2015_shiftcount",
]

sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "agent"))

from agent.dataset import Dataset  # noqa: E402
from agent.evaluator import Evaluator  # noqa: E402
from agent.lean_repl import LeanREPL, LeanREPLPool  # noqa: E402
from cktarchon.run import make_runner  # noqa: E402

jsonl_lock = threading.Lock()
print_lock = threading.Lock()


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
    with print_lock:
        print(f"[{utc_now()}] {msg}", flush=True)


def environment() -> dict[str, str]:
    env = os.environ.copy()
    env["NL2CHIP_ISOLATION_ROOT"] = str(PRIVATE / "agent_state")
    env["PYTHONPATH"] = str(PROJECT) + ":" + str(PROJECT / "agent")
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


def strip_synth(text: str) -> str:
    return re.sub(r"(?m)^[ \t]*#synthesizeVerilog[^\n]*\n?", "", text)


def wrap_orig(orig: str) -> str:
    body = strip_synth(orig).rstrip() + "\n"
    return "namespace PPAOrig\n\n" + body + "\nend PPAOrig\n"


def orig_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def extract_orig_ns(text: str) -> str | None:
    m = re.search(r"namespace PPAOrig\n(.*)\nend PPAOrig", text, re.S)
    return m.group(0) if m else None


def starter_lean(orig: str, opt: str) -> str:
    return (
        wrap_orig(orig)
        + "\n"
        + opt.rstrip()
        + "\n\n"
        + "/- Frozen original is `PPAOrig.TopModule`. Optimized is `TopModule`. -/\n"
        + "theorem TopModule_eq_orig : PPAOrig.TopModule = TopModule := by\n"
        + "  sorry\n"
    )


ONE_SHOT = """
### One-Shot Proof Condition
You may use `.venv/bin/python -m cktarchon.tools lean-check Generated/<id>.lean`.
Do NOT run `lean_proof_step.py` and do not inspect intermediate proof goals.
"""

STEPWISE = f"""
### Stepwise Proof Condition
PROOF_REPL_URL is set. From the repo root:
  {PYTHON} {STEP_BIN} --reset
  {PYTHON} {STEP_BIN} --code-file /tmp/step.lean
  {PYTHON} {STEP_BIN} --env N --code-file /tmp/step.lean
Do NOT include import/open in code sent to lean_proof_step.
Workflow: send defs (no --env) → theorem with `by sorry` and --env N → replace sorry.
Then lean-check the final Generated file.
"""


def build_prompt(prob_id: str, info: Any, mode: str, orig: str, opt: str) -> str:
    mode_label = "one-shot" if mode == "one_shot" else "stepwise"
    return f"""## PPA rewrite equivalence ({mode_label}) — {prob_id}

The two Sparkle circuits below already exist. Do NOT regenerate from the English
problem. Do NOT write a new specification. Prove the optimized circuit equals
the frozen original.

### Public problem text (context only)
{getattr(info, "prompt_text", "")}

### Frozen original (already injected as `namespace PPAOrig` — DO NOT EDIT)
```lean
{orig[:6000]}
```

### Optimized implementation (already in `def TopModule`)
```lean
{opt[:6000]}
```

`Generated/{prob_id}.lean` is pre-seeded with `PPAOrig.TopModule`, `TopModule`,
and `theorem TopModule_eq_orig : PPAOrig.TopModule = TopModule := by sorry`.

Rules:
1. Never change anything inside `namespace PPAOrig` ... `end PPAOrig`.
2. Prefer not to change `def TopModule` (PPA already sim-passed). You may only
   add lemmas/helpers *outside* PPAOrig if needed for the proof.
3. Replace `sorry` with a complete kernel proof. No `sorry`/`admit`.
4. If the two `TopModule` types do not unify, write a correctly typed
   observational-equivalence theorem still named `TopModule_eq_orig`.
5. Keep `#synthesizeVerilog TopModule` (optimized only).
6. Functional correctness is still judged by simulation of `TopModule`.

{ONE_SHOT if mode == "one_shot" else STEPWISE}
"""


def proof_counts(lean_text: str) -> tuple[int, int, int]:
    theorem_count = len(re.findall(r"(?m)^\s*(?:theorem|lemma)\s+", lean_text))
    sorry_count = len(re.findall(r"\bsorry\b", lean_text))
    admit_count = len(re.findall(r"\badmit\b", lean_text))
    return theorem_count, sorry_count, admit_count


def make_args(max_turns: int) -> SimpleNamespace:
    return SimpleNamespace(
        harness="codex-agent",
        model="gpt-5.6-sol",
        prompt_profile="compact",
        archon_src=str(ARCHON_SRC),
        codex_bin=str(WRAPPER),
        codex_effort="ultra",
        codex_sandbox="workspace-write",
        codex_idle_timeout=900.0,
        codex_max_attempts=3,
        codex_base_url_env=None,
        codex_key_env=None,
        codex_wire_api="responses",
        no_codex_chat_proxy=True,
        api_timeout=300.0,
        hide_cvdp_harness_from_agent=False,
        dataset="verilogeval",
        codex_agent_user=None,
        interface_prompt_policy="legacy",
        max_tokens=16384,
        max_turns=max_turns,
        total_turn_budget=None,
        generation_turn_cap=max_turns,
    )


def repl_reset(url: str) -> None:
    req = urllib.request.Request(url.rstrip("/") + "/reset", data=b"{}", method="POST")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=180) as resp:
        resp.read()


def kernel_has_sorry(repl: LeanREPL | None, lean_path: Path) -> bool | None:
    if repl is None or not lean_path.exists():
        return None
    result = repl.check_file(lean_path)
    if not result.passed:
        return True
    return not result.complete


def load_done(path: Path) -> set[tuple[str, str]]:
    done: set[tuple[str, str]] = set()
    if not path.exists():
        return done
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("prob_id") and row.get("proof_mode") and not row.get("agent_error"):
            done.add((row["proof_mode"], row["prob_id"]))
    return done


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def prepare_private() -> None:
    PRIVATE.mkdir(parents=True, exist_ok=True)
    os.chmod(PRIVATE, 0o700)
    (PRIVATE / "agent_state").mkdir(exist_ok=True)
    text = SOCKS_SRC.read_text()
    text = text.replace(
        'ROOT = Path("/home/sgli/work/NL2Chip_openlux_repair_state_20260914")',
        f'ROOT = Path("{PROJECT}")',
    )
    text = text.replace(
        'ISOLATION_PARENT = Path("/home/sgli/work/nl2chip_chatgpt_socks_private_20260918/agent_state")',
        f'ISOLATION_PARENT = Path("{PRIVATE / "agent_state"}")',
    )
    WRAPPER.write_text(text)
    os.chmod(WRAPPER, 0o700)
    src_step = Path("/home/sgli/work/nl2chip_proof_ablation_ve30_private_20260919/lean_proof_step.py")
    src_repl = Path("/home/sgli/work/nl2chip_proof_ablation_ve30_private_20260919/proof_repl_server.py")
    if src_step.exists() and not STEP_BIN.exists():
        shutil.copy2(src_step, STEP_BIN)
    if src_repl.exists() and not (PRIVATE / "proof_repl_server.py").exists():
        shutil.copy2(src_repl, PRIVATE / "proof_repl_server.py")


def run_sample(
    *,
    mode: str,
    prob_id: str,
    ds: Dataset,
    skill: str,
    evaluator: Evaluator,
    run_dir: Path,
    repl_url: str,
    lean_repl: LeanREPL | None,
) -> dict[str, Any]:
    info = ds.load_problem(prob_id)
    orig = (SEED / prob_id / "Generated" / f"{prob_id}.lean").read_text()
    opt = (PPA_ISO / prob_id / "Generated" / f"{prob_id}.lean").read_text()
    frozen = wrap_orig(orig)
    frozen_h = orig_hash(frozen)
    sample_dir = run_dir / "samples" / mode / prob_id
    sample_dir.mkdir(parents=True, exist_ok=True)
    extra = "\n## PPA orig≡opt proof\n" + (ONE_SHOT if mode == "one_shot" else STEPWISE)
    iso = PRIVATE / "agent_state" / prob_id
    iso.mkdir(parents=True, exist_ok=True)
    (iso / "repl_url").write_text(repl_url)
    dest = iso / "Generated" / f"{prob_id}.lean"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(starter_lean(orig, opt))
    host = PROJECT / "Generated" / f"{prob_id}.lean"
    if host.exists() or host.is_symlink():
        host.unlink()
    host.symlink_to(dest)
    if mode == "stepwise":
        repl_reset(repl_url)
    row: dict[str, Any] = {
        "experiment": "ppa_rewrite_equiv",
        "dataset": "verilogeval",
        "prob_id": prob_id,
        "proof_mode": mode,
        "model": "gpt-5.6-sol",
        "orig_sha256": frozen_h,
        "compile_pass": False,
        "sim_status": "not_run",
        "proof_present": False,
        "proof_complete": False,
        "orig_preserved": False,
        "has_sorry": True,
        "timestamp": utc_now(),
    }
    started = time.monotonic()
    try:
        args = make_args(MAX_TURNS)
        runner = make_runner(
            args=args,
            prob_id=prob_id,
            role=f"ppa-equiv-{mode}",
            log_base=sample_dir / "generate",
            skill=skill + extra,
            info=info,
            repl=None,
        )
        os.environ["PROOF_REPL_URL"] = repl_url
        stats = runner.run(build_prompt(prob_id, info, mode, orig, opt), max_turns=MAX_TURNS)
        row["agent_turns"] = getattr(stats, "turns", 0)
        row["agent_input_tokens"] = getattr(stats, "input_tokens", 0)
        row["agent_output_tokens"] = getattr(stats, "output_tokens", 0)
    except Exception as exc:
        row["agent_error"] = f"{type(exc).__name__}: {exc}"
        row["detail"] = traceback.format_exc()[-2000:]
        row["elapsed_seconds"] = int(time.monotonic() - started)
        return row

    lean_text = dest.read_text() if dest.exists() else ""
    ns = extract_orig_ns(lean_text)
    if ns != frozen.rstrip() and frozen not in lean_text:
        # restore frozen original if the agent edited it
        if ns is not None:
            lean_text = lean_text.replace(ns, frozen.rstrip(), 1)
            dest.write_text(lean_text)
        else:
            dest.write_text(frozen + "\n" + strip_synth(opt) + "\n" + lean_text)
            lean_text = dest.read_text()
    row["orig_preserved"] = orig_hash(extract_orig_ns(lean_text) or "") == orig_hash(frozen.rstrip()) or frozen.rstrip() in lean_text

    result = evaluator.evaluate(prob_id, sample_dir / "eval")
    row.update({k: result.get(k) for k in (
        "compile_pass", "sv_extracted", "lint_pass", "sim_status",
        "sim_mismatches", "detail", "failure_stage",
    ) if k in result})
    theorems, sorry_n, admit_n = proof_counts(lean_text)
    row["theorem_count"] = theorems
    row["sorry_count"] = sorry_n
    row["admit_count"] = admit_n
    row["proof_present"] = theorems > 0
    kernel_sorry = kernel_has_sorry(lean_repl, dest)
    row["kernel_has_sorry"] = kernel_sorry
    row["has_sorry"] = bool(
        (kernel_sorry if kernel_sorry is not None else True) or sorry_n or admit_n
    )
    row["proof_complete"] = bool(
        row.get("compile_pass") and row["proof_present"] and not row["has_sorry"]
        and row["orig_preserved"]
    )
    row["sim_and_proof_pass"] = bool(
        row.get("sim_status") == "sim_pass" and row["proof_complete"]
    )
    row["elapsed_seconds"] = int(time.monotonic() - started)
    (sample_dir / f"{prob_id}.lean").write_text(lean_text)
    return row


def start_repl_servers() -> list[subprocess.Popen]:
    procs = []
    for i in range(WORKERS):
        port = REPL_BASE_PORT + i
        log_path = PRIVATE / f"repl_{port}.log"
        proc = subprocess.Popen(
            [str(PYTHON), "-u", str(PRIVATE / "proof_repl_server.py"),
             "--port", str(port), "--project", str(PROJECT)],
            stdout=log_path.open("a"),
            stderr=subprocess.STDOUT,
        )
        procs.append(proc)
        log(f"REPL gateway pid={proc.pid} port={port}")
    deadline = time.monotonic() + 180
    for i in range(WORKERS):
        url = f"http://127.0.0.1:{REPL_BASE_PORT + i}"
        while True:
            if time.monotonic() > deadline:
                raise RuntimeError(f"REPL not ready {url}")
            try:
                repl_reset(url)
                break
            except Exception:
                time.sleep(1)
    return procs


def main() -> int:
    os.environ.update(environment())
    os.chdir(PROJECT)
    subprocess.check_call(["bash", "/home/sgli/work/codex_jing_chatgpt_probe/ensure_socks.sh"])
    prepare_private()
    (PROJECT / "Generated").mkdir(exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = OUT / f"run_{stamp}"
    run_dir.mkdir(parents=True, exist_ok=True)
    jsonl = run_dir / "results.jsonl"
    done = load_done(jsonl)
    skill = (PROJECT / "agent" / "skill.txt").read_text()
    ds = Dataset("verilogeval", PROJECT)
    jobs = [("one_shot", pid) for pid in PROBLEMS] + [("stepwise", pid) for pid in PROBLEMS]
    only = os.environ.get("PROOF_ONLY", "").strip()
    if only:
        jobs = []
        for item in only.split(","):
            item = item.strip()
            if not item:
                continue
            mode, pid = item.split(":", 1)
            jobs.append((mode.strip(), pid.strip()))
    else:
        jobs = [j for j in jobs if j not in done]
    log(f"ppa-equiv jobs={len(jobs)} workers={WORKERS} only={bool(only)}")
    eval_pool = LeanREPLPool(size=WORKERS, project_dir=PROJECT)
    servers = start_repl_servers()
    urls: queue.Queue[str] = queue.Queue()
    for i in range(WORKERS):
        urls.put(f"http://127.0.0.1:{REPL_BASE_PORT + i}")

    def _one(job: tuple[str, str]) -> dict[str, Any]:
        mode, pid = job
        url = urls.get()
        repl = eval_pool.acquire()
        try:
            log(f"{mode} {pid} start")
            evaluator = Evaluator(
                project_root=PROJECT,
                enable_synth=False,
                enable_pnr=False,
                dataset="verilogeval",
                dataset_obj=ds,
                lean_repl=repl,
            )
            with generated_lock(pid):
                row = run_sample(
                    mode=mode, prob_id=pid, ds=ds, skill=skill,
                    evaluator=evaluator, run_dir=run_dir,
                    repl_url=url, lean_repl=repl,
                )
            with jsonl_lock:
                append_jsonl(jsonl, row)
            log(
                f"{mode} {pid} C={int(bool(row.get('compile_pass')))} "
                f"S={row.get('sim_status')} P={int(bool(row.get('proof_complete')))} "
                f"orig={int(bool(row.get('orig_preserved')))} "
                f"turns={row.get('agent_turns')}"
            )
            return row
        finally:
            eval_pool.release(repl)
            urls.put(url)

    try:
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            futs = [pool.submit(_one, job) for job in jobs]
            for fut in as_completed(futs):
                fut.result()
    finally:
        eval_pool.close_all()
        for proc in servers:
            proc.terminate()
        for proc in servers:
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
    log("ppa rewrite equiv finished 28x2")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
