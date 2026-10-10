#!/usr/bin/env python3
"""Lean/SV co-simulation diagnostics for generated Sparkle problems.

This script is intentionally independent from the main evaluator.  It answers a
different question: when a generated RTL artifact fails a benchmark harness, did
the Lean model itself behave differently from the emitted Verilog?

For each problem it:
  1. Imports Generated.<prob_id> and evaluates the synthesized declaration on a
     deterministic input trace in Lean.
  2. Builds Generated.<prob_id>, extracts the Sparkle-emitted SystemVerilog, and
     runs a tiny Icarus testbench over the same trace.
  3. Compares Lean and Verilog outputs cycle by cycle.  The comparison also
     tries a one-cycle shift because Sparkle registers have a Lean initial-state
     semantics while the backend emits an explicit resettable flop.
  4. Optionally attaches benchmark-harness status from an existing results.jsonl.

It is a diagnostic, not a replacement for CVDP/VerilogEval simulation.
"""
from __future__ import annotations

import argparse
import ast
import concurrent.futures as futures
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
AGENT_DIR = PROJECT_ROOT / "agent"
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

try:
    from evaluator import Evaluator, parse_module_ports, _port_width
except Exception as exc:  # pragma: no cover - surfaced in CLI diagnostics
    raise SystemExit(f"Failed to import evaluator helpers: {exc}") from exc


SIGNAL_RE = re.compile(r"^Signal\s+dom\s+(.+)$", re.DOTALL)
SYNTH_RE = re.compile(r"#synthesizeVerilog\s+([A-Za-z_][\w']*)")


@dataclass
class Arg:
    name: str
    lean_name: str
    typ: str
    is_signal: bool


@dataclass
class EntryInfo:
    name: str
    args: list[Arg]
    output_type: str
    field_widths: list[int]


def _run(
    cmd: list[str],
    *,
    cwd: Path,
    timeout: int,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    run_env = os.environ.copy()
    run_env["PATH"] = (
        f"{Path.home() / '.elan' / 'bin'}:{Path.home() / '.local' / 'bin'}:"
        + run_env.get("PATH", "")
    )
    if env:
        run_env.update(env)
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        env=run_env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _strip_lean_comments(text: str) -> str:
    text = re.sub(r"/-.*?-/", "", text, flags=re.DOTALL)
    return re.sub(r"--.*", "", text)


def _sanitize_lean_name(name: str) -> str:
    name = name.strip()
    if name.startswith("«") and name.endswith("»"):
        return name[1:-1]
    return name


def _lean_ident(name: str) -> str:
    if re.match(r"^[A-Za-z_][A-Za-z0-9_']*$", name):
        return name
    return f"«{name}»"


def _split_top_level_blocks(sig: str) -> list[str]:
    blocks: list[str] = []
    i = 0
    while i < len(sig):
        if sig[i] != "(":
            i += 1
            continue
        start = i
        depth = 0
        while i < len(sig):
            ch = sig[i]
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    blocks.append(sig[start + 1 : i])
                    break
            i += 1
        i += 1
    return blocks


def _split_names(raw: str) -> list[str]:
    names = []
    for m in re.finditer(r"«([^»]+)»|[A-Za-z_][A-Za-z0-9_']*", raw):
        name = m.group(1) if m.group(1) is not None else m.group(0)
        if name not in {"fun", "let", "dom"}:
            names.append(name)
    return names


def _split_type_list(type_text: str) -> list[str]:
    """Return primitive output types in left-to-right tuple order."""
    clean = type_text.replace("\n", " ")
    return re.findall(r"\bBool\b|\bBitVec\s+(?:\([^)]+\)|[A-Za-z0-9_]+)", clean)


def _simple_int_expr(expr: str, params: dict[str, int]) -> int | None:
    expr = expr.strip()
    if expr.startswith("(") and expr.endswith(")"):
        expr = expr[1:-1].strip()
    if expr in params:
        return params[expr]
    if re.fullmatch(r"\d+", expr):
        return int(expr)
    replaced = expr
    for name, value in sorted(params.items(), key=lambda x: -len(x[0])):
        replaced = re.sub(rf"\b{re.escape(name)}\b", str(value), replaced)
    if re.fullmatch(r"[0-9+\-*/% ()]+", replaced):
        try:
            return int(eval(replaced, {"__builtins__": {}}, {}))
        except Exception:
            return None
    return None


def _type_width(typ: str, params: dict[str, int]) -> int | None:
    typ = typ.strip()
    while typ.startswith("(") and typ.endswith(")"):
        typ = typ[1:-1].strip()
    if typ == "Bool":
        return 1
    m = re.match(r"BitVec\s+(.+)$", typ)
    if not m:
        return None
    return _simple_int_expr(m.group(1), params)


def _default_param_value(name: str, typ: str, sv_params: dict[str, int]) -> int:
    if name in sv_params:
        return sv_params[name]
    upper = name.upper()
    if "WIDTH" in upper:
        return 8
    if any(token in upper for token in ("DEPTH", "SIZE", "COUNT", "NUM", "N")):
        return 4
    if "THRESHOLD" in upper:
        return 8
    return 0


def _parse_sv_params(sv_code: str) -> dict[str, int]:
    params: dict[str, int] = {}
    for match in re.finditer(
        r"\bparameter\b\s+(?:integer|int|logic|bit)?\s*(?:\[[^\]]+\]\s*)?"
        r"([A-Za-z_]\w*)\s*=\s*([^,\n;)]+)",
        sv_code,
    ):
        val = _simple_int_expr(match.group(2), params)
        if val is not None:
            params[match.group(1)] = val
    return params


def parse_entry(lean_file: Path, sv_params: dict[str, int] | None = None) -> EntryInfo:
    sv_params = sv_params or {}
    text = lean_file.read_text(errors="replace")
    synth = SYNTH_RE.search(text)
    if not synth:
        raise ValueError("No #synthesizeVerilog declaration found")
    name = synth.group(1)

    decl = re.search(rf"\bdef\s+{re.escape(name)}\b(?P<body>.*?)\s*:=", text, re.DOTALL)
    if not decl:
        raise ValueError(f"Could not find def signature for {name}")
    sig = _strip_lean_comments(decl.group("body"))

    out_idx = sig.rfind(": Signal dom")
    if out_idx < 0:
        raise ValueError("Could not parse Signal output type")
    output_type = sig[out_idx + len(": Signal dom") :].strip()

    args: list[Arg] = []
    params_for_widths = dict(sv_params)
    for block in _split_top_level_blocks(sig):
        if ":" not in block:
            continue
        lhs, rhs = block.split(":", 1)
        rhs = " ".join(rhs.split())
        if "DomainConfig" in rhs:
            continue
        signal_match = SIGNAL_RE.match(rhs)
        names = _split_names(lhs)
        if not names:
            continue
        if signal_match:
            val_type = signal_match.group(1).strip()
            for name_i in names:
                args.append(Arg(name_i, _lean_ident(name_i), val_type, True))
        else:
            for name_i in names:
                args.append(Arg(name_i, _lean_ident(name_i), rhs, False))
                if rhs in {"Nat", "Int"}:
                    params_for_widths[name_i] = _default_param_value(name_i, rhs, sv_params)

    field_widths: list[int] = []
    for prim in _split_type_list(output_type):
        width = _type_width(prim, params_for_widths)
        if width is None:
            raise ValueError(f"Unsupported output primitive type: {prim}")
        field_widths.append(width)
    if not field_widths:
        raise ValueError(f"Unsupported output type: {output_type}")

    return EntryInfo(name=name, args=args, output_type=output_type, field_widths=field_widths)


def _stable_seed(*parts: str) -> int:
    h = hashlib.sha256("::".join(parts).encode()).digest()
    return int.from_bytes(h[:8], "big")


def _is_clock(name: str) -> bool:
    lower = name.lower()
    return lower in {"clk", "clock", "clk_in", "clock_in", "aclk"} or lower.endswith("_clk")


def _is_reset(name: str) -> bool:
    lower = name.lower()
    return any(tok in lower for tok in ("reset", "rst", "arst", "areset"))


def _active_low_reset(name: str) -> bool:
    lower = name.lower()
    return lower.endswith(("_n", "_ni", "_b", "_bar")) or "rst_n" in lower or "reset_n" in lower


def _trace_value(prob_id: str, arg: Arg, width: int, cycle: int, index: int) -> int:
    name = arg.name
    if _is_clock(name):
        return cycle & 1
    if _is_reset(name):
        asserted = cycle < 2
        return 0 if asserted and _active_low_reset(name) else int(asserted)
    seed = _stable_seed(prob_id, name)
    if width == 1:
        return ((cycle + index + seed) >> 1) & 1
    # Keep values small enough for fast Lean parsing while still changing bits.
    mask = (1 << min(width, 20)) - 1
    val = (seed + (cycle + 1) * (index + 3) * 17 + cycle * cycle * 5) & mask
    if width < 20:
        val &= (1 << width) - 1
    return val


def make_traces(prob_id: str, signals: list[Arg], params: dict[str, int], cycles: int) -> dict[str, list[int]]:
    traces: dict[str, list[int]] = {}
    for idx, arg in enumerate(signals):
        width = _type_width(arg.typ, params)
        if width is None:
            raise ValueError(f"Unsupported input type for {arg.name}: {arg.typ}")
        traces[arg.name] = [_trace_value(prob_id, arg, width, t, idx) for t in range(cycles)]
    return traces


def _lean_literal(typ: str, width: int, value: int) -> str:
    typ = typ.strip()
    while typ.startswith("(") and typ.endswith(")"):
        typ = typ[1:-1].strip()
    if typ == "Bool":
        return "true" if value else "false"
    return f"(BitVec.ofNat {width} {value})"


def _lean_signal_def(arg: Arg, trace: list[int], params: dict[str, int]) -> str:
    width = _type_width(arg.typ, params)
    if width is None:
        raise ValueError(f"Unsupported input type for {arg.name}: {arg.typ}")
    lines = [
        f"def trace_{arg.lean_name} : Signal defaultDomain ({arg.typ}) := Signal.fromStream fun t =>",
        "  match t with",
    ]
    for i, value in enumerate(trace):
        lines.append(f"  | {i} => {_lean_literal(arg.typ, width, value)}")
    lines.append(f"  | _ => {_lean_literal(arg.typ, width, trace[-1] if trace else 0)}")
    return "\n".join(lines)


def _param_lean_expr(arg: Arg, value: int) -> str:
    typ = arg.typ.strip()
    while typ.startswith("(") and typ.endswith(")"):
        typ = typ[1:-1].strip()
    if typ == "Nat":
        return str(max(value, 0))
    if typ == "Int":
        return str(value)
    width = _type_width(typ, {})
    if width is not None:
        return f"(BitVec.ofNat {width} {value})"
    raise ValueError(f"Unsupported parameter type for {arg.name}: {arg.typ}")


def _generated_source_for_trace(prob_id: str) -> str:
    """Copy Generated.<prob_id> into the trace file with a diagnostic loop.

    Importing Generated.<prob_id> directly would bind generated definitions to
    Sparkle's production `Signal.loop`, whose C FFI barrier is not available in
    `lean` interpreter mode.  For direct simulation only, we replace
    `Signal.loop` with a small Lean-level memoized fixed point.  The emitted
    Verilog path still uses the original source and original backend.
    """
    source = (PROJECT_ROOT / "Generated" / f"{prob_id}.lean").read_text(errors="replace")
    kept: list[str] = []
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith("import "):
            continue
        if stripped.startswith("#synthesizeVerilog"):
            continue
        kept.append(line)
    copied = "\n".join(kept)
    copied = copied.replace("Signal.loop", "diagLoop")
    return copied


DIAG_LOOP_LEAN = r"""
private unsafe def diagLoopImpl {dom : DomainConfig} {α : Type} [Inhabited α]
    (f : Signal dom α → Signal dom α) : Signal dom α :=
  match unsafeIO (do
    let cacheRef ← IO.mkRef (#[] : Array α)
    let result : Signal dom α := ⟨fun t =>
      match unsafeIO (do
        let arr ← cacheRef.get
        return if h : t < arr.size then arr[t] else default) with
      | .ok v => v
      | .error _ => default⟩
    let inner := f result
    let evalAt (t : Nat) : IO α := do
      let mut arr ← cacheRef.get
      if h : t < arr.size then
        return arr[t]
      else
        for i in [arr.size:t + 1] do
          let v := inner.val i
          arr := arr.push v
          cacheRef.set arr
        return if h2 : t < arr.size then arr[t] else default
    return (⟨fun t =>
      match unsafeIO (evalAt t) with
      | .ok v => v
      | .error _ => default⟩ : Signal dom α)) with
  | .ok sig => sig
  | .error _ => default

@[implemented_by diagLoopImpl]
opaque diagLoop {dom : DomainConfig} {α : Type} [Inhabited α]
    (f : Signal dom α → Signal dom α) : Signal dom α
"""


def write_lean_trace_file(
    path: Path,
    prob_id: str,
    entry: EntryInfo,
    traces: dict[str, list[int]],
    params: dict[str, int],
    cycles: int,
) -> None:
    signal_args = [a for a in entry.args if a.is_signal]
    const_args = [a for a in entry.args if not a.is_signal]
    arg_exprs: list[str] = []
    parts = [
        "import cktlean",
        "import cktlean.Compiler.Elab",
        "",
        "open cktlean.Core.Signal",
        "open cktlean.Core.Domain",
        "open cktlean.Library.RTL",
        "",
        "namespace LeanCosimDiagnostic",
        "",
        DIAG_LOOP_LEAN.strip(),
        "",
        _generated_source_for_trace(prob_id),
        "",
        "class ToFields (α : Type) where",
        "  fields : α -> List Nat",
        "",
        "instance {n : Nat} : ToFields (BitVec n) where",
        "  fields x := [x.toNat]",
        "",
        "instance : ToFields Bool where",
        "  fields x := [if x then 1 else 0]",
        "",
        "instance [ToFields α] [ToFields β] : ToFields (α × β) where",
        "  fields x := ToFields.fields x.1 ++ ToFields.fields x.2",
        "",
    ]

    for arg in const_args:
        value = params[arg.name]
        expr = _param_lean_expr(arg, value)
        parts.append(f"def param_{arg.lean_name} : {arg.typ} := {expr}")
        arg_exprs.append(f"param_{arg.lean_name}")
    if const_args:
        parts.append("")

    for arg in signal_args:
        parts.append(_lean_signal_def(arg, traces[arg.name], params))
        parts.append("")
        arg_exprs.append(f"trace_{arg.lean_name}")

    if arg_exprs:
        call = f"{entry.name} " + " ".join(arg_exprs)
    else:
        call = f"{entry.name} (dom := defaultDomain)"
    parts.extend(
        [
            f"def dutOut := {call}",
            "",
            f"#eval (List.range {cycles}).map fun t => ToFields.fields (dutOut.atTime t)",
            "",
            "end LeanCosimDiagnostic",
            "",
        ]
    )
    path.write_text("\n".join(parts))


def run_lean_trace(
    prob_id: str,
    entry: EntryInfo,
    traces: dict[str, list[int]],
    params: dict[str, int],
    cycles: int,
    work_dir: Path,
    timeout: int,
) -> tuple[str, list[list[int]] | None, str]:
    trace_file = work_dir / f"{prob_id}_trace.lean"
    write_lean_trace_file(trace_file, prob_id, entry, traces, params, cycles)
    try:
        proc = _run(["lake", "env", "lean", str(trace_file)], cwd=PROJECT_ROOT, timeout=timeout)
    except subprocess.TimeoutExpired:
        return "timeout", None, f"Lean trace timeout after {timeout}s"
    output = proc.stdout + proc.stderr
    if proc.returncode != 0:
        return "error", None, output[-3000:]
    rows = None
    collecting = False
    buf_parts: list[str] = []
    balance = 0
    for line in output.splitlines():
        stripped = line.strip()
        if not collecting and stripped.startswith("["):
            collecting = True
            buf_parts = []
            balance = 0
        if collecting:
            buf_parts.append(stripped)
            balance += stripped.count("[") - stripped.count("]")
            if balance > 0:
                continue
            candidate = " ".join(buf_parts)
            collecting = False
            try:
                value = ast.literal_eval(candidate)
                if isinstance(value, list):
                    rows = value
            except Exception:
                pass
    if rows is None:
        return "error", None, f"Could not parse Lean #eval output:\n{output[-1000:]}"
    return "ok", rows, ""


def _sv_decl(typ: str, name: str, kind: str) -> str:
    width = _port_width(typ)
    if width == 1:
        return f"{kind} {name};"
    return f"{kind} [{width - 1}:0] {name};"


def _sv_const(width: int, value: int) -> str:
    return f"{width}'d{value}"


def _sv_port_map_name(lean_name: str) -> set[str]:
    base = lean_name.lower()
    return {base, f"_gen_{base}"}


def _build_sv_input_map(signal_args: list[Arg], ports: list[tuple[str, str, str]]) -> dict[str, Arg]:
    by_name: dict[str, Arg] = {}
    for arg in signal_args:
        for key in _sv_port_map_name(arg.name):
            by_name[key] = arg
    result: dict[str, Arg] = {}
    for direction, _, port_name in ports:
        if direction != "input":
            continue
        key = port_name.lower()
        if key in by_name:
            result[port_name] = by_name[key]
            continue
        if port_name.startswith("_gen_") and port_name[5:].lower() in by_name:
            result[port_name] = by_name[port_name[5:].lower()]
    return result


def _sv_output_exprs(
    output_ports: list[tuple[str, str, str]],
    field_widths: list[int],
) -> tuple[list[str], str | None]:
    if not output_ports:
        return [], "No output ports in emitted Verilog"
    if len(output_ports) == len(field_widths):
        return [name for _, _, name in output_ports], None
    if len(output_ports) == 1:
        _, typ, name = output_ports[0]
        port_w = _port_width(typ)
        if sum(field_widths) != port_w:
            return [], f"Output field widths {field_widths} do not match SV output width {port_w}"
        exprs = []
        offset = port_w
        for width in field_widths:
            high = offset - 1
            low = offset - width
            if width == 1:
                exprs.append(f"{name}[{low}]")
            else:
                exprs.append(f"{name}[{high}:{low}]")
            offset -= width
        return exprs, None
    return [], f"Cannot map {len(output_ports)} SV outputs to {len(field_widths)} Lean fields"


def write_sv_testbench(
    tb_path: Path,
    sv_path: Path,
    module_name: str,
    ports: list[tuple[str, str, str]],
    signal_args: list[Arg],
    traces: dict[str, list[int]],
    field_widths: list[int],
    cycles: int,
) -> str | None:
    input_map = _build_sv_input_map(signal_args, ports)
    output_ports = [p for p in ports if p[0] == "output"]
    output_exprs, error = _sv_output_exprs(output_ports, field_widths)
    if error:
        return error

    input_ports = [p for p in ports if p[0] == "input"]
    has_clk = any(name == "clk" for _, _, name in input_ports)
    has_rst = any(name == "rst" for _, _, name in input_ports)

    lines = [
        "`timescale 1ns/1ps",
        f'`include "{sv_path.name}"',
        "",
        "module tb;",
    ]
    for direction, typ, name in ports:
        kind = "reg" if direction == "input" else "wire"
        lines.append(f"  {_sv_decl(typ, name, kind)}")
    lines.append("")
    lines.append(f"  {module_name} dut (")
    lines.append(",\n".join(f"    .{name}({name})" for _, _, name in ports))
    lines.append("  );")
    lines.append("")
    lines.append("  initial begin")
    for _, typ, name in input_ports:
        width = _port_width(typ)
        lines.append(f"    {name} = {_sv_const(width, 0)};")
    if has_rst:
        lines.append("    rst = 1'b1;")
    if has_clk:
        lines.append("    clk = 1'b0;")
    lines.append("    #2;")
    if has_rst:
        lines.append("    rst = 1'b0;")
    lines.append("")

    fmt_fields = " ".join(f"F{i}=%0d" for i in range(len(output_exprs)))
    for cycle in range(cycles):
        for _, typ, name in input_ports:
            if name == "clk":
                continue
            if name == "rst" and name not in input_map:
                continue
            arg = input_map.get(name)
            width = _port_width(typ)
            value = traces.get(arg.name, [0] * cycles)[cycle] if arg else 0
            lines.append(f"    {name} = {_sv_const(width, value)};")
        if has_clk:
            lines.append("    #1; clk = 1'b1; #1; clk = 1'b0; #1;")
        else:
            lines.append("    #1;")
        args = ", ".join(output_exprs)
        lines.append(f'    $display("CYCLE {cycle} {fmt_fields}", {args});')
    lines.append("    $finish;")
    lines.append("  end")
    lines.append("endmodule")
    tb_path.write_text("\n".join(lines) + "\n")
    return None


def run_sv_trace(
    prob_id: str,
    sv_code: str,
    module_name: str,
    ports: list[tuple[str, str, str]],
    signal_args: list[Arg],
    traces: dict[str, list[int]],
    field_widths: list[int],
    cycles: int,
    work_dir: Path,
    timeout: int,
) -> tuple[str, list[list[int]] | None, str]:
    sv_path = work_dir / f"{prob_id}.sv"
    tb_path = work_dir / f"{prob_id}_tb.sv"
    simv_path = work_dir / f"{prob_id}.vvp"
    sv_path.write_text(sv_code)
    error = write_sv_testbench(
        tb_path, sv_path, module_name, ports, signal_args, traces, field_widths, cycles
    )
    if error:
        return "unsupported", None, error
    try:
        comp = _run(
            ["iverilog", "-g2012", "-o", str(simv_path), str(tb_path)],
            cwd=work_dir,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        return "compile_error", None, str(exc)
    if comp.returncode != 0:
        return "compile_error", None, (comp.stdout + comp.stderr)[-3000:]
    try:
        proc = _run(["vvp", str(simv_path)], cwd=work_dir, timeout=timeout)
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        return "sim_error", None, str(exc)
    output = proc.stdout + proc.stderr
    if proc.returncode != 0:
        return "sim_error", None, output[-3000:]
    rows: list[list[int]] = []
    for line in output.splitlines():
        m = re.match(r"CYCLE\s+\d+\s+(.*)$", line.strip())
        if not m:
            continue
        vals = [int(x) for x in re.findall(r"F\d+=([0-9]+)", m.group(1))]
        rows.append(vals)
    if len(rows) != cycles:
        return "sim_error", None, f"Expected {cycles} SV rows, got {len(rows)}:\n{output[-1000:]}"
    return "ok", rows, ""


def compare_rows(
    lean_rows: list[list[int]],
    sv_rows: list[list[int]],
) -> dict[str, Any]:
    if not lean_rows or not sv_rows:
        return {"status": "mismatch", "alignment": None, "matched_cycles": 0, "total_cycles": 0}
    best: dict[str, Any] | None = None
    for shift in (0, 1, 2):
        total = min(len(sv_rows), max(0, len(lean_rows) - shift))
        matched = 0
        first = None
        for i in range(total):
            left = lean_rows[i + shift]
            right = sv_rows[i]
            if left == right:
                matched += 1
            elif first is None:
                first = {
                    "cycle": i,
                    "lean_cycle": i + shift,
                    "lean": left,
                    "verilog": right,
                }
        candidate = {
            "status": "match" if total > 0 and matched == total else "mismatch",
            "alignment": f"lean_t_plus_{shift}",
            "matched_cycles": matched,
            "total_cycles": total,
            "first_mismatch": first,
        }
        if best is None or (candidate["matched_cycles"], candidate["total_cycles"]) > (
            best["matched_cycles"],
            best["total_cycles"],
        ):
            best = candidate
    return best or {"status": "mismatch", "alignment": None, "matched_cycles": 0, "total_cycles": 0}


def extract_sv(prob_id: str, timeout: int) -> tuple[str | None, str]:
    try:
        proc = _run(["lake", "build", f"Generated.{prob_id}"], cwd=PROJECT_ROOT, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, f"lake build timeout after {timeout}s"
    output = proc.stdout + proc.stderr
    if proc.returncode != 0 or re.search(r"\berror:", output):
        return None, output[-4000:]
    sv_code = Evaluator._extract_sv(output)
    if not sv_code:
        return None, output[-2000:] or "No SystemVerilog found in build output"
    return sv_code, ""


def load_benchmark_results(paths: list[Path]) -> dict[str, dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for path in paths:
        if not path.exists():
            continue
        for line in path.read_text(errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            pid = row.get("prob_id")
            if pid:
                merged[pid] = row
    return merged


def diagnose_one(
    prob_id: str,
    *,
    out_dir: Path,
    cycles: int,
    lean_cycles: int,
    build_timeout: int,
    sim_timeout: int,
    benchmark: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    started = time.time()
    row: dict[str, Any] = {
        "prob_id": prob_id,
        "lean_file": str(PROJECT_ROOT / "Generated" / f"{prob_id}.lean"),
        "entry": None,
        "compile_status": "not_run",
        "lean_sim_status": "not_run",
        "sv_trace_status": "not_run",
        "cosim_status": "not_run",
        "benchmark_sim_status": benchmark.get(prob_id, {}).get("sim_status"),
        "benchmark_compile_pass": benchmark.get(prob_id, {}).get("compile_pass"),
        "detail": "",
    }
    lean_file = PROJECT_ROOT / "Generated" / f"{prob_id}.lean"
    if not lean_file.exists():
        row["compile_status"] = "missing_lean"
        row["detail"] = f"Missing {lean_file}"
        row["elapsed_s"] = round(time.time() - started, 3)
        return row

    work_dir = out_dir / "work" / prob_id
    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    sv_code, build_detail = extract_sv(prob_id, build_timeout)
    if not sv_code:
        row["compile_status"] = "compile_error"
        row["detail"] = build_detail
        row["elapsed_s"] = round(time.time() - started, 3)
        return row
    row["compile_status"] = "ok"

    sv_params = _parse_sv_params(sv_code)
    try:
        entry = parse_entry(lean_file, sv_params=sv_params)
    except Exception as exc:
        row["lean_sim_status"] = "unsupported"
        row["detail"] = str(exc)
        row["elapsed_s"] = round(time.time() - started, 3)
        return row
    row["entry"] = entry.name
    row["output_field_widths"] = entry.field_widths

    params = dict(sv_params)
    for arg in entry.args:
        if not arg.is_signal:
            params[arg.name] = _default_param_value(arg.name, arg.typ, sv_params)
    signal_args = [arg for arg in entry.args if arg.is_signal]
    try:
        traces = make_traces(prob_id, signal_args, params, max(cycles, lean_cycles))
    except Exception as exc:
        row["lean_sim_status"] = "unsupported"
        row["detail"] = str(exc)
        row["elapsed_s"] = round(time.time() - started, 3)
        return row
    row["input_count"] = len(signal_args)
    row["const_params"] = params

    lean_status, lean_rows, lean_detail = run_lean_trace(
        prob_id, entry, traces, params, lean_cycles, work_dir, sim_timeout
    )
    row["lean_sim_status"] = lean_status
    if lean_status != "ok":
        row["detail"] = lean_detail
        row["elapsed_s"] = round(time.time() - started, 3)
        return row
    row["lean_rows"] = lean_rows[: min(4, len(lean_rows))]

    module_name, ports = parse_module_ports(sv_code)
    if not module_name or not ports:
        row["sv_trace_status"] = "unsupported"
        row["detail"] = "Could not parse emitted Verilog module/ports"
        row["elapsed_s"] = round(time.time() - started, 3)
        return row
    row["sv_module"] = module_name
    row["sv_ports"] = ports

    sv_status, sv_rows, sv_detail = run_sv_trace(
        prob_id,
        sv_code,
        module_name,
        ports,
        signal_args,
        traces,
        entry.field_widths,
        cycles,
        work_dir,
        sim_timeout,
    )
    row["sv_trace_status"] = sv_status
    if sv_status != "ok":
        row["detail"] = sv_detail
        row["elapsed_s"] = round(time.time() - started, 3)
        return row
    row["sv_rows"] = sv_rows[: min(4, len(sv_rows))]

    cmp_result = compare_rows(lean_rows, sv_rows)
    row.update(
        {
            "cosim_status": cmp_result["status"],
            "cosim_alignment": cmp_result.get("alignment"),
            "cosim_matched_cycles": cmp_result.get("matched_cycles"),
            "cosim_total_cycles": cmp_result.get("total_cycles"),
            "first_mismatch": cmp_result.get("first_mismatch"),
        }
    )
    row["elapsed_s"] = round(time.time() - started, 3)
    return row


def load_problem_ids(path: Path, limit: int | None) -> list[str]:
    ids = [line.strip() for line in path.read_text().splitlines() if line.strip()]
    seen = set()
    deduped = []
    for pid in ids:
        if pid not in seen:
            deduped.append(pid)
            seen.add(pid)
    return deduped[:limit] if limit else deduped


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def counts(key: str) -> dict[str, int]:
        out: dict[str, int] = {}
        for row in rows:
            value = str(row.get(key))
            out[value] = out.get(value, 0) + 1
        return dict(sorted(out.items()))

    summary = {
        "total": len(rows),
        "compile_status": counts("compile_status"),
        "lean_sim_status": counts("lean_sim_status"),
        "sv_trace_status": counts("sv_trace_status"),
        "cosim_status": counts("cosim_status"),
        "benchmark_sim_status": counts("benchmark_sim_status"),
        "categories": {
            "lean_or_compile_failure": 0,
            "backend_or_cosim_mismatch": 0,
            "lean_sv_match_benchmark_fail": 0,
            "lean_sv_match_benchmark_pass": 0,
            "unsupported_or_tool_error": 0,
        },
    }
    for row in rows:
        if row.get("compile_status") != "ok" or row.get("lean_sim_status") != "ok":
            summary["categories"]["lean_or_compile_failure"] += 1
        elif row.get("cosim_status") == "mismatch":
            summary["categories"]["backend_or_cosim_mismatch"] += 1
        elif row.get("cosim_status") == "match" and row.get("benchmark_sim_status") == "sim_fail":
            summary["categories"]["lean_sv_match_benchmark_fail"] += 1
        elif row.get("cosim_status") == "match" and row.get("benchmark_sim_status") == "sim_pass":
            summary["categories"]["lean_sv_match_benchmark_pass"] += 1
        else:
            summary["categories"]["unsupported_or_tool_error"] += 1
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--problem-file", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--benchmark-results", type=Path, action="append", default=[])
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--cycles", type=int, default=16)
    parser.add_argument("--lean-extra-cycles", type=int, default=2)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--build-timeout", type=int, default=90)
    parser.add_argument("--sim-timeout", type=int, default=60)
    args = parser.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    ids = load_problem_ids(args.problem_file, args.limit)
    benchmark = load_benchmark_results(args.benchmark_results)
    out_jsonl = args.out_dir / "lean_cosim_diagnostics.jsonl"
    summary_path = args.out_dir / "lean_cosim_summary.json"
    lean_cycles = args.cycles + args.lean_extra_cycles

    rows: list[dict[str, Any]] = []
    with out_jsonl.open("w") as fh:
        with futures.ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
            futs = {
                pool.submit(
                    diagnose_one,
                    pid,
                    out_dir=args.out_dir,
                    cycles=args.cycles,
                    lean_cycles=lean_cycles,
                    build_timeout=args.build_timeout,
                    sim_timeout=args.sim_timeout,
                    benchmark=benchmark,
                ): pid
                for pid in ids
            }
            for fut in futures.as_completed(futs):
                pid = futs[fut]
                try:
                    row = fut.result()
                except Exception as exc:
                    row = {
                        "prob_id": pid,
                        "compile_status": "error",
                        "lean_sim_status": "error",
                        "sv_trace_status": "error",
                        "cosim_status": "error",
                        "benchmark_sim_status": benchmark.get(pid, {}).get("sim_status"),
                        "detail": repr(exc),
                    }
                rows.append(row)
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                fh.flush()
                print(
                    f"[{len(rows)}/{len(ids)}] {pid} "
                    f"compile={row.get('compile_status')} "
                    f"lean={row.get('lean_sim_status')} "
                    f"sv={row.get('sv_trace_status')} "
                    f"cosim={row.get('cosim_status')} "
                    f"bench={row.get('benchmark_sim_status')}",
                    flush=True,
                )

    rows.sort(key=lambda r: r.get("prob_id", ""))
    out_jsonl.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n")
    summary = summarize(rows)
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"Wrote {out_jsonl}")
    print(f"Wrote {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
