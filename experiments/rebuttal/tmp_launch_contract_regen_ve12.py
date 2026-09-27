#!/usr/bin/env python3
"""Regenerate VE-12 contracts that fail the 3-layer checkers.

Writes a new tree. Does not overwrite formal_contract_gen_ve12/contracts.
"""
from __future__ import annotations

import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from tmp_contract_checkers import (  # noqa: E402
    GOLD,
    check_contract,
    feedback_from_row,
)
from tmp_contract_validate import extract_lean, lean_check  # noqa: E402
from tmp_launch_contract_gen_ve12 import (  # noqa: E402
    FEWSHOT,
    MAX_ITERS,
    MIN_PROPS,
    PROBLEMS,
    PROJECT,
    WORKERS,
    generate_one,
    load_problem,
    log,
    utc_now,
)
from tmp_contract_validate import validate_contract  # noqa: E402

CHECK = Path("/home/sgli/work/NL2Chip_rebuttal_artifacts/2026-09-24/formal_contract_check_ve12")
OUT = Path("/home/sgli/work/NL2Chip_rebuttal_artifacts/2026-09-24/formal_contract_gen_ve12_checked_fb4")

SYSTEM = """You translate a natural-language hardware description into a FORMAL CONTRACT.

A contract is a list of next-cycle CONDITIONS the circuit must satisfy.
It is NOT a second implementation, NOT synthesizable RTL, NOT Sparkle Signal code.

Output ONLY one Lean file inside a ```lean fence. No tools, no shell, no explanation.

REQUIRED SHAPE — copy the Cycle skeleton from the prompt; do not add fields:
import Init

namespace Contract.<ProbId>

structure Cycle where
  <exactly the listed non-clk ports — no ghost fields>

def <name> (pre post : Cycle) : Prop :=
  <implication or equality over pre.* and post.*>

end Contract.<ProbId>

RULES:
1. Clock is implicit. Do not put clk in Cycle.
2. At least 3 named properties covering reset/init, the main update, and wrap/hold/illegal.
3. Each property must mention pre.<field> and post.<field> and use → or ¬ or equality.
4. Forbidden: Sparkle, Signal, #synthesizeVerilog, theorem, lemma, sorry, Verilog, TopModule, extra ghost Cycle fields.
   Reserved Verilog port names (`in`, `out`, …) must be Lean fields `«in»`, `«out»`; write `pre.«in»`.
5. Do NOT reconstruct packed datapaths with magic constants (+7, +103, +1639). Constrain digits/bits.
6. Do NOT write tautologies (post.q = post.q). If a case is don't-care, omit it.
7. Import only Init. No ∀ over 512/256 bits — constrain a few concrete indices or neighbor triples.
8. Hidden counters must be observed via ports (q, tc, done, walk_left, …), not extra Cycle fields.
9. UART: start=0, 8 data LSB first, stop=1; constrain done/reset; do not require out_byte when it can be X.
10. Lemmings: fall > dig > turn; splat if falling more than 20 cycles then ground — constrain the four outputs.
"""

EXTRA_FEWSHOT = {
    "Prob067_countslow": r"""
Hold example (pause enable):
def hold_when_paused (pre post : Cycle) : Prop :=
  ¬pre.reset ∧ pre.slowena = false → post.q = pre.q
""",
    "Prob080_timer": r"""
Timer example (observe tc, never a hidden count field):
def loaded_zero_is_tc (pre post : Cycle) : Prop :=
  pre.load = true → (post.tc = true ↔ pre.data = 0#10)
def stay_at_zero (pre post : Cycle) : Prop :=
  ¬pre.load ∧ pre.tc = true → post.tc = true
""",
    "Prob115_shift18": r"""
ASR example (sign-fill, not zero-fill):
def asr1 (pre post : Cycle) : Prop :=
  ¬pre.load ∧ pre.ena = true ∧ pre.amount = 2#2 → post.q = pre.q >>> 1
""",
    "Prob124_rule110": r"""
CA example: do NOT write ∀ i, _. Constrain load and a couple of bits, e.g. q.getLsbD 0.
""",
    "Prob144_conwaylife": r"""
Life example: pre.load = true → post.q = pre.data. Do not quantify over all 256 cells.
""",
    "Prob146_fsm_serialdata": r"""
UART example: Cycle field for the serial pin is `«in»` (Lean keyword).
def reset_clears_done (pre post : Cycle) : Prop :=
  pre.reset = true → post.done = false
def idle_line_is_high (pre post : Cycle) : Prop :=
  pre.reset = false → post.done = true → pre.«in» = true
Keep out_byte unconstrained unless it is known 0/1. Do not add d0/d1/rx_stage to Cycle.
""",
    "Prob155_lemmings4": r"""
Lemmings example: only the four outputs + bump/ground/dig/areset.
def splat_outputs (pre post : Cycle) : Prop :=
  post.walk_left = false ∧ post.walk_right = false ∧ post.aaah = false ∧ post.digging = false
is for the splat case — do not add fall_cnt to Cycle.
""",
    "Prob037_review2015_count1k": r"""
Counter wrap must be explicit:
def wrap (pre post : Cycle) : Prop :=
  ¬pre.reset ∧ pre.q = 999#10 → post.q = 0#10
def reset0 (pre post : Cycle) : Prop :=
  pre.reset = true → post.q = 0#10
""",
}


def build_prompt(spec: dict, *, feedback: str | None = None, previous: str | None = None) -> str:
    bullets = GOLD.get(spec["prob_id"], [])
    ns = "Contract." + spec["prob_id"]
    skeleton = (
        "import Init\n\n"
        f"namespace {ns}\n\n"
        "structure Cycle where\n"
        f"{spec['cycle_fields']}\n\n"
        f"-- add ≥3 defs of type (pre post : Cycle) : Prop using only these fields\n\n"
        f"end {ns}\n"
    )
    extra = EXTRA_FEWSHOT.get(spec["prob_id"], "")
    prompt = (
        SYSTEM
        + "\n"
        + FEWSHOT
        + extra
        + f"\nCurrent problem id: {spec['prob_id']}\n"
        + f"Use this exact namespace: {ns}\n\n"
        + "Natural-language description:\n"
        + spec["nl"]
        + "\n\nPort list (clk omitted; do NOT add ghost Cycle fields):\n"
        + spec["iface"]
        + "\n\nCopy this Cycle skeleton (field names/types frozen):\n```lean\n"
        + skeleton
        + "```\n\nGold requirement bullets you MUST cover:\n"
        + json.dumps(bullets, ensure_ascii=False)
        + "\n\nWrite the full Lean file now. Cycle must match the skeleton exactly."
    )
    if previous:
        prompt += (
            "\n\nPrevious contract (KEEP every def that is not named as wrong; "
            "ADD a new def for each UNKILLED mutant; do not rewrite Cycle):\n```lean\n"
            + previous[:8000]
            + "\n```\n"
        )
    if feedback:
        prompt += (
            "\n\nThe previous contract was REJECTED.\n"
            "If UNKILLED: add the suggested Prop, keep the rest.\n"
            "If extract/Lean error: emit a complete ```lean file starting with import Init.\n"
            f"### Checker diagnostics\n```\n{feedback[:6000]}\n```\n"
        )
    return prompt


def failed_ids() -> list[str]:
    jsonl = CHECK / "results.jsonl"
    if not jsonl.exists():
        return list(PROBLEMS)
    done = {}
    for line in jsonl.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        done[row["prob_id"]] = row
    return [
        p for p in PROBLEMS
        if not (done.get(p) or {}).get("accept")
        and not (OUT / "contracts" / f"{p}.lean").exists()
    ]


def _last_check_row(pid: str) -> dict | None:
    jsonl = CHECK / "results.jsonl"
    if not jsonl.exists():
        return None
    hit = None
    for line in jsonl.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("prob_id") == pid:
            hit = row
    return hit


def seed_feedback(pid: str) -> str:
    """Use the previous check report so iteration 1 already sees rich diagnostics."""
    row = _last_check_row(pid)
    if not row:
        return ""
    row = dict(row)
    row["prob_id"] = pid
    return feedback_from_row(row)


def assess_full(pid: str, spec: dict, task_dir: Path) -> dict:
    cand = task_dir / "candidate.lean"
    last = task_dir / "last_message.txt"
    snippet = ""
    if last.exists():
        snippet = last.read_text()[:600]
    if not cand.exists():
        return {
            "gate_ok": False, "lean_ok": False, "accept": False,
            "detail": (
                "EXTRACT: no Lean file found. Reply with a single ```lean fence containing "
                "`import Init`, `namespace Contract.<id>`, `structure Cycle` matching the skeleton, "
                "and ≥3 defs. Do not add ghost fields. Model snippet:\n" + snippet
            ),
        }
    text = cand.read_text()
    gate = validate_contract(text, prob_id=pid, ports=spec["ports"], min_props=MIN_PROPS)
    if not gate["ok"]:
        return {
            "gate_ok": False, "lean_ok": False, "accept": False,
            "syntax": gate,
            "detail": "Syntax/style gate: " + "; ".join(gate["reasons"])[:4000],
        }
    checked = task_dir / "contract.lean"
    checked.write_text(text)
    ok, out = lean_check(checked, PROJECT)
    if not ok:
        return {
            "gate_ok": True, "lean_ok": False, "accept": False,
            "detail": (
                "LEAN ELABORATION FAILED. Keep the Cycle skeleton exactly; delete invented fields "
                "(like extra d2/fall_cnt). Compiler:\n" + out[-3500:]
            ),
        }
    row = check_contract(
        prob_id=pid,
        contract=text,
        ref_sv=(PROJECT / "verilog-eval" / "dataset_spec-to-rtl" / f"{pid}_ref.sv").read_text(),
        nl=spec["nl"],
        work=task_dir / "check",
        wrapper=None,
        run_llm_judge=True,
    )
    row["gate_ok"] = True
    row["lean_ok"] = True
    row["detail"] = feedback_from_row(row)
    return row


def main() -> int:
    os.environ["PATH"] = (
        "/home/sgli/.elan/bin:/home/sgli/.elan/toolchains/"
        "leanprover--lean4---v4.28.0-rc1/bin:/home/sgli/.local/bin:"
        + os.environ.get("PATH", "")
    )
    os.environ["LAKE_DIR"] = str(PROJECT)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "contracts").mkdir(exist_ok=True)
    todo = failed_ids()
    log(f"contract regen todo {len(todo)}/{len(PROBLEMS)} workers={WORKERS} iters={MAX_ITERS}")
    jsonl = OUT / "results.jsonl"
    lock = threading.Lock()

    def one(pid: str) -> dict:
        spec = load_problem(pid)
        task_dir = OUT / "tasks" / pid
        task_dir.mkdir(parents=True, exist_ok=True)
        history = []
        row: dict = {"prob_id": pid, "accepted": False}
        prev_lean = None
        for it in range(1, MAX_ITERS + 1):
            fb = seed_feedback(pid) if it == 1 else (row.get("detail") or "")
            prompt = build_prompt(
                spec,
                feedback=fb or None,
                previous=prev_lean if it > 1 else None,
            )
            gen = generate_one(
                pid, task_dir, prompt, it, cycle_fields=spec["cycle_fields"],
            )
            row = {"prob_id": pid, **gen}
            row.update(assess_full(pid, spec, task_dir))
            cand = task_dir / "candidate.lean"
            if cand.exists():
                prev_lean = cand.read_text()
            history.append({
                "iteration": it,
                "accept": row.get("accept"),
                "syntax_ok": row.get("syntax_ok", row.get("gate_ok")),
                "sound_ok": row.get("sound_ok"),
                "complete_ok": row.get("complete_ok"),
                "detail": (row.get("detail") or "")[:300],
            })
            if row.get("accept"):
                dest = OUT / "contracts" / f"{pid}.lean"
                dest.write_text((task_dir / "contract.lean").read_text())
                row["accepted"] = True
                row["contract_path"] = str(dest)
                break
        row["history"] = history
        row["iters"] = len(history)
        row["timestamp"] = utc_now()
        return row

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futs = {pool.submit(one, pid): pid for pid in todo}
        for fut in as_completed(futs):
            row = fut.result()
            with lock:
                with jsonl.open("a") as fh:
                    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            log(
                f"{row.get('prob_id')} accepted={row.get('accepted')} "
                f"syn={row.get('syntax_ok')} sound={row.get('sound_ok')} "
                f"comp={row.get('complete_ok')} it={row.get('iters')}"
            )
    log("contract regen finished")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
