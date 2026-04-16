#!/usr/bin/env python3
"""
autoformalize.py — NL → Sparkle DSL autoformalization pipeline

Reads VerilogEval NL descriptions, uses LLM with few-shot examples to generate
Sparkle HDL (Lean 4) code, then verifies via `lake build`.

Usage:
    uv run python autoformalize.py              # run all 146 problems
    uv run python autoformalize.py --limit 5    # first 5 only
    uv run python autoformalize.py --resume     # skip already-passed
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SPARKLE_ROOT = Path(__file__).parent.resolve()
BENCHMARK_DIR = SPARKLE_ROOT / "Benchmark"
DATASET_DIR = SPARKLE_ROOT / "verilog-eval" / "dataset_spec-to-rtl"
GENERATED_DIR = SPARKLE_ROOT / "Generated"
RESULTS_DIR = SPARKLE_ROOT / "results"

BENCHMARK_IDS = {5, 22, 24, 27, 31, 35, 54, 86, 109, 127}
BENCHMARK_FILES = {
    5: "Prob005_notgate", 22: "Prob022_mux2to1", 24: "Prob024_hadd",
    27: "Prob027_fadd", 31: "Prob031_dff", 35: "Prob035_count1to10",
    54: "Prob054_edgedetect", 86: "Prob086_lfsr5",
    109: "Prob109_fsm1", 127: "Prob127_lemmings1",
}

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Problem:
    prob_id: str        # e.g. "Prob001_zero"
    prob_num: int       # e.g. 1
    name: str           # e.g. "zero"
    nl_description: str
    ref_verilog: str

@dataclass
class FewShot:
    prob_id: str
    nl_description: str
    ref_verilog: str
    sparkle_code: str   # .lean content stripped of comment header

@dataclass
class Result:
    problem_id: str
    status: str         # "pass", "fail", "error"
    attempts: int
    tokens_in: int = 0
    tokens_out: int = 0
    build_time: float = 0.0
    sim_status: str = ""  # "sim_pass", "sim_fail", "sim_error", ""
    sim_mismatches: int = -1
    error_msg: str | None = None

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are an expert at translating hardware circuit descriptions into Sparkle HDL, \
a Lean 4-based hardware description DSL. Given a natural language specification \
and reference Verilog, produce the equivalent Sparkle DSL code.

## File Template (ALWAYS use this exact structure)

```lean
import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- <one-line docstring> -/
def <func_name> {dom : DomainConfig}
    (<inputs>) : <output_type> :=
  <implementation>

#synthesizeVerilog <func_name>
```

## Naming Convention
- Function name: `prob<NNN>_<name>` where NNN is zero-padded 3 digits
- Example: `prob001_zero`, `prob042_mux256to1`

## Type System
- `Signal dom (BitVec N)` — N-bit hardware signal
- `Signal dom Bool` — 1-bit boolean signal (used for conditions)
- `Signal dom (BitVec M × BitVec N)` — bundled multi-output (use `bundle2`)
- Clock and reset are IMPLICIT in `DomainConfig` — do NOT add clk/reset as inputs \
unless the problem has an explicit user-controlled reset signal

## Core Operators (Signal-level, lifted)
| Op | Meaning |
|----|---------|
| `~~~a` | bitwise NOT |
| `a &&& b` | bitwise AND |
| `a \\|\\|\\| b` | bitwise OR |
| `a ^^^ b` | bitwise XOR |
| `a >>> n` | shift right (use `n#W` as BitVec literal) |
| `a <<< n` | shift left |
| `a === b` | equality, returns `Signal dom Bool` |
| `a + b`, `a - b` | arithmetic |

## Core API
| Function | Purpose |
|----------|---------|
| `Signal.mux (cond : Signal dom Bool) (ifTrue ifFalse : Signal dom α)` | 2-to-1 mux |
| `Signal.register (init : α) (input : Signal dom α)` | D flip-flop with reset value |
| `Signal.loop (fun state => ... Signal.register init next)` | Feedback loop (counter, FSM) |
| `Signal.pure (val : α)` | Constant signal |
| `bundle2 a b` | Bundle two signals into a pair |
| `Signal.map (f : α → β) (s : Signal dom α)` | Apply pure function (e.g., bit extract) |
| `hw_cond default \\| cond1 => val1 \\| cond2 => val2` | Priority mux chain |

## Constants
- BitVec literal: `N#W` where N is the value and W is the bit width
- Examples: `0#4` (4-bit zero), `1#1` (1-bit one), `10#4` (4-bit 10)
- Can use directly with mixed-type operators: `q + 1#4`, `q === 10#4`
- Or wrap in Signal.pure: `Signal.pure 1#4`

## Key Patterns

### Combinational (no state)
```lean
def myCircuit {dom : DomainConfig}
    (a b : Signal dom (BitVec 1)) : Signal dom (BitVec 1) :=
  a &&& b
```

### Sequential feed-forward (register chain, no feedback)
```lean
def myReg {dom : DomainConfig}
    (d : Signal dom (BitVec 8)) : Signal dom (BitVec 8) :=
  Signal.register 0#8 d
```

### Sequential with feedback (counter, FSM)
```lean
def myCounter {dom : DomainConfig}
    (reset : Signal dom Bool) : Signal dom (BitVec 4) :=
  Signal.loop fun q =>
    let next := Signal.mux reset (Signal.pure 0#4) (q + 1#4)
    Signal.register 0#4 next
```

### Multi-output
```lean
def myCircuit {dom : DomainConfig}
    (a b : Signal dom (BitVec 1))
    : Signal dom (BitVec 1 × BitVec 1) :=
  bundle2 (a ^^^ b) (a &&& b)
```

### FSM with state encoding
```lean
private abbrev stA : BitVec 2 := 0#2
private abbrev stB : BitVec 2 := 1#2

def myFSM {dom : DomainConfig}
    (inp : Signal dom Bool) : Signal dom (BitVec 1) :=
  Signal.loop fun state =>
    let isA := state === (Signal.pure stA)
    let next := Signal.mux isA
      (Signal.mux inp (Signal.pure stA) (Signal.pure stB))
      (Signal.mux inp (Signal.pure stB) (Signal.pure stA))
    Signal.register stA next
```

## Critical Rules
1. NEVER use `if ... then ... else` — use `Signal.mux` instead
2. Clock is implicit — do NOT add `clk` as an input parameter
3. `Signal.mux cond trueVal falseVal` — true branch first, false second
4. `Signal.loop` body MUST end with `Signal.register`
5. For multi-output, return `Signal dom (T1 × T2)` with `bundle2`
6. State abbreviations should be `private abbrev stXxx : BitVec N := val`
7. For async reset as an explicit input, model it as synchronous mux before register
8. Use `Signal.map (BitVec.extractLsb' start len) sig` for bit slicing
9. Respond with ONLY the complete .lean file content, no explanation
10. The code must type-check in Lean 4 and synthesize via `#synthesizeVerilog`
"""

# ---------------------------------------------------------------------------
# Config / CLI
# ---------------------------------------------------------------------------

def load_env(path: Path) -> dict[str, str]:
    """Parse a simple key=value .env file."""
    env = {}
    if not path.exists():
        return env
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            v = v.strip().strip("'\"")
            env[k.strip()] = v
    return env


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="NL → Sparkle autoformalization")
    p.add_argument("--model", default="claude-sonnet-4-20250514",
                    help="Model name (default: claude-sonnet-4-20250514)")
    p.add_argument("--max-retries", type=int, default=2,
                    help="Max retries per problem (default: 2)")
    p.add_argument("--limit", type=int, default=None,
                    help="Only process first N problems")
    p.add_argument("--resume", action="store_true",
                    help="Skip already-passed problems")
    p.add_argument("--dry-run", action="store_true",
                    help="Build prompts but don't call LLM")
    p.add_argument("--timeout", type=int, default=120,
                    help="Lake build timeout in seconds (default: 120)")
    p.add_argument("--filter", type=str, default=None,
                    help="Only process problems matching this regex")
    return p.parse_args()

# ---------------------------------------------------------------------------
# Few-shot loading
# ---------------------------------------------------------------------------

def load_few_shots() -> list[FewShot]:
    """Load the 10 hand-written benchmark examples as few-shot pairs."""
    shots = []
    for num, stem in sorted(BENCHMARK_FILES.items()):
        lean_path = BENCHMARK_DIR / f"{stem}.lean"
        prompt_path = DATASET_DIR / f"{stem}_prompt.txt"
        ref_path = DATASET_DIR / f"{stem}_ref.sv"

        if not lean_path.exists():
            print(f"  WARNING: Missing {lean_path}", file=sys.stderr)
            continue

        lean_code = lean_path.read_text()
        nl_desc = prompt_path.read_text() if prompt_path.exists() else ""
        ref_sv = ref_path.read_text() if ref_path.exists() else ""

        # Strip the leading /- ... -/ comment block from Lean code
        # to avoid redundancy (NL + ref are already in the user turn)
        stripped = re.sub(r"^/-.*?-/\s*", "", lean_code, flags=re.DOTALL)

        shots.append(FewShot(
            prob_id=stem,
            nl_description=nl_desc.strip(),
            ref_verilog=ref_sv.strip(),
            sparkle_code=stripped.strip(),
        ))
    return shots

# ---------------------------------------------------------------------------
# Problem discovery
# ---------------------------------------------------------------------------

def discover_problems(limit: int | None = None, filt: str | None = None) -> list[Problem]:
    """Find all VerilogEval problems, excluding the 10 benchmarks."""
    problems = []
    pattern = re.compile(r"(Prob(\d+)_(\w+))_prompt\.txt$")

    for f in sorted(DATASET_DIR.iterdir()):
        m = pattern.match(f.name)
        if not m:
            continue
        prob_id, num_str, name = m.group(1), m.group(2), m.group(3)
        prob_num = int(num_str)

        if prob_num in BENCHMARK_IDS:
            continue
        if filt and not re.search(filt, prob_id):
            continue

        nl_desc = f.read_text().strip()
        ref_path = DATASET_DIR / f"{prob_id}_ref.sv"
        ref_sv = ref_path.read_text().strip() if ref_path.exists() else ""

        problems.append(Problem(
            prob_id=prob_id, prob_num=prob_num, name=name,
            nl_description=nl_desc, ref_verilog=ref_sv,
        ))

    problems.sort(key=lambda p: p.prob_num)
    if limit:
        problems = problems[:limit]
    return problems

# ---------------------------------------------------------------------------
# Prompt building
# ---------------------------------------------------------------------------

def build_messages(
    problem: Problem,
    few_shots: list[FewShot],
    error_feedback: str | None = None,
) -> list[dict]:
    """Build the messages list for the Anthropic API."""
    messages = []

    # Few-shot examples as user/assistant turns
    for shot in few_shots:
        user_content = f"## Natural Language Description\n\n{shot.nl_description}"
        if shot.ref_verilog:
            user_content += f"\n\n## Reference Verilog\n\n```verilog\n{shot.ref_verilog}\n```"
        messages.append({"role": "user", "content": user_content})
        messages.append({"role": "assistant", "content": f"```lean\n{shot.sparkle_code}\n```"})

    # Target problem
    target_content = f"## Natural Language Description\n\n{problem.nl_description}"
    if problem.ref_verilog:
        target_content += f"\n\n## Reference Verilog\n\n```verilog\n{problem.ref_verilog}\n```"
    messages.append({"role": "user", "content": target_content})

    # Error feedback for retries
    if error_feedback:
        messages.append({
            "role": "assistant",
            "content": "```lean\n" + error_feedback.split("```lean\n", 1)[-1] if "```lean" in error_feedback else error_feedback,
        })
        messages.append({
            "role": "user",
            "content": (
                "The code above failed to compile. Here is the Lean compiler error:\n\n"
                f"```\n{error_feedback}\n```\n\n"
                "Common fixes:\n"
                "- Type mismatch: check BitVec widths, use correct N#W literals\n"
                "- Use Signal.mux instead of if-then-else\n"
                "- Signal.loop body must end with Signal.register\n"
                "- For equality checks returning Bool, use ===\n"
                "- Multi-output needs bundle2 and return type (T1 × T2)\n\n"
                "Please provide the complete corrected .lean file."
            ),
        })

    return messages

# ---------------------------------------------------------------------------
# LLM calling
# ---------------------------------------------------------------------------

def call_llm(
    messages: list[dict],
    api_key: str,
    base_url: str,
    model: str,
) -> tuple[str, int, int]:
    """Call the Anthropic API. Returns (response_text, input_tokens, output_tokens)."""
    import anthropic

    client = anthropic.Anthropic(api_key=api_key, base_url=base_url)

    max_backoff_attempts = 5
    for attempt in range(max_backoff_attempts):
        try:
            resp = client.messages.create(
                model=model,
                max_tokens=4096,
                temperature=0.0,
                system=SYSTEM_PROMPT,
                messages=messages,
            )
            text = resp.content[0].text if resp.content else ""
            return text, resp.usage.input_tokens, resp.usage.output_tokens
        except anthropic.RateLimitError:
            wait = 10 * (2 ** attempt)
            print(f"    Rate limited, waiting {wait}s...", file=sys.stderr)
            time.sleep(wait)
        except anthropic.APIStatusError as e:
            if e.status_code >= 500:
                wait = 10 * (2 ** attempt)
                print(f"    Server error {e.status_code}, waiting {wait}s...", file=sys.stderr)
                time.sleep(wait)
            else:
                raise
    raise RuntimeError("Max backoff attempts exceeded")

# ---------------------------------------------------------------------------
# Code extraction
# ---------------------------------------------------------------------------

def extract_lean_code(response: str) -> str:
    """Extract Lean code from LLM response."""
    # Try ```lean ... ``` blocks
    m = re.search(r"```lean\s*\n(.*?)```", response, re.DOTALL)
    if m:
        code = m.group(1).strip()
    else:
        # Fallback: find import Sparkle as start
        idx = response.find("import Sparkle")
        if idx >= 0:
            code = response[idx:].strip()
        else:
            code = response.strip()

    # Ensure required imports
    if "import Sparkle.Compiler.Elab" not in code:
        code = code.replace("import Sparkle\n", "import Sparkle\nimport Sparkle.Compiler.Elab\n")
    if "import Sparkle" not in code:
        code = "import Sparkle\nimport Sparkle.Compiler.Elab\n\n" + code

    # Ensure open declarations
    if "open Sparkle.Core.Domain" not in code:
        # Insert after imports
        code = re.sub(
            r"(import Sparkle\.Compiler\.Elab\n)",
            r"\1\nopen Sparkle.Core.Domain\nopen Sparkle.Core.Signal\n",
            code,
        )

    return code

# ---------------------------------------------------------------------------
# Verilog extraction from lake build output
# ---------------------------------------------------------------------------

def extract_verilog_from_build(build_output: str) -> str | None:
    """Extract generated SystemVerilog from lake build info output."""
    # Look for the block between "// Generated by Sparkle HDL" and "-- Verilog successfully generated!"
    m = re.search(
        r"(// Generated by Sparkle HDL.*?endmodule)",
        build_output,
        re.DOTALL,
    )
    return m.group(1) if m else None


def parse_module_ports(sv_code: str) -> tuple[str, list[tuple[str, str, str]]]:
    """Parse a SystemVerilog module to get name and ports.
    Returns (module_name, [(direction, type, name), ...])
    """
    # Module name
    m = re.search(r"module\s+(\w+)\s*\(", sv_code)
    if not m:
        return "", []
    mod_name = m.group(1)

    # Ports: match "input logic [W:0] name" or "output logic [W:0] name"
    ports = []
    for pm in re.finditer(
        r"(input|output)\s+(logic(?:\s*\[\d+:\d+\])?)\s+(\w+)",
        sv_code,
    ):
        direction, typ, name = pm.group(1), pm.group(2), pm.group(3)
        ports.append((direction, typ, name))
    return mod_name, ports


def parse_ref_ports(ref_sv: str) -> list[tuple[str, str, str]]:
    """Parse RefModule ports from reference Verilog.
    Returns [(direction, type_str, name), ...]
    """
    ports = []
    # Match patterns like: input clk, input [7:0] in, output reg [3:0] q
    for m in re.finditer(
        r"(input|output)\s+(?:reg\s+|logic\s+|wire\s+)?(\[[\d:]+\])?\s*(\w+)",
        ref_sv,
    ):
        direction = m.group(1)
        width = m.group(2) or ""
        name = m.group(3)
        typ = f"logic {width}".strip() if width else "logic"
        ports.append((direction, typ, name))
    return ports


def generate_top_wrapper(
    sparkle_mod_name: str,
    sparkle_ports: list[tuple[str, str, str]],
    ref_ports: list[tuple[str, str, str]],
) -> str | None:
    """Generate a TopModule wrapper mapping ref ports to sparkle ports.
    Returns the wrapper SV code, or None if mapping fails.
    """
    # Build port name mapping: ref_name -> sparkle_name
    # Strategy: match by direction and order; also handle _gen_ prefix and special names
    ref_inputs = [(d, t, n) for d, t, n in ref_ports if d == "input"]
    ref_outputs = [(d, t, n) for d, t, n in ref_ports if d == "output"]
    sp_inputs = [(d, t, n) for d, t, n in sparkle_ports if d == "input"]
    sp_outputs = [(d, t, n) for d, t, n in sparkle_ports if d == "output"]

    # Filter out clk/rst from sparkle (they're implicit in domain but added by synthesizer)
    sp_user_inputs = [(d, t, n) for d, t, n in sp_inputs if n not in ("clk", "rst")]

    # Filter out clk from ref (implicit in testbench stimulus)
    ref_user_inputs = [(d, t, n) for d, t, n in ref_inputs if n != "clk"]

    # Try to map by matching order
    input_map = {}  # ref_name -> sparkle_name
    for i, (_, _, rn) in enumerate(ref_user_inputs):
        if i < len(sp_user_inputs):
            input_map[rn] = sp_user_inputs[i][2]
        else:
            # Try exact name or _gen_ prefix match
            for _, _, sn in sp_user_inputs:
                if sn == rn or sn == f"_gen_{rn}":
                    input_map[rn] = sn
                    break

    output_map = {}  # ref_name -> sparkle_name
    for i, (_, _, rn) in enumerate(ref_outputs):
        if i < len(sp_outputs):
            output_map[rn] = sp_outputs[i][2]
        else:
            for _, _, sn in sp_outputs:
                if sn == rn or sn == f"_gen_{rn}":
                    output_map[rn] = sn
                    break

    # Generate wrapper
    lines = []
    lines.append("module TopModule (")
    all_ref_ports = ref_inputs + ref_outputs
    port_decls = []
    for d, t, n in all_ref_ports:
        # Use the ref type to get the width right
        width = ""
        wm = re.search(r"\[(\d+):(\d+)\]", t)
        if wm:
            width = f" [{wm.group(1)}:{wm.group(2)}]"
        port_decls.append(f"    {d}{width} {n}")
    lines.append(",\n".join(port_decls))
    lines.append(");")
    lines.append("")

    # Wire declarations for sparkle module output
    for _, t, n in sp_outputs:
        lines.append(f"    {t} {n}_wire;")

    # Instantiate sparkle module
    lines.append(f"    {sparkle_mod_name} dut (")
    inst_conns = []

    # Connect inputs
    for _, _, sn in sp_inputs:
        if sn == "clk":
            inst_conns.append(f"        .clk(clk)")
        elif sn == "rst":
            # Map rst to areset if available, else tie to 0
            if "areset" in input_map:
                inst_conns.append(f"        .rst(areset)")
            elif "reset" in input_map:
                inst_conns.append(f"        .rst(reset)")
            else:
                inst_conns.append(f"        .rst(1'b0)")
        else:
            # Find matching ref input
            matched = False
            for rn, sn2 in input_map.items():
                if sn2 == sn:
                    inst_conns.append(f"        .{sn}({rn})")
                    matched = True
                    break
            if not matched:
                inst_conns.append(f"        .{sn}({sn})")

    # Connect outputs
    for _, _, sn in sp_outputs:
        inst_conns.append(f"        .{sn}({sn}_wire)")

    lines.append(",\n".join(inst_conns))
    lines.append("    );")

    # Assign outputs
    # Special case: if we have N ref outputs but only 1 sparkle output that's a bundle,
    # split the bundle bits
    if len(ref_outputs) > 1 and len(sp_outputs) == 1:
        sp_out_name = sp_outputs[0][2]
        sp_out_type = sp_outputs[0][1]
        # Check if it's a multi-bit output (bundle)
        wm = re.search(r"\[(\d+):0\]", sp_out_type)
        if wm and int(wm.group(1)) + 1 >= len(ref_outputs):
            # It's a bundle! Split it
            for i, (_, _, rn) in enumerate(ref_outputs):
                lines.append(f"    assign {rn} = {sp_out_name}_wire[{len(ref_outputs) - 1 - i}];")
        else:
            # Not a bundle, fall back to normal mapping
            for rn, sn in output_map.items():
                lines.append(f"    assign {rn} = {sn}_wire;")
    else:
        # Normal case: direct mapping
        for rn, sn in output_map.items():
            lines.append(f"    assign {rn} = {sn}_wire;")

    lines.append("endmodule")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# iverilog simulation
# ---------------------------------------------------------------------------

def run_iverilog_sim(
    prob_id: str,
    generated_sv: str,
    run_dir: Path,
    timeout: int = 30,
) -> tuple[str, int, str]:
    """Run iverilog simulation against VerilogEval testbench.
    Returns (status, mismatches, detail_output).
    status: "sim_pass", "sim_fail", "sim_error"
    """
    ref_sv_path = DATASET_DIR / f"{prob_id}_ref.sv"
    test_sv_path = DATASET_DIR / f"{prob_id}_test.sv"

    if not ref_sv_path.exists() or not test_sv_path.exists():
        return "sim_error", -1, f"Missing ref or test SV for {prob_id}"

    ref_sv = ref_sv_path.read_text()
    test_sv = test_sv_path.read_text()

    # Parse ports
    sparkle_mod_name, sparkle_ports = parse_module_ports(generated_sv)
    ref_ports = parse_ref_ports(ref_sv)

    if not sparkle_mod_name:
        return "sim_error", -1, "Could not parse Sparkle module name"

    # Generate TopModule wrapper
    wrapper = generate_top_wrapper(sparkle_mod_name, sparkle_ports, ref_ports)
    if not wrapper:
        return "sim_error", -1, "Could not generate TopModule wrapper"

    # Write files to temp dir
    sim_dir = run_dir / "sim" / prob_id
    sim_dir.mkdir(parents=True, exist_ok=True)

    (sim_dir / "sparkle_dut.sv").write_text(generated_sv)
    (sim_dir / "wrapper.sv").write_text(wrapper)
    (sim_dir / "ref.sv").write_text(ref_sv)
    (sim_dir / "test.sv").write_text(test_sv)

    # Compile with iverilog
    compile_cmd = [
        "iverilog", "-g2012", "-o", str(sim_dir / "sim.vvp"),
        str(sim_dir / "ref.sv"),
        str(sim_dir / "sparkle_dut.sv"),
        str(sim_dir / "wrapper.sv"),
        str(sim_dir / "test.sv"),
    ]
    try:
        comp = subprocess.run(
            compile_cmd, capture_output=True, text=True, timeout=30
        )
        if comp.returncode != 0:
            err = comp.stderr[:500]
            (sim_dir / "compile_error.txt").write_text(comp.stderr)
            return "sim_error", -1, f"iverilog compile failed:\n{err}"
    except subprocess.TimeoutExpired:
        return "sim_error", -1, "iverilog compile timeout"

    # Run simulation
    try:
        sim = subprocess.run(
            ["vvp", str(sim_dir / "sim.vvp")],
            capture_output=True, text=True, timeout=timeout
        )
        sim_output = sim.stdout + sim.stderr
        (sim_dir / "sim_output.txt").write_text(sim_output)
    except subprocess.TimeoutExpired:
        return "sim_error", -1, "Simulation timeout"

    # Parse results: look for "Mismatches: N in M samples"
    m = re.search(r"Mismatches:\s*(\d+)\s+in\s+(\d+)\s+samples", sim_output)
    if m:
        mismatches = int(m.group(1))
        total = int(m.group(2))
        if mismatches == 0:
            return "sim_pass", 0, f"0 mismatches in {total} samples"
        else:
            return "sim_fail", mismatches, f"{mismatches} mismatches in {total} samples"

    # Check for TIMEOUT in simulation
    if "TIMEOUT" in sim_output:
        return "sim_error", -1, "Simulation hit internal timeout"

    return "sim_error", -1, f"Could not parse simulation output:\n{sim_output[:300]}"


# ---------------------------------------------------------------------------
# Build runner
# ---------------------------------------------------------------------------

def run_build(prob_id: str, timeout: int = 120) -> tuple[bool, str]:
    """Run `lake build Generated.<module>` and return (success, output)."""
    module = prob_id  # e.g., "Prob001_zero"
    try:
        result = subprocess.run(
            ["lake", "build", f"Generated.{module}"],
            cwd=str(SPARKLE_ROOT),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        output = result.stdout + result.stderr
        success = result.returncode == 0
        return success, output
    except subprocess.TimeoutExpired:
        return False, f"BUILD TIMEOUT after {timeout}s"
    except Exception as e:
        return False, f"BUILD ERROR: {e}"

# ---------------------------------------------------------------------------
# Generated.lean maintenance
# ---------------------------------------------------------------------------

def update_generated_lean(prob_id: str):
    """Add import line to Generated.lean if not present."""
    gen_lean = SPARKLE_ROOT / "Generated.lean"
    content = gen_lean.read_text()
    import_line = f"import Generated.{prob_id}"
    if import_line not in content:
        content = content.rstrip() + f"\n{import_line}\n"
        gen_lean.write_text(content)

# ---------------------------------------------------------------------------
# Results tracking
# ---------------------------------------------------------------------------

def init_run_dir() -> Path:
    """Create a timestamped run directory."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = RESULTS_DIR / f"run_{ts}"
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def save_result(run_dir: Path, result: Result):
    """Append result to JSONL."""
    jsonl_path = run_dir / "results.jsonl"
    with open(jsonl_path, "a") as f:
        f.write(json.dumps(asdict(result)) + "\n")


def save_summary(run_dir: Path, results: list[Result], wall_time: float):
    """Write summary.json."""
    total = len(results)
    passed = sum(1 for r in results if r.status == "pass")
    failed = sum(1 for r in results if r.status == "fail")
    errors = sum(1 for r in results if r.status == "error")
    first_attempt = sum(1 for r in results if r.status == "pass" and r.attempts == 1)
    retry_fixed = passed - first_attempt
    total_in = sum(r.tokens_in for r in results)
    total_out = sum(r.tokens_out for r in results)

    summary = {
        "total": total, "passed": passed, "failed": failed, "errors": errors,
        "pass_rate": f"{passed/total*100:.1f}%" if total else "0%",
        "first_attempt_pass": first_attempt, "retry_fixed": retry_fixed,
        "sim_pass": sum(1 for r in results if r.sim_status == "sim_pass"),
        "sim_fail": sum(1 for r in results if r.sim_status == "sim_fail"),
        "sim_error": sum(1 for r in results if r.sim_status == "sim_error"),
        "total_tokens_in": total_in, "total_tokens_out": total_out,
        "wall_time_s": round(wall_time, 1),
    }
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary

# ---------------------------------------------------------------------------
# Core: process one problem
# ---------------------------------------------------------------------------

def process_problem(
    problem: Problem,
    few_shots: list[FewShot],
    api_key: str,
    base_url: str,
    model: str,
    max_retries: int,
    build_timeout: int,
    run_dir: Path,
    dry_run: bool = False,
) -> Result:
    """Generate Sparkle code for one problem with retry loop."""
    total_in, total_out = 0, 0
    error_feedback = None
    last_code = ""
    build_time = 0.0

    for attempt in range(1, max_retries + 2):  # +2 because range is exclusive
        # Build prompt
        messages = build_messages(problem, few_shots, error_feedback)

        if dry_run:
            print(f"    [DRY RUN] Would call LLM with {len(messages)} messages")
            return Result(problem.prob_id, "skip", attempt)

        # Call LLM
        try:
            response, tok_in, tok_out = call_llm(messages, api_key, base_url, model)
            total_in += tok_in
            total_out += tok_out
        except Exception as e:
            return Result(problem.prob_id, "error", attempt, total_in, total_out, error_msg=str(e))

        # Extract code
        code = extract_lean_code(response)
        last_code = code

        # Write file
        lean_path = GENERATED_DIR / f"{problem.prob_id}.lean"
        lean_path.write_text(code + "\n")

        # Update Generated.lean
        update_generated_lean(problem.prob_id)

        # Build
        t0 = time.time()
        success, build_output = run_build(problem.prob_id, build_timeout)
        build_time = time.time() - t0

        if success:
            # Extract generated Verilog and run simulation
            generated_sv = extract_verilog_from_build(build_output)
            sim_status, sim_mismatches, sim_detail = "", -1, ""

            if generated_sv:
                print(f"    ✓ attempt {attempt}: compiled+synthesized ({build_time:.1f}s), simulating...")
                sim_status, sim_mismatches, sim_detail = run_iverilog_sim(
                    problem.prob_id, generated_sv, run_dir
                )
                if sim_status == "sim_pass":
                    print(f"    ✓ simulation: PASS ({sim_detail})")
                elif sim_status == "sim_fail":
                    print(f"    ✗ simulation: FAIL ({sim_detail})")
                else:
                    print(f"    ⚠ simulation: ERROR ({sim_detail[:80]})")
            else:
                print(f"    ✓ attempt {attempt}: compiled ({build_time:.1f}s), no Verilog to simulate")

            return Result(
                problem.prob_id, "pass", attempt, total_in, total_out,
                build_time, sim_status, sim_mismatches,
            )

        # Build failed — prepare error feedback for retry
        error_lines = build_output.strip().splitlines()
        if len(error_lines) > 100:
            error_lines = error_lines[:50] + ["... (truncated) ..."] + error_lines[-50:]
        error_feedback = "\n".join(error_lines)

        if attempt <= max_retries:
            print(f"    ✗ attempt {attempt}: FAIL, retrying...")
        else:
            print(f"    ✗ attempt {attempt}: FAIL (final)")

    return Result(
        problem.prob_id, "fail", max_retries + 1,
        total_in, total_out, build_time,
        error_msg=error_feedback[:500] if error_feedback else None,
    )

# ---------------------------------------------------------------------------
# Check resume status
# ---------------------------------------------------------------------------

def get_passed_problems(run_dir: Path | None) -> set[str]:
    """Read the latest results.jsonl to find already-passed problems."""
    passed = set()
    if not run_dir:
        # Find latest run dir
        if not RESULTS_DIR.exists():
            return passed
        dirs = sorted(RESULTS_DIR.iterdir(), reverse=True)
        if not dirs:
            return passed
        run_dir = dirs[0]

    jsonl = run_dir / "results.jsonl"
    if not jsonl.exists():
        return passed
    for line in jsonl.read_text().splitlines():
        try:
            r = json.loads(line)
            if r.get("status") == "pass":
                passed.add(r["problem_id"])
        except json.JSONDecodeError:
            continue
    return passed

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    args = parse_args()

    # Load API config
    env = load_env(SPARKLE_ROOT / "key.env")
    api_key = env.get("ANTHROPIC_API_KEY", os.environ.get("ANTHROPIC_API_KEY", ""))
    base_url = env.get("ANTHROPIC_BASE_URL", os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com"))

    if not api_key and not args.dry_run:
        print("ERROR: No API key found. Set ANTHROPIC_API_KEY in key.env or environment.", file=sys.stderr)
        sys.exit(1)

    # Load few-shot examples
    print("Loading few-shot examples...")
    few_shots = load_few_shots()
    print(f"  Loaded {len(few_shots)} few-shot examples")

    # Discover problems
    print("Discovering problems...")
    problems = discover_problems(limit=args.limit, filt=args.filter)
    print(f"  Found {len(problems)} problems to process")

    # Resume: skip passed
    if args.resume:
        passed = get_passed_problems(None)
        before = len(problems)
        problems = [p for p in problems if p.prob_id not in passed]
        print(f"  Resuming: skipping {before - len(problems)} already-passed problems")

    if not problems:
        print("No problems to process.")
        return

    # Init run directory
    run_dir = init_run_dir()
    print(f"  Results → {run_dir}")

    # Ensure Generated/ directory exists
    GENERATED_DIR.mkdir(exist_ok=True)

    # Process each problem
    results = []
    t_start = time.time()

    for i, prob in enumerate(problems, 1):
        print(f"\n[{i}/{len(problems)}] {prob.prob_id}")
        result = process_problem(
            prob, few_shots, api_key, base_url,
            args.model, args.max_retries, args.timeout, run_dir, args.dry_run,
        )
        results.append(result)
        save_result(run_dir, result)

    wall_time = time.time() - t_start

    # Summary
    summary = save_summary(run_dir, results, wall_time)
    print(f"\n{'='*60}")
    print(f"Results: {summary['passed']}/{summary['total']} compiled "
          f"({summary['pass_rate']})")
    print(f"  First attempt: {summary['first_attempt_pass']}, "
          f"Retry fixed: {summary['retry_fixed']}")
    print(f"  Simulation: {summary['sim_pass']} pass, "
          f"{summary['sim_fail']} fail, {summary['sim_error']} error")
    print(f"  Build failed: {summary['failed']}, Errors: {summary['errors']}")
    print(f"  Tokens: {summary['total_tokens_in']} in, "
          f"{summary['total_tokens_out']} out")
    print(f"  Wall time: {wall_time:.0f}s")
    print(f"  Results saved to: {run_dir}")


if __name__ == "__main__":
    main()
