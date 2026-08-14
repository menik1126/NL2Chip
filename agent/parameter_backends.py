"""Fail-closed backend policies for native parameterized designs."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from typing import Any, Callable
from pathlib import Path

from cvdp_specialization import FiniteParameterPlan


FORMAL_POLICIES = {"off", "auto", "generic", "per_configuration"}
CPPSIM_POLICIES = {"off", "per_configuration"}
PPA_POLICIES = {"off", "per_configuration"}


def _lean_declaration_header(source: str, name: str) -> str | None:
    match = re.search(
        rf"\b(?:theorem|lemma)\s+{re.escape(name)}\b(?P<header>[\s\S]*?)(?::=\s*by|:=|\bwhere\b)",
        str(source or ""),
    )
    if not match:
        return None
    return match.group(0)


def _render_case_theorem(template: str, values: dict[str, int]) -> str:
    try:
        return template.format(**values)
    except (KeyError, ValueError) as exc:
        raise ValueError(f"invalid formal case theorem template: {exc}") from exc


def evaluate_formal_parameter_policy(
    *,
    lean_source: str,
    lean_complete: bool,
    plan: FiniteParameterPlan,
    requested_policy: str = "auto",
    contract: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate proof coverage without confusing typecheck with correctness.

    ``contract`` is supplied by a benchmark/integration layer that owns the
    formal specification. A generated Lean definition with no theorem is not a
    functional proof and therefore never receives family coverage.
    """
    if requested_policy not in FORMAL_POLICIES:
        raise ValueError(f"unknown formal parameter policy: {requested_policy}")

    contract = dict(contract or {})
    scope = str(contract.get("scope") or "functional_correctness")
    required = bool(contract.get("required", False))
    generic_theorem = str(contract.get("generic_theorem") or "").strip()
    case_template = str(contract.get("case_theorem_template") or "").strip()
    policy = requested_policy
    if policy == "auto":
        if generic_theorem:
            policy = "generic"
        elif case_template:
            policy = "per_configuration"
        else:
            policy = "unavailable"

    manifest: dict[str, Any] = {
        "schema_version": 1,
        "backend": "formal",
        "requested_policy": requested_policy,
        "effective_policy": policy,
        "scope": scope,
        "required": required,
        "status": "not_run",
        "coverage": "none",
        "family_covered": False,
        "lean_file_complete": bool(lean_complete),
        "cases": [
            {"parameters": case.values, "formal_status": "not_run"}
            for case in plan.cases
        ],
        "diagnostics": [],
    }

    if requested_policy == "off":
        manifest["diagnostics"].append("formal parameter checking explicitly disabled")
        return manifest
    if policy == "unavailable":
        manifest["status"] = "unsupported"
        manifest["diagnostics"].append(
            "no public formal specification/proof obligation was supplied; "
            "Lean elaboration alone is not functional correctness evidence"
        )
        return manifest
    if not lean_complete or re.search(r"\b(?:sorry|admit)\b", lean_source):
        manifest["status"] = "incomplete"
        manifest["diagnostics"].append(
            "Lean source contains unresolved proof obligations; formal coverage is zero"
        )
        return manifest

    if policy == "generic":
        if not generic_theorem:
            manifest["status"] = "unsupported"
            manifest["diagnostics"].append(
                "generic formal policy requires `generic_theorem` in the formal contract"
            )
            return manifest
        header = _lean_declaration_header(lean_source, generic_theorem)
        if header is None:
            manifest["status"] = "incomplete"
            manifest["diagnostics"].append(
                f"required generic theorem `{generic_theorem}` is missing"
            )
            return manifest
        missing_parameters = [
            name for name in plan.parameter_names
            if not re.search(rf"\b{re.escape(name)}\b", header)
        ]
        if missing_parameters:
            manifest["status"] = "incomplete"
            manifest["diagnostics"].append(
                f"theorem `{generic_theorem}` does not quantify/reference parameter(s): "
                + ", ".join(missing_parameters)
            )
            return manifest
        manifest.update({
            "status": "passed",
            "coverage": "full_parameter_family",
            "family_covered": True,
            "generic_theorem": generic_theorem,
        })
        for row in manifest["cases"]:
            row.update({
                "formal_status": "covered_by_generic_theorem",
                "theorem": generic_theorem,
            })
        return manifest

    if policy != "per_configuration":
        raise ValueError(f"unsupported effective formal policy: {policy}")
    if not case_template:
        manifest["status"] = "unsupported"
        manifest["diagnostics"].append(
            "per-configuration formal policy requires `case_theorem_template`"
        )
        return manifest

    passed = 0
    for row in manifest["cases"]:
        try:
            theorem = _render_case_theorem(case_template, row["parameters"])
        except ValueError as exc:
            manifest["status"] = "unsupported"
            manifest["diagnostics"].append(str(exc))
            return manifest
        row["theorem"] = theorem
        if _lean_declaration_header(lean_source, theorem) is not None:
            row["formal_status"] = "passed"
            passed += 1
        else:
            row["formal_status"] = "missing"

    if passed == len(manifest["cases"]):
        manifest.update({
            "status": "passed",
            "coverage": "all_public_configurations",
            "family_covered": True,
        })
    else:
        manifest.update({
            "status": "incomplete",
            "coverage": "partial_configuration_set" if passed else "none",
        })
        manifest["diagnostics"].append(
            f"formal proofs cover {passed}/{len(manifest['cases'])} public configurations"
        )
    return manifest


def formal_policy_is_required_failure(manifest: dict[str, Any]) -> bool:
    """Return whether an explicitly required formal contract failed closed."""
    explicit = manifest.get("requested_policy") in {"generic", "per_configuration"}
    return bool(manifest.get("required") or explicit) and manifest.get("status") != "passed"


def _cpp_identifier(name: str) -> str:
    identifier = re.sub(r"[^A-Za-z0-9_]", "_", str(name))
    if not identifier or identifier[0].isdigit():
        identifier = "design_" + identifier
    return identifier


def _case_slug(parameters: dict[str, int]) -> str:
    parts = []
    for name, value in parameters.items():
        rendered = f"neg_{abs(value)}" if value < 0 else str(value)
        parts.append(f"{_cpp_identifier(name)}_{rendered}")
    return "__".join(parts) or "default"


def run_cppsim_parameter_policy(
    *,
    project_root: Path,
    lean_file: Path,
    target_name: str,
    case_requests: list[dict[str, Any]],
    output_dir: Path,
    requested_policy: str = "per_configuration",
    required: bool = False,
    timeout_s: int = 120,
) -> dict[str, Any]:
    """Generate, compile, and smoke-run one concrete CppSim per case."""
    if requested_policy not in CPPSIM_POLICIES:
        raise ValueError(f"unknown CppSim parameter policy: {requested_policy}")
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "backend": "cppsim",
        "requested_policy": requested_policy,
        "effective_policy": requested_policy,
        "scope": "concrete_backend_build_and_reset_eval_tick_smoke",
        "required": required,
        "status": "not_run",
        "coverage": "none",
        "family_covered": False,
        "cases": [],
        "diagnostics": [],
    }
    if requested_policy == "off":
        manifest["diagnostics"].append("CppSim parameter checking explicitly disabled")
        return manifest

    lake = shutil.which("lake")
    compiler = shutil.which("g++") or shutil.which("clang++")
    if lake is None or compiler is None:
        manifest["status"] = "unsupported"
        manifest["diagnostics"].append(
            "CppSim policy requires both lake and a C++ compiler"
        )
        return manifest

    source = lean_file.read_text(errors="replace")
    passed = unsupported = failed = 0
    for request in case_requests:
        parameters = {
            str(name): int(value)
            for name, value in dict(request.get("parameters") or {}).items()
        }
        slug = _case_slug(parameters)
        case_dir = output_dir / slug
        case_dir.mkdir(parents=True, exist_ok=True)
        row: dict[str, Any] = {
            "parameters": parameters,
            "cppsim_status": "not_run",
        }
        unsupported_reason = str(request.get("unsupported_reason") or "").strip()
        if unsupported_reason:
            row.update({
                "cppsim_status": "unsupported",
                "detail": unsupported_reason,
            })
            manifest["cases"].append(row)
            unsupported += 1
            continue

        header = case_dir / "design_cppsim.h"
        bindings = ", ".join(f"{name} := {value}" for name, value in parameters.items())
        command = (
            f"#writeParameterizedCppSimDesign {target_name} "
            f"[{bindings}] {json.dumps(str(header))}"
        )
        case_lean = case_dir / "emit_cppsim.lean"
        case_lean.write_text(source.rstrip() + "\n\n" + command + "\n", encoding="utf-8")
        try:
            emit = subprocess.run(
                [lake, "env", "lean", str(case_lean)],
                cwd=project_root,
                capture_output=True,
                text=True,
                timeout=timeout_s,
            )
        except subprocess.TimeoutExpired:
            row.update({
                "cppsim_status": "failed",
                "failure_stage": "lean_specialization",
                "detail": "CppSim Lean specialization timeout",
            })
            manifest["cases"].append(row)
            failed += 1
            continue
        emit_output = (emit.stdout or "") + (emit.stderr or "")
        (case_dir / "lean_output.txt").write_text(emit_output, encoding="utf-8")
        if emit.returncode != 0 or "PANIC at" in emit_output or not header.exists():
            row.update({
                "cppsim_status": "failed",
                "failure_stage": "lean_specialization",
                "detail": emit_output[-3000:] or "CppSim header was not generated",
            })
            manifest["cases"].append(row)
            failed += 1
            continue

        driver = case_dir / "smoke.cpp"
        binary = case_dir / "smoke"
        class_name = _cpp_identifier(target_name)
        driver.write_text(
            f'#include "{header.name}"\n'
            f"int main() {{ {class_name} design; design.reset(); design.eval(); "
            "design.tick(); return 0; }\n",
            encoding="utf-8",
        )
        try:
            compile_result = subprocess.run(
                [compiler, "-std=c++17", "-O0", str(driver), "-o", str(binary)],
                cwd=case_dir,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except subprocess.TimeoutExpired:
            compile_result = None
        if compile_result is None or compile_result.returncode != 0:
            detail = "C++ compilation timeout" if compile_result is None else (
                (compile_result.stdout or "") + (compile_result.stderr or "")
            )[-3000:]
            row.update({
                "cppsim_status": "failed",
                "failure_stage": "cpp_compilation",
                "detail": detail,
            })
            manifest["cases"].append(row)
            failed += 1
            continue
        try:
            smoke = subprocess.run(
                [str(binary)],
                cwd=case_dir,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except subprocess.TimeoutExpired:
            smoke = None
        if smoke is None or smoke.returncode != 0:
            detail = "C++ smoke run timeout" if smoke is None else (
                (smoke.stdout or "") + (smoke.stderr or "")
            )[-3000:]
            row.update({
                "cppsim_status": "failed",
                "failure_stage": "cpp_smoke_run",
                "detail": detail,
            })
            manifest["cases"].append(row)
            failed += 1
            continue

        row.update({
            "cppsim_status": "passed",
            "header": str(header),
            "header_sha256": hashlib.sha256(header.read_bytes()).hexdigest(),
        })
        manifest["cases"].append(row)
        passed += 1

    total = len(case_requests)
    if total > 0 and passed == total:
        manifest.update({
            "status": "passed",
            "coverage": "all_public_configurations",
            "family_covered": True,
        })
    elif failed:
        manifest.update({
            "status": "failed",
            "coverage": "partial_configuration_set" if passed else "none",
        })
    else:
        manifest.update({
            "status": "unsupported",
            "coverage": "partial_configuration_set" if passed else "none",
        })
    if passed != total:
        manifest["diagnostics"].append(
            f"CppSim covered {passed}/{total} cases; unsupported={unsupported}, failed={failed}"
        )
    return manifest


def cppsim_policy_is_required_failure(manifest: dict[str, Any]) -> bool:
    return bool(manifest.get("required")) and manifest.get("status") != "passed"


def _matching_parenthesis(text: str, open_index: int) -> int:
    depth = 0
    for index in range(open_index, len(text)):
        if text[index] == "(":
            depth += 1
        elif text[index] == ")":
            depth -= 1
            if depth == 0:
                return index
    return -1


def _mask_sv_comments(text: str) -> str:
    def mask(match: re.Match[str]) -> str:
        return "".join("\n" if char == "\n" else " " for char in match.group(0))

    return re.sub(r"//[^\n]*|/\*.*?\*/", mask, text, flags=re.DOTALL)


def _split_parameter_entries(text: str) -> list[str]:
    entries: list[str] = []
    start = 0
    paren = bracket = brace = 0
    for index, char in enumerate(text):
        if char == "(":
            paren += 1
        elif char == ")":
            paren -= 1
        elif char == "[":
            bracket += 1
        elif char == "]":
            bracket -= 1
        elif char == "{":
            brace += 1
        elif char == "}":
            brace -= 1
        elif char == "," and paren == bracket == brace == 0:
            entries.append(text[start:index].strip())
            start = index + 1
    entries.append(text[start:].strip())
    return [entry for entry in entries if entry]


def bind_top_parameter_defaults(
    sv_code: str,
    *,
    top_module: str,
    parameters: dict[str, int],
) -> tuple[str | None, list[str]]:
    """Bind one case by changing only the generic top's parameter defaults."""
    masked_sv = _mask_sv_comments(str(sv_code or ""))
    module_match = re.search(
        rf"\bmodule\s+{re.escape(top_module)}\b", masked_sv
    )
    if module_match is None:
        return None, [f"PPA top module `{top_module}` was not found"]

    index = module_match.end()
    while index < len(masked_sv) and masked_sv[index].isspace():
        index += 1
    if index >= len(masked_sv) or masked_sv[index] != "#":
        return None, [f"PPA top module `{top_module}` has no parameter list"]
    index += 1
    while index < len(masked_sv) and masked_sv[index].isspace():
        index += 1
    if index >= len(masked_sv) or masked_sv[index] != "(":
        return None, [f"PPA top module `{top_module}` has a malformed parameter list"]
    close = _matching_parenthesis(masked_sv, index)
    if close < 0:
        return None, [f"PPA top module `{top_module}` has an unterminated parameter list"]

    found: set[str] = set()
    rebound: list[str] = []
    for entry in _split_parameter_entries(sv_code[index + 1:close]):
        left, separator, _right = entry.partition("=")
        identifiers = re.findall(r"[A-Za-z_]\w*", left)
        name = identifiers[-1] if identifiers else ""
        if name in parameters:
            if not separator:
                return None, [f"PPA parameter `{name}` has no default to bind"]
            rebound.append(f"{left.rstrip()} = {int(parameters[name])}")
            found.add(name)
        else:
            rebound.append(entry)

    missing = sorted(set(parameters) - found)
    if missing:
        return None, [
            f"PPA top module `{top_module}` does not declare parameter(s): "
            + ", ".join(missing)
        ]
    bound = sv_code[: index + 1] + "\n    " + ",\n    ".join(rebound) + "\n" + sv_code[close:]
    return bound, []


def _metric_ranges(cases: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    ranges: dict[str, dict[str, float]] = {}
    for name in ("area_um2", "cell_count", "wns_ns", "power_uw"):
        values = [row[name] for row in cases if row.get(name) is not None]
        if values:
            ranges[name] = {"min": min(values), "max": max(values)}
    return ranges


def _ppa_failure_diagnostic(message: str, *, code: str) -> dict[str, str]:
    text = str(message or "").lower()
    if any(marker in text for marker in (
        "docker execution failed",
        "docker command timed out",
        "cannot connect to the docker daemon",
        "permission denied",
        "no such file or directory",
        "runner exception",
        "timeout",
    )):
        stage = "infrastructure"
    elif any(marker in text for marker in (
        "syntax error",
        "parser error",
        "module not found",
        "can't find top module",
        "cannot find top module",
        "verilog elaboration",
    )):
        stage = "verilog_elaboration"
    else:
        stage = "unsupported_backend"
    return {"stage": stage, "code": code, "message": str(message)}


def run_ppa_parameter_policy(
    *,
    sv_code: str,
    top_module: str,
    prob_id: str,
    plan: FiniteParameterPlan,
    output_dir: Path,
    synth_runner: Callable[[str, str, str, Path], dict[str, Any]],
    pnr_runner: Callable[[str, str, str, Path], dict[str, Any]] | None = None,
    requested_policy: str = "per_configuration",
    required: bool = False,
    require_drc: bool = False,
    require_lvs: bool = False,
) -> dict[str, Any]:
    """Run synthesis/PPA independently for every public parameter case."""
    if requested_policy not in PPA_POLICIES:
        raise ValueError(f"unknown PPA parameter policy: {requested_policy}")
    output_dir.mkdir(parents=True, exist_ok=True)
    source_hash = hashlib.sha256(sv_code.encode("utf-8")).hexdigest()
    stages = ["synthesis"]
    if pnr_runner is not None:
        stages.append("pnr")
    if require_drc:
        stages.append("drc")
    if require_lvs:
        stages.append("lvs")
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "backend": "ppa",
        "requested_policy": requested_policy,
        "effective_policy": requested_policy,
        "required": bool(required),
        "status": "not_run",
        "coverage": "none",
        "family_covered": False,
        "synthesis_family_covered": False,
        "pnr_family_covered": False,
        "source_sv_sha256": source_hash,
        "expected_stages": stages,
        "case_count": len(plan.cases),
        "covered_case_count": 0,
        "metric_ranges": {},
        "cases": [],
        "diagnostics": [],
    }
    if requested_policy == "off":
        manifest["diagnostics"].append({
            "stage": "unsupported_backend",
            "code": "ppa_policy_disabled",
            "message": "PPA parameter checking explicitly disabled",
        })
        return manifest

    for index, case in enumerate(plan.cases):
        parameters = case.values
        slug = _case_slug(parameters)
        case_id = f"{_cpp_identifier(prob_id)}__ppa_{index}_{slug}"
        case_dir = output_dir / f"case_{index:03d}_{slug}"
        case_dir.mkdir(parents=True, exist_ok=True)
        row: dict[str, Any] = {
            "parameters": parameters,
            "case_id": case_id,
            "artifact_dir": str(case_dir),
            "source_sv_sha256": source_hash,
            "concrete_sv_sha256": None,
            "parameter_binding": "failed",
            "synth_status": "not_run",
            "pnr_status": "not_run",
            "drc_status": "not_run",
            "lvs_status": "not_run",
            "area_um2": None,
            "cell_count": None,
            "wns_ns": None,
            "power_uw": None,
        }
        concrete_sv, diagnostics = bind_top_parameter_defaults(
            sv_code,
            top_module=top_module,
            parameters=parameters,
        )
        if concrete_sv is None:
            row["detail"] = "; ".join(diagnostics)
            row["diagnostic"] = {
                "stage": "parameter_contract",
                "code": "ppa_parameter_binding_failed",
                "message": row["detail"],
            }
            manifest["cases"].append(row)
            manifest["diagnostics"].append(row["diagnostic"])
            continue

        row["parameter_binding"] = "passed"
        row["concrete_sv_sha256"] = hashlib.sha256(
            concrete_sv.encode("utf-8")
        ).hexdigest()
        (case_dir / "bound_design.sv").write_text(concrete_sv, encoding="utf-8")
        try:
            synth_result = synth_runner(case_id, concrete_sv, top_module, case_dir)
        except Exception as exc:
            synth_result = {
                "synth_pass": False,
                "synth_error": f"Synthesis runner exception: {type(exc).__name__}: {exc}",
            }
        row["synthesis"] = synth_result
        row["synth_status"] = "passed" if synth_result.get("synth_pass") else "failed"
        if row["synth_status"] == "failed":
            row["diagnostic"] = _ppa_failure_diagnostic(
                str(synth_result.get("synth_error") or "ORFS synthesis did not pass"),
                code="ppa_synthesis_failed",
            )
            manifest["diagnostics"].append(row["diagnostic"])
        for metric in ("area_um2", "cell_count", "wns_ns", "power_uw"):
            row[metric] = synth_result.get(metric)

        if row["synth_status"] == "passed" and pnr_runner is not None:
            try:
                pnr_result = pnr_runner(case_id, concrete_sv, top_module, case_dir)
            except Exception as exc:
                pnr_result = {
                    "pnr_pass": False,
                    "pnr_error": f"P&R runner exception: {type(exc).__name__}: {exc}",
                }
            row["place_and_route"] = pnr_result
            row["pnr_status"] = "passed" if pnr_result.get("pnr_pass") else "failed"
            if row["pnr_status"] == "failed":
                row["diagnostic"] = _ppa_failure_diagnostic(
                    str(pnr_result.get("pnr_error") or "ORFS place and route did not pass"),
                    code="ppa_place_and_route_failed",
                )
                manifest["diagnostics"].append(row["diagnostic"])
            for metric in ("wns_ns", "power_uw"):
                row[metric] = pnr_result.get(metric)
            if require_drc:
                row["drc_status"] = "passed" if pnr_result.get("drc_pass") else "failed"
                if row["drc_status"] == "failed" and row.get("diagnostic") is None:
                    row["diagnostic"] = _ppa_failure_diagnostic(
                        str(pnr_result.get("drc_error") or "DRC did not pass"),
                        code="ppa_drc_failed",
                    )
                    manifest["diagnostics"].append(row["diagnostic"])
            if require_lvs:
                row["lvs_status"] = "passed" if pnr_result.get("lvs_pass") else "failed"
                if row["lvs_status"] == "failed" and row.get("diagnostic") is None:
                    row["diagnostic"] = _ppa_failure_diagnostic(
                        str(pnr_result.get("lvs_error") or "LVS did not pass"),
                        code="ppa_lvs_failed",
                    )
                    manifest["diagnostics"].append(row["diagnostic"])
        manifest["cases"].append(row)

    cases = manifest["cases"]
    synth_covered = bool(cases) and all(
        row["synth_status"] == "passed" for row in cases
    )
    pnr_covered = pnr_runner is not None and bool(cases) and all(
        row["pnr_status"] == "passed" for row in cases
    )
    drc_covered = not require_drc or all(
        row["drc_status"] == "passed" for row in cases
    )
    lvs_covered = not require_lvs or all(
        row["lvs_status"] == "passed" for row in cases
    )
    family_covered = (
        synth_covered
        and (pnr_runner is None or pnr_covered)
        and drc_covered
        and lvs_covered
    )
    completed = sum(
        row["synth_status"] == "passed"
        and (pnr_runner is None or row["pnr_status"] == "passed")
        and (not require_drc or row["drc_status"] == "passed")
        and (not require_lvs or row["lvs_status"] == "passed")
        for row in cases
    )
    manifest.update({
        "status": "passed" if family_covered else ("partial" if completed else "failed"),
        "coverage": (
            "all_public_configurations" if family_covered
            else "partial_configuration_set" if completed else "none"
        ),
        "family_covered": family_covered,
        "synthesis_family_covered": synth_covered,
        "pnr_family_covered": pnr_covered,
        "covered_case_count": completed,
        "case_count": len(cases),
        "metric_ranges": _metric_ranges(cases),
    })
    if not family_covered:
        stages = [
            item.get("stage") for item in manifest["diagnostics"]
            if isinstance(item, dict)
        ]
        summary_stage = next(
            (stage for stage in (
                "infrastructure", "verilog_elaboration",
                "parameter_contract", "unsupported_backend",
            ) if stage in stages),
            "unsupported_backend",
        )
        manifest["diagnostics"].append({
            "stage": summary_stage,
            "code": "ppa_family_coverage_incomplete",
            "message": f"PPA flow covers {completed}/{len(cases)} public configurations",
        })
    return manifest


def ppa_policy_is_required_failure(manifest: dict[str, Any]) -> bool:
    """Return whether required per-configuration PPA coverage failed."""
    return bool(manifest.get("required")) and manifest.get("status") != "passed"


def ppa_manifest_failure_stage(manifest: dict[str, Any]) -> str:
    """Return the highest-signal evaluator stage for a failed PPA family."""
    stages = {
        item.get("stage") for item in manifest.get("diagnostics", [])
        if isinstance(item, dict)
    }
    for stage in (
        "infrastructure", "verilog_elaboration",
        "parameter_contract", "unsupported_backend",
    ):
        if stage in stages:
            return stage
    return "unsupported_backend"
