"""Fail-closed backend policies for native parameterized designs."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from typing import Any
from pathlib import Path

from cvdp_specialization import FiniteParameterPlan


FORMAL_POLICIES = {"off", "auto", "generic", "per_configuration"}
CPPSIM_POLICIES = {"off", "per_configuration"}


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
