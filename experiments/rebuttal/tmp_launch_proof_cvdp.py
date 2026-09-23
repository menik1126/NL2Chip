#!/usr/bin/env python3
"""Fig 5(c)-style one-shot vs stepwise proof ablation on CVDP (hard 12).

Uses the repaired Sparkle tree copied from the VE proof ablation project.
Does not touch ve_hard, unrepaired 416ec86 fourds, or backend PD.
Prompt/sim use CVDP design_name (not hardcoded TopModule). Hidden harness
is not shown to the agent (public-spec-v2).
"""
from __future__ import annotations

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
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

PROJECT = Path("/home/sgli/work/NL2Chip_sparkle_proof_ablation_cvdp_20260920")
PRIVATE = Path("/home/sgli/work/nl2chip_proof_ablation_cvdp12_private_20260920")
PYTHON = Path("/home/sgli/work/NL2Chip/.venv/bin/python")
OUT = Path(
    "/home/sgli/work/NL2Chip_rebuttal_artifacts/2026-09-19/"
    "sparkle_proof_ablation_cvdp12"
)
ARCHON_SRC = Path("/home/sgli/work/archon-official/src")
WRAPPER = PRIVATE / "codex_chatgpt_socks.py"
DUMMY_KEY = Path("/home/sgli/work/nl2chip_chatgpt_socks_private_20260918/dummy.key.env")
STEP_BIN = PRIVATE / "lean_proof_step.py"

WORKERS = int(os.environ.get("PROOF_WORKERS", "4"))
MAX_TURNS = int(os.environ.get("PROOF_TURNS", "80"))
REPL_BASE_PORT = int(os.environ.get("PROOF_REPL_BASE_PORT", "19910"))
PROMPT_VERSION = "proof_ablation_v1_sparkle_codex_cvdp12"

# compile_pass & !sim_pass families from unrepaired fourds; no trivial gates.
PROBLEMS = [
    "cvdp_copilot_64b66b_decoder_0001",
    "cvdp_copilot_64b66b_encoder_0005",
    "cvdp_copilot_fifo_async_0001",
    "cvdp_copilot_apb_gpio_0001",
    "cvdp_copilot_axi_stream_upscale_0001",
    "cvdp_copilot_axis_joiner_0001",
    "cvdp_copilot_gcd_0001",
    "cvdp_copilot_galois_encryption_0001",
    "cvdp_copilot_gf_multiplier_0005",
    "cvdp_copilot_hamming_code_tx_and_rx_0001",
    "cvdp_copilot_bus_arbiter_0004",
    "cvdp_copilot_data_width_converter_0003",
]

sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "agent"))

from agent.dataset import Dataset  # noqa: E402
from agent.evaluator import Evaluator  # noqa: E402
from agent.lean_repl import LeanREPL, LeanREPLPool  # noqa: E402
from cktarchon.run import make_runner  # noqa: E402


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(msg: str) -> None:
    print(f"[{utc_now()}] {msg}", flush=True)


def environment() -> dict[str, str]:
    env = os.environ.copy()
    env["NL2CHIP_ISOLATION_ROOT"] = str(PRIVATE / "agent_state")
    env["PYTHONPATH"] = str(PROJECT) + ":" + str(PROJECT / "agent")
    env["CVDP_HARNESS_PROFILE"] = "race-safe-v1"
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


def impl_name_for(info: Any) -> str:
    name = (getattr(info, "design_name", None) or "").strip()
    if not name or not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name):
        raise ValueError(f"CVDP design_name unusable as Sparkle ident: {name!r}")
    return name


def shared_proof(impl: str) -> str:
    spec = f"{impl}_spec"
    thm = f"{impl}_correct"
    return f"""

## Proof Ablation Mode ({PROMPT_VERSION})

In addition to writing synthesizable Sparkle HDL, the final file must include
at least one Lean theorem proving the implementation equivalent to a separately
written specification.

This is a CVDP problem. The synthesizable implementation function MUST be named
`{impl}` so Verilog extraction matches the CVDP harness DUT. Do not use
`TopModule` unless `{impl}` is literally `TopModule`.

Required final-file structure:
1. Standard Sparkle imports and opens.
2. The synthesizable implementation `def {impl}`.
3. A separate specification named `{spec}`.
4. At least one theorem named `{thm}` or similar.
5. `#synthesizeVerilog {impl}` for the implementation, not for the spec.

Rules for the specification:
- Encode the natural-language/public-spec behavior independently.
- Do not define the spec as an alias of the implementation.
- Spec must not be a rename of `{impl}` or the same combinator tree; prefer a
  declarative spec (truth table, next-state relation, or pure function on values).
- For combinational circuits, prefer a pure BitVec/Bool spec and prove the
  implementation output at time `t` equals the spec applied to inputs at time `t`.
- For sequential circuits, use either a Signal-level spec with matching latency
  or a focused theorem about the next-state/output behavior.

Proof metric:
- A file with `sorry` or `admit` may compile, but it is unverified.
- Try to produce a complete kernel proof with no `sorry`/`admit`.
- Functional correctness is still judged by the CVDP harness simulation; proof
  completeness and simulation pass are recorded separately.
"""


ONE_SHOT = """
### One-Shot Proof Condition

Generate the implementation, spec, and proof in one integrated attempt.
You may use `.venv/bin/python -m cktarchon.tools lean-check Generated/<id>.lean`
to catch syntax/type errors.
Do NOT run `lean_proof_step.py` and do not inspect intermediate proof goals.
The goal of this arm is to measure whether the model can write the full proof directly.
"""


def stepwise_block(impl: str) -> str:
    spec = f"{impl}_spec"
    return f"""
### Stepwise Proof Condition

Use interactive proof-goal feedback via:
  PROOF_REPL_URL is already set. From the repo root:

  .venv/bin/python {STEP_BIN} --reset
  .venv/bin/python {STEP_BIN} --code-file /tmp/step.lean
  .venv/bin/python {STEP_BIN} --env N --code-file /tmp/step.lean

Do NOT include `import`/`open` lines in the code sent to lean_proof_step.
Workflow:
1. Write defs (`{impl}` and `{spec}`) to a temp file and send them
   (no --env) to get env=N.
2. Send the theorem with `by sorry` using `--env N` to inspect the goals.
3. Replace `sorry` with tactics and iterate using the definition env, not a
   failed-proof env.
4. Write the final `Generated/<id>.lean` only after the proof completes or you
   decide it is not currently provable.
5. Still run `cktarchon.tools lean-check` on the final file before stopping.

The goal of this arm is to measure whether explicit proof-goal feedback improves
proof completion and end-to-end correctness.
"""


def public_ref_section(info: Any) -> str:
    ref = (info.ref_code or "").strip()
    if not ref or "no public reference" in ref.lower():
        return (
            "### Public reference Verilog\n\n"
            "No public reference RTL is provided for this item. Implement from "
            "the natural-language / public specification only. Do not search "
            "for hidden testbenches or golden models.\n"
        )
    if len(ref) > 12000:
        ref = ref[:12000] + "\n// ... [truncated public reference]"
    return f"""### Public reference Verilog (interface/behavior hint, not to copy blindly)

```systemverilog
{ref}
```
"""


def build_user_message(info: Any, mode: str) -> str:
    impl = impl_name_for(info)
    spec = f"{impl}_spec"
    mode_label = "one-shot full proof" if mode == "one_shot" else "step-by-step proof"
    return f"""## Problem: {info.prob_id}

Required Verilog top / Sparkle implementation name: `{impl}`

### Natural Language Description

{info.prompt_text}

{public_ref_section(info)}
### Task

Write `Generated/{info.prob_id}.lean` for the {mode_label} proof-ablation arm.

The implementation function must be named `{impl}` (CVDP DUT name).
The final file must include:
- `def {impl} ...` as the synthesizable Sparkle implementation
- `def {spec} ...` as an independent specification
- at least one theorem proving `{impl}` agrees with `{spec}`
- no `sorry`/`admit` if you can complete the proof
- `#synthesizeVerilog {impl}`

The design must still compile, synthesize to SystemVerilog, and pass the
CVDP harness simulation. Do not read hidden harness files.
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
        hide_cvdp_harness_from_agent=True,
        dataset="cvdp",
        codex_agent_user=None,
        interface_prompt_policy="public-spec-v2",
        max_tokens=16384,
        max_turns=max_turns,
        total_turn_budget=None,
        generation_turn_cap=max_turns,
        finite_parameter_specialization=False,
    )


def repl_reset(url: str) -> None:
    req = urllib.request.Request(
        url.rstrip("/") + "/reset", data=b"{}", method="POST"
    )
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=180) as resp:
        resp.read()


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


jsonl_lock = threading.Lock()
print_lock = threading.Lock()


def kernel_has_sorry(repl: LeanREPL | None, lean_path: Path) -> bool | None:
    """Lab-style: REPL complete flag on the problem file only, not lake/lib."""
    if repl is None or not lean_path.exists():
        return None
    result = repl.check_file(lean_path)
    if not result.passed:
        return True
    return not result.complete


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
    impl = impl_name_for(info)
    sample_dir = run_dir / "samples" / mode / prob_id / "sample_000"
    sample_dir.mkdir(parents=True, exist_ok=True)
    extra = shared_proof(impl) + (ONE_SHOT if mode == "one_shot" else stepwise_block(impl))
    prompt = build_user_message(info, mode)
    iso = PRIVATE / "agent_state" / prob_id
    iso.mkdir(parents=True, exist_ok=True)
    (iso / "repl_url").write_text(repl_url)
    dest = iso / "Generated" / f"{prob_id}.lean"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("")
    host = PROJECT / "Generated" / f"{prob_id}.lean"
    if host.exists() or host.is_symlink():
        host.unlink()
    host.symlink_to(dest)
    if mode == "stepwise":
        repl_reset(repl_url)

    row: dict[str, Any] = {
        "experiment": "proof_ablation",
        "prompt_version": PROMPT_VERSION,
        "dataset": "cvdp",
        "prob_id": prob_id,
        "design_name": impl,
        "proof_mode": mode,
        "sample_idx": 0,
        "model": "gpt-5.6-sol",
        "max_turns": MAX_TURNS,
        "compile_pass": False,
        "sv_extracted": False,
        "sim_status": "not_run",
        "proof_present": False,
        "proof_complete": False,
        "has_sorry": True,
        "theorem_count": 0,
        "sorry_count": 0,
        "admit_count": 0,
        "sim_and_proof_pass": False,
        "timestamp": utc_now(),
    }
    started = time.monotonic()
    try:
        args = make_args(MAX_TURNS)
        runner = make_runner(
            args=args,
            prob_id=prob_id,
            role=f"proof-{mode}",
            log_base=sample_dir / "generate",
            skill=skill + extra,
            info=info,
            repl=None,
        )
        stats = runner.run(prompt, max_turns=MAX_TURNS)
        row["agent_turns"] = getattr(stats, "turns", 0)
        row["agent_input_tokens"] = getattr(stats, "input_tokens", 0)
        row["agent_output_tokens"] = getattr(stats, "output_tokens", 0)
    except Exception as exc:
        row["agent_error"] = f"{type(exc).__name__}: {exc}"
        row["detail"] = traceback.format_exc()[-2000:]
        row["elapsed_seconds"] = int(time.monotonic() - started)
        return row

    result = evaluator.evaluate(prob_id, sample_dir / "eval", problem_info=info)
    row.update({k: result.get(k) for k in (
        "compile_pass", "sv_extracted", "lint_pass", "sim_status",
        "sim_mismatches", "detail", "failure_stage",
    ) if k in result})
    lean_text = dest.read_text() if dest.exists() else ""
    theorems, sorry_n, admit_n = proof_counts(lean_text)
    row["theorem_count"] = theorems
    row["sorry_count"] = sorry_n
    row["admit_count"] = admit_n
    row["proof_present"] = theorems > 0
    kernel_sorry = kernel_has_sorry(lean_repl, dest)
    row["eval_has_sorry"] = result.get("has_sorry")
    row["kernel_has_sorry"] = kernel_sorry
    row["has_sorry"] = bool(
        (kernel_sorry if kernel_sorry is not None else True) or sorry_n or admit_n
    )
    row["proof_complete"] = bool(
        row.get("compile_pass") and row["proof_present"] and not row["has_sorry"]
    )
    row["sim_and_proof_pass"] = bool(
        row.get("sim_status") == "sim_pass" and row["proof_complete"]
    )
    row["elapsed_seconds"] = int(time.monotonic() - started)
    if lean_text:
        lean_out = sample_dir / "lean" / f"{prob_id}.lean"
        lean_out.parent.mkdir(parents=True, exist_ok=True)
        lean_out.write_text(lean_text)
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
        log(f"started REPL gateway pid={proc.pid} port={port}")
    deadline = time.monotonic() + 180
    for i in range(WORKERS):
        url = f"http://127.0.0.1:{REPL_BASE_PORT + i}"
        while True:
            if time.monotonic() > deadline:
                raise RuntimeError(f"REPL gateway not ready: {url}")
            try:
                repl_reset(url)
                break
            except Exception:
                time.sleep(1)
    return procs


def dry_run() -> int:
    """Adapter check: load 12 IDs, emit names/prompts, construct runner+evaluator."""
    os.environ.update(environment())
    os.chdir(PROJECT)
    ds = Dataset("cvdp", PROJECT)
    skill = (PROJECT / "agent" / "skill.txt").read_text()[:80]
    print(f"project={PROJECT} skill_head={skill!r}")
    for pid in PROBLEMS:
        info = ds.load_problem(pid)
        impl = impl_name_for(info)
        prompt = build_user_message(info, "one_shot")
        extra = shared_proof(impl)
        assert f"def {impl}" in extra
        assert "def TopModule" not in extra or impl == "TopModule"
        assert impl in prompt
        assert "TopModule" not in prompt or impl == "TopModule"
        args = make_args(8)
        runner = make_runner(
            args=args,
            prob_id=pid,
            role="proof-one_shot",
            log_base=PRIVATE / "dry_run" / pid,
            skill="x" + extra[:40],
            info=info,
            repl=None,
        )
        req = getattr(runner, "required_verilog_modules", None)
        print(
            f"ok {pid} design={impl} prompt_chars={len(prompt)} "
            f"required_modules={req} runner={type(runner).__name__}"
        )
        ev = Evaluator(
            project_root=PROJECT,
            enable_synth=False,
            dataset="cvdp",
            dataset_obj=ds,
        )
        assert ev.dataset_name == "cvdp"
    print(f"dry-run ok n={len(PROBLEMS)} jobs={2 * len(PROBLEMS)}")
    return 0


def main() -> int:
    if os.environ.get("PROOF_DRY_RUN") == "1":
        return dry_run()
    os.environ.update(environment())
    os.chdir(PROJECT)
    subprocess.check_call(["bash", "/home/sgli/work/codex_jing_chatgpt_probe/ensure_socks.sh"])
    (PRIVATE / "agent_state").mkdir(parents=True, exist_ok=True)
    (PROJECT / "Generated").mkdir(exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    existing = sorted(OUT.glob("run_*"))
    if existing and os.environ.get("PROOF_FRESH") != "1":
        run_dir = existing[-1]
        log(f"resume {run_dir}")
    else:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_dir = OUT / f"run_{stamp}"
        run_dir.mkdir(parents=True, exist_ok=True)
        log(f"fresh {run_dir}")
    jsonl = run_dir / "results.jsonl"
    done = load_done(jsonl)
    skill = (PROJECT / "agent" / "skill.txt").read_text()
    ds = Dataset("cvdp", PROJECT)
    jobs = [("one_shot", pid) for pid in PROBLEMS] + [("stepwise", pid) for pid in PROBLEMS]
    jobs = [j for j in jobs if j not in done]
    log(f"jobs {len(jobs)} remaining / {2 * len(PROBLEMS)} workers={WORKERS}")
    log("Starting Lean REPL pool for Lab-aligned kernel scoring...")
    eval_pool = LeanREPLPool(size=WORKERS, project_dir=PROJECT)
    log("Lean REPL pool ready.")
    servers = start_repl_servers()
    urls: queue.Queue[str] = queue.Queue()
    for i in range(WORKERS):
        urls.put(f"http://127.0.0.1:{REPL_BASE_PORT + i}")

    def _one(job: tuple[str, str]) -> dict[str, Any]:
        mode, pid = job
        url = urls.get()
        repl = eval_pool.acquire()
        try:
            with print_lock:
                log(f"{mode} {pid} start")
            evaluator = Evaluator(
                project_root=PROJECT,
                enable_synth=False,
                enable_pnr=False,
                dataset="cvdp",
                dataset_obj=ds,
                lean_repl=repl,
            )
            row = run_sample(
                mode=mode,
                prob_id=pid,
                ds=ds,
                skill=skill,
                evaluator=evaluator,
                run_dir=run_dir,
                repl_url=url,
                lean_repl=repl,
            )
            with jsonl_lock:
                append_jsonl(jsonl, row)
            with print_lock:
                log(
                    f"{mode} {pid} C={int(bool(row.get('compile_pass')))} "
                    f"S={row.get('sim_status')} P={int(bool(row.get('proof_complete')))} "
                    f"turns={row.get('agent_turns')} t={row.get('elapsed_seconds')}s"
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
    log("proof ablation finished listed cvdp 12x2 set")
    return 0


if __name__ == "__main__":
    sys.exit(main())
