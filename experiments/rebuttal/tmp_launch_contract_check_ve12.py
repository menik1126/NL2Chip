#!/usr/bin/env python3
"""Run the 3-layer contract checkers on the frozen VE-12 contracts.

Does not overwrite formal_contract_gen_ve12/contracts.
Writes a new report tree.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from tmp_contract_checkers import check_contract  # noqa: E402
from tmp_launch_contract_gen_ve12 import (  # noqa: E402
    PROBLEMS, PROJECT, environment, load_problem, log, utc_now,
)

SRC = Path("/home/sgli/work/NL2Chip_rebuttal_artifacts/2026-09-23/formal_contract_gen_ve12")
OUT = Path("/home/sgli/work/NL2Chip_rebuttal_artifacts/2026-09-24/formal_contract_check_ve12")
WORKERS = int(os.environ.get("CONTRACT_CHECK_WORKERS", "2"))
JUDGE = os.environ.get("CONTRACT_JUDGE", "1") != "0"


def main() -> int:
    env = environment()
    os.environ.update({k: v for k, v in env.items() if v is not None})
    os.environ["PATH"] = (
        "/home/sgli/.elan/bin:/home/sgli/.elan/toolchains/"
        "leanprover--lean4---v4.28.0-rc1/bin:/home/sgli/.local/bin:"
        + os.environ.get("PATH", "")
    )
    os.environ["LAKE_DIR"] = str(PROJECT)
    OUT.mkdir(parents=True, exist_ok=True)
    jsonl = OUT / "results.jsonl"
    lock = threading.Lock()
    log(f"contract check {len(PROBLEMS)} judge={JUDGE} workers={WORKERS}")

    def one(pid: str) -> dict:
        spec = load_problem(pid)
        lean_path = SRC / "contracts" / f"{pid}.lean"
        contract = lean_path.read_text()
        work = OUT / "work" / pid
        row = check_contract(
            prob_id=pid,
            contract=contract,
            ref_sv=(PROJECT / "verilog-eval" / "dataset_spec-to-rtl" / f"{pid}_ref.sv").read_text(),
            nl=spec["nl"],
            work=work,
            wrapper=None,
            run_llm_judge=JUDGE,
        )
        row["timestamp"] = utc_now()
        row["contract_path"] = str(lean_path)
        return row

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futs = {pool.submit(one, pid): pid for pid in PROBLEMS}
        for fut in as_completed(futs):
            row = fut.result()
            with lock:
                with jsonl.open("a") as fh:
                    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            log(
                f"{row.get('prob_id')} accept={row.get('accept')} "
                f"syn={row.get('syntax_ok')} sound={row.get('sound_ok')} "
                f"comp={row.get('complete_ok')} judge={row.get('judge_ok')} "
                f"kill={((row.get('completeness') or {}).get('n_killed'))}/"
                f"{((row.get('completeness') or {}).get('n_applied'))}"
            )
    rows = [json.loads(l) for l in jsonl.read_text().splitlines() if l.strip()]
    summary = {
        "n": len(rows),
        "syntax": sum(1 for r in rows if r.get("syntax_ok")),
        "sound": sum(1 for r in rows if r.get("sound_ok")),
        "complete": sum(1 for r in rows if r.get("complete_ok")),
        "accept": sum(1 for r in rows if r.get("accept")),
        "timestamp": utc_now(),
    }
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    log("summary " + json.dumps(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
