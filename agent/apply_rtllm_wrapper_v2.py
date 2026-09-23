#!/usr/bin/env python3
"""Install RTLLM wrapper v2 on H20 and rescore existing Sparkle DUTs."""
from __future__ import annotations

import json
import os
import shutil
import sys
from collections import Counter
from pathlib import Path

PROJECT = Path("/home/sgli/work/NL2Chip_openlux_repair_state_20260914")
RUN = Path("/home/sgli/work/NL2Chip_rebuttal_artifacts/2026-09-15/mainflow_expanded_aligned100_40_10/rtllm/cktarchon_run_20260915_193740")
EVAL = PROJECT / "agent" / "evaluator.py"
HERE = Path(__file__).resolve().parent
MARKER = "from rtllm_wrapper import build_rtllm_wrapper"
OLD_INSERT = '''        from rtllm_wrapper import build_rtllm_wrapper
        return build_rtllm_wrapper(
            design_name,
            sparkle_mod_name,
            sparkle_ports,
            tb_code,
            sv_code,
            ports_equivalent=_ports_equivalent,
            port_width=_port_width,
            is_reset_like=_is_reset_like,
            is_active_low_reset=_is_active_low_reset,
        )
'''
INSERT = '''        from rtllm_wrapper import build_rtllm_wrapper
        _built = build_rtllm_wrapper(
            design_name,
            sparkle_mod_name,
            sparkle_ports,
            tb_code,
            sv_code,
            ref_code,
            ports_equivalent=_ports_equivalent,
            port_width=_port_width,
            is_reset_like=_is_reset_like,
            is_active_low_reset=_is_active_low_reset,
        )
        if _built:
            return _built
'''


def patch_evaluator() -> None:
    dest = PROJECT / "agent" / "rtllm_wrapper.py"
    src = HERE / "rtllm_wrapper.py"
    if src.resolve() != dest.resolve():
        shutil.copy2(src, dest)
    text = EVAL.read_text()
    backup = EVAL.with_suffix(".py.bak_rtllm_wrapper_v2_20260915")
    if not backup.exists():
        shutil.copy2(EVAL, backup)
    if OLD_INSERT in text:
        text = text.replace(OLD_INSERT, INSERT, 1)
        EVAL.write_text(text)
        print("updated wrapper hook", EVAL)
        return
    if MARKER in text:
        print("wrapper v2 already wired")
        return
    needle = '        Handles bundled outputs (Sparkle packs multiple outputs into one port).\n        """\n'
    if needle not in text:
        raise SystemExit("wrapper docstring not found")
    text = text.replace(needle, needle + INSERT, 1)
    EVAL.write_text(text)
    print("patched", EVAL, "copied", dest)


def rescore() -> None:
    os.environ["PATH"] = "/home/sgli/.local/bin:" + os.environ.get("PATH", "")
    sys.path[:0] = [str(PROJECT), str(PROJECT / "agent")]
    from dataset import Dataset
    from evaluator import Evaluator, parse_module_ports

    ds = Dataset("rtllm", project_root=PROJECT)
    ev = Evaluator(project_root=PROJECT, dataset="rtllm", dataset_obj=ds)
    path = RUN / "results.jsonl"
    shutil.copy2(path, path.with_suffix(".jsonl.bak_wrapper_v2b"))
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    before = Counter(r.get("sim_status") for r in rows)
    focus = {
        "ROM", "calendar", "freq_div", "traffic_light",
        "fixed_point_adder", "fixed_point_substractor", "clkgenerator", "barrel_shifter",
    }
    for row in rows:
        pid = row["prob_id"]
        sv_path = RUN / "sv" / f"{pid}.sv"
        if not sv_path.exists():
            print(json.dumps({"prob_id": pid, "skipped": "no sv", "sim_status": row.get("sim_status")}))
            continue
        sv = sv_path.read_text()
        info = ds.load_problem(pid)
        mod, ports = parse_module_ports(sv, module_name=info.design_name)
        if not mod:
            mod, ports = parse_module_ports(sv)
        status, mismatches, detail = ev._run_sim_rtllm(pid, sv, mod, ports, RUN)
        row["prev_sim_status"] = row.get("sim_status")
        row["sim_status"] = status
        row["sim_mismatches"] = mismatches
        row["detail"] = detail
        if status in {"sim_pass", "sim_fail"}:
            row["compile_pass"] = True
        if status == "sim_pass":
            row["failure_stage"] = None
            row["failure_family"] = "passed"
            row["failure_category"] = None
        payload = {
            "prob_id": pid,
            "was": row.get("prev_sim_status"),
            "now": status,
            "detail": (detail or "")[:160].replace("\n", " "),
        }
        if pid in focus:
            payload["focus"] = True
        print(json.dumps(payload))
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    after = Counter(r.get("sim_status") for r in rows)
    print("before", dict(before))
    print("after", dict(after))
    print("focus", {pid: next(r["sim_status"] for r in rows if r["prob_id"] == pid) for pid in focus if any(r["prob_id"] == pid for r in rows)})


if __name__ == "__main__":
    patch_evaluator()
    if "--patch-only" not in sys.argv:
        rescore()
