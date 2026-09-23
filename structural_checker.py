"""Conservative public-declaration checks; no hidden harness input."""
from __future__ import annotations

import re
from typing import Any


def numeric_width(typ: str) -> int | None:
    match = re.search(r"\[\s*(\d+)\s*:\s*(\d+)\s*\]", typ)
    return abs(int(match[1]) - int(match[2])) + 1 if match else None


def check_public_structure(*, sv: str, info: Any, search_module: Any) -> list[dict[str, str]]:
    if info.metadata.get("interface_prompt_policy") != "public-spec-v2":
        raise ValueError("Structural checks require a sanitized public-spec-v2 view")
    public_ports = search_module._parse_ports_from_prompt(info.prompt_text)
    module, ports = search_module.parse_module_ports(sv, module_name=info.design_name)
    if not module or not ports:
        return []
    actual = {name: (direction, typ) for direction, typ, name in ports}
    # Sparkle uses this deterministic input prefix; it is not a missing port.
    for direction, typ, name in ports:
        if name.startswith("_gen_") and name[5:] not in actual:
            actual[name[5:]] = (direction, typ)
    packed = any(direction == "output" and name == "out" for direction, _, name in ports)
    issues = []
    for direction, typ, name in public_ports:
        if name not in actual:
            # Packed bundles and implicit ABI ports may be mapped by the adapter.
            if direction == "output" and packed:
                continue
            if direction == "input":
                if re.search(r"clk|clock", name, re.I) and "clk" in actual:
                    continue
                if re.search(r"rst|reset", name, re.I) and "rst" in actual:
                    continue
            issues.append({"kind": "missing_port", "name": name, "expected": direction,
                           "source": "public prompt interface declaration"})
            continue
        got_direction, got_type = actual[name]
        if got_direction != direction:
            issues.append({"kind": "port_direction", "name": name, "expected": direction,
                           "actual": got_direction, "source": "public prompt interface declaration"})
        expected_width = numeric_width(typ)
        got_width = numeric_width(got_type) if "[" in got_type else 1
        if expected_width is not None and got_width is not None and expected_width != got_width:
            issues.append({"kind": "port_width", "name": name, "expected": str(expected_width),
                           "actual": str(got_width), "source": "public prompt width declaration"})
    # Parameter lists alone cannot distinguish configurable ports from derived
    # constants in this parser. Leave those cases to the public-spec inventory.
    return issues


def format_structural_feedback(issues: list[dict[str, str]]) -> str:
    if not issues:
        return ""
    lines = ["### Public Structural Contract Check"]
    for issue in issues[:20]:
        details = ", ".join(f"{key}={value}" for key, value in issue.items() if key != "kind")
        lines.append(f"- {issue['kind']}: {details}")
    lines.append("Repair the upstream Lean using these public declarations. No simulation outcome or hidden test evidence is supplied.")
    return "\n".join(lines)


def repair_eligible(result, issues, structural_used, compile_eligible):
    return compile_eligible or (bool(issues) and not structural_used)


def public_progress_key(result, issues):
    return (bool(result.get("compile_pass")), bool(result.get("sv_extracted")),
            bool(result.get("lint_pass")), -len(issues))
