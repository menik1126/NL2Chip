"""Fail-closed backend policies for native parameterized designs."""
from __future__ import annotations

import re
from typing import Any

from cvdp_specialization import FiniteParameterPlan


FORMAL_POLICIES = {"off", "auto", "generic", "per_configuration"}


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
