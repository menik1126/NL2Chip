#!/usr/bin/env python3
"""
Sparkle Agent Search — run coding agent over VerilogEval problems.

For each problem: agent reads NL description + reference Verilog, writes Sparkle
Lean code, iteratively fixes compilation errors, then evaluator scores the result
(compile → extract SV → lint → sim).

Usage:
    python agent/search.py --limit 5
    python agent/search.py --resume
    python agent/search.py --filter "mux|counter"
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import threading
import textwrap
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import anthropic

# Thread-safe locks
_stats_lock = threading.Lock()
_log_lock = threading.Lock()

# Add agent/ to path for local imports
sys.path.insert(0, str(Path(__file__).parent))

from coding_agent import CodingAgent, create_message_with_retries, load_env
from dataset import Dataset, ProblemInfo
from evaluator import (
    CVDP_ADAPTER_ERROR,
    CVDP_PARAMETERIZATION_UNSUPPORTED,
    Evaluator,
    _cvdp_derived_reference_parameter_names,
    _cvdp_exact_parameter_sweep_plan,
    _cvdp_parameter_overrides,
    _cvdp_required_benchmark_parameter_names,
    _module_body_localparameter_declarations,
    _module_localparameters,
    _module_parameters,
    _module_records,
    _rename_module_declaration,
    _strip_sv_comments,
    parse_module_ports,
)
from cvdp_idioms import (
    CVDPIdiomQuery,
    idiom_catalog_sha256,
    render_cvdp_idiom_context,
    select_cvdp_idioms,
)
try:
    from universal_theorem import is_valid_universal_theorem_evidence
except ModuleNotFoundError:  # package-style ``import agent.search``
    from .universal_theorem import is_valid_universal_theorem_evidence
from lean_repl import LeanREPLPool
from report import generate_report

from rich.console import Console, Group
from rich.live import Live
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.table import Table

PROJECT_ROOT = Path(__file__).parent.parent.resolve()
DATASET_DIR = PROJECT_ROOT / "verilog-eval" / "dataset_spec-to-rtl"
GENERATED_DIR = PROJECT_ROOT / "Generated"
RESULTS_DIR = PROJECT_ROOT / "results"

console = Console(force_terminal=True)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Sparkle Agent: NL → Lean → Verilog")
    p.add_argument("--limit", "-l", type=int, default=None, help="Max problems to process")
    p.add_argument("--resume", "-r", action="store_true", help="Skip already-passed problems")
    p.add_argument("--filter", "-f", type=str, default=None, help="Regex filter on problem IDs")
    p.add_argument(
        "--problem-file",
        type=str,
        default=None,
        help="Optional newline-delimited problem id file; blank lines and # comments are ignored.",
    )
    p.add_argument("--model", "-m", type=str, default="claude-sonnet-4-5-20250929")
    p.add_argument("--max-tokens", type=int, default=16384)
    p.add_argument("--max-turns", type=int, default=80)
    p.add_argument(
        "--condition-sv-dir",
        type=str,
        default=None,
        help=(
            "Experimental diagnostic mode: condition Lean generation on an "
            "existing SystemVerilog implementation from this directory. "
            "The file is looked up as <dir>/<prob_id>.sv or <dir>/sv/<prob_id>.sv."
        ),
    )
    p.add_argument("--sim-feedback", action="store_true",
                   help="Enable Lean repair using RTL compile/simulation feedback within the shared --max-turns budget")
    # Deprecated compatibility flags. They are accepted so old scripts do not
    # fail, but sim feedback now shares the main --max-turns budget.
    p.add_argument("--sim-feedback-iters", type=int, default=None, help=argparse.SUPPRESS)
    p.add_argument("--sim-feedback-turns", type=int, default=None, help=argparse.SUPPRESS)
    p.add_argument("--synth", action="store_true", help="Run synthesis + PPA via siliconcrew/ORFS Docker")
    p.add_argument("--synth-feedback", action="store_true", help="Use synthesis failures as generation-time feedback (implies --synth)")
    p.add_argument("--synth-feedback-iters", type=int, default=2, help="Max synthesis-feedback repair iterations (default: 2)")
    p.add_argument("--synth-feedback-turns", type=int, default=30, help="Max agent turns per synthesis-feedback repair (default: 30)")
    p.add_argument("--pnr", action="store_true", help="Full P&R (implies --synth)")
    p.add_argument("--drc", action="store_true", help="Run DRC after P&R (implies --pnr --synth)")
    p.add_argument("--lvs", action="store_true", help="Run LVS after P&R (implies --pnr --synth)")
    p.add_argument("--ppa-opt", action="store_true", help="[DEPRECATED] Use --arch-explore instead. Enable PPA optimization feedback loop (implies --synth)")
    p.add_argument("--ppa-iters", type=int, default=3, help="Max PPA optimization iterations (default: 3)")
    p.add_argument("--ppa-turns", type=int, default=30, help="Max agent turns per PPA iteration (default: 30)")
    p.add_argument(
        "--ppa-feedback-target",
        type=str,
        default="lean",
        choices=["lean", "verilog"],
        help="Where PPA feedback is applied: continue editing Lean (default) or optimize exported SystemVerilog directly",
    )
    p.add_argument("--arch-explore", action="store_true", help="Enable architecture exploration mode (implies --synth)")
    p.add_argument("--arch-candidates", type=int, default=3, help="Max architecture candidates to explore (default: 3)")
    p.add_argument("--arch-turns", type=int, default=40, help="Max agent turns per architecture candidate (default: 40)")
    p.add_argument("--full-history-repair", action="store_true",
                   help="Use full previous conversation for repair loops instead of compact repair prompts")
    p.add_argument("--no-compact-history", action="store_true",
                   help="Disable sliding-window compaction inside agent sessions")
    p.add_argument("--history-window", type=int, default=10,
                   help="Recent messages kept when compacting agent history (default: 10)")
    p.add_argument("--area-budget", type=float, default=None, help="Area constraint in μm² (optional)")
    p.add_argument("--latency-budget", type=int, default=None, help="Latency constraint in cycles (optional)")
    p.add_argument("--corners", action="store_true", help="Run multi-corner PVT STA after P&R (implies --pnr --synth)")
    p.add_argument("--gls", action="store_true", help="Run gate-level simulation after synthesis/PnR (implies --synth)")
    p.add_argument("--quiet", "-q", action="store_true", help="Minimal output (progress bar only)")
    p.add_argument("--workers", "-w", type=int, default=1, help="Concurrent workers (default: 1, recommended: 4)")
    p.add_argument(
        "--ppa-workers",
        type=int,
        default=2,
        help="Global concurrent parameter-sweep Yosys/OpenROAD jobs (default: 2)",
    )
    p.add_argument(
        "--ppa-cache-dir",
        type=str,
        default=None,
        help="Persistent parameter-sweep cache (default: .lake/build/ppa_sweep_cache)",
    )
    p.add_argument("--results-dir", type=str, default=None, help="Directory for run outputs (default: ./results)")
    p.add_argument("--resume-mode", choices=["passed", "completed"], default="passed",
                    help="With --resume, skip sim-passing problems or any completed non-agent-error problem")
    p.add_argument("--dataset", "-d", type=str, default="verilogeval",
                    choices=["verilogeval", "rtllm", "resbench", "cvdp", "realbench"],
                    help="Dataset to evaluate (default: verilogeval)")
    p.add_argument("--no-repl", action="store_true", help="Disable Lean REPL (use lake build instead)")
    return p.parse_args()


def discover_problems(
    limit: int | None = None,
    filter_re: str | None = None,
) -> list[str]:
    """Discover VerilogEval problem IDs from dataset directory."""
    pattern = re.compile(filter_re) if filter_re else None
    prob_ids = set()
    for f in sorted(DATASET_DIR.iterdir()):
        m = re.match(r"(Prob\d+_\w+)_prompt\.txt$", f.name)
        if not m:
            continue
        pid = m.group(1)
        if pattern and not pattern.search(pid):
            continue
        prob_ids.add(pid)
    result = sorted(prob_ids)
    if limit:
        result = result[:limit]
    return result


def load_skill() -> str:
    """Load the Sparkle skill prompt."""
    skill_path = Path(__file__).parent / "skill.txt"
    return skill_path.read_text()


def lean_identifier(name: str) -> str:
    """Return a Lean-friendly identifier while preserving readable module names."""
    ident = re.sub(r"\W+", "_", name.strip())
    ident = re.sub(r"_+", "_", ident).strip("_")
    if not ident:
        ident = "generated_design"
    if ident[0].isdigit():
        ident = f"design_{ident}"
    return ident


def _parse_env_value(env_text: str, key: str) -> str:
    m = re.search(rf"^{re.escape(key)}\s*=\s*(.*?)\s*$", env_text, re.MULTILINE)
    return m.group(1).strip() if m else ""


def _format_param_value(value: object) -> str:
    if isinstance(value, tuple):
        return "(" + ", ".join(_format_param_value(item) for item in value) + ")"
    return str(value)


def _cvdp_generated_power_pairs(iterations: int) -> list[tuple[int, int]]:
    """Recognize CVDP's common Hamming-code parameter helper."""
    value = 4
    pairs: list[tuple[int, int]] = []
    for _ in range(iterations):
        m = value
        p = 0
        while 2 ** p < (p + m + 1):
            p += 1
        pairs.append((m, p))
        value *= 2
    return pairs


def _literal_values_from_ast(
    node: ast.AST,
    assignments: dict[str, list[object]],
) -> list[object] | None:
    if isinstance(node, ast.Name):
        return assignments.get(node.id)
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "get_powers_of_two_pairs"
        and node.args
    ):
        try:
            iterations = ast.literal_eval(node.args[0])
        except Exception:
            return None
        if isinstance(iterations, int) and 0 <= iterations <= 64:
            return list(_cvdp_generated_power_pairs(iterations))
    try:
        value = ast.literal_eval(node)
    except Exception:
        return None
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _cvdp_parse_parameter_sweeps(
    harness_files: dict,
    params: set[str],
) -> tuple[dict[str, list[str]], list[dict[str, str]]]:
    """Extract simple CVDP parameter sweep values from Python harnesses."""
    param_values: dict[str, set[str]] = {name: set() for name in params}
    param_combos: list[dict[str, str]] = []

    for path, content in harness_files.items():
        if not str(path).endswith(".py"):
            continue
        try:
            tree = ast.parse(textwrap.dedent(str(content)))
        except SyntaxError:
            continue

        assignments: dict[str, list[object]] = {}
        loop_values: dict[str, list[object]] = {}

        def module_level_assignments(stmts: list[ast.stmt]):
            for stmt in stmts:
                if isinstance(stmt, ast.Assign):
                    yield stmt
                elif isinstance(stmt, ast.If):
                    yield from module_level_assignments(stmt.body)
                    yield from module_level_assignments(stmt.orelse)

        for node in module_level_assignments(tree.body):
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                values = _literal_values_from_ast(node.value, assignments)
                if values is not None:
                    var_name = node.targets[0].id
                    assignments[var_name] = values
                    param_guess = re.sub(r"_values?$", "", var_name, flags=re.IGNORECASE).upper()
                    if param_guess in param_values:
                        param_values[param_guess].update(_format_param_value(v) for v in values)

        for node in ast.walk(tree):
            if isinstance(node, ast.For) and isinstance(node.target, ast.Name):
                values = _literal_values_from_ast(node.iter, assignments)
                if values is not None:
                    loop_values[node.target.id] = values

        def values_for_node(value_node: ast.AST) -> list[object] | None:
            if isinstance(value_node, ast.Name):
                return loop_values.get(value_node.id) or assignments.get(value_node.id)
            return _literal_values_from_ast(value_node, assignments)

        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            for deco in node.decorator_list:
                if not isinstance(deco, ast.Call) or len(deco.args) < 2:
                    continue
                func_name = ast.unparse(deco.func) if hasattr(ast, "unparse") else ""
                if not func_name.endswith("parametrize"):
                    continue
                try:
                    names_text = ast.literal_eval(deco.args[0])
                except Exception:
                    continue
                names = [name.strip() for name in str(names_text).split(",") if name.strip()]
                values = values_for_node(deco.args[1])
                if not names or values is None:
                    continue
                if len(names) == 1:
                    param = names[0]
                    if param in param_values:
                        param_values[param].update(_format_param_value(v) for v in values)
                else:
                    for row in values:
                        if not isinstance(row, (list, tuple)) or len(row) != len(names):
                            continue
                        combo = {
                            name: _format_param_value(value)
                            for name, value in zip(names, row)
                            if name in param_values
                        }
                        if combo:
                            param_combos.append(combo)
                            for name, value in combo.items():
                                param_values[name].add(value)

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for kw in node.keywords:
                if kw.arg != "parameters" or not isinstance(kw.value, ast.Dict):
                    continue
                for key_node, value_node in zip(kw.value.keys, kw.value.values):
                    if key_node is None:
                        continue
                    try:
                        param = ast.literal_eval(key_node)
                    except Exception:
                        continue
                    if param not in param_values:
                        continue
                    values = values_for_node(value_node)
                    if values is not None:
                        param_values[param].update(_format_param_value(v) for v in values)

    return (
        {name: sorted(values, key=lambda item: (len(item), item)) for name, values in param_values.items() if values},
        param_combos,
    )


def _parse_cvdp_harness_usage(harness_files: dict) -> dict[str, set[str]]:
    """Extract real cocotb DUT uses without scanning comments or strings."""
    all_names: set[str] = set()
    direct_names: set[str] = set()
    assigned: set[str] = set()
    clocked: set[str] = set()
    params: set[str] = set()

    def direct_handle_name(node: ast.AST) -> str | None:
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "dut"
            and node.attr not in {"_log", "_id"}
        ):
            return node.attr
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Name)
            and node.value.id == "dut"
        ):
            try:
                name = ast.literal_eval(node.slice)
            except Exception:
                return None
            return name if isinstance(name, str) and re.fullmatch(r"[A-Za-z_]\w*", name) else None
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "dut"
            and node.func.attr == "_id"
            and node.args
        ):
            try:
                name = ast.literal_eval(node.args[0])
            except Exception:
                return None
            return name if isinstance(name, str) and re.fullmatch(r"[A-Za-z_]\w*", name) else None
        return None

    def dict_parameter_names(node: ast.AST) -> set[str]:
        if not isinstance(node, ast.Dict):
            return set()
        result: set[str] = set()
        for key in node.keys:
            if key is None:
                continue
            try:
                name = ast.literal_eval(key)
            except Exception:
                continue
            if isinstance(name, str) and re.fullmatch(r"[A-Za-z_]\w*", name):
                result.add(name)
        return result

    for path, content in harness_files.items():
        if not str(path).endswith(".py"):
            continue
        try:
            tree = ast.parse(textwrap.dedent(str(content)))
        except SyntaxError:
            continue

        parameter_dicts: dict[str, set[str]] = {}
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                value = node.value
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                names = dict_parameter_names(value)
                for target in targets:
                    if isinstance(target, ast.Name) and names:
                        parameter_dicts[target.id] = names
                        if target.id.lower() in {"parameter", "parameters"}:
                            params.update(names)

        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "dut"
                and node.attr not in {"_log", "_id"}
            ):
                all_names.add(node.attr)
            elif isinstance(node, (ast.Subscript, ast.Call)):
                name = direct_handle_name(node)
                if name:
                    all_names.add(name)

            if isinstance(node, ast.Attribute) and node.attr == "value":
                name = direct_handle_name(node.value)
                if name:
                    direct_names.add(name)

            if isinstance(node, ast.Call):
                func_name = (
                    node.func.attr if isinstance(node.func, ast.Attribute)
                    else node.func.id if isinstance(node.func, ast.Name)
                    else ""
                )
                for argument in [*node.args, *(kw.value for kw in node.keywords)]:
                    name = direct_handle_name(argument)
                    if name:
                        direct_names.add(name)
                        if func_name == "Clock":
                            clocked.add(name)
                for keyword in node.keywords:
                    if keyword.arg not in {"parameter", "parameters"}:
                        continue
                    names = dict_parameter_names(keyword.value)
                    if isinstance(keyword.value, ast.Name):
                        names.update(parameter_dicts.get(keyword.value.id, set()))
                    params.update(names)

            targets: list[ast.AST] = []
            if isinstance(node, ast.Assign):
                targets = list(node.targets)
            elif isinstance(node, ast.AnnAssign):
                targets = [node.target]
            elif isinstance(node, ast.AugAssign):
                targets = [node.target]
            for target in targets:
                if isinstance(target, ast.Attribute) and target.attr == "value":
                    name = direct_handle_name(target.value)
                    if name:
                        assigned.add(name)

    all_names.difference_update({"_log", "_id"})
    direct_names.difference_update({"_log", "_id"})
    reset_like = {name for name in all_names if _port_kind(name) == "reset"}
    clock_like = {name for name in all_names if _port_kind(name) == "clock"}
    inputs = (assigned | clocked | reset_like | clock_like) - params
    ports = all_names - params
    return {
        "ports": ports,
        "direct_ports": direct_names - params,
        "inputs": inputs & ports,
        "outputs": ports - inputs,
        "params": params,
    }


def _module_internal_signal_names(sv_code: str, module_name: str) -> set[str]:
    """Return identifiers already owned by the exact baseline module body.

    SystemVerilog permits internal objects with builtin, typedef, enum, struct,
    interface, and user-defined types. Conservatively treating every body
    identifier as baseline-owned avoids promoting any of those cocotb-visible
    hierarchy handles to a top-level port. An explicit prompt declaration can
    still promote a name because the merge subtracts ``explicit_names``.
    """
    record = next(
        (candidate for candidate in _module_records(sv_code) if candidate[0] == module_name),
        None,
    )
    if record is None:
        return set()
    body = _strip_sv_comments(record[2])
    return set(re.findall(r"[A-Za-z_][A-Za-z0-9_$]*", body))


def _module_nonport_constant_names(sv_code: str, module_name: str) -> set[str]:
    return {
        parameter.name
        for parameter in (
            *_module_localparameters(sv_code, module_name),
            *_module_body_localparameter_declarations(sv_code, module_name),
        )
    }


def _port_width_bits(typ: str) -> int:
    m = re.search(r"\[\s*(\d+)\s*:\s*(\d+)\s*\]", typ or "")
    if not m:
        return 1
    return abs(int(m.group(1)) - int(m.group(2))) + 1


def _port_kind(name: str) -> str:
    lower = name.lower()
    if re.search(r"(^|_)(clk|clock|aclk|pclk)($|_)", lower):
        return "clock"
    if re.search(r"(^|_)(rst|reset|areset|arst|rst_n|reset_n|aresetn|rstn|rstb|resetb|arstb|aresetb)($|_)", lower):
        return "reset"
    return ""


def _reset_polarity(name: str) -> str:
    lower = name.lower()
    compact = re.sub(r"[^a-z0-9]", "", lower)
    if lower.endswith(("_n", "_ni", "_b")) or compact.endswith(("rstn", "resetn", "aresetn", "arstn", "rstb", "resetb", "aresetb", "arstb")):
        return "active-low"
    return "active-high"


def _classify_reset_drive(values: list[str]) -> str | None:
    bits = [v for v in values if v in {"0", "1"}]
    if len(bits) < 2:
        return None
    first = bits[0]
    if any(v != first for v in bits[1:]):
        return "active-low" if first == "0" else "active-high"
    return None


def _infer_cvdp_reset_polarities(harness_files: dict, reset_names: list[str]) -> dict[str, str]:
    py_text = "\n\n".join(
        str(content) for path, content in harness_files.items() if str(path).endswith(".py")
    )
    inferred: dict[str, str] = {}
    for name in reset_names:
        direct = re.findall(rf"\bdut\.{re.escape(name)}\.value\s*=\s*([01])\b", py_text)
        polarity = _classify_reset_drive(direct)
        if polarity:
            inferred[name] = polarity

    helper_polarity: dict[str, str] = {}
    func_re = re.compile(
        r"(?ms)^(?:async\s+)?def\s+([A-Za-z_]\w*)\s*\(([^)]*)\)\s*:\s*(.*?)(?=^(?:async\s+)?def\s+|\Z)"
    )
    for match in func_re.finditer(py_text):
        func_name = match.group(1)
        args = [arg.strip().split("=")[0].strip() for arg in match.group(2).split(",") if arg.strip()]
        if not args:
            continue
        body = match.group(3)
        for arg in args[:2]:
            values = re.findall(rf"\b{re.escape(arg)}\.value\s*=\s*([01])\b", body)
            polarity = _classify_reset_drive(values)
            if polarity:
                helper_polarity[func_name] = polarity
                break
    for func_name, polarity in helper_polarity.items():
        for name in re.findall(rf"\b{re.escape(func_name)}\s*\(\s*dut\.([A-Za-z_]\w*)", py_text):
            if name in reset_names and name not in inferred:
                inferred[name] = polarity
    return inferred


def _format_port(direction: str, typ: str, name: str, reset_polarities: dict[str, str] | None = None) -> str:
    width = _port_width_bits(typ)
    kind = _port_kind(name)
    suffix = []
    if width != 1:
        suffix.append(f"{width} bits")
    if kind == "reset":
        suffix.append(f"reset, {(reset_polarities or {}).get(name) or _reset_polarity(name)}")
    elif kind:
        suffix.append(kind)
    tail = f" ({', '.join(suffix)})" if suffix else ""
    return f"{direction} {name}: {typ or 'logic'}{tail}"


def _clean_prompt_cell(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", str(text))
    text = text.replace("`", "").replace("**", "").replace("__", "")
    return re.sub(r"\s+", " ", text).strip()


def _prompt_size_to_type(size_text: str) -> str:
    """Convert a human-readable prompt size cell into a contract type string."""
    size = _clean_prompt_cell(size_text)
    if not size:
        return "logic"
    packed = re.fullmatch(r"\[\s*(.+?)\s*\]", size)
    if packed:
        return f"logic [{packed.group(1)}]"
    lower = size.lower()
    if size.isdigit():
        width = int(size)
        return "logic" if width <= 1 else f"logic [{width - 1}:0]"
    if re.search(r"\b1\s*-?\s*(?:bit|bits?)?\b", lower) and not re.search(r"[\w$]\s*[*+:-]", size):
        return "logic"
    m = re.search(r"\b(\d+)\s*-?\s*(?:bit|bits?)\b", lower)
    if m:
        width = int(m.group(1))
        return "logic" if width <= 1 else f"logic [{width - 1}:0]"
    expr = re.sub(r"\bbits?\b", "", size, flags=re.IGNORECASE).strip()
    expr = expr.strip("` ")
    if not expr:
        return "logic"
    return f"logic [{expr}-1:0]"


def _normalize_prompt_heading(text: str) -> str:
    heading = _clean_prompt_cell(text).strip().strip("# :-")
    heading = re.sub(r"^\d+\s*[.)]\s*", "", heading)
    return re.sub(r"\s+", " ", heading).strip().lower()


def _prompt_section_kind(heading: str) -> str | None:
    normalized = _normalize_prompt_heading(heading)
    if re.fullmatch(
        r"(?:(?:module|design)\s+)?parameters?|parameter (?:table|definitions?)|parameterization",
        normalized,
    ):
        return "parameter"
    if re.fullmatch(r"inputs?|input (?:signals?|ports?)", normalized):
        return "input"
    if re.fullmatch(r"outputs?|output (?:signals?|ports?)", normalized):
        return "output"
    return None


def _prompt_interface_container(heading: str) -> bool:
    normalized = _normalize_prompt_heading(heading)
    return bool(
        re.fullmatch(
            r"(?:module )?interface(?: specifications?)?|"
            r"interface of (?:the )?module(?: .+)?|design specifications?|i/o ports?",
            normalized,
        )
    )


def _prompt_excluded_section(heading: str) -> bool:
    """Return whether a section contains examples rather than declarations."""
    normalized = _normalize_prompt_heading(heading)
    return bool(
        re.search(
            r"\b(?:examples?|computations?(?: steps?)?|waveforms?|wave diagrams?|"
            r"timing diagrams?|test vectors?)\b|"
            r"^sample (?:operation|usage|values?)\b|"
            r"^(?:expected|observed) (?:values?|results?)\b",
            normalized,
        )
    )


def _prompt_heading_info(line: str) -> tuple[int | None, str]:
    """Parse markdown and CVDP's bold/numbered pseudo-headings."""
    stripped = str(line or "").strip()
    markdown = re.match(r"^(#{1,6})\s*(.*?)\s*#*\s*$", stripped)
    if markdown:
        return len(markdown.group(1)), _normalize_prompt_heading(markdown.group(2))

    list_item = bool(re.match(r"^(?:[-*+]|\d+[.)])\s+", stripped))
    body = re.sub(r"^(?:(?:[-*+]|\d+[.)])\s+)", "", stripped)
    bold_match = re.fullmatch(r"(?:\*\*|__)(.*?)(?:\*\*|__)\s*:?", body)
    candidate = bold_match.group(1) if bold_match else body
    normalized = _normalize_prompt_heading(candidate)
    if _prompt_section_kind(normalized) or _prompt_interface_container(normalized):
        return 7, normalized
    standalone_heading = (
        bold_match is not None
        or (not list_item and len(stripped) <= 120 and stripped.rstrip().endswith(":"))
    )
    if standalone_heading and _prompt_excluded_section(normalized):
        return 7, normalized
    return None, normalized


def _prompt_heading(line: str) -> tuple[bool, str]:
    level, heading = _prompt_heading_info(line)
    return level is not None, heading


def _parse_prompt_declaration(text: str) -> tuple[str, str] | None:
    declaration = _clean_prompt_cell(text).strip().rstrip(":;,.")
    declaration = re.sub(r"^(?:input|output|inout)\s+", "", declaration, flags=re.IGNORECASE)
    if "=" in declaration or re.search(r"\b\d+\s*'[sS]?[bBoOdDhH]", declaration):
        return None

    match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_$]*)\s*\((.*?)\)", declaration)
    if match:
        name, size = match.groups()
        packed_range = re.search(r"\[[^\]]+\]", size)
        return (f"logic {packed_range.group(0)}" if packed_range else "logic"), name

    match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_$]*)\s*(\[[^\]]+\])", declaration)
    if match:
        name, packed_range = match.groups()
        return f"logic {packed_range}", name

    match = re.fullmatch(r"(\[[^\]]+\])\s*([A-Za-z_][A-Za-z0-9_$]*)", declaration)
    if match:
        packed_range, name = match.groups()
        return f"logic {packed_range}", name

    match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_$]*)", declaration)
    return ("logic", match.group(1)) if match else None


def _prompt_list_body(raw_line: str) -> str | None:
    match = re.match(r"^\s*(?:[-*+]|\d+[.)])\s+(.*?)\s*$", raw_line)
    return match.group(1) if match else None


def _prompt_list_declaration(
    body: str,
    *,
    allow_bare: bool = False,
) -> tuple[str, str] | None:
    """Return a strongly marked declaration fragment and its description."""
    patterns = (
        r"^(?:\*\*|__)(.*?)(?:\*\*|__)\s*:?(.*)$",
        r"^`([^`]+)`\s*:?(.*)$",
        r"^([A-Za-z_][A-Za-z0-9_$]*(?:\s*\([^)]*\)|\s*\[[^\]]+\])?|"
        r"\[[^\]]+\]\s*[A-Za-z_][A-Za-z0-9_$]*)\s*:(.*)$",
    )
    for pattern in patterns:
        match = re.match(pattern, body)
        if match:
            return match.group(1).strip(), match.group(2).strip()
    if allow_bare:
        return body.strip(), ""
    return None


def _prompt_description_is_example_value(text: str) -> bool:
    cleaned = _clean_prompt_cell(text).strip()
    literal_suffix = (
        r"(?:\s*\([^)]*\)|"
        r"\s+(?:cycles?|clocks?|ticks?|ns|us|ms|seconds?))?[.;]?"
    )
    return bool(
        re.match(
            r"^(?:=\s*)?(?:\d+\s*)?'[sS]?[bBoOdDhH][0-9a-fA-F_xXzZ?]+\b",
            cleaned,
        )
        or re.fullmatch(rf"[+-]?\d+(?:\.\d+)?{literal_suffix}", cleaned)
        or re.fullmatch(rf"[+-]?0[xX][0-9a-fA-F_]+{literal_suffix}", cleaned)
        or re.fullmatch(rf"[+-]?0[bB][01_]+{literal_suffix}", cleaned)
        or re.fullmatch(rf"[+-]?0[oO][0-7_]+{literal_suffix}", cleaned)
        or re.fullmatch(rf"'[01xXzZ]{literal_suffix}", cleaned)
        or re.fullmatch(
            rf"(?:true|false|null|none){literal_suffix}",
            cleaned,
            re.IGNORECASE,
        )
        or re.fullmatch(r"[\"'].*[\"']", cleaned)
        or re.match(r"^[A-Za-z_][A-Za-z0-9_$]*\s*=", cleaned)
    )


def _prompt_width_from_text(
    text: str,
    *,
    known_symbols: set[str] | None = None,
) -> str | None:
    raw = str(text or "")
    cleaned = _clean_prompt_cell(text)
    packed_range = re.search(r"\[[^\]]*:[^\]]*\]", cleaned)
    if packed_range:
        return _prompt_size_to_type(packed_range.group(0))
    defined_range = re.search(
        r"\b(?:defined|sized|declared)\s+as\s+(\[[^\]]+\])",
        cleaned,
        re.IGNORECASE,
    )
    if defined_range:
        return _prompt_size_to_type(defined_range.group(1))
    parenthesized = re.search(
        r"\(\s*([^()]{1,96}?)\s*-?\s*bits?\s*\)",
        cleaned,
        re.IGNORECASE,
    )
    if parenthesized:
        width = parenthesized.group(1).strip()
        identifiers = set(re.findall(r"[A-Za-z_$][A-Za-z0-9_$]*", width))
        quoted = set(re.findall(r"`([A-Za-z_$][A-Za-z0-9_$]*)`", raw))
        if width.isdigit() or (
            identifiers
            and identifiers <= (known_symbols or set()) | quoted
        ):
            return _prompt_size_to_type(width)
    immediate = re.match(
        r"^\s*(\d+|[A-Za-z_$][A-Za-z0-9_$]*(?:\s*[*+\-/]\s*[A-Za-z0-9_$()]+)*)\s*-?\s*bits?\b",
        cleaned,
        re.IGNORECASE,
    )
    if immediate:
        width = immediate.group(1).strip()
        identifiers = set(re.findall(r"[A-Za-z_$][A-Za-z0-9_$]*", width))
        quoted = set(re.findall(r"`([A-Za-z_$][A-Za-z0-9_$]*)`", raw))
        if width.isdigit() or (
            identifiers
            and identifiers <= (known_symbols or set()) | quoted
        ):
            return _prompt_size_to_type(width)
    return None


def _prompt_parameter_from_body(
    body: str,
    *,
    allow_bare: bool = False,
) -> str | None:
    declaration = _prompt_list_declaration(body, allow_bare=allow_bare)
    candidate = declaration[0] if declaration else ""
    if not candidate:
        default_sentence = re.match(
            r"^The\s+default\s+value\s+of\s+`([A-Za-z_][A-Za-z0-9_$]*)`(?![A-Za-z0-9_$])",
            body,
            re.IGNORECASE,
        )
        candidate = default_sentence.group(1) if default_sentence else ""
    candidate = _clean_prompt_cell(candidate).strip().rstrip(":")
    candidate = re.sub(
        r"^(?:parameter|localparam)(?:\s+(?:int|integer|logic))?\s+",
        "",
        candidate,
        flags=re.IGNORECASE,
    )
    if "=" in candidate:
        return None
    match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_$]*)", candidate)
    return match.group(1) if match else None


def _prompt_parameter_is_derived(text: str) -> bool:
    cleaned = _clean_prompt_cell(text)
    return bool(
        re.search(
            r"\b(?:dynamically\s+)?calculated\b|"
            r"\bcalculate(?:s|d)?\s+the\s+(?:width|number|size)\b|"
            r"\b(?:derived|computed)\s+from\b",
            cleaned,
            re.IGNORECASE,
        )
    )


@dataclass
class _PromptInterface:
    ports: list[tuple[str, str, str]] = field(default_factory=list)
    parameters: set[str] = field(default_factory=set)
    derived_parameters: set[str] = field(default_factory=set)
    complete_directions: set[str] = field(default_factory=set)


def _parse_prompt_interface(prompt_text: str) -> _PromptInterface:
    """Parse only declarative interface sections from a CVDP prompt."""
    result = _PromptInterface()
    port_index: dict[str, int] = {}
    port_priority: dict[str, int] = {}
    section: str | None = None
    blocked_level: int | None = None
    table_header: list[str] | None = None
    last_port: str | None = None
    last_parameter: str | None = None
    in_fence = False
    pseudo_excluded = False
    nearest_markdown_heading = ""
    non_signal_labels = {
        "action", "behavior", "check", "computation", "description",
        "implementation", "initialization", "purpose", "transition", "update",
    }

    def add_port(direction: str, typ: str, name: str, priority: int) -> None:
        if (
            not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", name)
            or name.lower() in non_signal_labels
        ):
            return
        entry = (direction, typ or "logic", name)
        if name in port_index:
            if priority < port_priority[name]:
                return
            result.ports[port_index[name]] = entry
            port_priority[name] = priority
            return
        port_index[name] = len(result.ports)
        port_priority[name] = priority
        result.ports.append(entry)

    for raw_line in str(prompt_text or "").splitlines():
        stripped = raw_line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            section = None
            table_header = None
            last_port = None
            last_parameter = None
            continue
        if in_fence:
            continue

        level, heading = _prompt_heading_info(raw_line)
        markdown_heading = level is not None and level <= 6
        if pseudo_excluded:
            if level is not None and _prompt_interface_container(heading):
                pseudo_excluded = False
            else:
                continue
        if markdown_heading:
            nearest_markdown_heading = heading
        if markdown_heading and blocked_level is not None and level <= blocked_level:
            blocked_level = None
        if blocked_level is not None:
            continue
        if level is not None and _prompt_excluded_section(heading):
            if markdown_heading:
                blocked_level = level
                section = None
                table_header = None
                last_port = None
                last_parameter = None
            else:
                raw_body = re.sub(
                    r"^(?:(?:[-*+]|\d+[.)])\s+)",
                    "",
                    stripped,
                )
                pure_bold = bool(
                    re.fullmatch(r"(?:\*\*|__).*?(?:\*\*|__)\s*:?", raw_body)
                )
                title_scope = bool(re.fullmatch(
                    r"(?:(?:(?:usage|worked|simulation) )?"
                    r"(?:examples?|computations?|waveforms?)|"
                    r"for examples?)",
                    heading,
                ))
                if pure_bold or title_scope:
                    pseudo_excluded = True
                    section = None
                    table_header = None
                    last_port = None
                    last_parameter = None
            continue
        if level is not None:
            kind = _prompt_section_kind(heading)
            pseudo_in_behavior = (
                not markdown_heading
                and kind in {"input", "output"}
                and bool(re.match(
                    r"^(?:when\b|module behavior\b|behavioral\b|functional requirements?\b)",
                    nearest_markdown_heading,
                ))
            )
            if pseudo_in_behavior:
                continue
            if markdown_heading or kind or _prompt_interface_container(heading):
                section = kind
                table_header = None
                last_port = None
                last_parameter = None
            if kind in {"input", "output"}:
                result.complete_directions.add(kind)
            continue

        # Explicit modification prose is additive, not a closed-world port
        # list. This covers prompts such as gf_mac's two new status outputs.
        for prose in re.finditer(
            r"\b(?:introduce|add)\s+(?:(?:a|an|another|new|additional)\s+)*"
            r"(input|output)\s+(?:signal|port)\s*,?\s*`([A-Za-z_][A-Za-z0-9_$]*)`",
            raw_line,
            re.IGNORECASE,
        ):
            add_port(prose.group(1).lower(), "logic", prose.group(2), 0)

        if "|" in stripped:
            cells = [_clean_prompt_cell(cell) for cell in stripped.strip("|").split("|")]
            normalized = [_normalize_prompt_heading(cell) for cell in cells]
            if all(not cell or re.fullmatch(r":?-{2,}:?", cell) for cell in normalized):
                continue

            if section == "parameter":
                candidate = cells[0] if cells else ""
                if _normalize_prompt_heading(candidate) not in {"parameter", "name", "parameter name"}:
                    name = _prompt_parameter_from_body(f"`{candidate}`:")
                    if name:
                        result.parameters.add(name)
                        if _prompt_parameter_is_derived(" ".join(cells[1:])):
                            result.derived_parameters.add(name)
                continue

            if any(cell in {"direction", "dir"} for cell in normalized) and any(
                cell in {"port", "port name", "signal", "signal name", "name"}
                for cell in normalized
            ):
                table_header = normalized
                continue
            if table_header is None:
                if normalized and normalized[0] in {
                    "port", "port name", "signal", "signal name", "name"
                }:
                    table_header = normalized
                continue

            def column_index(names: set[str]) -> int | None:
                return next(
                    (idx for idx, value in enumerate(table_header or []) if value in names),
                    None,
                )

            name_idx = column_index({"port", "port name", "signal", "signal name", "name"})
            width_idx = column_index({"width", "size", "bits", "bit width"})
            direction_idx = column_index({"direction", "dir"})
            if name_idx is None or name_idx >= len(cells):
                continue
            row_direction = section
            if direction_idx is not None and direction_idx < len(cells):
                value = cells[direction_idx].strip().lower()
                row_direction = (
                    "input" if value in {"input", "in"}
                    else "output" if value in {"output", "out"}
                    else None
                )
            if row_direction not in {"input", "output"}:
                continue
            parsed = _parse_prompt_declaration(cells[name_idx])
            if not parsed:
                continue
            embedded_typ, name = parsed
            typ = embedded_typ
            if width_idx is not None and width_idx < len(cells) and cells[width_idx]:
                typ = _prompt_size_to_type(cells[width_idx])
            add_port(row_direction, typ, name, 2)
            result.complete_directions.add(row_direction)
            last_port = name
            continue

        body = _prompt_list_body(raw_line)
        numbered_declaration = bool(re.match(r"^\s*\d+[.)]\s+", raw_line))
        if section == "parameter" and body is not None:
            name = _prompt_parameter_from_body(body, allow_bare=numbered_declaration)
            if name:
                result.parameters.add(name)
                last_parameter = name
                if _prompt_parameter_is_derived(body):
                    result.derived_parameters.add(name)
            elif last_parameter and _prompt_parameter_is_derived(body):
                result.derived_parameters.add(last_parameter)
            continue

        if section in {"input", "output"} and body is not None:
            declaration = _prompt_list_declaration(
                body,
                allow_bare=numbered_declaration,
            )
            if declaration and not _prompt_description_is_example_value(declaration[1]):
                parsed = _parse_prompt_declaration(declaration[0])
                if parsed:
                    typ, name = parsed
                    described_typ = (
                        _prompt_width_from_text(
                            declaration[0], known_symbols=result.parameters
                        )
                        or _prompt_width_from_text(
                            declaration[1], known_symbols=result.parameters
                        )
                    )
                    add_port(section, described_typ if typ == "logic" and described_typ else typ, name, 1)
                    last_port = name
                    continue

        if section in {"input", "output"} and last_port:
            described_typ = _prompt_width_from_text(
                raw_line, known_symbols=result.parameters
            )
            if described_typ and last_port in port_index:
                idx = port_index[last_port]
                old_direction, _, name = result.ports[idx]
                result.ports[idx] = (old_direction, described_typ, name)

    return result


def _parse_parameters_from_prompt(prompt_text: str) -> set[str]:
    """Extract public module-parameter names from declarative prompt sections."""
    return _parse_prompt_interface(prompt_text).parameters


def _apply_prompt_parameters(
    harness_usage: dict[str, set[str]], prompt_text: str
) -> dict[str, set[str]]:
    usage = {key: set(values) for key, values in harness_usage.items()}
    usage.setdefault("params", set()).update(_parse_parameters_from_prompt(prompt_text))
    for key in ("ports", "direct_ports", "inputs", "outputs"):
        usage.setdefault(key, set()).difference_update(usage["params"])
    return usage


def _parse_ports_from_prompt(prompt_text: str) -> list[tuple[str, str, str]]:
    """Extract benchmark-declared ports from declarative prompt sections.

    Example/computation/waveform sections and fenced context code are excluded;
    they describe values and implementation details, not the public interface.
    """
    return _parse_prompt_interface(prompt_text).ports


def _benchmark_expected_ports(info: ProblemInfo | None) -> list[tuple[str, str, str]]:
    if info is None:
        return []
    metadata = info.metadata or {}
    design_name = info.design_name
    harness_usage: dict[str, set[str]] = {
        "ports": set(),
        "direct_ports": set(),
        "inputs": set(),
        "outputs": set(),
        "params": set(),
    }
    if metadata.get("dataset") == "cvdp":
        harness_files = metadata.get("harness_files", {}) or {}
        env_text = str(harness_files.get("src/.env", ""))
        design_name = _parse_env_value(env_text, "TOPLEVEL") or design_name
        harness_usage = _parse_cvdp_harness_usage(harness_files)
        harness_usage = _apply_prompt_parameters(harness_usage, info.prompt_text or "")
        harness_usage["params"].update(
            parameter.name
            for parameter in _module_parameters(info.ref_code or "", design_name)
        )
        for key in ("ports", "direct_ports", "inputs", "outputs"):
            harness_usage.setdefault(key, set()).difference_update(harness_usage["params"])

    ref_mod, ref_ports = parse_module_ports(
        info.ref_code or "",
        module_name=design_name or None,
    )
    if design_name and ref_mod != design_name:
        ref_ports = []

    prompt_interface = _parse_prompt_interface(info.prompt_text or "")
    prompt_ports = prompt_interface.ports
    explicit_names = {name for _, _, name in prompt_ports}
    baseline_nonports = (
        _module_internal_signal_names(info.ref_code or "", design_name)
        | _module_nonport_constant_names(info.ref_code or "", design_name)
    )
    required_handles = harness_usage.get("direct_ports", set()) - (
        baseline_nonports - explicit_names
    )
    if ref_ports:
        required_handles.difference_update(name for _, _, name in ref_ports)

    def handle_direction(name: str) -> str:
        if name in harness_usage.get("inputs", set()) or _port_kind(name) in {"clock", "reset"}:
            return "input"
        return "output"

    if not ref_ports:
        expected = list(prompt_ports)
        seen = {name for _, _, name in expected}
        for name in sorted(required_handles - seen):
            direction = handle_direction(name)
            if direction in prompt_interface.complete_directions:
                continue
            expected.append((direction, "logic", name))
        return expected

    # CVDP's exact target module is the pre-modification baseline, not a
    # closed-world declaration of the requested final module. Dedicated prompt
    # Input(s)/Output(s) sections replace that direction; additive prose and
    # direct top-level harness handles extend the untouched baseline. Internal
    # hierarchical handles are absent from ``required_handles`` by design.
    expected: list[tuple[str, str, str]] = []
    for direction in ("input", "output"):
        prompt_direction = [port for port in prompt_ports if port[0] == direction]
        if direction in prompt_interface.complete_directions and prompt_direction:
            selected = list(prompt_direction)
        else:
            selected = [
                port
                for port in ref_ports
                if port[0] == direction and port[2] not in explicit_names
            ]
            selected.extend(prompt_direction)

        seen = {name for _, _, name in selected}
        for name in sorted(required_handles - seen):
            if (
                handle_direction(name) == direction
                and direction not in prompt_interface.complete_directions
            ):
                selected.append((direction, "logic", name))
                seen.add(name)
        expected.extend(selected)

    expected.extend(port for port in ref_ports if port[0] not in {"input", "output"})
    return expected


def format_benchmark_interface_contract(info: ProblemInfo | None) -> str:
    """Summarize benchmark-visible interface facts without including testbench source."""
    if info is None:
        return ""
    metadata = info.metadata or {}
    design_name = info.design_name
    harness_usage: dict[str, set[str]] = {
        "ports": set(),
        "direct_ports": set(),
        "inputs": set(),
        "outputs": set(),
        "params": set(),
    }
    prompt_interface = _parse_prompt_interface(info.prompt_text or "")
    derived_symbols: set[str] = set()
    adapter_observable_metadata: set[str] = set()

    if metadata.get("dataset") == "cvdp":
        harness_files = metadata.get("harness_files", {}) or {}
        env_text = str(harness_files.get("src/.env", ""))
        design_name = _parse_env_value(env_text, "TOPLEVEL") or design_name
        raw_harness_usage = _parse_cvdp_harness_usage(harness_files)
        harness_parameters = set(raw_harness_usage["params"])
        ref_parameters = {
            parameter.name
            for parameter in _module_parameters(info.ref_code or "", design_name)
        }
        reference_derived_parameters = (
            _cvdp_derived_reference_parameter_names(
                info.ref_code or "", design_name
            )
        )
        ref_constants = _module_nonport_constant_names(
            info.ref_code or "",
            design_name,
        )
        derived_symbols = (
            prompt_interface.derived_parameters
            | reference_derived_parameters
            | (raw_harness_usage.get("direct_ports", set()) & ref_constants)
        ) - harness_parameters
        if (
            design_name == "hamming_rx"
            and "ENCODED_DATA" in derived_symbols
            and "ENCODED_DATA" in raw_harness_usage.get("outputs", set())
        ):
            adapter_observable_metadata.add("ENCODED_DATA")
        overrideable_parameters = (
            harness_parameters
            | ref_parameters
            | (prompt_interface.parameters - prompt_interface.derived_parameters)
        ) - derived_symbols

        harness_usage = _apply_prompt_parameters(
            raw_harness_usage,
            info.prompt_text or "",
        )
        harness_usage["params"] = overrideable_parameters
        nonport_symbols = (
            prompt_interface.parameters
            | ref_constants
            | overrideable_parameters
            | derived_symbols
        )
        for key in ("ports", "direct_ports", "inputs", "outputs"):
            harness_usage.setdefault(key, set()).difference_update(nonport_symbols)
        param_values, param_combos = _cvdp_parse_parameter_sweeps(
            harness_files,
            overrideable_parameters,
        )
    else:
        param_values, param_combos = {}, []

    ref_mod, _ = parse_module_ports(info.ref_code or "")
    expected_ports = _benchmark_expected_ports(info)

    if not expected_ports and not design_name:
        return ""

    lines = [f"- Expected top module: `{design_name or ref_mod}`"]
    verilog_sources = metadata.get("verilog_sources")
    if verilog_sources:
        lines.append(f"- Benchmark RTL source target(s): {', '.join(str(s) for s in verilog_sources)}")
    if harness_usage["params"]:
        lines.append(
            "- Benchmark parameters required by reference/harness: "
            + ", ".join(sorted(harness_usage["params"]))
        )
        if param_values:
            value_text = "; ".join(
                f"{name}={{{', '.join(values[:8])}{', ...' if len(values) > 8 else ''}}}"
                for name, values in sorted(param_values.items())
            )
            lines.append(f"- Observed parameter sweep values: {value_text}")
        if param_combos:
            rendered = []
            for combo in param_combos[:8]:
                rendered.append("(" + ", ".join(f"{k}={v}" for k, v in sorted(combo.items())) + ")")
            suffix = ", ..." if len(param_combos) > 8 else ""
            lines.append(f"- Observed parameter combinations: {', '.join(rendered)}{suffix}")
        lines.append(
            "- Parameterization requirement: cocotb rebuilds/instantiates the DUT with these parameter values; "
            "a fixed Sparkle core behind a parameterized wrapper is not a valid implementation. "
            "Use a top-level Lean `Nat` binder with the exact benchmark parameter name and synthesize it with "
            "`#synthesizeVerilog <design> parameters [PARAM := <nonnegative-default>]`. Sparkle then emits a native "
            "SystemVerilog module parameter that must actually occur in the affected ports, state, memories, or logic."
        )
    if derived_symbols:
        lines.append(
            "- Derived interface symbols (not independent sweep parameters): "
            + ", ".join(sorted(derived_symbols))
        )
        lines.append(
            "- Derived-symbol requirement: compute these symbols from the active benchmark parameters for every "
            "configuration; do not expose them as unrelated output ports or freeze them to a default-width constant."
        )
    if adapter_observable_metadata:
        lines.append(
            "- Adapter-observable derived metadata exception: `ENCODED_DATA` is not an independent sweep "
            "parameter or functional data output, but the hamming_rx harness reads it as a DUT metadata handle. "
            "Preserve the scaffold's 32-bit metadata leaf and drive it with "
            "`DATA_WIDTH + PARITY_BIT + 1` for every parameter combination."
        )

    inputs = [p for p in expected_ports if p[0] == "input"]
    outputs = [p for p in expected_ports if p[0] == "output"]
    reset_names = [name for _, _, name in inputs if _port_kind(name) == "reset"]
    reset_polarities = _infer_cvdp_reset_polarities(harness_files, reset_names) if metadata.get("dataset") == "cvdp" else {}
    if inputs:
        lines.append("- Expected inputs: " + "; ".join(_format_port(*p, reset_polarities=reset_polarities) for p in inputs))
    if outputs:
        lines.append("- Expected outputs: " + "; ".join(_format_port(*p, reset_polarities=reset_polarities) for p in outputs))
    if len(outputs) > 1:
        out_names = ", ".join(name for _, _, name in outputs)
        lines.append(
            "- Output packaging: benchmark expects separate named outputs "
            f"({out_names}); do not replace them with an unrelated packed `out` port unless the wrapper maps them."
        )
        lines.append(
            "- If you return a single packed output internally, build it as an explicit MSB-to-LSB concat of the benchmark outputs "
            f"in named form, e.g. `{{{out_names}}}` with the exact intended order and widths visible in generated SV."
        )
    clocks = [name for _, _, name in inputs if _port_kind(name) == "clock"]
    resets = reset_names
    if clocks or resets:
        lines.append(
            "- Clock/reset names: "
            + ", ".join([*(f"clock={c}" for c in clocks), *(f"reset={r} ({reset_polarities.get(r) or _reset_polarity(r)})" for r in resets)])
        )
    return "\n".join(lines)


def benchmark_expected_port_names(info: ProblemInfo | None) -> tuple[set[str], set[str]]:
    if info is None:
        return set(), set()
    ports = _benchmark_expected_ports(info)
    return (
        {name for direction, _, name in ports if direction == "input"},
        {name for direction, _, name in ports if direction == "output"},
    )


_CVDP_SCAFFOLD_DERIVED_WIDTHS = {
    "COUNT_WIDTH": "Sparkle.IR.Type.DimExpr.clog2Nat (BIT_WIDTH + 1)",
    "ENCODED_DATA": "DATA_WIDTH + PARITY_BIT + 1",
    "ENCODED_DATA_BIT": "DATA_WIDTH + PARITY_BIT + 1",
    "BIT_WIDTH": "Sparkle.IR.Type.DimExpr.clog2Nat DICE_MAX + 1",
}


_CVDP_SCAFFOLD_DEFAULTS = {
    "ADDR_WIDTH": 3,
    "BIT_WIDTH": 3,
    "C_S_AXI_ADDR_WIDTH": 8,
    "C_S_AXI_DATA_WIDTH": 32,
    "DATA_WIDTH": 8,
    "DICE_MAX": 6,
    "FILO_DEPTH": 16,
    "NUM_DICE": 2,
    "PARITY_BIT": 3,
    "TOTAL_SPACES": 12,
    "WIDTH": 8,
}
_CVDP_SCAFFOLD_DESIGN_DEFAULTS = {
    "Bit_Difference_Counter": {"BIT_WIDTH": 3},
    "nbit_swizzling": {"DATA_WIDTH": 64},
    "sync_lifo": {"ADDR_WIDTH": 3, "DATA_WIDTH": 8},
    "gf_mac": {"WIDTH": 32},
    "car_parking_system": {"TOTAL_SPACES": 12},
    "hamming_rx": {"DATA_WIDTH": 4, "PARITY_BIT": 3},
    "square_root_seq": {"WIDTH": 16},
    "FILO_RTL": {"DATA_WIDTH": 8, "FILO_DEPTH": 16},
    "digital_dice_roller": {"DICE_MAX": 6, "NUM_DICE": 2},
    "restoring_division": {"WIDTH": 6},
    "hamming_tx": {"DATA_WIDTH": 4, "PARITY_BIT": 3},
    "precision_counter_axi": {"C_S_AXI_ADDR_WIDTH": 8, "C_S_AXI_DATA_WIDTH": 32},
}


def _cvdp_scaffold_parameters(
    info: ProblemInfo,
) -> tuple[
    list[str],
    dict[str, str],
    dict[str, list[str]],
    list[dict[str, str]],
]:
    """Return authoritative CVDP parameters, widths, and parameter sweeps."""

    metadata = info.metadata or {}
    harness_files = metadata.get("harness_files", {}) or {}
    prompt_interface = _parse_prompt_interface(info.prompt_text or "")
    raw_usage = _parse_cvdp_harness_usage(harness_files)
    harness_parameters = set(raw_usage.get("params", set()))
    reference_parameters = {
        parameter.name
        for parameter in _module_parameters(info.ref_code or "", info.design_name)
    }
    reference_derived = _cvdp_derived_reference_parameter_names(
        info.ref_code or "", info.design_name
    )
    derived_names = (
        prompt_interface.derived_parameters | reference_derived
    ) - harness_parameters
    parameters = sorted(
        (
            harness_parameters
            | reference_parameters
            | (prompt_interface.parameters - prompt_interface.derived_parameters)
        )
        - derived_names
    )
    sweep_values, sweep_combinations = _cvdp_parse_parameter_sweeps(
        harness_files, set(parameters)
    )
    derived_widths = {
        name: expression
        for name, expression in _CVDP_SCAFFOLD_DERIVED_WIDTHS.items()
        if name in derived_names
    }
    return parameters, derived_widths, sweep_values, sweep_combinations


def _cvdp_scaffold_default(
    name: str,
    design_name: str,
    sweep_values: dict[str, list[str]],
) -> int:
    preferred = _CVDP_SCAFFOLD_DESIGN_DEFAULTS.get(
        design_name, {}
    ).get(name, _CVDP_SCAFFOLD_DEFAULTS.get(name))
    numeric_values = [
        int(value)
        for value in sweep_values.get(name, [])
        if re.fullmatch(r"[0-9]+", str(value).strip())
        and int(value) > 0
    ]
    if preferred is not None and (not numeric_values or preferred in numeric_values):
        return preferred
    if numeric_values:
        return min(numeric_values)
    return preferred or 1


def _cvdp_scaffold_defaults(
    parameters: list[str],
    design_name: str,
    sweep_values: dict[str, list[str]],
    sweep_combinations: list[dict[str, str]],
) -> dict[str, int]:
    """Choose defaults without inventing an untested cross-parameter tuple."""

    defaults = {
        name: _cvdp_scaffold_default(name, design_name, sweep_values)
        for name in parameters
    }
    complete_combinations: list[dict[str, int]] = []
    for combination in sweep_combinations:
        parsed: dict[str, int] = {}
        for name in parameters:
            value = str(combination.get(name, "")).strip()
            if not re.fullmatch(r"[0-9]+", value) or int(value) <= 0:
                break
            parsed[name] = int(value)
        else:
            complete_combinations.append(parsed)

    if complete_combinations and defaults not in complete_combinations:
        return complete_combinations[0]
    return defaults


_CVDP_IDIOM_MEMORY_TERMS = (
    "memory", "memories", "ram", "fifo", "lifo", "stack", "stacks",
    "queue", "queues", "buffer", "buffers", "storage", "register file",
    "register files",
)
_CVDP_IDIOM_BIT_NETWORK_TERMS = (
    "bit", "bits", "xor", "parity", "encode", "decode", "mask", "shift",
    "swizzle", "multiply", "hamming", "popcount", "reduction",
)
_CVDP_IDIOM_WIDTH_TERMS = (
    "slice", "slices", "concat", "concatenate", "concatenation", "extend",
    "extension", "zero-extend", "truncate", "truncation", "packed",
    "bit position", "bit positions", "variable width", "parameterized width",
)


def _cvdp_idiom_text_has_any(text: str, terms: tuple[str, ...]) -> bool:
    """Match complete public-spec terms, never substrings such as ram in parameter."""

    return any(
        re.search(
            rf"(?<![a-z0-9_]){re.escape(term)}(?![a-z0-9_])",
            text,
        ) is not None
        for term in terms
    )


def build_cvdp_idiom_query(info: ProblemInfo | None) -> CVDPIdiomQuery:
    """Derive retrieval features from the already-resolved public contract."""

    if info is None or (info.metadata or {}).get("dataset") != "cvdp":
        return CVDPIdiomQuery()
    ports = _benchmark_expected_ports(info)
    parameters, derived_widths, _, _ = _cvdp_scaffold_parameters(info)
    inputs = [port for port in ports if port[0] == "input"]
    outputs = [port for port in ports if port[0] == "output"]
    text = " ".join((info.prompt_text or "").lower().split())
    has_clock = any(_port_kind(name) == "clock" for _, _, name in inputs)
    reset_names = [
        name for _, _, name in inputs if _port_kind(name) == "reset"
    ]
    harness_files = (info.metadata or {}).get("harness_files", {}) or {}
    inferred_polarities = _infer_cvdp_reset_polarities(
        harness_files, reset_names
    )
    reset_polarities = {
        inferred_polarities.get(name, _reset_polarity(name))
        for name in reset_names
    }
    if not reset_polarities:
        reset_polarity = "none"
    elif len(reset_polarities) == 1:
        reset_polarity = next(iter(reset_polarities))
    else:
        reset_polarity = "mixed"
    return CVDPIdiomQuery(
        has_parameters=bool(parameters),
        has_derived_widths=bool(derived_widths),
        is_sequential=has_clock,
        has_reset=bool(reset_names),
        reset_polarity=reset_polarity,
        has_multiple_outputs=len(outputs) > 1,
        uses_memory=_cvdp_idiom_text_has_any(text, _CVDP_IDIOM_MEMORY_TERMS),
        uses_bit_network=_cvdp_idiom_text_has_any(
            text, _CVDP_IDIOM_BIT_NETWORK_TERMS
        ),
        uses_width_transform=bool(derived_widths) or _cvdp_idiom_text_has_any(
            text, _CVDP_IDIOM_WIDTH_TERMS
        ),
    )


def cvdp_verified_idiom_cards(
    info: ProblemInfo | None,
    *,
    feedback: str = "",
    repair: bool = False,
):
    return select_cvdp_idioms(
        build_cvdp_idiom_query(info),
        feedback=feedback,
        max_cards=1 if repair else 3,
        max_chars=1400 if repair else 3800,
    )


def cvdp_verified_idiom_ids(
    info: ProblemInfo | None,
    *,
    feedback: str = "",
    repair: bool = False,
) -> tuple[str, ...]:
    return tuple(
        card.idiom_id
        for card in cvdp_verified_idiom_cards(
            info, feedback=feedback, repair=repair
        )
    )


def format_cvdp_verified_idioms(
    info: ProblemInfo | None,
    *,
    feedback: str = "",
    repair: bool = False,
) -> str:
    return render_cvdp_idiom_context(
        cvdp_verified_idiom_cards(info, feedback=feedback, repair=repair),
        repair=repair,
    )


def cvdp_verified_idiom_catalog_sha256() -> str:
    return idiom_catalog_sha256()


def _cvdp_scaffold_lean_expr(
    expression: str,
    derived_widths: dict[str, str],
) -> str:
    """Translate the small public CVDP packed-width expression subset to Lean."""

    text = re.sub(r"\s+", " ", str(expression or "").strip())
    text = re.sub(
        r"\$?clog2\s*\(\s*([^()]+?)\s*\)",
        r"Sparkle.IR.Type.DimExpr.clog2Nat (\1)",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\blog2\s*\(\s*([^()]+?)\s*\)",
        r"Nat.max 1 (Sparkle.IR.Type.DimExpr.clog2Nat \1)",
        text,
        flags=re.IGNORECASE,
    )
    for _ in range(len(derived_widths) + 1):
        old = text
        for name, replacement in derived_widths.items():
            text = re.sub(
                rf"\b{re.escape(name)}\b", f"({replacement})", text
            )
        if text == old:
            break
    return text


def _cvdp_scaffold_port_width(
    sv_type: str,
    derived_widths: dict[str, str],
) -> str:
    packed = re.search(r"\[\s*(.+?)\s*:\s*(.+?)\s*\]", str(sv_type or ""))
    if packed is None:
        return "1"
    msb = packed.group(1).strip()
    lsb = packed.group(2).strip()
    if lsb == "0":
        minus_one = re.fullmatch(r"(.+?)\s*-\s*1", msb)
        width = minus_one.group(1).strip() if minus_one else f"({msb}) + 1"
    else:
        width = f"({msb}) - ({lsb}) + 1"
    return _cvdp_scaffold_lean_expr(width, derived_widths)


def build_cvdp_typed_scaffold(info: ProblemInfo | None) -> str:
    """Build a compile-safe, behavior-neutral Sparkle starter for CVDP.

    Only public interface facts are fixed: module/parameter names, Signal
    binders, packed widths, output order, clock/reset roles, and the synthesis
    command.  The model must replace the explicitly marked zero logic.
    """

    if info is None or (info.metadata or {}).get("dataset") != "cvdp":
        return ""
    ports = _benchmark_expected_ports(info)
    if not ports or not info.design_name:
        return ""
    (
        parameters,
        derived_widths,
        sweep_values,
        sweep_combinations,
    ) = _cvdp_scaffold_parameters(info)
    inputs = [port for port in ports if port[0] == "input"]
    outputs = [port for port in ports if port[0] == "output"]
    raw_usage = _parse_cvdp_harness_usage(
        (info.metadata or {}).get("harness_files", {}) or {}
    )
    adapter_metadata_outputs: set[str] = set()
    if (
        info.design_name == "hamming_rx"
        and "ENCODED_DATA" in raw_usage.get("outputs", set())
        and all(name != "ENCODED_DATA" for _, _, name in outputs)
    ):
        outputs.insert(0, ("output", "logic [31:0]", "ENCODED_DATA"))
        adapter_metadata_outputs.add("ENCODED_DATA")
    if not outputs:
        return ""

    function_name = lean_identifier(info.design_name)
    lines = [
        "import Sparkle",
        "import Sparkle.Compiler.Elab",
        "",
        "open Sparkle.Core.Domain",
        "open Sparkle.Core.Signal",
        "open Sparkle.Library.RTL",
        "",
        "/-- Compile-safe public-interface scaffold. Replace every TODO zero with real logic. -/",
        "-- CKTARCHON_IMPLEMENTATION_REQUIRED: remove only after every named output is implemented.",
        f"def {function_name} {{dom : DomainConfig}}",
    ]
    if parameters:
        lines.append("    {" + " ".join(parameters) + " : Nat}")
    omitted_clocks = [
        name for _, _, name in inputs if _port_kind(name) == "clock"
    ]
    input_widths: list[tuple[str, str, str]] = []
    for _, sv_type, name in inputs:
        if _port_kind(name) == "clock":
            continue
        width = _cvdp_scaffold_port_width(sv_type, derived_widths)
        input_widths.append((sv_type, name, width))
        lean_type = "Bool" if width == "1" else f"BitVec ({width})"
        lines.append(f"    ({name} : Signal dom ({lean_type}))")

    output_widths = [
        (name, _cvdp_scaffold_port_width(sv_type, derived_widths))
        for _, sv_type, name in outputs
    ]
    total_width = " + ".join(f"({width})" for _, width in output_widths)
    lines.append(f"    : Signal dom (BitVec ({total_width})) :=")
    if omitted_clocks:
        lines.append(
            "  -- Public clock " + ", ".join(omitted_clocks)
            + " is wrapper-owned through Sparkle's implicit domain clk."
        )
    if not input_widths:
        return ""
    _, first_input, first_width = input_widths[0]
    condition_name = "scaffold_condition"
    if first_width == "1":
        lines.append(
            f"  let {condition_name} : Signal dom Bool := {first_input}"
        )
    else:
        lines.append(
            f"  let {condition_name} : Signal dom Bool := isZero {first_input}"
        )
    port_type_text = " ".join(width for _, _, width in input_widths) + " " + " ".join(width for _, width in output_widths)
    unused_parameters = [
        parameter for parameter in parameters
        if not re.search(rf"\b{re.escape(parameter)}\b", port_type_text)
    ]
    for index, parameter in enumerate(unused_parameters):
        witness = f"scaffold_{parameter.lower()}_state"
        next_condition = f"scaffold_condition_{index + 1}"
        lines.extend([
            f"  -- TODO scaffold-only live witness for benchmark parameter {parameter}; replace with real state.",
            f"  let {witness} : Signal dom (BitVec ({parameter})) :=",
            f"    Signal.loop fun q => dff (BitVec.ofNat ({parameter}) 0) q",
            f"  let {next_condition} : Signal dom Bool :=",
            f"    Signal.mux (isZero {witness}) {condition_name} {condition_name}",
        ])
        condition_name = next_condition
    if omitted_clocks and not unused_parameters:
        lines.extend([
            "  -- TODO scaffold-only clock witness; replace with the benchmark state machine.",
            "  let scaffold_clock_state : Signal dom Bool :=",
            "    Signal.loop fun q => dff false q",
            "  let scaffold_clock_condition : Signal dom Bool :=",
            f"    Signal.mux scaffold_clock_state {condition_name} {condition_name}",
        ])
        condition_name = "scaffold_clock_condition"
    for name, width in output_widths:
        if name in adapter_metadata_outputs:
            metadata_name = f"{name}_metadata"
            lines.extend([
                f"  -- Adapter-observable derived metadata; this leaf is already implemented, not a behavioral TODO.",
                f"  let {metadata_name} : Signal dom (BitVec ({width})) :=",
                f"    Signal.pure (BitVec.ofNat ({width}) (DATA_WIDTH + PARITY_BIT + 1))",
                f"  let {name} : Signal dom (BitVec ({width})) :=",
                f"    Signal.mux {condition_name} {metadata_name} {metadata_name}",
            ])
            continue
        zero_name = f"{name}_zero"
        lines.extend([
            f"  let {zero_name} : Signal dom (BitVec ({width})) :=",
            f"    Signal.pure (BitVec.ofNat ({width}) 0)",
            f"  let {name} : Signal dom (BitVec ({width})) :=",
            f"    Signal.mux {condition_name} {zero_name} {zero_name} -- TODO: implement `{name}`",
        ])
    lines.append("  " + " ++ ".join(name for name, _ in output_widths))
    lines.append("")
    selected_defaults = _cvdp_scaffold_defaults(
        parameters,
        info.design_name,
        sweep_values,
        sweep_combinations,
    )
    defaults = ", ".join(
        f"{name} := {selected_defaults[name]}" for name in parameters
    )
    if defaults:
        lines.extend([
            f"#synthesizeVerilog {function_name} parameters",
            f"  [{defaults}]",
        ])
    else:
        lines.append(f"#synthesizeVerilog {function_name}")
    return "\n".join(lines) + "\n"


def format_cvdp_typed_scaffold(info: ProblemInfo | None) -> str:
    source = build_cvdp_typed_scaffold(info)
    if not source:
        return ""
    return (
        "### Deterministic Typed Sparkle Scaffold\n\n"
        "This scaffold was generated only from the public interface and is already "
        "compile-checked by the local harness. Preserve its parameters, binders, "
        "return width, named MSB-to-LSB packing, and synthesis command. Replace "
        "the TODO zeros with real logic; do not restart from a guessed signature.\n\n"
        f"```lean\n{source}```"
    )


def build_user_message(
    prob_id: str,
    has_repl: bool = False,
    info: ProblemInfo | None = None,
    dataset_name: str = "verilogeval",
    condition_sv: str | None = None,
    include_cvdp_scaffold: bool = False,
    include_cvdp_verified_idioms: bool = False,
) -> str:
    """Build the user message for the agent, including NL description and reference Verilog."""
    if info is not None:
        nl_desc = info.prompt_text
        ref_sv = info.ref_code
        design_name = info.design_name
    else:
        prompt_file = DATASET_DIR / f"{prob_id}_prompt.txt"
        ref_file = DATASET_DIR / f"{prob_id}_ref.sv"
        nl_desc = prompt_file.read_text() if prompt_file.exists() else "(no description available)"
        ref_sv = ref_file.read_text() if ref_file.exists() else "(no reference Verilog available)"
        design_name = "TopModule"

    # VerilogEval uses TopModule via wrapper; other datasets instantiate the named DUT.
    if dataset_name == "verilogeval":
        func_name = prob_id.lower()
    else:
        func_name = lean_identifier(design_name)

    if has_repl:
        compile_instructions = (
            f"3. Use the `lean_check` tool to verify your code instantly (~0.1s)\n"
            f"   - Pass your COMPLETE Lean 4 code (WITHOUT import/open lines), including `#synthesizeVerilog {func_name}`, to `lean_check`\n"
            f"   - Treat the check as successful only when it returns generated Verilog; the harness automatically saves that compile-safe candidate\n"
            f"   - This is much faster than `lake build` — use it for every iteration\n"
            f"4. Fix any errors iteratively until it compiles and generates correct Verilog\n"
            f"5. Check the generated Verilog looks correct, then stop"
        )
    else:
        compile_instructions = (
            f"3. Run `lake build Generated.{prob_id}` to compile\n"
            f"4. Fix any errors iteratively until it compiles and generates correct Verilog\n"
            f"5. Check the generated Verilog looks correct"
        )

    if condition_sv is not None:
        ref_section = (
            "### Conditioning SystemVerilog Implementation\n\n"
            "The SystemVerilog below was produced by an enhanced direct-SV baseline and "
            "passed the benchmark RTL simulation. Use it as an implementation-level "
            "behavioral guide for this diagnostic run. Translate its behavior into "
            "Sparkle HDL / Lean; do not edit or output SystemVerilog directly.\n\n"
            f"```systemverilog\n{condition_sv.strip()}\n```\n\n"
        )
    elif ref_sv.strip().startswith("(no public reference"):
        ref_section = (
            "### Reference Verilog\n\n"
            "No public reference Verilog is available for this problem. Use the specification and "
            "the target module interface described there.\n\n"
        )
    else:
        ref_section = (
            "### Reference Verilog (for understanding, NOT for copying)\n\n"
            f"```systemverilog\n{ref_sv}\n```\n\n"
        )

    dataset_note = ""
    if dataset_name in ("resbench", "cvdp", "realbench"):
        example_guidance = (
            "- Use only the retrieved verified idiom bodies supplied below; migrate expression shapes into the authoritative scaffold, and do not inspect unrelated Benchmark or Generated candidates.\n"
            if include_cvdp_verified_idioms else
            "- Read `Benchmark/RTLIdioms.lean` if the task has memory, packed fields, priority logic, or generate-like bit operations.\n"
        )
        dataset_note = (
            f"- The evaluator expects the generated SystemVerilog top module to be `{design_name}`.\n"
            f"- Use port names and widths exactly as specified by the problem statement.\n"
            f"- Preserve reset polarity and cycle latency exactly; these testbenches often check protocol timing.\n"
            f"- Prefer Sparkle.Library.RTL helpers for bit slices, reset muxes, registers, memories, and register files.\n"
            f"{example_guidance}"
        )
    interface_contract = format_benchmark_interface_contract(info)
    interface_section = (
        f"### Benchmark Interface Contract\n\n{interface_contract}\n\n"
        if interface_contract else ""
    )
    scaffold_section = (
        format_cvdp_typed_scaffold(info) + "\n\n"
        if include_cvdp_scaffold else ""
    )
    idiom_section = (
        format_cvdp_verified_idioms(info) + "\n\n"
        if include_cvdp_verified_idioms else ""
    )
    if scaffold_section:
        guardrail_note = (
            "The target file is already initialized with the typed scaffold above. Its current "
            "contents are authoritative: preserve the typed shell and implement every TODO. "
            "Remove `CKTARCHON_IMPLEMENTATION_REQUIRED` only after the completed design passes "
            "the compile check and produces Verilog. A marked scaffold compiling is not completion.\n\n"
        )
        implementation_step = (
            "2. Implement the specification in that file. Use only the retrieved verified idiom bodies below when a matching Sparkle expression shape is needed; never copy their outer definition shell\n"
            if include_cvdp_verified_idioms else
            "2. Implement the specification in that file. Consult at most one or two relevant Benchmark examples only if a specific Sparkle idiom is needed\n"
        )
        workflow_instructions = (
            f"1. Read `Generated/{prob_id}.lean` first; do not replace its typed shell with a generic from-scratch template\n"
            f"{implementation_step}"
            f"{compile_instructions}\n\n"
        )
        structure_section = ""
    else:
        guardrail_note = ""
        workflow_instructions = (
            f"1. Start by reading a few Benchmark/*.lean examples to see working patterns\n"
            f"2. Write your solution to `Generated/{prob_id}.lean`\n"
            f"{compile_instructions}\n\n"
        )
        structure_section = (
            f"The file must follow this exact structure:\n"
            f"```lean\n"
            f"import Sparkle\n"
            f"import Sparkle.Compiler.Elab\n\n"
            f"open Sparkle.Core.Domain\n"
            f"open Sparkle.Core.Signal\n\n"
            f"open Sparkle.Library.RTL\n\n"
            f"/-- <description> -/\n"
            f"def {func_name} {{dom : DomainConfig}}\n"
            f"    (<inputs>) : <output_type> :=\n"
            f"  <implementation>\n\n"
            f"#synthesizeVerilog {func_name}\n"
            f"```\n"
        )
    return (
        f"## Problem: {prob_id}\n\n"
        f"### Target Module\n\n`{design_name}`\n\n"
        f"### Natural Language Description\n\n{nl_desc}\n\n"
        f"{interface_section}"
        f"{scaffold_section}"
        f"{idiom_section}"
        f"{ref_section}"
        f"### Your Task\n\n"
        f"Write a Sparkle HDL (Lean 4) implementation for this problem.\n\n"
        f"{guardrail_note}"
        f"{workflow_instructions}"
        f"{dataset_note}"
        f"{structure_section}"
    )


def load_conditioning_sv(condition_sv_dir: str | None, prob_id: str) -> tuple[str | None, str | None]:
    """Load an optional per-problem SystemVerilog implementation for diagnostic conditioning."""
    if not condition_sv_dir:
        return None, None
    base = Path(condition_sv_dir)
    candidates = [
        base / f"{prob_id}.sv",
        base / "sv" / f"{prob_id}.sv",
    ]
    for path in candidates:
        if path.exists():
            return path.read_text(errors="replace"), str(path)
    raise FileNotFoundError(
        f"--condition-sv-dir was set, but no SV file was found for {prob_id}; "
        f"tried: {', '.join(str(p) for p in candidates)}"
    )


def log_event(run_dir: Path, event: dict) -> None:
    """Append an event to results.jsonl (thread-safe)."""
    event["timestamp"] = datetime.now().isoformat()
    with _log_lock:
        with open(run_dir / "results.jsonl", "a") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")


# ── PPA optimization helpers ────────────────────────────────────


def extract_ppa(result: dict) -> dict:
    """Extract PPA metrics from an evaluator result."""
    return {
        "area_um2": result.get("area_um2"),
        "cell_count": result.get("cell_count"),
        "wns_ns": result.get("wns_ns"),
        "power_uw": result.get("power_uw"),
    }


def classify_failure_record(record: dict) -> dict[str, str]:
    """Classify a result/event into a failure category and high-level family."""
    if record.get("agent_error"):
        return {
            "failure_stage": "generation",
            "failure_category": "agent_error",
            "failure_family": "other",
        }

    if record.get("terminal_capability_error"):
        return {
            "failure_stage": "capability",
            "failure_category": "unsupported_parameterization",
            "failure_family": "unsupported",
        }

    detail = str(record.get("detail", "") or record.get("error_msg", "")).lower()
    if (
        record.get("unsupported_parameterization")
        or CVDP_PARAMETERIZATION_UNSUPPORTED.lower() in detail
    ):
        return {
            "failure_stage": "simulation",
            "failure_category": "parameterization_contract",
            "failure_family": "interface",
        }

    if record.get("adapter_error") or CVDP_ADAPTER_ERROR.lower() in detail:
        return {
            "failure_stage": "simulation",
            "failure_category": "adapter_contract_error",
            "failure_family": "interface",
        }
    compile_pass = bool(record.get("compile_pass", False))
    sim_status = record.get("sim_status", "not_run")

    if compile_pass and sim_status == "sim_pass":
        return {
            "failure_stage": "success",
            "failure_category": "passed",
            "failure_family": "passed",
        }

    if not compile_pass:
        if "could not extract systemverilog" in detail:
            category = "sv_extract_error"
        elif "lean file not found" in detail:
            category = "missing_source"
        else:
            category = "source_compile_error"
        return {
            "failure_stage": "compile",
            "failure_category": category,
            "failure_family": "syntax_or_compile",
        }

    if sim_status == "sim_fail":
        return {
            "failure_stage": "simulation",
            "failure_category": "functional_mismatch",
            "failure_family": "functional",
        }

    if sim_status == "sim_error":
        rtl_build_error = (
            "iverilog compile failed" in detail
            or "during build/elaboration" in detail
            or "error(s) during elaboration" in detail
            or "unable to bind parameter" in detail
            or "dimensions must be constant" in detail
            or ("calledprocesserror" in detail and "iverilog" in detail)
        )
        if (
            rtl_build_error
            or "compile failed" in detail
            or "could not generate" in detail
            or "could not parse" in detail
        ):
            category = "rtl_compile_or_interface_error"
            family = "syntax_or_compile"
        elif "timeout" in detail:
            category = "simulation_timeout"
            family = "other"
        elif "not found" in detail:
            category = "environment_error"
            family = "other"
        else:
            category = "simulation_error"
            family = "other"
        return {
            "failure_stage": "simulation",
            "failure_category": category,
            "failure_family": family,
        }

    return {
        "failure_stage": "other",
        "failure_category": "unknown_failure",
        "failure_family": "other",
    }


def summarize_failure_breakdown(run_dir: Path) -> dict:
    """Aggregate structured failure counts/ratios from results.jsonl."""
    results_path = run_dir / "results.jsonl"
    records = []
    if results_path.exists():
        for line in results_path.read_text().splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            # Keep only final per-problem rows and early agent errors.
            if row.get("phase") == "synth":
                continue
            if "ppa_iteration" in row or "arch_iteration" in row:
                continue
            if "compile_pass" in row or row.get("agent_error"):
                records.append(row)

    category_counts: dict[str, int] = {}
    family_counts: dict[str, int] = {}
    stage_counts: dict[str, int] = {}
    total_failures = 0
    for row in records:
        cls = classify_failure_record(row)
        if cls["failure_category"] == "passed":
            continue
        total_failures += 1
        category_counts[cls["failure_category"]] = category_counts.get(cls["failure_category"], 0) + 1
        family_counts[cls["failure_family"]] = family_counts.get(cls["failure_family"], 0) + 1
        stage_counts[cls["failure_stage"]] = stage_counts.get(cls["failure_stage"], 0) + 1

    def _ratios(counts: dict[str, int]) -> dict[str, str]:
        return {
            key: f"{value / total_failures * 100:.1f}%"
            for key, value in counts.items()
        } if total_failures > 0 else {}

    return {
        "total_failures": total_failures,
        "failure_breakdown": category_counts,
        "failure_breakdown_pct_of_failures": _ratios(category_counts),
        "failure_family_breakdown": family_counts,
        "failure_family_pct_of_failures": _ratios(family_counts),
        "failure_stage_breakdown": stage_counts,
        "failure_stage_pct_of_failures": _ratios(stage_counts),
    }


def _tail_text(text: str, max_chars: int = 4000) -> str:
    """Return the tail of a long diagnostic string."""
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


def _read_tail(path: Path, max_chars: int = 4000) -> str:
    try:
        text = path.read_text(errors="replace")
    except Exception:
        return ""
    return _tail_text(text, max_chars=max_chars)


COMPACT_SPEC_CHARS = 5000
COMPACT_REF_CHARS = 7000
COMPACT_CONTEXT_CHARS = 5000
COMPACT_CODE_CHARS = 18000
COMPACT_FEEDBACK_CHARS = 8000
COMPACT_ASSERTION_CHARS = 3600
COMPACT_DIAGNOSTIC_CHARS = 2800
COMPACT_INTERFACE_CHARS = 1200
COMPACT_ATTEMPT_CHARS = 1600
COMPACT_RECENT_ATTEMPTS = 3
SIM_DIAGNOSTIC_CHARS = 3500
SIM_FEEDBACK_MAX_NON_IMPROVING = 2


def truncate_text(text: str | None, limit: int, *, keep: str = "head") -> str:
    """Bound prompt text while preserving either the head, tail, or both ends."""
    if text is None:
        return ""
    text = str(text)
    if len(text) <= limit:
        return text
    omitted = len(text) - limit
    marker = f"\n\n... [truncated {omitted} chars] ...\n\n"
    if limit <= len(marker) + 20:
        return text[:limit]
    if keep == "tail":
        return marker + text[-(limit - len(marker)):]
    if keep == "middle":
        budget = limit - len(marker)
        head = budget // 2
        tail = budget - head
        return text[:head] + marker + text[-tail:]
    return text[:limit - len(marker)] + marker


def clean_diagnostic_text(text: str | None) -> str:
    """Remove terminal noise while preserving simulator diagnostics."""
    if not text:
        return ""
    cleaned = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", str(text))
    cleaned = cleaned.replace("\r", "\n")
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _looks_like_pytest_source(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return True
    return (
        stripped.startswith((">", "@pytest", "def ", "runner.build(", "runner.test("))
        or stripped in {"(", ")", "{" , "}"}
        or re.match(r"^(sources|hdl_toplevel|always|clean|verbose|timescale|log_file|waves)\s*=", stripped)
        is not None
    )


def extract_sim_diagnostics(detail: str | None, max_chars: int = SIM_DIAGNOSTIC_CHARS) -> str:
    """Extract useful simulator feedback without adding benchmark source code."""
    text = clean_diagnostic_text(detail)
    if not text:
        return ""

    lines = [line.rstrip() for line in text.splitlines()]
    # cocotb redirects the Icarus build stream to ``sim.log``.  Pytest then
    # reports only a generic CalledProcessError, whose traceback can easily
    # consume the diagnostic budget before the actual elaboration error is
    # reached.  Select simulator-native build diagnostics in a separate pass
    # so they cannot be starved by pytest/Python traceback noise.
    icarus_re = re.compile(
        r"(CVDP_ADAPTER_ERROR|CVDP_FIXED_CORE_PARAMETERIZATION_UNSUPPORTED|"
        r"Unable to bind|Dimensions must be constant|during elaboration|"
        r"Unknown module type|Unable to find the root module|"
        r"(?:^|:\d+(?::\d+)?:)\s*(?:syntax\s+)?error:)",
        re.IGNORECASE,
    )
    icarus_lines: list[str] = []
    icarus_seen: set[str] = set()
    for line in lines:
        compact = re.sub(r"\s+", " ", line.strip())
        if not compact or compact in icarus_seen or not icarus_re.search(compact):
            continue
        icarus_seen.add(compact)
        icarus_lines.append(compact)
        if len(icarus_lines) >= 80:
            break
    if icarus_lines:
        return truncate_text("\n".join(icarus_lines), max_chars, keep="tail")

    picked: list[str] = []
    seen: set[str] = set()

    def add(line: str) -> None:
        compact = re.sub(r"\s+", " ", line.strip())
        if not compact or compact in seen:
            return
        seen.add(compact)
        picked.append(compact)

    failure_re = re.compile(
        r"(\bFAIL(?:ED)?\b|\bERROR\b|AssertionError|ValueError|RuntimeError|"
        r"Cannot convert|Logic\('X'\)|\bX\b|mismatch|expected|actual|timeout|timed out|"
        r"TESTS=|TEST STATUS)",
        re.IGNORECASE,
    )
    pytest_case_re = re.compile(r"FAILED\s+\S+::\S+", re.IGNORECASE)

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if re.match(r"^_+\s*\w+\s*_+$", stripped):
            continue
        if not failure_re.search(stripped) and not pytest_case_re.search(stripped):
            continue
        if _looks_like_pytest_source(stripped) and not re.search(
            r"(AssertionError|ValueError|RuntimeError|Cannot convert|expected|actual|FAIL|ERROR)",
            stripped,
            re.IGNORECASE,
        ):
            continue
        add(stripped)
        if len(picked) >= 80:
            break

    if picked:
        return truncate_text("\n".join(picked), max_chars, keep="tail")

    # Fallback to the tail when the simulator output has no recognizable markers.
    return truncate_text(text, max_chars, keep="tail")


def extract_sim_test_counts(detail: str | None) -> tuple[int, int, int] | None:
    """Return (tests, pass, fail) from simulator summaries when present."""
    text = clean_diagnostic_text(detail)
    if not text:
        return None
    matches = re.findall(r"TESTS=(\d+)\s+PASS=(\d+)\s+FAIL=(\d+)", text, flags=re.IGNORECASE)
    if matches:
        tests, passed, failed = matches[-1]
        return int(tests), int(passed), int(failed)

    passed = len(re.findall(r"\bPASS\b", text))
    failed = len(re.findall(r"\bFAIL(?:ED)?\b", text, flags=re.IGNORECASE))
    if passed or failed:
        return passed + failed, passed, failed
    return None


def _compact_signal_value(expr: str) -> str:
    expr = re.sub(r"\s+", " ", expr.strip())
    logic = re.search(r"Logic\(['\"]([^'\"]+)['\"]\)", expr)
    if logic:
        return logic.group(1)
    binary = re.search(r"BinaryValue\([^)]*value=['\"]([^'\"]+)['\"]", expr)
    if binary:
        return binary.group(1)
    return truncate_text(expr, 80, keep="head")


def extract_first_failure_diagnostics(detail: str | None, max_failures: int = 4, max_chars: int = 3000) -> str:
    """Extract first failing test/signal/expected-actual snippets from simulator output."""
    text = clean_diagnostic_text(detail)
    if not text:
        return ""
    lines = [line.rstrip() for line in text.splitlines()]
    failures: list[str] = []
    seen: set[str] = set()
    current_test = ""

    for idx, line in enumerate(lines):
        stripped = line.strip()
        run_m = re.search(r"\brunning\s+([A-Za-z0-9_.]+)\s*\(\d+/\d+\)", stripped)
        if run_m:
            current_test = run_m.group(1)

        is_failure_anchor = (
            "AssertionError:" in stripped
            or re.search(r"\bassert\s+.+\s+==\s+.+", stripped)
            or re.search(r"\btest_[A-Za-z0-9_\\.]+ failed\b", stripped, re.IGNORECASE)
        )
        if "dut." in stripped and "Logic(" not in stripped and "BinaryValue" not in stripped:
            is_failure_anchor = False
        if not is_failure_anchor:
            continue

        block = lines[max(0, idx - 5): min(len(lines), idx + 8)]
        block_text = "\n".join(block)
        test = current_test
        for back in reversed(lines[max(0, idx - 12): idx + 1]):
            m = re.search(r"(test_[A-Za-z0-9_]+\.test_[A-Za-z0-9_]+)", back)
            if m:
                test = m.group(1)
                break
        time_matches = re.findall(r"(\d+(?:\.\d+)?)\s*ns\b", block_text)
        time_s = f" @ {time_matches[-1]}ns" if time_matches else ""
        signals = extract_dut_signal_refs(block_text)

        assertion_msg = ""
        for item in block:
            item = item.strip()
            if "AssertionError:" in item:
                assertion_msg = item.split("AssertionError:", 1)[1].strip()
                break
        if not assertion_msg:
            for item in reversed(block):
                item = item.strip()
                if item and not item.startswith(("File ", "+ where", "Traceback")) and "assert " not in item:
                    if re.search(r"should|expected|actual|mismatch|fail", item, re.IGNORECASE):
                        assertion_msg = item
                        break

        expr = ""
        actual = expected = ""
        for item in block:
            item = item.strip()
            if "dut." in item and "Logic(" not in item and "BinaryValue" not in item:
                continue
            m = re.search(r"\bassert\s+(.+?)\s*==\s*(.+)$", item)
            if m:
                lhs = m.group(1).strip()
                rhs = re.sub(r",\s*[\"'].*$", "", m.group(2).strip())
                expr = f"assert {lhs} == {rhs}"
                actual = _compact_signal_value(lhs)
                expected = _compact_signal_value(rhs)
                break

        parts = []
        if test:
            parts.append(test)
        if time_s:
            parts.append(time_s.strip())
        if signals:
            parts.append("signals=" + ",".join(signals))
        if actual or expected:
            parts.append(f"got={actual or '?'} expected={expected or '?'}")
        if assertion_msg:
            parts.append("message=" + truncate_text(assertion_msg, 180, keep="head"))
        if expr:
            parts.append("expr=" + truncate_text(expr, 160, keep="head"))
        summary = "; ".join(parts)
        key = re.sub(r"\s+", " ", "|".join([
            ",".join(signals),
            assertion_msg,
            actual,
            expected,
        ]))
        if key and key not in seen:
            seen.add(key)
            failures.append("- " + summary)
        if len(failures) >= max_failures:
            break

    return truncate_text("\n".join(failures), max_chars, keep="tail") if failures else ""


def _failure_times_ns(detail: str | None) -> list[float]:
    text = clean_diagnostic_text(detail)
    values = []
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*ns\b", text):
        try:
            values.append(float(m.group(1)))
        except ValueError:
            pass
    return values[:3]


def _find_waveform_files(prob_id: str, run_dir: Path) -> list[Path]:
    roots = [
        run_dir / "cvdp_sim" / prob_id,
        run_dir,
    ]
    found: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        for suffix in ("*.vcd", "*.fst"):
            found.extend(sorted(root.rglob(suffix)))
    return found[:4]


def _vcd_timescale_to_ns(text: str) -> float:
    m = re.search(r"\$timescale\s+(\d+)\s*(fs|ps|ns|us|ms|s)\s+\$end", text)
    if not m:
        return 1.0
    value = float(m.group(1))
    unit = m.group(2)
    return value * {
        "fs": 1e-6,
        "ps": 1e-3,
        "ns": 1.0,
        "us": 1e3,
        "ms": 1e6,
        "s": 1e9,
    }.get(unit, 1.0)


def extract_waveform_context(prob_id: str, run_dir: Path, detail: str | None, max_chars: int = 2200) -> str:
    """Best-effort VCD context around the first failing simulator timestamp."""
    wave_files = _find_waveform_files(prob_id, run_dir)
    if not wave_files:
        return ""
    vcd_files = [p for p in wave_files if p.suffix == ".vcd"]
    if not vcd_files:
        return "Waveform artifact exists but is not text VCD: " + ", ".join(str(p.name) for p in wave_files)

    vcd_path = vcd_files[0]
    try:
        text = vcd_path.read_text(errors="replace")
    except Exception:
        return ""
    signal_names = set(extract_dut_signal_refs(detail))
    if not signal_names:
        signal_names = {"clk", "rst", "reset"}
    ids: dict[str, str] = {}
    for m in re.finditer(r"\$var\s+\w+\s+\d+\s+(\S+)\s+([A-Za-z_][A-Za-z0-9_$]*)\s+\$end", text):
        code, name = m.group(1), m.group(2)
        if name in signal_names or _port_kind(name) in {"clock", "reset"}:
            ids[code] = name
    if not ids:
        return f"VCD found ({vcd_path.name}) but no failing DUT signal names were present in the waveform header."

    times = _failure_times_ns(detail)
    target_ns = times[0] if times else None
    scale_ns = _vcd_timescale_to_ns(text)
    latest: dict[str, str] = {}
    recent: list[str] = []
    current_ns = 0.0
    for raw in text.splitlines():
        if raw.startswith("#"):
            try:
                current_ns = int(raw[1:].strip()) * scale_ns
            except ValueError:
                continue
            if target_ns is not None and current_ns > target_ns:
                break
            continue
        code = value = ""
        if raw and raw[0] in "01xXzZ":
            value, code = raw[0], raw[1:].strip()
        elif raw.startswith("b"):
            parts = raw.split()
            if len(parts) == 2:
                value, code = parts
        if code in ids:
            name = ids[code]
            latest[name] = value
            if target_ns is None or abs(current_ns - target_ns) <= 20:
                recent.append(f"{current_ns:g}ns {name}={value}")

    lines = [f"VCD: {vcd_path.name}"]
    if target_ns is not None:
        lines.append(f"First logged failure time: ~{target_ns:g}ns")
    if latest:
        lines.append("Signal values at/before failure: " + ", ".join(f"{k}={v}" for k, v in sorted(latest.items())))
    if recent:
        lines.append("Nearby changes: " + "; ".join(recent[-20:]))
    return truncate_text("\n".join(lines), max_chars, keep="tail")


def extract_sv_module_ports(sv_code: str | None) -> list[str]:
    """Extract generated SystemVerilog top-level port names from the module header."""
    if not sv_code:
        return []
    text = re.sub(r"/\*.*?\*/", "", sv_code, flags=re.S)
    text = re.sub(r"//.*", "", text)
    match = re.search(r"\bmodule\s+\w+\s*\((.*?)\)\s*;", text, flags=re.S)
    if not match:
        return []
    ports: list[str] = []
    for raw in match.group(1).split(","):
        item = raw.strip()
        if not item:
            continue
        item = re.sub(r"\[[^\]]+\]", " ", item)
        item = re.sub(
            r"\b(input|output|inout|wire|reg|logic|signed|unsigned)\b",
            " ",
            item,
        )
        item = item.split("=")[0].strip()
        name = item.split()[-1] if item.split() else ""
        name = name.strip(" ,);")
        if re.match(r"^[A-Za-z_][A-Za-z0-9_$]*$", name):
            ports.append(name)
    return ports


def extract_dut_signal_refs(detail: str | None) -> list[str]:
    """Extract DUT signal names referenced in simulator diagnostics."""
    text = clean_diagnostic_text(detail)
    if not text:
        return []
    refs = set(re.findall(r"\bdut\.([A-Za-z_][A-Za-z0-9_$]*)\b", text))
    refs.update(re.findall(r"HierarchyObject\([^)]*\)\.([A-Za-z_][A-Za-z0-9_$]*)", text))
    return sorted(refs)


def build_interface_diagnostics(result: dict, sv_code: str | None) -> str:
    ports = extract_sv_module_ports(sv_code)
    dut_refs = extract_dut_signal_refs(result.get("detail"))
    if not ports and not dut_refs:
        return ""

    lines = []
    if ports:
        lines.append(f"- Generated SV module ports: {', '.join(ports)}")
    if dut_refs:
        lines.append(f"- Simulator diagnostics reference DUT signals: {', '.join(dut_refs)}")
    if ports and dut_refs:
        missing = [name for name in dut_refs if name not in ports]
        if missing:
            lines.append(
                "- Potential interface mismatch: simulator references these DUT signals "
                f"but they are not generated SV ports/signals: {', '.join(missing)}"
            )
            if "out" in ports:
                lines.append(
                    "- Generated SV has a packed `out` port; check whether the benchmark expects "
                    "separate named outputs instead of a packed aggregate."
                )
    return "\n".join(lines)


def build_expected_vs_generated_port_diagnostics(info: ProblemInfo | None, sv_code: str | None) -> str:
    generated = set(extract_sv_module_ports(sv_code))
    expected_inputs, expected_outputs = benchmark_expected_port_names(info)
    expected = expected_inputs | expected_outputs
    if not generated or not expected:
        return ""
    missing = sorted(expected - generated)
    extra = sorted(generated - expected)
    lines = []
    if missing:
        lines.append(
            "- Expected benchmark ports missing from generated SV top module: "
            + ", ".join(missing)
        )
    if extra:
        lines.append(
            "- Generated SV top module has extra/non-benchmark ports: "
            + ", ".join(extra[:20])
        )
    if missing and "out" in generated and len(expected_outputs) > 1:
        lines.append(
            "- Packed-output suspicion: benchmark expects separate outputs "
            f"({', '.join(sorted(expected_outputs))}), but generated SV exposes `out`."
        )
    return "\n".join(lines)


def read_current_lean(prob_id: str) -> str:
    lean_file = GENERATED_DIR / f"{prob_id}.lean"
    if not lean_file.exists():
        return f"(current Lean file missing: Generated/{prob_id}.lean)"
    return lean_file.read_text(errors="replace")


def read_current_sv(prob_id: str, run_dir: Path) -> str:
    sv_file = run_dir / "sv" / f"{prob_id}.sv"
    if not sv_file.exists():
        return ""
    return sv_file.read_text(errors="replace")


def read_simulator_output(prob_id: str, run_dir: Path) -> str:
    sim_root = run_dir / "cvdp_sim" / prob_id
    candidates = [
        ("Icarus build log", sim_root / "rundir" / "sim.log"),
        ("CVDP pytest/cocotb output", sim_root / "cvdp_local_output.txt"),
    ]
    chunks = []
    for label, path in candidates:
        if path.exists():
            content = path.read_text(errors="replace").strip()
            if content:
                chunks.append(f"[{label}]\n{content}")
    return "\n\n".join(chunks)


def bool_status(value) -> str:
    if value is True:
        return "pass"
    if value is False:
        return "fail"
    return "not_run"


def summarize_eval_result(result: dict | None) -> str:
    if not result:
        return "No evaluator result available."
    lines = [
        f"- Lean compile: {bool_status(result.get('compile_pass'))}",
        f"- SV extracted: {bool_status(result.get('sv_extracted'))}",
        f"- SV lint: {bool_status(result.get('lint_pass'))}",
        f"- RTL sim: {result.get('sim_status', 'unknown')}",
    ]
    detail = result.get("detail")
    if detail:
        sim_status = result.get("sim_status")
        sim_diag = extract_sim_diagnostics(detail, 1100) if sim_status in {"sim_fail", "sim_error"} else ""
        if sim_diag:
            lines.append(f"- Key simulator diagnostics: {sim_diag}")
        else:
            lines.append(f"- Detail: {truncate_text(clean_diagnostic_text(detail), 900, keep='tail')}")
        counts = extract_sim_test_counts(detail)
        if counts:
            tests, passed, failed = counts
            lines.append(f"- RTL sim tests: {passed}/{tests} pass, {failed} fail")
    ppa = extract_ppa(result)
    if any(v is not None for v in ppa.values()):
        metrics = []
        if ppa["area_um2"] is not None:
            metrics.append(f"area={ppa['area_um2']:.2f}")
        if ppa["cell_count"] is not None:
            metrics.append(f"cells={ppa['cell_count']}")
        if ppa["wns_ns"] is not None:
            metrics.append(f"wns={ppa['wns_ns']:.3f}")
        if ppa["power_uw"] is not None:
            metrics.append(f"power={ppa['power_uw']:.4f}")
        lines.append(f"- PPA: {', '.join(metrics)}")
    if result.get("synth_pass") is not None:
        lines.append(f"- Synthesis: {bool_status(result.get('synth_pass'))}")
    return "\n".join(lines)


def summarize_recent_attempts(attempts: list[dict]) -> str:
    if not attempts:
        return "None yet in this repair phase."
    lines = []
    for attempt in attempts[-COMPACT_RECENT_ATTEMPTS:]:
        lines.append(f"### Attempt {attempt.get('iteration', '?')} ({attempt.get('phase', 'repair')})")
        note = attempt.get("note")
        if note:
            lines.append(truncate_text(note, COMPACT_ATTEMPT_CHARS, keep="tail"))
        result_summary = attempt.get("result_summary")
        if result_summary:
            lines.append(result_summary)
        lines.append("")
    return "\n".join(lines).strip()


def format_context_files(info: ProblemInfo | None) -> str:
    if info is None:
        return "No extra context files."
    context_files = info.metadata.get("input_context_files", {})
    if not isinstance(context_files, dict) or not context_files:
        return "No extra context files."
    blocks = []
    budget = COMPACT_CONTEXT_CHARS
    for name, content in sorted(context_files.items(), key=lambda item: str(item[0])):
        if budget <= 0:
            break
        snippet_limit = min(2000, budget)
        snippet = truncate_text(str(content), snippet_limit, keep="head")
        blocks.append(f"### {name}\n```systemverilog\n{snippet}\n```")
        budget -= len(snippet)
    return "\n\n".join(blocks) if blocks else "No extra context files."


def compact_repair_feedback(feedback: str) -> str:
    """Keep semantic evidence ahead of verbose generated-SystemVerilog text.

    ``build_sim_feedback`` may include a large synthesized SV dump after the
    simulator assertions. A tail-only truncation silently dropped the first
    DUT/expected/actual evidence in compact Lean repair sessions. Preserve the
    structured failure sections explicitly and omit the full SV dump: the agent
    can reproduce it with ``lean_check`` after changing the Lean source.
    """
    text = str(feedback or "").strip()
    if not text:
        return "No simulator feedback was captured. Re-check the contract and current Lean behavior."

    heading_re = re.compile(r"(?m)^### ([^\n]+)\n")
    matches = list(heading_re.finditer(text))
    blocks: dict[str, str] = {}
    preamble_end = matches[0].start() if matches else len(text)
    preamble = text[:preamble_end].strip()
    for idx, match in enumerate(matches):
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        blocks[match.group(1).strip()] = text[match.start():end].strip()

    selected: list[str] = []
    if preamble:
        selected.append(truncate_text(preamble, 900, keep="head"))
    for heading, limit in (
        ("First Failing Assertions", COMPACT_ASSERTION_CHARS),
        ("Waveform Context", COMPACT_ASSERTION_CHARS),
        ("Cleaned Simulator Diagnostics", COMPACT_DIAGNOSTIC_CHARS),
        ("Current Evaluation Summary", 900),
        ("Interface Diagnostics", COMPACT_INTERFACE_CHARS),
        ("Benchmark Interface Contract", COMPACT_INTERFACE_CHARS),
    ):
        block = blocks.get(heading)
        if block:
            selected.append(truncate_text(block, limit, keep="head"))

    if not selected:
        return truncate_text(text, COMPACT_FEEDBACK_CHARS, keep="head")
    selected.append(
        "Do not infer a root cause from a packed `out` port alone: the CVDP adapter may unpack tuple outputs. "
        "Prioritize the first failing assertion's expected/actual values, parameter setting, and timing. "
        "The full generated SV is intentionally omitted here; run `lean_check` to inspect a fresh extraction after edits."
    )
    return truncate_text("\n\n".join(selected), COMPACT_FEEDBACK_CHARS, keep="head")


def build_compact_repair_prompt(
    *,
    prob_id: str,
    info: ProblemInfo | None,
    dataset_name: str,
    has_repl: bool,
    phase: str,
    iteration: int,
    current_lean: str,
    latest_feedback: str,
    recent_attempts: list[dict],
    extra_constraints: str = "",
    include_cvdp_scaffold: bool = False,
    include_cvdp_verified_idioms: bool = False,
) -> str:
    design_name = info.design_name if info else "TopModule"
    prompt_text = info.prompt_text if info else "(no description available)"
    ref_code = info.ref_code if info else "(no reference Verilog available)"
    func_name = prob_id.lower() if dataset_name == "verilogeval" else lean_identifier(design_name)
    check_instruction = (
        f"Use `lean_check` on the complete Lean body including `#synthesizeVerilog {func_name}`; require generated Verilog. The harness saves the latest compile-safe check automatically."
        if has_repl else
        f"Run `lake build Generated.{prob_id}` before ending the repair."
    )
    constraints = [
        f"Write the final candidate to `Generated/{prob_id}.lean`.",
        f"The generated SystemVerilog top module must remain `{design_name}`.",
        f"`#synthesizeVerilog` must reference `{func_name}`.",
        "Preserve the original interface, port widths, reset/clock semantics, and functional behavior.",
        "Do not trade functional correctness for optimization.",
        check_instruction,
    ]
    if include_cvdp_scaffold:
        constraints.extend([
            "Preserve the deterministic typed scaffold shell and named MSB-to-LSB output order.",
            "A compile-successful marked scaffold is incomplete; implement all TODO behavior and remove CKTARCHON_IMPLEMENTATION_REQUIRED.",
            "Read only this task's Generated target; other concurrent candidates are unverified and isolated.",
            f"Before editing, use `read_file` on `Generated/{prob_id}.lean`; that file is the authoritative current candidate.",
        ])
    if include_cvdp_verified_idioms:
        if include_cvdp_scaffold:
            constraints.extend([
                "Directly adopt the single retrieved repair idiom body in the current typed scaffold, adapting only the authoritative task names, widths, output order, reset polarity, and latency.",
                f"After the one required `read_file` of `Generated/{prob_id}.lean`, your next tool action must edit that target. Do not read or search anything else, and do not merely analyze the idiom.",
                "Run the required compile check only after that edit, then iterate edit followed by check until generated Verilog is produced.",
            ])
        else:
            constraints.append(
                "Directly adopt the single retrieved repair idiom body in the current candidate, adapting names, signature, output packing, reset polarity, and latency; edit before running the required compile check."
            )
    if extra_constraints:
        constraints.append(extra_constraints)
    interface_contract = format_benchmark_interface_contract(info)
    interface_section = (
        f"### Benchmark Interface Contract\n\n{interface_contract}\n\n"
        if interface_contract else ""
    )
    if include_cvdp_scaffold:
        resume_note = (
            "The full previous conversation is intentionally omitted to save tokens. "
            f"First use `read_file` on `Generated/{prob_id}.lean`; repair that authoritative "
            "rollback-selected candidate using the contract and latest diagnostics below."
        )
        reference_section = ""
        scaffold_section = ""
        current_candidate_section = (
            "### Current Lean Candidate\n\n"
            f"Read `Generated/{prob_id}.lean` with `read_file` before editing. The target file, "
            "not a copied prompt snapshot, is the authoritative current candidate.\n\n"
        )
    else:
        resume_note = (
            "The full previous conversation is intentionally omitted to save tokens; "
            "rely on the compact state below."
        )
        reference_section = (
            "### Reference Verilog / Interface Context\n\n"
            f"```systemverilog\n{truncate_text(ref_code, COMPACT_REF_CHARS, keep='middle')}\n```\n\n"
        )
        scaffold_section = ""
        current_candidate_section = (
            "### Current Lean Candidate\n\n"
            f"```lean\n{truncate_text(current_lean, COMPACT_CODE_CHARS, keep='middle')}\n```\n\n"
        )
    idiom_section = (
        format_cvdp_verified_idioms(
            info, feedback=latest_feedback, repair=True
        ) + "\n\n"
        if include_cvdp_verified_idioms else ""
    )
    return (
        f"## Compact Repair Context\n\n"
        f"You are resuming `{phase}` repair for `{prob_id}` at iteration {iteration}. "
        f"{resume_note}\n\n"
        f"### Problem\n"
        f"- Dataset: {dataset_name}\n"
        f"- Target module: `{design_name}`\n"
        f"- Lean function: `{func_name}`\n\n"
        f"### Natural Language Specification\n\n"
        f"{truncate_text(prompt_text, COMPACT_SPEC_CHARS, keep='head')}\n\n"
        f"{reference_section}"
        f"{interface_section}"
        f"{scaffold_section}"
        f"{idiom_section}"
        f"### Additional Input Context Files\n\n"
        f"{format_context_files(info)}\n\n"
        f"{current_candidate_section}"
        f"### Latest Feedback To Fix\n\n"
        f"{compact_repair_feedback(latest_feedback)}\n\n"
        f"### Recent Attempts Summary\n\n"
        f"{summarize_recent_attempts(recent_attempts)}\n\n"
        f"### Required Constraints\n\n"
        + "\n".join(f"- {c}" for c in constraints)
        + "\n\n"
        + (
            f"Read `Generated/{prob_id}.lean` once, then immediately edit it using the repair idiom body before any check or further read/search. "
            "Reading or explaining without an edit is not a repair. End only after the edited final file is written and checked."
            if include_cvdp_scaffold and include_cvdp_verified_idioms else
            "Repair the current Lean candidate using the tools. End only after the final file is written."
        )
    )


def eval_progress_key(result: dict | None) -> tuple:
    if not result:
        return (0, 0, 0, 0, 0)
    sim_rank = {
        "not_run": 0,
        "sim_error": 1,
        "sim_fail": 2,
        "sim_pass": 3,
    }.get(result.get("sim_status"), 0)
    mismatches = result.get("sim_mismatches")
    mismatch_score = 0
    if isinstance(mismatches, int) and mismatches >= 0:
        mismatch_score = -mismatches
    counts = extract_sim_test_counts(result.get("detail"))
    sim_passed = counts[1] if counts else 0
    sim_failed = -counts[2] if counts else 0
    return (
        1 if result.get("compile_pass") else 0,
        1 if result.get("sv_extracted") else 0,
        sim_rank,
        1 if result.get("lint_pass") else 0,
        sim_passed,
        sim_failed,
        mismatch_score,
    )


def build_sim_feedback(
    prob_id: str,
    result: dict,
    iteration: int,
    history: list[dict],
    run_dir: Path,
    info: ProblemInfo | None = None,
    repair_target: str = "lean",
    current_sv: str | None = None,
) -> str:
    direct_verilog = repair_target == "verilog"
    if direct_verilog:
        evaluation_summary = "\n".join([
            f"- SystemVerilog compile: {bool_status(result.get('compile_pass'))}",
            f"- RTL sim: {result.get('sim_status', 'unknown')}",
            f"- RTL sim mismatches: {result.get('sim_mismatches', 'unknown')}",
        ])
        implementation_summary = (
            f"The current direct SystemVerilog implementation for `{prob_id}` did not pass the full RTL evaluation."
        )
        repair_summary = "Repair the SystemVerilog candidate directly."
    else:
        evaluation_summary = summarize_eval_result(result)
        implementation_summary = (
            f"The current Lean implementation for `{prob_id}` did not pass the full RTL evaluation."
        )
        repair_summary = "Repair the Lean code, not the generated SystemVerilog directly."
    lines = [
        f"## RTL Simulation Feedback - Iteration {iteration + 1}",
        "",
        implementation_summary,
        repair_summary,
        "",
        "### Current Evaluation Summary",
        evaluation_summary,
    ]
    simulator_output = read_simulator_output(prob_id, run_dir)
    # Put simulator artifacts first.  In particular, Icarus build diagnostics
    # live in ``sim.log`` while pytest's tail often contains only the Python
    # subprocess traceback that wrapped the failed elaboration.
    combined_detail = "\n".join(
        part for part in [simulator_output, str(result.get("detail") or "")] if part.strip()
    )
    sim_diagnostics = extract_sim_diagnostics(combined_detail, SIM_DIAGNOSTIC_CHARS)
    benchmark_contract = format_benchmark_interface_contract(info)
    if benchmark_contract:
        lines.extend([
            "",
            "### Benchmark Interface Contract",
            "Derived from benchmark metadata, wrapper expectations, and public interface context.",
            benchmark_contract,
        ])
    if sim_diagnostics:
        lines.extend([
            "",
            "### Cleaned Simulator Diagnostics",
            "Extracted only from simulator output; no benchmark source or reference RTL is included.",
            "```text",
            sim_diagnostics,
            "```",
        ])
    first_failures = extract_first_failure_diagnostics(combined_detail)
    if first_failures:
        lines.extend([
            "",
            "### First Failing Assertions",
            "Structured from simulator output; use it to identify the first wrong signal/value.",
            first_failures,
        ])
    waveform_context = extract_waveform_context(prob_id, run_dir, combined_detail)
    if waveform_context:
        lines.extend([
            "",
            "### Waveform Context",
            "Best-effort signal context around the first logged failure time.",
            "```text",
            waveform_context,
            "```",
        ])
    sv_code = current_sv if current_sv is not None else read_current_sv(prob_id, run_dir)
    interface_diagnostics = build_interface_diagnostics(
        {**result, "detail": combined_detail},
        sv_code,
    )
    expected_port_diagnostics = build_expected_vs_generated_port_diagnostics(info, sv_code)
    if expected_port_diagnostics:
        interface_diagnostics = "\n".join(
            part for part in [interface_diagnostics, expected_port_diagnostics] if part
        )
    if interface_diagnostics:
        lines.extend([
            "",
            "### Interface Diagnostics",
            "Derived from generated SV ports and simulator-reported DUT signal names.",
            interface_diagnostics,
        ])
    if sv_code:
        lines.extend([
            "",
            "### Latest Generated SystemVerilog",
            (
                "This is the current candidate to repair directly."
                if direct_verilog
                else "Use this only to diagnose the Lean-to-Verilog behavior and interface."
            ),
            "```systemverilog",
            truncate_text(sv_code, COMPACT_REF_CHARS, keep="middle"),
            "```",
        ])
    if history:
        lines.extend([
            "",
            "### Previous Simulation Repair Attempts",
            summarize_recent_attempts(history),
        ])
    lines.extend(["", "### Repair Guidance"])
    if direct_verilog:
        lines.extend([
            "- If Verilog compile failed, fix syntax, module names, ports, widths, signedness, and reset/clock wiring first.",
            "- If RTL simulation mismatched, compare the current behavior against the natural language spec, interface contract, and simulator diagnostics.",
            "- Keep the target module interface stable. Do not edit benchmark testbenches or reference files.",
            "- Stop after writing the complete corrected SystemVerilog candidate.",
        ])
    else:
        lines.extend([
            "- If Lean compile failed, fix the Lean type/API error first.",
            "- If Verilog compile failed, inspect generated module names, ports, widths, signedness, and reset/clock wiring.",
            "- If RTL simulation mismatched, compare the current behavior against the natural language spec, reference/interface context, and testbench expectations.",
            "- Keep the target module interface stable. Do not edit benchmark testbenches or reference files.",
            "- Stop when the candidate compiles, extracts SystemVerilog, and passes RTL simulation.",
        ])
    return "\n".join(lines)


def collect_synth_failure_context(
    prob_id: str,
    result: dict,
    run_dir: Path,
    max_chars: int = 9000,
) -> str:
    """Collect compact synthesis/evaluation diagnostics for LLM feedback."""
    chunks: list[str] = []

    detail = str(result.get("detail") or "").strip()
    if detail:
        chunks.append(f"[evaluation detail]\n{_tail_text(detail, 2500)}")

    synth_error = str(result.get("synth_error") or "").strip()
    if synth_error:
        chunks.append(f"[synth_error]\n{_tail_text(synth_error, 2500)}")

    synth_dir = run_dir / "synth" / prob_id
    for name in ("synth_stderr.txt", "synth_stdout.txt"):
        path = synth_dir / name
        if path.exists():
            tail = _read_tail(path, 3500)
            if tail.strip():
                chunks.append(f"[{name} tail]\n{tail}")

    log_dir = synth_dir / "orfs_logs"
    if log_dir.exists():
        try:
            logs = sorted(
                log_dir.rglob("*.log"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )[:3]
        except Exception:
            logs = []
        for log_path in logs:
            tail = _read_tail(log_path, 2500)
            if tail.strip():
                try:
                    label = str(log_path.relative_to(synth_dir))
                except ValueError:
                    label = str(log_path)
                chunks.append(f"[{label} tail]\n{tail}")

    if not chunks:
        return "No synthesis log was captured. Re-check the generated Lean/Sparkle for unsupported constructs or bad module/interface generation."
    return _tail_text("\n\n".join(chunks), max_chars=max_chars)


def build_synth_generation_feedback(
    prob_id: str,
    result: dict,
    iteration: int,
    run_dir: Path,
) -> str:
    """Build feedback used to repair a functionally passing design that fails synthesis."""
    lines = [
        f"## Generation-Time Synthesis Feedback -- Iteration {iteration + 1}",
        "",
        f"Your current `Generated/{prob_id}.lean` was generated and evaluated.",
        "Use the feedback below to repair the Lean/Sparkle implementation so it remains functionally correct and becomes synthesizable.",
        "Do not do PPA optimization here; only fix synthesizability or any regression introduced by the previous repair.",
        "",
        "### Current status",
        f"- compile_pass: {result.get('compile_pass')}",
        f"- sv_extracted: {result.get('sv_extracted')}",
        f"- lint_pass: {result.get('lint_pass')}",
        f"- sim_status: {result.get('sim_status')}",
        f"- synth_pass: {result.get('synth_pass')}",
        "",
        "### Diagnostics",
        "```text",
        collect_synth_failure_context(prob_id, result, run_dir),
        "```",
        "",
        "### Instructions",
        f"1. Read and edit `Generated/{prob_id}.lean`.",
        "2. Preserve the problem behavior and public interface.",
        "3. Use `lean_check` to verify Lean compilation and Verilog extraction.",
        "4. Keep `#synthesizeVerilog` on the implementation function.",
        "5. Write the final repaired file. The external evaluator will rerun simulation and synthesis.",
    ]
    return "\n".join(lines)


def _should_run_synth_feedback(
    args: argparse.Namespace,
    agent_stats: dict | None,
    result: dict,
) -> bool:
    """Return whether a real synthesis failure should enter the repair loop."""
    return bool(
        args.synth_feedback
        and agent_stats is not None
        and result.get("sim_status") == "sim_pass"
        and not result.get("synth_pass")
        and not result.get("parameterized_ppa_unsupported")
    )


def ppa_improved(old: dict, new: dict) -> bool:
    """Check if PPA improved: any metric >5% better, none >20% worse."""
    dominated_metrics = {"area_um2": -1, "cell_count": -1, "wns_ns": -1, "power_uw": -1}
    any_improved = False
    for key, direction in dominated_metrics.items():
        ov, nv = old.get(key), new.get(key)
        if ov is None or nv is None or ov == 0:
            continue
        # direction=-1 means lower is better
        change = (nv - ov) / abs(ov) * direction
        if change > 0.05:
            any_improved = True
        if change < -0.20:
            return False  # regressed too much
    return any_improved


def _has_kernel_checked_universal_theorem(result: dict) -> bool:
    """Return true only for explicit universal Lean-source evidence.

    ``has_sorry == False`` is deliberately insufficient: a source file can be
    complete while declaring no correctness theorem at all.
    """
    evidence_items = result.get("verification_evidence", [])
    if isinstance(evidence_items, dict):
        evidence_items = [evidence_items]
    for evidence in evidence_items or []:
        if isinstance(evidence, dict) and is_valid_universal_theorem_evidence(evidence):
            return True
    return False


def _has_kernel_checked_design_contract(result: dict) -> bool:
    """Return whether evidence is trusted *and bound to this design contract*.

    A generic theorem over ``W`` can be perfectly valid Lean-source evidence
    while saying nothing about the generated circuit.  The current certificate
    schema intentionally makes no compiler-correctness claim and carries no
    trusted binding to a design artifact, so it must not influence architecture
    or PPA candidate selection.  A future design-contract certificate can make
    this predicate nontrivial once it includes a kernel-checked subject binding
    and a compiler-correctness story.
    """
    _ = result
    return False


def build_ppa_feedback(prob_id: str, result: dict, iteration: int, history: list[dict]) -> str:
    """Build a PPA feedback message for the agent."""
    ppa = extract_ppa(result)
    lines = [
        f"## PPA Optimization Feedback — Iteration {iteration + 1}",
        "",
        f"Your implementation for `{prob_id}` passed functional simulation and synthesis.",
        "Now optimize the design for better PPA (Power, Performance, Area).",
        "",
        "### Current PPA Metrics",
        f"- Area: {ppa['area_um2']:.2f} μm²" if ppa["area_um2"] is not None else "- Area: N/A",
        f"- Cell count: {ppa['cell_count']}" if ppa["cell_count"] is not None else "- Cell count: N/A",
        f"- WNS (worst negative slack): {ppa['wns_ns']:.2f} ns" if ppa["wns_ns"] is not None else "- WNS: N/A",
        f"- Power: {ppa['power_uw']:.4f} μW" if ppa["power_uw"] is not None else "- Power: N/A",
    ]

    if len(history) > 1:
        lines.append("")
        lines.append("### PPA History")
        lines.append("| Iter | Area (μm²) | Cells | WNS (ns) | Power (μW) |")
        lines.append("|------|-----------|-------|----------|------------|")
        for idx, h in enumerate(history):
            a = f"{h['area_um2']:.2f}" if h["area_um2"] is not None else "N/A"
            c = str(h["cell_count"]) if h["cell_count"] is not None else "N/A"
            w = f"{h['wns_ns']:.2f}" if h["wns_ns"] is not None else "N/A"
            p = f"{h['power_uw']:.4f}" if h["power_uw"] is not None else "N/A"
            label = "baseline" if idx == 0 else str(idx)
            lines.append(f"| {label} | {a} | {c} | {w} | {p} |")

    # Suggest focus area
    lines.append("")
    lines.append("### Optimization Focus")
    if ppa["wns_ns"] is not None and ppa["wns_ns"] < 0:
        lines.append("- **CRITICAL**: WNS is negative — timing violation. Focus on breaking the critical path.")
    elif ppa["area_um2"] is not None and ppa["cell_count"] is not None:
        lines.append("- Focus on reducing area and cell count through logic simplification.")
    lines.append("")
    lines.append("### Instructions (Proof-aware Optimization)")
    lines.append(f"You MUST prove functional equivalence when optimizing. Use `lean_proof_step` for interactive proofs:")
    lines.append(f"1. Read your current `Generated/{prob_id}.lean`")
    lines.append(f"2. Use `lean_proof_step` to define both `{prob_id.lower()}_spec` (original) and `{prob_id.lower()}` (optimized)")
    lines.append(f"   → Note the returned `env` number")
    lines.append(f"3. Use `lean_proof_step(env=<that env>)` to write the theorem with `sorry`:")
    lines.append(f"   `theorem {prob_id.lower()}_equiv : {prob_id.lower()} = {prob_id.lower()}_spec := by sorry`")
    lines.append(f"   → Read the proof goal to understand what needs to be proved")
    lines.append(f"4. Replace `sorry` with tactics (`unfold`/`ext t`/`simp`/`bv_omega`) — reuse the same env from step 2")
    lines.append(f"   → Each attempt shows updated goals; iterate until COMPLETE (no sorry)")
    lines.append(f"5. If you cannot prove equivalence, do NOT optimize — keep the original unchanged")
    lines.append(f"6. Once the proof is COMPLETE, write the final code to `Generated/{prob_id}.lean` and verify with `lean_check`")
    lines.append(f"7. `#synthesizeVerilog` must reference the optimized `{prob_id.lower()}`, not `_spec`")

    return "\n".join(lines)


def _build_sv_opt_system_prompt(module_name: str) -> str:
    return f"""\
You are an expert hardware designer optimizing synthesizable SystemVerilog for better PPA.

RULES:
1. Preserve the exact module name `{module_name}` and the exact port interface.
2. Output ONLY a complete SystemVerilog-2012 module (`module ... endmodule`).
3. The rewritten RTL must stay synthesizable with Icarus Verilog (`-g2012`).
4. Preserve functional behavior. Improve PPA only through RTL restructuring.
5. Prefer smaller area/cell count unless timing is violated, in which case prioritize fixing WNS.
"""


def _extract_sv_module(text: str, expected_module: str) -> str | None:
    """Extract module...endmodule from model output, renaming if needed."""
    text = re.sub(r"```(?:systemverilog|verilog|sv)?\s*", "", text)
    text = text.replace("```", "")
    m = re.search(rf"(module\s+{re.escape(expected_module)}\s*[\s\S]*?endmodule)", text)
    if m:
        return m.group(1)
    m = re.search(r"(module\s+\w+\s*[\s\S]*?endmodule)", text)
    if m:
        code = m.group(1)
        return re.sub(r"module\s+\w+", f"module {expected_module}", code, count=1)
    return None


def build_sv_ppa_feedback(
    prob_id: str,
    info: ProblemInfo,
    current_sv: str,
    result: dict,
    iteration: int,
    history: list[dict],
) -> str:
    """Build direct-SystemVerilog PPA feedback."""
    ppa = extract_ppa(result)
    expected_module = info.design_name
    lines = [
        f"## Direct SystemVerilog PPA Optimization — Iteration {iteration + 1}",
        "",
        f"Problem: `{prob_id}`",
        "Rewrite the current SystemVerilog RTL for better PPA while preserving functionality.",
        "",
        "### Natural Language Spec",
        info.prompt_text.strip(),
        "",
        "### Current SystemVerilog",
        f"```systemverilog\n{current_sv.strip()}\n```",
        "",
        "### Current PPA Metrics",
        f"- Area: {ppa['area_um2']:.2f} μm²" if ppa["area_um2"] is not None else "- Area: N/A",
        f"- Cell count: {ppa['cell_count']}" if ppa["cell_count"] is not None else "- Cell count: N/A",
        f"- WNS (worst negative slack): {ppa['wns_ns']:.2f} ns" if ppa["wns_ns"] is not None else "- WNS: N/A",
        f"- Power: {ppa['power_uw']:.4f} μW" if ppa["power_uw"] is not None else "- Power: N/A",
    ]
    if len(history) > 1:
        lines += [
            "",
            "### PPA History",
            "| Iter | Area (μm²) | Cells | WNS (ns) | Power (μW) |",
            "|------|-----------|-------|----------|------------|",
        ]
        for idx, h in enumerate(history):
            a = f"{h['area_um2']:.2f}" if h.get("area_um2") is not None else "N/A"
            c = str(h["cell_count"]) if h.get("cell_count") is not None else "N/A"
            w = f"{h['wns_ns']:.2f}" if h.get("wns_ns") is not None else "N/A"
            p = f"{h['power_uw']:.4f}" if h.get("power_uw") is not None else "N/A"
            label = "baseline" if idx == 0 else str(idx)
            lines.append(f"| {label} | {a} | {c} | {w} | {p} |")
    lines += [
        "",
        "### Optimization Focus",
    ]
    if ppa["wns_ns"] is not None and ppa["wns_ns"] < 0:
        lines.append("- Timing is violated. Prioritize a shorter critical path and better WNS.")
    else:
        lines.append("- Focus on lowering area and cell count without breaking functionality.")
    lines += [
        "",
        "### Output Requirements",
        f"- Keep the module name exactly `{expected_module}`.",
        "- Keep the exact external port interface.",
        "- Output ONLY the complete rewritten SystemVerilog module.",
    ]
    return "\n".join(lines)


def _cvdp_direct_sv_requires_parameter_sweep(
    info: ProblemInfo,
    evaluator: Evaluator,
    sv_code: str | None = None,
) -> bool:
    """Return whether CVDP may elaborate the DUT with native parameters.

    A single synthesis run cannot represent PPA for a parameter sweep.  Treat
    unresolved parameter dictionaries conservatively: simulation may execute
    the benchmark's own build matrix, but PPA must remain unset until the flow
    can synthesize and report every concrete configuration separately.
    """
    if evaluator.dataset_name != "cvdp":
        return False
    harness_files = (info.metadata or {}).get("harness_files", {})
    has_overrides, _parameter_names, unresolved = _cvdp_parameter_overrides(
        harness_files
    )
    benchmark_parameters = _cvdp_required_benchmark_parameter_names(
        ref_code=info.ref_code or "",
        design_name=info.design_name,
        harness_files=harness_files,
    )
    reference_has_parameters = bool(
        benchmark_parameters - set(_parameter_names)
    )
    top_has_parameters = bool(
        sv_code and _module_parameters(sv_code, info.design_name)
    )
    return (
        has_overrides
        or unresolved
        or reference_has_parameters
        or top_has_parameters
    )


def _cvdp_direct_sv_parameter_plan(
    info: ProblemInfo,
    evaluator: Evaluator,
    sv_code: str | None = None,
):
    """Return an exact finite plan for a parameterized CVDP top, if needed."""
    if not _cvdp_direct_sv_requires_parameter_sweep(info, evaluator, sv_code):
        return None
    harness_files = (info.metadata or {}).get("harness_files", {})
    _has_overrides, observed_names, _unresolved = _cvdp_parameter_overrides(
        harness_files
    )
    parameter_names = _cvdp_required_benchmark_parameter_names(
        ref_code=info.ref_code or "",
        design_name=info.design_name,
        harness_files=harness_files,
    )
    if sv_code:
        parameter_names.update(
            parameter.name
            for parameter in _module_parameters(sv_code, info.design_name)
        )
    return _cvdp_exact_parameter_sweep_plan(harness_files, parameter_names)


def _mark_parameterized_ppa_unsupported(
    result: dict,
    reasons: tuple[str, ...] | list[str] = (),
) -> dict:
    """Mark PPA as intentionally skipped without changing simulation status."""
    result.update({
        "parameterized_ppa_unsupported": True,
        "synth_status": "not_run_parameterized_sweep",
        "ppa_status": "unsupported_parameter_sweep",
        "ppa_error": (
            "PPA was not run because the complete CVDP parameter configuration "
            "matrix could not be recovered exactly"
            + (": " + "; ".join(reasons) if reasons else ".")
            + " Synthesizing only module defaults would be incomplete."
        ),
        "synth_pass": False,
        "synth_error": None,
        "pnr_pass": False,
        "pnr_error": None,
        "gds_generated": False,
        "drc_pass": None,
        "drc_violations": None,
        "drc_error": None,
        "lvs_pass": None,
        "lvs_error": None,
        "gls_synth_status": "not_run",
        "gls_synth_mismatches": -1,
        "gls_pnr_status": "not_run",
        "gls_pnr_mismatches": -1,
        "area_um2": None,
        "cell_count": None,
        "wns_ns": None,
        "power_uw": None,
    })
    return result


def evaluate_verilog_candidate(
    prob_id: str,
    info: ProblemInfo,
    sv_code: str,
    evaluator: Evaluator,
    run_dir: Path,
) -> dict:
    """Evaluate a direct-SystemVerilog candidate using the existing evaluator helpers."""
    result = {
        "prob_id": prob_id,
        "compile_pass": False,
        "sv_extracted": True,
        "lint_pass": False,
        "sim_status": "not_run",
        "sim_mismatches": -1,
        "synth_pass": False,
        "area_um2": None,
        "cell_count": None,
        "wns_ns": None,
        "power_uw": None,
        "unsupported_parameterization": False,
        "terminal_capability_error": False,
        "repairable_parameterization_error": False,
        "parameterized_ppa_unsupported": False,
        "verification_evidence": [],
        "unsupported_reason": None,
        "detail": "",
    }

    parameter_plan = _cvdp_direct_sv_parameter_plan(
        info, evaluator, sv_code
    )

    requested_module = (
        info.design_name if evaluator.dataset_name == "cvdp" else None
    )
    sparkle_mod_name, sparkle_ports = parse_module_ports(
        sv_code, module_name=requested_module
    )
    if requested_module and sparkle_mod_name != requested_module:
        result["detail"] = (
            f"Could not find generated SystemVerilog top module "
            f"'{requested_module}'"
        )
        return result
    if not sparkle_mod_name:
        result["detail"] = "Could not parse generated SystemVerilog module name"
        return result

    # VerilogEval simulation always wraps the DUT in a generated TopModule.
    # Rename direct-SV PPA candidates so they do not collide with that wrapper.
    if evaluator.dataset_name == "verilogeval" and sparkle_mod_name == "TopModule":
        inner_mod_name = f"__verilog_ppa_{prob_id.lower()}"
        sv_code = _rename_module_declaration(sv_code, sparkle_mod_name, inner_mod_name)
        sparkle_mod_name = inner_mod_name
        sparkle_mod_name, sparkle_ports = parse_module_ports(sv_code)
        if not sparkle_mod_name:
            result["detail"] = "Could not parse renamed SystemVerilog module name"
            return result

    eval_dir = run_dir / "verilog_ppa_eval"
    eval_dir.mkdir(parents=True, exist_ok=True)
    sv_file = eval_dir / f"{prob_id}.sv"
    sv_file.write_text(sv_code)
    result["lint_pass"] = evaluator._run_lint(sv_file)

    if evaluator.dataset_name == "cvdp":
        sim_status, mismatches, detail = evaluator._run_sim_cvdp(
            prob_id,
            sv_code,
            sparkle_mod_name,
            sparkle_ports,
            eval_dir,
            benchmark_ports=_benchmark_expected_ports(info),
            direct_top=True,
        )
    else:
        sim_status, mismatches, detail = evaluator._run_sim(
            prob_id, sv_code, sparkle_mod_name, sparkle_ports, eval_dir
        )
    result["sim_status"] = sim_status
    result["sim_mismatches"] = mismatches
    result["detail"] = detail
    if CVDP_PARAMETERIZATION_UNSUPPORTED in detail:
        result["unsupported_parameterization"] = True
        result["terminal_capability_error"] = False
        result["repairable_parameterization_error"] = True
        result["unsupported_reason"] = detail
    result["compile_pass"] = result["lint_pass"] and not (
        sim_status == "sim_error" and "compile failed" in detail.lower()
    )

    parameterized_sweep_ran = False
    if parameter_plan is not None and parameter_plan.unresolved:
        _mark_parameterized_ppa_unsupported(
            result, parameter_plan.unresolved_reasons
        )
    elif parameter_plan is not None and evaluator.enable_synth:
        if not hasattr(evaluator, "run_parameterized_ppa"):
            _mark_parameterized_ppa_unsupported(
                result,
                ("the evaluator does not provide the parameterized PPA runner",),
            )
        elif result["sim_status"] == "sim_pass":
            sweep_result = evaluator.run_parameterized_ppa(
                sv_code=sv_code,
                top_module=sparkle_mod_name or info.design_name,
                configurations=parameter_plan.configurations,
            )
            sweep_evidence = sweep_result.pop("verification_evidence", [])
            result.update(sweep_result)
            result["verification_evidence"] = (
                list(result.get("verification_evidence", []))
                + list(sweep_evidence)
            )
            parameterized_sweep_ran = True
    elif evaluator.enable_synth and result["sim_status"] == "sim_pass":
        synth_result = evaluator._run_synthesis(
            prob_id, sv_code, sparkle_mod_name or info.design_name, eval_dir
        )
        result.update(synth_result)

    if (
        evaluator.enable_pnr
        and result.get("synth_pass")
        and not parameterized_sweep_ran
    ):
        pnr_result = evaluator._run_pnr(
            prob_id, sv_code, sparkle_mod_name or info.design_name, eval_dir
        )
        result.update(pnr_result)

    return result


def run_verilog_ppa_loop(
    prob_id: str,
    info: ProblemInfo,
    args: argparse.Namespace,
    evaluator: Evaluator,
    run_dir: Path,
    result: dict,
    stats: dict,
) -> tuple[dict, list[dict]]:
    """Run PPA optimization directly on exported SystemVerilog instead of Lean."""
    if (
        _cvdp_direct_sv_requires_parameter_sweep(info, evaluator)
        and not result.get("parameter_sweep_results")
    ):
        return _mark_parameterized_ppa_unsupported(result), []

    env = load_env(PROJECT_ROOT / "key.env")
    api_key = env.get("ANTHROPIC_API_KEY", os.environ.get("ANTHROPIC_API_KEY", ""))
    base_url = env.get("ANTHROPIC_BASE_URL", os.environ.get("ANTHROPIC_BASE_URL"))
    client_kwargs = {"api_key": api_key}
    if base_url:
        client_kwargs["base_url"] = base_url
    client = anthropic.Anthropic(**client_kwargs)

    sv_file = run_dir / "sv" / f"{prob_id}.sv"
    if not sv_file.exists():
        return result, []

    current_code = sv_file.read_text()
    expected_module = info.design_name
    ppa_history = [extract_ppa(result)]
    best_code = current_code
    best_ppa = ppa_history[0]
    best_result = result

    opt_dir = run_dir / "verilog_ppa" / prob_id
    opt_dir.mkdir(parents=True, exist_ok=True)
    (opt_dir / "baseline.sv").write_text(current_code)

    for ppa_iter in range(args.ppa_iters):
        feedback = build_sv_ppa_feedback(prob_id, info, current_code, result, ppa_iter, ppa_history)
        try:
            iter_t0 = time.monotonic()
            response = create_message_with_retries(
                client,
                model=args.model,
                max_tokens=args.max_tokens,
                temperature=0.0,
                system=_build_sv_opt_system_prompt(expected_module),
                messages=[{"role": "user", "content": feedback}],
            )
            iter_elapsed = time.monotonic() - iter_t0
        except Exception as e:
            log_event(run_dir, {
                "prob_id": prob_id,
                "ppa_iteration": ppa_iter + 1,
                "ppa_target": "verilog",
                "ppa_error": str(e),
            })
            break

        with _stats_lock:
            stats["agent_tokens"]["input"] += response.usage.input_tokens
            stats["agent_tokens"]["output"] += response.usage.output_tokens
            stats["agent_turns_total"] += 1
            stats["agent_elapsed_total"] += iter_elapsed

        candidate = _extract_sv_module(
            response.content[0].text if response.content else "",
            expected_module,
        )
        if not candidate:
            log_event(run_dir, {
                "prob_id": prob_id,
                "ppa_iteration": ppa_iter + 1,
                "ppa_target": "verilog",
                "ppa_status": "no_module",
                "ppa_elapsed_seconds": round(iter_elapsed, 3),
            })
            continue

        candidate_path = opt_dir / f"iter_{ppa_iter + 1}.sv"
        candidate_path.write_text(candidate)
        new_result = evaluate_verilog_candidate(
            prob_id, info, candidate, evaluator, opt_dir / f"iter_{ppa_iter + 1}"
        )

        if new_result["sim_status"] != "sim_pass":
            log_event(run_dir, {
                "prob_id": prob_id,
                "ppa_iteration": ppa_iter + 1,
                "ppa_target": "verilog",
                "ppa_status": "rollback_sim_fail",
                "ppa_elapsed_seconds": round(iter_elapsed, 3),
                "detail": new_result.get("detail", "")[:300],
                **extract_ppa(new_result),
            })
            continue

        new_ppa = extract_ppa(new_result)
        improved = ppa_improved(best_ppa, new_ppa)
        status = "improved" if improved else "converged"
        log_event(run_dir, {
            "prob_id": prob_id,
            "ppa_iteration": ppa_iter + 1,
            "ppa_target": "verilog",
            "ppa_status": status,
            "ppa_elapsed_seconds": round(iter_elapsed, 3),
            **new_ppa,
        })

        current_code = candidate
        result = new_result
        ppa_history.append(new_ppa)
        with _stats_lock:
            stats["ppa_iterations_total"] += 1
        if improved:
            best_ppa = new_ppa
            best_code = candidate
            best_result = new_result

    if len(ppa_history) > 1:
        (opt_dir / "best.sv").write_text(best_code)
        with _stats_lock:
            stats["ppa_optimized"] += 1

    return best_result, ppa_history


# ── Architecture exploration helpers ─────────────────────────────


@dataclass
class ArchCandidate:
    index: int
    code: str
    ppa: dict
    sim_pass: bool
    verified: bool
    description: str
    verification_evidence: list[dict] = field(default_factory=list)


def pareto_dominant(a: dict, b: dict) -> bool:
    """Return True if a Pareto-dominates b (all metrics no worse, at least one better)."""
    keys = ["area_um2", "cell_count", "power_uw"]
    any_better = False
    for k in keys:
        av, bv = a.get(k), b.get(k)
        if av is None or bv is None:
            continue
        if av > bv:
            return False
        if av < bv:
            any_better = True
    # WNS: closer to 0 is better (less negative = better)
    aw, bw = a.get("wns_ns"), b.get("wns_ns")
    if aw is not None and bw is not None:
        if aw < bw:
            return False
        if aw > bw:
            any_better = True
    return any_better


def select_best_candidate(
    candidates: list[ArchCandidate],
    constraints: dict,
) -> ArchCandidate:
    """Select best candidate: pass sim, then prefer a bound design proof and PPA."""
    valid = [c for c in candidates if c.sim_pass]
    if not valid:
        return candidates[0]  # fallback to initial

    # Filter by constraints if specified
    budget = constraints.get("area_budget")
    if budget is not None:
        within = [c for c in valid if c.ppa.get("area_um2") is not None and c.ppa["area_um2"] <= budget]
        if within:
            valid = within

    # Sort: verified first, then by area (smallest), then by cell count
    valid.sort(key=lambda c: (
        not c.verified,  # verified=True → 0 (first)
        c.ppa.get("area_um2") or float("inf"),
        c.ppa.get("cell_count") or float("inf"),
    ))
    return valid[0]


def build_arch_feedback(
    prob_id: str,
    candidates: list[ArchCandidate],
    iteration: int,
    constraints: dict,
) -> str:
    """Build architecture exploration feedback with PPA comparison table."""
    func_name = prob_id.lower()
    lines = [
        f"## Architecture Exploration — Candidate {iteration + 2}",
        "",
        f"Your task: write a COMPLETELY DIFFERENT architecture for `{prob_id}`.",
        "Do NOT tweak the existing implementation — write a new one from scratch.",
        "",
        "### Candidates So Far",
        "| # | Description | Sim | Verified | Area (μm²) | Cells | WNS (ns) | Power (μW) |",
        "|---|-------------|-----|----------|-----------|-------|----------|------------|",
    ]
    for c in candidates:
        sim = "Pass" if c.sim_pass else "FAIL"
        v = "Yes" if c.verified else "No"
        a = f"{c.ppa['area_um2']:.1f}" if c.ppa.get("area_um2") is not None else "N/A"
        cells = str(c.ppa["cell_count"]) if c.ppa.get("cell_count") is not None else "N/A"
        w = f"{c.ppa['wns_ns']:.3f}" if c.ppa.get("wns_ns") is not None else "N/A"
        p = f"{c.ppa['power_uw']:.4f}" if c.ppa.get("power_uw") is not None else "N/A"
        lines.append(f"| v{c.index} | {c.description} | {sim} | {v} | {a} | {cells} | {w} | {p} |")

    lines.append("")

    # Constraints
    budget = constraints.get("area_budget")
    latency = constraints.get("latency_budget")
    if budget or latency:
        lines.append("### Constraints")
        if budget:
            lines.append(f"- Area budget: {budget:.1f} μm²")
        if latency:
            lines.append(f"- Latency budget: {latency} cycles")
        lines.append("")

    # Suggestions based on what's been tried
    lines.append("### Suggestions")
    lines.append("Try a fundamentally different approach:")
    if len(candidates) == 1:
        lines.append("- If the initial design is combinational, try a pipelined or sequential version")
        lines.append("- If it uses a flat mux tree, try a hierarchical or encoded approach")
    else:
        tried = ", ".join(f"v{c.index} ({c.description})" for c in candidates)
        lines.append(f"- Already tried: {tried}")
        lines.append("- Explore a different point in the parallelism/pipeline/resource-sharing space")

    lines.append("")
    lines.append("### Instructions (Proof-aware Architecture)")
    lines.append(f"Use `lean_proof_step` for interactive proof development:")
    lines.append(f"1. Read your current `Generated/{prob_id}.lean`")
    lines.append(f"2. Use `lean_proof_step` to define `{func_name}_spec` (keep original) and `{func_name}` (new architecture)")
    lines.append(f"   → Note the returned `env` number")
    lines.append(f"3. Use `lean_proof_step(env=<that env>)` to write `theorem {func_name}_equiv : {func_name} = {func_name}_spec := by sorry`")
    lines.append(f"   → Read the proof goals to understand what needs to be proved")
    lines.append(f"4. Replace `sorry` with tactics (`unfold`/`ext t`/`simp`/`bv_omega`) — iterate using the same def env")
    lines.append(f"5. If you CANNOT prove equivalence, still write the new architecture — use `sorry` as a placeholder. It will be marked as unverified but still evaluated via simulation")
    lines.append(f"6. Once done, write the final code to `Generated/{prob_id}.lean` and verify with `lean_check`")
    lines.append(f"7. `#synthesizeVerilog` must reference `{func_name}`, not `{func_name}_spec`")

    return "\n".join(lines)


def print_summary_table(stats: dict, elapsed: float, synth_enabled: bool = False, pnr_enabled: bool = False, drc_enabled: bool = False, lvs_enabled: bool = False, gls_enabled: bool = False, ppa_opt_enabled: bool = False, arch_explore_enabled: bool = False, synth_feedback_enabled: bool = False) -> None:
    """Print a rich summary table."""
    attempted = stats["total"] - stats["skipped"]
    sim_rate = f"{stats['sim_pass']/attempted*100:.1f}%" if attempted > 0 else "N/A"
    compile_rate = f"{stats['compile_pass']/attempted*100:.1f}%" if attempted > 0 else "N/A"

    h, rem = divmod(int(elapsed), 3600)
    m, s = divmod(rem, 60)
    elapsed_str = f"{h}:{m:02d}:{s:02d}"

    table = Table(title="Sparkle Agent Summary", show_header=False, border_style="cyan")
    table.add_column("Key", style="bold")
    table.add_column("Value")

    table.add_row("Problems", f"{attempted} attempted / {stats['total']} total")
    if stats["skipped"]:
        table.add_row("Skipped", str(stats["skipped"]))
    if stats.get("agent_error"):
        table.add_row("Agent error", f"[red]{stats['agent_error']}[/red]")
    table.add_row("Compile pass", f"[green]{stats['compile_pass']}[/green] ({compile_rate})")
    table.add_row("Sim pass", f"[green]{stats['sim_pass']}[/green] ({sim_rate})")
    table.add_row("Sim fail", f"[red]{stats['sim_fail']}[/red]" if stats["sim_fail"] else "0")
    table.add_row("Sim error", f"[yellow]{stats['sim_error']}[/yellow]" if stats["sim_error"] else "0")
    if stats.get("sim_not_run"):
        table.add_row("Sim not run", f"[yellow]{stats['sim_not_run']}[/yellow]")
    if synth_enabled:
        parameterized_ppa_skipped = stats.get("parameterized_ppa_skipped", 0)
        synth_eligible = max(attempted - parameterized_ppa_skipped, 0)
        synth_rate = (
            f"{stats['synth_pass']/synth_eligible*100:.1f}%"
            if synth_eligible > 0 else "N/A"
        )
        table.add_row("Synth pass", f"[green]{stats['synth_pass']}[/green] ({synth_rate})")
        if parameterized_ppa_skipped:
            table.add_row(
                "Synth skipped",
                f"[yellow]{parameterized_ppa_skipped} parameter sweep[/yellow]",
            )
    if pnr_enabled:
        parameterized_ppa_skipped = stats.get("parameterized_ppa_skipped", 0)
        pnr_eligible = max(attempted - parameterized_ppa_skipped, 0)
        pnr_rate = (
            f"{stats['pnr_pass']/pnr_eligible*100:.1f}%"
            if pnr_eligible > 0 else "N/A"
        )
        table.add_row("P&R pass", f"[green]{stats['pnr_pass']}[/green] ({pnr_rate})")
    if drc_enabled:
        parameterized_ppa_skipped = stats.get("parameterized_ppa_skipped", 0)
        drc_eligible = max(attempted - parameterized_ppa_skipped, 0)
        drc_rate = (
            f"{stats['drc_pass']/drc_eligible*100:.1f}%"
            if drc_eligible > 0 else "N/A"
        )
        table.add_row("DRC pass", f"[green]{stats['drc_pass']}[/green] ({drc_rate})")
    if lvs_enabled:
        parameterized_ppa_skipped = stats.get("parameterized_ppa_skipped", 0)
        lvs_eligible = max(attempted - parameterized_ppa_skipped, 0)
        lvs_rate = (
            f"{stats['lvs_pass']/lvs_eligible*100:.1f}%"
            if lvs_eligible > 0 else "N/A"
        )
        table.add_row("LVS pass", f"[green]{stats['lvs_pass']}[/green] ({lvs_rate})")
    if gls_enabled:
        parameterized_ppa_skipped = stats.get("parameterized_ppa_skipped", 0)
        gls_eligible = max(attempted - parameterized_ppa_skipped, 0)
        gls_s_rate = (
            f"{stats['gls_synth_pass']/gls_eligible*100:.1f}%"
            if gls_eligible > 0 else "N/A"
        )
        table.add_row("GLS synth pass", f"[green]{stats['gls_synth_pass']}[/green] ({gls_s_rate})")
        if pnr_enabled:
            gls_p_rate = (
                f"{stats['gls_pnr_pass']/gls_eligible*100:.1f}%"
                if gls_eligible > 0 else "N/A"
            )
            table.add_row("GLS PnR pass", f"[green]{stats['gls_pnr_pass']}[/green] ({gls_p_rate})")
    if synth_feedback_enabled:
        table.add_row("Synth feedback", f"[yellow]{stats['synth_feedback_fixed']}[/yellow] fixed ({stats['synth_feedback_attempts']} iters)")
    if stats.get("sim_feedback_iterations_total"):
        table.add_row(
            "Sim feedback",
            f"[yellow]{stats['sim_feedback_success']}[/yellow] fixed "
            f"({stats['sim_feedback_iterations_total']} attempts, shared turn budget)",
        )
    if ppa_opt_enabled:
        table.add_row("PPA optimized", f"[yellow]{stats['ppa_optimized']}[/yellow] ({stats['ppa_iterations_total']} iters)")
    if arch_explore_enabled:
        table.add_row("Arch explored", f"[blue]{stats['arch_explored']}[/blue] ({stats['arch_candidates_total']} candidates)")
    table.add_row("Tokens", f"{stats['agent_tokens']['input']}+{stats['agent_tokens']['output']}")
    completed = stats.get("agent_completed", 0)
    if completed:
        total_tokens = stats["agent_tokens"]["input"] + stats["agent_tokens"]["output"]
        table.add_row("Avg tokens/problem", f"{total_tokens / completed:.0f}")
        table.add_row("Avg turns/problem", f"{stats.get('agent_turns_total', 0) / completed:.1f}")
        table.add_row("Avg compile checks/problem", f"{stats.get('agent_compile_checks_total', 0) / completed:.1f}")
        table.add_row("Avg wall time/problem", f"{stats.get('problem_elapsed_total', 0.0) / completed:.1f}s")
    table.add_row("Time", elapsed_str)
    table.add_row("Model", stats.get("model", ""))

    console.print()
    console.print(table)


def _process_one_problem(
    prob_id: str,
    args: argparse.Namespace,
    skill: str,
    evaluator: Evaluator,
    run_dir: Path,
    stats: dict,
    overall_progress: Progress,
    current_progress: Progress,
    overall_task,
    repl_pool=None,
):
    """Process a single problem (agent → eval → PPA opt / arch explore).

    Thread-safe: all stats mutations go through _stats_lock.
    """
    # Acquire a REPL from the pool for this worker
    repl = repl_pool.acquire() if repl_pool is not None else None
    has_repl = repl is not None

    # Create a per-worker evaluator with this worker's REPL instance
    # (the shared evaluator is only used as a template for config)
    worker_evaluator = Evaluator(
        project_root=evaluator.project_root,
        enable_synth=evaluator.enable_synth,
        enable_pnr=evaluator.enable_pnr,
        enable_drc=evaluator.enable_drc,
        enable_lvs=evaluator.enable_lvs,
        enable_corners=evaluator.enable_corners,
        enable_gls=evaluator.enable_gls,
        lean_repl=repl,
        dataset=evaluator.dataset_name,
        dataset_obj=evaluator.dataset_obj,
        parameterized_ppa_runner=evaluator.parameterized_ppa_runner,
        ppa_cache_dir=evaluator.ppa_cache_dir,
        ppa_workers=evaluator.ppa_workers,
        universal_certifier_path=evaluator.universal_certifier_path,
        universal_certifier_sha256=evaluator.universal_certifier_sha256,
    )

    try:
        _process_one_problem_inner(
            prob_id, args, skill, worker_evaluator, run_dir, stats,
            overall_progress, current_progress, overall_task,
            repl, has_repl,
        )
    finally:
        # Always release the REPL back to the pool
        if repl is not None and repl_pool is not None:
            repl_pool.release(repl)


def _process_one_problem_inner(
    prob_id: str,
    args: argparse.Namespace,
    skill: str,
    evaluator: Evaluator,
    run_dir: Path,
    stats: dict,
    overall_progress: Progress,
    current_progress: Progress,
    overall_task,
    repl,
    has_repl: bool,
):
    problem_t0 = time.monotonic()
    # Check resume
    if args.resume:
        prev_results = list(run_dir.parent.glob("*/results.jsonl"))
        already_done = False
        for prev in prev_results:
            try:
                for line in prev.read_text().splitlines():
                    r = json.loads(line)
                    if r.get("prob_id") != prob_id:
                        continue
                    if args.resume_mode == "passed" and r.get("sim_status") == "sim_pass":
                        already_done = True
                    elif args.resume_mode == "completed" and not r.get("agent_error"):
                        already_done = True
                    if already_done:
                        break
            except Exception:
                pass
            if already_done:
                break

        if already_done:
            with _stats_lock:
                stats["skipped"] += 1
            overall_progress.advance(overall_task)
            return

    # Phase 1: Run coding agent
    agent_task = current_progress.add_task(
        f"[cyan]{prob_id}[/cyan]  Agent running...",
    )

    agent = CodingAgent(
        model=args.model,
        max_tokens=args.max_tokens,
        project_root=PROJECT_ROOT,
        log_dir=run_dir / "logs" / prob_id,
        lean_repl=repl,
    )

    # Load problem info from dataset
    info = evaluator.dataset_obj.load_problem(prob_id) if evaluator.dataset_obj else None
    benchmark_ports = (
        _benchmark_expected_ports(info) if info is not None else None
    )

    def evaluate_candidate(active_evaluator):
        if benchmark_ports is None:
            return active_evaluator.evaluate(prob_id, run_dir)
        return active_evaluator.evaluate(
            prob_id, run_dir, benchmark_ports=benchmark_ports
        )
    condition_sv, condition_sv_path = load_conditioning_sv(args.condition_sv_dir, prob_id)
    user_msg = build_user_message(
        prob_id,
        has_repl=has_repl,
        info=info,
        dataset_name=evaluator.dataset_name,
        condition_sv=condition_sv,
    )
    agent_stats = None
    agent_elapsed = 0.0
    try:
        agent_t0 = time.monotonic()
        agent_stats = agent.run(
            system_prompt=skill,
            user_message=user_msg,
            max_turns=args.max_turns,
            compact_history=not args.no_compact_history,
            history_window=args.history_window,
        )
        agent_elapsed = time.monotonic() - agent_t0
        with _stats_lock:
            stats["agent_tokens"]["input"] += agent_stats["input_tokens"]
            stats["agent_tokens"]["output"] += agent_stats["output_tokens"]
            stats["agent_completed"] += 1
            stats["agent_turns_total"] += agent_stats.get("turns", 0)
            stats["agent_compile_checks_total"] += agent_stats.get("compile_checks", 0)
            stats["agent_elapsed_total"] += agent_elapsed

        current_progress.update(
            agent_task,
            description=(
                f"[cyan]{prob_id}[/cyan]  Agent done "
                f"({agent_stats['turns']}t, "
                f"{agent_stats.get('compile_checks', 0)} checks, "
                f"{agent_stats['input_tokens']}+{agent_stats['output_tokens']} tok)  "
                f"Evaluating..."
            ),
        )
    except Exception as e:
        current_progress.update(
            agent_task,
            description=f"[red]{prob_id}[/red]  Agent error: {e}",
        )
        log_event(run_dir, {
            "prob_id": prob_id,
            "agent_error": str(e),
            "sim_status": "agent_error",
            "elapsed_seconds": round(time.monotonic() - problem_t0, 3),
            **classify_failure_record({"agent_error": str(e)}),
        })
        with _stats_lock:
            stats["agent_error"] += 1
        current_progress.remove_task(agent_task)
        overall_progress.advance(overall_task)
        return

    # Phase 2: Evaluate. With sim feedback, keep this phase light and defer
    # expensive backend checks until after the repair loop.
    needs_backend_eval = (
        evaluator.enable_synth
        or evaluator.enable_pnr
        or evaluator.enable_drc
        or evaluator.enable_lvs
        or evaluator.enable_gls
    )
    sim_evaluator = evaluator
    if args.sim_feedback and needs_backend_eval:
        sim_evaluator = Evaluator(
            project_root=evaluator.project_root,
            enable_synth=False,
            enable_pnr=False,
            enable_drc=False,
            enable_lvs=False,
            enable_corners=False,
            enable_gls=False,
            lean_repl=repl,
            dataset=evaluator.dataset_name,
            dataset_obj=evaluator.dataset_obj,
            universal_certifier_path=evaluator.universal_certifier_path,
            universal_certifier_sha256=evaluator.universal_certifier_sha256,
        )

    eval_t0 = time.monotonic()
    result = evaluate_candidate(sim_evaluator)
    eval_elapsed = time.monotonic() - eval_t0
    repair_stats_total = {
        "input_tokens": 0,
        "output_tokens": 0,
        "turns": 0,
        "compile_checks": 0,
    }
    repair_context_mode = "full_history" if args.full_history_repair else "compact"
    sim_feedback_history = []
    sim_feedback_turn_budget = 0
    sim_feedback_turns_remaining = 0

    # Phase 2a: optional Verilog-side compile/simulation feedback.
    if (
        args.sim_feedback
        and agent_stats is not None
        and result["sim_status"] != "sim_pass"
        and not result.get("terminal_capability_error")
    ):
        lean_file = GENERATED_DIR / f"{prob_id}.lean"
        messages = agent_stats.get("messages", []) if args.full_history_repair else []
        best_code = read_current_lean(prob_id)
        best_result = result
        sim_feedback_turn_budget = max(0, args.max_turns - agent_stats.get("turns", 0))
        sim_feedback_turns_remaining = sim_feedback_turn_budget

        if sim_feedback_turns_remaining <= 0:
            sim_feedback_history.append({
                "phase": "sim_feedback",
                "iteration": 0,
                "note": (
                    "Skipped: no remaining shared turn budget "
                    f"(used {agent_stats.get('turns', 0)}/{args.max_turns})."
                ),
            })

        sim_iter = 0
        non_improving_sim_repairs = 0
        while result["sim_status"] != "sim_pass" and sim_feedback_turns_remaining > 0:
            sim_iter += 1
            current_progress.update(
                agent_task,
                description=(
                    f"[cyan]{prob_id}[/cyan]  "
                    f"[yellow]Sim feedback {sim_iter}[/yellow] "
                    f"({sim_feedback_turns_remaining}t left)..."
                ),
            )

            current_code = read_current_lean(prob_id)
            feedback = build_sim_feedback(
                prob_id=prob_id,
                result=result,
                iteration=sim_iter - 1,
                history=sim_feedback_history,
                run_dir=run_dir,
                info=info,
            )
            repair_elapsed = 0.0
            try:
                repair_t0 = time.monotonic()
                if args.full_history_repair:
                    sim_repair_stats = agent.resume(
                        system_prompt=skill,
                        messages=messages,
                        feedback_message=feedback,
                        max_turns=sim_feedback_turns_remaining,
                        compact_history=not args.no_compact_history,
                        history_window=args.history_window,
                    )
                    messages = sim_repair_stats["messages"]
                    agent_stats["messages"] = messages
                else:
                    compact_prompt = build_compact_repair_prompt(
                        prob_id=prob_id,
                        info=info,
                        dataset_name=evaluator.dataset_name,
                        has_repl=has_repl,
                        phase="RTL simulation feedback",
                        iteration=sim_iter,
                        current_lean=current_code,
                        latest_feedback=feedback,
                        recent_attempts=sim_feedback_history,
                        extra_constraints="Use the Verilog compile/simulation feedback to repair the Lean source. The target outcome is RTL sim_pass.",
                    )
                    sim_repair_stats = agent.resume_compact(
                        system_prompt=skill,
                        compact_message=compact_prompt,
                        max_turns=sim_feedback_turns_remaining,
                        label=f"sim_feedback_iter_{sim_iter}",
                        compact_history=not args.no_compact_history,
                        history_window=args.history_window,
                    )
                repair_elapsed = time.monotonic() - repair_t0
                sim_repair_turns = sim_repair_stats.get("turns", 0)
                sim_feedback_turns_remaining = max(0, sim_feedback_turns_remaining - sim_repair_turns)
                with _stats_lock:
                    stats["agent_tokens"]["input"] += sim_repair_stats["input_tokens"]
                    stats["agent_tokens"]["output"] += sim_repair_stats["output_tokens"]
                    stats["agent_turns_total"] += sim_repair_turns
                    stats["agent_compile_checks_total"] += sim_repair_stats.get("compile_checks", 0)
                    stats["agent_elapsed_total"] += repair_elapsed
                    stats["sim_feedback_iterations_total"] += 1
                repair_stats_total["input_tokens"] += sim_repair_stats["input_tokens"]
                repair_stats_total["output_tokens"] += sim_repair_stats["output_tokens"]
                repair_stats_total["turns"] += sim_repair_turns
                repair_stats_total["compile_checks"] += sim_repair_stats.get("compile_checks", 0)
            except Exception as e:
                sim_feedback_history.append({
                    "phase": "sim_feedback",
                    "iteration": sim_iter,
                    "note": f"Agent error: {e}",
                })
                log_event(run_dir, {
                    "prob_id": prob_id,
                    "sim_feedback_iteration": sim_iter,
                    "sim_feedback_error": str(e),
                })
                break

            repair_eval_t0 = time.monotonic()
            new_result = evaluate_candidate(sim_evaluator)
            eval_elapsed += time.monotonic() - repair_eval_t0
            sim_feedback_history.append({
                "phase": "sim_feedback",
                "iteration": sim_iter,
                "note": "Candidate result after Verilog-side feedback repair.",
                "result_summary": summarize_eval_result(new_result),
                "repair_turns": sim_repair_turns,
                "remaining_turns": sim_feedback_turns_remaining,
                "repair_input_tokens": sim_repair_stats.get("input_tokens", 0),
                "repair_output_tokens": sim_repair_stats.get("output_tokens", 0),
                "repair_compile_checks": sim_repair_stats.get("compile_checks", 0),
                "repair_elapsed_seconds": round(repair_elapsed, 3),
            })

            log_event(run_dir, {
                "prob_id": prob_id,
                "sim_feedback_iteration": sim_iter,
                "sim_feedback_status": new_result.get("sim_status"),
                "sim_feedback_compile_pass": new_result.get("compile_pass"),
                "sim_feedback_lint_pass": new_result.get("lint_pass"),
                "sim_feedback_mismatches": new_result.get("sim_mismatches"),
                "sim_feedback_test_counts": extract_sim_test_counts(new_result.get("detail")),
                "sim_feedback_detail": truncate_text(new_result.get("detail", ""), 1000, keep="tail"),
                "sim_feedback_remaining_turns": sim_feedback_turns_remaining,
            })

            new_key = eval_progress_key(new_result)
            best_key = eval_progress_key(best_result)
            result = new_result
            if new_key > best_key:
                best_result = new_result
                best_code = read_current_lean(prob_id)
                non_improving_sim_repairs = 0
            else:
                non_improving_sim_repairs += 1

            if result["sim_status"] == "sim_pass":
                with _stats_lock:
                    stats["sim_feedback_success"] += 1
                break

            if non_improving_sim_repairs >= SIM_FEEDBACK_MAX_NON_IMPROVING:
                note = (
                    "Early stop: simulation feedback did not improve the best "
                    f"candidate for {non_improving_sim_repairs} consecutive repair attempts."
                )
                sim_feedback_history.append({
                    "phase": "sim_feedback",
                    "iteration": sim_iter,
                    "note": note,
                    "best_result_summary": summarize_eval_result(best_result),
                })
                log_event(run_dir, {
                    "prob_id": prob_id,
                    "sim_feedback_iteration": sim_iter,
                    "sim_feedback_early_stop": note,
                    "sim_feedback_best_key": list(eval_progress_key(best_result)),
                    "sim_feedback_current_key": list(eval_progress_key(result)),
                })
                break

        if eval_progress_key(best_result) > eval_progress_key(result) and lean_file.exists():
            lean_file.write_text(best_code)
            restore_eval_t0 = time.monotonic()
            result = evaluate_candidate(sim_evaluator)
            eval_elapsed += time.monotonic() - restore_eval_t0
            sim_feedback_history.append({
                "phase": "sim_feedback",
                "iteration": sim_iter,
                "note": "Restored the best simulation-feedback candidate and re-evaluated it.",
                "result_summary": summarize_eval_result(result),
            })

    if args.sim_feedback and needs_backend_eval and not result.get("terminal_capability_error"):
        current_progress.update(
            agent_task,
            description=f"[cyan]{prob_id}[/cyan]  Backend evaluation...",
        )
        backend_eval_t0 = time.monotonic()
        result = evaluate_candidate(evaluator)
        eval_elapsed += time.monotonic() - backend_eval_t0

    # Phase 2b: generation-time synthesis feedback.
    # This is separate from --ppa-opt: it only tries to repair designs that
    # already pass functional simulation but fail synthesis.
    synth_feedback_history = []
    if _should_run_synth_feedback(args, agent_stats, result):
        lean_file = GENERATED_DIR / f"{prob_id}.lean"
        messages = agent_stats.get("messages", [])
        best_code = lean_file.read_text() if lean_file.exists() else ""
        best_result = result

        def _synth_feedback_score(row: dict) -> tuple[int, int, int, int, int]:
            return (
                int(row.get("sim_status") == "sim_pass" and row.get("synth_pass")),
                int(row.get("sim_status") == "sim_pass"),
                int(bool(row.get("synth_pass"))),
                int(bool(row.get("compile_pass"))),
                int(bool(row.get("sv_extracted"))),
            )

        for synth_iter in range(args.synth_feedback_iters):
            current_progress.update(
                agent_task,
                description=(
                    f"[cyan]{prob_id}[/cyan]  "
                    f"[yellow]Synth feedback {synth_iter + 1}/{args.synth_feedback_iters}[/yellow]..."
                ),
            )

            feedback = build_synth_generation_feedback(
                prob_id, result, synth_iter, run_dir
            )
            repair_elapsed = 0.0
            try:
                repair_t0 = time.monotonic()
                repair_stats = agent.resume(
                    system_prompt=skill,
                    messages=messages,
                    feedback_message=feedback,
                    max_turns=args.synth_feedback_turns,
                    compact_history=not args.no_compact_history,
                    history_window=args.history_window,
                )
                repair_elapsed = time.monotonic() - repair_t0
                messages = repair_stats["messages"]
                agent_stats["messages"] = messages
                with _stats_lock:
                    stats["agent_tokens"]["input"] += repair_stats["input_tokens"]
                    stats["agent_tokens"]["output"] += repair_stats["output_tokens"]
                    stats["agent_turns_total"] += repair_stats.get("turns", 0)
                    stats["agent_compile_checks_total"] += repair_stats.get("compile_checks", 0)
                    stats["agent_elapsed_total"] += repair_elapsed
                    stats["synth_feedback_attempts"] += 1
                repair_stats_total["input_tokens"] += repair_stats["input_tokens"]
                repair_stats_total["output_tokens"] += repair_stats["output_tokens"]
                repair_stats_total["turns"] += repair_stats.get("turns", 0)
                repair_stats_total["compile_checks"] += repair_stats.get("compile_checks", 0)
            except Exception as e:
                log_event(run_dir, {
                    "prob_id": prob_id,
                    "synth_feedback_iteration": synth_iter + 1,
                    "synth_feedback_error": str(e),
                })
                break

            new_result = evaluate_candidate(evaluator)
            entry = {
                "iteration": synth_iter + 1,
                "compile_pass": new_result.get("compile_pass"),
                "sv_extracted": new_result.get("sv_extracted"),
                "lint_pass": new_result.get("lint_pass"),
                "sim_status": new_result.get("sim_status"),
                "synth_pass": new_result.get("synth_pass"),
                "synth_error": str(new_result.get("synth_error") or "")[:300],
                "repair_turns": repair_stats.get("turns", 0),
                "repair_input_tokens": repair_stats.get("input_tokens", 0),
                "repair_output_tokens": repair_stats.get("output_tokens", 0),
                "repair_compile_checks": repair_stats.get("compile_checks", 0),
                "repair_elapsed_seconds": round(repair_elapsed, 3),
            }
            synth_feedback_history.append(entry)
            log_event(run_dir, {
                "prob_id": prob_id,
                "synth_feedback_iteration": synth_iter + 1,
                **entry,
            })

            if _synth_feedback_score(new_result) > _synth_feedback_score(best_result):
                best_result = new_result
                best_code = lean_file.read_text() if lean_file.exists() else best_code

            result = new_result
            if result["sim_status"] == "sim_pass" and result.get("synth_pass"):
                with _stats_lock:
                    stats["synth_feedback_fixed"] += 1
                break

        if not (result["sim_status"] == "sim_pass" and result.get("synth_pass")):
            if best_code:
                lean_file.write_text(best_code)
            result = best_result

    # Phase 3: PPA optimization loop (only with --ppa-opt)
    ppa_history = []
    if (
        args.ppa_opt
        and result["sim_status"] == "sim_pass"
        and result.get("synth_pass")
    ):
        current_progress.update(
            agent_task,
            description=(
                f"[cyan]{prob_id}[/cyan]  "
                f"[yellow]PPA opt ({args.ppa_feedback_target})[/yellow]..."
            ),
        )

        if args.ppa_feedback_target == "verilog" and info is not None:
            result, ppa_history = run_verilog_ppa_loop(
                prob_id, info, args, evaluator, run_dir, result, stats
            )
        else:
            ppa_history.append(extract_ppa(result))
            lean_file = GENERATED_DIR / f"{prob_id}.lean"
            messages = agent_stats.get("messages", []) if args.full_history_repair else []
            repair_attempts: list[dict] = []
            best_code = lean_file.read_text()
            best_ppa = ppa_history[0]
            best_result = result

            for ppa_iter in range(args.ppa_iters):
                current_progress.update(
                    agent_task,
                    description=(
                        f"[cyan]{prob_id}[/cyan]  "
                        f"[yellow]PPA opt {ppa_iter + 1}/{args.ppa_iters}[/yellow]..."
                    ),
                )

                # Backup current Lean file for rollback.
                backup_code = lean_file.read_text()

                feedback = build_ppa_feedback(prob_id, result, ppa_iter, ppa_history)
                opt_elapsed = 0.0
                try:
                    opt_t0 = time.monotonic()
                    if args.full_history_repair:
                        opt_stats = agent.resume(
                            system_prompt=skill,
                            messages=messages,
                            feedback_message=feedback,
                            max_turns=args.ppa_turns,
                            compact_history=not args.no_compact_history,
                            history_window=args.history_window,
                        )
                        messages = opt_stats["messages"]
                    else:
                        compact_prompt = build_compact_repair_prompt(
                            prob_id=prob_id,
                            info=info,
                            dataset_name=evaluator.dataset_name,
                            has_repl=has_repl,
                            phase="PPA optimization",
                            iteration=ppa_iter + 1,
                            current_lean=backup_code,
                            latest_feedback=feedback,
                            recent_attempts=repair_attempts,
                            extra_constraints="Keep the previous simulation-passing behavior; rollbacks will discard candidates that fail RTL simulation.",
                        )
                        opt_stats = agent.resume_compact(
                            system_prompt=skill,
                            compact_message=compact_prompt,
                            max_turns=args.ppa_turns,
                            label=f"ppa_iter_{ppa_iter + 1}",
                            compact_history=not args.no_compact_history,
                            history_window=args.history_window,
                        )
                    opt_elapsed = time.monotonic() - opt_t0
                    with _stats_lock:
                        stats["agent_tokens"]["input"] += opt_stats["input_tokens"]
                        stats["agent_tokens"]["output"] += opt_stats["output_tokens"]
                        stats["agent_turns_total"] += opt_stats.get("turns", 0)
                        stats["agent_compile_checks_total"] += opt_stats.get("compile_checks", 0)
                        stats["agent_elapsed_total"] += opt_elapsed
                    repair_stats_total["input_tokens"] += opt_stats["input_tokens"]
                    repair_stats_total["output_tokens"] += opt_stats["output_tokens"]
                    repair_stats_total["turns"] += opt_stats.get("turns", 0)
                    repair_stats_total["compile_checks"] += opt_stats.get("compile_checks", 0)
                except Exception as e:
                    repair_attempts.append({
                        "phase": "PPA",
                        "iteration": ppa_iter + 1,
                        "note": f"Agent error: {e}",
                    })
                    log_event(run_dir, {
                        "prob_id": prob_id,
                        "ppa_iteration": ppa_iter + 1,
                        "ppa_target": "lean",
                        "ppa_error": str(e),
                    })
                    break

                # Re-evaluate
                new_result = evaluate_candidate(evaluator)
                repair_attempts.append({
                    "phase": "PPA",
                    "iteration": ppa_iter + 1,
                    "note": "Candidate result after compact repair and re-evaluation.",
                    "result_summary": summarize_eval_result(new_result),
                })

                # A no-sorry source is not automatically a universal theorem.
                verified = _has_kernel_checked_design_contract(new_result)

                # Check: functional correctness preserved?
                if new_result["sim_status"] != "sim_pass":
                    # Rollback to pre-iteration code and continue
                    lean_file.write_text(backup_code)
                    log_event(run_dir, {
                        "prob_id": prob_id,
                        "ppa_iteration": ppa_iter + 1,
                        "ppa_target": "lean",
                        "ppa_verified": verified,
                        "verification_evidence": new_result.get(
                            "verification_evidence", []
                        ),
                        "ppa_status": "rollback_sim_fail",
                        "ppa_elapsed_seconds": round(opt_elapsed, 3),
                        **extract_ppa(new_result),
                    })
                    continue

                # Check: PPA improved vs global best?
                new_ppa = extract_ppa(new_result)
                improved = ppa_improved(best_ppa, new_ppa)
                status = "improved" if improved else "converged"
                log_event(run_dir, {
                    "prob_id": prob_id,
                    "ppa_iteration": ppa_iter + 1,
                    "ppa_target": "lean",
                    "ppa_verified": verified,
                    "verification_evidence": new_result.get(
                        "verification_evidence", []
                    ),
                    "ppa_status": status,
                    "ppa_elapsed_seconds": round(opt_elapsed, 3),
                    **new_ppa,
                })

                ppa_history.append(new_ppa)
                with _stats_lock:
                    stats["ppa_iterations_total"] += 1
                if improved:
                    best_ppa = new_ppa
                    best_code = lean_file.read_text()
                    best_result = new_result

            # Restore global best code at the end
            lean_file.write_text(best_code)
            result = best_result

            if len(ppa_history) > 1:
                with _stats_lock:
                    stats["ppa_optimized"] += 1

    # Phase 3b: Architecture exploration loop (only with --arch-explore)
    arch_candidates = []
    if (
        args.arch_explore
        and not args.ppa_opt  # don't run both
        and result["sim_status"] == "sim_pass"
        and result.get("synth_pass")
    ):
        lean_file = GENERATED_DIR / f"{prob_id}.lean"
        messages = agent_stats.get("messages", []) if args.full_history_repair else []
        repair_attempts: list[dict] = []
        constraints = {
            "area_budget": args.area_budget,
            "latency_budget": args.latency_budget,
        }

        # Baseline candidate
        arch_candidates.append(ArchCandidate(
            index=0,
            code=lean_file.read_text(),
            ppa=extract_ppa(result),
            sim_pass=True,
            verified=_has_kernel_checked_design_contract(result),
            description="initial",
            verification_evidence=list(result.get("verification_evidence", [])),
        ))

        for arch_iter in range(args.arch_candidates):
            current_progress.update(
                agent_task,
                description=(
                    f"[cyan]{prob_id}[/cyan]  "
                    f"[blue]Arch {arch_iter + 1}/{args.arch_candidates}[/blue]..."
                ),
            )

            feedback = build_arch_feedback(prob_id, arch_candidates, arch_iter, constraints)
            try:
                if args.full_history_repair:
                    opt_stats = agent.resume(
                        system_prompt=skill,
                        messages=messages,
                        feedback_message=feedback,
                        max_turns=args.arch_turns,
                        compact_history=not args.no_compact_history,
                        history_window=args.history_window,
                    )
                    messages = opt_stats["messages"]
                else:
                    compact_prompt = build_compact_repair_prompt(
                        prob_id=prob_id,
                        info=info,
                        dataset_name=evaluator.dataset_name,
                        has_repl=has_repl,
                        phase="architecture exploration",
                        iteration=arch_iter + 1,
                        current_lean=read_current_lean(prob_id),
                        latest_feedback=feedback,
                        recent_attempts=repair_attempts,
                        extra_constraints="Produce a meaningfully different architecture, but keep the same externally visible behavior and interface.",
                    )
                    opt_stats = agent.resume_compact(
                        system_prompt=skill,
                        compact_message=compact_prompt,
                        max_turns=args.arch_turns,
                        label=f"arch_iter_{arch_iter + 1}",
                        compact_history=not args.no_compact_history,
                        history_window=args.history_window,
                    )
                with _stats_lock:
                    stats["agent_tokens"]["input"] += opt_stats["input_tokens"]
                    stats["agent_tokens"]["output"] += opt_stats["output_tokens"]
                    stats["agent_turns_total"] += opt_stats.get("turns", 0)
                    stats["agent_compile_checks_total"] += opt_stats.get("compile_checks", 0)
                repair_stats_total["input_tokens"] += opt_stats["input_tokens"]
                repair_stats_total["output_tokens"] += opt_stats["output_tokens"]
                repair_stats_total["turns"] += opt_stats.get("turns", 0)
                repair_stats_total["compile_checks"] += opt_stats.get("compile_checks", 0)
            except Exception as e:
                repair_attempts.append({
                    "phase": "arch",
                    "iteration": arch_iter + 1,
                    "note": f"Agent error: {e}",
                })
                log_event(run_dir, {
                    "prob_id": prob_id,
                    "arch_iteration": arch_iter + 1,
                    "arch_error": str(e),
                })
                break

            new_result = evaluate_candidate(evaluator)
            new_candidate = ArchCandidate(
                index=arch_iter + 1,
                code=lean_file.read_text(),
                ppa=extract_ppa(new_result),
                sim_pass=new_result["sim_status"] == "sim_pass",
                verified=_has_kernel_checked_design_contract(new_result),
                description=f"candidate_{arch_iter + 1}",
                verification_evidence=list(
                    new_result.get("verification_evidence", [])
                ),
            )
            arch_candidates.append(new_candidate)
            repair_attempts.append({
                "phase": "arch",
                "iteration": arch_iter + 1,
                "note": "Architecture candidate result after repair and re-evaluation.",
                "result_summary": summarize_eval_result(new_result),
            })

            log_event(run_dir, {
                "prob_id": prob_id,
                "arch_iteration": arch_iter + 1,
                "arch_sim_pass": new_candidate.sim_pass,
                "arch_verified": new_candidate.verified,
                "verification_evidence": new_candidate.verification_evidence,
                **new_candidate.ppa,
            })

        # Select best and restore
        best = select_best_candidate(arch_candidates, constraints)
        if best.index != arch_candidates[-1].index:
            lean_file.write_text(best.code)
            result = evaluate_candidate(evaluator)
        else:
            result = new_result  # already the latest

        with _stats_lock:
            stats["arch_explored"] += 1
            stats["arch_candidates_total"] += len(arch_candidates)

    status_icon = {
        "sim_pass": "[green]✓[/green]",
        "sim_fail": "[red]✗[/red]",
        "sim_error": "[yellow]![/yellow]",
        "not_run": "–",
    }.get(result["sim_status"], "?")

    compile_str = "[green]C✓[/green]" if result["compile_pass"] else "[red]C✗[/red]"
    lint_str = "[green]L✓[/green]" if result["lint_pass"] else "[red]L✗[/red]"
    detail_short = result["detail"][:50]

    # Build synth/PPA suffix
    synth_str = ""
    if args.synth and result.get("parameterized_ppa_unsupported"):
        with _stats_lock:
            stats["parameterized_ppa_skipped"] += 1
        synth_str = "  [yellow]S skipped (parameter sweep)[/yellow]"
    elif args.synth and result.get("synth_pass"):
        with _stats_lock:
            stats["synth_pass"] += 1
        ppa_parts = []
        if result.get("area_um2") is not None:
            ppa_parts.append(f"A={result['area_um2']:.0f}")
        if result.get("cell_count") is not None:
            ppa_parts.append(f"C={result['cell_count']}")
        if result.get("wns_ns") is not None:
            ppa_parts.append(f"WNS={result['wns_ns']:.2f}")
        synth_str = "  [green]S✓[/green]" + (f" {' '.join(ppa_parts)}" if ppa_parts else "")
    elif args.synth and result.get("sv_extracted"):
        synth_str = "  [red]S✗[/red]"

    # Build PNR/DRC/LVS suffix
    pnr_str = ""
    if args.pnr or args.drc or args.lvs:
        if result.get("pnr_pass"):
            with _stats_lock:
                stats["pnr_pass"] += 1
            pnr_str += "  [green]P✓[/green]"
        elif result.get("synth_pass"):
            pnr_str += "  [red]P✗[/red]"
        if args.drc:
            if result.get("drc_pass"):
                with _stats_lock:
                    stats["drc_pass"] += 1
                drc_v = result.get("drc_violations", 0)
                pnr_str += f"  [green]D✓({drc_v})[/green]"
            elif result.get("pnr_pass"):
                pnr_str += "  [red]D✗[/red]"
        if args.lvs:
            if result.get("lvs_pass"):
                with _stats_lock:
                    stats["lvs_pass"] += 1
                pnr_str += "  [green]V✓[/green]"
            elif result.get("lvs_error"):
                pnr_str += f"  [yellow]V?[/yellow]"
            elif result.get("pnr_pass"):
                pnr_str += "  [red]V✗[/red]"

    # Build GLS suffix
    gls_str = ""
    if args.gls:
        gs = result.get("gls_synth_status", "not_run")
        if gs == "sim_pass":
            with _stats_lock:
                stats["gls_synth_pass"] += 1
            gls_str += "  [green]GS✓[/green]"
        elif gs in ("sim_fail", "sim_error"):
            gls_str += "  [red]GS✗[/red]"
        gp = result.get("gls_pnr_status", "not_run")
        if gp == "sim_pass":
            with _stats_lock:
                stats["gls_pnr_pass"] += 1
            gls_str += "  [green]GP✓[/green]"
        elif gp in ("sim_fail", "sim_error"):
            gls_str += "  [red]GP✗[/red]"

    current_progress.update(
        agent_task,
        description=(
            f"{status_icon} [cyan]{prob_id}[/cyan]  "
            f"{compile_str} {lint_str}  "
            f"{result['sim_status']}  {detail_short}{synth_str}{gls_str}{pnr_str}"
        ),
    )

    with _stats_lock:
        if result["compile_pass"]:
            stats["compile_pass"] += 1
        if result["sim_status"] == "sim_pass":
            stats["sim_pass"] += 1
        elif result["sim_status"] == "sim_fail":
            stats["sim_fail"] += 1
        elif result["sim_status"] == "not_run":
            stats["sim_not_run"] += 1
        else:
            stats["sim_error"] += 1
        stats["problem_elapsed_total"] += time.monotonic() - problem_t0

    # Log result
    arch_history = None
    arch_history_with_best = None
    if arch_candidates:
        arch_history = [
            {
                "index": c.index,
                "description": c.description,
                "sim_pass": c.sim_pass,
                "verified": c.verified,
                "verification_evidence": c.verification_evidence,
                **c.ppa,
            }
            for c in arch_candidates
        ]
        # Mark which candidate was selected
        best = select_best_candidate(arch_candidates, {
            "area_budget": args.area_budget,
            "latency_budget": args.latency_budget,
        })
        arch_history_with_best = {
            "candidates": arch_history,
            "selected": best.index,
        }
    log_event(run_dir, {
        "prob_id": prob_id,
        "agent_turns": agent_stats.get("turns", 0) if agent_stats else 0,
        "agent_input_tokens": agent_stats.get("input_tokens", 0) if agent_stats else 0,
        "agent_output_tokens": agent_stats.get("output_tokens", 0) if agent_stats else 0,
        "agent_compile_checks": agent_stats.get("compile_checks", 0) if agent_stats else 0,
        "agent_tool_counts": agent_stats.get("tool_counts", {}) if agent_stats else {},
        "compact_history": not args.no_compact_history,
        "history_window": args.history_window,
        "repair_context_mode": repair_context_mode,
        "repair_input_tokens": repair_stats_total["input_tokens"],
        "repair_output_tokens": repair_stats_total["output_tokens"],
        "repair_turns": repair_stats_total["turns"],
        "repair_compile_checks": repair_stats_total["compile_checks"],
        "sv_conditioning_enabled": bool(args.condition_sv_dir),
        "sv_conditioning_path": condition_sv_path,
        "sim_feedback_enabled": args.sim_feedback,
        "sim_feedback_turn_budget": sim_feedback_turn_budget if args.sim_feedback else None,
        "sim_feedback_turns_remaining": sim_feedback_turns_remaining if args.sim_feedback else None,
        "sim_feedback_history": sim_feedback_history if sim_feedback_history else None,
        "agent_turn_budget": args.max_turns,
        "agent_turns_total": (
            (agent_stats.get("turns", 0) if agent_stats else 0)
            + repair_stats_total["turns"]
        ),
        "agent_elapsed_seconds": round(agent_elapsed, 3),
        "eval_elapsed_seconds": round(eval_elapsed, 3),
        "elapsed_seconds": round(time.monotonic() - problem_t0, 3),
        "synth_feedback_history": synth_feedback_history if synth_feedback_history else None,
        "ppa_history": ppa_history if ppa_history else None,
        "arch_history": arch_history_with_best if arch_candidates else None,
        **classify_failure_record(result),
        **result,
    })

    overall_progress.advance(overall_task)


def _apply_implied_stage_flags(args: argparse.Namespace) -> argparse.Namespace:
    """Normalize CLI stage dependencies before display, execution, and summary."""
    if args.corners:
        args.pnr = True
    if (
        args.synth_feedback
        or args.ppa_opt
        or args.arch_explore
        or args.gls
        or args.pnr
        or args.drc
        or args.lvs
    ):
        args.synth = True
    return args


def _prepare_universal_certifier(project_root: Path) -> tuple[Path, str | None]:
    """Build and fingerprint the trusted certifier before candidate code runs."""
    executable = project_root / ".lake" / "build" / "bin" / "sparkle-certify"
    try:
        completed = subprocess.run(
            ["lake", "build", "sparkle-certify"],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=180,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        console.print(
            f"[yellow]Universal theorem evidence disabled:[/yellow] "
            f"could not build trusted sparkle-certify ({error})."
        )
        return project_root / ".lake" / "build" / "disabled-sparkle-certify", None
    if completed.returncode != 0 or not executable.is_file():
        detail = (completed.stderr or completed.stdout).strip().splitlines()
        suffix = f" ({detail[-1]})" if detail else ""
        console.print(
            "[yellow]Universal theorem evidence disabled:[/yellow] "
            f"trusted sparkle-certify did not build{suffix}."
        )
        return project_root / ".lake" / "build" / "disabled-sparkle-certify", None
    try:
        digest = hashlib.sha256(executable.read_bytes()).hexdigest()
    except OSError as error:
        console.print(
            "[yellow]Universal theorem evidence disabled:[/yellow] "
            f"could not fingerprint sparkle-certify ({error})."
        )
        return project_root / ".lake" / "build" / "disabled-sparkle-certify", None
    return executable, digest


def main():
    args = _apply_implied_stage_flags(parse_args())
    t0 = time.monotonic()

    if args.sim_feedback and (args.sim_feedback_iters is not None or args.sim_feedback_turns is not None):
        console.print(
            "[yellow]Note:[/yellow] --sim-feedback-iters/--sim-feedback-turns are deprecated "
            "and ignored; --sim-feedback shares the main --max-turns budget."
        )

    # Discover problems
    ds = Dataset(args.dataset, project_root=PROJECT_ROOT)
    if args.problem_file:
        problem_path = Path(args.problem_file)
        problems = [
            line.strip()
            for line in problem_path.read_text().splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        if args.filter:
            pattern = re.compile(args.filter)
            problems = [pid for pid in problems if pattern.search(pid)]
        if args.limit:
            problems = problems[:args.limit]
    else:
        problems = ds.discover_problems(limit=args.limit, filter_re=args.filter)
    if not problems:
        console.print("[red]No problems found matching criteria.[/red]")
        sys.exit(1)

    synth_str = ", [magenta]synth+PPA[/magenta]" if args.synth else ""
    sim_feedback_str = ", [yellow]sim-feedback(shared-turns)[/yellow]" if args.sim_feedback else ""
    synth_feedback_str = f", [yellow]synth-feedback({args.synth_feedback_iters}x)[/yellow]" if args.synth_feedback else ""
    ppa_opt_str = (
        f", [yellow]PPA-opt({args.ppa_iters}x,{args.ppa_feedback_target})[/yellow]"
        if args.ppa_opt else ""
    )
    arch_str = f", [blue]arch-explore({args.arch_candidates}x)[/blue]" if args.arch_explore else ""
    history_str = ", [green]compact-history[/green]"
    if args.no_compact_history:
        history_str = ", [yellow]full-history[/yellow]"
    repair_str = ""
    if args.sim_feedback or args.ppa_opt or args.arch_explore:
        repair_str = ", [green]compact-repair[/green]"
        if args.full_history_repair:
            repair_str = ", [yellow]full-history-repair[/yellow]"
    pnr_str = ""
    if args.pnr or args.drc or args.lvs:
        parts = ["P&R"]
        if args.drc:
            parts.append("DRC")
        if args.lvs:
            parts.append("LVS")
        pnr_str = f", [cyan]{'+'.join(parts)}[/cyan]"
    console.print(f"Found [cyan]{len(problems)}[/cyan] problems, model: [cyan]{args.model}[/cyan]{synth_str}{sim_feedback_str}{synth_feedback_str}{ppa_opt_str}{arch_str}{history_str}{repair_str}{pnr_str}\n")

    # Set up results directory
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    results_base = Path(args.results_dir).resolve() if args.results_dir else RESULTS_DIR
    run_dir = results_base / f"agent_run_{timestamp}"
    run_dir.mkdir(parents=True, exist_ok=True)

    # Ensure Generated/ exists
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)

    # Load skill prompt
    skill = load_skill()

    # This executable is compiled before any generated candidate is checked.
    # Evaluators pin its digest and fail closed if candidate code changes it.
    universal_certifier_path, universal_certifier_sha256 = (
        _prepare_universal_certifier(PROJECT_ROOT)
    )

    # Create evaluator (REPL will be set per-worker below)
    evaluator = Evaluator(
        project_root=PROJECT_ROOT,
        enable_synth=args.synth,
        enable_pnr=args.pnr,
        enable_drc=args.drc,
        enable_lvs=args.lvs,
        enable_corners=args.corners,
        enable_gls=args.gls,
        dataset=args.dataset,
        dataset_obj=ds,
        ppa_cache_dir=(
            Path(args.ppa_cache_dir).resolve()
            if args.ppa_cache_dir else None
        ),
        ppa_workers=args.ppa_workers,
        universal_certifier_path=universal_certifier_path,
        universal_certifier_sha256=universal_certifier_sha256,
    )
    if args.synth:
        # One shared executor provides a true global concurrency limit across
        # all problem workers and enables cross-evaluator single-flight.
        evaluator.prepare_parameterized_ppa_runner()

    # Track stats
    stats = {
        "total": len(problems),
        "compile_pass": 0,
        "sim_pass": 0,
        "sim_fail": 0,
        "sim_error": 0,
        "sim_not_run": 0,
        "agent_error": 0,
        "synth_pass": 0,
        "parameterized_ppa_skipped": 0,
        "pnr_pass": 0,
        "drc_pass": 0,
        "lvs_pass": 0,
        "gls_synth_pass": 0,
        "gls_pnr_pass": 0,
        "synth_feedback_attempts": 0,
        "synth_feedback_fixed": 0,
        "sim_feedback_success": 0,
        "sim_feedback_iterations_total": 0,
        "ppa_optimized": 0,
        "ppa_iterations_total": 0,
        "arch_explored": 0,
        "arch_candidates_total": 0,
        "skipped": 0,
        "agent_tokens": {"input": 0, "output": 0},
        "agent_completed": 0,
        "agent_turns_total": 0,
        "agent_compile_checks_total": 0,
        "agent_elapsed_total": 0.0,
        "problem_elapsed_total": 0.0,
        "model": args.model,
    }

    # ── Progress bars ────────────────────────────────────────────
    overall_progress = Progress(
        SpinnerColumn(),
        MofNCompleteColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
    )

    current_progress = Progress(
        SpinnerColumn(),
        TimeElapsedColumn(),
        TextColumn("[progress.description]{task.description}"),
    )

    num_workers = max(1, args.workers)
    console.print(f"Workers: [cyan]{num_workers}[/cyan]")

    # Create REPL pool (unless --no-repl)
    repl_pool = None
    if not args.no_repl:
        try:
            console.print(f"[cyan]Starting Lean REPL pool ({num_workers} instances)...[/cyan]")
            repl_pool = LeanREPLPool(
                size=num_workers,
                project_dir=PROJECT_ROOT,
            )
            console.print(f"[green]REPL pool ready — agent will use lean_check (~0.1s per check)[/green]\n")
        except Exception as e:
            console.print(f"[yellow]REPL pool init failed: {e}[/yellow]")
            console.print(f"[yellow]Falling back to lake build[/yellow]\n")
            repl_pool = None
    else:
        console.print(f"[yellow]REPL disabled (--no-repl), using lake build[/yellow]\n")

    with Live(Group(overall_progress, current_progress), console=console, refresh_per_second=4):
        overall_task = overall_progress.add_task(
            "Running trials...", total=len(problems)
        )

        def _worker(prob_id: str):
            _process_one_problem(
                prob_id, args, skill, evaluator, run_dir, stats,
                overall_progress, current_progress, overall_task,
                repl_pool=repl_pool,
            )

        with ThreadPoolExecutor(max_workers=num_workers) as executor:
            futures = [executor.submit(_worker, pid) for pid in problems]
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as exc:
                    console.print(f"[red]Worker exception: {exc}[/red]")

    if evaluator.parameterized_ppa_runner is not None:
        evaluator.parameterized_ppa_runner.close()

    # ── Cleanup REPL pool ──────────────────────────────────────────
    if repl_pool is not None:
        repl_pool.close_all()

    # ── Summary ──────────────────────────────────────────────────
    elapsed = time.monotonic() - t0
    attempted = stats["total"] - stats["skipped"]
    sim_rate = f"{stats['sim_pass']/attempted*100:.1f}%" if attempted > 0 else "N/A"
    compile_rate = f"{stats['compile_pass']/attempted*100:.1f}%" if attempted > 0 else "N/A"
    failure_summary = summarize_failure_breakdown(run_dir)

    summary = {
        "dataset": args.dataset,
        "filter": args.filter,
        "total": stats["total"],
        "attempted": attempted,
        "skipped": stats["skipped"],
        "compile_pass": stats["compile_pass"],
        "compile_rate": compile_rate,
        "sim_pass": stats["sim_pass"],
        "sim_fail": stats["sim_fail"],
        "sim_error": stats["sim_error"],
        "sim_not_run": stats["sim_not_run"],
        "agent_error": stats["agent_error"],
        "sim_rate": sim_rate,
        "synth_pass": stats["synth_pass"],
        "parameterized_ppa_skipped": stats["parameterized_ppa_skipped"],
        "pnr_pass": stats["pnr_pass"],
        "drc_pass": stats["drc_pass"],
        "lvs_pass": stats["lvs_pass"],
        "gls_synth_pass": stats["gls_synth_pass"],
        "gls_pnr_pass": stats["gls_pnr_pass"],
        "gls_enabled": args.gls,
        "sim_feedback_enabled": args.sim_feedback,
        "sim_feedback_success": stats["sim_feedback_success"],
        "sim_feedback_iterations_total": stats["sim_feedback_iterations_total"],
        "sim_feedback_turn_budget": "shared_max_turns" if args.sim_feedback else None,
        "synth_feedback_enabled": args.synth_feedback,
        "synth_feedback_attempts": stats["synth_feedback_attempts"],
        "synth_feedback_fixed": stats["synth_feedback_fixed"],
        "synth_feedback_iters": args.synth_feedback_iters,
        "synth_feedback_turns": args.synth_feedback_turns,
        "ppa_optimized": stats["ppa_optimized"],
        "ppa_iterations_total": stats["ppa_iterations_total"],
        "agent_tokens": stats["agent_tokens"],
        "agent_completed": stats["agent_completed"],
        "avg_input_tokens": round(stats["agent_tokens"]["input"] / stats["agent_completed"], 2) if stats["agent_completed"] else 0,
        "avg_output_tokens": round(stats["agent_tokens"]["output"] / stats["agent_completed"], 2) if stats["agent_completed"] else 0,
        "avg_total_tokens": round((stats["agent_tokens"]["input"] + stats["agent_tokens"]["output"]) / stats["agent_completed"], 2) if stats["agent_completed"] else 0,
        "avg_agent_turns": round(stats["agent_turns_total"] / stats["agent_completed"], 2) if stats["agent_completed"] else 0,
        "avg_compile_checks": round(stats["agent_compile_checks_total"] / stats["agent_completed"], 2) if stats["agent_completed"] else 0,
        "avg_agent_elapsed_seconds": round(stats["agent_elapsed_total"] / stats["agent_completed"], 2) if stats["agent_completed"] else 0,
        "avg_problem_elapsed_seconds": round(stats["problem_elapsed_total"] / stats["agent_completed"], 2) if stats["agent_completed"] else 0,
        "elapsed_seconds": int(elapsed),
        "model": args.model,
        "compact_history": not args.no_compact_history,
        "history_window": args.history_window,
        "repair_context_mode": "full_history" if args.full_history_repair else "compact",
        "synth_enabled": args.synth,
        "pnr_enabled": args.pnr or args.drc or args.lvs,
        "drc_enabled": args.drc,
        "lvs_enabled": args.lvs,
        "ppa_opt_enabled": args.ppa_opt,
        "ppa_feedback_target": args.ppa_feedback_target,
        "arch_explore_enabled": args.arch_explore,
        "arch_explored": stats["arch_explored"],
        "arch_candidates_total": stats["arch_candidates_total"],
        **failure_summary,
    }
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2))

    print_summary_table(stats, elapsed, synth_enabled=args.synth, pnr_enabled=args.pnr or args.drc or args.lvs, drc_enabled=args.drc, lvs_enabled=args.lvs, gls_enabled=args.gls, ppa_opt_enabled=args.ppa_opt, arch_explore_enabled=args.arch_explore, synth_feedback_enabled=args.synth_feedback)

    # Auto-generate HTML report
    report_path = generate_report(run_dir)
    console.print(f"\nReport: [cyan]{report_path}[/cyan]")
    console.print(f"Results: [cyan]{run_dir}[/cyan]")


if __name__ == "__main__":
    main()
