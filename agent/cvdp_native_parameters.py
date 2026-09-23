"""Native SystemVerilog parameter contracts for the CVDP P3 path.

The CVDP harness is used only to discover public build-parameter combinations.
This module then verifies that one generated SystemVerilog module retains those
parameters instead of silently freezing the default Lean widths.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Iterable

from cvdp_specialization import FiniteParameterPlan, SpecializationCase


NATIVE_PARAMETER_MODE = "native_parameter_sweep"


def _matching_paren(text: str, open_index: int) -> int:
    depth = 0
    for index in range(open_index, len(text)):
        if text[index] == "(":
            depth += 1
        elif text[index] == ")":
            depth -= 1
            if depth == 0:
                return index
    return -1


def _split_commas(text: str) -> list[str]:
    parts: list[str] = []
    start = 0
    paren = bracket = brace = 0
    for index, char in enumerate(text):
        if char == "(":
            paren += 1
        elif char == ")":
            paren = max(0, paren - 1)
        elif char == "[":
            bracket += 1
        elif char == "]":
            bracket = max(0, bracket - 1)
        elif char == "{":
            brace += 1
        elif char == "}":
            brace = max(0, brace - 1)
        elif char == "," and paren == 0 and bracket == 0 and brace == 0:
            parts.append(text[start:index].strip())
            start = index + 1
    tail = text[start:].strip()
    if tail:
        parts.append(tail)
    return parts


def _strip_comments(text: str) -> str:
    text = re.sub(r"//[^\n]*", "", text)
    return re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)


@dataclass(frozen=True)
class NativeModule:
    name: str
    parameter_text: str
    port_text: str
    body: str
    source: str
    parameters: tuple[tuple[str, str | None], ...]

    @property
    def parameter_names(self) -> tuple[str, ...]:
        return tuple(name for name, _ in self.parameters)

    @property
    def implementation_text(self) -> str:
        """Text in which parameter use is semantic, excluding declarations."""
        return f"{self.port_text}\n{self.body}"


def _parse_parameters(parameter_text: str) -> tuple[tuple[str, str | None], ...]:
    declarations: list[tuple[str, str | None]] = []
    in_parameter_declaration = False
    for raw_entry in _split_commas(parameter_text):
        entry = raw_entry.strip()
        if re.match(r"^localparam\b", entry):
            in_parameter_declaration = False
            continue
        if re.match(r"^parameter\b", entry):
            in_parameter_declaration = True
            entry = re.sub(r"^parameter\b", "", entry, count=1).strip()
        elif not in_parameter_declaration:
            continue

        left, separator, right = entry.partition("=")
        identifiers = re.findall(r"[A-Za-z_]\w*", left)
        if not identifiers:
            continue
        name = identifiers[-1]
        if name in {
            "parameter", "integer", "int", "logic", "bit", "signed", "unsigned",
        }:
            continue
        default = right.strip() if separator else None
        if name not in {existing for existing, _ in declarations}:
            declarations.append((name, default))
    return tuple(declarations)


def parse_native_modules(sv_code: str) -> list[NativeModule]:
    """Parse complete modules, including ANSI parameter and port headers."""
    clean = _strip_comments(str(sv_code or ""))
    modules: list[NativeModule] = []
    search_from = 0
    while True:
        match = re.search(r"\bmodule\s+([A-Za-z_]\w*)\b", clean[search_from:])
        if not match:
            break
        module_start = search_from + match.start()
        name = match.group(1)
        index = search_from + match.end()
        while index < len(clean) and clean[index].isspace():
            index += 1

        parameter_text = ""
        if index < len(clean) and clean[index] == "#":
            index += 1
            while index < len(clean) and clean[index].isspace():
                index += 1
            if index >= len(clean) or clean[index] != "(":
                search_from = index
                continue
            close = _matching_paren(clean, index)
            if close < 0:
                break
            parameter_text = clean[index + 1:close]
            index = close + 1

        while index < len(clean) and clean[index].isspace():
            index += 1
        port_text = ""
        if index < len(clean) and clean[index] == "(":
            close = _matching_paren(clean, index)
            if close < 0:
                break
            port_text = clean[index + 1:close]
            index = close + 1

        semicolon = clean.find(";", index)
        if semicolon < 0:
            break
        end_match = re.search(r"\bendmodule\b", clean[semicolon + 1:])
        if not end_match:
            break
        module_end = semicolon + 1 + end_match.end()
        body_end = semicolon + 1 + end_match.start()
        modules.append(
            NativeModule(
                name=name,
                parameter_text=parameter_text,
                port_text=port_text,
                body=clean[semicolon + 1:body_end],
                source=clean[module_start:module_end],
                parameters=_parse_parameters(parameter_text),
            )
        )
        search_from = module_end
    return modules


def native_plan_to_dict(plan: FiniteParameterPlan) -> dict[str, Any]:
    payload = plan.to_dict()
    payload["mode"] = NATIVE_PARAMETER_MODE
    payload["specialization_count"] = 0
    payload["sweep_case_count"] = len(plan.cases)
    for row in payload.get("cases", []):
        row.pop("module_name", None)
    return payload


def native_plan_from_dict(payload: dict[str, Any] | None) -> FiniteParameterPlan | None:
    if not payload or payload.get("mode") != NATIVE_PARAMETER_MODE:
        return None
    names = tuple(str(name) for name in payload.get("parameter_names", []))
    cases: list[SpecializationCase] = []
    for index, row in enumerate(payload.get("cases", [])):
        raw = row.get("parameters", {})
        try:
            parameters = tuple((name, int(raw[name])) for name in names)
        except (KeyError, TypeError, ValueError):
            return None
        cases.append(
            SpecializationCase(
                parameters=parameters,
                module_name=f"{payload.get('design_name', 'dut')}__native_case_{index}",
            )
        )
    return FiniteParameterPlan(
        design_name=str(payload.get("design_name", "dut")),
        parameter_names=names,
        cases=tuple(cases),
        diagnostics=tuple(str(item) for item in payload.get("diagnostics", [])),
        source=str(payload.get("source", "public_cvdp_harness")),
    )


def select_native_core_module(
    sv_code: str,
    *,
    required_parameters: Iterable[str],
    preferred_name: str | None = None,
) -> NativeModule | None:
    """Choose the generated module that owns every required parameter."""
    required = set(required_parameters)
    modules = parse_native_modules(sv_code)
    candidates = [module for module in modules if required <= set(module.parameter_names)]
    if not candidates:
        return None

    instantiated: set[str] = set()
    for owner in modules:
        for candidate in candidates:
            if owner.name == candidate.name:
                continue
            if re.search(
                rf"\b{re.escape(candidate.name)}\b\s*(?:#\s*\([^;]*?\)\s*)?[A-Za-z_]\w*\s*\(",
                owner.body,
                flags=re.DOTALL,
            ):
                instantiated.add(candidate.name)

    def score(module: NativeModule) -> tuple[int, int, int]:
        return (
            1 if preferred_name and module.name == preferred_name else 0,
            1 if module.name not in instantiated else 0,
            len(module.parameter_names),
        )

    return max(candidates, key=score)


def validate_native_parameter_ownership(
    module: NativeModule,
    *,
    required_parameters: Iterable[str],
) -> list[str]:
    """Reject missing and declaration-only parameters on the generated core."""
    diagnostics: list[str] = []
    declared = set(module.parameter_names)
    for name in required_parameters:
        if name not in declared:
            diagnostics.append(
                f"generated core `{module.name}` does not declare required parameter `{name}`"
            )
            continue
        if not re.search(rf"\b{re.escape(name)}\b", module.implementation_text):
            diagnostics.append(
                f"generated core parameter `{name}` is declaration-only; its ports/body remain fixed"
            )
    return diagnostics


def format_native_parameter_contract(plan: FiniteParameterPlan) -> str:
    defaults = plan.cases[0].values if plan.cases else {}
    lines = [
        "### Native Parameter Sweep (P3)",
        "",
        "Emit exactly one generic Sparkle/SystemVerilog design. Do not enumerate sweep values "
        "as concrete Lean aliases. Every listed parameter must remain symbolic in dependent "
        "ports, state, memories, slices, and child instances.",
        "",
        "- Retained parameters: " + ", ".join(plan.parameter_names),
        "- Defaults for Lean elaboration: "
        + ", ".join(f"{name}={defaults.get(name)}" for name in plan.parameter_names),
        f"- Public sweep configurations: {len(plan.cases)}",
        "",
        "Critical Lean syntax: every retained parameter must be a direct implicit "
        "top-level Nat binder of the synthesized definition, e.g. "
        "`def top {dom : DomainConfig} {WIDTH : Nat}`. Do not place it in a "
        "local let, structure, or runtime Signal argument.",
    ]
    for case in plan.cases:
        lines.append(
            "- " + ", ".join(f"{name}={value}" for name, value in case.parameters)
        )
    lines.extend([
        "",
        "Use `#synthesizeParameterizedVerilog` only for a leaf design. If the top calls "
        "any named `@[sparkle_module]` helper (including through `Signal.mapChunks`), you "
        "must use `#synthesizeParameterizedVerilogDesign` instead, with one default binding "
        "per retained Nat binder. The Design command emits the helper module definitions; "
        "the leaf command emits only the top and will fail downstream Verilog elaboration "
        "with unknown module types. The evaluator will elaborate and run this same emitted "
        "DUT at every public configuration and will reject fixed-width inner cores or "
        "wrapper-only parameters.",
    ])
    return "\n".join(lines)


def sv_sha256(sv_code: str) -> str:
    return hashlib.sha256(str(sv_code).encode("utf-8")).hexdigest()
