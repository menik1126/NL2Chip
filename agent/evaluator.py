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
import hashlib
import json
import math
import os
import re
import signal
import shutil
import subprocess
import sys
import textwrap
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from universal_theorem import (
        UNIVERSAL_THEOREM_MARKER,
        extract_universal_theorem_evidence,
        independently_revalidate_universal_theorems,
    )
except ModuleNotFoundError:  # package-style ``import agent.evaluator``
    from .universal_theorem import (
        UNIVERSAL_THEOREM_MARKER,
        extract_universal_theorem_evidence,
        independently_revalidate_universal_theorems,
    )


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
CVDP_ADAPTER_ERROR = "CVDP_ADAPTER_ERROR"
_CVDP_EXACT_CLOCK_PORT_NAMES = frozenset({
    "clk", "clock", "aclk", "clk_i", "clk_in", "i_clk",
})
# A mutable Docker tag is not a persistent cache identity.  Keep duplicate
# work single-flight within this process, but force a new cache namespace on
# the next invocation unless the caller supplies an immutable image digest.
_UNPINNED_ORFS_PROCESS_NONCE = uuid.uuid4().hex


def _cvdp_incomplete_pytest_summary(output: str) -> str | None:
    """Return a stable summary when pytest did not execute every test."""
    incomplete = sorted(set(re.findall(
        r"\b([1-9][0-9]*)\s+(skipped|xfailed|xpassed)\b",
        output,
        re.IGNORECASE,
    )))
    if not incomplete:
        return None
    return ", ".join(f"{count} {kind.lower()}" for count, kind in incomplete)


def _extract_universal_theorem_evidence(
    compiler_output: str,
    source: str,
    source_artifact: str,
) -> list[dict]:
    """Parse a candidate marker as an untrusted revalidation request.

    Evaluator results never attach this output directly; the dedicated
    ``sparkle-certify`` process must independently revalidate it first.
    """
    return extract_universal_theorem_evidence(
        compiler_output, source, source_artifact
    )


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

def _exact_module_text(sv_code: str, module_name: str | None) -> str:
    """Return only one named module, never falling back to a neighbour."""
    if module_name is None:
        return sv_code
    for name, header, body in _module_records(sv_code):
        if name == module_name:
            return f"{header}\n{body}"
    return ""




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
    module_name: str | None = None,
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
        if module_name is not None and mod_match.group(1) != module_name:
            continue
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

def _parse_ref_module_ports_strict(
    ref_code: str,
    module_name: str,
) -> list[tuple[str, str, str]] | None:
    """Parse one complete module interface, rejecting partial recovery."""
    record = next(
        (candidate for candidate in _module_records(ref_code)
         if candidate[0] == module_name),
        None,
    )
    if record is None:
        return None
    _, raw_header, raw_body = record
    header = _strip_sv_comments(raw_header)
    body = _strip_sv_comments(raw_body)
    entries = _split_sv_commas(header)
    ports: list[tuple[str, str, str]] = []

    if any(re.search(r"\b(?:input|output)\b", entry) for entry in entries):
        current_direction: str | None = None
        current_type = ""
        for entry in entries:
            match = re.fullmatch(
                r"\s*(?:(input|output)\s+)?"
                r"(?:(?:var\s+)?(?:reg|logic|wire|bit)\s+)?"
                r"(?:(signed|unsigned)\s+)?"
                r"((?:\s*\[[^\]]+\])*)\s*"
                r"([A-Za-z_]\w*)\s*",
                entry,
                re.DOTALL,
            )
            if not match:
                return None
            if match.group(1):
                current_direction = match.group(1)
                current_type = ""
            signing = match.group(2)
            dimensions = match.group(3).strip()
            if signing or dimensions:
                current_type = " ".join(
                    part for part in (signing, dimensions) if part
                )
            if current_direction is None:
                return None
            ports.append((
                current_direction,
                f" {current_type}" if current_type else "",
                match.group(4),
            ))
        return ports

    names: list[str] = []
    for entry in entries:
        match = re.fullmatch(r"\s*([A-Za-z_]\w*)\s*", entry)
        if not match:
            return None
        names.append(match.group(1))

    port_info: dict[str, tuple[str, str]] = {}
    for match in re.finditer(
        r"\b(input|output)\s+"
        r"(?:(?:var\s+)?(?:reg|logic|wire|bit)\s+)?"
        r"(?:(signed|unsigned)\s+)?"
        r"((?:\s*\[[^\]]+\])*)\s*([^;]+);",
        body,
        re.DOTALL,
    ):
        signing = match.group(2)
        dimensions = match.group(3).strip()
        typ = " ".join(part for part in (signing, dimensions) if part)
        for name_entry in _split_sv_commas(match.group(4)):
            name_match = re.fullmatch(r"\s*([A-Za-z_]\w*)\s*", name_entry)
            if name_match:
                port_info[name_match.group(1)] = (
                    match.group(1),
                    f" {typ}" if typ else "",
                )
    if any(name not in port_info for name in names):
        return None
    return [
        (port_info[name][0], port_info[name][1], name)
        for name in names
    ]



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


def _cvdp_hierarchical_field_expr(
    expr: str,
    instance_name: str = "sparkle_dut",
) -> str | None:
    """Render a simple tuple leaf with only self-contained numeric selects."""
    text = expr.strip()
    match = re.fullmatch(
        r"([A-Za-z_]\w*)((?:\s*\[\s*\d+\s*(?::\s*\d+\s*)?\])*)",
        text,
    )
    if not match:
        return None
    return f"{instance_name}.{match.group(1)}{match.group(2)}"


def _cvdp_tuple_field_width_expr(expr: str) -> str | None:
    hierarchical = _cvdp_hierarchical_field_expr(expr)
    if hierarchical:
        return f"$bits({hierarchical})"
    sized = re.fullmatch(
        r"\s*(\d+)\s*'\s*[sS]?[bBoOdDhH][0-9a-fA-F_xXzZ?]+\s*",
        expr,
    )
    return sized.group(1) if sized else None

def _cvdp_tuple_field_shape_type(
    expr: str,
    type_lookup: dict[str, str],
) -> str | None:
    """Return a local mirror type only for an unselected tuple signal."""
    match = re.fullmatch(r"\s*([A-Za-z_]\w*)\s*", expr)
    if not match:
        return None
    name = match.group(1)
    typ = type_lookup.get(name)
    if name.startswith("_gen_"):
        typ = typ or type_lookup.get(name[5:])
    return typ


def _cvdp_signal_type_lookup(
    sv_code: str,
    sparkle_ports: list[tuple[str, str, str]],
    module_name: str | None = None,
) -> dict[str, str]:
    """Collect widths for generated ports and one exact module's signals."""
    lookup: dict[str, str] = {}

    def remember(name: str, typ: str) -> None:
        norm = _cvdp_normalize_type(typ)
        lookup.setdefault(name, norm)
        if name.startswith("_gen_"):
            lookup.setdefault(name[5:], norm)

    for _, typ, name in sparkle_ports:
        remember(name, typ)

    clean = _strip_sv_comments(_exact_module_text(sv_code, module_name))
    for m in re.finditer(
        r"\b(?P<base>logic|wire|reg|bit)\b\s*"
        r"(?P<signing>signed|unsigned)?\s*"
        r"(?P<packed>\[[^\]]+\])?\s*(?P<decls>[^;]+);",
        clean,
        re.DOTALL,
    ):
        typ = " ".join(
            part for part in (
                m.group("base"),
                m.group("signing"),
                m.group("packed"),
            )
            if part
        )
        for decl in _cvdp_split_commas(m.group("decls")):
            name_match = re.match(r"\s*([A-Za-z_]\w*)", decl.split("=", 1)[0].strip())
            if name_match:
                remember(name_match.group(1), typ)
    return lookup


def _cvdp_internal_unpacked_arrays(
    sv_code: str,
    module_name: str | None = None,
) -> dict[str, tuple[str, str]]:
    """Return internal unpacked arrays as name -> (element type, range)."""
    if module_name is None:
        records = _module_records(sv_code)
        body = records[0][2] if records else ""
    else:
        record = next(
            (candidate for candidate in _module_records(sv_code)
             if candidate[0] == module_name),
            None,
        )
        body = record[2] if record is not None else ""
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
    related_names: tuple[str, ...] = (),
) -> tuple[str, str, str] | None:
    """Map a packed concat field back to the benchmark output it represents."""
    field_name = _cvdp_expr_signal_name(field_expr)
    if not field_name:
        return None
    candidates = [p for p in expected_outputs if p[2] not in used_outputs]

    # Generated tuple leaves are often mux/register temporaries whose RHS
    # still carries the semantic output name.  Preserve the concat as the
    # ordering boundary, but use that bounded assignment provenance for names.
    provenance_matches: dict[str, tuple[str, str, str]] = {}
    for related_name in related_names:
        match = _cvdp_match_port(
            related_name, candidates, direction="output"
        )
        if match:
            provenance_matches[match[2]] = match
    if len(provenance_matches) == 1:
        return next(iter(provenance_matches.values()))
    if len(provenance_matches) > 1:
        return None

    match = _cvdp_match_port(field_name, candidates, direction="output")
    if match:
        return match

    return None


def _cvdp_concat_assignments(
    sv_code: str,
    module_name: str | None = None,
) -> dict[str, list[str]]:
    clean = _strip_sv_comments(_exact_module_text(sv_code, module_name))
    assigns: dict[str, list[str]] = {}
    for m in re.finditer(
        r"\bassign\s+([A-Za-z_]\w*)\s*=\s*\{([^;]+)\}\s*;",
        clean,
        re.DOTALL,
    ):
        assigns[m.group(1)] = _cvdp_split_commas(m.group(2))
    return assigns


def _cvdp_assignment_provenance(
    sv_code: str,
    module_name: str | None = None,
) -> dict[str, tuple[str, ...]]:
    """Return only semantics-preserving continuous-assignment aliases.

    Identifier bags from arithmetic, inversion, and mux RHS expressions are
    not provenance: they transform values. Only a bare identifier or numeric
    constant select can carry a benchmark name through a temporary.
    """
    clean = _strip_sv_comments(_exact_module_text(sv_code, module_name))
    provenance: dict[str, tuple[str, ...]] = {}
    for match in re.finditer(
        r"\bassign\s+([A-Za-z_]\w*)\s*=\s*([^;]+);",
        clean,
        re.DOTALL,
    ):
        alias = re.fullmatch(
            r"\s*([A-Za-z_]\w*)\s*",
            match.group(2),
        )
        if alias:
            provenance[match.group(1)] = (alias.group(1),)
    return provenance



_CVDP_SV_SCOPE_TOKEN_RE = re.compile(
    r'"(?:\\.|[^"\\])*"'
    r"|[A-Za-z_$][A-Za-z0-9_$]*"
    r"|===|!==|==\?|!=\?|<<<|>>>|<<=|>>="
    r"|::|<=|>=|==|!=|&&|\|\||\+\+|--|\+=|-=|\*=|/=|%=|&=|\|=|\^=|<<|>>"
    r"|[^\s]",
    re.DOTALL,
)


def _cvdp_sv_alias_scope(
    body: str,
) -> tuple[list[tuple[str, int, int]], list[bool]] | None:
    """Tokenize a module body and mark every non-module-item token unsafe.

    Only the small structural subset needed to prove a continuous alias is
    accepted. Procedural regions, explicit or implicit generate constructs,
    and nested declaration regions are marked unsafe as whole statements.
    Unbalanced or unknown statements fail closed.
    """
    tokens = [
        (match.group(0), match.start(), match.end())
        for match in _CVDP_SV_SCOPE_TOKEN_RE.finditer(body)
    ]
    lowered = [token[0].lower() for token in tokens]
    unsafe = [False] * len(tokens)

    def mark(start: int, end: int) -> None:
        for index in range(start, min(end, len(unsafe))):
            unsafe[index] = True

    def skip_balanced(start: int, opener: str, closer: str) -> int | None:
        if start >= len(tokens) or tokens[start][0] != opener:
            return None
        depth = 0
        for index in range(start, len(tokens)):
            text = tokens[index][0]
            if text == opener:
                depth += 1
            elif text == closer:
                depth -= 1
                if depth == 0:
                    return index + 1
        return None

    def skip_keyword_block(
        start: int,
        openers: frozenset[str],
        closer: str,
    ) -> int | None:
        depth = 0
        for index in range(start, len(tokens)):
            keyword = lowered[index]
            if keyword in openers:
                depth += 1
            elif keyword == closer:
                depth -= 1
                if depth == 0:
                    return index + 1
        return None

    def skip_label(index: int) -> int:
        if index + 1 < len(tokens) and tokens[index][0] == ":":
            return index + 2
        return index

    def skip_attributes(index: int) -> int | None:
        while (
            index + 1 < len(tokens)
            and tokens[index][0] == "("
            and tokens[index + 1][0] == "*"
        ):
            cursor = index + 2
            while cursor + 1 < len(tokens):
                if tokens[cursor][0] == "*" and tokens[cursor + 1][0] == ")":
                    index = cursor + 2
                    break
                cursor += 1
            else:
                return None
        return index

    def skip_controls(index: int) -> int | None:
        while index < len(tokens) and tokens[index][0] in {"@", "#"}:
            index += 1
            if index >= len(tokens):
                return None
            if tokens[index][0] == "(":
                balanced = skip_balanced(index, "(", ")")
                if balanced is None:
                    return None
                index = balanced
            else:
                index += 1
        return index

    def skip_statement(start: int) -> int | None:
        index = skip_attributes(start)
        if index is None:
            return None
        index = skip_controls(index)
        if index is None or index >= len(tokens):
            return None
        keyword = lowered[index]

        if tokens[index][0] == ";":
            return index + 1
        if keyword == "begin":
            end = skip_keyword_block(index, frozenset({"begin"}), "end")
            return skip_label(end) if end is not None else None
        if keyword == "fork":
            depth = 0
            for cursor in range(index, len(tokens)):
                nested = lowered[cursor]
                if nested == "fork":
                    depth += 1
                elif nested in {"join", "join_any", "join_none"}:
                    depth -= 1
                    if depth == 0:
                        return cursor + 1
            return None
        if keyword == "if":
            condition = skip_balanced(index + 1, "(", ")")
            if condition is None:
                return None
            consequent = skip_statement(condition)
            if consequent is None:
                return None
            if consequent < len(tokens) and lowered[consequent] == "else":
                return skip_statement(consequent + 1)
            return consequent
        if keyword in {"for", "foreach", "while", "repeat"}:
            control = skip_balanced(index + 1, "(", ")")
            return skip_statement(control) if control is not None else None
        if keyword == "forever":
            return skip_statement(index + 1)
        if keyword == "do":
            statement = skip_statement(index + 1)
            if (
                statement is None
                or statement >= len(tokens)
                or lowered[statement] != "while"
            ):
                return None
            condition = skip_balanced(statement + 1, "(", ")")
            if condition is None:
                return None
            if condition < len(tokens) and tokens[condition][0] == ";":
                condition += 1
            return condition
        if keyword in {"case", "casex", "casez", "randcase"}:
            end = skip_keyword_block(
                index,
                frozenset({"case", "casex", "casez", "randcase"}),
                "endcase",
            )
            return skip_label(end) if end is not None else None

        paren_depth = 0
        bracket_depth = 0
        brace_depth = 0
        for cursor in range(index, len(tokens)):
            text = tokens[cursor][0]
            if text == "(":
                paren_depth += 1
            elif text == ")":
                paren_depth -= 1
            elif text == "[":
                bracket_depth += 1
            elif text == "]":
                bracket_depth -= 1
            elif text == "{":
                brace_depth += 1
            elif text == "}":
                brace_depth -= 1
            elif (
                text == ";"
                and paren_depth == bracket_depth == brace_depth == 0
            ):
                return cursor + 1
            elif (
                lowered[cursor] == "begin"
                and paren_depth == bracket_depth == brace_depth == 0
            ):
                return None
            if min(paren_depth, bracket_depth, brace_depth) < 0:
                return None
        return None

    paired_regions = {
        "generate": (frozenset({"generate"}), "endgenerate"),
        "function": (frozenset({"function"}), "endfunction"),
        "task": (frozenset({"task"}), "endtask"),
        "class": (frozenset({"class"}), "endclass"),
        "checker": (frozenset({"checker"}), "endchecker"),
        "covergroup": (frozenset({"covergroup"}), "endgroup"),
        "property": (frozenset({"property"}), "endproperty"),
        "sequence": (frozenset({"sequence"}), "endsequence"),
        "specify": (frozenset({"specify"}), "endspecify"),
        "clocking": (frozenset({"clocking"}), "endclocking"),
    }
    procedural_starters = {
        "initial",
        "final",
        "always",
        "always_comb",
        "always_ff",
        "always_latch",
    }

    index = 0
    while index < len(tokens):
        if unsafe[index]:
            index += 1
            continue
        keyword = lowered[index]
        if keyword in paired_regions:
            end = skip_keyword_block(index, *paired_regions[keyword])
            if end is None:
                return None
            mark(index, end)
            index = end
            continue
        if keyword in procedural_starters:
            statement = skip_controls(index + 1)
            end = skip_statement(statement) if statement is not None else None
            if end is None:
                return None
            mark(index, end)
            index = end
            continue
        index += 1

    implicit_generate = {"if", "for", "case", "casex", "casez"}
    index = 0
    while index < len(tokens):
        if unsafe[index]:
            index += 1
            continue
        keyword = lowered[index]
        if keyword in implicit_generate or keyword == "begin":
            end = skip_statement(index)
            if end is None:
                return None
            mark(index, end)
            index = end
            continue
        if keyword in {
            "else",
            "end",
            "endcase",
            "endgenerate",
            "endfunction",
            "endtask",
            "endclass",
            "endchecker",
            "endgroup",
            "endproperty",
            "endsequence",
            "endspecify",
            "endclocking",
            "join",
            "join_any",
            "join_none",
        }:
            return None
        index += 1
    return tokens, unsafe

def _cvdp_unique_plain_alias_chain(
    sv_code: str,
    module_name: str,
    *,
    source_name: str,
    semantic_name: str,
    max_depth: int = 32,
) -> tuple[str, ...] | None:
    """Prove one top-level, non-reused bare-identifier alias chain.

    This deliberately recognizes less than SystemVerilog permits. Every edge
    must be a module-scope continuous assignment with one bare identifier on
    the RHS. Duplicate drivers, generate/function/task-local assignments,
    cycles, operations, selects, constants, and alias fanout all fail closed.
    """
    record = next(
        (
            candidate
            for candidate in _module_records(sv_code)
            if candidate[0] == module_name
        ),
        None,
    )
    if record is None:
        return None
    body = _strip_sv_comments(record[2])
    scope = _cvdp_sv_alias_scope(body)
    if scope is None:
        return None
    tokens, unsafe = scope

    def is_module_scope(position: int) -> bool:
        for index, (_, start, end) in enumerate(tokens):
            if start <= position < end:
                return not unsafe[index]
            if start > position:
                break
        return False

    _, module_ports = parse_module_ports(sv_code, module_name=module_name)
    declared_names = {name for _, _, name in module_ports}
    for declaration in re.finditer(
        r"\b(?:logic|wire|reg|bit)\b\s*"
        r"(?:signed|unsigned)?\s*(?:\[[^\]]+\])?\s*([^;]+);",
        body,
        re.DOTALL,
    ):
        if not is_module_scope(declaration.start()):
            continue
        for entry in _cvdp_split_commas(declaration.group(1)):
            lhs = entry.split("=", 1)[0].strip()
            name_match = re.match(r"([A-Za-z_]\w*)", lhs)
            if name_match:
                declared_names.add(name_match.group(1))

    by_lhs: dict[str, list[tuple[str, bool]]] = {}
    rhs_users: dict[str, set[str]] = {}
    safe_assign_tokens: set[int] = set()
    for index, (text, _, _) in enumerate(tokens):
        if text.lower() != "assign":
            continue
        cursor = index + 1
        if (
            cursor >= len(tokens)
            or re.fullmatch(r"[A-Za-z_]\w*", tokens[cursor][0]) is None
        ):
            continue
        lhs = tokens[cursor][0]
        cursor += 1
        if cursor >= len(tokens) or tokens[cursor][0] != "=":
            by_lhs.setdefault(lhs, []).append(("", False))
            continue
        rhs_start = cursor + 1
        cursor = rhs_start
        while cursor < len(tokens) and tokens[cursor][0] != ";":
            cursor += 1
        if cursor >= len(tokens) or rhs_start == cursor:
            return None
        rhs = body[tokens[rhs_start][1]:tokens[cursor][1]].strip()
        at_module_scope = not unsafe[index]
        by_lhs.setdefault(lhs, []).append((rhs, at_module_scope))
        if at_module_scope:
            safe_assign_tokens.update(range(index, cursor + 1))
        for identifier in re.findall(r"\b[A-Za-z_]\w*\b", rhs):
            rhs_users.setdefault(identifier, set()).add(lhs)

    assignment_ops = {
        "=", "<=", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=",
    }
    for index, (text, _, _) in enumerate(tokens):
        if index in safe_assign_tokens:
            continue
        if text.lower() in {"deassign", "force", "release"}:
            if (
                index + 1 < len(tokens)
                and re.fullmatch(r"[A-Za-z_]\w*", tokens[index + 1][0])
            ):
                by_lhs.setdefault(tokens[index + 1][0], []).append(("", False))
            continue
        if re.fullmatch(r"[A-Za-z_]\w*", text) is None:
            continue
        cursor = index + 1
        while cursor < len(tokens) and tokens[cursor][0] == "[":
            depth = 1
            cursor += 1
            while cursor < len(tokens) and depth:
                if tokens[cursor][0] == "[":
                    depth += 1
                elif tokens[cursor][0] == "]":
                    depth -= 1
                cursor += 1
            if depth:
                return None
        if cursor < len(tokens) and tokens[cursor][0] in assignment_ops:
            by_lhs.setdefault(text, []).append(("", False))
        elif cursor < len(tokens) and tokens[cursor][0] in {"++", "--"}:
            by_lhs.setdefault(text, []).append(("", False))

    chain = [source_name]
    current = source_name
    seen: set[str] = set()
    for _ in range(max_depth):
        if current in seen:
            return None
        seen.add(current)
        drivers = by_lhs.get(current, [])
        if len(drivers) != 1:
            return None
        rhs, at_module_scope = drivers[0]
        if not at_module_scope:
            return None
        alias = re.fullmatch(r"([A-Za-z_]\w*)", rhs)
        if alias is None:
            return None
        dependency = alias.group(1)
        if dependency not in declared_names:
            return None
        if rhs_users.get(dependency, set()) != {current}:
            return None
        chain.append(dependency)
        if dependency == semantic_name:
            return tuple(chain)
        current = dependency
    return None


def _cvdp_related_signal_names(
    expr: str,
    provenance: dict[str, tuple[str, ...]],
    *,
    max_depth: int = 6,
) -> tuple[str, ...]:
    """Collect bounded generated-signal provenance for one tuple leaf."""
    root = _cvdp_expr_signal_name(expr)
    if not root:
        return ()
    ordered: list[str] = []
    seen: set[str] = set()

    def visit(name: str, depth: int) -> None:
        if name in seen or depth > max_depth:
            return
        seen.add(name)
        ordered.append(name)
        for dependency in provenance.get(name, ()):
            visit(dependency, depth + 1)

    visit(root, 0)
    return tuple(ordered)


def _cvdp_infer_concat_fields(
    sv_code: str,
    sp_out_name: str,
    module_name: str | None = None,
) -> list[str] | None:
    """Infer raw SV fields packed into a Sparkle bundled output."""
    clean = _strip_sv_comments(_exact_module_text(sv_code, module_name))
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
    sparkle_mod_name: str | None = None,
) -> list[tuple[tuple[str, str, str], str, str | None]]:
    """Return MSB-first mapping from expected output ports to packed fields."""
    fields = _cvdp_infer_concat_fields(sv_code, sp_out_name, sparkle_mod_name) or []
    if not fields:
        return []
    type_lookup = _cvdp_signal_type_lookup(sv_code, sparkle_ports, sparkle_mod_name)
    provenance = _cvdp_assignment_provenance(sv_code, sparkle_mod_name)
    used: set[str] = set()
    mapping: list[tuple[tuple[str, str, str], str, str | None]] = []
    for field in fields:
        field_name = _cvdp_expr_signal_name(field) or ""
        field_type = type_lookup.get(field_name)
        if field_name.startswith("_gen_"):
            field_type = field_type or type_lookup.get(field_name[5:])
        matched = _cvdp_match_output_for_field(
            field,
            expected_outputs,
            used,
            _cvdp_related_signal_names(field, provenance),
        )
        if not matched:
            continue
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
    sparkle_mod_name: str | None = None,
) -> list[tuple[str, int, str]]:
    """Map a packed Sparkle tuple to every benchmark output when provable.

    Sparkle lowers tuples MSB-first. Only semantic field/provenance matches are
    accepted; equal widths and positional elimination do not prove which
    benchmark output an anonymous field represents.
    """
    fields = _cvdp_infer_concat_fields(sv_code, sp_out_name, sparkle_mod_name) or []
    if not fields:
        return []
    provenance = _cvdp_assignment_provenance(sv_code, sparkle_mod_name)
    slices: list[tuple[str, int, str]] = []
    used: set[str] = set()
    for index, field in enumerate(fields):
        matched = _cvdp_match_output_for_field(
            field,
            remaining_outputs,
            used,
            _cvdp_related_signal_names(field, provenance),
        )
        if matched:
            slices.append((matched[2], index, field))
            used.add(matched[2])
    # A subset can still be a sound semantic mapping (for example a bundle
    # also carries an unobserved valid bit). The caller fails closed for any
    # genuinely unmapped harness-observed output.
    return slices


def _cvdp_unobserved_bundle_fields_are_safe(
    *,
    fields: list[str],
    observed_assignments: list[tuple[str, int, str]],
    expected_outputs: list[tuple[str, str, str]],
    provenance: dict[str, tuple[str, ...]],
    type_lookup: dict[str, str],
) -> bool:
    """Accept only named, typed, non-ambiguous unobserved tuple leaves.

    A Sparkle return bundle can contain source-level outputs that the benchmark
    harness never reads. Such a leaf is safe to omit from the public wrapper
    only when the exact child signal remains available for hierarchy/width
    guards and its generated name cannot also denote an observed output.
    Constants, expressions, selects, compiler temporaries, and duplicate or
    alias-equivalent leaves do not carry that evidence.
    """
    observed_indices = {index for _, index, _ in observed_assignments}
    hidden_indices = [
        index for index in range(len(fields))
        if index not in observed_indices
    ]
    if not hidden_indices:
        return True

    plain_names: dict[int, str] = {}
    for index, field in enumerate(fields):
        match = re.fullmatch(r"\s*(_gen_[A-Za-z]\w*)\s*", field)
        if match:
            plain_names[index] = match.group(1)

    for index in hidden_indices:
        field = fields[index]
        hidden_name = plain_names.get(index)
        if hidden_name is None:
            return False
        if _cvdp_tuple_field_shape_type(field, type_lookup) is None:
            return False
        if _cvdp_hierarchical_field_expr(field) is None:
            return False

        related_names = _cvdp_related_signal_names(field, provenance)
        # Test each output independently: a multi-output ambiguity must not be
        # mistaken for "no match" merely because the normal matcher returns
        # None when more than one candidate is plausible.
        if any(
            _cvdp_match_output_for_field(
                field,
                [expected_output],
                set(),
                related_names,
            ) is not None
            for expected_output in expected_outputs
        ):
            return False

        # A hidden leaf must name a distinct semantic child. This catches an
        # exact duplicate as well as spelling aliases such as underscore-only
        # variants that the observed-output matcher treats as equivalent.
        for other_index, other_name in plain_names.items():
            if other_index == index:
                continue
            if _ports_equivalent(hidden_name, other_name):
                return False

    return True


def _cvdp_derived_width_bindings(
    names: set[str],
    expected_outputs: list[tuple[str, str, str]],
) -> dict[str, str]:
    """Associate read-only ``*_WIDTH`` aliases with one observed output."""
    bindings: dict[str, str] = {}
    for name in sorted(names):
        stem_match = re.fullmatch(r"(.+?)_?WIDTH", name, re.IGNORECASE)
        if not stem_match:
            continue
        stem = re.sub(r"[^a-z0-9]", "", stem_match.group(1).lower())
        if len(stem) < 3:
            continue
        candidates = [
            port_name
            for _, _, port_name in expected_outputs
            if stem in re.sub(r"[^a-z0-9]", "", port_name.lower())
        ]
        if len(candidates) == 1:
            bindings[name] = candidates[0]
    return bindings


def _cvdp_ast_qualified_name(node: ast.AST) -> str | None:
    """Return a dotted name for a simple Python name/attribute expression."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _cvdp_ast_qualified_name(node.value)
        return f"{base}.{node.attr}" if base is not None else None
    return None


def _cvdp_ast_dut_port_name(node: ast.AST) -> str | None:
    """Resolve only literal cocotb handles rooted directly at ``dut``."""
    name: str | None = None
    if (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "dut"
    ):
        name = node.attr
    elif (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "dut"
        and node.func.attr == "_id"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    ):
        name = node.args[0].value
    elif (
        isinstance(node, ast.Subscript)
        and isinstance(node.value, ast.Name)
        and node.value.id == "dut"
    ):
        subscript = node.slice
        if isinstance(subscript, ast.Constant) and isinstance(
            subscript.value, str
        ):
            name = subscript.value
    if name is None or re.fullmatch(r"[A-Za-z_]\w*", name) is None:
        return None
    return name


def _cvdp_structured_harness_clocks(
    py_files: dict[str, str],
) -> tuple[set[str], set[str]]:
    """Extract cocotb Clock targets from syntax, never comments or strings.

    Only imports with known cocotb provenance and literal DUT handles are
    accepted.  The second result records syntax, import, or target shapes that
    make the clock role unresolved so callers can fail closed.
    """
    clocks: set[str] = set()
    unresolved: set[str] = set()
    for path, content in py_files.items():
        if Path(path).name == "harness_library.py":
            continue
        try:
            tree = ast.parse(textwrap.dedent(content), filename=path)
        except SyntaxError:
            unresolved.add(f"{path}:syntax")
            continue

        clock_callables: set[str] = set()
        for statement in tree.body:
            if isinstance(statement, ast.ImportFrom):
                if statement.module == "cocotb.clock":
                    for alias in statement.names:
                        if alias.name == "Clock":
                            clock_callables.add(alias.asname or alias.name)
                elif statement.module == "cocotb":
                    for alias in statement.names:
                        if alias.name == "clock":
                            clock_callables.add(
                                f"{alias.asname or alias.name}.Clock"
                            )
            elif isinstance(statement, ast.Import):
                for alias in statement.names:
                    if alias.name == "cocotb.clock":
                        if alias.asname:
                            clock_callables.add(f"{alias.asname}.Clock")
                        else:
                            clock_callables.add("cocotb.clock.Clock")
                    elif alias.name == "cocotb":
                        root = alias.asname or alias.name
                        clock_callables.add(f"{root}.clock.Clock")

        clock_roots = {
            callable_name.split(".", 1)[0]
            for callable_name in clock_callables
        }
        rebound_clock_root = any(
            (
                isinstance(node, ast.Name)
                and isinstance(node.ctx, ast.Store)
                and node.id in clock_roots
            )
            or (isinstance(node, ast.arg) and node.arg in clock_roots)
            or (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                and node.name in clock_roots
            )
            or (
                isinstance(node, ast.ExceptHandler)
                and node.name in clock_roots
            )
            for node in ast.walk(tree)
        )
        if rebound_clock_root:
            unresolved.add(f"{path}:binding")
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            callable_name = _cvdp_ast_qualified_name(node.func)
            is_known_clock = callable_name in clock_callables
            looks_like_clock = (
                callable_name == "Clock"
                or (
                    callable_name is not None
                    and callable_name.endswith(".Clock")
                )
            )
            if not is_known_clock:
                if looks_like_clock:
                    unresolved.add(f"{path}:{node.lineno}:import")
                continue
            if not node.args:
                unresolved.add(f"{path}:{node.lineno}:target")
                continue
            port_name = _cvdp_ast_dut_port_name(node.args[0])
            if port_name is None:
                unresolved.add(f"{path}:{node.lineno}:target")
                continue
            clocks.add(port_name)
    return clocks, unresolved


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
    clocks, clock_errors = _cvdp_structured_harness_clocks(py_files)
    all_names.update(clocks)

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
    inputs = set(assigned) | clocks | clock_or_reset
    ports = all_names - params
    outputs = ports - inputs
    return {
        "ports": ports,
        "inputs": inputs & ports,
        "outputs": outputs,
        "params": params,
        "clocks": clocks,
        "clock_errors": clock_errors,
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


ParameterConfiguration = tuple[tuple[str, int], ...]


@dataclass(frozen=True)
class CVDPParameterSweepPlan:
    """Exact concrete configurations observed at CVDP ``build`` calls.

    A plan is executable only when ``unresolved`` is false.  In particular,
    independent per-parameter value sets are never multiplied here: doing so
    can invent configurations that the benchmark never requests.
    """

    configurations: tuple[ParameterConfiguration, ...]
    unresolved: bool
    unresolved_reasons: tuple[str, ...] = ()


_CVDP_EXACT_UNKNOWN = object()
_CVDP_RUNNER = object()
_CVDP_BUILD_CALLABLE = object()
_CVDP_MAX_SWEEP_CONFIGS = 256
_CVDP_MAX_INTERPRETER_STEPS = 4096


def _canonical_parameter_configuration(
    mapping: dict[str, int],
) -> ParameterConfiguration:
    return tuple(sorted((str(name), int(value)) for name, value in mapping.items()))


def _render_orfs_top_parameters(parameters: dict[str, int] | None) -> str:
    """Render validated Yosys top parameters for an ORFS make config.

    SystemVerilog permits ``$`` after the first identifier character, while
    GNU make consumes dollar syntax before exporting this value.  Keep the
    flow contract deliberately narrower and fail closed instead of depending
    on multiple layers of make/Tcl escaping for a cached result identity.
    """
    if not parameters:
        return ""
    validated: list[tuple[str, int]] = []
    for name, value in parameters.items():
        if not isinstance(name, str) or not re.fullmatch(
            r"[A-Za-z_][A-Za-z0-9_$]*", name
        ):
            raise ValueError(f"Invalid parameter name for ORFS: {name!r}")
        if "$" in name:
            raise ValueError(
                f"Parameter name {name!r} contains '$', which is not supported "
                "safely by the ORFS make configuration"
            )
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not (0 <= value <= 0xFFFF_FFFF)
        ):
            raise ValueError(
                f"Parameter {name}={value!r} is outside the 32-bit unsigned Nat contract"
            )
        validated.append((name, value))
    rendered_parameters = [
        item
        for name, value in sorted(validated)
        for item in (name, str(value))
    ]
    return "export VERILOG_TOP_PARAMS = " + " ".join(rendered_parameters) + "\n"


class _CVDPExactSweepExtractor:
    """Conservative interpreter for literal CVDP build matrices.

    It intentionally understands a small, deterministic Python subset:
    literal containers, integer arithmetic, ``range``, exact ``for`` loops,
    pytest ``parametrize`` decorators, dictionary ``update`` and direct
    ``runner.build(parameters=...)`` calls.  Any build whose complete mapping
    cannot be recovered makes the whole plan unresolved instead of returning
    a misleading partial sweep.
    """

    def __init__(self, parameter_names: set[str]) -> None:
        self.parameter_names = set(parameter_names)
        self.configurations: set[ParameterConfiguration] = set()
        self.unresolved_reasons: list[str] = []
        self.build_calls = 0
        # The broad coverage analyzer below counts syntactic build sites.  A
        # site in a branch that this interpreter can prove dead is accounted
        # for here without turning it into an executable sweep point.
        self.ignored_build_calls = 0
        self.execution_steps = 0
        self.budget_exhausted = False
        self.local_function_names: set[str] = set()
        self.runner_factory_names: set[str] = set()

    def _unresolved(self, reason: str) -> None:
        if reason not in self.unresolved_reasons:
            self.unresolved_reasons.append(reason)

    def _take_step(self) -> bool:
        if self.budget_exhausted:
            return False
        self.execution_steps += 1
        if self.execution_steps > _CVDP_MAX_INTERPRETER_STEPS:
            self._unresolved(
                f"parameter sweep interpretation exceeds {_CVDP_MAX_INTERPRETER_STEPS} steps"
            )
            self.budget_exhausted = True
            return False
        return True

    def _eval(self, node: ast.AST | None, env: dict[str, Any]) -> Any:
        if node is None:
            return None
        if isinstance(node, ast.Constant):
            if isinstance(node.value, (int, str, bool)) or node.value is None:
                return node.value
            return _CVDP_EXACT_UNKNOWN
        if isinstance(node, ast.Name):
            return env.get(node.id, _CVDP_EXACT_UNKNOWN)
        if isinstance(node, ast.Attribute):
            if (
                node.attr == "build"
                and self._eval(node.value, env) is _CVDP_RUNNER
            ):
                return _CVDP_BUILD_CALLABLE
            return _CVDP_EXACT_UNKNOWN
        if isinstance(node, ast.Set):
            # Set iteration order is an implementation detail, not source
            # order; it cannot define an exact deterministic sweep matrix.
            return _CVDP_EXACT_UNKNOWN
        if isinstance(node, (ast.List, ast.Tuple)):
            values = [self._eval(item, env) for item in node.elts]
            if any(value is _CVDP_EXACT_UNKNOWN for value in values):
                return _CVDP_EXACT_UNKNOWN
            return values if isinstance(node, ast.List) else tuple(values)
        if isinstance(node, ast.Dict):
            result: dict[Any, Any] = {}
            for key_node, value_node in zip(node.keys, node.values):
                if key_node is None:
                    expanded = self._eval(value_node, env)
                    if not isinstance(expanded, dict):
                        return _CVDP_EXACT_UNKNOWN
                    result.update(expanded)
                    continue
                key = self._eval(key_node, env)
                value = self._eval(value_node, env)
                if key is _CVDP_EXACT_UNKNOWN or value is _CVDP_EXACT_UNKNOWN:
                    return _CVDP_EXACT_UNKNOWN
                result[key] = value
            return result
        if isinstance(node, ast.UnaryOp):
            value = self._eval(node.operand, env)
            if not isinstance(value, int):
                return _CVDP_EXACT_UNKNOWN
            if isinstance(node.op, ast.UAdd):
                return value
            if isinstance(node.op, ast.USub):
                return -value
            if isinstance(node.op, ast.Invert):
                return ~value
            if isinstance(node.op, ast.Not):
                return not value
            return _CVDP_EXACT_UNKNOWN
        if isinstance(node, ast.BinOp):
            left = self._eval(node.left, env)
            right = self._eval(node.right, env)
            if not isinstance(left, int) or not isinstance(right, int):
                return _CVDP_EXACT_UNKNOWN
            try:
                if isinstance(node.op, ast.Add):
                    return left + right
                if isinstance(node.op, ast.Sub):
                    return left - right
                if isinstance(node.op, ast.Mult):
                    return left * right
                if isinstance(node.op, ast.FloorDiv) and right != 0:
                    return left // right
                if isinstance(node.op, ast.Mod) and right != 0:
                    return left % right
                if isinstance(node.op, ast.Pow) and 0 <= right <= 63:
                    return left ** right
                if isinstance(node.op, ast.LShift) and 0 <= right <= 63:
                    return left << right
                if isinstance(node.op, ast.RShift) and 0 <= right <= 63:
                    return left >> right
                if isinstance(node.op, ast.BitOr):
                    return left | right
                if isinstance(node.op, ast.BitAnd):
                    return left & right
                if isinstance(node.op, ast.BitXor):
                    return left ^ right
            except (ArithmeticError, OverflowError):
                return _CVDP_EXACT_UNKNOWN
            return _CVDP_EXACT_UNKNOWN
        if isinstance(node, ast.Compare):
            left = self._eval(node.left, env)
            if left is _CVDP_EXACT_UNKNOWN:
                return _CVDP_EXACT_UNKNOWN
            for operator, comparator_node in zip(node.ops, node.comparators):
                right = self._eval(comparator_node, env)
                if right is _CVDP_EXACT_UNKNOWN:
                    return _CVDP_EXACT_UNKNOWN
                try:
                    matched = (
                        left == right if isinstance(operator, ast.Eq) else
                        left != right if isinstance(operator, ast.NotEq) else
                        left < right if isinstance(operator, ast.Lt) else
                        left <= right if isinstance(operator, ast.LtE) else
                        left > right if isinstance(operator, ast.Gt) else
                        left >= right if isinstance(operator, ast.GtE) else
                        left in right if isinstance(operator, ast.In) else
                        left not in right if isinstance(operator, ast.NotIn) else
                        _CVDP_EXACT_UNKNOWN
                    )
                except (TypeError, ValueError):
                    return _CVDP_EXACT_UNKNOWN
                if matched is _CVDP_EXACT_UNKNOWN:
                    return matched
                if not matched:
                    return False
                left = right
            return True
        if isinstance(node, ast.BoolOp):
            if not node.values:
                return _CVDP_EXACT_UNKNOWN
            value = self._eval(node.values[0], env)
            if value is _CVDP_EXACT_UNKNOWN:
                return value
            for child in node.values[1:]:
                if isinstance(node.op, ast.And) and not bool(value):
                    return value
                if isinstance(node.op, ast.Or) and bool(value):
                    return value
                value = self._eval(child, env)
                if value is _CVDP_EXACT_UNKNOWN:
                    return value
            return value
        if isinstance(node, ast.IfExp):
            condition = self._eval(node.test, env)
            if condition is _CVDP_EXACT_UNKNOWN:
                return condition
            return self._eval(node.body if bool(condition) else node.orelse, env)
        if isinstance(node, ast.NamedExpr):
            return self._eval(node.value, env)
        if isinstance(node, ast.Subscript):
            value = self._eval(node.value, env)
            index = self._eval(node.slice, env)
            try:
                return value[index]
            except (KeyError, IndexError, TypeError):
                return _CVDP_EXACT_UNKNOWN
        if isinstance(node, ast.Call):
            factory_name = (
                node.func.id if isinstance(node.func, ast.Name) else None
            )
            factory_path = (
                ast.unparse(node.func) if hasattr(ast, "unparse") else ""
            )
            if (
                factory_name in self.runner_factory_names
                or factory_path in {
                    "cocotb.runner.get_runner",
                    "cocotb_tools.runner.get_runner",
                }
            ):
                return _CVDP_RUNNER
            if isinstance(node.func, ast.Name) and node.func.id == "range":
                args = [self._eval(arg, env) for arg in node.args]
                if not all(isinstance(arg, int) for arg in args):
                    return _CVDP_EXACT_UNKNOWN
                try:
                    range_value = range(*args)
                except (TypeError, ValueError, OverflowError):
                    return _CVDP_EXACT_UNKNOWN
                if len(range_value) > _CVDP_MAX_SWEEP_CONFIGS:
                    return _CVDP_EXACT_UNKNOWN
                return list(range_value)
            if (
                isinstance(node.func, ast.Name)
                and node.func.id == "getattr"
                and len(node.args) >= 2
                and self._eval(node.args[0], env) is _CVDP_RUNNER
                and self._eval(node.args[1], env) == "build"
            ):
                return _CVDP_BUILD_CALLABLE
            if isinstance(node.func, ast.Name) and node.func.id in {"list", "tuple"} and node.args:
                value = self._eval(node.args[0], env)
                if isinstance(value, (list, tuple, range)):
                    return list(value) if node.func.id == "list" else tuple(value)
            if isinstance(node.func, ast.Name) and node.func.id == "dict":
                if len(node.args) > 1:
                    return _CVDP_EXACT_UNKNOWN
                result: dict[Any, Any] = {}
                if node.args:
                    value = self._eval(node.args[0], env)
                    try:
                        result.update(value)
                    except (TypeError, ValueError):
                        return _CVDP_EXACT_UNKNOWN
                for keyword in node.keywords:
                    if keyword.arg is None:
                        expanded = self._eval(keyword.value, env)
                        if not isinstance(expanded, dict):
                            return _CVDP_EXACT_UNKNOWN
                        result.update(expanded)
                    else:
                        value = self._eval(keyword.value, env)
                        if value is _CVDP_EXACT_UNKNOWN:
                            return _CVDP_EXACT_UNKNOWN
                        result[keyword.arg] = value
                return result
            if isinstance(node.func, ast.Attribute):
                receiver = self._eval(node.func.value, env)
                if (
                    isinstance(receiver, dict)
                    and node.func.attr == "copy"
                    and not node.args
                    and not node.keywords
                ):
                    return dict(receiver)
                if isinstance(receiver, dict) and node.func.attr == "items" and not node.args:
                    return list(receiver.items())
                if isinstance(receiver, dict) and node.func.attr == "keys" and not node.args:
                    return list(receiver.keys())
                if isinstance(receiver, dict) and node.func.attr == "values" and not node.args:
                    return list(receiver.values())
                if (
                    isinstance(receiver, dict)
                    and node.func.attr == "get"
                    and 1 <= len(node.args) <= 2
                    and not node.keywords
                ):
                    key = self._eval(node.args[0], env)
                    default = self._eval(node.args[1], env) if len(node.args) > 1 else None
                    if key is not _CVDP_EXACT_UNKNOWN and default is not _CVDP_EXACT_UNKNOWN:
                        return receiver.get(key, default)
                if (
                    isinstance(receiver, list)
                    and node.func.attr == "copy"
                    and not node.args
                    and not node.keywords
                ):
                    return list(receiver)
            return _CVDP_EXACT_UNKNOWN
        return _CVDP_EXACT_UNKNOWN

    def _assign(self, target: ast.AST, value: Any, env: dict[str, Any]) -> bool:
        if isinstance(target, ast.Name):
            env[target.id] = value
            return True
        if isinstance(target, (ast.Tuple, ast.List)) and isinstance(value, (list, tuple)):
            if len(target.elts) == len(value):
                return all(
                    self._assign(child, child_value, env)
                    for child, child_value in zip(target.elts, value)
                )
            return False
        if isinstance(target, ast.Subscript):
            container = self._eval(target.value, env)
            index = self._eval(target.slice, env)
            if index is _CVDP_EXACT_UNKNOWN:
                return False
            if isinstance(container, (dict, list)):
                try:
                    # Mutate in place so aliases and nested containers retain
                    # Python reference semantics.
                    container[index] = value
                    return True
                except (IndexError, KeyError, TypeError):
                    return False
            return False
        # Starred unpacking, attributes and other structured targets are not
        # part of the exact interpreter's supported assignment subset.
        return False

    @staticmethod
    def _clone_env(env: dict[str, Any]) -> dict[str, Any]:
        """Clone mutable values while preserving aliases within the scope."""
        cloned_mutables: dict[int, Any] = {}
        result: dict[str, Any] = {}
        for name, value in env.items():
            if isinstance(value, dict):
                result[name] = cloned_mutables.setdefault(id(value), dict(value))
            elif isinstance(value, list):
                result[name] = cloned_mutables.setdefault(id(value), list(value))
            elif isinstance(value, set):
                result[name] = cloned_mutables.setdefault(id(value), set(value))
            else:
                result[name] = value
        return result

    def _is_build_call(self, call: ast.Call, env: dict[str, Any]) -> bool:
        if isinstance(call.func, ast.Attribute) and call.func.attr == "build":
            return self._eval(call.func.value, env) is _CVDP_RUNNER
        if isinstance(call.func, ast.Name) and env.get(call.func.id) is _CVDP_BUILD_CALLABLE:
            return True
        return False

    def _record_build(self, call: ast.Call, env: dict[str, Any]) -> None:
        self.build_calls += 1
        parameters: Any = None
        found_parameters = False
        seen_keywords: set[str] = set()
        for keyword in call.keywords:
            if keyword.arg is None:
                expanded = self._eval(keyword.value, env)
                if not isinstance(expanded, dict):
                    self._unresolved(
                        "a build() **kwargs mapping could not be resolved exactly"
                    )
                    return
                if not all(isinstance(key, str) for key in expanded):
                    self._unresolved(
                        "a build() **kwargs mapping contains a non-string key"
                    )
                    return
                duplicate_keywords = seen_keywords.intersection(expanded)
                if duplicate_keywords:
                    self._unresolved(
                        "duplicate build() keyword(s) across **kwargs: "
                        + ", ".join(sorted(duplicate_keywords))
                    )
                    return
                seen_keywords.update(expanded)
                if "parameters" in expanded:
                    found_parameters = True
                    parameters = expanded["parameters"]
                continue

            if keyword.arg in seen_keywords:
                self._unresolved(
                    f"duplicate build() keyword {keyword.arg!r}"
                )
                return
            seen_keywords.add(keyword.arg)
            if keyword.arg == "parameters":
                found_parameters = True
                parameters = self._eval(keyword.value, env)
        if found_parameters and not isinstance(parameters, dict):
            self._unresolved("a build() parameter dictionary could not be resolved exactly")
            return
        mapping = parameters if found_parameters else {}
        concrete: dict[str, int] = {}
        for key, value in mapping.items():
            if not isinstance(key, str) or not isinstance(value, int) or isinstance(value, bool):
                self._unresolved("a build() parameter key/value is not a literal integer")
                return
            if value < 0:
                self._unresolved(f"parameter {key} has a negative sweep value")
                return
            if value > 0xFFFF_FFFF:
                self._unresolved(
                    f"parameter {key}={value} exceeds the 32-bit unsigned Nat contract"
                )
                return
            if self.parameter_names and key not in self.parameter_names:
                self._unresolved(f"build() overrides unknown parameter {key}")
                return
            concrete[key] = value
        self.configurations.add(_canonical_parameter_configuration(concrete))
        if len(self.configurations) > _CVDP_MAX_SWEEP_CONFIGS:
            self._unresolved(
                f"parameter sweep exceeds {_CVDP_MAX_SWEEP_CONFIGS} concrete configurations"
            )
            self.budget_exhausted = True

    def _account_ignored_builds(self, node: ast.AST, env: dict[str, Any]) -> None:
        """Account for build sites in control flow proven not to execute."""
        self.ignored_build_calls += sum(
            1
            for child in ast.walk(node)
            if isinstance(child, ast.Call) and self._is_build_call(child, env)
        )

    @staticmethod
    def _is_mapping_update(call: ast.Call) -> bool:
        return (
            isinstance(call.func, ast.Attribute)
            and call.func.attr == "update"
        )

    def _contains_relevant_side_effect(self, node: ast.AST) -> bool:
        return any(
            isinstance(child, ast.Call)
            and (
                (isinstance(child.func, ast.Attribute)
                 and child.func.attr in {"build", "update"})
                or (isinstance(child.func, ast.Name)
                    and "build" in child.func.id.lower())
                or (
                    (ast.unparse(child.func) if hasattr(ast, "unparse") else "")
                    in {"pytest.skip", "pytest.xfail", "pytest.importorskip"}
                )
            )
            for child in ast.walk(node)
        )

    @staticmethod
    def _contains_state_mutation_syntax(node: ast.AST) -> bool:
        return any(
            isinstance(child, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Delete, ast.NamedExpr))
            or (
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Attribute)
                and child.func.attr in {
                    "update", "setdefault", "pop", "popitem", "clear", "__setitem__",
                }
            )
            for child in ast.walk(node)
        )

    def _may_mutate_tracked_state(
        self, node: ast.AST, env: dict[str, Any]
    ) -> bool:
        if self._contains_state_mutation_syntax(node):
            return True
        tracked_mutable_ids = {
            id(value)
            for value in env.values()
            if isinstance(value, (dict, list, set))
        }
        for child in ast.walk(node):
            if not isinstance(child, ast.Call):
                continue
            if isinstance(child.func, ast.Name) and child.func.id in self.local_function_names:
                return True
            values = [self._eval(argument, env) for argument in child.args]
            values.extend(self._eval(keyword.value, env) for keyword in child.keywords)
            if isinstance(child.func, ast.Attribute):
                values.append(self._eval(child.func.value, env))
            if any(
                isinstance(value, (dict, list, set))
                and id(value) in tracked_mutable_ids
                for value in values
            ):
                return True
        return False

    def _preflight_exact_subset(self, tree: ast.Module) -> bool:
        """Reject Python execution features outside the exact whitelist.

        Static source interpretation cannot soundly reproduce pytest fixture
        scheduling, definition-time custom decorators, or arbitrary control
        flow with shared global state.  Those harnesses still run functional
        simulation, but PPA enumeration is deliberately skipped.
        """
        unsupported_control = (
            ast.ClassDef,
            ast.Global,
            ast.Nonlocal,
            ast.Match,
            ast.Try,
            ast.With,
            ast.AsyncWith,
            ast.While,
            ast.Yield,
            ast.YieldFrom,
            ast.NamedExpr,
            ast.Lambda,
            ast.ListComp,
            ast.SetComp,
            ast.DictComp,
            ast.GeneratorExp,
        )

        # Calls in this small interpreter deliberately implement only these
        # Python builtins.  If the harness rebinds one of their names, applying
        # builtin semantics would fabricate a configuration which real Python
        # may never build (for example a user-defined ``dict`` constructor).
        interpreted_builtins = {
            "dict", "getattr", "len", "list", "range", "sorted", "tuple",
        }
        called_builtins = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in interpreted_builtins
        }
        shadowed_builtins: set[str] = set()
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Name)
                and isinstance(node.ctx, (ast.Store, ast.Del))
                and node.id in interpreted_builtins
            ):
                shadowed_builtins.add(node.id)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if node.name in interpreted_builtins:
                    shadowed_builtins.add(node.name)
                for argument in (
                    list(node.args.posonlyargs)
                    + list(node.args.args)
                    + list(node.args.kwonlyargs)
                ) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) else ():
                    if argument.arg in interpreted_builtins:
                        shadowed_builtins.add(argument.arg)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    bound_name = alias.asname or alias.name.split(".", 1)[0]
                    if bound_name in interpreted_builtins:
                        shadowed_builtins.add(bound_name)
        ambiguous_builtins = called_builtins & shadowed_builtins
        if ambiguous_builtins and self._contains_build_syntax(tree.body):
            self._unresolved(
                "interpreter builtin name(s) are shadowed: "
                + ", ".join(sorted(ambiguous_builtins))
            )
            return False

        for node in ast.walk(tree):
            if isinstance(node, unsupported_control):
                self._unresolved(
                    f"{type(node).__name__} is outside the exact parameter-sweep interpreter"
                )
                return False
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            decorator_names = [
                ast.unparse(decorator.func if isinstance(decorator, ast.Call) else decorator)
                if hasattr(ast, "unparse") else ""
                for decorator in node.decorator_list
            ]
            if (
                isinstance(node, ast.AsyncFunctionDef)
                and self._contains_build_syntax(node.body)
                and not any(
                    name.endswith("pytest.mark.asyncio")
                    or name.endswith("cocotb.test")
                    for name in decorator_names
                )
            ):
                self._unresolved(
                    f"async test {node.name} containing build() has no supported execution marker"
                )
                return False
            for decorator in node.decorator_list:
                decorator_func = decorator.func if isinstance(decorator, ast.Call) else decorator
                name = ast.unparse(decorator_func) if hasattr(ast, "unparse") else ""
                allowed = (
                    name.endswith("pytest.mark.parametrize")
                    or name.endswith("pytest.mark.skip")
                    or name.endswith("pytest.mark.skipif")
                    or name.endswith("pytest.mark.xfail")
                    or name.endswith("pytest.mark.asyncio")
                    or name.endswith("cocotb.test")
                )
                if name.endswith("pytest.fixture") or name.endswith("pytest.mark.usefixtures"):
                    self._unresolved(
                        "pytest fixture scheduling is outside the exact parameter-sweep interpreter"
                    )
                    return False
                if not allowed:
                    self._unresolved(
                        f"decorator {name or '<dynamic>'} is outside the exact parameter-sweep interpreter"
                    )
                    return False
                if name.endswith("cocotb.test") and isinstance(decorator, ast.Call):
                    for keyword in decorator.keywords:
                        if keyword.arg in {"skip", "expect_fail", "expect_error"}:
                            value = self._eval(keyword.value, {})
                            if value is _CVDP_EXACT_UNKNOWN or bool(value):
                                self._unresolved(
                                    "cocotb skip/expected-failure tests cannot establish complete finite evidence"
                                )
                                return False
        return True

    def _apply_direct_mapping_update(
        self, call: ast.Call, env: dict[str, Any]
    ) -> None:
        """Apply one update call after its arguments have been evaluated."""
        assert isinstance(call.func, ast.Attribute)
        old = self._eval(call.func.value, env)
        merged: Any = old if isinstance(old, dict) else _CVDP_EXACT_UNKNOWN
        if merged is _CVDP_EXACT_UNKNOWN or len(call.args) > 1:
            self._unresolved("a dictionary update could not be resolved exactly")
            return
        if call.args:
            updates = self._eval(call.args[0], env)
            if not isinstance(updates, dict):
                self._unresolved("a dictionary update could not be resolved exactly")
                return
            merged.update(updates)
        for keyword in call.keywords:
            if keyword.arg is None:
                expanded = self._eval(keyword.value, env)
                if not isinstance(expanded, dict):
                    self._unresolved("a dictionary update could not be resolved exactly")
                    return
                merged.update(expanded)
            else:
                value = self._eval(keyword.value, env)
                if value is _CVDP_EXACT_UNKNOWN:
                    self._unresolved("a dictionary update could not be resolved exactly")
                    return
                merged[keyword.arg] = value

    def _observe_expr(self, node: ast.AST, env: dict[str, Any]) -> None:
        """Observe expression side effects in Python evaluation order.

        This deliberately does not use ``ast.walk``: doing so executes calls
        hidden behind a false ``and`` operand and mutates sweep dictionaries
        in branches which the harness never takes.
        """
        if isinstance(node, ast.NamedExpr):
            self._observe_expr(node.value, env)
            if not self._assign(node.target, self._eval(node.value, env), env):
                self._unresolved(
                    "a named-expression target affecting build() is not exactly supported"
                )
            return
        if isinstance(node, ast.BoolOp):
            for index, child in enumerate(node.values):
                self._observe_expr(child, env)
                value = self._eval(child, env)
                if value is _CVDP_EXACT_UNKNOWN:
                    remaining = node.values[index + 1:]
                    if any(
                        self._contains_relevant_side_effect(item)
                        or self._may_mutate_tracked_state(item, env)
                        for item in remaining
                    ):
                        for item in remaining:
                            self._account_ignored_builds(item, env)
                        self._unresolved(
                            "an unknown short-circuit condition controls build() or its parameters"
                        )
                    return
                stops = (
                    isinstance(node.op, ast.And) and not bool(value)
                ) or (
                    isinstance(node.op, ast.Or) and bool(value)
                )
                if stops:
                    for item in node.values[index + 1:]:
                        self._account_ignored_builds(item, env)
                    return
            return
        if isinstance(node, ast.IfExp):
            self._observe_expr(node.test, env)
            condition = self._eval(node.test, env)
            if condition is _CVDP_EXACT_UNKNOWN:
                if (
                    self._contains_relevant_side_effect(node.body)
                    or self._contains_relevant_side_effect(node.orelse)
                    or self._may_mutate_tracked_state(node.body, env)
                    or self._may_mutate_tracked_state(node.orelse, env)
                ):
                    self._account_ignored_builds(node.body, env)
                    self._account_ignored_builds(node.orelse, env)
                    self._unresolved(
                        "an unknown conditional expression controls build() or its parameters"
                    )
                return
            selected = node.body if bool(condition) else node.orelse
            skipped = node.orelse if bool(condition) else node.body
            self._account_ignored_builds(skipped, env)
            self._observe_expr(selected, env)
            return
        if isinstance(node, ast.Call):
            # Python evaluates the receiver and arguments before the call.
            if isinstance(node.func, ast.Attribute):
                self._observe_expr(node.func.value, env)
            for argument in node.args:
                self._observe_expr(argument, env)
            for keyword in node.keywords:
                self._observe_expr(keyword.value, env)
            call_name = ast.unparse(node.func) if hasattr(ast, "unparse") else ""
            if call_name in {"pytest.skip", "pytest.xfail", "pytest.importorskip"}:
                self._unresolved(
                    f"{call_name} makes the executed parameter sweep incomplete"
                )
                return
            if self._is_build_call(node, env):
                self._record_build(node, env)
            elif isinstance(node.func, ast.Attribute) and node.func.attr == "build":
                self._unresolved(
                    "a .build() call has an unknown receiver and cannot be identified as the CVDP runner"
                )
            elif self._is_mapping_update(node):
                self._apply_direct_mapping_update(node, env)
            elif isinstance(node.func, ast.Name) and node.func.id in {
                "dict", "list", "tuple", "len", "sorted", "range", "getattr",
            }:
                pass
            elif isinstance(node.func, ast.Name) and node.func.id in self.local_function_names:
                self._unresolved(
                    f"call to local helper {node.func.id} may change build() parameters"
                )
            elif isinstance(node.func, ast.Attribute):
                receiver = self._eval(node.func.value, env)
                if isinstance(receiver, dict) and node.func.attr not in {
                    "copy", "get", "items", "keys", "values",
                }:
                    self._unresolved(
                        f"unsupported dictionary method {node.func.attr} may change a build() configuration"
                    )
                elif isinstance(receiver, (list, set)) and node.func.attr != "copy":
                    self._unresolved(
                        f"unsupported mutable method {node.func.attr} may change a build() configuration"
                    )
                elif not isinstance(receiver, (dict, list, set)):
                    argument_values = [self._eval(argument, env) for argument in node.args]
                    argument_values.extend(
                        self._eval(keyword.value, env) for keyword in node.keywords
                    )
                    tracked_mutable_ids = {
                        id(value)
                        for value in env.values()
                        if isinstance(value, (dict, list, set))
                    }
                    if any(
                        isinstance(value, (dict, list, set))
                        and id(value) in tracked_mutable_ids
                        for value in argument_values
                    ):
                        self._unresolved(
                            "an unknown method call may mutate a build() parameter dictionary"
                        )
            else:
                # An arbitrary callback receiving one of our tracked mapping
                # objects may mutate it.  Refuse to guess its post-state.
                argument_values = [self._eval(argument, env) for argument in node.args]
                argument_values.extend(
                    self._eval(keyword.value, env) for keyword in node.keywords
                )
                tracked_mutable_ids = {
                    id(value)
                    for value in env.values()
                    if isinstance(value, (dict, list, set))
                }
                if any(
                    isinstance(value, (dict, list, set))
                    and id(value) in tracked_mutable_ids
                    for value in argument_values
                ):
                    self._unresolved(
                        "an unknown call may mutate a build() parameter dictionary"
                    )
            return
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            for child in node.elts:
                self._observe_expr(child, env)
            return
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if key is not None:
                    self._observe_expr(key, env)
                self._observe_expr(value, env)
            return
        # ast fields are ordered in evaluation order for the simple arithmetic,
        # comparison, subscript and f-string forms accepted by this interpreter.
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.expr):
                self._observe_expr(child, env)

    def _contains_build_syntax(self, statements: list[ast.stmt]) -> bool:
        return any(
            isinstance(node, ast.Call)
            and (
                (isinstance(node.func, ast.Attribute) and node.func.attr == "build")
                or (
                    isinstance(node.func, ast.Name)
                    and "build" in node.func.id.lower()
                )
            )
            for statement in statements
            for node in ast.walk(statement)
        )

    def _execute(self, statements: list[ast.stmt], env: dict[str, Any]) -> None:
        block_contains_build = self._contains_build_syntax(statements)
        for statement_index, statement in enumerate(statements):
            if not self._take_step():
                return
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # Definition statements bind their name immediately in the
                # surrounding scope.  Do not leave the conventional ``runner``
                # sentinel (or a module parameter value) visible through a
                # real Python shadowing definition.
                env[statement.name] = _CVDP_EXACT_UNKNOWN
                continue
            if isinstance(statement, ast.ClassDef):
                continue
            if isinstance(statement, ast.Import):
                for alias in statement.names:
                    env[alias.asname or alias.name.split(".", 1)[0]] = _CVDP_EXACT_UNKNOWN
                continue
            if isinstance(statement, ast.ImportFrom):
                for alias in statement.names:
                    if alias.name == "*":
                        if block_contains_build:
                            self._unresolved(
                                "wildcard imports are outside the exact parameter-sweep interpreter"
                            )
                        continue
                    env[alias.asname or alias.name] = _CVDP_EXACT_UNKNOWN
                continue
            if isinstance(statement, ast.Assign):
                self._observe_expr(statement.value, env)
                value = self._eval(statement.value, env)
                for target in statement.targets:
                    if not self._assign(target, value, env) and block_contains_build:
                        self._unresolved(
                            "an assignment target affecting build() is not exactly supported"
                        )
                continue
            if isinstance(statement, ast.AnnAssign):
                if statement.value is not None:
                    self._observe_expr(statement.value, env)
                if not self._assign(
                    statement.target, self._eval(statement.value, env), env
                ) and block_contains_build:
                    self._unresolved(
                        "an annotated assignment target affecting build() is not exactly supported"
                    )
                continue
            if isinstance(statement, ast.AugAssign):
                if block_contains_build:
                    self._unresolved(
                        "augmented assignment may change a build() configuration"
                    )
                continue
            if isinstance(statement, ast.Delete):
                if block_contains_build:
                    self._unresolved(
                        "deletion may change a build() configuration"
                    )
                continue
            if isinstance(statement, ast.Expr):
                expression = statement.value
                self._observe_expr(expression, env)
                continue
            if isinstance(statement, (ast.For, ast.AsyncFor)):
                if any(
                    isinstance(node, (ast.Break, ast.Continue, ast.Return))
                    for child_statement in statement.body
                    for node in ast.walk(child_statement)
                ):
                    if block_contains_build:
                        for child_statement in statement.body:
                            self._account_ignored_builds(child_statement, env)
                        self._unresolved(
                            "a build() sweep loop uses break, continue, or return"
                        )
                    continue
                iterable = self._eval(statement.iter, env)
                if not isinstance(iterable, (list, tuple, range)):
                    if block_contains_build:
                        self._unresolved(
                            "a loop which may affect build() has a non-literal or unordered iteration space"
                        )
                    continue
                if len(iterable) > _CVDP_MAX_SWEEP_CONFIGS:
                    self._unresolved("a build() loop exceeds the sweep configuration limit")
                    continue
                if not iterable:
                    for child_statement in statement.body:
                        self._account_ignored_builds(child_statement, env)
                for value in iterable:
                    if self.budget_exhausted:
                        return
                    if not self._assign(statement.target, value, env):
                        self._unresolved(
                            "a loop target affecting build() is not exactly supported"
                        )
                        return
                    self._execute(statement.body, env)
                self._execute(statement.orelse, env)
                continue
            if isinstance(statement, ast.If):
                self._observe_expr(statement.test, env)
                condition = self._eval(statement.test, env)
                if condition is not _CVDP_EXACT_UNKNOWN:
                    selected = statement.body if bool(condition) else statement.orelse
                    skipped = statement.orelse if bool(condition) else statement.body
                    for child_statement in skipped:
                        self._account_ignored_builds(child_statement, env)
                    self._execute(selected, env)
                elif (
                    self._contains_build_syntax(statement.body + statement.orelse)
                    or any(
                        self._contains_relevant_side_effect(child_statement)
                        or self._may_mutate_tracked_state(child_statement, env)
                        for child_statement in statement.body + statement.orelse
                    )
                ):
                    for child_statement in statement.body + statement.orelse:
                        self._account_ignored_builds(child_statement, env)
                    self._unresolved(
                        "a data-dependent branch controls build() or its parameters"
                    )
                continue
            if isinstance(statement, (ast.With, ast.AsyncWith, ast.Try, ast.Match, ast.While)):
                if (
                    self._contains_build_syntax([statement])
                    or (
                        block_contains_build
                        and self._contains_state_mutation_syntax(statement)
                    )
                ):
                    self._unresolved(
                        "unsupported control flow may change build() execution or parameters"
                    )
                continue
            for _, value in ast.iter_fields(statement):
                nodes = value if isinstance(value, list) else [value]
                for node in nodes:
                    if isinstance(node, ast.expr):
                        self._observe_expr(node, env)

    def _parametrize_rows(
        self, decorator: ast.AST, env: dict[str, Any]
    ) -> list[dict[str, Any]] | None:
        if not isinstance(decorator, ast.Call) or len(decorator.args) < 2:
            return None
        name = ast.unparse(decorator.func) if hasattr(ast, "unparse") else ""
        if not name.endswith("parametrize"):
            return None
        for keyword in decorator.keywords:
            if keyword.arg != "indirect":
                continue
            indirect = self._eval(keyword.value, env)
            if indirect is _CVDP_EXACT_UNKNOWN or bool(indirect):
                self._unresolved(
                    "pytest indirect parameterization cannot be resolved without executing its fixture"
                )
                return []
        raw_names = self._eval(decorator.args[0], env)
        raw_values = self._eval(decorator.args[1], env)
        if not isinstance(raw_names, str) or not isinstance(raw_values, (list, tuple)):
            return []
        names = [item.strip() for item in raw_names.split(",") if item.strip()]
        rows: list[dict[str, Any]] = []
        if len(names) == 1:
            rows = [{names[0]: value} for value in raw_values]
        else:
            for raw_row in raw_values:
                if not isinstance(raw_row, (list, tuple)) or len(raw_row) != len(names):
                    return []
                rows.append(dict(zip(names, raw_row)))
        return rows

    def _function_mutates_module_mapping(
        self,
        statement: ast.FunctionDef | ast.AsyncFunctionDef,
        module_env: dict[str, Any],
    ) -> bool:
        """Detect cross-test mutation which our per-test scope cannot model."""
        module_mappings = {
            name
            for name, value in module_env.items()
            if isinstance(value, (dict, list, set))
        }
        if not module_mappings:
            return False
        for node in ast.walk(statement):
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Delete)):
                targets: list[ast.AST] = []
                if isinstance(node, ast.Assign):
                    targets = list(node.targets)
                elif isinstance(node, ast.Delete):
                    targets = list(node.targets)
                else:
                    targets = [node.target]
                for target in targets:
                    if isinstance(target, ast.Name) and not isinstance(
                        node, (ast.AugAssign, ast.Delete)
                    ):
                        continue
                    root = target
                    while isinstance(root, (ast.Subscript, ast.Attribute)):
                        root = root.value
                    if isinstance(root, ast.Name) and root.id in module_mappings:
                        return True
            if isinstance(node, ast.Call):
                if self._is_build_call(node, module_env):
                    continue
                candidates = list(node.args) + [keyword.value for keyword in node.keywords]
                if isinstance(node.func, ast.Attribute):
                    candidates.append(node.func.value)
                if any(
                    isinstance(candidate, ast.Name) and candidate.id in module_mappings
                    for candidate in candidates
                ):
                    return True
        return False

    @staticmethod
    def _function_local_bindings(
        statement: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> set[str]:
        """Return names which Python treats as local throughout ``statement``.

        The exact interpreter executes statements in source order, but Python
        decides function scope before execution.  Seeding a later-assigned name
        from the module environment would therefore miss an UnboundLocalError
        and could record the module value as an executed sweep point.
        """

        class BindingVisitor(ast.NodeVisitor):
            def __init__(self, root: ast.AST) -> None:
                self.root = root
                self.names: set[str] = set()

            def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
                if node is not self.root:
                    self.names.add(node.name)
                    return
                for child in node.body:
                    self.visit(child)

            def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
                if node is not self.root:
                    self.names.add(node.name)
                    return
                for child in node.body:
                    self.visit(child)

            def visit_Lambda(self, _node: ast.Lambda) -> None:
                return

            def visit_Name(self, node: ast.Name) -> None:
                if isinstance(node.ctx, (ast.Store, ast.Del)):
                    self.names.add(node.id)

            def visit_Import(self, node: ast.Import) -> None:
                for alias in node.names:
                    self.names.add(alias.asname or alias.name.split(".", 1)[0])

            def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
                for alias in node.names:
                    if alias.name != "*":
                        self.names.add(alias.asname or alias.name)

        visitor = BindingVisitor(statement)
        visitor.visit(statement)
        return visitor.names

    def analyze(self, tree: ast.Module) -> None:
        self.local_function_names = {
            statement.name
            for statement in tree.body
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        self.runner_factory_names = {
            alias.asname or alias.name
            for statement in tree.body
            if isinstance(statement, ast.ImportFrom)
            and statement.module in {"cocotb.runner", "cocotb_tools.runner"}
            for alias in statement.names
            if alias.name == "get_runner"
        }
        if not self._preflight_exact_subset(tree):
            return
        # ``runner`` is the conventional CVDP runner binding used by the
        # benchmark harness snippets. Other receivers must be derived from it
        # or from cocotb's imported ``get_runner`` factory.
        module_env: dict[str, Any] = {"runner": _CVDP_RUNNER}
        self._execute(tree.body, module_env)
        last_function_by_name = {
            statement.name: statement
            for statement in tree.body
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        for statement in tree.body:
            if not isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if last_function_by_name.get(statement.name) is not statement:
                for child_statement in statement.body:
                    self._account_ignored_builds(child_statement, module_env)
                continue
            skipped = False
            for decorator in statement.decorator_list:
                if not isinstance(decorator, ast.Call):
                    decorator_name = (
                        ast.unparse(decorator) if hasattr(ast, "unparse") else ""
                    )
                    if decorator_name.endswith("pytest.mark.skip"):
                        skipped = True
                    elif decorator_name.endswith("pytest.mark.xfail"):
                        self._unresolved(
                            f"pytest xfail marker on {statement.name} makes finite verification incomplete"
                        )
                        skipped = True
                    continue
                decorator_name = (
                    ast.unparse(decorator.func) if hasattr(ast, "unparse") else ""
                )
                if decorator_name.endswith("pytest.mark.skip"):
                    skipped = True
                elif decorator_name.endswith("pytest.mark.skipif"):
                    condition = self._eval(
                        decorator.args[0] if decorator.args else None,
                        module_env,
                    )
                    if condition is _CVDP_EXACT_UNKNOWN:
                        self._unresolved(
                            f"pytest skipif condition for {statement.name} is not statically known"
                        )
                        skipped = True
                    elif bool(condition):
                        skipped = True
                elif decorator_name.endswith("pytest.mark.xfail"):
                    self._unresolved(
                        f"pytest xfail marker on {statement.name} makes finite verification incomplete"
                    )
                    skipped = True
            if skipped:
                for child_statement in statement.body:
                    self._account_ignored_builds(child_statement, module_env)
                continue
            if self._function_mutates_module_mapping(statement, module_env):
                for child_statement in statement.body:
                    self._account_ignored_builds(child_statement, module_env)
                self._unresolved(
                    f"test {statement.name} mutates shared module parameter state"
                )
                continue
            variants: list[dict[str, Any]] = [{}]
            saw_parametrize = False
            invalid_parametrize = False
            parametrized_names: set[str] = set()
            for decorator in statement.decorator_list:
                rows = self._parametrize_rows(decorator, module_env)
                if rows is None:
                    continue
                saw_parametrize = True
                if not rows:
                    invalid_parametrize = True
                    break
                raw_names = self._eval(decorator.args[0], module_env)
                names = (
                    [item.strip() for item in raw_names.split(",") if item.strip()]
                    if isinstance(raw_names, str)
                    else []
                )
                duplicate_names = (
                    {name for name in names if names.count(name) > 1}
                    | (set(names) & parametrized_names)
                )
                if duplicate_names:
                    self._unresolved(
                        "pytest parameter(s) are parametrized more than once: "
                        + ", ".join(sorted(duplicate_names))
                    )
                    invalid_parametrize = True
                    break
                parametrized_names.update(names)
                variants = [
                    {**variant, **row}
                    for variant in variants
                    for row in rows
                ]
                if len(variants) > _CVDP_MAX_SWEEP_CONFIGS:
                    invalid_parametrize = True
                    break
            contains_build = self._contains_build_syntax(statement.body)
            if invalid_parametrize:
                if contains_build:
                    self._unresolved("pytest parameterization containing build() is not literal")
                continue
            is_test = saw_parametrize or statement.name.startswith("test") or any(
                "cocotb" in (ast.unparse(decorator) if hasattr(ast, "unparse") else "")
                for decorator in statement.decorator_list
            )
            if contains_build and not is_test:
                self._unresolved(
                    f"function {statement.name} contains build() but its invocation matrix is unknown"
                )
                continue
            if not contains_build:
                continue
            if any(
                isinstance(node, (ast.Return, ast.Break, ast.Continue))
                for child_statement in statement.body
                for node in ast.walk(child_statement)
            ):
                for child_statement in statement.body:
                    self._account_ignored_builds(child_statement, module_env)
                self._unresolved(
                    f"function {statement.name} containing build() uses unsupported terminating control flow"
                )
                continue
            for variant in variants:
                function_env = self._clone_env(module_env)
                for local_name in self._function_local_bindings(statement):
                    function_env[local_name] = _CVDP_EXACT_UNKNOWN
                for argument in (
                    list(statement.args.posonlyargs)
                    + list(statement.args.args)
                    + list(statement.args.kwonlyargs)
                ):
                    # Function arguments shadow module bindings, including the
                    # conventional ``runner`` name. Literal parametrize rows
                    # below re-establish only values proven by the decorator.
                    function_env[argument.arg] = _CVDP_EXACT_UNKNOWN
                function_env.update(variant)
                self._execute(statement.body, function_env)


def _cvdp_exact_parameter_sweep_plan(
    harness_files: dict,
    parameter_names: set[str],
) -> CVDPParameterSweepPlan:
    """Return an exact finite config list, or an unresolved fail-closed plan."""
    extractor = _CVDPExactSweepExtractor(parameter_names)
    saw_python = False
    for path, content in harness_files.items():
        if not str(path).endswith(".py"):
            continue
        saw_python = True
        try:
            tree = ast.parse(textwrap.dedent(str(content)))
        except SyntaxError:
            if re.search(r"(?:\.\s*|\b)build\s*\(", str(content)):
                extractor._unresolved("a harness containing build() could not be parsed")
            continue
        extractor.analyze(tree)

    # Reuse the broader alias/flow analyzer as a coverage oracle.  The exact
    # interpreter may deliberately not understand a clever build alias; such
    # a call must make the plan unresolved, never masquerade as a defaults-only
    # singleton.
    broad_analysis = _cvdp_parameter_override_analysis(harness_files)
    if broad_analysis.build_call_count > (
        extractor.build_calls + extractor.ignored_build_calls
    ):
        extractor._unresolved(
            "one or more build() calls were detected but their exact parameter mapping was not recovered"
        )

    configurations = tuple(sorted(extractor.configurations))
    if not configurations and parameter_names and not extractor.unresolved_reasons:
        if broad_analysis.build_call_count:
            extractor._unresolved(
                "all detected build() sites are skipped or unreachable"
            )
        else:
            extractor._unresolved(
                "no executable build() call was recovered for the parameterized top"
            )
    if not saw_python and parameter_names:
        extractor._unresolved(
            "no Python harness was available to recover an exact parameter configuration"
        )
    return CVDPParameterSweepPlan(
        configurations=configurations,
        unresolved=bool(extractor.unresolved_reasons),
        unresolved_reasons=tuple(extractor.unresolved_reasons),
    )


@dataclass(frozen=True)
class _SVModuleParameter:
    """One overrideable parameter from a SystemVerilog module header."""

    name: str
    declaration: str


def _module_header_parameter_declarations(
    sv_code: str,
    module_name: str,
) -> tuple[tuple[str, _SVModuleParameter], ...]:
    """Parse ordered ``parameter``/``localparam`` header declarations."""
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

    declarations: list[tuple[str, _SVModuleParameter]] = []
    inherited_kind: str | None = None
    inherited_prefix: str | None = None
    for entry in _split_sv_commas(text[idx + 1:end]):
        raw = entry.strip()
        if not raw:
            continue
        explicit_kind = re.match(r"^(parameter|localparam)\b", raw)
        if explicit_kind is None and inherited_prefix is None:
            continue

        lhs = raw.split("=", 1)[0].strip()
        identifiers = list(re.finditer(r"[A-Za-z_]\w*", lhs))
        if not identifiers:
            continue
        name_match = identifiers[-1]
        name = name_match.group(0)

        if explicit_kind is not None:
            kind = explicit_kind.group(1)
            prefix = lhs[:name_match.start()].strip()
            if not prefix.startswith(kind):
                inherited_kind = None
                inherited_prefix = None
                continue
            inherited_kind = kind
            inherited_prefix = prefix
            declaration = raw
        else:
            kind = inherited_kind
            declaration = f"{inherited_prefix} {raw}"

        if kind is not None:
            declarations.append((kind, _SVModuleParameter(
                name=name,
                declaration=declaration,
            )))

    return tuple(declarations)


def _module_parameters(
    sv_code: str,
    module_name: str,
) -> tuple[_SVModuleParameter, ...]:
    """Return only overrideable parameters from an exact module header."""
    return tuple(
        declaration
        for kind, declaration in _module_header_parameter_declarations(
            sv_code, module_name
        )
        if kind == "parameter"
    )


def _module_localparameters(
    sv_code: str,
    module_name: str,
) -> tuple[_SVModuleParameter, ...]:
    """Return derived local parameters declared in an exact module header."""
    return tuple(
        declaration
        for kind, declaration in _module_header_parameter_declarations(
            sv_code, module_name
        )
        if kind == "localparam"
    )


def _module_body_localparameter_declarations(
    sv_code: str,
    module_name: str,
) -> tuple[_SVModuleParameter, ...]:
    """Parse ordered module-body localparams without changing their types."""
    record = next(
        (candidate for candidate in _module_records(sv_code)
         if candidate[0] == module_name),
        None,
    )
    if record is None:
        return ()
    body = _strip_sv_comments(record[2])
    declarations: list[_SVModuleParameter] = []
    for statement in re.finditer(
        r"\blocalparam\b(?P<body>[^;]*);", body, re.DOTALL
    ):
        inherited_prefix: str | None = None
        for index, entry in enumerate(_split_sv_commas(statement.group("body"))):
            raw = entry.strip()
            lhs = raw.split("=", 1)[0].strip()
            names = list(re.finditer(r"[A-Za-z_]\w*", lhs))
            if not names:
                continue
            name_match = names[-1]
            if index == 0:
                inherited_prefix = (
                    "localparam " + lhs[:name_match.start()].strip()
                ).strip()
            if inherited_prefix is not None:
                declaration = (
                    f"localparam {raw}"
                    if index == 0 else f"{inherited_prefix} {raw}"
                )
                declarations.append(_SVModuleParameter(
                    name=name_match.group(0),
                    declaration=declaration,
                ))
    return tuple(declarations)


def _cvdp_parameter_declaration_parts(
    parameter: _SVModuleParameter,
) -> tuple[str, str] | None:
    """Return a declaration's typed prefix and default expression."""
    lhs, separator, rhs = parameter.declaration.partition("=")
    names = list(re.finditer(r"[A-Za-z_]\w*", lhs))
    if not separator or not names or names[-1].group(0) != parameter.name:
        return None
    name_match = names[-1]
    if lhs[name_match.end():].strip():
        return None
    return lhs[:name_match.start()].strip(), rhs.strip()


def _cvdp_derived_reference_parameter_names(
    sv_code: str,
    module_name: str,
) -> set[str]:
    """Return header parameters whose defaults derive from another parameter.

    CVDP baselines sometimes spell calculated interface widths as overrideable
    ``parameter`` declarations even though the benchmark only sweeps their
    source parameters. Treat such a symbol as a derived constant unless the
    harness explicitly overrides it. Dependencies through header localparams
    are followed, while type-prefix dependencies alone do not make an
    otherwise independent parameter value derived.
    """
    entries = _module_header_parameter_declarations(sv_code, module_name)
    parameter_names = {
        parameter.name for kind, parameter in entries if kind == "parameter"
    }
    symbol_names = {parameter.name for _, parameter in entries}
    rhs_by_name: dict[str, str] = {}
    for _, parameter in entries:
        parts = _cvdp_parameter_declaration_parts(parameter)
        if parts is not None:
            rhs_by_name[parameter.name] = parts[1]

    def rhs_dependencies(name: str) -> set[str]:
        rhs = rhs_by_name.get(name, "")
        return {
            candidate
            for candidate in symbol_names
            if candidate != name
            and re.search(rf"\b{re.escape(candidate)}\b", rhs)
        }

    derived: set[str] = set()
    for name in parameter_names:
        pending = list(rhs_dependencies(name))
        seen: set[str] = set()
        while pending:
            dependency = pending.pop()
            if dependency in seen:
                continue
            seen.add(dependency)
            if dependency in parameter_names:
                derived.add(name)
                break
            pending.extend(rhs_dependencies(dependency) - seen)
    return derived


def _cvdp_required_benchmark_parameter_names(
    *,
    ref_code: str,
    design_name: str,
    harness_files: dict,
) -> set[str]:
    """Return only independently overrideable benchmark parameters."""
    harness_names = set(
        _cvdp_parameter_override_analysis(harness_files).parameter_names
    )
    reference_names = _module_parameter_names(ref_code, design_name)
    derived_reference_names = (
        _cvdp_derived_reference_parameter_names(ref_code, design_name)
        - harness_names
    )
    return harness_names | (reference_names - derived_reference_names)


def _cvdp_header_localparam_dependencies(
    sv_code: str,
    module_name: str,
) -> dict[str, str]:
    dependencies: dict[str, str] = {}
    for parameter in _module_localparameters(sv_code, module_name):
        parts = _cvdp_parameter_declaration_parts(parameter)
        if parts is not None:
            dependencies[parameter.name] = f"{parts[0]} {parts[1]}"
    return dependencies


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
        r"sparkle_invalid_(?:nat_parameter|dimension|nat_work_width)_\d+\b"
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
                name_match = names[-1]
                dependencies[name_match.group(0)] = (
                    f"{lhs[:name_match.start()]} {rhs}"
                )
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
        parameter = next(
            (item for item in parameters if item.name == name), None
        )
        parts = (
            _cvdp_parameter_declaration_parts(parameter)
            if parameter is not None else None
        )
        dependency_text = (
            f"{parts[0]} {parts[1]}" if parts is not None else declaration
        )
        dependencies = (
            _cvdp_parameters_in_text(dependency_text, parameter_names) - active
        )
        active.update(dependencies)
        pending.extend(dependencies)
    return active


def _cvdp_parameters_reachable_from_text(
    text: str,
    parameters: tuple[_SVModuleParameter, ...],
    localparams: dict[str, str],
) -> set[str]:
    """Trace type/RTL use through header and body localparam aliases."""
    parameter_names = {parameter.name for parameter in parameters}
    direct = _cvdp_parameters_in_text(text, parameter_names)
    live_localparams = _cvdp_parameters_in_text(text, set(localparams))
    pending = list(live_localparams)
    while pending:
        name = pending.pop()
        dependency_text = localparams.get(name, "")
        direct.update(_cvdp_parameters_in_text(
            dependency_text, parameter_names
        ))
        dependencies = _cvdp_parameters_in_text(
            dependency_text, set(localparams)
        ) - live_localparams
        live_localparams.update(dependencies)
        pending.extend(dependencies)
    return _cvdp_parameter_dependency_closure(direct, parameters)


def _cvdp_module_localparam_dependencies(
    sv_code: str,
    module_name: str,
) -> dict[str, str]:
    _, body_localparams = _cvdp_localparam_dependencies(
        _cvdp_parameter_usage_text(sv_code, module_name)
    )
    return {
        **_cvdp_header_localparam_dependencies(sv_code, module_name),
        **body_localparams,
    }


def _cvdp_active_module_parameters(
    sv_code: str,
    module_name: str,
    parameters: tuple[_SVModuleParameter, ...],
) -> set[str]:
    """Find parameters with a transitive path into emitted hardware."""
    usage_text, _ = _cvdp_localparam_dependencies(
        _cvdp_parameter_usage_text(sv_code, module_name)
    )
    return _cvdp_parameters_reachable_from_text(
        usage_text,
        parameters,
        _cvdp_module_localparam_dependencies(sv_code, module_name),
    )


_CVDP_SV_CONSTANT_KEYWORDS = frozenset({
    "automatic", "bit", "byte", "const", "int", "integer", "localparam",
    "logic", "longint", "parameter", "real", "realtime", "reg", "shortint",
    "shortreal", "signed", "static", "string", "time", "type", "unsigned",
    "wire",
})


@dataclass(frozen=True)
class _CVDPCoreTypeMirrorContract:
    declarations: tuple[str, ...]
    rename_by_name: dict[str, str]


def _cvdp_rewrite_identifiers(text: str, renames: dict[str, str]) -> str:
    if not renames:
        return text
    pattern = re.compile(
        r"\b(" + "|".join(
            re.escape(name)
            for name in sorted(renames, key=len, reverse=True)
        ) + r")\b"
    )
    return pattern.sub(lambda match: renames[match.group(1)], text)


def _cvdp_constant_expression_identifiers(text: str) -> set[str]:
    """Return user identifiers after removing literals/system functions."""
    scrubbed = re.sub(r'"(?:\\.|[^"\\])*"', "", text)
    scrubbed = re.sub(
        r"(?:\d+)?\s*'\s*[sS]?[bBoOdDhH][0-9a-fA-F_xXzZ?]+",
        "",
        scrubbed,
    )
    scrubbed = re.sub(r"'\s*[01xXzZ]", "", scrubbed)
    scrubbed = re.sub(r"\$[A-Za-z_]\w*", "", scrubbed)
    return set(re.findall(r"\b[A-Za-z_]\w*\b", scrubbed))


def _cvdp_core_constant_dependencies(
    text: str,
    symbol_names: set[str],
    *,
    context: str,
) -> set[str]:
    if "::" in text:
        raise CVDPAdapterContractError(
            f"{CVDP_ADAPTER_ERROR}: core {context} uses a package-scoped "
            "constant that cannot be mirrored safely in the adapter."
        )
    identifiers = _cvdp_constant_expression_identifiers(text)
    unknown = identifiers - symbol_names - _CVDP_SV_CONSTANT_KEYWORDS
    if unknown:
        raise CVDPAdapterContractError(
            f"{CVDP_ADAPTER_ERROR}: core {context} depends on unsupported "
            f"identifier(s) {', '.join(sorted(unknown))}; its type cannot be "
            "mirrored safely in the adapter."
        )
    return identifiers & symbol_names


def _cvdp_core_type_mirror_contract(
    *,
    sv_code: str,
    module_name: str,
    required_types: list[str],
    forwarded_names: set[str],
    reserved_names: set[str],
) -> _CVDPCoreTypeMirrorContract:
    """Mirror the exact core constant closure into an isolated wrapper scope."""
    ordered_entries: list[tuple[str, _SVModuleParameter]] = list(
        _module_header_parameter_declarations(sv_code, module_name)
    )
    ordered_entries.extend(
        ("localparam", declaration)
        for declaration in _module_body_localparameter_declarations(
            sv_code, module_name
        )
    )

    occurrences: dict[str, list[tuple[str, _SVModuleParameter]]] = {}
    for entry in ordered_entries:
        occurrences.setdefault(entry[1].name, []).append(entry)
    symbol_names = set(occurrences)

    required: set[str] = set()
    for typ in required_types:
        required.update(_cvdp_core_constant_dependencies(
            typ,
            symbol_names,
            context=f"type '{typ}'",
        ))

    dependencies_by_name: dict[str, set[str]] = {}
    pending = list(required)
    while pending:
        name = pending.pop()
        entries = occurrences.get(name, [])
        if len(entries) != 1:
            raise CVDPAdapterContractError(
                f"{CVDP_ADAPTER_ERROR}: core constant '{name}' has "
                "ambiguous declarations and cannot be mirrored safely."
            )
        kind, parameter = entries[0]
        parts = _cvdp_parameter_declaration_parts(parameter)
        if parts is None:
            raise CVDPAdapterContractError(
                f"{CVDP_ADAPTER_ERROR}: core constant declaration "
                f"'{parameter.declaration}' cannot be mirrored safely."
            )
        prefix, rhs = parts
        dependency_text = prefix
        if kind != "parameter" or name not in forwarded_names:
            dependency_text += " " + rhs
        dependencies = _cvdp_core_constant_dependencies(
            dependency_text,
            symbol_names,
            context=f"constant '{name}'",
        )
        dependencies.discard(name)
        dependencies_by_name[name] = dependencies
        new_dependencies = dependencies - required
        required.update(new_dependencies)
        pending.extend(new_dependencies)

    occupied = set(reserved_names)
    rename_by_name: dict[str, str] = {}
    for _, parameter in ordered_entries:
        if parameter.name not in required or parameter.name in rename_by_name:
            continue
        stem = f"_cvdp_core_{parameter.name}"
        candidate = stem
        suffix = 2
        while candidate in occupied:
            candidate = f"{stem}_{suffix}"
            suffix += 1
        rename_by_name[parameter.name] = candidate
        occupied.add(candidate)

    order_index = {
        parameter.name: index
        for index, (_, parameter) in enumerate(ordered_entries)
        if parameter.name in required
    }
    ordered_required: list[str] = []
    state: dict[str, int] = {}

    def visit(name: str) -> None:
        marker = state.get(name, 0)
        if marker == 2:
            return
        if marker == 1:
            raise CVDPAdapterContractError(
                f"{CVDP_ADAPTER_ERROR}: core constant dependency cycle "
                f"involving '{name}' cannot be mirrored safely."
            )
        state[name] = 1
        for dependency in sorted(
            dependencies_by_name.get(name, ()),
            key=lambda item: order_index.get(item, len(order_index)),
        ):
            visit(dependency)
        state[name] = 2
        ordered_required.append(name)

    for name in sorted(required, key=lambda item: order_index[item]):
        visit(name)

    declaration_by_name = {
        parameter.name: (kind, parameter)
        for kind, parameter in ordered_entries
        if parameter.name in required
    }
    declarations: list[str] = []
    for name in ordered_required:
        kind, parameter = declaration_by_name[name]
        parts = _cvdp_parameter_declaration_parts(parameter)
        assert parts is not None
        prefix, rhs = parts
        local_prefix = re.sub(
            r"^(?:parameter|localparam)\b",
            "localparam",
            prefix,
            count=1,
        )
        local_prefix = _cvdp_rewrite_identifiers(
            local_prefix, rename_by_name
        )
        if kind == "parameter" and name in forwarded_names:
            mirror_rhs = name
        else:
            mirror_rhs = _cvdp_rewrite_identifiers(rhs, rename_by_name)
        declarations.append(
            f"{local_prefix} {rename_by_name[name]} = {mirror_rhs};"
        )

    return _CVDPCoreTypeMirrorContract(
        declarations=tuple(declarations),
        rename_by_name=rename_by_name,
    )


def _cvdp_render_core_type(
    typ: str,
    contract: _CVDPCoreTypeMirrorContract,
) -> str:
    return _cvdp_rewrite_identifiers(typ, contract.rename_by_name)


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
    localparameter_names: tuple[str, ...]


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
    harness_parameter_names = set(analysis.parameter_names)
    reference_entries = _module_header_parameter_declarations(
        ref_code, design_name
    )
    reference_localparameters = _module_localparameters(ref_code, design_name)
    reference_parameters = _module_parameters(ref_code, design_name)
    core_parameters = _module_parameters(sv_code, sparkle_mod_name)
    reference_by_name = {parameter.name: parameter for parameter in reference_parameters}
    core_by_name = {parameter.name: parameter for parameter in core_parameters}
    derived_reference_names = (
        _cvdp_derived_reference_parameter_names(ref_code, design_name)
        - harness_parameter_names
    )

    # A cocotb handle can expose a parameter added by a newer benchmark even
    # when the checked-in reference module is an older baseline. Such a
    # handle is authoritative only when the exact generated core really has
    # the same overrideable parameter; otherwise it remains a derived handle
    # (handled later) or an adapter error. In particular, never turn an
    # uppercase parameter handle into a synthetic output port.
    parsed_usage = _cvdp_parse_harness_usage(harness_files)
    parsed_reference_ports = _parse_ref_module_ports_strict(
        ref_code, design_name
    ) or []
    reference_port_names = {
        name for _, _, name in parsed_reference_ports
    }
    observed_parameter_handles = (
        parsed_usage["params"]
        | (
            (parsed_usage["ports"] - reference_port_names)
            & set(core_by_name)
        )
    )
    core_parameter_handles = (
        observed_parameter_handles - derived_reference_names
    ) & set(core_by_name)
    required_names = (
        (set(reference_by_name) - derived_reference_names)
        | harness_parameter_names
        | core_parameter_handles
    )
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
    for kind, parameter in reference_entries:
        declaration = parameter.declaration
        if kind == "parameter" and parameter.name in derived_reference_names:
            declaration = re.sub(
                r"^\s*parameter\b", "localparam", declaration, count=1
            )
        declarations.append(declaration)
        if kind == "parameter" and parameter.name not in derived_reference_names:
            forwarded_names.append(parameter.name)
    new_required_names = required_names - set(reference_by_name)
    for parameter in core_parameters:
        if parameter.name in new_required_names:
            declarations.append(parameter.declaration)
            forwarded_names.append(parameter.name)

    return _CVDPWrapperParameterContract(
        declarations=tuple(declarations),
        forwarded_names=tuple(forwarded_names),
        localparameter_names=tuple(
            sorted(
                {parameter.name for parameter in reference_localparameters}
                | derived_reference_names
            )
        ),
    )


def _cvdp_match_port(
    name: str,
    candidates: list[tuple[str, str, str]],
    *,
    direction: str | None = None,
) -> tuple[str, str, str] | None:
    """Find a uniquely provable exact/alias-equivalent candidate port."""
    filtered = [p for p in candidates if direction is None or p[0] == direction]
    wanted = [name, f"_gen_{name}"]
    lowered = {n.lower() for n in wanted}
    tiers = (
        [port for port in filtered if port[2] in wanted],
        [port for port in filtered if port[2].lower() in lowered],
        [port for port in filtered if _ports_equivalent(port[2], name)],
    )
    for matches in tiers:
        unique = {port[2]: port for port in matches}
        if len(unique) == 1:
            return next(iter(unique.values()))
        if len(unique) > 1:
            return None
    return None


def _cvdp_bridge_reset_expr(sp_name: str, matched_name: str) -> str:
    if (
        _is_reset_like(sp_name)
        and _is_reset_like(matched_name)
        and _is_active_low_reset(sp_name) != _is_active_low_reset(matched_name)
    ):
        return f"~{matched_name}"
    return matched_name


def _cvdp_infer_concat_order(
    sv_code: str,
    sp_out_name: str,
    module_name: str | None = None,
) -> list[str] | None:
    """Infer names packed into a Sparkle bundled output, if visible."""
    fields = _cvdp_infer_concat_fields(sv_code, sp_out_name, module_name)
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
    benchmark_ports: list[tuple[str, str, str]] | None = None,
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
    sp_inputs = [(d, t, n) for d, t, n in sparkle_ports if d == "input"]
    sp_outputs = [(d, t, n) for d, t, n in sparkle_ports if d == "output"]
    core_parameter_declarations = _module_parameters(
        sv_code, sparkle_mod_name
    )
    core_parameter_names = {
        parameter.name for parameter in core_parameter_declarations
    }
    core_localparameter_declarations = (
        *_module_localparameters(sv_code, sparkle_mod_name),
        *_module_body_localparameter_declarations(sv_code, sparkle_mod_name),
    )
    core_localparameter_names = {
        parameter.name for parameter in core_localparameter_declarations
    }
    exact_reference_present = bool(_exact_module_text(ref_code, design_name))
    # Context may contain reusable helper modules while omitting the requested
    # top. Importing a "closest" helper creates a hybrid interface and can
    # leak helper-only derived widths into the wrapper. Only the exact design
    # module is authoritative here; otherwise infer observed ports from the
    # Sparkle core and its tuple provenance.
    parsed_ref_ports = _parse_ref_module_ports_strict(
        ref_code, design_name
    ) if exact_reference_present else None
    if exact_reference_present and parsed_ref_ports is None:
        raise CVDPAdapterContractError(
            f"{CVDP_ADAPTER_ERROR}: exact reference top '{design_name}' "
            "has an interface the adapter cannot parse completely."
        )
    raw_ref_ports = parsed_ref_ports or []
    ref_ports = [(d, _cvdp_normalize_type(t), n) for d, t, n in raw_ref_ports]
    ref_port_names = {n for _, _, n in ref_ports}

    if benchmark_ports is None:
        expected_ports = list(ref_ports)
    else:
        expected_ports = [
            (direction, _cvdp_normalize_type(typ), name)
            for direction, typ, name in benchmark_ports
        ]
        expected_names = [name for _, _, name in expected_ports]
        if len(expected_names) != len(set(expected_names)):
            raise CVDPAdapterContractError(
                f"{CVDP_ADAPTER_ERROR}: authoritative benchmark interface "
                "contains duplicate port names."
            )

    # A handle that the simple harness parser initially classified as a port
    # is still a parameter handle when the exact core declares that constant
    # and neither the core nor the reference declares a same-named port. This
    # is evidence-based and avoids manufacturing outputs such as NUM_DICE from
    # capitalization alone.
    core_port_names = {name for _, _, name in sparkle_ports}
    actual_constant_handles = (
        usage["ports"]
        & (core_parameter_names | core_localparameter_names)
        - core_port_names
        - ref_port_names
    )
    if actual_constant_handles:
        usage = {key: set(names) for key, names in usage.items()}
        usage["ports"] -= actual_constant_handles
        usage["inputs"] -= actual_constant_handles
        usage["outputs"] -= actual_constant_handles
        usage["params"] |= actual_constant_handles

    by_name = {n: (d, t, n) for d, t, n in expected_ports}

    declared_wrapper_type_constants = (
        set(parameter_contract.forwarded_names)
        | set(parameter_contract.localparameter_names)
    )

    def has_unresolved_type_identifier(typ: str) -> bool:
        identifiers = (
            _cvdp_constant_expression_identifiers(typ)
            - _CVDP_SV_CONSTANT_KEYWORDS
        )
        return bool(identifiers - declared_wrapper_type_constants)

    ref_internal_arrays = _cvdp_internal_unpacked_arrays(ref_code, design_name)
    observed_internal_arrays = {
        name: ref_internal_arrays[name]
        for name in sorted(usage["ports"] - set(by_name))
        if name in ref_internal_arrays
    }
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
            sparkle_mod_name,
        ):
            if field_type:
                bundled_type_by_output[out_port[2]] = field_type

    # A resolved prompt contract may replace one stale reference output. The
    # generated core is allowed to expose that one new observed output through
    # a differently named whole-output port only with exact semantic alias
    # provenance. This path is unavailable without the authoritative resolved
    # interface, so a raw old baseline can never silently lose a public port.
    whole_output_alias_by_expected: dict[
        str,
        tuple[tuple[str, str, str], str, tuple[str, ...]],
    ] = {}
    if exact_reference_present and benchmark_ports is not None:
        baseline_output_names = {
            name for direction, _, name in ref_ports if direction == "output"
        }
        resolved_output_by_name = {
            name: port
            for port in expected_ports
            for direction, _, name in [port]
            if direction == "output"
        }
        new_observed_outputs = (
            set(usage["outputs"])
            & set(resolved_output_by_name)
            - baseline_output_names
        )
        if len(new_observed_outputs) == 1:
            expected_name = next(iter(new_observed_outputs))
            other_outputs = [
                port
                for name, port in resolved_output_by_name.items()
                if name != expected_name
            ]
            consumed_core_outputs: set[str] = set()
            other_outputs_are_direct = True
            for expected_port in other_outputs:
                available = [
                    port
                    for port in sp_outputs
                    if port[2] not in consumed_core_outputs
                ]
                matched = _cvdp_match_port(
                    expected_port[2], available, direction="output"
                )
                if matched is None:
                    other_outputs_are_direct = False
                    break
                consumed_core_outputs.add(matched[2])
            unconsumed_core_outputs = [
                port
                for port in sp_outputs
                if port[2] not in consumed_core_outputs
            ]
            semantic_name = f"_gen_{expected_name}"
            alias_matches: list[
                tuple[tuple[str, str, str], tuple[str, ...]]
            ] = []
            for core_output in sp_outputs:
                chain = _cvdp_unique_plain_alias_chain(
                    sv_code,
                    sparkle_mod_name,
                    source_name=core_output[2],
                    semantic_name=semantic_name,
                )
                if chain is not None:
                    alias_matches.append((core_output, chain))
            if (
                other_outputs_are_direct
                and len(unconsumed_core_outputs) == 1
                and len(alias_matches) == 1
                and alias_matches[0][0][2]
                == unconsumed_core_outputs[0][2]
                and not _cvdp_infer_concat_fields(
                    sv_code,
                    unconsumed_core_outputs[0][2],
                    sparkle_mod_name,
                )
            ):
                whole_output_alias_by_expected[expected_name] = (
                    unconsumed_core_outputs[0],
                    semantic_name,
                    alias_matches[0][1],
                )

    # With no exact reference top, cardinality plus the absence of tuple
    # packing proves a whole-output mapping: one observed output is the one
    # core output. Preserve its exact symbolic type instead of inventing a
    # scalar wrapper. This does not permit multi-output elimination or expose
    # a packed tuple with hidden fields as one benchmark output.
    whole_output_type_by_name: dict[str, str] = {}
    if (
        not exact_reference_present
        and len(usage["outputs"]) == 1
        and len(sp_outputs) == 1
        and not _cvdp_infer_concat_fields(
            sv_code, sp_outputs[0][2], sparkle_mod_name
        )
    ):
        whole_output_name = next(iter(usage["outputs"]))
        whole_output_type_by_name[whole_output_name] = sp_outputs[0][1]

    # Prompt-only interfaces can use a derived width handle that is not a
    # public wrapper constant (for example ENCODED_DATA or COUNT_WIDTH). With
    # no exact target reference, the already-proven whole-output mapping may
    # supply the core's symbolic type. Resolvable prompt types remain intact,
    # so their independent width guard can still expose a disagreement.
    if whole_output_type_by_name:
        expected_ports = [
            (
                direction,
                whole_output_type_by_name[name]
                if (
                    direction == "output"
                    and name in whole_output_type_by_name
                    and has_unresolved_type_identifier(typ)
                ) else typ,
                name,
            )
            for direction, typ, name in expected_ports
        ]
        by_name = {n: (d, t, n) for d, t, n in expected_ports}

    # An exact reference can be an older baseline than the harness. Extend it
    # only from exact core-port identity or from uniquely named provenance in
    # the core's single packed output. Any other new handle remains rejected.
    safe_extension_ports: dict[str, tuple[str, str, str]] = {}
    if exact_reference_present:
        reference_parameters = _module_parameter_names(ref_code, design_name)
        reference_localparameters = {
            parameter.name
            for parameter in _module_localparameters(ref_code, design_name)
        }
        unknown_ports = (
            set(usage["ports"]) - ref_port_names - set(ref_internal_arrays)
        )
        for name in sorted(unknown_ports):
            direction = "input" if name in usage["inputs"] else "output"
            core_match = _cvdp_match_port(
                name, sparkle_ports, direction=direction
            )
            if core_match is not None:
                safe_extension_ports[name] = (
                    direction, _cvdp_normalize_type(core_match[1]), name
                )
            elif direction == "output" and name in whole_output_alias_by_expected:
                resolved_port = by_name[name]
                safe_extension_ports[name] = (
                    direction,
                    resolved_port[1],
                    name,
                )
            elif direction == "output" and name in bundled_type_by_output:
                safe_extension_ports[name] = (
                    direction,
                    _cvdp_normalize_type(bundled_type_by_output[name]),
                    name,
                )
        unknown_parameters = (
            set(usage["params"])
            - reference_parameters
            - reference_localparameters
            - core_parameter_names
            - core_localparameter_names
        )
        unmapped_new_ports = unknown_ports - set(safe_extension_ports)
        unknown_handles = sorted(unmapped_new_ports | unknown_parameters)
        if unknown_handles:
            raise CVDPAdapterContractError(
                f"{CVDP_ADAPTER_ERROR}: harness handle(s) "
                f"{', '.join(unknown_handles)} are not declared by the exact "
                "reference top interface and have no unique exact-core "
                "mapping."
            )

    for name in sorted(usage["ports"]):
        if name in by_name:
            continue
        if name in observed_internal_arrays:
            continue
        direction = "input" if name in usage["inputs"] else "output"
        if exact_reference_present:
            port = safe_extension_ports[name]
            by_name[name] = port
            expected_ports.append(port)
            continue
        sp_match = _cvdp_match_port(
            name, sparkle_ports, direction=direction
        )
        if sp_match is not None:
            typ = sp_match[1]
        elif name in usage["outputs"] and name in bundled_type_by_output:
            typ = bundled_type_by_output[name]
        elif name in whole_output_type_by_name:
            typ = whole_output_type_by_name[name]
        else:
            typ = "logic"
        port = (direction, _cvdp_normalize_type(typ), name)
        by_name[name] = port
        expected_ports.append(port)

    if bundled_type_by_output:
        expected_ports = [
            (
                d,
                bundled_type_by_output.get(n, t)
                if d == "output" and n not in ref_port_names else t,
                n,
            )
            for d, t, n in expected_ports
        ]

    # Direct semantic-name provenance can resolve the same prompt-only type
    # problem for an existing output. Do not use width, position, or a
    # non-unique alias match as evidence.
    if not exact_reference_present:
        provisional_outputs = [
            (d, t, n) for d, t, n in expected_ports if d == "output"
        ]
        bundle_fields_by_core_output = {
            name: (_cvdp_infer_concat_fields(
                sv_code, name, sparkle_mod_name
            ) or [])
            for _, _, name in sp_outputs
        }
        direct_output_type_by_name: dict[str, str] = {}
        for expected_output in provisional_outputs:
            _, expected_type, output_name = expected_output
            if not has_unresolved_type_identifier(expected_type):
                continue
            candidates = [
                port for port in sp_outputs
                if (
                    len(provisional_outputs) == 1
                    or not bundle_fields_by_core_output[port[2]]
                )
            ]
            core_output = _cvdp_match_port(
                output_name, candidates, direction="output"
            )
            if core_output is not None:
                direct_output_type_by_name[output_name] = core_output[1]
        if direct_output_type_by_name:
            expected_ports = [
                (
                    direction,
                    direct_output_type_by_name.get(name, typ)
                    if direction == "output" else typ,
                    name,
                )
                for direction, typ, name in expected_ports
            ]

    if not expected_ports or not sparkle_mod_name:
        return None

    expected_inputs = [(d, t, n) for d, t, n in expected_ports if d == "input"]
    expected_outputs = [(d, t, n) for d, t, n in expected_ports if d == "output"]
    param_decls = list(parameter_contract.declarations)
    param_names = set(parameter_contract.forwarded_names)
    core_localparam_dependencies = _cvdp_module_localparam_dependencies(
        sv_code, sparkle_mod_name
    )
    localparameter_names = set(parameter_contract.localparameter_names)
    core_localparameter_handle_names = (
        set(usage["params"])
        & core_localparameter_names
        - localparameter_names
    )
    derived_parameter_names = (
        set(usage["params"])
        - param_names
        - localparameter_names
        - core_localparameter_handle_names
    )
    exact_reference_present = bool(_exact_module_text(ref_code, design_name))
    derived_width_bindings: dict[str, str] = {}
    if derived_parameter_names and not exact_reference_present:
        candidate_bindings = _cvdp_derived_width_bindings(
            derived_parameter_names,
            expected_outputs,
        )
        output_types = {name: typ for _, typ, name in expected_outputs}
        derived_width_bindings = {
            name: port_name
            for name, port_name in candidate_bindings.items()
            if not re.search(
                rf"\b{re.escape(name)}\b",
                output_types.get(port_name, ""),
            )
        }
    unresolved_derived_parameters = (
        derived_parameter_names - set(derived_width_bindings)
    )
    if unresolved_derived_parameters:
        raise CVDPAdapterContractError(
            f"{CVDP_ADAPTER_ERROR}: harness-observed derived parameter(s) "
            f"{', '.join(sorted(unresolved_derived_parameters))} have no "
            "unique, width-proven output association."
        )

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
        core_parameters = _cvdp_parameters_reachable_from_text(
            sp_t,
            core_parameter_declarations,
            core_localparam_dependencies,
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

    input_binding_by_core: dict[
        str,
        tuple[tuple[str, str, str], tuple[str, str, str] | None, str],
    ] = {}
    used_expected_inputs: set[str] = set()
    ordinary_consumer_by_expected: dict[str, str] = {}
    direct_input_type_by_name: dict[str, str] = {}
    ordinary_sp_inputs = [
        port for port in sp_inputs if port[2] not in {"clk", "rst"}
    ]
    abi_sp_inputs = [
        port for port in sp_inputs if port[2] in {"clk", "rst"}
    ]

    # Generated/source-level inputs retain the ordinary one-to-one contract.
    # In particular, a generated input named `_gen_clk` is not the compiler's
    # implicit clock ABI and therefore may neither use role matching nor be
    # silently tied off.
    for sp_port in ordinary_sp_inputs:
        _, _, sn = sp_port
        available = [
            port for port in expected_inputs
            if port[2] not in used_expected_inputs
        ]
        base = sn[5:] if sn.startswith("_gen_") else sn
        matched_port = _cvdp_match_port(
            base, available, direction="input"
        )
        if matched_port is None:
            raise CVDPAdapterContractError(
                f"{CVDP_ADAPTER_ERROR}: Sparkle input '{sn}' has no "
                "unique benchmark input mapping."
            )
        matched_name = matched_port[2]
        used_expected_inputs.add(matched_name)
        ordinary_consumer_by_expected[matched_name] = sn
        require_native_parameterized_core_port(sp_port, matched_port)
        connection = _cvdp_bridge_reset_expr(sn, matched_name)
        input_binding_by_core[sn] = (sp_port, matched_port, connection)
        if (
            not exact_reference_present
            and has_unresolved_type_identifier(matched_port[1])
        ):
            direct_input_type_by_name[matched_name] = sp_port[1]

    # Sparkle's exact raw `clk`/`rst` ports are compiler ABI inputs, distinct
    # from source-level generated binders.  Prefer one unique structured
    # harness Clock target; without one, accept only a unique exact allowlisted
    # clock name.  It may fan out from the same public port
    # as one generated clock binder, which preserves that binder's ordinary
    # one-to-one mapping while actually clocking compiler-introduced state.
    # Reset semantics are not encoded in this ABI yet, so keep only the exact
    # raw `rst` deasserted instead of guessing polarity or reset kind.
    for sp_port in abi_sp_inputs:
        _, _, sn = sp_port
        if sn == "rst":
            input_binding_by_core[sn] = (sp_port, None, "1'b0")
            continue

        clock_errors = usage.get("clock_errors", set())
        if clock_errors:
            raise CVDPAdapterContractError(
                f"{CVDP_ADAPTER_ERROR}: Sparkle implicit clock 'clk' has "
                "unresolved structured harness Clock evidence."
            )
        harness_clocks = usage.get("clocks", set())
        if len(harness_clocks) > 1:
            raise CVDPAdapterContractError(
                f"{CVDP_ADAPTER_ERROR}: Sparkle implicit clock 'clk' has "
                "ambiguous structured harness Clock evidence."
            )
        if harness_clocks:
            matched_name = next(iter(harness_clocks))
            matched_port = next(
                (port for port in expected_inputs if port[2] == matched_name),
                None,
            )
            if matched_port is None:
                raise CVDPAdapterContractError(
                    f"{CVDP_ADAPTER_ERROR}: structured harness clock "
                    f"'{matched_name}' is not a benchmark input."
                )
        else:
            exact_name_clocks = [
                port for port in expected_inputs
                if port[2] in _CVDP_EXACT_CLOCK_PORT_NAMES
            ]
            if len(exact_name_clocks) != 1:
                raise CVDPAdapterContractError(
                    f"{CVDP_ADAPTER_ERROR}: Sparkle implicit clock 'clk' "
                    "has no structured harness Clock(dut.<port>, ...) "
                    "evidence and no unique exact-name clock contract."
                )
            matched_port = exact_name_clocks[0]
            matched_name = matched_port[2]
        existing_consumer = ordinary_consumer_by_expected.get(matched_name)
        if (
            existing_consumer is not None
            and not existing_consumer.startswith("_gen_")
        ):
            raise CVDPAdapterContractError(
                f"{CVDP_ADAPTER_ERROR}: benchmark clock '{matched_name}' "
                "cannot be shared by Sparkle implicit clock 'clk' and "
                f"non-generated input '{existing_consumer}'."
            )
        used_expected_inputs.add(matched_name)
        require_native_parameterized_core_port(sp_port, matched_port)
        input_binding_by_core[sn] = (sp_port, matched_port, matched_name)

    input_bindings = [
        input_binding_by_core[sp_port[2]] for sp_port in sp_inputs
    ]

    # Apply an input type only after the ordinary one-to-one matcher has
    # proved its semantic binding. Raw compiler ABI ports are ineligible, and
    # exact-reference declarations remain authoritative.
    if direct_input_type_by_name:
        expected_ports = [
            (
                direction,
                direct_input_type_by_name.get(name, typ)
                if direction == "input" else typ,
                name,
            )
            for direction, typ, name in expected_ports
        ]

    unconsumed_inputs = [
        name for _, _, name in expected_inputs
        if name not in used_expected_inputs
    ]
    if unconsumed_inputs:
        raise CVDPAdapterContractError(
            f"{CVDP_ADAPTER_ERROR}: benchmark input(s) "
            f"{', '.join(unconsumed_inputs)} are not consumed exactly once "
            "by the Sparkle core."
        )

    bundle_fields_by_output = {
        name: (_cvdp_infer_concat_fields(
            sv_code, name, sparkle_mod_name
        ) or [])
        for _, _, name in sp_outputs
    }
    bundle_type_lookup = _cvdp_signal_type_lookup(
        sv_code, sparkle_ports, sparkle_mod_name
    )
    required_core_types = [typ for _, typ, _ in sparkle_ports]
    for bundle_fields in bundle_fields_by_output.values():
        for field in bundle_fields:
            shape_type = _cvdp_tuple_field_shape_type(
                field, bundle_type_lookup
            )
            if shape_type is not None:
                required_core_types.append(shape_type)
    required_core_types.extend(sorted(core_localparameter_handle_names))

    reserved_mirror_names = (
        {name for _, _, name in expected_ports}
        | {name for _, _, name in sparkle_ports}
        | param_names
        | localparameter_names
        | derived_parameter_names
        | core_localparameter_handle_names
        | {f"{name}_wire" for _, _, name in sparkle_ports}
    )
    core_type_mirrors = _cvdp_core_type_mirror_contract(
        sv_code=sv_code,
        module_name=sparkle_mod_name,
        required_types=required_core_types,
        forwarded_names=param_names,
        reserved_names=reserved_mirror_names,
    )

    rendered_expected_ports = [
        (
            direction,
            (
                typ
                if name in ref_port_names
                else _cvdp_render_core_type(typ, core_type_mirrors)
            ),
            name,
        )
        for direction, typ, name in expected_ports
    ]
    core_localparameter_by_name = {
        declaration.name: declaration
        for declaration in core_localparameter_declarations
    }
    public_core_localparameter_declarations: list[str] = []
    for name in sorted(core_localparameter_handle_names):
        declaration = core_localparameter_by_name[name]
        parts = _cvdp_parameter_declaration_parts(declaration)
        # Requiring the name above already made the mirror contract validate
        # uniqueness and syntax, so this is an internal consistency check.
        assert parts is not None
        prefix, _ = parts
        rendered_prefix = _cvdp_render_core_type(prefix, core_type_mirrors)
        mirror_name = core_type_mirrors.rename_by_name[name]
        public_core_localparameter_declarations.append(
            f"{rendered_prefix} {name} = {mirror_name}"
        )
    header_param_decls = [
        *param_decls,
        *(
            declaration.rstrip(";")
            for declaration in core_type_mirrors.declarations
        ),
        *public_core_localparameter_declarations,
    ]

    tick = chr(96)
    lines = [
        tick + "ifdef YOSYS",
        tick + "define CVDP_ADAPTER_FATAL(message)",
        tick + "else",
        tick + "define CVDP_ADAPTER_FATAL(message) $fatal(1, message)",
        tick + "endif",
        f"module {design_name}",
    ]
    if header_param_decls:
        lines.append(" #(")
        lines.append(",\n".join(
            f"    {decl}" for decl in header_param_decls
        ))
        lines.append(")")
    lines.append(" (")
    lines.append(",\n".join(
        f"    {direction} {typ} {name}"
        for direction, typ, name in rendered_expected_ports
    ))
    lines.append(");")
    lines.append("")
    for name, port_name in derived_width_bindings.items():
        lines.append(f"    localparam integer {name} = $bits({port_name});")
    if derived_width_bindings:
        lines.append("")

    for note in wrapper_notes:
        lines.append(f"    // CVDP adapter diagnostic: {note}")
    if wrapper_notes:
        lines.append("")

    for _, t, n in sp_outputs:
        core_type = _cvdp_render_core_type(t, core_type_mirrors)
        lines.append(f"    {core_type} {n}_wire;")
    if sp_outputs:
        lines.append("")
    input_bridge_by_core: dict[str, str] = {}
    for sp_port, _, connection in input_bindings:
        _, sp_type, sp_name = sp_port
        bridge_name = f"_cvdp_input_{sp_name}_wire"
        input_bridge_by_core[sp_name] = bridge_name
        core_type = _cvdp_render_core_type(sp_type, core_type_mirrors)
        lines.append(f"    {core_type} {bridge_name};")
        lines.append(f"    assign {bridge_name} = {connection};")
    if input_bindings:
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
        inst_conns.append(
            f"        .{sn}({input_bridge_by_core[sn]})"
        )


    lines.append(",\n".join(inst_conns))
    lines.append("    );")
    lines.append("")

    input_guard_counts: dict[str, int] = {}
    for _, matched_port, _ in input_bindings:
        if matched_port is None:
            continue
        expected_name = matched_port[2]
        input_guard_counts[expected_name] = (
            input_guard_counts.get(expected_name, 0) + 1
        )
    for sp_port, matched_port, _ in input_bindings:
        if matched_port is None:
            continue
        _, _, sp_name = sp_port
        _, _, expected_name = matched_port
        bridge_name = input_bridge_by_core[sp_name]
        guard_name = expected_name
        if input_guard_counts[expected_name] > 1:
            guard_name = f"{expected_name}_{sp_name}"
        lines.extend([
            f"    initial begin : _cvdp_bad_width_input_{guard_name}",
            f"        if ($bits({expected_name}) != "
            f"$bits({bridge_name}) ||",
            f"            $bits({bridge_name}) != "
            f"$bits(sparkle_dut.{sp_name})) begin",
            f"            `CVDP_ADAPTER_FATAL(\"{CVDP_ADAPTER_ERROR}: "
            f"direct input width mismatch for {expected_name}\");",
            "        end",
            "    end",
        ])
    if any(matched is not None for _, matched, _ in input_bindings):
        lines.append("")

    generated_arrays = _cvdp_internal_unpacked_arrays(sv_code, sparkle_mod_name)
    used_generated_arrays: set[str] = set()
    for ref_name, (element_type, unpacked_range) in observed_internal_arrays.items():
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
            raise CVDPAdapterContractError(
                f"{CVDP_ADAPTER_ERROR}: harness-observed internal array "
                f"'{ref_name}' {reason}."
            )
        generated_element_type, generated_range = generated_arrays[generated_name]
        same_element_type = (
            _cvdp_normalize_type(element_type)
            == _cvdp_normalize_type(generated_element_type)
        )
        same_range = (
            re.sub(r"\s+", "", unpacked_range)
            == re.sub(r"\s+", "", generated_range)
        )
        if not same_element_type or not same_range:
            raise CVDPAdapterContractError(
                f"{CVDP_ADAPTER_ERROR}: harness-observed internal array "
                f"'{ref_name}' does not have the same proven element type, "
                "depth, and direction as its Sparkle memory mapping."
            )
        used_generated_arrays.add(generated_name)
        loop_low, loop_high = loop_bounds
        bridge_index = f"_cvdp_bridge_{ref_name}_i"
        lines.extend([
            f"    initial begin : _cvdp_bad_memory_{ref_name}",
            f"        if ($bits({ref_name}[{loop_low}]) != $bits(sparkle_dut.{generated_name}[{loop_low}]) ||",
            f"            $size({ref_name}) != $size(sparkle_dut.{generated_name}) ||",
            f"            $left({ref_name}) != $left(sparkle_dut.{generated_name}) ||",
            f"            $right({ref_name}) != $right(sparkle_dut.{generated_name})) begin",
            f"            `CVDP_ADAPTER_FATAL(\"{CVDP_ADAPTER_ERROR}: internal array contract mismatch for {ref_name}\");",
            "        end",
            "    end",
            "",
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
    used_direct_sp_outputs: set[str] = set()
    for expected_port in expected_outputs:
        _, _, rn = expected_port
        alias_binding = whole_output_alias_by_expected.get(rn)
        if alias_binding is not None:
            sp_match, semantic_name, alias_chain = alias_binding
            require_native_parameterized_core_port(sp_match, expected_port)
            sp_name = sp_match[2]
            lines.append(f"    assign {rn} = {sp_name}_wire;")
            semantic_width_checks = [
                f"$bits(sparkle_dut.{lhs}) != $bits(sparkle_dut.{rhs})"
                for lhs, rhs in zip(alias_chain, alias_chain[1:])
            ]
            lines.extend([
                f"    initial begin : _cvdp_bad_width_alias_wrapper_{rn}",
                f"        if ($bits({rn}) != $bits({sp_name}_wire)) begin",
                f"            {tick}CVDP_ADAPTER_FATAL(\"{CVDP_ADAPTER_ERROR}: "
                f"whole-output wrapper width mismatch for {rn}\");",
                "        end",
                "    end",
                f"    initial begin : _cvdp_bad_width_alias_core_{rn}",
                f"        if ($bits({sp_name}_wire) != "
                f"$bits(sparkle_dut.{sp_name})) begin",
                f"            {tick}CVDP_ADAPTER_FATAL(\"{CVDP_ADAPTER_ERROR}: "
                f"whole-output core width mismatch for {rn}\");",
                "        end",
                "    end",
                f"    initial begin : _cvdp_bad_width_alias_semantic_{rn}",
                "        if (" + " ||\n            ".join(
                    semantic_width_checks
                ) + ") begin",
                f"            {tick}CVDP_ADAPTER_FATAL(\"{CVDP_ADAPTER_ERROR}: "
                f"whole-output semantic alias width mismatch for {rn}\");",
                "        end",
                "    end",
            ])
            assigned_outputs.add(rn)
            used_direct_sp_outputs.add(sp_name)
            continue
        direct_candidates = [
            port for port in sp_outputs
            if port[2] not in used_direct_sp_outputs
            and (
                len(expected_outputs) == 1
                or not bundle_fields_by_output[port[2]]
            )
        ]
        sp_match = _cvdp_match_port(
            rn, direct_candidates, direction="output"
        )
        if sp_match:
            require_native_parameterized_core_port(sp_match, expected_port)
            lines.append(f"    assign {rn} = {sp_match[2]}_wire;")
            lines.extend([
                f"    initial begin : _cvdp_bad_width_output_{rn}",
                f"        if ($bits({rn}) != "
                f"$bits({sp_match[2]}_wire) ||",
                f"            $bits({sp_match[2]}_wire) != "
                f"$bits(sparkle_dut.{sp_match[2]})) begin",
                f"            `CVDP_ADAPTER_FATAL(\"{CVDP_ADAPTER_ERROR}: "
                f"direct output width mismatch for {rn}\");",
                "        end",
                "    end",
            ])
            assigned_outputs.add(rn)
            used_direct_sp_outputs.add(sp_match[2])

    if len(sp_outputs) == 1:
        sp_out_d, sp_out_t, sp_out_n = sp_outputs[0]
        remaining = [(d, t, n) for d, t, n in expected_outputs if n not in assigned_outputs]
        if remaining:
            field_assigns = _cvdp_assign_bundled_output_slices(
                sp_out_name=sp_out_n,
                sp_out_type=sp_out_t,
                remaining_outputs=remaining,
                sv_code=sv_code,
                sparkle_ports=sparkle_ports,
                sparkle_mod_name=sparkle_mod_name,
            )
            bundle_fields = _cvdp_infer_concat_fields(
                sv_code, sp_out_n, sparkle_mod_name
            ) or []
            field_assigned = False
            field_widths: list[str | None] = []
            actual_field_widths: list[str | None] = []
            field_shape_by_index: dict[int, str] = {}
            field_hierarchy_by_index: dict[int, str] = {}
            for field_index, bundle_field in enumerate(bundle_fields):
                shape_type = _cvdp_tuple_field_shape_type(
                    bundle_field, bundle_type_lookup
                )
                if shape_type is not None:
                    shape_name = (
                        f"_cvdp_shape_{sp_out_n}_{field_index}"
                    )
                    rendered_shape_type = _cvdp_render_core_type(
                        shape_type, core_type_mirrors
                    )
                    lines.append(
                        f"    {rendered_shape_type} {shape_name};"
                    )
                    hierarchy = _cvdp_hierarchical_field_expr(bundle_field)
                    if hierarchy is None:
                        field_widths.append(None)
                        actual_field_widths.append(None)
                        continue
                    field_shape_by_index[field_index] = shape_name
                    field_hierarchy_by_index[field_index] = hierarchy
                    field_widths.append(f"$bits({shape_name})")
                    actual_field_widths.append(f"$bits({hierarchy})")
                    continue
                literal_width = _cvdp_tuple_field_width_expr(bundle_field)
                if _cvdp_hierarchical_field_expr(bundle_field) is not None:
                    literal_width = None
                field_widths.append(literal_width)
                actual_field_widths.append(literal_width)
            if field_shape_by_index:
                lines.append("")
            # A packed tuple is an interface boundary, not a bag of bits. All
            # observed outputs require unique provenance. Extra source-level
            # fields may remain private only when each is a distinct plain
            # `_gen_<semantic>` child with independently provable type and
            # hierarchy, and none could denote an observed output.
            bundle_provenance = _cvdp_assignment_provenance(
                sv_code, sparkle_mod_name
            )
            if bundle_fields and not _cvdp_unobserved_bundle_fields_are_safe(
                fields=bundle_fields,
                observed_assignments=field_assigns,
                expected_outputs=expected_outputs,
                provenance=bundle_provenance,
                type_lookup=bundle_type_lookup,
            ):
                field_assigns = []
            if (
                any(width is None for width in field_widths)
                or any(width is None for width in actual_field_widths)
            ):
                field_assigns = []
            if field_assigns and (
                len(field_shape_by_index) != len(bundle_fields)
                or len(field_hierarchy_by_index) != len(bundle_fields)
            ):
                field_assigns = []
            if field_assigns:
                total_width = " + ".join(
                    width for width in field_widths if width is not None
                )
                actual_total_width = " + ".join(
                    width for width in actual_field_widths
                    if width is not None
                )
                lines.extend([
                    f"    initial begin : _cvdp_bad_width_bundle_{sp_out_n}",
                    f"        if ($bits({sp_out_n}_wire) != "
                    f"({total_width}) ||",
                    f"            $bits({sp_out_n}_wire) != "
                    f"$bits(sparkle_dut.{sp_out_n}) ||",
                    f"            $bits(sparkle_dut.{sp_out_n}) != "
                    f"({actual_total_width})) begin",
                    f"            `CVDP_ADAPTER_FATAL(\"{CVDP_ADAPTER_ERROR}: "
                    f"packed bundle width mismatch for {sp_out_n}\");",
                    "        end",
                    "    end",
                ])
                for field_index in range(len(bundle_fields)):
                    field_shape = field_shape_by_index[field_index]
                    field_hierarchy = field_hierarchy_by_index[field_index]
                    lines.extend([
                        "    initial begin : "
                        f"_cvdp_bad_width_bundle_child_{sp_out_n}_{field_index}",
                        f"        if ($bits({field_shape}) != "
                        f"$bits({field_hierarchy})) begin",
                        f"            `CVDP_ADAPTER_FATAL(\"{CVDP_ADAPTER_ERROR}: "
                        "packed bundle child width mismatch for "
                        f"{sp_out_n}[{field_index}]\");",
                        "        end",
                        "    end",
                    ])
            for n, index, field in field_assigns:
                preceding_widths = field_widths[:index]
                field_shape = field_shape_by_index.get(index)
                if field_shape is None:
                    continue
                high = f"$bits({sp_out_n}_wire) - 1"
                if preceding_widths:
                    high += " - (" + " + ".join(
                        width for width in preceding_widths if width is not None
                    ) + ")"
                lines.append(
                    f"    assign {n} = {sp_out_n}_wire["
                    f"{high} -: $bits({field_shape})];"
                )
                field_hierarchy = field_hierarchy_by_index[index]
                lines.extend([
                    f"    initial begin : _cvdp_bad_width_{n}",
                    f"        if ($bits({n}) != $bits({field_shape}) ||",
                    f"            $bits({field_shape}) != "
                    f"$bits({field_hierarchy})) begin",
                    f"            `CVDP_ADAPTER_FATAL(\"{CVDP_ADAPTER_ERROR}: "
                    f"packed field width mismatch for {n}\");",
                    "        end",
                    "    end",
                ])
                assigned_outputs.add(n)
                field_assigned = True
            # A scalar/normal single output has no tuple concat to unpack.
            # Preserve direct wiring for this established CVDP case.
            if (
                len(remaining) == 1
                and not field_assigned
                and sp_out_n not in used_direct_sp_outputs
                and not _cvdp_infer_concat_fields(
                    sv_code, sp_out_n, sparkle_mod_name
                )
            ):
                require_native_parameterized_core_port(
                    (sp_out_d, sp_out_t, sp_out_n), remaining[0]
                )
                fallback_name = remaining[0][2]
                lines.extend([
                    f"    initial begin : _cvdp_bad_width_output_{fallback_name}",
                    f"        if ($bits({fallback_name}) != "
                    f"$bits({sp_out_n}_wire) ||",
                    f"            $bits({sp_out_n}_wire) != "
                    f"$bits(sparkle_dut.{sp_out_n})) begin",
                    f"            `CVDP_ADAPTER_FATAL(\"{CVDP_ADAPTER_ERROR}: "
                    f"direct output width mismatch for {fallback_name}\");",
                    "        end",
                    "    end",
                ])
                lines.append(f"    assign {remaining[0][2]} = {sp_out_n}_wire;")
                assigned_outputs.add(remaining[0][2])
                field_assigned = True

    unmapped_outputs = [
        rn for _, _, rn in expected_outputs if rn not in assigned_outputs
    ]
    if unmapped_outputs:
        raise CVDPAdapterContractError(
            f"{CVDP_ADAPTER_ERROR}: harness-observed output(s) "
            f"{', '.join(unmapped_outputs)} could not be mapped safely from "
            "Sparkle output provenance."
        )

    lines.append("endmodule")
    lines.append(tick + "undef CVDP_ADAPTER_FATAL")
    return "\n".join(lines)


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
        parameterized_ppa_runner=None,
        ppa_cache_dir: Path | None = None,
        ppa_workers: int = 2,
        universal_certifier_path: Path | None = None,
        universal_certifier_sha256: str | None = None,
    ):
        self.project_root = project_root.resolve()
        self.dataset_name = dataset.lower()
        self.dataset_obj = dataset_obj  # Optional Dataset instance from dataset.py
        self.dataset_dir = self.project_root / "verilog-eval" / "dataset_spec-to-rtl"
        self.enable_pnr = (
            enable_pnr or enable_drc or enable_lvs or enable_corners
        )  # drc/lvs/corners imply pnr
        self.enable_synth = enable_synth or self.enable_pnr  # pnr implies synth
        self.enable_gls = enable_gls and self.enable_synth  # gls requires synth
        self.enable_drc = enable_drc
        self.enable_lvs = enable_lvs
        self.enable_corners = enable_corners and self.enable_pnr  # corners require pnr
        self.lean_repl = lean_repl  # Optional LeanREPL instance for fast compilation
        self.parameterized_ppa_runner = parameterized_ppa_runner
        self.ppa_cache_dir = (
            Path(ppa_cache_dir).resolve()
            if ppa_cache_dir is not None
            else self.project_root / ".lake" / "build" / "ppa_sweep_cache"
        )
        self.ppa_workers = max(1, int(ppa_workers))
        self.universal_certifier_path = (
            Path(universal_certifier_path).resolve()
            if universal_certifier_path is not None
            else self.project_root / ".lake" / "build" / "bin" / "sparkle-certify"
        )
        self.universal_certifier_sha256 = universal_certifier_sha256
        if (
            self.universal_certifier_sha256 is None
            and self.universal_certifier_path.is_file()
        ):
            try:
                self.universal_certifier_sha256 = hashlib.sha256(
                    self.universal_certifier_path.read_bytes()
                ).hexdigest()
            except OSError:
                self.universal_certifier_sha256 = None

        if self.enable_synth:
            # Add siliconcrew/src to path for synthesis tools
            sc_src = self.project_root / "siliconcrew" / "src"
            if str(sc_src) not in sys.path:
                sys.path.insert(0, str(sc_src))

    def _materialize_certificate_module(
        self, prob_id: str, lean_file: Path, expected_source_sha256: str
    ) -> bool:
        """Build the exact REPL candidate as an importable checked module.

        The standalone certifier never elaborates candidate text.  The REPL
        fast path therefore materializes the already accepted source first,
        and rejects evidence if compilation mutates that source.
        """
        try:
            completed = subprocess.run(
                ["lake", "build", f"Generated.{prob_id}"],
                capture_output=True,
                text=True,
                timeout=BASH_TIMEOUT,
                cwd=str(self.project_root),
            )
            current_source_sha256 = hashlib.sha256(lean_file.read_bytes()).hexdigest()
        except (OSError, subprocess.TimeoutExpired):
            return False
        build_output = completed.stdout + "\n" + completed.stderr
        return bool(
            completed.returncode == 0
            and not re.search(r"error:", build_output)
            and current_source_sha256 == expected_source_sha256
        )

    def _parameterized_ppa_flow_fingerprint(self) -> str:
        """Fingerprint every visible input to the configured ORFS flow."""
        docker_adapter = self.project_root / "siliconcrew" / "src" / "tools" / "run_docker.py"
        adapter_hash = None
        if docker_adapter.exists():
            adapter_hash = hashlib.sha256(docker_adapter.read_bytes()).hexdigest()
        image_digest = os.environ.get("ORFS_DOCKER_IMAGE_DIGEST", "unknown")
        payload = {
            "schema": 1,
            "evaluator_sha256": hashlib.sha256(
                Path(__file__).read_bytes()
            ).hexdigest(),
            "platform": "sky130hd",
            "clock_period_ns": 10,
            "core_utilization": 5,
            "core_aspect_ratio": 1,
            "core_margin_um": 2,
            "place_density": 0.15,
            "pdk_version": SKY130_VOLARE_VERSION,
            "docker_image": os.environ.get("ORFS_DOCKER_IMAGE", "default"),
            "docker_image_digest": image_digest,
            "unversioned_toolchain_nonce": (
                _UNPINNED_ORFS_PROCESS_NONCE
                if not image_digest or image_digest == "unknown"
                else None
            ),
            "runner_sha256": adapter_hash,
            "stages": {
                "synth": bool(self.enable_synth),
                "pnr": bool(self.enable_pnr),
                "drc": bool(self.enable_drc),
                "lvs": bool(self.enable_lvs),
                "corners": bool(self.enable_corners),
            },
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))

    def prepare_parameterized_ppa_runner(self):
        """Create (or return) the shared finite-sweep runner."""
        if self.parameterized_ppa_runner is not None:
            return self.parameterized_ppa_runner
        try:
            from parameterized_ppa import ParameterizedPPARunner
        except ModuleNotFoundError:  # package-style ``agent.evaluator`` import
            from .parameterized_ppa import ParameterizedPPARunner

        self.parameterized_ppa_runner = ParameterizedPPARunner(
            cache_dir=self.ppa_cache_dir,
            synthesize=self._run_parameterized_flow,
            max_workers=self.ppa_workers,
        )
        return self.parameterized_ppa_runner

    def _run_parameterized_flow(self, request, workspace: Path) -> dict:
        """Run one concrete Yosys/OpenROAD point in its isolated workspace."""
        parameters = dict(request.config)
        result = self._run_synthesis(
            request.top_module,
            request.sv_code,
            request.top_module,
            workspace.parent,
            parameters=parameters,
            synth_dir=workspace,
        )
        if (
            result.get("synth_pass")
            and result.get("area_um2") is None
            and result.get("cell_count") is None
            and not result.get("ppa_error")
        ):
            result["ppa_error"] = "synthesis completed without an area or cell-count report"
        if self.enable_pnr and result.get("synth_pass"):
            result.update(self._run_pnr(
                request.top_module,
                request.sv_code,
                request.top_module,
                workspace.parent,
                parameters=parameters,
                synth_dir=workspace,
            ))
        explicit_flow_error = any(
            result.get(name)
            for name in ("synth_error", "ppa_error", "pnr_error")
        )
        result["success"] = bool(
            result.get("synth_pass")
            and (
                not self.enable_pnr
                or (result.get("pnr_pass") and result.get("gds_generated"))
            )
            and (not self.enable_drc or result.get("drc_pass") is True)
            and (not self.enable_lvs or result.get("lvs_pass") is True)
            and (not self.enable_corners or result.get("corners_pass") is True)
            and not explicit_flow_error
        )
        return result

    def run_parameterized_ppa(
        self,
        *,
        sv_code: str,
        top_module: str,
        configurations: tuple[ParameterConfiguration, ...],
    ) -> dict:
        """Run and aggregate a finite concrete parameter sweep.

        This result is explicitly finite evidence.  It never creates or
        upgrades ``universal_lean_theorem`` evidence, even when every point
        passes.
        """
        try:
            from parameterized_ppa import PPARequest
        except ModuleNotFoundError:  # package-style ``agent.evaluator`` import
            from .parameterized_ppa import PPARequest

        runner = self.prepare_parameterized_ppa_runner()
        fingerprint = self._parameterized_ppa_flow_fingerprint()
        requests = [
            PPARequest(
                config=config,
                sv_code=sv_code,
                top_module=top_module,
                flow_fingerprint=fingerprint,
            )
            for config in configurations
        ]
        point_results = runner.run(requests)
        rendered_points: list[dict] = []
        for point in point_results:
            rendered_points.append({
                "config": dict(point.config),
                "success": bool(point.success),
                "metrics": dict(point.metrics),
                "error": point.error,
                "cache_hit": bool(point.cache_hit),
                "cache_key": getattr(point, "cache_key", None),
                "cache_error": getattr(point, "cache_error", None),
                "evidence": {
                    "kind": point.evidence.kind.value,
                    "status": "passed" if point.success else "failed",
                    "scope": "enumerated_configuration_only",
                    "configurations": [dict(config) for config in point.evidence.configurations],
                    "theorem": point.evidence.theorem,
                },
            })

        all_success = bool(rendered_points) and all(point["success"] for point in rendered_points)
        synth_pass = bool(rendered_points) and all(
            point["metrics"].get("synth_pass", False)
            for point in rendered_points
        )
        pnr_pass = bool(rendered_points) and all(
            point["metrics"].get("pnr_pass", False)
            for point in rendered_points
        ) if self.enable_pnr else False

        def metric_values(name: str) -> list[float | int]:
            return [
                point["metrics"][name]
                for point in rendered_points
                if point["metrics"].get(name) is not None
            ]

        area_values = metric_values("area_um2")
        cell_values = metric_values("cell_count")
        wns_values = metric_values("wns_ns")
        power_values = metric_values("power_uw")
        drc_values = metric_values("drc_violations")
        pvt_wns_values = metric_values("pvt_worst_wns_ns")
        pvt_whs_values = metric_values("pvt_worst_whs_ns")
        pvt_power_values = metric_values("pvt_worst_power_uw")
        requested = [dict(config) for config in configurations]
        executed = [point["config"] for point in rendered_points]
        evidence = {
            "kind": "finite_parameter_sweep",
            "status": "passed" if all_success else "failed",
            "scope": "enumerated_configurations_only",
            "requested_configurations": requested,
            "executed_configurations": executed,
            "runs": [
                {
                    "parameters": point["config"],
                    "status": "passed" if point["success"] else "failed",
                    "cache_hit": point["cache_hit"],
                }
                for point in rendered_points
            ],
        }
        errors = [
            f"{point['config']}: {point['error']}"
            for point in rendered_points
            if not point["success"] and point["error"]
        ]
        cache_errors = [
            f"{point['config']}: {point['cache_error']}"
            for point in rendered_points
            if point.get("cache_error")
        ]
        lvs_errors = [
            f"{point['config']}: {point['metrics'].get('lvs_error')}"
            for point in rendered_points
            if point["metrics"].get("lvs_error")
        ]
        pvt_corners = [
            {**corner, "config": point["config"]}
            for point in rendered_points
            for corner in (point["metrics"].get("pvt_corners") or [])
            if isinstance(corner, dict)
        ]
        return {
            "parameterized_ppa_unsupported": False,
            "parameter_sweep_results": rendered_points,
            "parameterized_ppa_results": rendered_points,
            "verification_evidence": [evidence],
            "synth_status": (
                "finite_parameter_sweep_passed"
                if synth_pass else "finite_parameter_sweep_failed"
            ),
            "ppa_status": "finite_parameter_sweep",
            "synth_pass": synth_pass,
            "pnr_pass": pnr_pass,
            "gds_generated": (
                bool(rendered_points)
                and all(point["metrics"].get("gds_generated") is True for point in rendered_points)
            ) if self.enable_pnr else False,
            "drc_pass": (
                bool(rendered_points)
                and all(point["metrics"].get("drc_pass") is True for point in rendered_points)
            ) if self.enable_drc else None,
            "drc_violations": sum(drc_values) if drc_values else None,
            "lvs_pass": (
                bool(rendered_points)
                and all(point["metrics"].get("lvs_pass") is True for point in rendered_points)
            ) if self.enable_lvs else None,
            "lvs_error": "; ".join(lvs_errors) if lvs_errors else None,
            "pvt_corners": pvt_corners,
            "corners_pass": (
                bool(rendered_points)
                and all(point["metrics"].get("corners_pass") is True for point in rendered_points)
            ) if self.enable_corners else None,
            "pvt_worst_wns_ns": min(pvt_wns_values) if pvt_wns_values else None,
            "pvt_worst_whs_ns": min(pvt_whs_values) if pvt_whs_values else None,
            "pvt_worst_power_uw": max(pvt_power_values) if pvt_power_values else None,
            # Conservative scalar summaries retained for existing ranking.
            "area_um2": max(area_values) if area_values else None,
            "cell_count": max(cell_values) if cell_values else None,
            "wns_ns": min(wns_values) if wns_values else None,
            "power_uw": max(power_values) if power_values else None,
            "ppa_error": "; ".join(errors) if errors else None,
            "ppa_cache_error": "; ".join(cache_errors) if cache_errors else None,
            "parameter_sweep_cache_hits": sum(
                1 for point in rendered_points if point["cache_hit"]
            ),
        }

    def evaluate(
        self,
        prob_id: str,
        run_dir: Path,
        *,
        benchmark_ports: list[tuple[str, str, str]] | None = None,
    ) -> dict:
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
            # Compatibility source-quality field only.  Absence of ``sorry``
            # is not evidence that a universal theorem was declared.
            "has_sorry": True,
            "lean_source_status": "not_compiled",
            "verification_evidence": [],
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
        lean_source = lean_file.read_text()
        certificate_output = ""

        if self.lean_repl is not None:
            # ── Fast path: use persistent REPL (~0.1s) ──
            repl_result = self.lean_repl.check_file(lean_file)
            if not repl_result.passed:
                result["detail"] = f"Compile failed:\n{repl_result.error_text[:1000]}"
                return result
            result["compile_pass"] = True
            result["has_sorry"] = not repl_result.complete
            result["lean_source_status"] = (
                "complete" if repl_result.complete else "contains_sorry"
            )
            certificate_output = "\n".join(
                "info: " + str(info.get("data", ""))
                for info in getattr(repl_result, "infos", [])
                if isinstance(info, dict)
            )
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
            result["lean_source_status"] = (
                "contains_sorry" if result["has_sorry"] else "complete"
            )
            certificate_output = build_output
            sv_code = self._extract_sv(build_output)

        certificate_requests = _extract_universal_theorem_evidence(
            certificate_output,
            lean_source,
            str(lean_file.relative_to(self.project_root)),
        )
        certificate_module_ready = not certificate_requests
        if certificate_requests:
            certificate_module_ready = self.lean_repl is None or (
                self._materialize_certificate_module(
                    prob_id,
                    lean_file,
                    hashlib.sha256(lean_file.read_bytes()).hexdigest(),
                )
            )
        if certificate_requests and certificate_module_ready:
            result["verification_evidence"] = (
                independently_revalidate_universal_theorems(
                    certificate_output,
                    lean_source,
                    str(lean_file.relative_to(self.project_root)),
                    project_root=self.project_root,
                    module_name=f"Generated.{prob_id}",
                    certifier_path=self.universal_certifier_path,
                    expected_certifier_sha256=self.universal_certifier_sha256,
                    timeout=BASH_TIMEOUT,
                )
            )

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
        if benchmark_ports is None:
            sim_status, mismatches, detail = self._run_sim(
                prob_id, sv_code, sparkle_mod_name, sparkle_ports, run_dir
            )
        else:
            sim_status, mismatches, detail = self._run_sim(
                prob_id,
                sv_code,
                sparkle_mod_name,
                sparkle_ports,
                run_dir,
                benchmark_ports=benchmark_ports,
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
            harness_files = info.metadata.get("harness_files", {})
            parameter_analysis = _cvdp_parameter_override_analysis(
                harness_files
            )
            benchmark_parameter_names = (
                _cvdp_required_benchmark_parameter_names(
                    ref_code=info.ref_code or "",
                    design_name=info.design_name,
                    harness_files=harness_files,
                )
            )
            core_parameters = _module_parameters(sv_code, sparkle_mod_name)
            parameter_names = (
                benchmark_parameter_names
                | {parameter.name for parameter in core_parameters}
            )
            reference_has_parameters = bool(
                benchmark_parameter_names
                - set(parameter_analysis.parameter_names)
            )
            core_has_parameters = bool(core_parameters)
            if (
                parameter_analysis.may_have_overrides
                or reference_has_parameters
                or core_has_parameters
            ):
                plan = _cvdp_exact_parameter_sweep_plan(
                    harness_files, parameter_names
                )
                result["parameter_sweep_plan"] = {
                    "configurations": [
                        dict(config) for config in plan.configurations
                    ],
                    "exact": not plan.unresolved,
                    "unresolved_reasons": list(plan.unresolved_reasons),
                }
                if plan.unresolved:
                    result.update({
                        "parameterized_ppa_unsupported": True,
                        "synth_status": "not_run_parameterized_sweep",
                        "ppa_status": "unsupported_parameter_sweep",
                        "ppa_error": (
                            "PPA was not run because the complete CVDP parameter "
                            "configuration matrix could not be recovered exactly: "
                            + "; ".join(plan.unresolved_reasons)
                            + ". A module-default configuration alone is not representative."
                        ),
                    })
                    return result
                if self.enable_synth and result["sim_status"] == "sim_pass":
                    sweep_result = self.run_parameterized_ppa(
                        sv_code=sv_code,
                        top_module=sparkle_mod_name or info.design_name,
                        configurations=plan.configurations,
                    )
                    sweep_evidence = sweep_result.pop("verification_evidence", [])
                    result.update(sweep_result)
                    result["verification_evidence"] = (
                        list(result.get("verification_evidence", []))
                        + list(sweep_evidence)
                    )
                    # Gate-level simulation is intentionally not summarized
                    # as one scalar across heterogeneous parameter configs.
                    result["gls_synth_status"] = "not_run_parameter_sweep"
                    result["gls_pnr_status"] = "not_run_parameter_sweep"
                    return result
                if self.enable_synth:
                    result.update({
                        "synth_status": "not_run_simulation_failed",
                        "ppa_status": "not_run_simulation_failed",
                        "ppa_error": (
                            "Finite parameter PPA was not run because the "
                            "functional parameter sweep did not pass."
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
        *,
        benchmark_ports: list[tuple[str, str, str]] | None = None,
    ) -> tuple[str, int, str]:
        """Run simulation — dispatches to dataset-specific mode."""
        if self.dataset_name == "rtllm":
            return self._run_sim_rtllm(prob_id, sv_code, sparkle_mod_name, sparkle_ports, run_dir)
        if self.dataset_name == "cvdp":
            return self._run_sim_cvdp(
                prob_id,
                sv_code,
                sparkle_mod_name,
                sparkle_ports,
                run_dir,
                benchmark_ports=benchmark_ports,
            )
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
        benchmark_ports: list[tuple[str, str, str]] | None = None,
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
            required_parameters = _cvdp_required_benchmark_parameter_names(
                ref_code=info.ref_code or "",
                design_name=design_name,
                harness_files=harness_files,
            )
            if parameter_analysis.may_have_overrides or required_parameters:
                declared_parameters = _module_parameter_names(sv_code, design_name)
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
                        benchmark_ports=benchmark_ports,
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
            incomplete = _cvdp_incomplete_pytest_summary(output)
            if incomplete:
                return (
                    "sim_error",
                    -1,
                    f"CVDP harness is incomplete ({incomplete}); finite verification is not claimed",
                )
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
        sim_log = (rundir / "sim.log").read_text(errors="replace") if (rundir / "sim.log").exists() else ""
        (sim_dir / "cvdp_local_output.txt").write_text(output)
        tail = "\n".join(output.splitlines()[-40:])
        if proc.returncode == 0:
            incomplete = _cvdp_incomplete_pytest_summary(output)
            if incomplete:
                return (
                    "sim_error",
                    -1,
                    f"CVDP local harness is incomplete ({incomplete}); finite verification is not claimed",
                )
            return "sim_pass", 0, "CVDP local harness passed"
        combined_output = "\n".join(part for part in (sim_log, output) if part)
        adapter_marker = next(
            (
                marker for marker in (
                    CVDP_ADAPTER_ERROR,
                    CVDP_PARAMETERIZATION_UNSUPPORTED,
                )
                if marker in combined_output
            ),
            None,
        )
        if adapter_marker:
            source = sim_log if adapter_marker in sim_log else tail
            return "sim_error", -1, f"CVDP local adapter contract error:\n{source[-2000:]}"

        iverilog_error_re = re.compile(
            r"(?:^.*:\d+(?::\d+)?:\s*(?:syntax\s+)?error:|"
            r"Unable to bind|Dimensions must be constant|during elaboration|"
            r"Unknown module type|Unable to find the root module)",
            re.IGNORECASE | re.MULTILINE,
        )
        called_iverilog = re.search(
            r"subprocess\.CalledProcessError:.*Command[^\n]*\biverilog\b",
            output,
            re.IGNORECASE,
        )
        iverilog_lines = [
            line for line in sim_log.splitlines() if iverilog_error_re.search(line)
        ]
        if called_iverilog or iverilog_lines:
            diagnostic = "\n".join(iverilog_lines) or tail
            return (
                "sim_error",
                -1,
                f"CVDP local iverilog compile failed during build/elaboration:\n{diagnostic[-2000:]}",
            )

        actual_timeout = re.search(
            r"(?:^\s*E\s+.*(?:TimeoutExpired|TimeoutError|SimTimeoutError):|"
            r"\btimed out after\s+\d+(?:\.\d+)?\s*(?:s|seconds?)\b|"
            r"\b(?:simulation|test)\s+(?:hit\s+)?(?:internal\s+)?timeout\b)",
            output,
            re.IGNORECASE | re.MULTILINE,
        )
        if actual_timeout:
            return "sim_error", -1, f"CVDP local simulation timeout:\n{tail[-1200:]}"

        functional_failure = re.search(
            r"(?:AssertionError:|^\s*E\s+assert\b|\*\*[^\n]*\bFAIL\b|"
            r"ERROR[^\n]*Failed\s+\d+\s+of\s+\d+\s+tests|"
            r"\btest_[A-Za-z0-9_.]+\s+failed\b)",
            output,
            re.IGNORECASE | re.MULTILINE,
        )
        if functional_failure:
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

    @staticmethod
    def _selected_top_clock_port(sv_code: str, top_module: str) -> str | None:
        """Return the selected top's supported clock port, if it has one."""
        parsed_top, top_ports = parse_module_ports(
            sv_code, module_name=top_module
        )
        if parsed_top != top_module:
            return None
        return next(
            (
                name
                for direction, _typ, name in top_ports
                if direction == "input"
                and re.search(
                    r"(^|_)(clk|clock|aclk|pclk)($|_)", name.lower()
                )
            ),
            None,
        )

    def _run_synthesis(
        self,
        prob_id: str,
        sv_code: str,
        top_module: str,
        run_dir: Path,
        *,
        parameters: dict[str, int] | None = None,
        synth_dir: Path | None = None,
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
        synth_dir = synth_dir or (run_dir / "synth" / prob_id)
        synth_dir.mkdir(parents=True, exist_ok=True)

        # Write SV to workspace
        sv_file = synth_dir / f"{prob_id}.sv"
        sv_file.write_text(sv_code)

        # Inspect only the selected top.  A clock on a helper module must not
        # cause an invalid SDC constraint on a combinational design root.
        clock_port = self._selected_top_clock_port(sv_code, top_module)
        sdc_file = synth_dir / "constraints.sdc"
        if clock_port is None:
            sdc_file.write_text("# Combinational design — no clock constraint\n")
        else:
            sdc_file.write_text(
                f"create_clock -period 10 [get_ports {clock_port}]\n"
            )

        # Generate config.mk
        container_sv = f"/workspace/{prob_id}.sv"
        try:
            parameter_line = _render_orfs_top_parameters(parameters)
        except ValueError as exc:
            result["synth_error"] = str(exc)
            return result
        config_content = (
            f"export DESIGN_NAME = {top_module}\n"
            f"export PLATFORM = sky130hd\n"
            f"export VERILOG_FILES = {container_sv}\n"
            f"export SDC_FILE = /workspace/constraints.sdc\n"
            f"{parameter_line}"
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
        self,
        prob_id: str,
        sv_code: str,
        top_module: str,
        run_dir: Path,
        *,
        parameters: dict[str, int] | None = None,
        synth_dir: Path | None = None,
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

        synth_dir = synth_dir or (run_dir / "synth" / prob_id)
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
        clock_port = self._selected_top_clock_port(sv_code, top_module)
        container_sv = f"/workspace/{prob_id}.sv"
        try:
            parameter_line = _render_orfs_top_parameters(parameters)
        except ValueError as exc:
            result["pnr_error"] = str(exc)
            return result
        config_content = (
            f"export DESIGN_NAME = {top_module}\n"
            f"export PLATFORM = sky130hd\n"
            f"export VERILOG_FILES = {container_sv}\n"
            f"export SDC_FILE = /workspace/constraints.sdc\n"
            f"{parameter_line}"
            f"export DIE_AREA = 0 0 {die_side} {die_side}\n"
            f"export CORE_AREA = {margin} {margin} {core_side} {core_side}\n"
            f"export PLACE_DENSITY = 0.15\n"
        )
        (synth_dir / "config.mk").write_text(config_content)

        # Ensure SDC exists (should already from synth phase)
        sdc_file = synth_dir / "constraints.sdc"
        if not sdc_file.exists():
            if clock_port is None:
                sdc_file.write_text("# Combinational design\n")
            else:
                sdc_file.write_text(
                    f"create_clock -period 10 [get_ports {clock_port}]\n"
                )

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

        has_clk = self._selected_top_clock_port(sv_code, top_module) is not None
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

            if not sta_result.get("success"):
                corners_data.append({
                    "corner": cname,
                    "label": corner["label"],
                    "error": str(sta_result.get("stderr") or "STA failed"),
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
            "corners_pass": (
                len(corners_data) == len(PVT_CORNERS)
                and all(not corner.get("error") for corner in corners_data)
            ),
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
