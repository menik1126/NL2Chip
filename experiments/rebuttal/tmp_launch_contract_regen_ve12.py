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
    PRIVATE,
    PROBLEMS,
    PROJECT,
    WRAPPER,
    WORKERS,
    environment,
    generate_one,
    load_problem,
    log,
    prepare_wrapper,
    utc_now,
)
from tmp_contract_validate import validate_contract  # noqa: E402

CHECK = Path("/home/sgli/work/NL2Chip_rebuttal_artifacts/2026-09-23/formal_contract_check_ve12")
OUT = Path("/home/sgli/work/NL2Chip_rebuttal_artifacts/2026-09-23/formal_contract_gen_ve12_checked")

SYSTEM = """You translate a natural-language hardware description into a FORMAL CONTRACT.

A contract is a list of next-cycle CONDITIONS the circuit must satisfy.
It is NOT a second implementation, NOT synthesizable RTL, NOT Sparkle Signal code.

Output ONLY one Lean file. No tools, no shell, no explanation.

REQUIRED SHAPE:
import Init

namespace Contract.<ProbId>

structure Cycle where
  <every non-clk port>
  <optional ghost fields for hidden state: count, fall_cnt, dir, rx_stage, ...>

def <name> (pre post : Cycle) : Prop :=
  <implication or equality over pre.* and post.*>

end Contract.<ProbId>

RULES:
1. Clock is implicit. Cycle is one post-posedge snapshot.
2. At least 3 named properties.
3. Each property must mention pre.<field> and post.<field> and use → or ¬ or equality.
4. Forbidden: Sparkle, Signal, #synthesizeVerilog, theorem, lemma, sorry, Verilog, TopModule.
5. Do NOT reconstruct packed datapaths with magic constants (+7, +103, +1639, +0x07 on a whole word). Constrain digits/bits.
6. Do NOT write tautologies such as post.q = post.q or pre.q = pre.q. If a case is don't-care, omit it.
7. Import only Init.
8. Hidden counters/protocol stages belong in extra Cycle fields, then constrain them observationally.
9. For UART: start bit 0, 8 data bits LSB first, stop bit 1, out_byte valid when done.
10. For Lemmings: fall > dig > turn; splat if fall_cnt > 20 then ground.
"""


def build_prompt(spec: dict, *, feedback: str | None = None) -> str:
    bullets = GOLD.get(spec["prob_id"], [])
    prompt = (
        SYSTEM
        + "\n"
        + FEWSHOT
        + f"\nCurrent problem id: {spec['prob_id']}\n"
        + "Use this exact namespace: "
        + f"Contract.{spec['prob_id']}\n\n"
        + "Natural-language description:\n"
        + spec["nl"]
        + "\n\nPort list (clk omitted from Cycle; extra ghost fields allowed):\n"
        + spec["iface"]
        + "\n\nRequired Cycle port fields:\n"
        + spec["cycle_fields"]
        + "\n\nGold requirement bullets you MUST cover:\n"
        + json.dumps(bullets, ensure_ascii=False)
        + "\n\nWrite the contract Lean file now."
    )
    if feedback:
        prompt += (
            "\n\nThe previous contract was REJECTED by syntax/soundness/completeness/judge.\n"
            "Fix every listed failure. Output ONLY the Lean file.\n"
            f"### Checker diagnostics\n```\n{feedback[:4000]}\n```\n"
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
    return [p for p in PROBLEMS if not (done.get(p) or {}).get("accept")]


def assess_full(pid: str, spec: dict, task_dir: Path) -> dict:
    cand = task_dir / "candidate.lean"
    if not cand.exists():
        return {"gate_ok": False, "lean_ok": False, "accept": False, "detail": "no Lean extracted"}
    text = cand.read_text()
    gate = validate_contract(text, prob_id=pid, ports=spec["ports"], min_props=MIN_PROPS)
    if not gate["ok"]:
        return {
            "gate_ok": False, "lean_ok": False, "accept": False,
            "syntax": gate, "detail": "; ".join(gate["reasons"])[:4000],
        }
    checked = task_dir / "contract.lean"
    checked.write_text(text)
    ok, out = lean_check(checked, PROJECT)
    if not ok:
        return {"gate_ok": True, "lean_ok": False, "accept": False, "detail": out[-4000:]}
    row = check_contract(
        prob_id=pid,
        contract=text,
        ref_sv=(PROJECT / "verilog-eval" / "dataset_spec-to-rtl" / f"{pid}_ref.sv").read_text(),
        nl=spec["nl"],
        work=task_dir / "check",
        wrapper=WRAPPER,
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
    import subprocess as _sp
    _sp.check_call(["bash", "/home/sgli/work/codex_jing_chatgpt_probe/ensure_socks.sh"])
    prepare_wrapper()
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
        for it in range(1, MAX_ITERS + 1):
            fb = None if it == 1 else (row.get("detail") or "")
            prompt = build_prompt(spec, feedback=fb)
            gen = generate_one(pid, task_dir, prompt, it)
            row = {"prob_id": pid, **gen}
            row.update(assess_full(pid, spec, task_dir))
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
