#!/usr/bin/env python3
"""Gates for NL→formal contract files.

A contract is a self-contained Lean file of observational next-cycle
properties (Prop over pre/post Cycle). It is not a second circuit.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

FORBIDDEN_PATTERNS = [
    (r"(?i)\bSparkle\b", "imports or mentions Sparkle"),
    (r"(?i)#\s*synthesizeVerilog", "contains #synthesizeVerilog"),
    (r"\bSignal\b", "uses Signal DSL"),
    (r"Library\.RTL", "uses Sparkle RTL combinators"),
    (r"\bTopModule\b", "names the DUT"),
    (r"(?i)\bendmodule\b", "contains Verilog"),
    (r"(?i)\balways_ff\b|\balways_comb\b|\balways\s*@", "contains Verilog sequential/comb blocks"),
    (r"(?i)\bmodule\s+\w+", "contains a Verilog module"),
    (r"\bSignal\.loop\b|\bSignal\.register\b|\bSignal\.mux\b", "uses Signal combinators"),
    (r"\btheorem\b|\blemma\b", "claims a theorem; generation may only define Props"),
    (r"\bsorry\b|\badmit\b|\baxiom\b", "uses sorry/admit/axiom"),
]

TRIVIAL_BODY = re.compile(
    r"^(True|False|trivial|∀\s*[^,]+\s*,\s*True)\s*$",
    re.I,
)

TAUT_EQ = re.compile(
    r"\b(pre|post)\.((?:«)?[A-Za-z][\w']*(?:»)?)\s*=\s*\1\.\2\b"
)
MAGIC_ADD = re.compile(
    r"\bpre\.[A-Za-z][\w'«»]*\s*\+\s*(0x[0-9A-Fa-f]+|\d+)#(\d+)"
)
MAGIC_LIT = re.compile(
    r"\b(39321|2457|1639|103)#16\b"
)

DEF_PROP = re.compile(
    r"(?ms)^def\s+([A-Za-z][\w']*)\s*\(\s*pre\s+post\s*:\s*Cycle\s*\)\s*:\s*Prop\s*:=\s*(.*?)(?=^def\s+|^end\b|\Z)",
)

NAMESPACE = re.compile(r"(?m)^namespace\s+(Contract\.([A-Za-z][\w']*))\s*$")
STRUCTURE = re.compile(r"(?ms)^structure\s+Cycle\s+where\s*(.*?)(?=^def\s+|^end\b)")
FIELD = re.compile(r"(?m)^\s*«?([A-Za-z][\w']*)»?\s*:\s*([^\n]+)")


def strip_comments(text: str) -> str:
    text = re.sub(r"/-.*?-/", "", text, flags=re.S)
    text = re.sub(r"--[^\n]*", "", text)
    return text


def extract_lean(text: str) -> str | None:
    if not text:
        return None
    blocks = re.findall(r"```(?:lean)?\s*([\s\S]*?)```", text, flags=re.I)
    for block in blocks:
        if "namespace Contract" in block or "structure Cycle" in block:
            return _close_namespace(block.strip() + "\n")
    if "namespace Contract" in text and "structure Cycle" in text:
        start = text.find("import ")
        if start < 0:
            start = text.find("namespace Contract")
        rest = text[start:]
        ends = list(re.finditer(r"(?m)^end(?:\s+[A-Za-z][\w'.]*)*\s*$", rest))
        if ends:
            rest = rest[: ends[-1].end()]
        return _close_namespace(rest.strip() + "\n")
    return None


def _close_namespace(src: str) -> str:
    ns = NAMESPACE.search(src)
    if not ns:
        return src if src.endswith("\n") else src + "\n"
    full = ns.group(1)
    src = src.rstrip() + "\n"
    src = re.sub(r"(?m)^end(?:\s+[A-Za-z][\w'.]*)*\s*$", f"end {full}", src)
    if not re.search(r"(?m)^end\s+", src):
        src += f"end {full}\n"
    return src if src.endswith("\n") else src + "\n"


def parse_ref_ports(ref_code: str) -> list[tuple[str, str, str]]:
    """Return (direction, lean_type, name) from a Verilog module header."""
    ref_code = ref_code or ""
    m = re.search(r"module\s+\w+\s*\((.*?)\)\s*;", ref_code, re.S)
    header = m.group(1) if m else ref_code
    ports: list[tuple[str, str, str]] = []
    for part in re.split(r"[,;\n]+", header):
        part = part.strip()
        if not part:
            continue
        dm = re.match(
            r"(input|output|inout)\s+(?:reg\s+)?(?:\[(\d+)\s*:\s*(\d+)\]\s+)?(\w+)",
            part,
        )
        if not dm:
            continue
        direction, hi, lo, name = dm.group(1), dm.group(2), dm.group(3), dm.group(4)
        if hi is None:
            lean_ty = "Bool"
        else:
            width = abs(int(hi) - int(lo)) + 1
            lean_ty = "Bool" if width == 1 else f"BitVec {width}"
        ports.append((direction, lean_ty, name))
    return ports


def expected_cycle_fields(ports: list[tuple[str, str, str]]) -> dict[str, str]:
    fields = {}
    for _d, ty, name in ports:
        if name.lower() in {"clk", "clock"}:
            continue
        fields[name] = ty
    return fields


def _prop_defs(src: str) -> list[tuple[str, str]]:
    found = []
    for m in DEF_PROP.finditer(src):
        body = re.sub(r"\s+", " ", m.group(2)).strip()
        found.append((m.group(1), body))
    return found


def validate_contract(
    text: str,
    *,
    prob_id: str,
    ports: list[tuple[str, str, str]],
    min_props: int = 3,
) -> dict:
    reasons: list[str] = []
    src = strip_comments(text or "")
    if not src.strip():
        return {"ok": False, "reasons": ["empty file"], "n_props": 0, "props": []}

    if not re.search(r"(?m)^import\s+Init\s*$", src):
        reasons.append("must `import Init` and nothing else")
    extra_imports = [
        ln.strip()
        for ln in src.splitlines()
        if ln.strip().startswith("import ") and ln.strip() != "import Init"
    ]
    if extra_imports:
        reasons.append("extra imports: " + ", ".join(extra_imports[:4]))

    for pat, why in FORBIDDEN_PATTERNS:
        if re.search(pat, src):
            reasons.append(why)

    ns = NAMESPACE.search(src)
    want_ns = "Contract." + re.sub(r"[^A-Za-z0-9_]", "_", prob_id)
    if not ns:
        reasons.append("missing `namespace Contract.<id>`")
    elif ns.group(1) != want_ns:
        reasons.append(f"namespace is {ns.group(1)}, expected {want_ns}")

    st = STRUCTURE.search(src)
    fields: dict[str, str] = {}
    if not st:
        reasons.append("missing `structure Cycle where`")
    else:
        for name, ty in FIELD.findall(st.group(1)):
            fields[name] = ty.strip()

    expected = expected_cycle_fields(ports)
    field_names = {n.strip("«»"): n for n in fields}
    missing = [n for n in expected if n not in field_names]
    if missing:
        reasons.append("Cycle missing fields: " + ", ".join(missing))
    ghost = [n for n in field_names if n not in expected]

    props = _prop_defs(src)
    if len(props) < min_props:
        reasons.append(f"need ≥{min_props} defs of type Cycle → Cycle → Prop, found {len(props)}")

    bodies = []
    for name, body in props:
        bodies.append(body)
        if TRIVIAL_BODY.match(body):
            reasons.append(f"{name} is trivial ({body})")
        if "pre." not in body:
            reasons.append(f"{name} does not mention pre.<field>")
        if "post." not in body:
            reasons.append(f"{name} does not mention post.<field>")
        if "→" not in body and "->" not in body and "¬" not in body and "Not" not in body:
            if body.count("=") < 1:
                reasons.append(f"{name} is not an implication, negation, or equality constraint")
        tauts = TAUT_EQ.findall(body)
        if tauts:
            pretty = ", ".join(f"{side}.{fld}={side}.{fld}" for side, fld in tauts)
            reasons.append(f"{name} contains a tautology ({pretty})")
        for m in MAGIC_ADD.finditer(body):
            if int(m.group(1), 0) != 1:
                reasons.append(
                    f"{name} uses magic datapath arithmetic `+ {m.group(1)}#{m.group(2)}`; "
                    "write digit/bit-wise observational constraints"
                )
        if MAGIC_LIT.search(body):
            reasons.append(f"{name} uses a packed-BCD magic literal; constrain digits separately")

    if len(set(bodies)) == 1 and len(bodies) > 1:
        reasons.append("all properties have identical bodies")

    return {
        "ok": not reasons,
        "reasons": reasons,
        "n_props": len(props),
        "props": [{"name": n, "body": b} for n, b in props],
        "fields": fields,
        "ghost_fields": ghost,
        "namespace": ns.group(1) if ns else None,
    }


def lean_check(path: Path, project: Path, timeout: int = 60) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            ["lake", "env", "lean", str(path)],
            cwd=str(project),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=os.environ.copy(),
        )
    except FileNotFoundError:
        return False, "lake not found"
    except subprocess.TimeoutExpired:
        return False, "lake env lean timeout"
    out = (proc.stdout or "") + "\n" + (proc.stderr or "")
    return proc.returncode == 0, out[-4000:]


GOOD_EXAMPLE = r"""
import Init

namespace Contract.Prob035_count1to10

structure Cycle where
  reset : Bool
  q : BitVec 4

/-- Synchronous reset to 1. -/
def reset_to_one (pre post : Cycle) : Prop :=
  pre.reset = true → post.q = 1#4

/-- After 10, wrap to 1. -/
def wrap_after_ten (pre post : Cycle) : Prop :=
  pre.reset = false → pre.q = 10#4 → post.q = 1#4

/-- Otherwise increment. -/
def increment (pre post : Cycle) : Prop :=
  pre.reset = false → pre.q ≠ 10#4 → post.q = pre.q + 1#4

end Contract.Prob035_count1to10
"""

BAD_CIRCUIT = r"""
import Sparkle
def TopModule {dom : DomainConfig} (reset : Signal dom Bool) :=
  Signal.loop fun q => Signal.register 1#4 (q + 1)
#synthesizeVerilog TopModule
"""

BAD_TRIVIAL = r"""
import Init
namespace Contract.Prob035_count1to10
structure Cycle where
  reset : Bool
  q : BitVec 4
def p1 (pre post : Cycle) : Prop := True
def p2 (pre post : Cycle) : Prop := True
def p3 (pre post : Cycle) : Prop := True
end Contract.Prob035_count1to10
"""


def selftest() -> None:
    ports = [("input", "Bool", "clk"), ("input", "Bool", "reset"), ("output", "BitVec 4", "q")]
    good = validate_contract(GOOD_EXAMPLE, prob_id="Prob035_count1to10", ports=ports)
    assert good["ok"], good
    bad1 = validate_contract(BAD_CIRCUIT, prob_id="Prob035_count1to10", ports=ports)
    assert not bad1["ok"]
    bad2 = validate_contract(BAD_TRIVIAL, prob_id="Prob035_count1to10", ports=ports)
    assert not bad2["ok"]
    print("selftest_ok", {"good_props": good["n_props"], "bad_circuit": bad1["reasons"][:3], "bad_trivial": bad2["reasons"][:3]})


if __name__ == "__main__":
    selftest()
