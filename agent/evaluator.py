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

import ast
import math
import os
import re
import signal
import shutil
import subprocess
import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path


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
CVDP_PARAMETERIZATION_UNSUPPORTED = "CVDP_FIXED_CORE_PARAMETERIZATION_UNSUPPORTED"


class CVDPAdapterContractError(RuntimeError):
    """Raised when an adapter would expose parameters around a fixed RTL core."""

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
                r"^(?:(input|output)\s+)?(?:(?:reg|logic|wire)\s*)?(?:signed\s*)?"
                r"((?:\s*\[[^\]]+\])*)\s*([A-Za-z_]\w*)$",
                entry,
            )
            if not m:
                continue
            if m.group(1):
                current_direction = m.group(1)
                current_width = ""
            if m.group(2):
                current_width = m.group(2).strip()
            if current_direction is None:
                continue
            typ = f"logic {current_width}".strip() if current_width else "logic"
            ports.append((current_direction, typ, m.group(3)))
        if ports:
            return mod_name, ports

    for pm in re.finditer(
        r"(input|output)\s+(?:(?:reg|logic|wire)\s*)?(?:signed\s*)?"
        r"((?:\s*\[[^\]]+\])*)\s*([A-Za-z_]\w*)",
        body_text,
    ):
        direction, width, name = pm.group(1), pm.group(2).strip(), pm.group(3)
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
        r"(input|output)\s+(?:reg\s*|logic\s*|wire\s*)?(?:signed\s*)?"
        r"((?:\s*\[[^\]]+\])*)\s*([A-Za-z_]\w*)",
        ref_sv,
    ):
        direction = m.group(1)
        width = m.group(2).strip()
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
                    r"^(?:(input|output)\s+)?(?:(?:reg|logic|wire)\s*)?(?:signed\s*)?"
                    r"((?:\s*\[[^\]]+\])*)\s*([A-Za-z_]\w*)$",
                    entry,
                )
                if not m:
                    continue
                if m.group(1):
                    current_direction = m.group(1)
                    current_width = ""
                if m.group(2):
                    current_width = f" {m.group(2).strip()}"
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
                r"(input|output)\s+(?:reg\s*|wire\s*|logic\s*)?(?:signed\s*)?"
                r"((?:\s*\[[^\]]+\])*)\s*([^;]+)",
                body,
            ):
                direction = m.group(1)
                width = f" {m.group(2).strip()}" if m.group(2) else ""
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


@dataclass(frozen=True)
class CVDPParameterOverrideAnalysis:
    """Static summary of ``build(..., parameters=...)`` calls in a harness.

    ``may_have_overrides`` is deliberately stronger than
    ``has_definite_overrides``.  A fixed Sparkle core is safe only when the
    former is false: an unresolved ``**kwargs`` or parameter dictionary must
    not be mistaken for an empty parameter set.
    """

    has_definite_overrides: bool
    may_have_overrides: bool
    parameter_names: frozenset[str]
    unresolved: bool
    build_call_count: int
    unresolved_reasons: tuple[str, ...]


@dataclass(frozen=True)
class _CVDPStaticValue:
    kind: str
    # Mapping entries are (key, value, definitely-present).  Keeping optional
    # entries lets branch merging retain useful names without claiming that a
    # key exists on every execution path.
    entries: tuple[tuple[str, "_CVDPStaticValue", bool], ...] = ()
    unknown_mapping_keys: bool = False


_CVDP_UNKNOWN_VALUE = _CVDPStaticValue("unknown")
_CVDP_NONE_VALUE = _CVDPStaticValue("none")
_CVDP_OTHER_VALUE = _CVDPStaticValue("other")


def _cvdp_mapping_value(
    entries: dict[str, tuple[_CVDPStaticValue, bool]] | None = None,
    *,
    unknown_keys: bool = False,
) -> _CVDPStaticValue:
    rendered = tuple(
        (key, value, definite)
        for key, (value, definite) in sorted((entries or {}).items())
    )
    return _CVDPStaticValue("mapping", rendered, unknown_keys)


def _cvdp_mapping_entries(
    value: _CVDPStaticValue,
) -> dict[str, tuple[_CVDPStaticValue, bool]]:
    return {key: (entry_value, definite) for key, entry_value, definite in value.entries}


def _cvdp_merge_static_values(
    left: _CVDPStaticValue,
    right: _CVDPStaticValue,
) -> _CVDPStaticValue:
    if left == right:
        return left
    if left.kind != "mapping" or right.kind != "mapping":
        return _CVDP_UNKNOWN_VALUE

    left_entries = _cvdp_mapping_entries(left)
    right_entries = _cvdp_mapping_entries(right)
    merged: dict[str, tuple[_CVDPStaticValue, bool]] = {}
    for key in left_entries.keys() | right_entries.keys():
        if key in left_entries and key in right_entries:
            left_value, left_definite = left_entries[key]
            right_value, right_definite = right_entries[key]
            merged[key] = (
                _cvdp_merge_static_values(left_value, right_value),
                left_definite and right_definite,
            )
        elif key in left_entries:
            merged[key] = (left_entries[key][0], False)
        else:
            merged[key] = (right_entries[key][0], False)
    return _cvdp_mapping_value(
        merged,
        unknown_keys=left.unknown_mapping_keys or right.unknown_mapping_keys,
    )


def _cvdp_merge_environments(
    left: dict[str, _CVDPStaticValue],
    right: dict[str, _CVDPStaticValue],
) -> dict[str, _CVDPStaticValue]:
    merged: dict[str, _CVDPStaticValue] = {}
    for name in left.keys() | right.keys():
        if name in left and name in right:
            merged[name] = _cvdp_merge_static_values(left[name], right[name])
        else:
            # A name bound on only one path cannot be resolved reliably.
            merged[name] = _CVDP_UNKNOWN_VALUE
    return merged


def _cvdp_replace_static_reference(
    value: _CVDPStaticValue,
    old: _CVDPStaticValue,
    new: _CVDPStaticValue,
) -> _CVDPStaticValue:
    """Update aliases, including dictionaries nested inside ``**kwargs``."""
    if value is old:
        return new
    if value.kind != "mapping":
        return value
    changed = False
    entries: dict[str, tuple[_CVDPStaticValue, bool]] = {}
    for key, entry_value, definite in value.entries:
        replacement = _cvdp_replace_static_reference(entry_value, old, new)
        changed = changed or replacement is not entry_value
        entries[key] = (replacement, definite)
    if not changed:
        return value
    return _cvdp_mapping_value(entries, unknown_keys=value.unknown_mapping_keys)


@dataclass(frozen=True)
class _CVDPParameterUse:
    definite: bool
    possible: bool
    names: frozenset[str]
    unresolved: bool


def _cvdp_classify_parameter_value(value: _CVDPStaticValue) -> _CVDPParameterUse:
    if value.kind == "none":
        return _CVDPParameterUse(False, False, frozenset(), False)
    if value.kind != "mapping":
        return _CVDPParameterUse(False, True, frozenset(), True)

    definite_names = {key for key, _, definite in value.entries if definite}
    possible_names = {key for key, _, _ in value.entries}
    uncertain = value.unknown_mapping_keys or any(
        not definite for _, _, definite in value.entries
    )
    return _CVDPParameterUse(
        bool(definite_names),
        bool(possible_names) or value.unknown_mapping_keys,
        frozenset(possible_names),
        uncertain,
    )


class _CVDPHarnessParameterAnalyzer:
    """Small, flow-sensitive interpreter for harness dictionary construction."""

    def __init__(self) -> None:
        self.has_definite_overrides = False
        self.may_have_overrides = False
        self.parameter_names: set[str] = set()
        self.unresolved = False
        self.build_call_count = 0
        self.unresolved_reasons: list[str] = []
        self.function_defs: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
        self.build_aliases: set[str] = set()
        self.build_attribute_aliases: set[str] = set()

    @staticmethod
    def _string_key(node: ast.AST) -> str | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        return None

    def _eval_expr(
        self,
        node: ast.AST | None,
        env: dict[str, _CVDPStaticValue],
    ) -> _CVDPStaticValue:
        if node is None:
            return _CVDP_NONE_VALUE
        if isinstance(node, ast.Constant):
            return _CVDP_NONE_VALUE if node.value is None else _CVDP_OTHER_VALUE
        if isinstance(node, ast.Name):
            return env.get(node.id, _CVDP_UNKNOWN_VALUE)
        if isinstance(node, ast.Dict):
            entries: dict[str, tuple[_CVDPStaticValue, bool]] = {}
            unknown_keys = False
            for key_node, value_node in zip(node.keys, node.values):
                if key_node is None:
                    expanded = self._eval_expr(value_node, env)
                    if expanded.kind != "mapping":
                        unknown_keys = True
                        continue
                    for key, value, definite in expanded.entries:
                        entries[key] = (value, definite)
                    unknown_keys = unknown_keys or expanded.unknown_mapping_keys
                    continue
                key = self._string_key(key_node)
                if key is None:
                    unknown_keys = True
                    continue
                entries[key] = (self._eval_expr(value_node, env), True)
            return _cvdp_mapping_value(entries, unknown_keys=unknown_keys)
        if isinstance(node, ast.IfExp):
            return _cvdp_merge_static_values(
                self._eval_expr(node.body, env),
                self._eval_expr(node.orelse, env),
            )
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            left = self._eval_expr(node.left, env)
            right = self._eval_expr(node.right, env)
            return self._mapping_update(left, right)
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == "dict":
                result = _cvdp_mapping_value()
                for arg in node.args:
                    result = self._mapping_update(result, self._eval_expr(arg, env))
                keyword_entries: dict[str, tuple[_CVDPStaticValue, bool]] = {}
                unknown_keys = False
                for keyword in node.keywords:
                    if keyword.arg is None:
                        result = self._mapping_update(
                            result, self._eval_expr(keyword.value, env)
                        )
                    else:
                        keyword_entries[keyword.arg] = (
                            self._eval_expr(keyword.value, env),
                            True,
                        )
                if keyword_entries or unknown_keys:
                    result = self._mapping_update(
                        result,
                        _cvdp_mapping_value(keyword_entries, unknown_keys=unknown_keys),
                    )
                return result
            if (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "copy"
                and not node.args
                and not node.keywords
            ):
                original = self._eval_expr(node.func.value, env)
                if original.kind != "mapping":
                    return _CVDP_UNKNOWN_VALUE
                # ``dict.copy`` is shallow: nested values remain aliases, but
                # mutations of the new outer dictionary must not affect the
                # original mapping.
                return _cvdp_mapping_value(
                    _cvdp_mapping_entries(original),
                    unknown_keys=original.unknown_mapping_keys,
                )
            return _CVDP_UNKNOWN_VALUE
        if isinstance(node, ast.NamedExpr):
            value = self._eval_expr(node.value, env)
            self._assign_target(node.target, value, env)
            return value
        return _CVDP_UNKNOWN_VALUE

    @staticmethod
    def _mapping_update(
        base: _CVDPStaticValue,
        update: _CVDPStaticValue,
    ) -> _CVDPStaticValue:
        if base.kind != "mapping":
            base = _cvdp_mapping_value(unknown_keys=True)
        entries = _cvdp_mapping_entries(base)
        unknown_keys = base.unknown_mapping_keys
        if update.kind != "mapping":
            return _cvdp_mapping_value(entries, unknown_keys=True)
        for key, value, definite in update.entries:
            if definite:
                entries[key] = (value, True)
            elif key in entries:
                entries[key] = (
                    _cvdp_merge_static_values(entries[key][0], value),
                    entries[key][1],
                )
            else:
                entries[key] = (value, False)
        return _cvdp_mapping_value(
            entries,
            unknown_keys=unknown_keys or update.unknown_mapping_keys,
        )

    @staticmethod
    def _attribute_key(node: ast.AST) -> str | None:
        if not isinstance(node, ast.Attribute):
            return None
        try:
            return ast.unparse(node)
        except Exception:
            return None

    def _function_returns_build(
        self,
        name: str,
        seen: set[str],
    ) -> bool:
        if name in seen:
            return False
        function = self.function_defs.get(name)
        if function is None:
            return False
        nested_seen = set(seen)
        nested_seen.add(name)

        def contains_build_return(node: ast.AST) -> bool:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                return False
            if isinstance(node, ast.Return):
                return (
                    node.value is not None
                    and self._is_build_callable(node.value, nested_seen)
                )
            return any(contains_build_return(child) for child in ast.iter_child_nodes(node))

        return any(contains_build_return(statement) for statement in function.body)

    def _is_build_callable(
        self,
        node: ast.AST,
        seen: set[str] | None = None,
    ) -> bool:
        seen = seen or set()
        if isinstance(node, ast.Name):
            return node.id == "build" or node.id in self.build_aliases
        if isinstance(node, ast.Attribute):
            key = self._attribute_key(node)
            return node.attr == "build" or (
                key is not None and key in self.build_attribute_aliases
            )
        if isinstance(node, ast.Call):
            fn = node.func
            is_partial = (
                isinstance(fn, ast.Name) and fn.id == "partial"
            ) or (
                isinstance(fn, ast.Attribute) and fn.attr == "partial"
            )
            if is_partial and node.args:
                return self._is_build_callable(node.args[0], seen)
            is_getattr = (
                isinstance(fn, ast.Name) and fn.id == "getattr"
            ) or (
                isinstance(fn, ast.Attribute) and fn.attr == "__getattribute__"
            )
            if is_getattr and node.args:
                name_node = (
                    node.args[1]
                    if isinstance(fn, ast.Name) and len(node.args) > 1
                    else node.args[0]
                )
                return self._string_key(name_node) == "build"
            if isinstance(fn, ast.Name):
                return self._function_returns_build(fn.id, seen)
        return False

    def _record_build_aliases(self, targets: list[ast.AST], value: ast.AST) -> None:
        for target in targets:
            if (
                isinstance(target, (ast.Tuple, ast.List))
                and isinstance(value, (ast.Tuple, ast.List))
            ):
                for target_item, value_item in zip(target.elts, value.elts):
                    self._record_build_aliases([target_item], value_item)
                continue
            if not self._is_build_callable(value):
                continue
            if isinstance(target, ast.Name):
                self.build_aliases.add(target.id)
            elif isinstance(target, ast.Attribute):
                key = self._attribute_key(target)
                if key is not None:
                    self.build_attribute_aliases.add(key)

    def _is_build_call(self, node: ast.Call) -> bool:
        # A partial application is itself where bound ``parameters=`` values
        # appear, so inspect it in addition to the eventual alias invocation.
        if (
            ((isinstance(node.func, ast.Name) and node.func.id == "partial")
             or (isinstance(node.func, ast.Attribute) and node.func.attr == "partial"))
            and node.args
            and self._is_build_callable(node.args[0])
        ):
            return True
        return self._is_build_callable(node.func)

    def _record_use(self, use: _CVDPParameterUse, reason: str) -> None:
        self.has_definite_overrides |= use.definite
        self.may_have_overrides |= use.possible
        self.parameter_names.update(use.names)
        if use.unresolved:
            self.unresolved = True
            if reason not in self.unresolved_reasons:
                self.unresolved_reasons.append(reason)

    def _inspect_build_call(
        self,
        node: ast.Call,
        env: dict[str, _CVDPStaticValue],
    ) -> None:
        self.build_call_count += 1
        for keyword in node.keywords:
            if keyword.arg == "parameters":
                self._record_use(
                    _cvdp_classify_parameter_value(self._eval_expr(keyword.value, env)),
                    "dynamic value passed to parameters=",
                )
                continue
            if keyword.arg is not None:
                continue

            expanded = self._eval_expr(keyword.value, env)
            if expanded.kind != "mapping":
                self._record_use(
                    _CVDPParameterUse(False, True, frozenset(), True),
                    "unresolved **kwargs passed to build()",
                )
                continue
            entries = _cvdp_mapping_entries(expanded)
            if "parameters" in entries:
                parameter_value, _ = entries["parameters"]
                self._record_use(
                    _cvdp_classify_parameter_value(parameter_value),
                    "dynamic parameters entry in **kwargs",
                )
            if expanded.unknown_mapping_keys:
                self._record_use(
                    _CVDPParameterUse(False, True, frozenset(), True),
                    "**kwargs may contain a parameters entry",
                )

    def _observe_build_calls(
        self,
        node: ast.AST | None,
        env: dict[str, _CVDPStaticValue],
    ) -> None:
        if node is None:
            return
        calls = [candidate for candidate in ast.walk(node) if isinstance(candidate, ast.Call)]
        build_calls = [candidate for candidate in calls if self._is_build_call(candidate)]
        for candidate in build_calls:
            self._inspect_build_call(candidate, env)

        # Evaluation order inside tuples/comprehensions can mutate a mapping
        # before a sibling build() call.  Rather than silently assuming the
        # pre-expression environment, flag the call as unresolved.
        if build_calls and any(
            self._call_may_mutate_tracked_mapping(candidate, env)
            for candidate in calls
            if not self._is_build_call(candidate)
        ):
            self._record_use(
                _CVDPParameterUse(False, True, frozenset(), True),
                "mapping may be mutated in the expression containing build()",
            )

        if build_calls:
            return

        # Preserve side effects for subsequent statements. Unknown helper
        # calls receiving a tracked dictionary are treated as possible
        # mutations; this is required for fail-closed adapter checking.
        for candidate in calls:
            # A mutating call has the same side effect whether it is a bare
            # expression or is embedded in an assignment/condition, e.g.
            # `ignored = params.update({...})`.  Apply it here for every
            # expression context so a later build(parameters=params) observes
            # the updated mapping.
            self._apply_call_mutation(candidate, env)
            if isinstance(candidate.func, ast.Name) and candidate.func.id in self.function_defs:
                function = self.function_defs[candidate.func.id]
                for name in self._outer_mapping_mutations(function, env):
                    self._mark_mapping_unknown(name, env)
            if isinstance(candidate.func, ast.Attribute):
                method = candidate.func.attr
                receiver = candidate.func.value
                if isinstance(receiver, ast.Name) and env.get(receiver.id, _CVDP_UNKNOWN_VALUE).kind == "mapping":
                    if method not in {
                        "clear", "update", "setdefault", "pop", "popitem",
                        "__delitem__", "copy", "get", "items", "keys", "values",
                    }:
                        self._mark_mapping_unknown(receiver.id, env)
            for argument in list(candidate.args) + [keyword.value for keyword in candidate.keywords]:
                if isinstance(argument, ast.Name) and env.get(argument.id, _CVDP_UNKNOWN_VALUE).kind == "mapping":
                    self._mark_mapping_unknown(argument.id, env)

    @staticmethod
    def _outer_mapping_mutations(
        function: ast.FunctionDef | ast.AsyncFunctionDef,
        env: dict[str, _CVDPStaticValue],
    ) -> set[str]:
        candidates = {
            name for name, value in env.items() if value.kind == "mapping"
        }
        if not candidates:
            return set()
        explicit_outer = {
            name
            for node in ast.walk(function)
            if isinstance(node, (ast.Global, ast.Nonlocal))
            for name in node.names
        }
        local_names = {
            argument.arg
            for argument in (
                list(function.args.posonlyargs)
                + list(function.args.args)
                + list(function.args.kwonlyargs)
            )
        }
        for node in ast.walk(function):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node is not function:
                continue
            targets: list[ast.AST] = []
            if isinstance(node, ast.Assign):
                targets = list(node.targets)
            elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
                targets = [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    local_names.add(target.id)
        visible_candidates = candidates - (local_names - explicit_outer)

        mutated: set[str] = set()
        for node in ast.walk(function):
            target = None
            if isinstance(node, ast.Assign):
                for assigned in node.targets:
                    root = assigned
                    while isinstance(root, (ast.Subscript, ast.Attribute)):
                        root = root.value
                    if isinstance(root, ast.Name) and root.id in visible_candidates:
                        mutated.add(root.id)
            elif isinstance(node, (ast.AnnAssign, ast.AugAssign, ast.Delete)):
                targets = node.targets if isinstance(node, ast.Delete) else [node.target]
                for assigned in targets:
                    root = assigned
                    while isinstance(root, (ast.Subscript, ast.Attribute)):
                        root = root.value
                    if isinstance(root, ast.Name) and root.id in visible_candidates:
                        mutated.add(root.id)
            elif isinstance(node, ast.Call):
                receiver = node.func.value if isinstance(node.func, ast.Attribute) else None
                while isinstance(receiver, (ast.Subscript, ast.Attribute)):
                    receiver = receiver.value
                if isinstance(receiver, ast.Name) and receiver.id in visible_candidates:
                    if isinstance(node.func, ast.Attribute) and node.func.attr not in {
                        "copy", "get", "items", "keys", "values"
                    }:
                        mutated.add(receiver.id)
                for argument in list(node.args) + [keyword.value for keyword in node.keywords]:
                    if isinstance(argument, ast.Name) and argument.id in visible_candidates:
                        mutated.add(argument.id)
        return mutated

    @staticmethod
    def _call_may_mutate_tracked_mapping(
        node: ast.Call,
        env: dict[str, _CVDPStaticValue],
    ) -> bool:
        if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
            receiver = env.get(node.func.value.id, _CVDP_UNKNOWN_VALUE)
            if receiver.kind == "mapping" and node.func.attr not in {
                "copy", "get", "items", "keys", "values"
            }:
                return True
        return any(
            isinstance(argument, ast.Name)
            and env.get(argument.id, _CVDP_UNKNOWN_VALUE).kind == "mapping"
            for argument in list(node.args) + [keyword.value for keyword in node.keywords]
        )

    def _replace_aliases(
        self,
        env: dict[str, _CVDPStaticValue],
        old: _CVDPStaticValue,
        new: _CVDPStaticValue,
    ) -> None:
        if old.kind != "mapping":
            return
        for name, value in list(env.items()):
            env[name] = _cvdp_replace_static_reference(value, old, new)

    def _mutate_named_mapping(
        self,
        name: str,
        env: dict[str, _CVDPStaticValue],
        update: _CVDPStaticValue,
    ) -> None:
        old = env.get(name, _CVDP_UNKNOWN_VALUE)
        new = self._mapping_update(old, update)
        self._replace_aliases(env, old, new)
        env[name] = new

    def _mark_mapping_unknown(
        self,
        name: str,
        env: dict[str, _CVDPStaticValue],
    ) -> None:
        old = env.get(name, _CVDP_UNKNOWN_VALUE)
        if old.kind != "mapping":
            return
        new = _cvdp_mapping_value(
            _cvdp_mapping_entries(old),
            unknown_keys=True,
        )
        self._replace_aliases(env, old, new)
        env[name] = new

    def _mark_nested_mapping_unknown(
        self,
        root_name: str,
        env: dict[str, _CVDPStaticValue],
    ) -> None:
        """Conservatively invalidate a root mapping after a nested mutation."""
        self._mark_mapping_unknown(root_name, env)

    def _assign_target(
        self,
        target: ast.AST,
        value: _CVDPStaticValue,
        env: dict[str, _CVDPStaticValue],
    ) -> None:
        if isinstance(target, ast.Name):
            env[target.id] = value
            return
        if isinstance(target, (ast.Tuple, ast.List)):
            for element in target.elts:
                self._assign_target(element, _CVDP_UNKNOWN_VALUE, env)
            return
        if (
            isinstance(target, ast.Subscript)
            and isinstance(target.value, ast.Name)
        ):
            key = self._string_key(target.slice)
            if key is None:
                update = _cvdp_mapping_value(unknown_keys=True)
            else:
                update = _cvdp_mapping_value({key: (value, True)})
            self._mutate_named_mapping(target.value.id, env, update)
            return
        if isinstance(target, ast.Subscript):
            root = target.value
            while isinstance(root, ast.Subscript):
                root = root.value
            if isinstance(root, ast.Name):
                self._mark_nested_mapping_unknown(root.id, env)

    def _apply_call_mutation(
        self,
        node: ast.Call,
        env: dict[str, _CVDPStaticValue],
    ) -> None:
        if not isinstance(node.func, ast.Attribute):
            return
        if not isinstance(node.func.value, ast.Name):
            receiver = node.func.value
            while isinstance(receiver, ast.Subscript):
                receiver = receiver.value
            if isinstance(receiver, ast.Name):
                self._mark_nested_mapping_unknown(receiver.id, env)
            return
        name = node.func.value.id
        method = node.func.attr
        old = env.get(name, _CVDP_UNKNOWN_VALUE)
        if method == "clear":
            new = _cvdp_mapping_value()
        elif method == "update":
            update = _cvdp_mapping_value()
            for arg in node.args:
                update = self._mapping_update(update, self._eval_expr(arg, env))
            for keyword in node.keywords:
                if keyword.arg is None:
                    update = self._mapping_update(
                        update, self._eval_expr(keyword.value, env)
                    )
                else:
                    update = self._mapping_update(
                        update,
                        _cvdp_mapping_value({
                            keyword.arg: (self._eval_expr(keyword.value, env), True)
                        }),
                    )
            new = self._mapping_update(old, update)
        elif method == "setdefault" and node.args:
            key = self._string_key(node.args[0])
            if key is None:
                new = self._mapping_update(old, _cvdp_mapping_value(unknown_keys=True))
            else:
                entries = _cvdp_mapping_entries(old) if old.kind == "mapping" else {}
                if key not in entries:
                    default = self._eval_expr(node.args[1], env) if len(node.args) > 1 else _CVDP_NONE_VALUE
                    entries[key] = (default, True)
                new = _cvdp_mapping_value(
                    entries,
                    unknown_keys=old.unknown_mapping_keys if old.kind == "mapping" else True,
                )
        elif method in {"pop", "popitem", "__delitem__"}:
            # Removal can make a dictionary empty, but without executing the
            # harness we cannot prove which keys remain.
            entries = _cvdp_mapping_entries(old) if old.kind == "mapping" else {}
            new = _cvdp_mapping_value(entries, unknown_keys=True)
        else:
            return
        self._replace_aliases(env, old, new)
        env[name] = new

    def _process_block(
        self,
        statements: list[ast.stmt],
        env: dict[str, _CVDPStaticValue],
    ) -> dict[str, _CVDPStaticValue]:
        for statement in statements:
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.function_defs[statement.name] = statement
                function_env = dict(env)
                arguments = (
                    list(statement.args.posonlyargs)
                    + list(statement.args.args)
                    + list(statement.args.kwonlyargs)
                )
                if statement.args.vararg:
                    arguments.append(statement.args.vararg)
                if statement.args.kwarg:
                    arguments.append(statement.args.kwarg)
                for argument in arguments:
                    function_env[argument.arg] = _CVDP_UNKNOWN_VALUE
                self._process_block(statement.body, function_env)
                continue
            if isinstance(statement, ast.ClassDef):
                self._process_block(statement.body, dict(env))
                continue
            if isinstance(statement, ast.Assign):
                self._record_build_aliases(list(statement.targets), statement.value)
                self._observe_build_calls(statement.value, env)
                value = self._eval_expr(statement.value, env)
                for target in statement.targets:
                    self._assign_target(target, value, env)
                continue
            if isinstance(statement, ast.AnnAssign):
                self._record_build_aliases([statement.target], statement.value)
                self._observe_build_calls(statement.value, env)
                self._assign_target(
                    statement.target, self._eval_expr(statement.value, env), env
                )
                continue
            if isinstance(statement, ast.AugAssign):
                self._observe_build_calls(statement.value, env)
                if isinstance(statement.target, ast.Name) and isinstance(statement.op, ast.BitOr):
                    self._mutate_named_mapping(
                        statement.target.id,
                        env,
                        self._eval_expr(statement.value, env),
                    )
                else:
                    self._assign_target(statement.target, _CVDP_UNKNOWN_VALUE, env)
                continue
            if isinstance(statement, ast.Expr):
                self._observe_build_calls(statement.value, env)
                continue
            if isinstance(statement, ast.If):
                self._observe_build_calls(statement.test, env)
                left = self._process_block(statement.body, dict(env))
                right = self._process_block(statement.orelse, dict(env))
                env = _cvdp_merge_environments(left, right)
                continue
            if isinstance(statement, (ast.For, ast.AsyncFor, ast.While)):
                before = dict(env)
                loop_env = dict(env)
                if isinstance(statement, (ast.For, ast.AsyncFor)):
                    self._observe_build_calls(statement.iter, env)
                    self._assign_target(statement.target, _CVDP_UNKNOWN_VALUE, loop_env)
                else:
                    self._observe_build_calls(statement.test, env)
                loop_env = self._process_block(statement.body, loop_env)
                env = _cvdp_merge_environments(before, loop_env)
                env = self._process_block(statement.orelse, env)
                continue
            if isinstance(statement, (ast.With, ast.AsyncWith)):
                for item in statement.items:
                    self._observe_build_calls(item.context_expr, env)
                    if item.optional_vars:
                        self._assign_target(item.optional_vars, _CVDP_UNKNOWN_VALUE, env)
                env = self._process_block(statement.body, env)
                continue
            if isinstance(statement, ast.Try):
                paths = [self._process_block(statement.body, dict(env))]
                paths.extend(
                    self._process_block(handler.body, dict(env))
                    for handler in statement.handlers
                )
                merged = paths[0]
                for path in paths[1:]:
                    merged = _cvdp_merge_environments(merged, path)
                merged = self._process_block(statement.orelse, merged)
                env = self._process_block(statement.finalbody, merged)
                continue
            if isinstance(statement, ast.Match):
                self._observe_build_calls(statement.subject, env)
                paths = [self._process_block(case.body, dict(env)) for case in statement.cases]
                if paths:
                    merged = paths[0]
                    for path in paths[1:]:
                        merged = _cvdp_merge_environments(merged, path)
                    env = merged
                continue
            if isinstance(statement, ast.Delete):
                for target in statement.targets:
                    if isinstance(target, ast.Name):
                        env.pop(target.id, None)
                    elif isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name):
                        old = env.get(target.value.id, _CVDP_UNKNOWN_VALUE)
                        entries = _cvdp_mapping_entries(old) if old.kind == "mapping" else {}
                        key = self._string_key(target.slice)
                        if key is not None:
                            entries.pop(key, None)
                        new = _cvdp_mapping_value(entries, unknown_keys=True)
                        self._replace_aliases(env, old, new)
                        env[target.value.id] = new
                continue

            for _, value in ast.iter_fields(statement):
                nodes = value if isinstance(value, list) else [value]
                for node in nodes:
                    if isinstance(node, ast.expr):
                        self._observe_build_calls(node, env)
        return env

    def analyze(self, tree: ast.Module) -> CVDPParameterOverrideAnalysis:
        self._process_block(tree.body, {})
        return CVDPParameterOverrideAnalysis(
            has_definite_overrides=self.has_definite_overrides,
            may_have_overrides=self.may_have_overrides,
            parameter_names=frozenset(self.parameter_names),
            unresolved=self.unresolved,
            build_call_count=self.build_call_count,
            unresolved_reasons=tuple(self.unresolved_reasons),
        )


def _cvdp_parameter_override_analysis(
    harness_files: dict,
) -> CVDPParameterOverrideAnalysis:
    """Analyze each Python harness independently, preserving lexical scopes."""
    analyses: list[CVDPParameterOverrideAnalysis] = []
    for path, content in harness_files.items():
        if not str(path).endswith(".py"):
            continue
        source = textwrap.dedent(str(content))
        try:
            tree = ast.parse(source)
        except SyntaxError:
            if re.search(r"(?:\.\s*|\b)build\s*\(", source):
                fallback_names = _cvdp_parse_harness_usage({path: source})["params"]
                analyses.append(CVDPParameterOverrideAnalysis(
                    has_definite_overrides=False,
                    may_have_overrides=True,
                    parameter_names=frozenset(fallback_names),
                    unresolved=True,
                    build_call_count=1,
                    unresolved_reasons=("harness containing build() could not be parsed",),
                ))
            continue
        analyses.append(_CVDPHarnessParameterAnalyzer().analyze(tree))

    return CVDPParameterOverrideAnalysis(
        has_definite_overrides=any(item.has_definite_overrides for item in analyses),
        may_have_overrides=any(item.may_have_overrides for item in analyses),
        parameter_names=frozenset().union(
            *(item.parameter_names for item in analyses)
        ),
        unresolved=any(item.unresolved for item in analyses),
        build_call_count=sum(item.build_call_count for item in analyses),
        unresolved_reasons=tuple(dict.fromkeys(
            reason for item in analyses for reason in item.unresolved_reasons
        )),
    )


def _cvdp_parameter_overrides(
    harness_files: dict,
) -> tuple[bool, set[str], bool]:
    """Compatibility tuple: possible overrides, known names, and uncertainty."""
    analysis = _cvdp_parameter_override_analysis(harness_files)
    return (
        analysis.may_have_overrides,
        set(analysis.parameter_names),
        analysis.unresolved,
    )


@dataclass(frozen=True)
class _SVModuleParameter:
    """One overrideable parameter from a SystemVerilog module header."""

    name: str
    declaration: str


def _module_parameters(
    sv_code: str,
    module_name: str,
) -> tuple[_SVModuleParameter, ...]:
    """Parse the ordered parameter-port list of one SystemVerilog module.

    This deliberately looks only at the requested module's ``#(...)`` header;
    parameters on helper modules and ``localparam`` declarations in the body do
    not establish a parameterized DUT interface.  A declaration prefix is
    carried across comma-separated names, as SystemVerilog permits constructs
    such as ``parameter int WIDTH = 8, DEPTH = 4``.
    """
    text = re.sub(r"//.*", "", sv_code)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    match = re.search(rf"\bmodule\s+{re.escape(module_name)}\b", text)
    if not match:
        return ()
    idx = match.end()
    while idx < len(text) and text[idx].isspace():
        idx += 1
    if idx >= len(text) or text[idx] != "#":
        return ()
    idx += 1
    while idx < len(text) and text[idx].isspace():
        idx += 1
    if idx >= len(text) or text[idx] != "(":
        return ()
    end = _matching_paren(text, idx)
    if end < 0:
        return ()

    parameters: list[_SVModuleParameter] = []
    inherited_prefix: str | None = None
    for entry in _split_sv_commas(text[idx + 1:end]):
        raw = entry.strip()
        if not raw:
            continue

        explicit_kind = re.match(r"^(parameter|localparam)\b", raw)
        if explicit_kind and explicit_kind.group(1) == "localparam":
            inherited_prefix = None
            continue
        if explicit_kind is None and inherited_prefix is None:
            continue

        lhs = raw.split("=", 1)[0].strip()
        identifiers = list(re.finditer(r"[A-Za-z_]\w*", lhs))
        if not identifiers:
            continue
        name_match = identifiers[-1]
        name = name_match.group(0)

        if explicit_kind is not None:
            prefix = lhs[:name_match.start()].strip()
            if not prefix.startswith("parameter"):
                inherited_prefix = None
                continue
            inherited_prefix = prefix
            declaration = raw
        else:
            declaration = f"{inherited_prefix} {raw}"

        parameters.append(_SVModuleParameter(name=name, declaration=declaration))

    return tuple(parameters)


def _module_parameter_names(sv_code: str, module_name: str) -> set[str]:
    """Extract parameter names declared by one SystemVerilog module header."""
    return {parameter.name for parameter in _module_parameters(sv_code, module_name)}


def _module_semantic_text(sv_code: str, module_name: str) -> str:
    """Return a module's ports/body, excluding its parameter declarations."""
    for name, header, body in _module_records(sv_code):
        if name == module_name:
            return f"{header}\n{body}"
    return ""


def _cvdp_parameter_usage_text(sv_code: str, module_name: str) -> str:
    """Return only RTL text that can make a benchmark parameter functional.

    Sparkle emits generate-time guards for every native ``Nat`` parameter.  A
    guard necessarily mentions the parameter, but it does not make an otherwise
    fixed datapath parameterized.  Exclude those backend contract guards (and
    diagnostic strings) before deciding whether a CVDP sweep reaches ports,
    logic, state, memories, or child overrides.
    """
    semantic_text = _module_semantic_text(sv_code, module_name)
    generated_guard = re.compile(
        r"\bgenerate\b(?:(?!\bendgenerate\b).)*?\bbegin\s*:\s*"
        r"sparkle_invalid_(?:nat_parameter|dimension)_\d+\b"
        r"(?:(?!\bendgenerate\b).)*?\bend\s+endgenerate\b",
        flags=re.DOTALL,
    )
    semantic_text = generated_guard.sub("", semantic_text)
    return re.sub(r'"(?:\\.|[^"\\])*"', '""', semantic_text)


def _cvdp_localparam_dependencies(
    text: str,
) -> tuple[str, dict[str, str]]:
    """Remove localparam declarations and retain their dependency graph.

    A dead declaration such as ``localparam UNUSED = DEPTH`` must not make a
    fixed core look parameterized.  Conversely, a localparam used by a real
    width/state declaration must keep its transitive header-parameter
    dependencies live.
    """
    dependencies: dict[str, str] = {}
    declaration = re.compile(r"\blocalparam\b(?P<body>[^;]*);", re.DOTALL)

    def capture(match: re.Match[str]) -> str:
        body = match.group("body")
        for entry in _split_sv_commas(body):
            lhs, separator, rhs = entry.partition("=")
            if not separator:
                continue
            names = list(re.finditer(r"[A-Za-z_]\w*", lhs))
            if names:
                dependencies[names[-1].group(0)] = rhs
        return ""

    return declaration.sub(capture, text), dependencies


def _cvdp_parameters_in_text(text: str, parameter_names: set[str]) -> set[str]:
    return {
        name
        for name in parameter_names
        if re.search(rf"\b{re.escape(name)}\b", text)
    }


def _cvdp_parameter_dependency_closure(
    active: set[str],
    parameters: tuple[_SVModuleParameter, ...],
) -> set[str]:
    parameter_names = {parameter.name for parameter in parameters}
    active = set(active) & parameter_names
    declarations = {parameter.name: parameter.declaration for parameter in parameters}
    pending = list(active)
    while pending:
        name = pending.pop()
        declaration = declarations.get(name, "")
        rhs = declaration.split("=", 1)[1] if "=" in declaration else ""
        dependencies = _cvdp_parameters_in_text(rhs, parameter_names) - active
        active.update(dependencies)
        pending.extend(dependencies)
    return active


def _cvdp_active_module_parameters(
    sv_code: str,
    module_name: str,
    parameters: tuple[_SVModuleParameter, ...],
) -> set[str]:
    """Find parameters with a transitive path into emitted hardware.

    A default expression only makes its dependencies live when the parameter
    it defines is itself used by the module.  Treating every header dependency
    as active would accept dead chains such as ``UNUSED = DEPTH`` around an
    otherwise fixed core.
    """
    parameter_names = {parameter.name for parameter in parameters}
    usage_text, localparams = _cvdp_localparam_dependencies(
        _cvdp_parameter_usage_text(sv_code, module_name)
    )
    direct = _cvdp_parameters_in_text(
        usage_text,
        parameter_names,
    )
    live_localparams = _cvdp_parameters_in_text(usage_text, set(localparams))
    pending = list(live_localparams)
    while pending:
        name = pending.pop()
        rhs = localparams.get(name, "")
        direct.update(_cvdp_parameters_in_text(rhs, parameter_names))
        dependencies = _cvdp_parameters_in_text(rhs, set(localparams))
        dependencies -= live_localparams
        live_localparams.update(dependencies)
        pending.extend(dependencies)
    return _cvdp_parameter_dependency_closure(direct, parameters)


def _cvdp_unsupported_parameterization_detail(
    parameter_names: set[str],
    *,
    unresolved: bool = False,
    missing_parameters: set[str] | None = None,
) -> str:
    rendered = ", ".join(sorted(parameter_names)) or "unknown/dynamic parameters"
    qualifier = " (the full parameter set could not be resolved statically)" if unresolved else ""
    missing = set(missing_parameters or parameter_names)
    missing_text = ", ".join(sorted(missing)) or "unknown/dynamic parameters"
    return (
        f"{CVDP_PARAMETERIZATION_UNSUPPORTED}: the CVDP benchmark requires "
        f"{rendered}{qualifier}, but the Sparkle RTL core does not declare required "
        f"SystemVerilog module parameter(s): {missing_text}. Wrapping a fixed or "
        "partially parameterized core would not specialize its datapath, state, or "
        "memories. Emit those parameters on the core itself before using the CVDP "
        "adapter."
    )


def _cvdp_inactive_parameterization_detail(parameter_names: set[str]) -> str:
    rendered = ", ".join(sorted(parameter_names))
    return (
        f"{CVDP_PARAMETERIZATION_UNSUPPORTED}: the Sparkle RTL core declares "
        f"required module parameter(s) {rendered}, but does not use them in "
        "its ports, logic, state, memories, instance overrides, or parameter "
        "dependencies. A declaration-only parameter does not implement the "
        "benchmark sweep."
    )


def _cvdp_fixed_parameterized_port_detail(
    *,
    expected_name: str,
    expected_type: str,
    core_name: str,
    core_type: str,
    missing_parameters: set[str],
) -> str:
    rendered = ", ".join(sorted(missing_parameters))
    return (
        f"{CVDP_PARAMETERIZATION_UNSUPPORTED}: benchmark port '{expected_name}' "
        f"has parameterized type {expected_type}, but mapped Sparkle core port "
        f"'{core_name}' has type {core_type} and does not depend on required "
        f"parameter(s) {rendered}. Declaring parameters only on the module or "
        "adapter does not make a fixed-width datapath native-parameterized."
    )


@dataclass(frozen=True)
class _CVDPWrapperParameterContract:
    declarations: tuple[str, ...]
    forwarded_names: tuple[str, ...]


def _cvdp_wrapper_parameter_contract(
    *,
    design_name: str,
    sparkle_mod_name: str,
    ref_code: str,
    harness_files: dict,
    sv_code: str,
) -> _CVDPWrapperParameterContract:
    """Validate and render the parameter boundary between CVDP and Sparkle.

    Reference-module parameters are part of the benchmark interface even when
    the current harness happens to use only their defaults.  Harness parameters
    absent from the reference are also included when their names are statically
    known.  Every required name must be an actual parameter of the emitted core;
    merely adding parameters to the outer adapter is never accepted.
    """
    analysis = _cvdp_parameter_override_analysis(harness_files)
    reference_parameters = _module_parameters(ref_code, design_name)
    core_parameters = _module_parameters(sv_code, sparkle_mod_name)
    reference_by_name = {parameter.name: parameter for parameter in reference_parameters}
    core_by_name = {parameter.name: parameter for parameter in core_parameters}

    required_names = set(reference_by_name) | set(analysis.parameter_names)
    if analysis.unresolved and not required_names:
        raise CVDPAdapterContractError(
            _cvdp_unsupported_parameterization_detail(set(), unresolved=True)
        )

    missing = required_names - set(core_by_name)
    if missing:
        raise CVDPAdapterContractError(
            _cvdp_unsupported_parameterization_detail(
                required_names,
                unresolved=analysis.unresolved,
                missing_parameters=missing,
            )
        )

    # A declaration by itself is not native parameterization. Require a path
    # from every benchmark parameter into generated hardware, following only
    # live parameter-default dependencies such as DEPTH = 2 * WIDTH.
    active_names = _cvdp_active_module_parameters(
        sv_code,
        sparkle_mod_name,
        core_parameters,
    ) & required_names
    inactive_names = required_names - active_names
    if inactive_names:
        raise CVDPAdapterContractError(
            _cvdp_inactive_parameterization_detail(inactive_names)
        )

    declarations: list[str] = []
    forwarded_names: list[str] = []
    for parameter in reference_parameters:
        declarations.append(parameter.declaration)
        forwarded_names.append(parameter.name)
    for name in sorted(set(analysis.parameter_names) - set(reference_by_name)):
        declarations.append(core_by_name[name].declaration)
        forwarded_names.append(name)

    return _CVDPWrapperParameterContract(
        declarations=tuple(declarations),
        forwarded_names=tuple(forwarded_names),
    )


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
) -> str | None:
    """Generate a CVDP top wrapper matching cocotb's expected DUT interface."""
    parameter_contract = _cvdp_wrapper_parameter_contract(
        design_name=design_name,
        sparkle_mod_name=sparkle_mod_name,
        ref_code=ref_code,
        harness_files=harness_files,
        sv_code=sv_code,
    )

    usage = _cvdp_parse_harness_usage(harness_files)
    preferred_names = sorted(usage["ports"] | usage["params"])
    raw_ref_ports = _parse_ref_module_ports(ref_code, preferred_names) or []
    ref_ports = [(d, _cvdp_normalize_type(t), n) for d, t, n in raw_ref_ports]

    by_name = {n: (d, t, n) for d, t, n in ref_ports}
    expected_ports = list(ref_ports)
    ref_internal_arrays = _cvdp_internal_unpacked_arrays(ref_code, design_name)
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

    if len(sp_outputs) == 1:
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
    param_decls = list(parameter_contract.declarations)
    param_names = set(parameter_contract.forwarded_names)
    core_parameter_declarations = _module_parameters(sv_code, sparkle_mod_name)
    core_parameter_names = {
        parameter.name for parameter in core_parameter_declarations
    }

    wrapper_notes: list[str] = []
    if param_names:
        wrapper_notes.append(
            "benchmark parameters exposed by wrapper: "
            + ", ".join(sorted(param_names))
        )

    def require_native_parameterized_core_port(
        sp_port: tuple[str, str, str],
        expected: tuple[str, str, str] | None,
    ) -> None:
        if expected is None or not param_names:
            return
        _, sp_t, sp_n = sp_port
        _, exp_t, exp_n = expected
        expected_parameters = _cvdp_parameters_in_text(exp_t, param_names)
        direct_core_parameters = _cvdp_parameters_in_text(
            sp_t,
            core_parameter_names,
        )
        core_parameters = _cvdp_parameter_dependency_closure(
            direct_core_parameters,
            core_parameter_declarations,
        ) & param_names
        missing_parameters = expected_parameters - core_parameters
        if missing_parameters:
            raise CVDPAdapterContractError(
                _cvdp_fixed_parameterized_port_detail(
                    expected_name=exp_n,
                    expected_type=exp_t,
                    core_name=sp_n,
                    core_type=sp_t,
                    missing_parameters=missing_parameters,
                )
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
        require_native_parameterized_core_port(sp_port, matched_port)

    for sp_port in sp_outputs:
        _, _, sn = sp_port
        base = sn[5:] if sn.startswith("_gen_") else sn
        require_native_parameterized_core_port(
            sp_port,
            _cvdp_match_port(base, expected_outputs, direction="output"),
        )

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

    if parameter_contract.forwarded_names:
        lines.append(f"    {sparkle_mod_name} #(")
        lines.append(",\n".join(
            f"        .{name}({name})"
            for name in parameter_contract.forwarded_names
        ))
        lines.append("    ) sparkle_dut (")
    else:
        lines.append(f"    {sparkle_mod_name} sparkle_dut (")
    inst_conns = []
    for d, _, sn in sparkle_ports:
        if d == "output":
            inst_conns.append(f"        .{sn}({sn}_wire)")
            continue

        matched = _cvdp_clock_or_reset_match(sn, expected_inputs)
        if matched is None:
            base = sn[5:] if sn.startswith("_gen_") else sn
            match = _cvdp_match_port(base, expected_inputs, direction="input")
            matched = match[2] if match else None
        conn = _cvdp_bridge_reset_expr(sn, matched) if matched else "'0"
        inst_conns.append(f"        .{sn}({conn})")

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
            if len(remaining) == 1 and not field_assigned and not _cvdp_infer_concat_fields(sv_code, sp_out_n):
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
            lines.append(
                f"    // CVDP adapter fallback: output {rn} was not mapped from Sparkle output; "
                "drive zero so simulation reports a functional mismatch."
            )
            lines.append(f"    assign {rn} = '0;")

    lines.append("endmodule")
    return "\n".join(lines)


# ── Evaluator ────────────────────────────────────────────────────


class Evaluator:
    """Evaluate a Sparkle-generated .lean file: compile → extract SV → lint → sim → (synth+PPA) → (P&R+DRC+LVS)."""

    def __init__(self, project_root: Path = Path("."), enable_synth: bool = False, enable_pnr: bool = False, enable_drc: bool = False, enable_lvs: bool = False, enable_corners: bool = False, enable_gls: bool = False, lean_repl=None, dataset: str = "verilogeval", dataset_obj=None):
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

        if self.enable_synth:
            # Add siliconcrew/src to path for synthesis tools
            sc_src = self.project_root / "siliconcrew" / "src"
            if str(sc_src) not in sys.path:
                sys.path.insert(0, str(sc_src))

    def evaluate(self, prob_id: str, run_dir: Path) -> dict:
        """Run full evaluation for a single problem.

        Args:
            prob_id: e.g. "Prob001_zero"
            run_dir: directory for storing sim artifacts

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
            "has_sorry": True,       # True = unverified (default), False = formally verified
            "unsupported_parameterization": False,
            "terminal_capability_error": False,
            "repairable_parameterization_error": False,
            "parameterized_ppa_unsupported": False,
            "unsupported_reason": None,
            "detail": "",
        }

        # 1. Compile
        lean_file = self.project_root / "Generated" / f"{prob_id}.lean"
        if not lean_file.exists():
            result["detail"] = f"Lean file not found: Generated/{prob_id}.lean"
            return result

        sv_code = None

        if self.lean_repl is not None:
            # ── Fast path: use persistent REPL (~0.1s) ──
            repl_result = self.lean_repl.check_file(lean_file)
            if not repl_result.passed:
                result["detail"] = f"Compile failed:\n{repl_result.error_text[:1000]}"
                return result
            result["compile_pass"] = True
            result["has_sorry"] = not repl_result.complete
            repl_verilog = repl_result.verilog or ""
            # Use the same multi-module extraction as the lake-build path.  A
            # hierarchical Sparkle design prints one generated block per
            # module, followed by a non-Verilog success message.
            sv_code = self._extract_sv(repl_verilog)
            if not sv_code and repl_verilog:
                # Compatibility fallback for older REPL responses that contain
                # raw Verilog without Sparkle's generated-module marker.
                last_end = repl_verilog.rfind("endmodule")
                if last_end >= 0:
                    sv_code = repl_verilog[:last_end + len("endmodule")]
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
                result["detail"] = "lake build timeout"
                return result

            build_output = comp.stdout + "\n" + comp.stderr
            has_error = comp.returncode != 0 or re.search(r"error:", build_output)
            if has_error:
                result["detail"] = f"Compile failed:\n{build_output[:1000]}"
                return result

            result["compile_pass"] = True
            result["has_sorry"] = bool(re.search(r"declaration uses `sorry`", build_output))
            sv_code = self._extract_sv(build_output)

        # 2. Extract SystemVerilog
        if not sv_code:
            result["detail"] = "Compiled but could not extract SystemVerilog"
            return result

        result["sv_extracted"] = True
        sv_dir = run_dir / "sv"
        sv_dir.mkdir(parents=True, exist_ok=True)
        sv_file = sv_dir / f"{prob_id}.sv"
        sv_file.write_text(sv_code)

        # 3. Lint
        result["lint_pass"] = self._run_lint(sv_file)

        # Parse the generated design root (needed for sim wrappers and
        # synthesis).  Sparkle emits hierarchical designs in dependency order,
        # so the root is the last module.  Prefer the benchmark's exact target
        # name when it is present, since externally supplied multi-module RTL
        # is not required to follow Sparkle's ordering convention.
        preferred_top = None
        if self.dataset_obj is not None:
            try:
                preferred_top = self.dataset_obj.load_problem(prob_id).design_name
            except (FileNotFoundError, KeyError, ValueError, AttributeError):
                preferred_top = None
        sparkle_mod_name, sparkle_ports = self._generated_top_module_ports(
            sv_code, preferred_top
        )

        # 4. Simulation
        sim_status, mismatches, detail = self._run_sim(
            prob_id, sv_code, sparkle_mod_name, sparkle_ports, run_dir
        )
        result["sim_status"] = sim_status
        result["sim_mismatches"] = mismatches
        result["detail"] = detail
        if CVDP_PARAMETERIZATION_UNSUPPORTED in detail:
            result["unsupported_parameterization"] = True
            # Native Sparkle module parameters are supported. A fixed or
            # incompletely parameterized candidate is therefore repairable by
            # the agent and must remain in the simulation-feedback loop.
            result["terminal_capability_error"] = False
            result["repairable_parameterization_error"] = True
            result["unsupported_reason"] = detail
            return result

        if self.dataset_name == "cvdp" and self.dataset_obj is not None:
            info = self.dataset_obj.load_problem(prob_id)
            parameter_analysis = _cvdp_parameter_override_analysis(
                info.metadata.get("harness_files", {})
            )
            reference_has_parameters = bool(
                _module_parameters(info.ref_code or "", info.design_name)
            )
            core_has_parameters = bool(
                _module_parameters(sv_code, sparkle_mod_name)
            )
            if (
                parameter_analysis.may_have_overrides
                or reference_has_parameters
                or core_has_parameters
            ):
                result.update({
                    "parameterized_ppa_unsupported": True,
                    "synth_status": "not_run_parameterized_sweep",
                    "ppa_status": "unsupported_parameter_sweep",
                    "ppa_error": (
                        "PPA was not run: the CVDP reference, harness, or "
                        "generated core uses native SystemVerilog parameters, "
                        "while the current backend "
                        "would synthesize only one module-default configuration. "
                        "Per-configuration synthesis and reporting are required "
                        "before these metrics are meaningful."
                    ),
                })
                return result

        # 5. Synthesis + PPA (optional)
        if self.enable_synth and result["sv_extracted"]:
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
        """Extract the last complete Sparkle SystemVerilog design.

        ``#synthesizeVerilogDesign`` prints a generated block for every child
        and then the top module.  Keep every block in that command's output;
        stopping at the first ``endmodule`` silently drops the design root.
        When a Lean file contains multiple synthesis commands, the final
        completed command is the artifact the evaluator should consume.
        """
        completed_groups: list[list[str]] = []
        current_group: list[str] = []
        current_module: list[str] = []
        capturing_module = False

        for line in build_output.splitlines():
            # Strip "info: Generated/Prob001_zero.lean:12:0: " prefix.
            cleaned = re.sub(r"^info:\s+\S+\s+", "", line)

            if "// Generated by Sparkle HDL" in cleaned:
                # A new marker before an endmodule means the preceding block
                # was malformed/incomplete; do not blend it into valid RTL.
                current_module = [cleaned]
                capturing_module = True
                continue

            if capturing_module:
                current_module.append(cleaned)
                if cleaned.strip() == "endmodule":
                    current_group.append("\n".join(current_module))
                    current_module = []
                    capturing_module = False
                continue

            if "Verilog successfully generated!" in cleaned and current_group:
                completed_groups.append(current_group)
                current_group = []

        # REPL messages may omit the human-readable success line.  A group of
        # complete modules is still safe to consume.
        if current_group:
            completed_groups.append(current_group)

        if not completed_groups:
            return ""
        return "\n\n".join(completed_groups[-1])

    @staticmethod
    def _generated_top_module_ports(
        sv_code: str,
        preferred_module: str | None = None,
    ) -> tuple[str, list[tuple[str, str, str]]]:
        """Return the generated design root and its ports.

        An exact benchmark target wins when present.  Otherwise use Sparkle's
        dependency-order invariant: submodules are emitted before their parent,
        making the final module the top-level design.
        """
        records = _module_records(sv_code)
        if not records:
            return "", []
        names = {name for name, _, _ in records}
        top_name = (
            preferred_module
            if preferred_module and preferred_module in names
            else records[-1][0]
        )
        return parse_module_ports(sv_code, module_name=top_name)

    @staticmethod
    def _run_lint(sv_file: Path) -> bool:
        """Run iverilog lint check."""
        try:
            comp = subprocess.run(
                ["iverilog", "-t", "null", "-g2012", str(sv_file)],
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
    ) -> tuple[str, int, str]:
        """Run a CVDP cocotb harness in its Docker simulation image."""
        if self.dataset_obj is None:
            return "sim_error", -1, "CVDP dataset object not set on evaluator"

        info = self.dataset_obj.load_problem(prob_id)
        harness_files = info.metadata.get("harness_files", {})
        verilog_sources = info.metadata.get("verilog_sources", [])
        design_name = info.design_name
        if not harness_files:
            return "sim_error", -1, "CVDP harness files missing"
        if not verilog_sources:
            return "sim_error", -1, "CVDP VERILOG_SOURCES missing"

        target_mod_name, target_ports = parse_module_ports(sv_code, module_name=design_name)
        parameter_analysis = _cvdp_parameter_override_analysis(harness_files)
        if direct_top:
            if target_mod_name != design_name:
                return (
                    "sim_error",
                    -1,
                    f"Direct CVDP SystemVerilog candidate must declare top module "
                    f"'{design_name}', found '{target_mod_name or '<none>'}'.",
                )
            reference_parameters = _module_parameter_names(
                info.ref_code or "", design_name
            )
            if parameter_analysis.may_have_overrides or reference_parameters:
                declared_parameters = _module_parameter_names(sv_code, design_name)
                required_parameters = (
                    set(parameter_analysis.parameter_names)
                    | reference_parameters
                )
                missing_parameters = (
                    required_parameters - declared_parameters
                )
                if missing_parameters:
                    return (
                        "sim_error",
                        -1,
                        f"{CVDP_PARAMETERIZATION_UNSUPPORTED}: direct SystemVerilog "
                        f"top '{design_name}' does not declare harness parameter(s): "
                        f"{', '.join(sorted(missing_parameters))}.",
                    )
                if parameter_analysis.unresolved and not declared_parameters:
                    return (
                        "sim_error",
                        -1,
                        _cvdp_unsupported_parameterization_detail(
                            set(), unresolved=True
                        ),
                    )
                active_parameters = _cvdp_active_module_parameters(
                    sv_code,
                    design_name,
                    _module_parameters(sv_code, design_name),
                ) & required_parameters
                inactive_parameters = required_parameters - active_parameters
                if inactive_parameters:
                    return (
                        "sim_error",
                        -1,
                        _cvdp_inactive_parameterization_detail(
                            inactive_parameters
                        ),
                    )
                # Unknown **kwargs are safe to pass through for a genuine
                # parameterized SystemVerilog top: the simulator/elaborator can
                # validate the concrete keys.  This exception does not apply to
                # a fixed Sparkle core hidden behind an adapter.
        else:
            preflight_core_name = (
                target_mod_name
                if target_mod_name == design_name
                else (sparkle_mod_name or target_mod_name)
            )
            try:
                _cvdp_wrapper_parameter_contract(
                    design_name=design_name,
                    sparkle_mod_name=preflight_core_name,
                    ref_code=info.ref_code,
                    harness_files=harness_files,
                    sv_code=sv_code,
                )
            except CVDPAdapterContractError as error:
                return "sim_error", -1, str(error)

        sim_dir = run_dir / "cvdp_sim" / prob_id
        if sim_dir.exists():
            shutil.rmtree(sim_dir)
        sim_dir.mkdir(parents=True, exist_ok=True)

        image_name = os.environ.get("OSS_SIM_IMAGE", "nvidia/cvdp-sim:v1.0.0")
        for rel_path, content in harness_files.items():
            out_path = sim_dir / rel_path
            out_path.parent.mkdir(parents=True, exist_ok=True)
            if rel_path == "docker-compose.yml":
                content = str(content).replace("__OSS_SIM_IMAGE__", image_name)
            out_path.write_text(str(content))

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

                try:
                    wrapper = generate_cvdp_wrapper(
                        design_name=design_name,
                        sparkle_mod_name=inner_name,
                        sparkle_ports=sparkle_ports,
                        ref_code=info.ref_code,
                        harness_files=harness_files,
                        sv_code=sv_code,
                    )
                except CVDPAdapterContractError as error:
                    return "sim_error", -1, str(error)
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
        compose_text = (sim_dir / "docker-compose.yml").read_text(errors="replace")
        service_match = re.search(r"^\s{2}([A-Za-z0-9_-]+):\s*$", compose_text, re.MULTILINE)
        if service_match:
            service = service_match.group(1)

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
            cleanup = subprocess.run(
                compose + ["-f", "docker-compose.yml", "down", "-v"],
                cwd=str(sim_dir),
                capture_output=True, text=True, timeout=60,
            )
        except subprocess.TimeoutExpired:
            return "sim_error", -1, "CVDP docker simulation timeout"
        except FileNotFoundError as e:
            return "sim_error", -1, f"Docker compose not found: {e}"

        output = proc.stdout + proc.stderr
        if "cleanup" in locals() and cleanup.stdout:
            output += "\n[CLEANUP]\n" + cleanup.stdout
        (sim_dir / "cvdp_output.txt").write_text(output)
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

        try:
            from tools.run_docker import run_docker_command
        except ImportError as e:
            result["synth_error"] = f"Cannot import siliconcrew tools: {e}"
            return result

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
        for d in [results_dir, logs_dir, reports_dir]:
            if d.exists():
                shutil.rmtree(d)
            d.mkdir(parents=True, exist_ok=True)

        volumes = [
            f"{results_dir}:/OpenROAD-flow-scripts/flow/results",
            f"{logs_dir}:/OpenROAD-flow-scripts/flow/logs",
            f"{reports_dir}:/OpenROAD-flow-scripts/flow/reports",
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
        from tools.run_docker import run_docker_command

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
        from tools.run_docker import run_docker_command

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
        from tools.run_docker import run_docker_command

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
        from tools.run_docker import run_docker_command

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
