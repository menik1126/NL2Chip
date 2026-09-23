#!/usr/bin/env python3
"""Generate observational Lean contracts from VerilogEval NL (no DUT, no second RTL).

Codex exec, stdin prompt, gpt-5.6-sol ultra via jing SOCKS.
Gates: tmp_contract_validate.py (syntax + non-circuit + nontrivial) then lake env lean.
Does not overwrite PPA/equiv/Direct trees. Does not write Sparkle implementations.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from tmp_contract_validate import (  # noqa: E402
    extract_lean,
    lean_check,
    parse_ref_ports,
    validate_contract,
)

PROJECT = Path("/home/sgli/work/NL2Chip_openlux_repair_state_20260914")
PRIVATE = Path("/home/sgli/work/nl2chip_contract_gen_private_20260923")
OUT = Path("/home/sgli/work/NL2Chip_rebuttal_artifacts/2026-09-23/formal_contract_gen_ve12")
WRAPPER = PRIVATE / "codex_chatgpt_socks.py"
WRAPPER_SRC = Path("/home/sgli/work/nl2chip_direct_compilefb_private_20260922/codex_chatgpt_socks.py")

WORKERS = int(os.environ.get("CONTRACT_WORKERS", "2"))
MAX_ITERS = int(os.environ.get("CONTRACT_ITERS", "6"))
MIN_PROPS = int(os.environ.get("CONTRACT_MIN_PROPS", "3"))

PROBLEMS = [
    "Prob035_count1to10",
    "Prob037_review2015_count1k",
    "Prob063_review2015_shiftcount",
    "Prob067_countslow",
    "Prob068_countbcd",
    "Prob080_timer",
    "Prob115_shift18",
    "Prob124_rule110",
    "Prob141_count_clock",
    "Prob144_conwaylife",
    "Prob146_fsm_serialdata",
    "Prob155_lemmings4",
]

SYSTEM = """You translate a natural-language hardware description into a FORMAL CONTRACT.

A contract is a list of next-cycle CONDITIONS the circuit must satisfy.
It is NOT a second implementation, NOT synthesizable RTL, NOT Sparkle Signal code.

Output ONLY one Lean file. No tools, no shell, no explanation.

REQUIRED SHAPE (every file, exact syntax):
import Init

namespace Contract.<ProbId>

structure Cycle where
  <every non-clk port, Lean type Bool or BitVec n>

def <name> (pre post : Cycle) : Prop :=
  <implication or equality over pre.* and post.*>

end Contract.<ProbId>

RULES:
1. Clock is implicit. Do not put clk in Cycle. Cycle is one post-posedge snapshot.
2. At least 3 named properties. Cover reset/init, the main update, and at least one wrap/hold/illegal-case.
3. Each property must mention pre.<field> and post.<field> and use → or ¬ or equality.
4. Forbidden: Sparkle, Signal, #synthesizeVerilog, theorem, lemma, sorry, Verilog, TopModule.
5. Do not reconstruct the datapath as combinators. Write observational constraints.
6. Import only Init.
"""

FEWSHOT = r"""
Example (NOT the current problem). An oven that counts 0..3 then wraps, reset to 0:

import Init

namespace Contract.ExampleOven

structure Cycle where
  reset : Bool
  q : BitVec 2

def reset_clears (pre post : Cycle) : Prop :=
  pre.reset = true → post.q = 0#2

def hold_is_not_required_when_reset (pre post : Cycle) : Prop :=
  pre.reset = false → pre.q = 3#2 → post.q = 0#2

def increment (pre post : Cycle) : Prop :=
  pre.reset = false → pre.q ≠ 3#2 → post.q = pre.q + 1#2

end Contract.ExampleOven
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(msg: str) -> None:
    print(f"[{utc_now()}] {msg}", flush=True)


def environment() -> dict[str, str]:
    env = os.environ.copy()
    env["NL2CHIP_ISOLATION_ROOT"] = str(PRIVATE / "agent_state")
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


def load_problem(prob_id: str) -> dict:
    ds_dir = PROJECT / "verilog-eval" / "dataset_spec-to-rtl"
    prompt = (ds_dir / f"{prob_id}_prompt.txt").read_text()
    ref = (ds_dir / f"{prob_id}_ref.sv").read_text()
    ports = parse_ref_ports(ref)
    fields = []
    iface_lines = []
    for direction, ty, name in ports:
        iface_lines.append(f"  {direction} {name} : {ty}")
        if name.lower() not in {"clk", "clock"}:
            fields.append(f"  {name} : {ty}")
    return {
        "prob_id": prob_id,
        "nl": prompt.strip(),
        "ports": ports,
        "iface": "\n".join(iface_lines),
        "cycle_fields": "\n".join(fields),
    }


def build_prompt(spec: dict, *, feedback: str | None = None) -> str:
    prompt = (
        SYSTEM
        + "\n"
        + FEWSHOT
        + f"\nCurrent problem id: {spec['prob_id']}\n"
        + "Use this exact namespace: "
        + f"Contract.{spec['prob_id']}\n\n"
        + "Natural-language description:\n"
        + spec["nl"]
        + "\n\nPort list (clk omitted from Cycle):\n"
        + spec["iface"]
        + "\n\nCycle fields you MUST declare, names unchanged:\n"
        + spec["cycle_fields"]
        + "\n\nWrite the contract Lean file now."
    )
    if feedback:
        prompt += (
            "\n\nThe previous contract was REJECTED. Fix it. Output ONLY the Lean file.\n"
            f"### Gate / compile diagnostics\n```\n{feedback[:4000]}\n```\n"
        )
    return prompt


def generate_one(prob_id: str, task_dir: Path, prompt: str, it: int) -> dict:
    last = task_dir / "last_message.txt"
    env = environment()
    env["NL2CHIP_TASK_ID"] = re.sub(r"[^A-Za-z0-9_-]", "_", f"contract_{prob_id}")[:80]
    prompt_path = task_dir / f"generate_{it:02d}.prompt.txt"
    prompt_path.write_text(prompt)
    logp = task_dir / f"generate_{it:02d}.log"
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
    lean = extract_lean(text)
    if lean:
        (task_dir / "candidate.lean").write_text(lean)
    return {
        "generate_rc": rc,
        "candidate": bool(lean),
        "last_chars": len(text),
    }


def assess(prob_id: str, spec: dict, task_dir: Path) -> dict:
    cand = task_dir / "candidate.lean"
    if not cand.exists():
        return {
            "gate_ok": False,
            "lean_ok": False,
            "reasons": ["no Lean extracted"],
            "n_props": 0,
            "detail": "no Lean extracted",
        }
    text = cand.read_text()
    gate = validate_contract(text, prob_id=prob_id, ports=spec["ports"], min_props=MIN_PROPS)
    if not gate["ok"]:
        return {
            "gate_ok": False,
            "lean_ok": False,
            "reasons": gate["reasons"],
            "n_props": gate["n_props"],
            "props": gate["props"],
            "detail": "; ".join(gate["reasons"])[:4000],
        }
    checked = task_dir / "contract.lean"
    checked.write_text(text)
    ok, out = lean_check(checked, PROJECT)
    return {
        "gate_ok": True,
        "lean_ok": ok,
        "reasons": [] if ok else ["lean_check failed"],
        "n_props": gate["n_props"],
        "props": gate["props"],
        "detail": "" if ok else out,
    }


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
    assert str(PRIVATE / "agent_state") in WRAPPER.read_text()


def main() -> int:
    os.environ["PATH"] = (
        "/home/sgli/.elan/bin:/home/sgli/.elan/toolchains/"
        "leanprover--lean4---v4.28.0-rc1/bin:/home/sgli/.local/bin:"
        + os.environ.get("PATH", "")
    )
    os.environ["LAKE_DIR"] = str(PROJECT)
    subprocess.check_call(["bash", "/home/sgli/work/codex_jing_chatgpt_probe/ensure_socks.sh"])
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "contracts").mkdir(exist_ok=True)
    prepare_wrapper()
    jsonl = OUT / "results.jsonl"
    done = set()
    if jsonl.exists():
        for line in jsonl.read_text().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("accepted") and row.get("prob_id"):
                done.add(row["prob_id"])
    todo = [p for p in PROBLEMS if p not in done]
    log(f"contract gen todo {len(todo)}/{len(PROBLEMS)} workers={WORKERS} iters={MAX_ITERS}")
    lock = threading.Lock()

    def one(pid: str) -> dict:
        spec = load_problem(pid)
        task_dir = OUT / "tasks" / pid
        task_dir.mkdir(parents=True, exist_ok=True)
        history = []
        row: dict = {"prob_id": pid, "accepted": False}
        try:
            for it in range(1, MAX_ITERS + 1):
                fb = None if it == 1 else (row.get("detail") or "")
                prompt = build_prompt(spec, feedback=fb)
                gen = generate_one(pid, task_dir, prompt, it)
                row = {"prob_id": pid, **gen}
                row.update(assess(pid, spec, task_dir))
                history.append({
                    "iteration": it,
                    "gate_ok": row.get("gate_ok"),
                    "lean_ok": row.get("lean_ok"),
                    "n_props": row.get("n_props"),
                    "detail": (row.get("detail") or "")[:400],
                })
                if row.get("gate_ok") and row.get("lean_ok"):
                    dest = OUT / "contracts" / f"{pid}.lean"
                    dest.write_text((task_dir / "contract.lean").read_text())
                    row["accepted"] = True
                    row["contract_path"] = str(dest)
                    break
            row["history"] = history
            row["iters"] = len(history)
            row["timestamp"] = utc_now()
            return row
        except Exception as exc:
            return {
                "prob_id": pid, "accepted": False, "worker_error": str(exc),
                "timestamp": utc_now(),
            }

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futs = {pool.submit(one, pid): pid for pid in todo}
        for fut in as_completed(futs):
            row = fut.result()
            with lock:
                with jsonl.open("a") as fh:
                    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            log(
                f"{row.get('prob_id')} accepted={row.get('accepted')} "
                f"gate={row.get('gate_ok')} lean={row.get('lean_ok')} "
                f"n={row.get('n_props')} it={row.get('iters')}"
            )
    log("contract gen finished")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
