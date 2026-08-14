"""
Sparkle Evaluator — compile, extract Verilog, lint, simulate, synthesize, P&R, DRC, LVS.

Given a prob_id, runs the full evaluation pipeline:
  1. lake build Generated.<prob_id>  → check compilation
  2. Extract SystemVerilog from build output
  3. iverilog lint check
  4. iverilog simulation against VerilogEval testbench (with TopModule wrapper)
  5. (optional) Yosys synthesis via ORFS Docker → PPA extraction
  6. (optional) Full P&R (floorplan → place → CTS → route → finish) → GDS
  7. (optional) DRC via KLayout
  8. (optional) LVS via KLayout (best-effort)

Returns a score dict for each problem.
"""
from __future__ import annotations

import math
import json
import os
import re
import signal
import shutil
import subprocess
import sys
from pathlib import Path

from cvdp_native_parameters import (
    native_plan_from_dict,
    parse_native_modules,
    select_native_core_module,
    sv_sha256,
    validate_native_parameter_ownership,
)
from cvdp_specialization import FiniteParameterPlan, plan_from_dict
from parameter_backends import (
    cppsim_policy_is_required_failure,
    evaluate_formal_parameter_policy,
    formal_policy_is_required_failure,
    ppa_manifest_failure_stage,
    ppa_policy_is_required_failure,
    run_cppsim_parameter_policy,
    run_ppa_parameter_policy,
)
from orfs_runner import run_docker_command


DATASET_DIR = Path("verilog-eval/dataset_spec-to-rtl")
RTLLM_PASS_PATTERN = re.compile(r"Your Design Passed", re.IGNORECASE)
RESBENCH_PASS_PATTERN = re.compile(r"All tests passed|Test PASSED", re.IGNORECASE)
BASH_TIMEOUT = 120
SIM_TIMEOUT = 30
SYNTH_TIMEOUT = 600
PNR_TIMEOUT = 900
DRC_TIMEOUT = 300
LVS_TIMEOUT = 300
STA_TIMEOUT = 120
GLS_TIMEOUT = 120
MIN_DIE_SIDE_UM = 50  # minimum die side for sky130hd PDN straps

DIAGNOSTIC_STAGES = {
    "lean_elaboration",
    "symbolic_dimension_lowering",
    "verilog_extraction",
    "parameter_contract",
    "verilog_elaboration",
    "simulation_mismatch",
    "unsupported_backend",
    "infrastructure",
}


def _record_failure(
    result: dict,
    stage: str,
    detail: str,
    *,
    code: str | None = None,
) -> None:
    if stage not in DIAGNOSTIC_STAGES:
        raise ValueError(f"unknown diagnostic stage: {stage}")
    result["failure_stage"] = stage
    result["detail"] = detail
    diagnostic = {"stage": stage, "message": detail}
    if code:
        diagnostic["code"] = code
    result.setdefault("diagnostics", []).append(diagnostic)


def _lean_diagnostic_stage(detail: str) -> str:
    text = str(detail or "").lower()
    symbolic_markers = (
        "symbolic dimension",
        "dimexpr",
        "parameter-dependent",
        "native parameter",
        "unsupported dimension",
    )
    return (
        "symbolic_dimension_lowering"
        if any(marker in text for marker in symbolic_markers)
        else "lean_elaboration"
    )


def _simulation_diagnostic_stage(status: str, detail: str) -> str | None:
    if status == "sim_pass":
        return None
    if status == "sim_fail":
        return "simulation_mismatch"
    text = str(detail or "").lower()
    if any(marker in text for marker in (
        "compile failed", "iverilog", "syntax error", "elaboration",
    )):
        return "verilog_elaboration"
    return "infrastructure"

# ── sky130 cell simulation models (via volare PDK manager) ──────────
SKY130_VOLARE_VERSION = "c6d73a35f524070e85faff4a6a9eef49553ebc2b"
SKY130_VOLARE_VERILOG_DIR = (
    Path.home() / ".volare" / "volare" / "sky130" / "versions"
    / SKY130_VOLARE_VERSION / "sky130A" / "libs.ref" / "sky130_fd_sc_hd" / "verilog"
)
SKY130_PRIMITIVES_NAME = "primitives.v"
SKY130_CELLS_NAME = "sky130_fd_sc_hd.v"

# ── PVT Corner definitions (sky130hd) ──────────────────────────────

PVT_CORNERS = [
    {
        "name": "ss_100C_1v60",
        "lib": "sky130_fd_sc_hd__ss_100C_1v60.lib",
        "label": "SS (100°C, 1.60V)",
    },
    {
        "name": "tt_025C_1v80",
        "lib": "sky130_fd_sc_hd__tt_025C_1v80.lib",
        "label": "TT (25°C, 1.80V)",
    },
    {
        "name": "ff_n40C_1v95",
        "lib": "sky130_fd_sc_hd__ff_n40C_1v95.lib",
        "label": "FF (-40°C, 1.95V)",
    },
]


# ── Port parsing (from autoformalize.py) ─────────────────────────


def _matching_paren(text: str, open_idx: int) -> int:
    depth = 0
    for idx in range(open_idx, len(text)):
        ch = text[idx]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return idx
    return -1


def _split_sv_commas(text: str) -> list[str]:
    parts: list[str] = []
    start = 0
    paren = bracket = brace = 0
    for idx, ch in enumerate(text):
        if ch == "(":
            paren += 1
        elif ch == ")":
            paren = max(0, paren - 1)
        elif ch == "[":
            bracket += 1
        elif ch == "]":
            bracket = max(0, bracket - 1)
        elif ch == "{":
            brace += 1
        elif ch == "}":
            brace = max(0, brace - 1)
        elif ch == "," and paren == 0 and bracket == 0 and brace == 0:
            parts.append(text[start:idx].strip())
            start = idx + 1
    tail = text[start:].strip()
    if tail:
        parts.append(tail)
    return parts


def _module_records(sv_code: str) -> list[tuple[str, str, str]]:
    """Return module name, ANSI port text, and body text without regex backtracking."""
    sv_code = re.sub(r"//.*", "", sv_code)
    sv_code = re.sub(r"/\*.*?\*/", "", sv_code, flags=re.DOTALL)
    records: list[tuple[str, str, str]] = []
    search_from = 0
    while True:
        m = re.search(r"\bmodule\s+([A-Za-z_]\w*)\b", sv_code[search_from:])
        if not m:
            break
        module_start = search_from + m.start()
        mod_name = m.group(1)
        idx = search_from + m.end()
        while idx < len(sv_code) and sv_code[idx].isspace():
            idx += 1
        if idx < len(sv_code) and sv_code[idx] == "#":
            idx += 1
            while idx < len(sv_code) and sv_code[idx].isspace():
                idx += 1
            if idx < len(sv_code) and sv_code[idx] == "(":
                end = _matching_paren(sv_code, idx)
                if end == -1:
                    search_from = idx + 1
                    continue
                idx = end + 1
        while idx < len(sv_code) and sv_code[idx].isspace():
            idx += 1

        header_text = ""
        header_end = idx
        if idx < len(sv_code) and sv_code[idx] == "(":
            end = _matching_paren(sv_code, idx)
            if end == -1:
                search_from = idx + 1
                continue
            header_text = sv_code[idx + 1:end]
            header_end = end + 1

        end_match = re.search(r"\bendmodule\b", sv_code[header_end:])
        if end_match:
            module_end = header_end + end_match.start()
            search_from = header_end + end_match.end()
        else:
            module_end = len(sv_code)
            search_from = len(sv_code)
        records.append((mod_name, header_text, sv_code[header_end:module_end]))

        if search_from <= module_start:
            search_from = module_start + len("module")
    return records


def _first_module_record(
    sv_code: str,
    module_name: str | None = None,
) -> tuple[str, str, str]:
    records = _module_records(sv_code)
    if module_name:
        for record in records:
            if record[0] == module_name:
                return record
    if records:
        return records[0]
    return "", "", ""


def _first_module_header(
    sv_code: str,
    module_name: str | None = None,
) -> tuple[str, str]:
    """Return a module name and ANSI port text without regex backtracking."""
    mod_name, header_text, _ = _first_module_record(sv_code, module_name)
    return mod_name, header_text


def parse_module_name(sv_code: str, module_name: str | None = None) -> str:
    """Parse a module name, allowing an optional parameter list."""
    mod_name, _ = _first_module_header(sv_code, module_name)
    return mod_name


def parse_module_ports(
    sv_code: str,
    module_name: str | None = None,
) -> tuple[str, list[tuple[str, str, str]]]:
    """Parse a SystemVerilog module to get name and ports."""
    mod_name, header_text, body_text = _first_module_record(sv_code, module_name)
    if not mod_name:
        return "", []

    ports = []
    if header_text and re.search(r"\b(?:input|output)\b", header_text):
        current_direction = None
        current_width = ""
        for entry in _split_sv_commas(header_text):
            m = re.match(
                r"^(?:(input|output)\s+)?(?:(?:reg|logic|wire)\s*)?(?:signed\s*)?(\[[^\]]+\])?\s*([A-Za-z_]\w*)$",
                entry,
            )
            if not m:
                continue
            if m.group(1):
                current_direction = m.group(1)
                current_width = ""
            if m.group(2) is not None:
                current_width = m.group(2)
            if current_direction is None:
                continue
            typ = f"logic {current_width}".strip() if current_width else "logic"
            ports.append((current_direction, typ, m.group(3)))
        if ports:
            return mod_name, ports

    for pm in re.finditer(
        r"(input|output)\s+(?:(?:reg|logic|wire)\s*)?(?:signed\s*)?(\[[^\]]+\])?\s*([A-Za-z_]\w*)",
        body_text,
    ):
        direction, width, name = pm.group(1), pm.group(2), pm.group(3)
        typ = f"logic {width}".strip() if width else "logic"
        ports.append((direction, typ, name))
    return mod_name, ports


def _port_width(typ: str) -> int:
    """Extract bit width from a type like 'logic [7:0]' or 'logic'."""
    m = re.search(r"\[(\d+):(\d+)\]", typ)
    if m:
        return abs(int(m.group(1)) - int(m.group(2))) + 1
    return 1


def _strip_sv_comments(code: str) -> str:
    code = re.sub(r"//.*", "", code)
    return re.sub(r"/\*.*?\*/", "", code, flags=re.DOTALL)


def _base_port_name(name: str) -> str:
    name = name.lower()
    return name[5:] if name.startswith("_gen_") else name


def _port_alias_key(name: str) -> str:
    base = _base_port_name(name)
    compact = re.sub(r"[^a-z0-9]", "", base)
    if compact == "input":
        return "in"
    if compact == "output":
        return "out"
    if compact == "clock":
        return "clk"
    if compact in {"reset", "rst", "areset", "arst"}:
        return "rst"
    if compact in {"resetn", "rstn", "aresetn", "arstn"}:
        return "rstn"
    compact = compact.replace("areset", "rst")
    compact = compact.replace("reset", "rst")
    compact = compact.replace("arst", "rst")
    return compact


def _ports_equivalent(left: str, right: str) -> bool:
    if left == right:
        return True
    if _base_port_name(left) == _base_port_name(right):
        return True
    return _port_alias_key(left) == _port_alias_key(right)


def _is_reset_like(name: str) -> bool:
    base = _base_port_name(name)
    compact = re.sub(r"[^a-z0-9]", "", base)
    tokens = [t for t in re.split(r"[^a-z0-9]+", base) if t]
    reset_tokens = {
        "reset", "rst", "areset", "arst",
        "resetn", "rstn", "aresetn", "arstn",
        "resetni", "rstni", "aresetni", "arstni",
        "resetb", "rstb",
    }
    reset_suffixes = tuple(reset_tokens - {"reset", "rst", "areset", "arst"})
    return (
        compact in reset_tokens
        or compact.endswith(reset_suffixes)
        or any(t in reset_tokens for t in tokens)
    )


def _is_active_low_reset(name: str) -> bool:
    base = _base_port_name(name)
    compact = re.sub(r"[^a-z0-9]", "", base)
    return (
        base.endswith(("_n", "_ni", "_b"))
        or compact in {"resetn", "rstn", "aresetn", "arstn", "resetni", "rstni", "aresetni", "arstni", "resetb", "rstb"}
        or compact.endswith(("resetn", "rstn", "aresetn", "arstn", "resetni", "rstni", "aresetni", "arstni", "resetb", "rstb"))
    )


def _reset_expr(name: str) -> str:
    return f"~{name}" if _is_active_low_reset(name) else name


def _has_async_reset_sensitivity(reset_name: str, sv_code: str) -> bool:
    clean = _strip_sv_comments(sv_code).lower()
    reset = re.escape(reset_name.lower())
    for sens in re.findall(r"\balways(?:_ff)?\s*@\s*\(([^)]*)\)", clean, re.DOTALL):
        if re.search(rf"\b(?:posedge|negedge)\s+{reset}\b", sens):
            return True
    return False


def _async_reset_expr(
    ref_inputs: list[tuple[str, str, str]],
    ref_code: str = "",
    prompt_text: str = "",
) -> str | None:
    reset_ports = [n for _, _, n in ref_inputs if _is_reset_like(n)]
    if not reset_ports:
        return None

    prompt = prompt_text.lower()
    for name in reset_ports:
        base = _base_port_name(name)
        if base.startswith(("areset", "arst")):
            return _reset_expr(name)
        if _has_async_reset_sensitivity(name, ref_code):
            return _reset_expr(name)
        if re.search(r"\b(?:async|asynchronous)\b", prompt) and base in prompt:
            return _reset_expr(name)
    return None


def parse_ref_ports(ref_sv: str) -> list[tuple[str, str, str]]:
    """Parse RefModule ports from reference Verilog."""
    ports = []
    for m in re.finditer(
        r"(input|output)\s+(?:reg\s*|logic\s*|wire\s*)?(?:signed\s*)?(\[[\d:]+\])?\s*([A-Za-z_]\w*)",
        ref_sv,
    ):
        direction = m.group(1)
        width = m.group(2) or ""
        name = m.group(3)
        typ = f"logic {width}".strip() if width else "logic"
        ports.append((direction, typ, name))
    return ports


def _parse_ref_module_ports(
    ref_code: str,
    preferred_port_names: list[str] | None = None,
) -> list[tuple[str, str, str]] | None:
    """Parse reference Verilog module to get ports in declaration order.

    Handles both ANSI and non-ANSI port styles, including comma-separated names.
    If multiple modules exist, prefer the one whose ports overlap most with
    preferred_port_names. Returns [(direction, width_str, name), ...] in module
    declaration order, or None.
    """
    module_matches = list(re.finditer(
        r"module\s+(\w+)\s*(?:#\s*\((?:[^)(]+|\([^)(]*\))*\)\s*)?\((.*?)\)\s*;(.*?)endmodule",
        ref_code,
        re.DOTALL,
    ))
    if not module_matches:
        return None

    preferred = set(preferred_port_names or [])
    candidates: list[tuple[int, int, list[tuple[str, str, str]]]] = []

    for mod_match in module_matches:
        header = re.sub(r"//.*", "", mod_match.group(2))
        header = re.sub(r"/\*.*?\*/", "", header, flags=re.DOTALL)
        body = re.sub(r"//.*", "", mod_match.group(3))
        body = re.sub(r"/\*.*?\*/", "", body, flags=re.DOTALL)

        ports = []
        if re.search(r"\b(?:input|output)\b", header):
            current_direction = None
            current_width = ""
            for entry in [p.strip() for p in header.split(",") if p.strip()]:
                m = re.match(
                    r"^(?:(input|output)\s+)?(?:(?:reg|logic|wire)\s*)?(?:signed\s*)?(\[[^\]]+\])?\s*([A-Za-z_]\w*)$",
                    entry,
                )
                if not m:
                    continue
                if m.group(1):
                    current_direction = m.group(1)
                    current_width = ""
                if m.group(2) is not None:
                    current_width = f" {m.group(2)}"
                if current_direction is None:
                    continue
                ports.append((current_direction, current_width, m.group(3)))
        else:
            raw_names = [p.strip().split()[-1] for p in header.split(",")]
            clean_names = [n for n in raw_names if re.match(r"\w+$", n)]
            if not clean_names:
                continue

            port_info: dict[str, tuple[str, str]] = {}
            for m in re.finditer(
                r"(input|output)\s+(?:reg\s*|wire\s*|logic\s*)?(?:signed\s*)?(\[[^\]]+\])?\s*([^;]+)",
                body,
            ):
                direction = m.group(1)
                width = f" {m.group(2)}" if m.group(2) else ""
                for name in m.group(3).split(","):
                    name = name.strip()
                    if name and re.match(r"\w+$", name):
                        port_info[name] = (direction, width)

            ports = [
                (port_info.get(n, ("input", ""))[0], port_info.get(n, ("input", ""))[1], n)
                for n in clean_names
            ]

        if ports:
            overlap = sum(1 for _, _, name in ports if name in preferred)
            candidates.append((overlap, len(ports), ports))

    if not candidates:
        return None

    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return candidates[0][2]


def _rename_module_declaration(sv_code: str, old_name: str, new_name: str) -> str:
    """Rename only the first module declaration."""
    return re.sub(
        rf"(\bmodule\s+){re.escape(old_name)}(\b)",
        rf"\1{new_name}\2",
        sv_code,
        count=1,
    )


def generate_top_wrapper(
    sparkle_mod_name: str,
    sparkle_ports: list[tuple[str, str, str]],
    ref_ports: list[tuple[str, str, str]],
    sv_code: str = "",
    ref_code: str = "",
    prompt_text: str = "",
) -> str | None:
    """Generate a TopModule wrapper mapping ref ports to sparkle ports."""
    ref_inputs = [(d, t, n) for d, t, n in ref_ports if d == "input"]
    ref_outputs = [(d, t, n) for d, t, n in ref_ports if d == "output"]
    sp_inputs = [(d, t, n) for d, t, n in sparkle_ports if d == "input"]
    sp_outputs = [(d, t, n) for d, t, n in sparkle_ports if d == "output"]

    sp_user_inputs = [(d, t, n) for d, t, n in sp_inputs if n not in ("clk", "rst")]
    ref_user_inputs = [(d, t, n) for d, t, n in ref_inputs if n != "clk"]

    input_map: dict[str, str] = {}
    # First pass: match by name (_gen_{ref_name} or exact match)
    matched_sp = set()
    for _, _, rn in ref_user_inputs:
        for _, _, sn in sp_user_inputs:
            if sn not in matched_sp and _ports_equivalent(sn, rn):
                input_map[rn] = sn
                matched_sp.add(sn)
                break
    # Second pass: fall back to positional for any unmatched
    unmatched_ref = [rn for _, _, rn in ref_user_inputs if rn not in input_map]
    unmatched_sp = [sn for _, _, sn in sp_user_inputs if sn not in matched_sp]
    for rn, sn in zip(unmatched_ref, unmatched_sp):
        input_map[rn] = sn

    output_map: dict[str, str] = {}
    bundled_output = False
    if len(ref_outputs) > len(sp_outputs) == 1:
        # Sparkle bundled multiple outputs into one port — check widths match
        sp_width = _port_width(sp_outputs[0][1])
        ref_total = sum(_port_width(t) for _, t, _ in ref_outputs)
        if sp_width == ref_total:
            bundled_output = True
    if not bundled_output:
        # First pass: match by name
        matched_sp_out = set()
        for _, _, rn in ref_outputs:
            for _, _, sn in sp_outputs:
                if sn not in matched_sp_out and _ports_equivalent(sn, rn):
                    output_map[rn] = sn
                    matched_sp_out.add(sn)
                    break
        # Second pass: positional fallback
        unmatched_ref_out = [rn for _, _, rn in ref_outputs if rn not in output_map]
        unmatched_sp_out = [sn for _, _, sn in sp_outputs if sn not in matched_sp_out]
        for rn, sn in zip(unmatched_ref_out, unmatched_sp_out):
            output_map[rn] = sn

    lines = ["module TopModule ("]
    all_ref_ports = ref_inputs + ref_outputs
    port_decls = []
    for d, t, n in all_ref_ports:
        width = ""
        wm = re.search(r"\[(\d+):(\d+)\]", t)
        if wm:
            width = f" [{wm.group(1)}:{wm.group(2)}]"
        port_decls.append(f"    {d}{width} {n}")
    lines.append(",\n".join(port_decls))
    lines.append(");")
    lines.append("")

    for _, t, n in sp_outputs:
        lines.append(f"    {t} {n}_wire;")

    lines.append(f"    {sparkle_mod_name} dut (")
    inst_conns = []
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

    for _, _, sn in sp_outputs:
        inst_conns.append(f"        .{sn}({sn}_wire)")

    lines.append(",\n".join(inst_conns))
    lines.append("    );")

    if bundled_output:
        # Bit-slice the single bundled output.
        # Sparkle packs via concat: {field_a, field_b} where field_a = MSB.
        # Try to infer correct mapping by matching field names in the concat
        # against ref output names. Fall back to positional MSB-first.
        sp_out_name = sp_outputs[0][2]
        sp_width = _port_width(sp_outputs[0][1])

        # Try to find the concat that feeds the output port (may be indirect)
        concat_order = None
        # Direct: assign out = {a, b}
        concat_pat = re.search(
            rf"assign\s+{re.escape(sp_out_name)}\s*=\s*\{{([^}}]+)\}}",
            sv_code,
        )
        if not concat_pat:
            # Indirect: assign out = _tmp; assign _tmp = {a, b}
            indirect = re.search(
                rf"assign\s+{re.escape(sp_out_name)}\s*=\s*(\w+)\s*;",
                sv_code,
            )
            if indirect:
                tmp_name = indirect.group(1)
                concat_pat = re.search(
                    rf"assign\s+{re.escape(tmp_name)}\s*=\s*\{{([^}}]+)\}}",
                    sv_code,
                )
        if concat_pat:
            fields = [f.strip() for f in concat_pat.group(1).split(",")]
            # Map _gen_X fields to ref names: _gen_sum -> sum
            concat_order = []
            for f in fields:
                base = f.replace("_gen_", "") if f.startswith("_gen_") else f
                concat_order.append(base)

        # Check if concat field names actually match ref output names
        use_concat = False
        if concat_order and len(concat_order) == len(ref_outputs):
            ref_names = {rn for _, _, rn in ref_outputs}
            if all(f in ref_names for f in concat_order):
                use_concat = True

        if use_concat:
            # Use concat field order: first field = MSB
            offset = sp_width
            for field_name in concat_order:
                # Find matching ref output
                matched_ref = None
                for _, rt, rn in ref_outputs:
                    if rn == field_name:
                        matched_ref = (rt, rn)
                        break
                if matched_ref:
                    rt, rn = matched_ref
                    w = _port_width(rt)
                    high = offset - 1
                    low = offset - w
                    if w == 1:
                        lines.append(f"    assign {rn} = {sp_out_name}_wire[{low}];")
                    else:
                        lines.append(f"    assign {rn} = {sp_out_name}_wire[{high}:{low}];")
                    offset -= w
        else:
            # Fallback: positional, first ref output = MSB
            offset = sp_width
            for _, rt, rn in ref_outputs:
                w = _port_width(rt)
                high = offset - 1
                low = offset - w
                if w == 1:
                    lines.append(f"    assign {rn} = {sp_out_name}_wire[{low}];")
                else:
                    lines.append(f"    assign {rn} = {sp_out_name}_wire[{high}:{low}];")
                offset -= w
    else:
        for rn, sn in output_map.items():
            lines.append(f"    assign {rn} = {sn}_wire;")

    lines.append("endmodule")
    return "\n".join(lines)


def _cvdp_split_commas(text: str) -> list[str]:
    """Split a SystemVerilog comma list without splitting nested expressions."""
    parts = []
    start = 0
    depth = 0
    for idx, ch in enumerate(text):
        if ch in "([{":
            depth += 1
        elif ch in ")]}" and depth > 0:
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append(text[start:idx].strip())
            start = idx + 1
    tail = text[start:].strip()
    if tail:
        parts.append(tail)
    return parts


def _cvdp_normalize_type(width_or_type: str) -> str:
    """Convert a parsed width/type fragment into a legal SV logic type."""
    text = (width_or_type or "").strip()
    if not text:
        return "logic"
    if re.match(r"^(?:logic|wire|reg)\b", text):
        return text
    return f"logic {text}"


def _cvdp_numeric_width(typ: str) -> int | None:
    """Return a concrete bit width when the declaration uses numeric bounds."""
    m = re.search(r"\[\s*(\d+)\s*:\s*(\d+)\s*\]", typ)
    if not m:
        if "[" in (typ or "") and "]" in (typ or ""):
            return None
        return 1
    return abs(int(m.group(1)) - int(m.group(2))) + 1


def _cvdp_expr_numeric_width(expr: str, type_lookup: dict[str, str]) -> int | None:
    """Return a concrete width for a simple field expression, honoring slices."""
    text = expr.strip()
    m = re.search(r"\[\s*(\d+)\s*:\s*(\d+)\s*\]\s*$", text)
    if m:
        return abs(int(m.group(1)) - int(m.group(2))) + 1
    m = re.search(r"\[\s*(\d+)\s*\]\s*$", text)
    if m:
        return 1
    name = _cvdp_expr_signal_name(text)
    if not name:
        return None
    typ = type_lookup.get(name)
    if name.startswith("_gen_"):
        typ = typ or type_lookup.get(name[5:])
    return _cvdp_numeric_width(typ or "")


def _cvdp_port_width_decl(typ: str) -> str:
    m = re.search(r"(\[[^\]]+\])", typ)
    return f" {m.group(1)}" if m else ""


def _cvdp_type_mentions_parameter(typ: str, params: set[str]) -> bool:
    return any(re.search(rf"\b{re.escape(param)}\b", typ or "") for param in params)


def _cvdp_expr_signal_name(expr: str) -> str | None:
    """Return the signal identifier at the root of a simple SV expression."""
    text = expr.strip()
    text = re.sub(r"\[[^\]]+\]\s*$", "", text)
    m = re.match(r"([A-Za-z_]\w*)$", text)
    if m:
        return m.group(1)
    m = re.match(r"([A-Za-z_]\w*)", text)
    return m.group(1) if m else None


def _cvdp_signal_type_lookup(
    sv_code: str,
    sparkle_ports: list[tuple[str, str, str]],
) -> dict[str, str]:
    """Collect concrete widths for generated ports and internal SV signals."""
    lookup: dict[str, str] = {}

    def remember(name: str, typ: str) -> None:
        norm = _cvdp_normalize_type(typ)
        lookup.setdefault(name, norm)
        if name.startswith("_gen_"):
            lookup.setdefault(name[5:], norm)

    for _, typ, name in sparkle_ports:
        remember(name, typ)

    clean = _strip_sv_comments(sv_code)
    for m in re.finditer(
        r"\b(?:logic|wire|reg)\s*(?:signed\s*)?(\[[^\]]+\])?\s*([^;]+);",
        clean,
        re.DOTALL,
    ):
        typ = _cvdp_normalize_type(m.group(1) or "")
        for decl in _cvdp_split_commas(m.group(2)):
            name_match = re.match(r"\s*([A-Za-z_]\w*)", decl.split("=", 1)[0].strip())
            if name_match:
                remember(name_match.group(1), typ)
    return lookup


def _cvdp_internal_unpacked_arrays(
    sv_code: str,
    module_name: str | None = None,
) -> dict[str, tuple[str, str]]:
    """Return internal unpacked arrays as name -> (element type, range)."""
    _, _, body = _first_module_record(sv_code, module_name)
    clean = _strip_sv_comments(body)
    arrays: dict[str, tuple[str, str]] = {}
    for match in re.finditer(
        r"\b(?:logic|wire|reg)\s*(?:signed\s*)?"
        r"(?P<packed>\[[^\]]+\])?\s*"
        r"(?P<name>[A-Za-z_]\w*)\s*"
        r"(?P<unpacked>\[[^\]]+\])\s*;",
        clean,
        re.DOTALL,
    ):
        arrays[match.group("name")] = (
            _cvdp_normalize_type(match.group("packed") or ""),
            match.group("unpacked"),
        )
    return arrays


def _cvdp_match_internal_array(
    name: str,
    generated_arrays: dict[str, tuple[str, str]],
    used: set[str],
) -> str | None:
    """Conservatively match a benchmark-visible internal array to Sparkle RTL."""
    candidates = [candidate for candidate in generated_arrays if candidate not in used]
    exact = [candidate for candidate in candidates if _ports_equivalent(candidate, name)]
    if len(exact) == 1:
        return exact[0]
    # Generated Sparkle names often describe the memory's read value rather
    # than the reference array. A unique memory is still unambiguous.
    if len(candidates) == 1:
        return candidates[0]
    return None


def _cvdp_unpacked_loop_bounds(unpacked_range: str) -> tuple[str, str] | None:
    """Return ascending loop bounds for common unpacked array declarations."""
    match = re.fullmatch(r"\[\s*(.+?)\s*:\s*(.+?)\s*\]", unpacked_range or "")
    if not match:
        return None
    left, right = (part.strip() for part in match.groups())
    if left == "0":
        return "0", right
    if right == "0":
        return "0", left
    if left.isdigit() and right.isdigit():
        low, high = sorted((int(left), int(right)))
        return str(low), str(high)
    return None


def _cvdp_name_forms(name: str) -> set[str]:
    base = _base_port_name(name)
    forms = {base, re.sub(r"[^a-z0-9]", "", base)}
    variants = {base, re.sub(r"_\d+$", "", base)}
    for prefix in ("o_", "out_", "output_", "predict_branch_", "predict_", "dmem_", "saved_", "req_"):
        if base.startswith(prefix):
            variants.add(base[len(prefix):])
    for suffix in ("_bit", "_flag", "_val", "_value", "_out", "_output", "_bv", "_signal", "_reg", "_r", "_o"):
        for item in list(variants):
            if item.endswith(suffix):
                variants.add(item[: -len(suffix)])
    for item in variants:
        if item:
            forms.add(item)
            forms.add(re.sub(r"[^a-z0-9]", "", item))
    return {f for f in forms if f}


def _cvdp_match_output_for_field(
    field_expr: str,
    expected_outputs: list[tuple[str, str, str]],
    used_outputs: set[str],
) -> tuple[str, str, str] | None:
    """Map a packed concat field back to the benchmark output it represents."""
    field_name = _cvdp_expr_signal_name(field_expr)
    if not field_name:
        return None
    candidates = [p for p in expected_outputs if p[2] not in used_outputs]

    match = _cvdp_match_port(field_name, candidates, direction="output")
    if match:
        return match

    field_forms = _cvdp_name_forms(field_name)
    scored: list[tuple[int, tuple[str, str, str]]] = []
    for cand in candidates:
        out_forms = _cvdp_name_forms(cand[2])
        field_compact = re.sub(r"[^a-z0-9]", "", _base_port_name(field_name))
        out_compact = re.sub(r"[^a-z0-9]", "", _base_port_name(cand[2]))
        score = 0
        if field_forms & out_forms:
            score = 90
        elif re.sub(r"[^a-z0-9]", "", _base_port_name(cand[2])) in field_forms:
            score = 80
        elif any(len(form) >= 4 and form in field_compact for form in out_forms):
            score = 70
        elif any(len(form) >= 4 and form in out_compact for form in field_forms):
            score = 65
        elif "pc" in out_forms and any(form.endswith("pc") for form in field_forms):
            score = 60
        # Saved request registers are Sparkle state names; CVDP exposes the
        # corresponding dmem_req_* observation ports.
        elif field_compact.startswith("saved"):
            semantic = re.sub(r"\d+$", "", field_compact[len("saved"):])
            if any(form.startswith("req") and semantic in form for form in out_forms):
                score = 75
        if score:
            scored.append((score, cand))

    if not scored:
        return None
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[0][1]


def _cvdp_concat_assignments(sv_code: str) -> dict[str, list[str]]:
    clean = _strip_sv_comments(sv_code)
    assigns: dict[str, list[str]] = {}
    for m in re.finditer(
        r"\bassign\s+([A-Za-z_]\w*)\s*=\s*\{([^;]+)\}\s*;",
        clean,
        re.DOTALL,
    ):
        assigns[m.group(1)] = _cvdp_split_commas(m.group(2))
    return assigns


def _cvdp_infer_concat_fields(sv_code: str, sp_out_name: str) -> list[str] | None:
    """Infer raw SV fields packed into a Sparkle bundled output."""
    clean = _strip_sv_comments(sv_code)
    assigns = _cvdp_concat_assignments(clean)
    direct = assigns.get(sp_out_name)
    if direct is None:
        indirect = re.search(
            rf"\bassign\s+{re.escape(sp_out_name)}\s*=\s*([A-Za-z_]\w*)\s*;",
            clean,
        )
        if indirect:
            direct = assigns.get(indirect.group(1))
    if direct is None:
        return None

    def expand(expr: str, seen: set[str]) -> list[str]:
        name = _cvdp_expr_signal_name(expr)
        if name and name.startswith("_tmp") and name in assigns and name not in seen:
            out: list[str] = []
            for child in assigns[name]:
                out.extend(expand(child, seen | {name}))
            return out
        return [expr.strip()]

    fields: list[str] = []
    for item in direct:
        fields.extend(expand(item, {sp_out_name}))
    return fields


def _cvdp_infer_bundled_output_mapping(
    sv_code: str,
    sp_out_name: str,
    expected_outputs: list[tuple[str, str, str]],
    sparkle_ports: list[tuple[str, str, str]],
) -> list[tuple[tuple[str, str, str], str, str | None]]:
    """Return MSB-first mapping from expected output ports to packed fields."""
    fields = _cvdp_infer_concat_fields(sv_code, sp_out_name) or []
    if not fields:
        return []
    type_lookup = _cvdp_signal_type_lookup(sv_code, sparkle_ports)
    used: set[str] = set()
    mapping: list[tuple[tuple[str, str, str], str, str | None]] = []
    for field in fields:
        matched = _cvdp_match_output_for_field(field, expected_outputs, used)
        if not matched:
            continue
        field_name = _cvdp_expr_signal_name(field) or ""
        field_type = type_lookup.get(field_name)
        if field_name.startswith("_gen_"):
            field_type = field_type or type_lookup.get(field_name[5:])
        mapping.append((matched, field, field_type))
        used.add(matched[2])
    return mapping


def _cvdp_assign_bundled_output_slices(
    *,
    sp_out_name: str,
    sp_out_type: str,
    remaining_outputs: list[tuple[str, str, str]],
    sv_code: str,
    sparkle_ports: list[tuple[str, str, str]],
) -> list[tuple[str, int, int]]:
    """Map a packed Sparkle tuple to every benchmark output when provable.

    Sparkle lowers tuples MSB-first. Prefer a semantic field-name match, but
    generated names such as _gen_enc1 often have no benchmark spelling.
    In that case declaration order is safe only when all remaining field and
    port widths agree exactly; otherwise leave the output unmapped.
    """
    sp_width = _cvdp_numeric_width(sp_out_type)
    fields = _cvdp_infer_concat_fields(sv_code, sp_out_name) or []
    if sp_width is None or not fields:
        return []
    type_lookup = _cvdp_signal_type_lookup(sv_code, sparkle_ports)
    field_widths = [_cvdp_expr_numeric_width(field, type_lookup) for field in fields]
    if any(width is None for width in field_widths) or sum(field_widths) != sp_width:
        return []

    offset = sp_width
    slices: list[tuple[str, int, int]] = []
    used: set[str] = set()
    unresolved: list[tuple[int, int, int]] = []
    for field, field_width in zip(fields, field_widths):
        high, low = offset - 1, offset - field_width
        matched = _cvdp_match_output_for_field(field, remaining_outputs, used)
        if matched and _cvdp_numeric_width(matched[1]) == field_width:
            slices.append((matched[2], high, low))
            used.add(matched[2])
        else:
            unresolved.append((field_width, high, low))
        offset -= field_width

    remaining = [port for port in remaining_outputs if port[2] not in used]
    if len(remaining) == len(unresolved) and all(
        _cvdp_numeric_width(port[1]) == field_width
        for port, (field_width, _, _) in zip(remaining, unresolved)
    ):
        slices.extend(
            (port[2], high, low)
            for port, (_, high, low) in zip(remaining, unresolved)
        )
    # A subset can still be a sound semantic mapping (for example a bundle
    # also carries an unobserved valid bit). The caller emits fallbacks only
    # for genuinely unmapped benchmark outputs.
    return slices


def _cvdp_parse_harness_usage(harness_files: dict) -> dict[str, set[str]]:
    """Infer CVDP top-level ports/parameters touched by cocotb harnesses."""
    py_files = {
        str(path): str(content)
        for path, content in harness_files.items()
        if str(path).endswith(".py")
    }
    port_py_text = "\n\n".join(
        content
        for path, content in py_files.items()
        if Path(path).name != "harness_library.py"
    )
    helper_py_text = "\n\n".join(
        content for path, content in py_files.items()
        if Path(path).name == "harness_library.py"
    )
    param_py_text = "\n\n".join(py_files.values())
    id_names = set(re.findall(r"\bdut\._id\s*\(\s*[\"']([A-Za-z_]\w*)[\"']", port_py_text))
    indexed_names = set(re.findall(r"\bdut\s*\[\s*[\"']([A-Za-z_]\w*)[\"']\s*\]", port_py_text))
    all_names = (
        set(re.findall(r"\bdut\.([A-Za-z_]\w*)\b", port_py_text))
        | id_names
        | indexed_names
    ) - {"_log", "_id"}
    assigned = set(
        re.findall(r"\bdut\.([A-Za-z_]\w*)\.value\s*=(?!=)", port_py_text)
    )
    assigned.update(
        re.findall(r"\bdut\._id\s*\(\s*[\"']([A-Za-z_]\w*)[\"'][^)]*\)\.value\s*=(?!=)", port_py_text)
    )
    assigned.update(
        re.findall(r"\bdut\s*\[\s*[\"']([A-Za-z_]\w*)[\"']\s*\]\.value\s*=(?!=)", port_py_text)
    )
    assigned.update(re.findall(r"\bdut\.([A-Za-z_]\w*)\s*<=", port_py_text))
    assigned.update(
        re.findall(r"\bdut\s*\[\s*[\"']([A-Za-z_]\w*)[\"']\s*\]\s*<=", port_py_text)
    )
    # CVDP helper classes frequently drive interfaces through
    # getattr(dut, f"{name}_suffix"). Recover literal helper prefixes and
    # suffixes so those ports are not silently tied to zero in the wrapper.
    for name_var, suffix in re.findall(
        r"getattr\s*\(\s*dut\s*,\s*f[\"']\{([A-Za-z_]\w*)\}_([A-Za-z_]\w*)[\"']\s*\)",
        param_py_text,
    ):
        prefixes = set(re.findall(
            rf"\b{name_var}\s*=\s*[\"']([A-Za-z_]\w*)[\"']", param_py_text
        ))
        recovered = {f"{prefix}_{suffix}" for prefix in prefixes}
        all_names.update(recovered)
        assigned.update(recovered)
    # Helpers can dynamically access bus ports using getattr(dut,
    # f"{name}_suffix"). Pair each helper class's suffixes with prefixes from
    # calls to that *same* class; cross-producting all helper prefixes creates
    # fictitious DUT ports such as ex_if_req_addr_o.
    for class_match in re.finditer(
        r"\bclass\s+([A-Za-z_]\w*).*?(?=\nclass\s+|\Z)",
        helper_py_text,
        re.DOTALL,
    ):
        class_name, class_body = class_match.group(1), class_match.group(0)
        suffixes = set(re.findall(
            r"getattr\s*\(\s*dut\s*,\s*f[\"']\{name\}_([A-Za-z_]\w*)[\"']\s*\)",
            class_body,
        ))
        prefixes = set(re.findall(
            rf"\b{re.escape(class_name)}\s*\(\s*dut\s*,\s*[\"']([A-Za-z_]\w*)[\"']",
            port_py_text,
        ))
        recovered = {f"{prefix}_{suffix}" for prefix in prefixes for suffix in suffixes}
        all_names.update(recovered)
        assigned.update(name for name in recovered if name.endswith(("_i", "_in")))
    clocked = set(re.findall(r"\bClock\s*\(\s*dut\.([A-Za-z_]\w*)\b", port_py_text))

    params: set[str] = set()
    for body in re.findall(r"\b(?:parameter|parameters)\s*=\s*\{([^}]+)\}", param_py_text):
        params.update(re.findall(r"[\"']([A-Za-z_]\w*)[\"']\s*:", body))
    params.update(
        name for name in all_names
        if name not in assigned
        and name == name.upper()
        and re.search(r"(?:^|_)(?:WIDTH|DEPTH|SIZE|THRESHOLD|PARAM|COUNT|NUM|NS|N)(?:_|$)", name)
    )

    clock_or_reset = {
        n for n in all_names
        if _is_reset_like(n) or "clk" in n.lower() or "clock" in n.lower()
    }
    inputs = set(assigned) | set(clocked) | clock_or_reset
    ports = all_names - params
    outputs = ports - inputs
    return {
        "ports": ports,
        "inputs": inputs & ports,
        "outputs": outputs,
        "params": params,
    }


def _cvdp_parse_module_parameters(ref_code: str, usage_params: set[str]) -> list[str]:
    """Parse parameter declarations from the reference/context module header."""
    text = re.sub(r"//.*", "", ref_code)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    params: dict[str, str] = {}
    module_re = re.compile(
        r"module\s+\w+\s*#\s*\((?P<params>(?:[^)(]+|\([^)(]*\))*)\)\s*\(",
        re.DOTALL,
    )
    for match in module_re.finditer(text):
        for entry in _cvdp_split_commas(match.group("params")):
            m = re.search(
                r"\bparameter\b\s+(?:(?:integer|int|logic|bit)\s+)?"
                r"(?:\[[^\]]+\]\s*)?(?P<name>[A-Za-z_]\w*)"
                r"(?:\s*=\s*(?P<expr>.+))?$",
                entry,
                re.DOTALL,
            )
            if not m:
                continue
            name = m.group("name")
            expr = (m.group("expr") or "1").strip()
            params.setdefault(name, f"parameter {name} = {expr}")

    for name in sorted(usage_params):
        params.setdefault(name, f"parameter {name} = 1")
    return list(params.values())


def _cvdp_match_port(
    name: str,
    candidates: list[tuple[str, str, str]],
    *,
    direction: str | None = None,
) -> tuple[str, str, str] | None:
    """Find a candidate port by exact, _gen_-stripped, or case-insensitive name."""
    filtered = [p for p in candidates if direction is None or p[0] == direction]
    wanted = [name, f"_gen_{name}"]
    lowered = {n.lower() for n in wanted}
    for port in filtered:
        if port[2] in wanted:
            return port
    for port in filtered:
        if port[2].lower() in lowered:
            return port
    for port in filtered:
        if _ports_equivalent(port[2], name):
            return port
    return None


def _cvdp_clock_or_reset_match(
    sp_name: str,
    expected_inputs: list[tuple[str, str, str]],
) -> str | None:
    names = [n for _, _, n in expected_inputs]
    lower = {n.lower(): n for n in names}
    if sp_name == "clk":
        for cand in ("clk", "clock", "clk_i", "clk_in", "i_clk", "aclk", "ATTN_CLK"):
            if cand.lower() in lower:
                return lower[cand.lower()]
        for n in names:
            if "clk" in n.lower() or "clock" in n.lower():
                return n
    if sp_name == "rst":
        for cand in ("rst", "reset", "srst", "rst_i", "rst_in", "reset_i", "reset_in", "reset_n", "rst_ni"):
            if cand.lower() in lower:
                return lower[cand.lower()]
        for n in names:
            nl = n.lower()
            if "rst" in nl or "reset" in nl:
                return n
    return None


def _cvdp_bridge_reset_expr(sp_name: str, matched_name: str) -> str:
    if (
        _is_reset_like(sp_name)
        and _is_reset_like(matched_name)
        and _is_active_low_reset(sp_name) != _is_active_low_reset(matched_name)
    ):
        return f"~{matched_name}"
    return matched_name


def _cvdp_infer_concat_order(sv_code: str, sp_out_name: str) -> list[str] | None:
    """Infer names packed into a Sparkle bundled output, if visible."""
    fields = _cvdp_infer_concat_fields(sv_code, sp_out_name)
    if not fields:
        return None
    names = []
    for field in fields:
        name = _cvdp_expr_signal_name(field)
        if name:
            names.append(name[5:] if name.startswith("_gen_") else name)
    return names or None


def generate_cvdp_wrapper(
    design_name: str,
    sparkle_mod_name: str,
    sparkle_ports: list[tuple[str, str, str]],
    ref_code: str,
    harness_files: dict,
    sv_code: str = "",
    expose_parameters: bool = True,
    expected_ports_override: list[tuple[str, str, str]] | None = None,
    reset_polarities: dict[str, str] | None = None,
    strict_mapping: bool = False,
    required_parameter_names: set[str] | None = None,
) -> str | None:
    """Generate a CVDP top wrapper matching cocotb's expected DUT interface."""
    usage = _cvdp_parse_harness_usage(harness_files)
    preferred_names = sorted(usage["ports"] | usage["params"])
    raw_ref_ports = (
        expected_ports_override
        if expected_ports_override is not None
        else _parse_ref_module_ports(ref_code, preferred_names) or []
    )
    ref_ports = [(d, _cvdp_normalize_type(t), n) for d, t, n in raw_ref_ports]

    by_name = {n: (d, t, n) for d, t, n in ref_ports}
    expected_ports = list(ref_ports)
    ref_internal_arrays = (
        {} if expected_ports_override is not None
        else _cvdp_internal_unpacked_arrays(ref_code, design_name)
    )
    observed_internal_arrays = {
        name: ref_internal_arrays[name]
        for name in sorted(usage["ports"] - set(by_name))
        if name in ref_internal_arrays
    }
    sp_inputs = [(d, t, n) for d, t, n in sparkle_ports if d == "input"]
    sp_outputs = [(d, t, n) for d, t, n in sparkle_ports if d == "output"]
    bundled_type_by_output: dict[str, str] = {}
    if len(sp_outputs) == 1:
        candidate_output_names = sorted(
            set(usage["outputs"]) | {n for d, _, n in ref_ports if d == "output"}
        )
        if expected_ports_override is None or len(candidate_output_names) > 1:
            pseudo_outputs = [("output", "logic", n) for n in candidate_output_names]
            for out_port, _, field_type in _cvdp_infer_bundled_output_mapping(
                sv_code,
                sp_outputs[0][2],
                pseudo_outputs,
                sparkle_ports,
            ):
                if field_type:
                    bundled_type_by_output[out_port[2]] = field_type

    for name in sorted(usage["ports"]):
        if name in by_name:
            continue
        if name in observed_internal_arrays:
            continue
        if expected_ports_override is not None:
            continue
        sp_match = _cvdp_match_port(name, sparkle_ports)
        if sp_match:
            typ = sp_match[1]
        elif name in usage["outputs"] and name in bundled_type_by_output:
            typ = bundled_type_by_output[name]
        elif name in usage["outputs"] and len(sp_outputs) == 1:
            typ = sp_outputs[0][1]
        else:
            typ = "logic"
        direction = "input" if name in usage["inputs"] else "output"
        port = (direction, _cvdp_normalize_type(typ), name)
        by_name[name] = port
        expected_ports.append(port)

    if bundled_type_by_output:
        expected_ports = [
            (d, bundled_type_by_output.get(n, t) if d == "output" else t, n)
            for d, t, n in expected_ports
        ]

    if len(sp_outputs) == 1 and (
        expected_ports_override is None
        or sum(direction == "output" for direction, _, _ in expected_ports) > 1
    ):
        bundle_fields = _cvdp_infer_concat_fields(sv_code, sp_outputs[0][2]) or []
        bundle_types = _cvdp_signal_type_lookup(sv_code, sparkle_ports)
        inferred = [
            _cvdp_expr_numeric_width(field, bundle_types) for field in bundle_fields
        ]
        missing = [p for p in expected_ports if p[0] == "output" and p[2] not in bundled_type_by_output]
        if len(missing) == len(inferred) and all(width is not None for width in inferred):
            inferred_by_name = {
                port[2]: ("logic" if width == 1 else f"logic [{width - 1}:0]")
                for port, width in zip(missing, inferred)
            }
            expected_ports = [
                (d, inferred_by_name.get(n, t) if d == "output" else t, n)
                for d, t, n in expected_ports
            ]

    if not expected_ports or not sparkle_mod_name:
        return None

    expected_inputs = [(d, t, n) for d, t, n in expected_ports if d == "input"]
    expected_outputs = [(d, t, n) for d, t, n in expected_ports if d == "output"]
    param_decls = (
        _cvdp_parse_module_parameters(ref_code, usage["params"])
        if expose_parameters else []
    )
    param_names = set(usage["params"]) if expose_parameters else set()
    for decl in param_decls:
        m = re.search(r"\bparameter\b\s+(?:\w+\s+)?(?:\[[^\]]+\]\s*)?([A-Za-z_]\w*)", decl)
        if m:
            param_names.add(m.group(1))

    wrapper_notes: list[str] = []
    if param_names:
        wrapper_notes.append(
            "benchmark parameters exposed by wrapper: "
            + ", ".join(sorted(param_names))
        )

    def note_fixed_width_core_port(
        sp_port: tuple[str, str, str],
        expected: tuple[str, str, str] | None,
    ) -> None:
        if expected is None or not param_names:
            return
        _, sp_t, sp_n = sp_port
        _, exp_t, exp_n = expected
        if (
            _cvdp_type_mentions_parameter(exp_t, param_names)
            and _cvdp_numeric_width(sp_t) is not None
            and not _cvdp_type_mentions_parameter(sp_t, param_names)
        ):
            wrapper_notes.append(
                f"Sparkle core port {sp_n} has fixed type {sp_t} while "
                f"benchmark port {exp_n} is parameterized as {exp_t}"
            )

    for sp_port in sp_inputs:
        _, _, sn = sp_port
        matched_name = _cvdp_clock_or_reset_match(sn, expected_inputs)
        matched_port = None
        if matched_name is not None:
            matched_port = next((p for p in expected_inputs if p[2] == matched_name), None)
        if matched_port is None:
            base = sn[5:] if sn.startswith("_gen_") else sn
            matched_port = _cvdp_match_port(base, expected_inputs, direction="input")
        note_fixed_width_core_port(sp_port, matched_port)

    for sp_port in sp_outputs:
        _, _, sn = sp_port
        base = sn[5:] if sn.startswith("_gen_") else sn
        note_fixed_width_core_port(sp_port, _cvdp_match_port(base, expected_outputs, direction="output"))

    lines = [f"module {design_name}"]
    if param_decls:
        lines.append(" #(")
        lines.append(",\n".join(f"    {decl}" for decl in param_decls))
        lines.append(")")
    lines.append(" (")
    lines.append(",\n".join(f"    {d} {t} {n}" for d, t, n in expected_ports))
    lines.append(");")
    lines.append("")
    for note in wrapper_notes:
        lines.append(f"    // CVDP adapter diagnostic: {note}")
    if wrapper_notes:
        lines.append("")

    for _, t, n in sp_outputs:
        lines.append(f"    {t} {n}_wire;")
    if sp_outputs:
        lines.append("")

    for name, (element_type, unpacked_range) in observed_internal_arrays.items():
        lines.append(f"    {element_type} {name} {unpacked_range};")
    if observed_internal_arrays:
        lines.append("")

    core_module = next(
        (module for module in parse_native_modules(sv_code) if module.name == sparkle_mod_name),
        None,
    )
    core_parameters = list(core_module.parameter_names) if core_module else []
    forwarded_parameters = [name for name in core_parameters if name in param_names]
    if strict_mapping and set(required_parameter_names or ()) - param_names:
        return None

    if forwarded_parameters:
        lines.append(f"    {sparkle_mod_name} #(")
        lines.append(",\n".join(
            f"        .{name}({name})" for name in forwarded_parameters
        ))
        lines.append("    ) sparkle_dut (")
    else:
        lines.append(f"    {sparkle_mod_name} sparkle_dut (")
    inst_conns = []
    matched_expected_inputs: set[str] = set()
    for d, _, sn in sparkle_ports:
        if d == "output":
            inst_conns.append(f"        .{sn}({sn}_wire)")
            continue

        matched = _cvdp_clock_or_reset_match(sn, expected_inputs)
        if matched is None:
            base = sn[5:] if sn.startswith("_gen_") else sn
            match = _cvdp_match_port(base, expected_inputs, direction="input")
            matched = match[2] if match else None
        if (
            matched is None
            and expected_ports_override is not None
            and sn.startswith("_gen_")
            and _is_reset_like(sn)
        ):
            public_resets = [
                name for _, _, name in expected_inputs if _is_reset_like(name)
            ]
            if len(public_resets) == 1:
                matched = public_resets[0]
        if (
            matched
            and expected_ports_override is not None
            and sn == "rst"
            and (reset_polarities or {}).get(matched) == "active-low"
        ):
            # Sparkle's implicit domain reset is active-high. A separate
            # user-visible ``_gen_reset`` input still receives the raw signal
            # so Lean's explicit resetLow/resetHigh logic remains authoritative.
            conn = f"~{matched}"
        elif (
            matched
            and expected_ports_override is not None
            and sn.startswith("_gen_")
            and _is_reset_like(sn)
        ):
            conn = matched
        else:
            if matched is None and strict_mapping:
                return None
            conn = _cvdp_bridge_reset_expr(sn, matched) if matched else "'0"
        if matched is not None:
            matched_expected_inputs.add(matched)
        inst_conns.append(f"        .{sn}({conn})")

    if strict_mapping:
        missing_public_inputs = {
            name for _, _, name in expected_inputs
            if name not in matched_expected_inputs
        }
        if missing_public_inputs:
            return None

    lines.append(",\n".join(inst_conns))
    lines.append("    );")
    lines.append("")

    generated_arrays = _cvdp_internal_unpacked_arrays(sv_code, sparkle_mod_name)
    used_generated_arrays: set[str] = set()
    for ref_name, (_, unpacked_range) in observed_internal_arrays.items():
        generated_name = _cvdp_match_internal_array(
            ref_name,
            generated_arrays,
            used_generated_arrays,
        )
        loop_bounds = _cvdp_unpacked_loop_bounds(unpacked_range)
        if generated_name is None or loop_bounds is None:
            reason = (
                "could not be mapped uniquely to a Sparkle memory"
                if generated_name is None
                else f"has unsupported unpacked range {unpacked_range}"
            )
            lines.append(
                f"    // CVDP adapter diagnostic: internal array {ref_name} was observed by the harness "
                f"but {reason}."
            )
            continue
        used_generated_arrays.add(generated_name)
        loop_low, loop_high = loop_bounds
        bridge_index = f"_cvdp_bridge_{ref_name}_i"
        lines.extend([
            "    generate",
            f"        for (genvar {bridge_index} = {loop_low}; "
            f"{bridge_index} <= {loop_high}; {bridge_index}++) begin : "
            f"_cvdp_bridge_{ref_name}",
            f"            assign {ref_name}[{bridge_index}] = "
            f"sparkle_dut.{generated_name}[{bridge_index}];",
            "        end",
            "    endgenerate",
            "",
        ])

    assigned_outputs: set[str] = set()
    for _, _, rn in expected_outputs:
        sp_match = _cvdp_match_port(rn, sp_outputs, direction="output")
        if sp_match:
            lines.append(f"    assign {rn} = {sp_match[2]}_wire;")
            assigned_outputs.add(rn)

    if len(sp_outputs) == 1:
        sp_out_d, sp_out_t, sp_out_n = sp_outputs[0]
        remaining = [(d, t, n) for d, t, n in expected_outputs if n not in assigned_outputs]
        if remaining:
            sp_w = _cvdp_numeric_width(sp_out_t)
            ref_widths = [(_cvdp_numeric_width(t), n) for _, t, n in remaining]
            if (
                expected_ports_override is not None
                and
                len(expected_outputs) == 1
                and len(remaining) == 1
                and sp_w is not None
                and _cvdp_numeric_width(remaining[0][1]) == sp_w
            ):
                lines.append(f"    assign {remaining[0][2]} = {sp_out_n}_wire;")
                assigned_outputs.add(remaining[0][2])
                field_assigned = True
            else:
                field_assigns = _cvdp_assign_bundled_output_slices(
                    sp_out_name=sp_out_n,
                    sp_out_type=sp_out_t,
                    remaining_outputs=remaining,
                    sv_code=sv_code,
                    sparkle_ports=sparkle_ports,
                )
                field_assigned = bool(field_assigns)
                for n, high, low in field_assigns:
                    if high == low:
                        lines.append(f"    assign {n} = {sp_out_n}_wire[{low}];")
                    else:
                        lines.append(f"    assign {n} = {sp_out_n}_wire[{high}:{low}];")
                    assigned_outputs.add(n)
            # A scalar/normal single output has no tuple concat to unpack.
            # Preserve direct wiring for this established CVDP case.
            if (
                len(remaining) == 1
                and not field_assigned
                and not _cvdp_infer_concat_fields(sv_code, sp_out_n)
                and (not strict_mapping or len(expected_outputs) == 1)
            ):
                lines.append(f"    assign {remaining[0][2]} = {sp_out_n}_wire;")
                assigned_outputs.add(remaining[0][2])
                field_assigned = True

            if (
                not field_assigned
                and sp_w is not None
                and all(w is not None for w, _ in ref_widths)
            ):
                total = sum(w for w, _ in ref_widths if w is not None)
                if total == sp_w:
                    concat_order = _cvdp_infer_concat_order(sv_code, sp_out_n)
                    remaining_names = {n for _, _, n in remaining}
                    if (
                        concat_order
                        and len(concat_order) == len(remaining)
                        and set(concat_order) == remaining_names
                    ):
                        ordered = [next(p for p in remaining if p[2] == name) for name in concat_order]
                        offset = sp_w
                        for _, t, n in ordered:
                            width = _cvdp_numeric_width(t) or 1
                            high = offset - 1
                            low = offset - width
                            if width == 1:
                                lines.append(f"    assign {n} = {sp_out_n}_wire[{low}];")
                            else:
                                lines.append(f"    assign {n} = {sp_out_n}_wire[{high}:{low}];")
                            assigned_outputs.add(n)
                            offset -= width

    for _, _, rn in expected_outputs:
        if rn not in assigned_outputs:
            if strict_mapping:
                return None
            lines.append(
                f"    // CVDP adapter fallback: output {rn} was not mapped from Sparkle output; "
                "drive zero so simulation reports a functional mismatch."
            )
            lines.append(f"    assign {rn} = '0;")

    lines.append("endmodule")
    return "\n".join(lines)


def _parameter_names_in_type(typ: str, parameter_names: set[str]) -> set[str]:
    return {
        name for name in parameter_names
        if re.search(rf"\b{re.escape(name)}\b", str(typ or ""))
    }


def validate_cvdp_native_parameter_contract(
    *,
    sv_code: str,
    core_module_name: str,
    core_ports: list[tuple[str, str, str]],
    required_parameters: list[str],
    expected_ports: list[tuple[str, str, str]],
) -> list[str]:
    """Validate that CVDP parameters reach the generated Sparkle core."""
    modules = parse_native_modules(sv_code)
    core = next((module for module in modules if module.name == core_module_name), None)
    if core is None:
        return [f"generated core module `{core_module_name}` could not be parsed"]

    diagnostics = validate_native_parameter_ownership(
        core,
        required_parameters=required_parameters,
    )
    parameter_names = set(required_parameters)
    core_outputs = [port for port in core_ports if port[0] == "output"]
    expected_outputs = [port for port in expected_ports if port[0] == "output"]

    for expected in expected_ports:
        direction, expected_type, expected_name = expected
        dependencies = _parameter_names_in_type(expected_type, parameter_names)
        if not dependencies:
            continue
        matched = _cvdp_match_port(expected_name, core_ports, direction=direction)
        if matched is None and direction == "output" and len(core_outputs) == 1:
            # Sparkle represents tuple outputs as one packed port. The wrapper
            # separately proves its field mapping before accepting the design.
            matched = core_outputs[0]
        if matched is None:
            diagnostics.append(
                f"parameter-dependent public port `{expected_name}` has no generated core port"
            )
            continue
        core_dependencies = _parameter_names_in_type(matched[1], parameter_names)
        if not dependencies <= core_dependencies:
            missing = ", ".join(sorted(dependencies - core_dependencies))
            diagnostics.append(
                f"public port `{expected_name}` depends on {missing}, but generated core "
                f"port `{matched[2]}` has fixed/non-matching type `{matched[1]}`"
            )

    if len(core_outputs) == 1 and len(expected_outputs) > 1:
        public_dependencies = set().union(*(
            _parameter_names_in_type(port[1], parameter_names)
            for port in expected_outputs
        ))
        packed_dependencies = _parameter_names_in_type(
            core_outputs[0][1], parameter_names
        )
        if not public_dependencies <= packed_dependencies:
            missing = ", ".join(sorted(public_dependencies - packed_dependencies))
            diagnostics.append(
                f"packed generated output `{core_outputs[0][2]}` does not preserve "
                f"public output parameter(s): {missing}"
            )
    return list(dict.fromkeys(diagnostics))


def prepare_cvdp_native_parameter_design(
    *,
    sv_code: str,
    design_name: str,
    plan: FiniteParameterPlan,
    expected_ports: list[tuple[str, str, str]],
    ref_code: str,
    harness_files: dict,
    derived_parameter_names: list[str] | None = None,
    reset_polarities: dict[str, str] | None = None,
) -> tuple[str | None, dict]:
    """Build one strict CVDP wrapper around a genuinely generic Sparkle core."""
    required_parameters = list(dict.fromkeys([
        *plan.parameter_names,
        *(derived_parameter_names or []),
    ]))
    manifest = {
        "schema_version": 1,
        "mode": "native_parameter_sweep",
        "design_name": design_name,
        "required_parameters": required_parameters,
        "sweep_parameter_names": list(plan.parameter_names),
        "sweep_case_count": len(plan.cases),
        "one_emitted_dut": True,
        "contract_pass": False,
        "diagnostics": [],
    }

    core = select_native_core_module(
        sv_code,
        required_parameters=required_parameters,
        preferred_name=design_name,
    )
    if core is None:
        parsed = parse_native_modules(sv_code)
        declarations = ", ".join(
            f"{module.name}({', '.join(module.parameter_names) or 'no parameters'})"
            for module in parsed
        ) or "none"
        manifest["diagnostics"] = [
            "no generated module owns every required native parameter; "
            f"required={required_parameters}, generated={declarations}"
        ]
        manifest["error"] = manifest["diagnostics"][0]
        return None, manifest

    core_name, core_ports = parse_module_ports(sv_code, module_name=core.name)
    diagnostics = validate_cvdp_native_parameter_contract(
        sv_code=sv_code,
        core_module_name=core.name,
        core_ports=core_ports,
        required_parameters=required_parameters,
        expected_ports=expected_ports,
    )
    manifest.update({
        "generated_core_module": core.name,
        "generated_core_parameters": list(core.parameter_names),
        "generated_core_ports": [list(port) for port in core_ports],
    })
    if diagnostics:
        manifest["diagnostics"] = diagnostics
        manifest["error"] = "; ".join(diagnostics)
        return None, manifest

    inner_name = core_name
    wrapped_core_sv = sv_code
    if core_name == design_name:
        inner_name = f"{design_name}_sparkle_inner"
        wrapped_core_sv = _rename_module_declaration(
            wrapped_core_sv, core_name, inner_name
        )

    inner_ports = parse_module_ports(wrapped_core_sv, module_name=inner_name)[1]
    wrapper = generate_cvdp_wrapper(
        design_name=design_name,
        sparkle_mod_name=inner_name,
        sparkle_ports=inner_ports,
        ref_code=ref_code,
        harness_files=harness_files,
        sv_code=wrapped_core_sv,
        expected_ports_override=expected_ports,
        reset_polarities=reset_polarities or {},
        strict_mapping=True,
        required_parameter_names=set(required_parameters),
    )
    if not wrapper:
        manifest["diagnostics"] = [
            "strict native CVDP wrapper could not map every parameter, input, and output"
        ]
        manifest["error"] = manifest["diagnostics"][0]
        return None, manifest

    missing_bindings = [
        name for name in required_parameters
        if not re.search(
            rf"\.{re.escape(name)}\s*\(\s*{re.escape(name)}\s*\)", wrapper
        )
    ]
    if missing_bindings:
        manifest["diagnostics"] = [
            "native wrapper does not forward core parameter(s): "
            + ", ".join(missing_bindings)
        ]
        manifest["error"] = manifest["diagnostics"][0]
        return None, manifest

    final_sv = f"{wrapped_core_sv.rstrip()}\n\n{wrapper}\n"
    design_hash = sv_sha256(final_sv)
    manifest.update({
        "contract_pass": True,
        "wrapper_parameter_bindings": required_parameters,
        "sv_sha256": design_hash,
        "cases": [
            {
                "parameters": case.values,
                "sv_sha256": design_hash,
                "verilog_elaboration": "not_run",
            }
            for case in plan.cases
        ],
    })
    return final_sv, manifest


def _strip_verilog_info_block(text: str) -> str:
    """Return only complete SystemVerilog modules from a Lean info message."""
    code = str(text or "")
    start = code.find("// Generated by Sparkle HDL")
    if start < 0:
        start = code.find("module ")
    if start < 0:
        return ""
    code = code[start:]
    last_end = code.rfind("endmodule")
    return code[: last_end + len("endmodule")].strip() if last_end >= 0 else ""


def _specialize_sv_type(typ: str, values: dict[str, int]) -> str:
    result = str(typ or "logic")
    for name, value in sorted(values.items(), key=lambda item: -len(item[0])):
        result = re.sub(rf"\b{re.escape(name)}\b", str(value), result)
    result = re.sub(
        r"(?<![$A-Za-z0-9_])(?:clog2|log2)\s*\(",
        "$clog2(",
        result,
    )
    return result


def _safe_sv_int_expr(expr: str) -> int | None:
    text = str(expr or "").strip()
    while "$clog2" in text:
        matches = list(re.finditer(r"\$clog2\s*\(([^()]*)\)", text))
        if not matches:
            return None
        changed = False
        for match in reversed(matches):
            argument = _safe_sv_int_expr(match.group(1))
            if argument is None or argument < 0:
                continue
            value = math.ceil(math.log2(max(1, argument)))
            text = text[:match.start()] + str(value) + text[match.end():]
            changed = True
        if not changed:
            return None
    if not re.fullmatch(r"[0-9\s()+*/%<>&|^~-]+", text):
        return None
    try:
        value = eval(text, {"__builtins__": {}}, {})
    except Exception:
        return None
    if isinstance(value, (int, bool)):
        return int(value)
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _concrete_sv_width(typ: str) -> int | None:
    match = re.search(r"\[\s*(.+?)\s*:\s*(.+?)\s*\]", typ or "")
    if not match:
        return 1
    high = _safe_sv_int_expr(match.group(1))
    low = _safe_sv_int_expr(match.group(2))
    if high is None or low is None:
        return None
    return abs(high - low) + 1


def _fixed_logic_type(width: int) -> str:
    return "logic" if width <= 1 else f"logic [{width - 1}:0]"


def _infer_concrete_public_ports(
    *,
    public_ports: list[tuple[str, str, str]],
    values: dict[str, int],
    core_ports: list[tuple[str, str, str]],
    core_sv: str,
) -> tuple[list[tuple[str, str, str]] | None, list[str]]:
    diagnostics: list[str] = []
    specialized = [
        (direction, _specialize_sv_type(typ, values), name)
        for direction, typ, name in public_ports
    ]
    by_name = {name: (direction, typ, name) for direction, typ, name in core_ports}
    core_outputs = [port for port in core_ports if port[0] == "output"]
    public_outputs = [port for port in specialized if port[0] == "output"]
    inferred_output_widths: dict[str, int] = {}
    if len(core_outputs) == 1 and len(public_outputs) > 1:
        mapping = _cvdp_infer_bundled_output_mapping(
            core_sv,
            core_outputs[0][2],
            public_outputs,
            core_ports,
        )
        for port, field, field_type in mapping:
            width = _concrete_sv_width(field_type or "")
            if width is not None:
                inferred_output_widths[port[2]] = width

    result = []
    for direction, typ, name in specialized:
        unspecified_width = not (typ or "").strip()
        width = None if unspecified_width else _concrete_sv_width(typ)
        match = _cvdp_match_port(name, core_ports, direction=direction)
        core_width = _concrete_sv_width(match[1]) if match else None
        if (
            core_width is None
            and direction == "output"
            and len(core_outputs) == 1
            and len(public_outputs) == 1
        ):
            core_width = _concrete_sv_width(core_outputs[0][1])
        inferred = inferred_output_widths.get(name)
        if unspecified_width and (core_width is not None or inferred is not None):
            width = core_width if core_width is not None else inferred
        if width is None:
            width = core_width or inferred
        if width is None:
            diagnostics.append(f"could not determine concrete width of public port {name}")
            continue
        if not unspecified_width and core_width is not None and core_width != width:
            diagnostics.append(
                f"port {name} width mismatch: contract={width}, core={core_width}"
            )
        if not unspecified_width and inferred is not None and inferred != width:
            diagnostics.append(
                f"packed output {name} width mismatch: contract={width}, core field={inferred}"
            )
        result.append((direction, _fixed_logic_type(width), name))
    if diagnostics or len(result) != len(public_ports):
        return None, diagnostics
    return result, []


def _specialization_condition(values: dict[str, int]) -> str:
    return " && ".join(f"({name} == {value})" for name, value in values.items())


def _finite_case_expression(
    plan: FiniteParameterPlan,
    case_values: list[int],
) -> str:
    expression = str(case_values[0])
    for case, value in reversed(list(zip(plan.cases, case_values))):
        expression = f"({_specialization_condition(case.values)}) ? {value} : ({expression})"
    return expression


def _selector_port_type(
    plan: FiniteParameterPlan,
    widths: list[int],
) -> str:
    if all(width == 1 for width in widths):
        return "logic"
    return f"logic [({_finite_case_expression(plan, widths)})-1:0]"


def _solve_derived_width_parameter(
    *,
    typ: str,
    parameter_name: str,
    case_values: dict[str, int],
    concrete_width: int,
) -> int | None:
    if not re.search(rf"\b{re.escape(parameter_name)}\b", typ or ""):
        return None
    upper_bound = max(1024, concrete_width * 4 + 16)
    matches = []
    for value in range(1, upper_bound + 1):
        values = {**case_values, parameter_name: value}
        candidate = _concrete_sv_width(_specialize_sv_type(typ, values))
        if candidate == concrete_width:
            matches.append(value)
            if len(matches) > 1:
                break
    return matches[0] if len(matches) == 1 else None


def _public_derived_parameter_value(
    *,
    parameter_name: str,
    case_values: dict[str, int],
    public_text: str,
) -> int | None:
    """Evaluate the small derived-width formulas used by the public P0 tasks."""
    name = parameter_name.upper()
    if name == "BIT_WIDTH" and "DICE_MAX" in case_values:
        return (max(1, case_values["DICE_MAX"] - 1)).bit_length() + 1
    if name == "ENCODED_DATA" and {"DATA_WIDTH", "PARITY_BIT"} <= case_values.keys():
        return case_values["DATA_WIDTH"] + case_values["PARITY_BIT"] + 1
    if name == "ENCODED_DATA_BIT" and {"DATA_WIDTH", "PARITY_BIT"} <= case_values.keys():
        encoded = case_values["DATA_WIDTH"] + case_values["PARITY_BIT"] + 1
        return max(1, math.ceil(math.log2(max(1, encoded))))
    if name == "COUNT_WIDTH" and "BIT_WIDTH" in case_values:
        return max(1, math.ceil(math.log2(case_values["BIT_WIDTH"] + 1)))

    text = re.sub(r"//.*", "", str(public_text or ""))
    match = re.search(
        rf"\b(?:parameter|localparam)\b[^;\n,]*\b{re.escape(parameter_name)}\b\s*=\s*([^,;\n)]+(?:\([^\n]*\)[^,;\n]*)?)",
        text,
        re.IGNORECASE,
    )
    if not match:
        return None
    expr = match.group(1).strip()
    for key, value in sorted(case_values.items(), key=lambda item: -len(item[0])):
        expr = re.sub(rf"\b{re.escape(key)}\b", str(value), expr)
    expr = re.sub(
        r"\$?clog2\s*\(\s*(\d+)\s*\)",
        lambda m: str(max(1, math.ceil(math.log2(max(1, int(m.group(1))))))),
        expr,
        flags=re.IGNORECASE,
    )
    return _safe_sv_int_expr(expr)


def _cppsim_native_case_requests(
    *,
    plan: FiniteParameterPlan,
    payload: dict,
    info,
    native_manifest: dict,
) -> list[dict]:
    """Build concrete CppSim requests and expose current ABI exclusions."""
    expected_ports = [tuple(port) for port in payload.get("expected_ports", [])]
    core_ports = [
        tuple(port) for port in native_manifest.get("generated_core_ports", [])
    ]
    derived_names = list(payload.get("derived_parameter_names", []))
    public_text = (
        str(getattr(info, "ref_code", ""))
        + "\n"
        + str(getattr(info, "prompt_text", ""))
        + "\n"
        + "\n".join(
            str(content)
            for path, content in (
                (getattr(info, "metadata", {}) or {}).get("harness_files", {})
            ).items()
            if str(path).endswith(".py")
        )
    )

    requests = []
    for case in plan.cases:
        values = dict(case.values)
        reasons: list[str] = []
        for name in derived_names:
            value = _public_derived_parameter_value(
                parameter_name=name,
                case_values=values,
                public_text=public_text,
            )
            if value is None:
                reasons.append(
                    f"could not derive concrete CppSim value for parameter {name}"
                )
            else:
                values[name] = value

        for _, typ, port_name in [*expected_ports, *core_ports]:
            concrete_type = _specialize_sv_type(typ, values)
            width = _concrete_sv_width(concrete_type)
            if width is None:
                reasons.append(
                    f"could not specialize CppSim port {port_name} type {typ}"
                )
            elif width > 64:
                reasons.append(
                    f"CppSim behavioral ABI supports packed ports up to 64 bits; "
                    f"{port_name} specializes to {width} bits"
                )
        request = {"parameters": values}
        if reasons:
            request["unsupported_reason"] = "; ".join(dict.fromkeys(reasons))
        requests.append(request)
    return requests


def _module_body(sv_code: str, module_name: str) -> str:
    return _first_module_record(sv_code, module_name)[2]


def _adapter_internal_array_bridges(
    adapter: str,
    design_name: str,
) -> list[tuple[str, str, str]]:
    """Return public array name, generated name, and unpacked range."""
    records = []
    for match in re.finditer(
        rf"assign\s+([A-Za-z_]\w*)\[([^\]]+)\]\s*=\s*"
        rf"sparkle_dut\.([A-Za-z_]\w*)\[\2\]",
        adapter,
    ):
        records.append((match.group(1), match.group(3), match.group(2)))
    return records


def generate_cvdp_specialization_wrapper(
    *,
    design_name: str,
    plan: FiniteParameterPlan,
    modules: dict[str, str],
    expected_ports: list[tuple[str, str, str]],
    ref_code: str,
    harness_files: dict,
    derived_parameter_names: set[str] | None = None,
    reset_polarities: dict[str, str] | None = None,
    public_spec: str = "",
) -> tuple[str | None, dict]:
    """Build fixed adapters plus a parameter-only selector for a finite family."""
    metadata = plan.to_dict()
    metadata.update({
        "module_status": [],
        "selector_generated": False,
        "unsupported_policy": "elaboration_error",
    })
    if not plan.supported:
        metadata["error"] = "; ".join(plan.diagnostics)
        return None, metadata
    if not expected_ports:
        metadata["error"] = "public benchmark interface has no ports"
        return None, metadata

    missing = [name for name in plan.expected_modules if name not in modules]
    extra = sorted(set(modules) - set(plan.expected_modules))
    metadata["missing_modules"] = missing
    metadata["extra_modules"] = extra
    if missing:
        metadata["error"] = "missing concrete Sparkle modules: " + ", ".join(missing)
        return None, metadata

    adapter_blocks: list[str] = []
    adapter_ports_by_case: list[list[tuple[str, str, str]]] = []
    widths_by_port: dict[str, list[int]] = {name: [] for _, _, name in expected_ports}
    for index, case in enumerate(plan.cases):
        core_sv = modules[case.module_name]
        core_name, core_ports = parse_module_ports(core_sv, module_name=case.module_name)
        row = {
            "parameters": case.values,
            "module_name": case.module_name,
            "compile_pass": bool(core_name and core_ports),
        }
        if not core_name or not core_ports:
            row["error"] = "generated module or ports could not be parsed"
            metadata["module_status"].append(row)
            metadata["error"] = f"could not parse concrete module {case.module_name}"
            return None, metadata

        concrete_ports, port_diagnostics = _infer_concrete_public_ports(
            public_ports=expected_ports,
            values=case.values,
            core_ports=core_ports,
            core_sv=core_sv,
        )
        if concrete_ports is None:
            row["compile_pass"] = False
            row["error"] = "; ".join(port_diagnostics)
            metadata["module_status"].append(row)
            metadata["error"] = (
                f"specialization contract mismatch for {case.module_name}: "
                + row["error"]
            )
            return None, metadata
        adapter_name = f"{case.module_name}__cvdp_adapter"
        concrete_ref = (
            f"module {adapter_name} (\n"
            + ",\n".join(f"    {d} {t} {n}" for d, t, n in concrete_ports)
            + "\n);\nendmodule"
        )
        adapter = generate_cvdp_wrapper(
            design_name=adapter_name,
            sparkle_mod_name=core_name,
            sparkle_ports=core_ports,
            ref_code=concrete_ref,
            harness_files=harness_files,
            sv_code=core_sv,
            expose_parameters=False,
            expected_ports_override=concrete_ports,
            reset_polarities=reset_polarities,
            strict_mapping=True,
        )
        if not adapter:
            row["compile_pass"] = False
            row["error"] = "could not generate fixed-width CVDP adapter"
            metadata["module_status"].append(row)
            metadata["error"] = f"could not adapt concrete module {case.module_name}"
            return None, metadata
        row["adapter_name"] = adapter_name
        row["core_ports"] = core_ports
        row["adapter_ports"] = concrete_ports
        metadata["module_status"].append(row)
        adapter_blocks.append(adapter)
        adapter_ports_by_case.append(concrete_ports)
        for _, typ, name in concrete_ports:
            width = _concrete_sv_width(typ)
            if width is None:
                metadata["error"] = f"adapter port {name} is not concrete in {adapter_name}"
                return None, metadata
            widths_by_port[name].append(width)

    usage = _cvdp_parse_harness_usage(harness_files)
    defaults = plan.cases[0].values
    param_decls = [
        f"parameter integer {name} = {defaults[name]}" for name in plan.parameter_names
    ]
    localparam_decls: list[str] = []
    derived_values: dict[str, list[int]] = {}
    public_text = ref_code + "\n" + public_spec + "\n" + "\n".join(
        str(content) for path, content in harness_files.items()
        if str(path).endswith(".py")
    )
    for derived_name in sorted(derived_parameter_names or set()):
        values: list[int] = []
        for case_index, case in enumerate(plan.cases):
            public_value = _public_derived_parameter_value(
                parameter_name=derived_name,
                case_values=case.values,
                public_text=public_text,
            )
            candidates = []
            for _, public_type, port_name in expected_ports:
                inferred = _solve_derived_width_parameter(
                    typ=public_type,
                    parameter_name=derived_name,
                    case_values=case.values,
                    concrete_width=widths_by_port[port_name][case_index],
                )
                if inferred is not None:
                    candidates.append(inferred)
            inferred_value = candidates[0] if candidates and len(set(candidates)) == 1 else None
            if public_value is not None and inferred_value not in {None, public_value}:
                metadata["error"] = (
                    f"derived public parameter {derived_name}={public_value} conflicts "
                    f"with concrete module width inference {inferred_value} for {case.module_name}"
                )
                return None, metadata
            value = public_value if public_value is not None else inferred_value
            if value is None:
                metadata["error"] = (
                    f"could not infer derived public parameter {derived_name} "
                    f"for {case.module_name}"
                )
                return None, metadata
            values.append(value)
        derived_values[derived_name] = values
        localparam_decls.append(
            f"localparam integer {derived_name} = {_finite_case_expression(plan, values)}"
        )

    selector_ports = [
        (direction, _selector_port_type(plan, widths_by_port[name]), name)
        for direction, _, name in expected_ports
    ]

    lines = [f"module {design_name}", " #("]
    lines.append(",\n".join(f"    {decl}" for decl in param_decls))
    lines.extend([
        ")",
        " (",
        ",\n".join(f"    {d} {t} {n}" for d, t, n in selector_ports),
        ");",
        "",
        *(f"    {decl};" for decl in localparam_decls),
        "" if localparam_decls else "",
        "    // P0 finite specialization: selector only; behavior lives in Lean-generated cores.",
        "    generate",
    ])
    public_names = {name for _, _, name in expected_ports}
    for index, (case, adapter_ports) in enumerate(zip(plan.cases, adapter_ports_by_case)):
        keyword = "if" if index == 0 else "else if"
        condition = _specialization_condition(case.values)
        adapter_name = f"{case.module_name}__cvdp_adapter"
        lines.append(f"        {keyword} ({condition}) begin : p0_case_{index}")
        lines.append(f"            {adapter_name} impl (")
        connections = [
            f"                .{name}({name})"
            for _, _, name in adapter_ports
            if name in public_names
        ]
        lines.append(",\n".join(connections))
        lines.append("            );")
        lines.append("        end")
    lines.extend([
        "        else begin : p0_unsupported_parameter_combination",
        "            initial $error(\"Unsupported finite Sparkle parameter combination\");",
        "        end",
        "    endgenerate",
        "endmodule",
    ])

    metadata["selector_generated"] = True
    metadata["selector_ports"] = selector_ports
    metadata["parameter_declarations"] = param_decls
    metadata["localparam_declarations"] = localparam_decls
    metadata["derived_parameter_values"] = derived_values
    metadata["harness_parameters"] = sorted(usage["params"])
    full_code = "\n\n".join([
        *(modules[case.module_name] for case in plan.cases),
        *adapter_blocks,
        "\n".join(lines),
    ])
    return full_code, metadata


# ── Evaluator ────────────────────────────────────────────────────


class Evaluator:
    """Evaluate a Sparkle-generated .lean file: compile → extract SV → lint → sim → (synth+PPA) → (P&R+DRC+LVS)."""

    def __init__(
        self,
        project_root: Path = Path("."),
        enable_synth: bool = False,
        enable_pnr: bool = False,
        enable_drc: bool = False,
        enable_lvs: bool = False,
        enable_corners: bool = False,
        enable_gls: bool = False,
        lean_repl=None,
        dataset: str = "verilogeval",
        dataset_obj=None,
        parameter_formal_policy: str = "auto",
        parameter_cppsim_policy: str = "off",
        parameter_cppsim_required: bool = False,
        parameter_ppa_policy: str = "per_configuration",
        parameter_ppa_required: bool = False,
    ):
        self.project_root = project_root.resolve()
        self.dataset_name = dataset.lower()
        self.dataset_obj = dataset_obj  # Optional Dataset instance from dataset.py
        self.dataset_dir = self.project_root / "verilog-eval" / "dataset_spec-to-rtl"
        self.enable_pnr = enable_pnr or enable_drc or enable_lvs  # drc/lvs imply pnr
        self.enable_synth = enable_synth or self.enable_pnr  # pnr implies synth
        self.enable_gls = enable_gls and self.enable_synth  # gls requires synth
        self.enable_drc = enable_drc
        self.enable_lvs = enable_lvs
        self.enable_corners = enable_corners and self.enable_pnr  # corners require pnr
        self.lean_repl = lean_repl  # Optional LeanREPL instance for fast compilation
        self.parameter_formal_policy = parameter_formal_policy
        self.parameter_cppsim_policy = parameter_cppsim_policy
        self.parameter_cppsim_required = parameter_cppsim_required
        self.parameter_ppa_policy = parameter_ppa_policy
        self.parameter_ppa_required = parameter_ppa_required

    def evaluate(
        self,
        prob_id: str,
        run_dir: Path,
        problem_info=None,
    ) -> dict:
        """Run full evaluation for a single problem.

        Args:
            prob_id: e.g. "Prob001_zero"
            run_dir: directory for storing sim artifacts
            problem_info: optional already-configured dataset ProblemInfo. This
                carries run-local metadata such as a finite specialization
                plan; callers that omit it retain the historical reload path.

        Returns dict with keys:
            compile_pass, sv_extracted, lint_pass, sim_status, sim_mismatches, detail
        """
        result = {
            "prob_id": prob_id,
            "compile_pass": False,
            "sv_extracted": False,
            "lint_pass": False,
            "sim_status": "not_run",
            "sim_mismatches": -1,
            "synth_pass": False,
            "area_um2": None,
            "cell_count": None,
            "wns_ns": None,
            "power_uw": None,
            "pnr_pass": False,
            "gds_generated": False,
            "drc_pass": None,        # None = not run, True/False = result
            "drc_violations": None,
            "lvs_pass": None,        # None = not run, True/False = result
            "lvs_error": None,
            "gls_synth_status": "not_run",   # post-synth gate-level sim
            "gls_synth_mismatches": -1,
            "gls_pnr_status": "not_run",     # post-PnR gate-level sim
            "gls_pnr_mismatches": -1,
            "ppa_parameter_policy": "off",
            "ppa_status": "not_run",
            "ppa_coverage": "none",
            "ppa_family_covered": False,
            "has_sorry": True,       # True = unverified (default), False = formally verified
            "detail": "",
            "failure_stage": None,
            "diagnostics": [],
        }
        info = problem_info
        if info is None and self.dataset_obj is not None:
            info = self.dataset_obj.load_problem(prob_id)
        metadata = (getattr(info, "metadata", {}) or {}) if info is not None else {}
        plan_payload = (
            metadata.get("finite_parameter_plan")
            if info is not None else None
        )
        finite_plan = plan_from_dict(plan_payload)
        native_payload = metadata.get("native_parameter_sweep_plan")
        native_plan = native_plan_from_dict(native_payload)
        if finite_plan is not None and native_plan is not None:
            _record_failure(
                result,
                "parameter_contract",
                "finite specialization and native parameter sweep cannot be active together",
                code="conflicting_parameter_modes",
            )
            return result
        if finite_plan is not None:
            result["finite_parameter_specialization"] = True
            result["specialization_count"] = len(finite_plan.cases)
        if native_plan is not None:
            result["native_parameter_sweep"] = True
            result["native_parameter_sweep_case_count"] = len(native_plan.cases)

        # 1. Compile
        lean_file = self.project_root / "Generated" / f"{prob_id}.lean"
        if not lean_file.exists():
            _record_failure(
                result,
                "lean_elaboration",
                f"Lean file not found: Generated/{prob_id}.lean",
                code="lean_file_missing",
            )
            return result

        sv_code = None
        sv_modules: list[str] = []

        if self.lean_repl is not None:
            # ── Fast path: use persistent REPL (~0.1s) ──
            repl_result = self.lean_repl.check_file(lean_file)
            if not repl_result.passed:
                detail = f"Compile failed:\n{repl_result.error_text[:1000]}"
                _record_failure(
                    result,
                    _lean_diagnostic_stage(detail),
                    detail,
                    code="lean_compile_failed",
                )
                return result
            result["compile_pass"] = True
            result["has_sorry"] = not repl_result.complete
            sv_modules = [
                cleaned
                for block in getattr(repl_result, "verilog_modules", [])
                if (cleaned := _strip_verilog_info_block(block))
            ]
            if native_plan is not None:
                sv_code = "\n\n".join(sv_modules)
            else:
                sv_code = sv_modules[0] if sv_modules else _strip_verilog_info_block(repl_result.verilog or "")
        else:
            # ── Fallback: lake build (~10s) ──
            try:
                comp = subprocess.run(
                    ["lake", "build", f"Generated.{prob_id}"],
                    capture_output=True, text=True,
                    timeout=BASH_TIMEOUT,
                    cwd=str(self.project_root),
                )
            except subprocess.TimeoutExpired:
                _record_failure(
                    result,
                    "infrastructure",
                    "lake build timeout",
                    code="lean_build_timeout",
                )
                return result

            build_output = comp.stdout + "\n" + comp.stderr
            has_error = comp.returncode != 0 or re.search(r"error:", build_output)
            if has_error:
                detail = f"Compile failed:\n{build_output[:1000]}"
                _record_failure(
                    result,
                    _lean_diagnostic_stage(detail),
                    detail,
                    code="lean_compile_failed",
                )
                return result

            result["compile_pass"] = True
            result["has_sorry"] = bool(re.search(r"declaration uses `sorry`", build_output))
            sv_modules = self._extract_sv_modules(build_output)
            sv_code = "\n\n".join(sv_modules) if native_plan is not None else (sv_modules[0] if sv_modules else "")

        if finite_plan is not None:
            modules_by_name = {}
            for module_code in sv_modules:
                module_name = parse_module_name(module_code)
                if module_name:
                    modules_by_name[module_name] = module_code
            expected_ports = [
                tuple(port) for port in (plan_payload or {}).get("expected_ports", [])
            ]
            specialized_sv, manifest = generate_cvdp_specialization_wrapper(
                design_name=finite_plan.design_name,
                plan=finite_plan,
                modules=modules_by_name,
                expected_ports=expected_ports,
                ref_code=getattr(info, "ref_code", ""),
                harness_files=(getattr(info, "metadata", {}) or {}).get("harness_files", {}),
                derived_parameter_names=set(
                    (plan_payload or {}).get("derived_parameter_names", [])
                ),
                reset_polarities=dict(
                    (plan_payload or {}).get("reset_polarities", {})
                ),
                public_spec=getattr(info, "prompt_text", ""),
            )
            manifest_dir = run_dir / "specialization" / prob_id
            manifest_dir.mkdir(parents=True, exist_ok=True)
            (manifest_dir / "manifest.json").write_text(
                json.dumps(manifest, indent=2), encoding="utf-8"
            )
            result["specialization_manifest"] = str(manifest_dir / "manifest.json")
            result["specialization_compile_pass"] = bool(specialized_sv)
            result["specialization_modules_found"] = len(modules_by_name)
            if not specialized_sv:
                _record_failure(
                    result,
                    "parameter_contract",
                    "Finite specialization failed: " + str(manifest.get("error", "unknown error")),
                    code="finite_specialization_failed",
                )
                return result
            sv_code = specialized_sv

        native_manifest = None
        native_manifest_path = None
        if native_plan is not None:
            expected_ports = [
                tuple(port) for port in (native_payload or {}).get("expected_ports", [])
            ]
            native_sv, native_manifest = prepare_cvdp_native_parameter_design(
                sv_code=sv_code or "",
                design_name=native_plan.design_name,
                plan=native_plan,
                expected_ports=expected_ports,
                ref_code=getattr(info, "ref_code", ""),
                harness_files=metadata.get("harness_files", {}),
                derived_parameter_names=list(
                    (native_payload or {}).get("derived_parameter_names", [])
                ),
                reset_polarities=dict(
                    (native_payload or {}).get("reset_polarities", {})
                ),
            )
            manifest_dir = run_dir / "native_parameter_sweep" / prob_id
            manifest_dir.mkdir(parents=True, exist_ok=True)
            native_manifest_path = manifest_dir / "manifest.json"
            native_manifest_path.write_text(
                json.dumps(native_manifest, indent=2), encoding="utf-8"
            )
            result["native_parameter_manifest"] = str(native_manifest_path)
            result["native_parameter_contract_pass"] = bool(native_sv)
            if not native_sv:
                _record_failure(
                    result,
                    "parameter_contract",
                    "Native parameter contract failed: "
                    + str((native_manifest or {}).get("error", "unknown error")),
                    code="native_parameter_contract_failed",
                )
                return result
            sv_code = native_sv

            formal_manifest = evaluate_formal_parameter_policy(
                lean_source=lean_file.read_text(errors="replace"),
                lean_complete=not result["has_sorry"],
                plan=native_plan,
                requested_policy=self.parameter_formal_policy,
                contract=metadata.get("formal_parameter_contract"),
            )
            formal_dir = run_dir / "formal" / prob_id
            formal_dir.mkdir(parents=True, exist_ok=True)
            formal_manifest_path = formal_dir / "manifest.json"
            formal_manifest_path.write_text(
                json.dumps(formal_manifest, indent=2), encoding="utf-8"
            )
            result.update({
                "formal_parameter_manifest": str(formal_manifest_path),
                "formal_parameter_policy": formal_manifest["effective_policy"],
                "formal_status": formal_manifest["status"],
                "formal_coverage": formal_manifest["coverage"],
                "formal_family_covered": formal_manifest["family_covered"],
            })
            if formal_policy_is_required_failure(formal_manifest):
                detail = "Formal parameter policy failed: " + "; ".join(
                    formal_manifest.get("diagnostics", [])
                )
                _record_failure(
                    result,
                    "unsupported_backend",
                    detail,
                    code="formal_parameter_policy_failed",
                )
                return result

            cppsim_dir = run_dir / "cppsim" / prob_id
            cppsim_manifest = run_cppsim_parameter_policy(
                project_root=self.project_root,
                lean_file=lean_file,
                target_name=str(
                    metadata.get("cppsim_target")
                    or native_manifest.get("generated_core_module")
                    or native_plan.design_name
                ),
                case_requests=_cppsim_native_case_requests(
                    plan=native_plan,
                    payload=native_payload or {},
                    info=info,
                    native_manifest=native_manifest,
                ),
                output_dir=cppsim_dir,
                requested_policy=self.parameter_cppsim_policy,
                required=self.parameter_cppsim_required,
            )
            cppsim_manifest_path = cppsim_dir / "manifest.json"
            cppsim_manifest_path.write_text(
                json.dumps(cppsim_manifest, indent=2), encoding="utf-8"
            )
            result.update({
                "cppsim_parameter_manifest": str(cppsim_manifest_path),
                "cppsim_parameter_policy": cppsim_manifest["effective_policy"],
                "cppsim_status": cppsim_manifest["status"],
                "cppsim_coverage": cppsim_manifest["coverage"],
                "cppsim_family_covered": cppsim_manifest["family_covered"],
            })
            if cppsim_policy_is_required_failure(cppsim_manifest):
                detail = "CppSim parameter policy failed: " + "; ".join(
                    cppsim_manifest.get("diagnostics", [])
                )
                _record_failure(
                    result,
                    "unsupported_backend",
                    detail,
                    code="cppsim_parameter_policy_failed",
                )
                return result

        # 2. Extract SystemVerilog
        if not sv_code:
            _record_failure(
                result,
                "verilog_extraction",
                "Compiled but could not extract SystemVerilog",
                code="systemverilog_missing",
            )
            return result

        result["sv_extracted"] = True
        sv_dir = run_dir / "sv"
        sv_dir.mkdir(parents=True, exist_ok=True)
        sv_file = sv_dir / f"{prob_id}.sv"
        sv_file.write_text(sv_code)

        # 3. Lint
        result["lint_pass"] = self._run_lint(sv_file)

        if native_plan is not None:
            elaboration_pass, case_results, detail, stage = (
                self._run_native_parameter_elaboration(
                    sv_file=sv_file,
                    top_module=native_plan.design_name,
                    plan=native_plan,
                )
            )
            result["native_parameter_elaboration_pass"] = elaboration_pass
            result["native_parameter_case_results"] = case_results
            if native_manifest is not None:
                by_parameters = {
                    tuple(sorted(row["parameters"].items())): row
                    for row in case_results
                }
                for row in native_manifest.get("cases", []):
                    case_result = by_parameters.get(
                        tuple(sorted(row["parameters"].items()))
                    )
                    if case_result:
                        row.update(case_result)
                native_manifest["all_cases_elaborated"] = elaboration_pass
                if not elaboration_pass:
                    native_manifest["error"] = detail
                assert native_manifest_path is not None
                native_manifest_path.write_text(
                    json.dumps(native_manifest, indent=2), encoding="utf-8"
                )
            if not elaboration_pass:
                _record_failure(
                    result,
                    stage,
                    detail,
                    code="native_case_elaboration_failed",
                )
                return result

        # Parse module name (needed for sim wrapper and synthesis)
        if finite_plan is not None:
            sparkle_mod_name, sparkle_ports = parse_module_ports(
                sv_code, module_name=finite_plan.design_name
            )
        elif native_plan is not None:
            sparkle_mod_name, sparkle_ports = parse_module_ports(
                sv_code, module_name=native_plan.design_name
            )
        else:
            sparkle_mod_name, sparkle_ports = parse_module_ports(sv_code)

        # 4. Simulation
        if (finite_plan is not None or native_plan is not None) and self.dataset_name == "cvdp":
            sim_status, mismatches, detail = self._run_sim_cvdp(
                prob_id, sv_code, sparkle_mod_name, sparkle_ports, run_dir,
                direct_top=True,
                problem_info=info,
            )
        else:
            sim_status, mismatches, detail = self._run_sim(
                prob_id, sv_code, sparkle_mod_name, sparkle_ports, run_dir
            )
        result["sim_status"] = sim_status
        result["sim_mismatches"] = mismatches
        result["detail"] = detail
        failure_stage = _simulation_diagnostic_stage(sim_status, detail)
        if failure_stage is not None:
            _record_failure(
                result,
                failure_stage,
                detail,
                code=(
                    "simulation_mismatch"
                    if failure_stage == "simulation_mismatch"
                    else "simulation_error"
                ),
            )

        # 5. Synthesis + PPA (optional)
        if self.enable_synth and result["sv_extracted"]:
            if native_plan is not None:
                ppa_dir = run_dir / "ppa_parameter_family" / prob_id
                ppa_manifest = run_ppa_parameter_policy(
                    sv_code=sv_code,
                    top_module=sparkle_mod_name or native_plan.design_name,
                    prob_id=prob_id,
                    plan=native_plan,
                    output_dir=ppa_dir,
                    synth_runner=self._run_synthesis,
                    pnr_runner=self._run_pnr if self.enable_pnr else None,
                    requested_policy=self.parameter_ppa_policy,
                    required=self.parameter_ppa_required,
                    require_drc=self.enable_drc,
                    require_lvs=self.enable_lvs,
                )
                ppa_manifest_path = ppa_dir / "manifest.json"
                ppa_manifest_path.write_text(
                    json.dumps(ppa_manifest, indent=2), encoding="utf-8"
                )
                result.update({
                    "ppa_parameter_manifest": str(ppa_manifest_path),
                    "ppa_parameter_policy": ppa_manifest["effective_policy"],
                    "ppa_status": ppa_manifest["status"],
                    "ppa_coverage": ppa_manifest["coverage"],
                    "ppa_family_covered": ppa_manifest["family_covered"],
                    "synth_pass": ppa_manifest["synthesis_family_covered"],
                    "pnr_pass": ppa_manifest["pnr_family_covered"],
                    "ppa_case_results": ppa_manifest["cases"],
                    "ppa_metric_ranges": ppa_manifest["metric_ranges"],
                })
                if native_manifest is not None:
                    native_manifest["ppa_parameter_family"] = {
                        "manifest": str(ppa_manifest_path),
                        "status": ppa_manifest["status"],
                        "coverage": ppa_manifest["coverage"],
                        "family_covered": ppa_manifest["family_covered"],
                    }
                    assert native_manifest_path is not None
                    native_manifest_path.write_text(
                        json.dumps(native_manifest, indent=2), encoding="utf-8"
                    )
                if ppa_policy_is_required_failure(ppa_manifest):
                    detail = "PPA parameter policy failed: " + "; ".join(
                        str(item.get("message") if isinstance(item, dict) else item)
                        for item in ppa_manifest.get("diagnostics", [])
                    )
                    _record_failure(
                        result,
                        ppa_manifest_failure_stage(ppa_manifest),
                        detail,
                        code="ppa_parameter_policy_failed",
                    )
            else:
                synth_result = self._run_synthesis(
                    prob_id, sv_code, sparkle_mod_name or prob_id.lower(), run_dir
                )
                result.update(synth_result)

                # 5b. Post-synthesis gate-level simulation
                if self.enable_gls and result["synth_pass"]:
                    synth_dir = run_dir / "synth" / prob_id
                    gls_result = self._run_gls(
                        prob_id, sv_code, sparkle_mod_name, sparkle_ports,
                        run_dir, synth_dir, "post_synth",
                    )
                    result.update(gls_result)

                # 6. Full P&R + DRC + LVS (optional)
                if self.enable_pnr and result["synth_pass"]:
                    pnr_result = self._run_pnr(
                        prob_id, sv_code, sparkle_mod_name or prob_id.lower(), run_dir
                    )
                    result.update(pnr_result)

                    # 6b. Post-PnR gate-level simulation
                    if self.enable_gls and result["pnr_pass"]:
                        synth_dir = run_dir / "synth" / prob_id
                        gls_result = self._run_gls(
                            prob_id, sv_code, sparkle_mod_name, sparkle_ports,
                            run_dir, synth_dir, "post_pnr",
                        )
                        result.update(gls_result)

        return result

    @staticmethod
    def _extract_sv(build_output: str) -> str:
        """Extract SystemVerilog from lake build output, stripping info: prefix."""
        lines = build_output.splitlines()
        sv_lines = []
        capturing = False
        for line in lines:
            # Strip "info: Generated/Prob001_zero.lean:12:0: " prefix
            cleaned = re.sub(r"^info:\s+\S+\s+", "", line)
            if "// Generated by Sparkle HDL" in cleaned:
                capturing = True
            if capturing:
                sv_lines.append(cleaned)
            if capturing and cleaned.strip() == "endmodule":
                break
        return "\n".join(sv_lines) if sv_lines else ""

    @staticmethod
    def _extract_sv_modules(build_output: str) -> list[str]:
        modules: list[str] = []
        current: list[str] = []
        capturing = False
        for line in build_output.splitlines():
            cleaned = re.sub(r"^info:\s+\S+\s+", "", line)
            if "// Generated by Sparkle HDL" in cleaned:
                current = [cleaned]
                capturing = True
                continue
            if not capturing:
                continue
            current.append(cleaned)
            if cleaned.strip() == "endmodule":
                modules.append("\n".join(current))
                current = []
                capturing = False
        return modules

    @staticmethod
    def _run_native_parameter_elaboration(
        *,
        sv_file: Path,
        top_module: str,
        plan: FiniteParameterPlan,
    ) -> tuple[bool, list[dict], str, str]:
        """Elaborate every public CVDP configuration from the same SV file."""
        iverilog = shutil.which("iverilog")
        if iverilog is None:
            return (
                False,
                [],
                "iverilog is unavailable for native parameter elaboration",
                "infrastructure",
            )

        case_results: list[dict] = []
        for case in plan.cases:
            command = [
                iverilog,
                "-g2012",
                "-t", "null",
                "-s", top_module,
            ]
            command.extend(
                f"-P{top_module}.{name}={value}"
                for name, value in case.parameters
            )
            command.append(str(sv_file))
            row = {
                "parameters": case.values,
                "verilog_elaboration": "failed",
                "command_parameters": [
                    f"{name}={value}" for name, value in case.parameters
                ],
            }
            try:
                proc = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
            except subprocess.TimeoutExpired:
                row["detail"] = "iverilog elaboration timeout"
                case_results.append(row)
                return (
                    False,
                    case_results,
                    "native parameter elaboration timed out for "
                    + ", ".join(row["command_parameters"]),
                    "infrastructure",
                )
            except FileNotFoundError as exc:
                row["detail"] = str(exc)
                case_results.append(row)
                return False, case_results, str(exc), "infrastructure"

            output = "\n".join(
                part.strip() for part in (proc.stdout, proc.stderr) if part.strip()
            )
            row["returncode"] = proc.returncode
            if output:
                row["detail"] = output[-2000:]
            if proc.returncode != 0:
                case_results.append(row)
                values = ", ".join(row["command_parameters"])
                return (
                    False,
                    case_results,
                    f"native parameter elaboration failed for {values}:\n{output[-2000:]}",
                    "verilog_elaboration",
                )
            row["verilog_elaboration"] = "passed"
            case_results.append(row)

        return True, case_results, "all native parameter cases elaborated", "verilog_elaboration"

    @staticmethod
    def _run_lint(sv_file: Path) -> bool:
        """Run iverilog lint check."""
        command = ["iverilog", "-t", "null", "-g2012", str(sv_file)]
        if shutil.which("iverilog") is None:
            image = os.environ.get("OSS_SIM_IMAGE", "").strip()
            if not image or shutil.which("docker") is None:
                return False
            mounted_dir = sv_file.resolve().parent
            command = [
                "docker", "run", "--rm",
                "--entrypoint", "iverilog",
                "-v", f"{mounted_dir}:/work:ro",
                image,
                "-t", "null", "-g2012", f"/work/{sv_file.name}",
            ]
        try:
            comp = subprocess.run(
                command,
                capture_output=True, text=True, timeout=30,
            )
            return comp.returncode == 0 and not comp.stderr.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False

    def _run_sim(
        self, prob_id: str, sv_code: str,
        sparkle_mod_name: str, sparkle_ports: list[tuple[str, str, str]],
        run_dir: Path,
    ) -> tuple[str, int, str]:
        """Run simulation — dispatches to dataset-specific mode."""
        if self.dataset_name == "rtllm":
            return self._run_sim_rtllm(prob_id, sv_code, sparkle_mod_name, sparkle_ports, run_dir)
        if self.dataset_name == "cvdp":
            return self._run_sim_cvdp(prob_id, sv_code, sparkle_mod_name, sparkle_ports, run_dir)
        if self.dataset_name == "resbench":
            return self._run_sim_resbench(prob_id, sv_code, sparkle_mod_name, run_dir)
        if self.dataset_name == "realbench":
            return "not_run", -1, "RealBench functional evaluation is handled by the RealBench verifier"
        return self._run_sim_verilogeval(prob_id, sv_code, sparkle_mod_name, sparkle_ports, run_dir)

    def _run_sim_verilogeval(
        self, prob_id: str, sv_code: str,
        sparkle_mod_name: str, sparkle_ports: list[tuple[str, str, str]],
        run_dir: Path,
    ) -> tuple[str, int, str]:
        """Run iverilog simulation against VerilogEval testbench."""
        ref_sv_path = self.dataset_dir / f"{prob_id}_ref.sv"
        test_sv_path = self.dataset_dir / f"{prob_id}_test.sv"

        if not ref_sv_path.exists() or not test_sv_path.exists():
            return "sim_error", -1, f"Missing ref or test SV for {prob_id}"

        ref_sv = ref_sv_path.read_text()
        prompt_path = self.dataset_dir / f"{prob_id}_prompt.txt"
        prompt_text = prompt_path.read_text(errors="replace") if prompt_path.exists() else ""

        # Generate wrapper
        ref_ports = _parse_ref_module_ports(ref_sv) or parse_ref_ports(ref_sv)

        if not sparkle_mod_name:
            return "sim_error", -1, "Could not parse Sparkle module name"

        wrapper = generate_top_wrapper(
            sparkle_mod_name,
            sparkle_ports,
            ref_ports,
            sv_code,
            ref_code=ref_sv,
            prompt_text=prompt_text,
        )
        if not wrapper:
            return "sim_error", -1, "Could not generate TopModule wrapper"

        # Write sim files
        sim_dir = run_dir / "sim" / prob_id
        sim_dir.mkdir(parents=True, exist_ok=True)
        (sim_dir / "sparkle_dut.sv").write_text(sv_code)
        (sim_dir / "wrapper.sv").write_text(wrapper)
        tb_copy = sim_dir / "testbench.sv"
        tb_text = test_sv_path.read_text(errors="replace")
        # Some VerilogEval testbenches dump tb_mismatch before its declaration.
        # The signal is only for waveforms, and Icarus 13 rejects the forward reference.
        tb_text = re.sub(r"\btb_mismatch\s*,\s*", "", tb_text)
        tb_copy.write_text(tb_text)

        # Compile
        try:
            comp = subprocess.run(
                [
                    "iverilog", "-g2012", "-o", str(sim_dir / "sim.vvp"),
                    str(ref_sv_path),
                    str(sim_dir / "sparkle_dut.sv"),
                    str(sim_dir / "wrapper.sv"),
                    str(tb_copy),
                ],
                capture_output=True, text=True, timeout=30,
            )
            if comp.returncode != 0:
                (sim_dir / "compile_error.txt").write_text(comp.stderr)
                return "sim_error", -1, f"iverilog compile failed:\n{comp.stderr[:500]}"
        except subprocess.TimeoutExpired:
            return "sim_error", -1, "iverilog compile timeout"
        except FileNotFoundError:
            return "sim_error", -1, "iverilog not found"

        # Run simulation
        try:
            sim = subprocess.run(
                ["vvp", str(sim_dir / "sim.vvp")],
                capture_output=True, text=True, timeout=SIM_TIMEOUT,
            )
            sim_output = sim.stdout + sim.stderr
            (sim_dir / "sim_output.txt").write_text(sim_output)
        except subprocess.TimeoutExpired:
            return "sim_error", -1, "Simulation timeout"

        # Parse results
        m = re.search(r"Mismatches:\s*(\d+)\s+in\s+(\d+)\s+samples", sim_output)
        if m:
            mismatches = int(m.group(1))
            total = int(m.group(2))
            if mismatches == 0:
                return "sim_pass", 0, f"0 mismatches in {total} samples"
            else:
                return "sim_fail", mismatches, f"{mismatches} mismatches in {total} samples"

        if "TIMEOUT" in sim_output:
            return "sim_error", -1, "Simulation hit internal timeout"

        return "sim_error", -1, f"Could not parse sim output:\n{sim_output[:300]}"

    def _run_sim_rtllm(
        self, prob_id: str, sv_code: str,
        sparkle_mod_name: str, sparkle_ports: list[tuple[str, str, str]],
        run_dir: Path,
    ) -> tuple[str, int, str]:
        """Run iverilog simulation against RTLLM testbench.

        RTLLM testbenches instantiate the design by its name (e.g. adder_8bit uut(...)).
        We generate a wrapper module with the design name that instantiates the Sparkle module.
        Pass is detected by 'Your Design Passed' in output.
        """
        if self.dataset_obj is None:
            return "sim_error", -1, "RTLLM dataset object not set on evaluator"

        info = self.dataset_obj.load_problem(prob_id)
        tb_path = info.testbench_path
        design_name = info.design_name

        if not tb_path.exists():
            return "sim_error", -1, f"Testbench not found: {tb_path}"

        if not sparkle_mod_name:
            return "sim_error", -1, "Could not parse Sparkle module name"

        sim_dir = run_dir / "sim" / prob_id
        sim_dir.mkdir(parents=True, exist_ok=True)

        # Rename Sparkle module if it collides with design_name
        # (wrapper must use design_name, so the inner module needs a distinct name)
        if sparkle_mod_name == design_name:
            renamed = f"{sparkle_mod_name}_sparkle"
            sv_code = re.sub(
                rf'\bmodule\s+{re.escape(sparkle_mod_name)}\b',
                f'module {renamed}',
                sv_code,
                count=1,
            )
            sparkle_mod_name = renamed

        (sim_dir / "sparkle_dut.sv").write_text(sv_code)

        # Generate wrapper: module <design_name>(...); sparkle_mod dut(...); endmodule
        # Parse testbench to extract expected ports from the DUT instantiation
        tb_code = tb_path.read_text()
        wrapper = self._generate_rtllm_wrapper(
            design_name, sparkle_mod_name, sparkle_ports, tb_code, sv_code,
            info.ref_code,
        )
        if not wrapper:
            return "sim_error", -1, "Could not generate RTLLM wrapper"

        compile_files = [str(sim_dir / "sparkle_dut.sv")]
        if wrapper:
            (sim_dir / "wrapper.sv").write_text(wrapper)
            compile_files.append(str(sim_dir / "wrapper.sv"))
        compile_files.append(str(tb_path))

        # Compile
        try:
            comp = subprocess.run(
                ["iverilog", "-g2012", "-o", str(sim_dir / "sim.vvp")] + compile_files,
                capture_output=True, text=True, timeout=30,
            )
            if comp.returncode != 0:
                (sim_dir / "compile_error.txt").write_text(comp.stderr)
                return "sim_error", -1, f"iverilog compile failed:\n{comp.stderr[:500]}"
        except subprocess.TimeoutExpired:
            return "sim_error", -1, "iverilog compile timeout"
        except FileNotFoundError:
            return "sim_error", -1, "iverilog not found"

        # Copy data files needed by $readmemh in testbenches (e.g. wfull.txt)
        tb_dir = tb_path.parent
        for data_file in tb_dir.iterdir():
            if data_file.is_file() and data_file.suffix in (".txt", ".dat", ".hex", ".mem"):
                dest = sim_dir / data_file.name
                if not dest.exists():
                    shutil.copy2(data_file, dest)

        # Run simulation (cwd=sim_dir so $readmemh finds copied data files)
        try:
            sim = subprocess.run(
                ["vvp", str(sim_dir / "sim.vvp")],
                capture_output=True, text=True, timeout=SIM_TIMEOUT,
                cwd=str(sim_dir),
            )
            sim_output = sim.stdout + sim.stderr
            (sim_dir / "sim_output.txt").write_text(sim_output)
        except subprocess.TimeoutExpired:
            return "sim_error", -1, "Simulation timeout"

        # Parse results — RTLLM uses "Your Design Passed"
        if RTLLM_PASS_PATTERN.search(sim_output):
            return "sim_pass", 0, "Your Design Passed"

        # Check for failure pattern
        fail_m = re.search(r"(\d+)\s*/\s*\d+\s*failures", sim_output)
        if fail_m:
            failures = int(fail_m.group(1))
            return "sim_fail", failures, f"{failures} failures"

        if "TIMEOUT" in sim_output:
            return "sim_error", -1, "Simulation hit internal timeout"

        return "sim_fail", -1, f"Design did not pass:\n{sim_output[:300]}"

    def _run_sim_cvdp(
        self, prob_id: str, sv_code: str,
        sparkle_mod_name: str,
        sparkle_ports: list[tuple[str, str, str]],
        run_dir: Path,
        *,
        direct_top: bool = False,
        problem_info=None,
    ) -> tuple[str, int, str]:
        """Run a CVDP cocotb harness in its Docker simulation image."""
        if self.dataset_obj is None:
            return "sim_error", -1, "CVDP dataset object not set on evaluator"

        info = problem_info or self.dataset_obj.load_problem(prob_id)
        harness_files = info.metadata.get("harness_files", {})
        verilog_sources = info.metadata.get("verilog_sources", [])
        design_name = info.design_name
        if not harness_files:
            return "sim_error", -1, "CVDP harness files missing"
        if not verilog_sources:
            return "sim_error", -1, "CVDP VERILOG_SOURCES missing"

        sim_dir = run_dir / "cvdp_sim" / prob_id
        if sim_dir.exists():
            shutil.rmtree(sim_dir)
        sim_dir.mkdir(parents=True, exist_ok=True)

        image_override = os.environ.get("OSS_SIM_IMAGE", "").strip()
        image_name = image_override or "nvidia/cvdp-sim:v1.0.0"
        for rel_path, content in harness_files.items():
            out_path = sim_dir / rel_path
            out_path.parent.mkdir(parents=True, exist_ok=True)
            if rel_path == "docker-compose.yml":
                content = str(content).replace("__OSS_SIM_IMAGE__", image_name)
                if image_override:
                    content = re.sub(
                        r"(?m)^(\s*image\s*:\s*)nvidia/cvdp-sim:v1\.0\.0\s*$",
                        lambda match: f"{match.group(1)}{image_name}",
                        content,
                    )
            out_path.write_text(str(content))

        target_mod_name, target_ports = parse_module_ports(sv_code, module_name=design_name)
        if not (direct_top and target_mod_name == design_name):
            if target_mod_name == design_name:
                sparkle_mod_name, sparkle_ports = target_mod_name, target_ports
            elif not sparkle_mod_name:
                sparkle_mod_name, sparkle_ports = parse_module_ports(sv_code)

            if sparkle_mod_name:
                inner_name = sparkle_mod_name
                if sparkle_mod_name == design_name:
                    inner_name = f"{design_name}_sparkle_inner"
                    sv_code = _rename_module_declaration(sv_code, sparkle_mod_name, inner_name)

                wrapper = generate_cvdp_wrapper(
                    design_name=design_name,
                    sparkle_mod_name=inner_name,
                    sparkle_ports=sparkle_ports,
                    ref_code=info.ref_code,
                    harness_files=harness_files,
                    sv_code=sv_code,
                )
                if wrapper:
                    sv_code = f"{sv_code.rstrip()}\n\n{wrapper}\n"
                elif sparkle_mod_name != design_name:
                    sv_code = _rename_module_declaration(sv_code, sparkle_mod_name, design_name)

        def local_source_path(source: str) -> Path:
            if source.startswith("/code/"):
                return sim_dir / source[len("/code/"):]
            return sim_dir / source.lstrip("/")

        primary = next(
            (s for s in verilog_sources if design_name in Path(s).stem),
            verilog_sources[0],
        )
        context_files = info.metadata.get("input_context_files", {})
        for source in verilog_sources:
            out_path = local_source_path(source)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            rel_source = str(out_path.relative_to(sim_dir))
            context_code = str(context_files.get(rel_source, ""))
            out_path.write_text(sv_code if source == primary else context_code)

        sim_mode = os.environ.get("CVDP_SIM_MODE", "local").lower()
        if sim_mode != "docker":
            return self._run_sim_cvdp_local(sim_dir)

        compose_path = sim_dir / "docker-compose.yml"
        compose_text = compose_path.read_text(errors="replace")
        required_mounts = {
            str(local_source_path(source).parent.relative_to(sim_dir))
            for source in verilog_sources
        }
        mount_lines = []
        for rel_dir in sorted(required_mounts):
            container_dir = "/code/" + rel_dir.strip("/")
            mount_lines.append(f"      - ./{rel_dir}/:{container_dir}/:ro")
        if mount_lines and "volumes:" in compose_text:
            first_volume = re.search(r"(?m)^(\s*volumes\s*:\s*)$", compose_text)
            if first_volume:
                insert_at = first_volume.end()
                compose_text = (
                    compose_text[:insert_at]
                    + "\n"
                    + "\n".join(mount_lines)
                    + compose_text[insert_at:]
                )
                compose_path.write_text(compose_text)

        compose = ["docker", "compose"]
        try:
            check = subprocess.run(
                compose + ["version"],
                capture_output=True, text=True, timeout=20,
            )
            if check.returncode != 0:
                compose = ["docker-compose"]
        except (FileNotFoundError, subprocess.TimeoutExpired):
            compose = ["docker-compose"]

        service = "direct"
        compose_text = compose_path.read_text(errors="replace")
        service_match = re.search(r"^\s{2}([A-Za-z0-9_-]+):\s*$", compose_text, re.MULTILINE)
        if service_match:
            service = service_match.group(1)

        proc = None
        timed_out = False
        run_error = None
        cleanup_error = None
        output = ""
        cleanup = None
        try:
            proc = subprocess.run(
                compose + [
                    "-f", "docker-compose.yml",
                    "up", "--abort-on-container-exit",
                    "--exit-code-from", service,
                ],
                cwd=str(sim_dir),
                capture_output=True, text=True, timeout=180,
            )
            output = (proc.stdout or "") + (proc.stderr or "")
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            for stream in (exc.stdout, exc.stderr):
                if isinstance(stream, bytes):
                    output += stream.decode(errors="replace")
                elif stream:
                    output += str(stream)
        except FileNotFoundError as exc:
            run_error = f"Docker compose not found: {exc}"
        finally:
            try:
                cleanup = subprocess.run(
                    compose + [
                        "-f", "docker-compose.yml", "down", "-v",
                        "--remove-orphans",
                    ],
                    cwd=str(sim_dir),
                    capture_output=True, text=True, timeout=60,
                )
            except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
                cleanup_error = f"CVDP docker cleanup failed: {exc}"

        if cleanup is not None:
            cleanup_output = (cleanup.stdout or "") + (cleanup.stderr or "")
            if cleanup_output:
                output += "\n[CLEANUP]\n" + cleanup_output
        (sim_dir / "cvdp_output.txt").write_text(output)
        if run_error is not None:
            return "sim_error", -1, run_error
        if timed_out:
            detail = "CVDP docker simulation timeout"
            if cleanup_error:
                detail += f"; {cleanup_error}"
            return "sim_error", -1, detail
        if cleanup_error is not None:
            return "sim_error", -1, cleanup_error
        assert proc is not None
        tail = "\n".join(output.splitlines()[-40:])
        if proc.returncode == 0:
            return "sim_pass", 0, "CVDP harness passed"
        if re.search(r"(AssertionError|assert .*failed|FAILED|failed)", output, re.IGNORECASE):
            return "sim_fail", -1, f"CVDP harness failed:\n{tail[:1200]}"
        return "sim_error", -1, f"CVDP harness error:\n{tail[:1200]}"

    def _run_sim_cvdp_local(self, sim_dir: Path) -> tuple[str, int, str]:
        """Run a CVDP harness directly with local pytest/cocotb and Icarus."""
        env_file = sim_dir / "src" / ".env"
        test_runner = sim_dir / "src" / "test_runner.py"
        if not env_file.exists() or not test_runner.exists():
            return "sim_error", -1, "CVDP local harness missing src/.env or src/test_runner.py"

        env = os.environ.copy()
        for raw_line in env_file.read_text(errors="replace").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().replace("/code/", f"{sim_dir}/")
            env[key] = value
        env["PYTHONPATH"] = str(sim_dir / "src") + os.pathsep + env.get("PYTHONPATH", "")

        rundir = sim_dir / "rundir"
        rundir.mkdir(parents=True, exist_ok=True)
        cache_dir = sim_dir / "harness" / ".cache"
        timeout_s = int(os.environ.get("CVDP_LOCAL_TIMEOUT", "180"))
        cmd = [
            sys.executable, "-m", "pytest", "-s",
            "-o", f"cache_dir={cache_dir}",
            str(test_runner),
        ]
        proc = subprocess.Popen(
            cmd,
            cwd=str(rundir),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        try:
            stdout, stderr = proc.communicate(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            stdout, stderr = proc.communicate()
            output = stdout + stderr
            (sim_dir / "cvdp_local_output.txt").write_text(output)
            return "sim_error", -1, f"CVDP local simulation timeout after {timeout_s}s"

        output = stdout + stderr
        (sim_dir / "cvdp_local_output.txt").write_text(output)
        tail = "\n".join(output.splitlines()[-40:])
        if proc.returncode == 0:
            return "sim_pass", 0, "CVDP local harness passed"
        if re.search(r"(AssertionError|assert .*failed|FAILED|failed)", output, re.IGNORECASE):
            return "sim_fail", -1, f"CVDP local harness failed:\n{tail[:1200]}"
        return "sim_error", -1, f"CVDP local harness error:\n{tail[:1200]}"

    def _run_sim_resbench(
        self, prob_id: str, sv_code: str,
        sparkle_mod_name: str, run_dir: Path,
    ) -> tuple[str, int, str]:
        """Run ResBench's embedded Icarus testbench."""
        if self.dataset_obj is None:
            return "sim_error", -1, "ResBench dataset object not set on evaluator"
        info = self.dataset_obj.load_problem(prob_id)
        testbench = info.metadata.get("testbench", "")
        if not testbench:
            return "sim_error", -1, "ResBench testbench missing"
        if sparkle_mod_name and sparkle_mod_name != info.design_name:
            sv_code = re.sub(
                rf"\bmodule\s+{re.escape(sparkle_mod_name)}\b",
                f"module {info.design_name}",
                sv_code,
                count=1,
            )

        sim_dir = run_dir / "sim" / prob_id
        sim_dir.mkdir(parents=True, exist_ok=True)
        dut_path = sim_dir / "dut.sv"
        tb_path = sim_dir / "testbench.sv"
        vvp_path = sim_dir / "sim.vvp"
        dut_path.write_text(sv_code)
        tb_path.write_text(testbench)

        try:
            comp = subprocess.run(
                ["iverilog", "-g2012", "-o", str(vvp_path), str(dut_path), str(tb_path)],
                capture_output=True, text=True, timeout=60,
            )
        except subprocess.TimeoutExpired:
            return "sim_error", -1, "ResBench iverilog timeout"
        except FileNotFoundError:
            return "sim_error", -1, "iverilog not found"
        if comp.returncode != 0:
            (sim_dir / "compile_error.txt").write_text(comp.stderr)
            return "sim_error", -1, f"iverilog compile failed:\n{comp.stderr[:800]}"

        try:
            sim = subprocess.run(
                ["vvp", str(vvp_path)],
                capture_output=True, text=True, timeout=SIM_TIMEOUT,
                cwd=str(sim_dir),
            )
        except subprocess.TimeoutExpired:
            return "sim_error", -1, "ResBench simulation timeout"
        output = sim.stdout + sim.stderr
        (sim_dir / "sim_output.txt").write_text(output)
        if RESBENCH_PASS_PATTERN.search(output):
            return "sim_pass", 0, "All tests passed"
        if "Some tests failed" in output or "FAIL" in output:
            return "sim_fail", -1, output[-800:]
        return "sim_error", -1, f"Could not parse ResBench output:\n{output[-800:]}"

    # ── Gate-Level Simulation (GLS) ──────────────────────────────────

    @staticmethod
    def _ensure_sky130_cache() -> Path:
        """Ensure sky130 cell simulation models are available via volare.

        Returns the directory containing primitives.v and sky130_fd_sc_hd.v.
        """
        verilog_dir = SKY130_VOLARE_VERILOG_DIR
        prim = verilog_dir / SKY130_PRIMITIVES_NAME
        cells = verilog_dir / SKY130_CELLS_NAME
        if prim.exists() and cells.exists():
            return verilog_dir
        # Try to fetch via volare
        try:
            subprocess.run(
                ["volare", "fetch", "--pdk", "sky130", SKY130_VOLARE_VERSION],
                capture_output=True, text=True, timeout=300,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        if not prim.exists() or not cells.exists():
            raise RuntimeError(
                f"sky130 cell models not found at {verilog_dir}. "
                f"Install with: pip install volare && volare fetch --pdk sky130 {SKY130_VOLARE_VERSION}"
            )
        return verilog_dir

    def _run_gls(
        self, prob_id: str, sv_code: str,
        sparkle_mod_name: str, sparkle_ports: list[tuple[str, str, str]],
        run_dir: Path, synth_dir: Path, stage: str,
    ) -> dict:
        """Run gate-level simulation after synthesis or P&R.

        Args:
            stage: "post_synth" or "post_pnr"
        Returns dict with gls_{stage}_status and gls_{stage}_mismatches.
        """
        if self.dataset_name in {"cvdp", "realbench"}:
            key = "synth" if stage == "post_synth" else "pnr"
            return {
                f"gls_{key}_status": "not_run",
                f"gls_{key}_mismatches": -1,
            }

        # Locate netlist on local filesystem
        # ORFS naming: post-synth = 1_*_yosys.v, post-PnR = 6_1_merged.v or 6_final.v
        if stage == "post_synth":
            netlist_glob = "1_*_yosys.v"
        else:
            netlist_glob = "6_1_merged.v"
        netlists = list((synth_dir / "orfs_results").rglob(netlist_glob))
        if not netlists and stage == "post_pnr":
            # Fallback: try 6_final.v
            netlists = list((synth_dir / "orfs_results").rglob("6_final.v"))
        if not netlists:
            key = "synth" if stage == "post_synth" else "pnr"
            return {
                f"gls_{key}_status": "sim_error",
                f"gls_{key}_mismatches": -1,
            }
        netlist_path = netlists[0]

        if self.dataset_name in {"rtllm", "resbench"}:
            status, mismatches, detail = self._run_gls_rtllm(
                prob_id, sv_code, sparkle_mod_name, sparkle_ports,
                run_dir, synth_dir, netlist_path, stage,
            )
        else:
            status, mismatches, detail = self._run_gls_verilogeval(
                prob_id, sv_code, sparkle_mod_name, sparkle_ports,
                run_dir, synth_dir, netlist_path, stage,
            )

        key = "synth" if stage == "post_synth" else "pnr"
        return {
            f"gls_{key}_status": status,
            f"gls_{key}_mismatches": mismatches,
        }

    def _run_gls_verilogeval(
        self, prob_id: str, sv_code: str,
        sparkle_mod_name: str, sparkle_ports: list[tuple[str, str, str]],
        run_dir: Path, synth_dir: Path,
        netlist_path: Path, stage: str,
    ) -> tuple[str, int, str]:
        """Run gate-level simulation for VerilogEval using local iverilog."""
        sky130_dir = self._ensure_sky130_cache()

        ref_sv_path = self.dataset_dir / f"{prob_id}_ref.sv"
        test_sv_path = self.dataset_dir / f"{prob_id}_test.sv"
        if not ref_sv_path.exists() or not test_sv_path.exists():
            return "sim_error", -1, f"Missing ref or test SV for {prob_id}"

        # Regenerate wrapper (same as RTL sim)
        ref_sv = ref_sv_path.read_text()
        prompt_path = self.dataset_dir / f"{prob_id}_prompt.txt"
        prompt_text = prompt_path.read_text(errors="replace") if prompt_path.exists() else ""
        ref_ports = _parse_ref_module_ports(ref_sv) or parse_ref_ports(ref_sv)
        if not sparkle_mod_name:
            return "sim_error", -1, "Could not parse Sparkle module name"
        wrapper = generate_top_wrapper(
            sparkle_mod_name,
            sparkle_ports,
            ref_ports,
            sv_code,
            ref_code=ref_sv,
            prompt_text=prompt_text,
        )
        if not wrapper:
            return "sim_error", -1, "Could not generate TopModule wrapper"

        # Stage files
        gls_dir = synth_dir / f"gls_{stage}"
        gls_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ref_sv_path, gls_dir / "ref.sv")
        shutil.copy2(test_sv_path, gls_dir / "test.sv")
        (gls_dir / "wrapper.sv").write_text(wrapper)

        # Compile with local iverilog
        vvp_file = gls_dir / "gls.vvp"
        compile_cmd = [
            "iverilog", "-g2012", "-DFUNCTIONAL", "-DUNIT_DELAY=#0",
            "-o", str(vvp_file),
            str(sky130_dir / SKY130_PRIMITIVES_NAME),
            str(sky130_dir / SKY130_CELLS_NAME),
            str(netlist_path),
            str(gls_dir / "ref.sv"),
            str(gls_dir / "wrapper.sv"),
            str(gls_dir / "test.sv"),
        ]

        try:
            comp = subprocess.run(
                compile_cmd, capture_output=True, text=True, timeout=GLS_TIMEOUT,
            )
            if comp.returncode != 0:
                (gls_dir / "gls_compile_error.txt").write_text(comp.stderr)
                return "sim_error", -1, f"GLS compile failed:\n{comp.stderr[:500]}"

            sim = subprocess.run(
                ["vvp", str(vvp_file)],
                capture_output=True, text=True, timeout=GLS_TIMEOUT,
                cwd=str(gls_dir),
            )
        except subprocess.TimeoutExpired:
            return "sim_error", -1, f"GLS {stage}: simulation timeout"
        except Exception as e:
            return "sim_error", -1, f"GLS error: {e}"

        sim_output = sim.stdout + sim.stderr
        (gls_dir / "gls_output.txt").write_text(sim_output)

        # Parse VerilogEval output
        m = re.search(r"Mismatches:\s*(\d+)\s+in\s+(\d+)\s+samples", sim_output)
        if m:
            mismatches = int(m.group(1))
            total = int(m.group(2))
            if mismatches == 0:
                return "sim_pass", 0, f"GLS {stage}: 0 mismatches in {total} samples"
            return "sim_fail", mismatches, f"GLS {stage}: {mismatches} mismatches in {total} samples"

        if "TIMEOUT" in sim_output:
            return "sim_error", -1, f"GLS {stage}: simulation timeout"

        return "sim_error", -1, f"GLS {stage}: could not parse output:\n{sim_output[:300]}"

    def _run_gls_rtllm(
        self, prob_id: str, sv_code: str,
        sparkle_mod_name: str, sparkle_ports: list[tuple[str, str, str]],
        run_dir: Path, synth_dir: Path,
        netlist_path: Path, stage: str,
    ) -> tuple[str, int, str]:
        """Run gate-level simulation for RTLLM using local iverilog."""
        sky130_dir = self._ensure_sky130_cache()

        if self.dataset_obj is None:
            return "sim_error", -1, "RTLLM dataset object not set on evaluator"

        info = self.dataset_obj.load_problem(prob_id)
        tb_path = info.testbench_path
        design_name = info.design_name

        if not tb_path.exists():
            return "sim_error", -1, f"Testbench not found: {tb_path}"

        # Stage files
        gls_dir = synth_dir / f"gls_{stage}"
        gls_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(tb_path, gls_dir / "testbench.v")

        # Copy data files for $readmemh
        tb_dir = tb_path.parent
        for data_file in tb_dir.iterdir():
            if data_file.is_file() and data_file.suffix in (".txt", ".dat", ".hex", ".mem"):
                dest = gls_dir / data_file.name
                if not dest.exists():
                    shutil.copy2(data_file, dest)

        # Build compile file list
        compile_files = [str(netlist_path)]

        if sparkle_mod_name != design_name:
            tb_code = tb_path.read_text()
            wrapper = self._generate_rtllm_wrapper(
                design_name, sparkle_mod_name, sparkle_ports,
                tb_code, sv_code, info.ref_code,
            )
            if not wrapper:
                return "sim_error", -1, "Could not generate RTLLM GLS wrapper"
            (gls_dir / "wrapper.sv").write_text(wrapper)
            compile_files.append(str(gls_dir / "wrapper.sv"))

        compile_files.append(str(gls_dir / "testbench.v"))

        # Compile with local iverilog
        vvp_file = gls_dir / "gls.vvp"
        compile_cmd = [
            "iverilog", "-g2012", "-DFUNCTIONAL", "-DUNIT_DELAY=#0",
            "-o", str(vvp_file),
            str(sky130_dir / SKY130_PRIMITIVES_NAME),
            str(sky130_dir / SKY130_CELLS_NAME),
        ] + compile_files

        try:
            comp = subprocess.run(
                compile_cmd, capture_output=True, text=True, timeout=GLS_TIMEOUT,
            )
            if comp.returncode != 0:
                (gls_dir / "gls_compile_error.txt").write_text(comp.stderr)
                return "sim_error", -1, f"GLS compile failed:\n{comp.stderr[:500]}"

            sim = subprocess.run(
                ["vvp", str(vvp_file)],
                capture_output=True, text=True, timeout=GLS_TIMEOUT,
                cwd=str(gls_dir),
            )
        except subprocess.TimeoutExpired:
            return "sim_error", -1, f"GLS {stage}: simulation timeout"
        except Exception as e:
            return "sim_error", -1, f"GLS error: {e}"

        sim_output = sim.stdout + sim.stderr
        (gls_dir / "gls_output.txt").write_text(sim_output)

        # Parse RTLLM/ResBench output
        if RTLLM_PASS_PATTERN.search(sim_output) or (
            self.dataset_name == "resbench" and RESBENCH_PASS_PATTERN.search(sim_output)
        ):
            return "sim_pass", 0, f"GLS {stage}: Design passed"

        fail_m = re.search(r"(\d+)\s*/\s*\d+\s*failures", sim_output)
        if fail_m:
            failures = int(fail_m.group(1))
            return "sim_fail", failures, f"GLS {stage}: {failures} failures"

        if "TIMEOUT" in sim_output:
            return "sim_error", -1, f"GLS {stage}: simulation timeout"

        return "sim_fail", -1, f"GLS {stage}: Design did not pass:\n{sim_output[:300]}"

    @staticmethod
    def _generate_rtllm_wrapper(
        design_name: str,
        sparkle_mod_name: str,
        sparkle_ports: list[tuple[str, str, str]],
        tb_code: str,
        sv_code: str = "",
        ref_code: str = "",
    ) -> str | None:
        """Generate a wrapper module named <design_name> that instantiates the Sparkle module.

        Parses the testbench to find the DUT instantiation and extract expected ports.
        Handles bundled outputs (Sparkle packs multiple outputs into one port).
        """
        # Parse testbench DUT instantiation to get expected port connections
        # Handles optional #(params) with one level of nested parens
        inst_pattern = re.compile(
            rf"{re.escape(design_name)}\s+(?:#\s*\((?:[^()]*|\([^()]*\))*\)\s*)?\w+\s*\(([^;]+)\)\s*;",
            re.DOTALL,
        )
        inst_match = inst_pattern.search(tb_code)
        if not inst_match:
            return None

        inst_body = inst_match.group(1)
        tb_ports = re.findall(r"\.(\w+)\s*\(", inst_body)

        # Fallback for positional connections: use reference design port names
        ref_port_lookup: dict[str, tuple[str, str]] = {}
        ref_ordered_for_reset = _parse_ref_module_ports(ref_code) if ref_code else None
        if not tb_ports and ref_code:
            ref_ordered = ref_ordered_for_reset
            if ref_ordered:
                tb_ports = [name for _, _, name in ref_ordered]
                ref_port_lookup = {name: (d, w) for d, w, name in ref_ordered}

        if not tb_ports:
            return None

        sp_port_map = {n: (d, t) for d, t, n in sparkle_ports}
        sp_port_map_lower = {n.lower(): n for n in sp_port_map}  # for case-insensitive fallback
        sp_outputs = [(d, t, n) for d, t, n in sparkle_ports if d == "output"]

        # Classify each testbench port: match to Sparkle port or infer from TB
        tb_classified = []  # (pname, direction, width_str, sparkle_name_or_None)
        for pname in tb_ports:
            sp_match = None
            if pname in sp_port_map:
                sp_match = pname
            else:
                gen_name = f"_gen_{pname}"
                if gen_name in sp_port_map:
                    sp_match = gen_name
                else:
                    alias_match = next((sn for sn in sp_port_map if _ports_equivalent(sn, pname)), None)
                    if alias_match:
                        sp_match = alias_match
                if sp_match is None:
                    # Case-insensitive fallback
                    pl = pname.lower()
                    gen_lower = f"_gen_{pl}"
                    if pl in sp_port_map_lower:
                        sp_match = sp_port_map_lower[pl]
                    elif gen_lower in sp_port_map_lower:
                        sp_match = sp_port_map_lower[gen_lower]

            if sp_match:
                d, t = sp_port_map[sp_match]
                wm = re.search(r"\[(\d+):(\d+)\]", t)
                width = f" [{wm.group(1)}:{wm.group(2)}]" if wm else ""
                tb_classified.append((pname, d, width, sp_match))
            else:
                if ref_port_lookup and pname in ref_port_lookup:
                    direction, width = ref_port_lookup[pname]
                else:
                    tb_decl = re.search(
                        rf"(reg|wire)\s*(\[\d+:\d+\])?\s*{re.escape(pname)}\s*;",
                        tb_code,
                    )
                    if tb_decl:
                        kind = tb_decl.group(1)
                        width = f" {tb_decl.group(2)}" if tb_decl.group(2) else ""
                        direction = "input" if kind == "reg" else "output"
                    else:
                        direction, width = "input", ""
                tb_classified.append((pname, direction, width, None))

        # Detect bundled output: unmatched TB outputs whose total width == single Sparkle output
        unmatched_outs = [(p, w) for p, d, w, m in tb_classified if d == "output" and m is None]
        bundled_sp = None
        if unmatched_outs and len(sp_outputs) == 1:
            sp_out_d, sp_out_t, sp_out_n = sp_outputs[0]
            sp_w = _port_width(sp_out_t)
            tb_total = 0
            for _, w in unmatched_outs:
                wm = re.search(r"\[(\d+):(\d+)\]", w)
                tb_total += (abs(int(wm.group(1)) - int(wm.group(2))) + 1) if wm else 1
            if sp_w == tb_total:
                bundled_sp = (sp_out_n, sp_out_t)

        # ── Build wrapper ──
        lines = [f"module {design_name} ("]
        port_decls = [f"    {d}{w} {p}" for p, d, w, _ in tb_classified]
        lines.append(",\n".join(port_decls))
        lines.append(");")
        lines.append("")

        # Wire for bundled output
        if bundled_sp:
            sp_out_n, sp_out_t = bundled_sp
            wm = re.search(r"\[(\d+):(\d+)\]", sp_out_t)
            wdecl = f" [{wm.group(1)}:{wm.group(2)}]" if wm else ""
            lines.append(f"    wire{wdecl} {sp_out_n}_wire;")

        # Wires for directly-matched outputs
        for p, d, w, m in tb_classified:
            if d == "output" and m is not None:
                lines.append(f"    wire{w} {m}_wire;")

        lines.append("")

        # Instantiate Sparkle module
        lines.append(f"    {sparkle_mod_name} sparkle_dut (")
        inst_conns = []
        ref_inputs = [(d, w, n) for d, w, n in (ref_ordered_for_reset or []) if d == "input"]
        async_reset = _async_reset_expr(ref_inputs, ref_code=ref_code)
        for _, _, sn in sparkle_ports:
            # Check if this is a matched input
            matched_input = next(
                (p for p, d, _, m in tb_classified if d == "input" and m == sn), None
            )
            if matched_input:
                inst_conns.append(f"        .{sn}({matched_input})")
                continue
            # Check if this is a matched output
            matched_output = next(
                (p for p, d, _, m in tb_classified if d == "output" and m == sn), None
            )
            if matched_output:
                inst_conns.append(f"        .{sn}({sn}_wire)")
                continue
            # Check if this is the bundled output
            if bundled_sp and sn == bundled_sp[0]:
                inst_conns.append(f"        .{sn}({sn}_wire)")
                continue
            # Unmatched: handle clk/rst, _gen_ prefix, or tie off
            if sn == "clk":
                clk_sig = "clk" if "clk" in tb_ports else "1'b0"
                inst_conns.append(f"        .clk({clk_sig})")
            elif sn == "rst":
                rst_sig = async_reset if async_reset else "1'b0"
                inst_conns.append(f"        .rst({rst_sig})")
            elif sn.startswith("_gen_"):
                base = sn[5:]
                matched_tb = next((tp for tp in tb_ports if _ports_equivalent(tp, base)), None)
                inst_conns.append(f"        .{sn}({matched_tb if matched_tb else sn})")
            else:
                inst_conns.append(f"        .{sn}({sn})")
        lines.append(",\n".join(inst_conns))
        lines.append("    );")

        # ── Output assignments ──
        if bundled_sp:
            sp_out_n, sp_out_t = bundled_sp
            sp_w = _port_width(sp_out_t)

            # Try to determine concat field order from SV code
            concat_order = None
            concat_pat = re.search(
                rf"assign\s+{re.escape(sp_out_n)}\s*=\s*\{{([^}}]+)\}}", sv_code
            )
            if not concat_pat:
                indirect = re.search(
                    rf"assign\s+{re.escape(sp_out_n)}\s*=\s*(\w+)\s*;", sv_code
                )
                if indirect:
                    concat_pat = re.search(
                        rf"assign\s+{re.escape(indirect.group(1))}\s*=\s*\{{([^}}]+)\}}", sv_code
                    )
            if concat_pat:
                fields = [f.strip().replace("_gen_", "") for f in concat_pat.group(1).split(",")]
                tb_out_names = {n for n, _ in unmatched_outs}
                if len(fields) == len(unmatched_outs) and all(f in tb_out_names for f in fields):
                    concat_order = fields

            ordered = concat_order or [n for n, _ in unmatched_outs]
            offset = sp_w
            for field_name in ordered:
                for n, w in unmatched_outs:
                    if n == field_name:
                        wm = re.search(r"\[(\d+):(\d+)\]", w)
                        fw = (abs(int(wm.group(1)) - int(wm.group(2))) + 1) if wm else 1
                        high, low = offset - 1, offset - fw
                        if fw == 1:
                            lines.append(f"    assign {n} = {sp_out_n}_wire[{low}];")
                        else:
                            lines.append(f"    assign {n} = {sp_out_n}_wire[{high}:{low}];")
                        offset -= fw
                        break

        # Direct-mapped outputs
        for p, d, _, m in tb_classified:
            if d == "output" and m is not None:
                lines.append(f"    assign {p} = {m}_wire;")

        lines.append("endmodule")
        return "\n".join(lines)

    def _run_synthesis(
        self, prob_id: str, sv_code: str, top_module: str, run_dir: Path
    ) -> dict:
        """Run Yosys synthesis via ORFS Docker and extract PPA metrics."""
        result: dict = {
            "synth_pass": False,
            "area_um2": None,
            "cell_count": None,
            "wns_ns": None,
            "power_uw": None,
        }

        # Set up workspace directory
        synth_dir = run_dir / "synth" / prob_id
        synth_dir.mkdir(parents=True, exist_ok=True)

        # Write SV to workspace
        sv_file = synth_dir / f"{prob_id}.sv"
        sv_file.write_text(sv_code)

        # Check if module has a clk port — if not, write an empty SDC
        # (ORFS defaults to `create_clock [get_ports clk]` which errors on combinational designs)
        has_clk = bool(re.search(r'\binput\b.*\bclk\b', sv_code))
        sdc_file = synth_dir / "constraints.sdc"
        if not has_clk:
            sdc_file.write_text("# Combinational design — no clock constraint\n")
        else:
            sdc_file.write_text("create_clock -period 10 [get_ports clk]\n")

        # Generate config.mk
        container_sv = f"/workspace/{prob_id}.sv"
        config_content = (
            f"export DESIGN_NAME = {top_module}\n"
            f"export PLATFORM = sky130hd\n"
            f"export VERILOG_FILES = {container_sv}\n"
            f"export SDC_FILE = /workspace/constraints.sdc\n"
            f"export CORE_UTILIZATION = 5\n"
            f"export CORE_ASPECT_RATIO = 1\n"
            f"export CORE_MARGIN = 2\n"
        )
        (synth_dir / "config.mk").write_text(config_content)

        # Set up output directories (clean first to avoid stale results from prior iterations)
        results_dir = synth_dir / "orfs_results"
        logs_dir = synth_dir / "orfs_logs"
        reports_dir = synth_dir / "orfs_reports"
        objects_dir = synth_dir / "orfs_objects"
        for d in [results_dir, logs_dir, reports_dir, objects_dir]:
            if d.exists():
                shutil.rmtree(d)
            d.mkdir(parents=True, exist_ok=True)

        volumes = [
            f"{results_dir}:/OpenROAD-flow-scripts/flow/results",
            f"{logs_dir}:/OpenROAD-flow-scripts/flow/logs",
            f"{reports_dir}:/OpenROAD-flow-scripts/flow/reports",
            f"{objects_dir}:/OpenROAD-flow-scripts/flow/objects",
        ]

        # Run synthesis only (not the full flow)
        make_cmd = "make -B DESIGN_CONFIG=/workspace/config.mk synth"

        # Run synthesis
        try:
            synth_result = run_docker_command(
                command=make_cmd,
                workspace_path=str(synth_dir),
                volumes=volumes,
                timeout=SYNTH_TIMEOUT,
            )
        except Exception as e:
            result["synth_error"] = f"Synthesis exception: {e}"
            return result

        if not synth_result.get("success"):
            stderr = synth_result.get("stderr", "")[:500]
            result["synth_error"] = f"Synthesis failed: {stderr}"
            # Save logs even on failure
            (synth_dir / "synth_stdout.txt").write_text(
                synth_result.get("stdout", "")
            )
            (synth_dir / "synth_stderr.txt").write_text(
                synth_result.get("stderr", "")
            )
            return result

        result["synth_pass"] = True

        # Save synthesis logs
        (synth_dir / "synth_stdout.txt").write_text(
            synth_result.get("stdout", "")
        )

        # Extract PPA metrics from synth_stat.txt
        try:
            stat_files = list((synth_dir / "orfs_reports").rglob("synth_stat.txt"))
            for sf in stat_files:
                content = sf.read_text()
                # Area: "Chip area for module '\\name': 11.260800"
                m_area = re.search(r"Chip area.*?:\s*([0-9.]+)", content)
                if m_area and result["area_um2"] is None:
                    result["area_um2"] = float(m_area.group(1))
                # Cell count: "2   11.261 cells"
                m_cells = re.search(r"(\d+)\s+[\d.]+\s+cells", content)
                if m_cells and result["cell_count"] is None:
                    result["cell_count"] = int(m_cells.group(1))
        except Exception as e:
            result["ppa_error"] = f"PPA extraction failed: {e}"

        return result

    def _run_pnr(
        self, prob_id: str, sv_code: str, top_module: str, run_dir: Path
    ) -> dict:
        """Run full P&R (floorplan→place→CTS→route→finish) + DRC + LVS."""
        result: dict = {
            "pnr_pass": False,
            "gds_generated": False,
            "drc_pass": None,
            "drc_violations": None,
            "lvs_pass": None,
            "lvs_error": None,
        }

        synth_dir = run_dir / "synth" / prob_id
        if not synth_dir.exists():
            result["pnr_error"] = "Synth directory not found"
            return result

        # ── Calculate DIE_AREA from synth cell area ──
        cell_area = None
        try:
            for sf in (synth_dir / "orfs_reports").rglob("synth_stat.txt"):
                m = re.search(r"Chip area.*?:\s*([0-9.]+)", sf.read_text())
                if m:
                    cell_area = float(m.group(1))
                    break
        except Exception:
            pass

        if cell_area and cell_area > 0:
            # Target ~30% utilization, with minimum die size for PDN
            core_side = math.sqrt(cell_area / 0.3)
            die_side = max(core_side + 4, MIN_DIE_SIDE_UM)
        else:
            die_side = MIN_DIE_SIDE_UM

        die_side = math.ceil(die_side)
        margin = 2
        core_side = die_side - 2 * margin

        # ── Update config.mk with DIE_AREA ──
        has_clk = bool(re.search(r'\binput\b.*\bclk\b', sv_code))
        container_sv = f"/workspace/{prob_id}.sv"
        config_content = (
            f"export DESIGN_NAME = {top_module}\n"
            f"export PLATFORM = sky130hd\n"
            f"export VERILOG_FILES = {container_sv}\n"
            f"export SDC_FILE = /workspace/constraints.sdc\n"
            f"export DIE_AREA = 0 0 {die_side} {die_side}\n"
            f"export CORE_AREA = {margin} {margin} {core_side} {core_side}\n"
            f"export PLACE_DENSITY = 0.15\n"
        )
        (synth_dir / "config.mk").write_text(config_content)

        # Ensure SDC exists (should already from synth phase)
        sdc_file = synth_dir / "constraints.sdc"
        if not sdc_file.exists():
            if not has_clk:
                sdc_file.write_text("# Combinational design\n")
            else:
                sdc_file.write_text("create_clock -period 10 [get_ports clk]\n")

        volumes = [
            f"{synth_dir / 'orfs_results'}:/OpenROAD-flow-scripts/flow/results",
            f"{synth_dir / 'orfs_logs'}:/OpenROAD-flow-scripts/flow/logs",
            f"{synth_dir / 'orfs_reports'}:/OpenROAD-flow-scripts/flow/reports",
            f"{synth_dir / 'orfs_objects'}:/OpenROAD-flow-scripts/flow/objects",
        ]

        # ── Phase 1: Full P&R (finish picks up from synth) ──
        try:
            pnr_result = run_docker_command(
                command="make DESIGN_CONFIG=/workspace/config.mk finish",
                workspace_path=str(synth_dir),
                volumes=volumes,
                timeout=PNR_TIMEOUT,
            )
        except Exception as e:
            result["pnr_error"] = f"P&R exception: {e}"
            return result

        (synth_dir / "pnr_stdout.txt").write_text(pnr_result.get("stdout", ""))
        (synth_dir / "pnr_stderr.txt").write_text(pnr_result.get("stderr", ""))

        if not pnr_result.get("success"):
            result["pnr_error"] = f"P&R failed: {pnr_result.get('stderr', '')[-300:]}"
            return result

        result["pnr_pass"] = True

        # Check GDS
        gds_files = list((synth_dir / "orfs_results").rglob("6_final.gds"))
        result["gds_generated"] = len(gds_files) > 0

        # ── Parse finish report for timing/power ──
        self._parse_finish_report(synth_dir, result)

        # ── Phase 2: Multi-corner STA ──
        if self.enable_corners:
            corner_result = self._run_multi_corner_sta(
                prob_id, sv_code, top_module, synth_dir, volumes
            )
            result.update(corner_result)

        # ── Phase 3: DRC ──
        if self.enable_drc:
            result.update(self._run_drc(synth_dir, volumes))

        # ── Phase 4: LVS (best-effort) ──
        if self.enable_lvs:
            result.update(self._run_lvs(synth_dir, volumes))

        return result

    @staticmethod
    def _parse_finish_report(synth_dir: Path, result: dict) -> None:
        """Parse 6_finish.rpt for WNS, TNS, and power."""
        try:
            for rpt in (synth_dir / "orfs_reports").rglob("6_finish.rpt"):
                text = rpt.read_text()
                # WNS
                m = re.search(r"wns\s+max\s+([0-9.eE+-]+)", text)
                if m and result.get("wns_ns") is None:
                    result["wns_ns"] = float(m.group(1))
                # TNS
                m = re.search(r"tns\s+max\s+([0-9.eE+-]+)", text)
                if m:
                    result["tns_ns"] = float(m.group(1))
                # Power: "Total  <int> <switch> <leak> <total> 100.0%"
                # Match the last column before "100.0%"
                for line in text.splitlines():
                    if line.strip().startswith("Total") and "100" in line:
                        parts = line.split()
                        # Total <internal> <switching> <leakage> <total> <pct>
                        if len(parts) >= 5:
                            try:
                                total_w = float(parts[-2])
                                result["power_uw"] = total_w * 1e6
                            except (ValueError, IndexError):
                                pass
                break
        except Exception:
            pass

    def _run_multi_corner_sta(
        self, prob_id: str, sv_code: str, top_module: str,
        synth_dir: Path, volumes: list[str],
    ) -> dict:
        """Run STA at each PVT corner on the post-route netlist."""
        has_clk = bool(re.search(r'\binput\b.*\bclk\b', sv_code))
        corners_data: list[dict] = []
        platform_dir = "/OpenROAD-flow-scripts/flow/platforms/sky130hd"
        results_base = f"/OpenROAD-flow-scripts/flow/results/sky130hd/{top_module}/base"

        for corner in PVT_CORNERS:
            cname = corner["name"]
            lib_path = f"{platform_dir}/lib/{corner['lib']}"

            # TCL script for this corner
            tcl_lines = [
                f"read_liberty {lib_path}",
                f"read_verilog {results_base}/6_final.v",
                f"link_design {top_module}",
                "read_sdc /workspace/constraints.sdc",
                f"catch {{ read_spef {results_base}/6_final.spef }}",
            ]
            if has_clk:
                tcl_lines += [
                    "report_checks -path_delay max -digits 4",
                    "report_checks -path_delay min -digits 4",
                ]
            tcl_lines += [
                "report_power",
                "exit",
            ]

            tcl_file = synth_dir / f"sta_{cname}.tcl"
            tcl_file.write_text("\n".join(tcl_lines) + "\n")

            try:
                sta_result = run_docker_command(
                    command=f"/OpenROAD-flow-scripts/tools/install/OpenROAD/bin/sta /workspace/sta_{cname}.tcl",
                    workspace_path=str(synth_dir),
                    volumes=volumes,
                    timeout=STA_TIMEOUT,
                )
            except Exception as e:
                corners_data.append({
                    "corner": cname, "label": corner["label"],
                    "error": str(e),
                })
                continue

            stdout = sta_result.get("stdout", "")
            (synth_dir / f"sta_{cname}_out.txt").write_text(stdout)

            cdata: dict = {
                "corner": cname,
                "label": corner["label"],
                "wns_ns": None,
                "whs_ns": None,
                "power_uw": None,
            }

            # Parse setup WNS and hold WHS from slack lines
            setup_slacks = re.findall(
                r"slack\s+\((?:MET|VIOLATED)\)\s+([0-9.eE+-]+)", stdout
            )
            if setup_slacks:
                cdata["wns_ns"] = float(setup_slacks[0])
            if len(setup_slacks) >= 2:
                cdata["whs_ns"] = float(setup_slacks[1])

            # Parse power: "Total  <int> <switch> <leak> <total>"
            for line in stdout.splitlines():
                if line.strip().startswith("Total"):
                    parts = line.split()
                    if len(parts) >= 5:
                        try:
                            cdata["power_uw"] = float(parts[-2]) * 1e6
                        except (ValueError, IndexError):
                            pass

            corners_data.append(cdata)

        # Compute worst-case across corners
        worst: dict = {
            "pvt_corners": corners_data,
            "pvt_worst_wns_ns": None,
            "pvt_worst_whs_ns": None,
            "pvt_worst_power_uw": None,
        }
        wns_vals = [c["wns_ns"] for c in corners_data if c.get("wns_ns") is not None]
        whs_vals = [c["whs_ns"] for c in corners_data if c.get("whs_ns") is not None]
        pwr_vals = [c["power_uw"] for c in corners_data if c.get("power_uw") is not None]
        if wns_vals:
            worst["pvt_worst_wns_ns"] = min(wns_vals)
        if whs_vals:
            worst["pvt_worst_whs_ns"] = min(whs_vals)
        if pwr_vals:
            worst["pvt_worst_power_uw"] = max(pwr_vals)

        return worst

    @staticmethod
    def _run_drc(synth_dir: Path, volumes: list[str]) -> dict:
        """Run KLayout DRC and parse violation count."""
        result: dict = {"drc_pass": None, "drc_violations": None}
        try:
            drc_result = run_docker_command(
                command="make DESIGN_CONFIG=/workspace/config.mk drc",
                workspace_path=str(synth_dir),
                volumes=volumes,
                timeout=DRC_TIMEOUT,
            )
        except Exception as e:
            result["drc_error"] = f"DRC exception: {e}"
            return result

        if not drc_result.get("success"):
            result["drc_error"] = f"DRC failed: {drc_result.get('stderr', '')[-200:]}"
            return result

        # Parse 6_drc_count.rpt
        try:
            for f in (synth_dir / "orfs_reports").rglob("6_drc_count.rpt"):
                count_str = f.read_text().strip()
                violations = int(count_str) if count_str.isdigit() else -1
                result["drc_violations"] = violations
                result["drc_pass"] = violations == 0
                break
        except Exception:
            result["drc_pass"] = False

        return result

    @staticmethod
    def _run_lvs(synth_dir: Path, volumes: list[str]) -> dict:
        """Run KLayout LVS, fixing sky130hd CDL 'short' keyword that KLayout can't parse."""
        result: dict = {"lvs_pass": None, "lvs_error": None}

        # sky130 CDL has two issues KLayout 0.30.x can't handle:
        # 1. "rXX ... short" resistor lines (zero-ohm connections) — delete them
        # 2. "/" separator between pins and subcircuit name — remove it
        lvs_cmd = (
            "sed -i -e '/ short$/d' -e 's| / | |g' "
            "/OpenROAD-flow-scripts/flow/platforms/sky130hd/cdl/sky130hd.cdl && "
            "make DESIGN_CONFIG=/workspace/config.mk lvs"
        )

        try:
            lvs_result = run_docker_command(
                command=lvs_cmd,
                workspace_path=str(synth_dir),
                volumes=volumes,
                timeout=LVS_TIMEOUT,
            )
        except Exception as e:
            result["lvs_error"] = f"LVS exception: {e}"
            return result

        if lvs_result.get("success"):
            result["lvs_pass"] = True
        else:
            stderr = lvs_result.get("stderr", "")
            if "Can't find a value for a R, C or L device" in stderr:
                result["lvs_error"] = "KLayout CDL parse error (platform issue)"
            else:
                result["lvs_pass"] = False
                result["lvs_error"] = stderr[-200:]

        return result
