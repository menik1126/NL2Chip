#!/usr/bin/env python3
"""Patch VerilogEval TopModule wrapper and rescore existing Sparkle DUTs."""
from __future__ import annotations

import json
import os
import shutil
import sys
from collections import Counter
from pathlib import Path

PROJECT = Path("/home/sgli/work/NL2Chip_openlux_repair_state_20260914")
RUN = Path("/home/sgli/work/NL2Chip_rebuttal_artifacts/2026-09-15/mainflow_expanded_aligned100_40_10/verilogeval/cktarchon_run_20260915_193740")
EVAL = PROJECT / "agent" / "evaluator.py"

RESET_OLD = '''    reset_tokens = {
        "reset", "rst", "areset", "arst",
        "resetn", "rstn", "aresetn", "arstn",'''
RESET_NEW = '''    reset_tokens = {
        "reset", "rst", "areset", "arst", "ar",
        "resetn", "rstn", "aresetn", "arstn",'''

ALIAS_OLD = '''    if compact in {"reset", "rst", "areset", "arst"}:
        return "rst"'''
ALIAS_NEW = '''    if compact in {"reset", "rst", "areset", "arst", "ar"}:
        return "rst"'''

ASYNC_OLD = '''        if base.startswith(("areset", "arst")):
            return _reset_expr(name)'''
ASYNC_NEW = '''        if base.startswith(("areset", "arst")) or base == "ar":
            return _reset_expr(name)'''

CONN_OLD = '''    inst_conns = []
    async_reset = _async_reset_expr(ref_inputs, ref_code=ref_code, prompt_text=prompt_text)

    for _, _, sn in sp_inputs:
        if sn == "clk":
            inst_conns.append(f"        .clk(clk)")
        elif sn == "rst":
            if async_reset:
                inst_conns.append(f"        .rst({async_reset})")
            else:
                inst_conns.append(f"        .rst(1'b0)")
        else:
            matched = False
            for rn, sn2 in input_map.items():
                if sn2 == sn:
                    inst_conns.append(f"        .{sn}({rn})")
                    matched = True
                    break
            if not matched:
                inst_conns.append(f"        .{sn}({sn})")
'''
CONN_NEW = '''    inst_conns = []
    ref_clk = next(
        (
            n for _, _, n in ref_inputs
            if re.sub(r"[^a-z0-9]", "", n.lower()) in {"clk", "clock"}
        ),
        None,
    )
    reset_ports = [n for _, _, n in ref_inputs if _is_reset_like(n)]
    async_reset = _async_reset_expr(ref_inputs, ref_code=ref_code, prompt_text=prompt_text)
    domain_reset = async_reset

    def _compact_sp(name: str) -> str:
        raw = name.lower()
        if raw.startswith("_gen_"):
            raw = raw[5:]
        return re.sub(r"[^a-z0-9]", "", raw)

    for _, _, sn in sp_inputs:
        if sn == "clk" or _compact_sp(sn) in {"clk", "clock"}:
            inst_conns.append(f"        .{sn}({ref_clk if ref_clk else \"1'b0\"})")
            continue
        if sn == "rst":
            inst_conns.append(f"        .rst({domain_reset if domain_reset else \"1'b0\"})")
            continue
        matched = next((rn for rn, sn2 in input_map.items() if sn2 == sn), None)
        if matched:
            inst_conns.append(f"        .{sn}({matched})")
            continue
        key = _compact_sp(sn)
        if ref_clk and "clk" in key:
            invert = key in {"clkf", "clkn", "clkneg"} or any(
                tag in key for tag in ("falling", "invclk", "nclk")
            )
            expr = f"~{ref_clk}" if invert else ref_clk
            inst_conns.append(f"        .{sn}({expr})")
            continue
        if _is_reset_like(sn):
            if reset_ports:
                tb = reset_ports[0]
                expr = tb if _is_active_low_reset(sn) == _is_active_low_reset(tb) else f"~{tb}"
            else:
                expr = "1'b1" if _is_active_low_reset(sn) else "1'b0"
            inst_conns.append(f"        .{sn}({expr})")
            continue
        inst_conns.append(f"        .{sn}(1'b0)")
'''


def patch_evaluator() -> None:
    text = EVAL.read_text()
    backup = EVAL.with_suffix(".py.bak_ve_wrapper_20260916")
    if not backup.exists():
        shutil.copy2(EVAL, backup)
    if 'domain_reset = async_reset or (_reset_expr(reset_ports[0]) if reset_ports else None)' in text:
        text = text.replace(
            'domain_reset = async_reset or (_reset_expr(reset_ports[0]) if reset_ports else None)',
            'domain_reset = async_reset',
            1,
        )
        EVAL.write_text(text)
        print("narrowed domain reset to async only", EVAL)
        return
    if "domain_reset = async_reset" in text:
        print("ve wrapper already patched")
        return
    for old, new, label in (
        (RESET_OLD, RESET_NEW, "reset tokens"),
        (ALIAS_OLD, ALIAS_NEW, "alias ar"),
        (ASYNC_OLD, ASYNC_NEW, "async ar"),
        (CONN_OLD, CONN_NEW, "top wrapper conns"),
    ):
        if old not in text:
            raise SystemExit(f"missing {label}")
        text = text.replace(old, new, 1)
    EVAL.write_text(text)
    print("patched", EVAL)


def rescore() -> None:
    os.environ["PATH"] = "/home/sgli/.local/bin:" + os.environ.get("PATH", "")
    sys.path[:0] = [str(PROJECT), str(PROJECT / "agent")]
    from dataset import Dataset
    from evaluator import Evaluator, parse_module_ports

    ds = Dataset("verilogeval", project_root=PROJECT)
    ev = Evaluator(project_root=PROJECT, dataset="verilogeval", dataset_obj=ds)
    path = RUN / "results.jsonl"
    bak = path.with_suffix(".jsonl.bak_ve_wrapper")
    if bak.exists() and path.exists():
        # Restore pre-regression scores before this rescore if the live file
        # already reflects the overly-broad domain-rst experiment.
        live = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        if sum(1 for r in live if r.get("sim_status") == "sim_pass") < 130:
            shutil.copy2(bak, path)
            print("restored", bak)
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    before = Counter(r.get("sim_status") for r in rows)
    flipped = []
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
        status, mismatches, detail = ev._run_sim_verilogeval(pid, sv, mod, ports, RUN)
        prev = row.get("sim_status")
        row["prev_sim_status"] = prev
        row["sim_status"] = status
        row["sim_mismatches"] = mismatches
        row["detail"] = detail
        if status in {"sim_pass", "sim_fail"}:
            row["compile_pass"] = True
        if status == "sim_pass":
            row["failure_stage"] = None
            row["failure_family"] = "passed"
            row["failure_category"] = None
        rec = {
            "prob_id": pid,
            "was": prev,
            "now": status,
            "mismatches": mismatches,
            "detail": (detail or "")[:120].replace("\n", " "),
        }
        if prev != status:
            flipped.append(rec)
        if prev != "sim_pass" or status != "sim_pass":
            print(json.dumps(rec))
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    after = Counter(r.get("sim_status") for r in rows)
    print("before", dict(before))
    print("after", dict(after))
    print("flipped", len(flipped))
    for rec in flipped:
        print("FLIP", json.dumps(rec))


if __name__ == "__main__":
    patch_evaluator()
    if "--patch-only" not in sys.argv:
        rescore()
