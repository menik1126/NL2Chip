"""Opt-in, lossless public-spec prompts shared by both CVDP generators.

The evaluation top-module label is retained as routing metadata. Port facts,
reset polarity, parameter cases, and behavior are never inferred from a harness.
"""

import copy
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Any


POLICY = "public-spec-v2"
PUBLIC_SPEC_RULES = """## Public specification interpretation
- Derive the complete interface and behavior from the full public specification and public context below. There is no authoritative machine-inferred port summary.
- For modification tasks, explicit requested changes take precedence over the affected parts of older context RTL. Preserve unaffected behavior. Implement newly requested ports and functionality even when absent from the old module header or an example.
- An interface list in one paragraph may be incomplete: reconcile it with the entire behavioral description and public examples. Do not silently delete a specified feature to fit an incomplete list.
- Verilog numeric literals in prose or examples are values, not port declarations. Do not invent ports from literals, helper variable names, or headings.
- Obtain widths, signedness, reset assertion/deassertion levels, active clock edges, and cycle latency from explicit public behavior. A name suffix is only a hint and cannot override that behavior. If prose is contradictory, state the ambiguity and implement the interpretation supported by the full public context.
- Preserve real parameterization for every public width/depth/configuration. Keep derived widths as expressions of their source parameters; do not turn them into unrelated fixed defaults or enumerate a private test set.
- In the existing generation or compile-repair reasoning, organize the public requirements into a concise inventory with columns: item | requirement | public source | evidence status. Do this before the first successful compile, not as a new review phase after compilation.
- Cover inputs/outputs (name, direction, width, signedness), independent parameters and derived dimensions, clock edges, reset levels and behavior, cycle latency and its reference event, and explicitly requested changes. Group related items where they share the same public source.
- For each public source, cite a short quotation from the public prompt or a context filename plus the relevant declaration or statement. Do not invent quotations, line numbers, ports, parameter defaults, or timing requirements.
- Use evidence status explicit, inferred, ambiguous, or unspecified. An inference is not an authoritative interface fact. Mark missing requirements unspecified; for conflicts, record the public alternatives and the interpretation you implement. Never resolve them using hidden evaluator information.
- Keep the inventory in the current reasoning, not a separate file or model call. It is an aid to implementing the public specification, not a new validation gate. Do not add tests, extra compiler calls solely for the inventory, or a post-compile review. Existing action limits and stop conditions still apply.
- During compile repair, correct an earlier candidate's interface when it contradicts the public specification. The previous candidate is not an interface authority.
- Hidden testbench files, parameter sweeps, reference solutions, and prior benchmark results are not task inputs. Do not search for them or infer requirements from them.
"""


def enabled(info: Any) -> bool:
    return (getattr(info, "metadata", {}) or {}).get("interface_prompt_policy") == POLICY


def public_problem_info(info: Any) -> Any:
    metadata = getattr(info, "metadata", {}) or {}
    if metadata.get("dataset") != "cvdp":
        raise ValueError(f"{POLICY} currently supports only CVDP")
    row = metadata.get("cvdp_row")
    if not isinstance(row, dict) or "input" not in row:
        raise ValueError(f"{POLICY} requires the original public input object")
    public_input = row["input"]
    if isinstance(public_input, str):
        specification, context = public_input, {}
    elif isinstance(public_input, dict):
        specification = public_input.get("prompt", "")
        context = public_input.get("context", {}) or {}
    else:
        raise ValueError("CVDP input must be a string or an object")
    if not isinstance(specification, str) or not isinstance(context, dict):
        raise ValueError("Public specification/context has an unsupported shape")
    if not all(isinstance(k, str) and isinstance(v, str) for k, v in context.items()):
        raise ValueError("Public context must map filenames to source strings")
    public = copy.copy(info)
    public.prompt_text = specification
    public.ref_code = "\n\n".join(context.values())
    public.testbench_path = None
    public.ref_path = None
    public.metadata = {
        "dataset": "cvdp",
        "interface_prompt_policy": POLICY,
        "agent_input_policy": "public-input-plus-top-module-label",
        "input_context_files": dict(context),
    }
    return public


def public_packet(info: Any) -> str:
    if not enabled(info):
        raise ValueError("Expected a public-spec-v2 problem view")
    chunks = ["### Full Public Specification\n\n" + info.prompt_text]
    for name, code in sorted(info.metadata["input_context_files"].items()):
        chunks.append(f"### Public Context: {name}\n\n```systemverilog\n{code}\n```")
    return "\n\n".join(chunks)


def generation_prompt(prob_id: str, info: Any, language: str) -> str:
    if language not in {"lean", "verilog"}:
        raise ValueError(language)
    target = f"Generated/{prob_id}.lean" if language == "lean" else f"cktarchon_work/{prob_id}/candidate.sv"
    rules = (
        "Write one complete Sparkle HDL / Lean 4 design. For public parameter-dependent widths or depths, "
        "retain top-level Nat binders and use #synthesizeParameterizedVerilog (or its Design form for hierarchy) "
        "with defaults justified by the public input. Otherwise use #synthesizeVerilog. "
        "Use supported Signal operations and explicit named outputs. Run the prescribed Lean check and fix "
        "compiler errors. Stop after a successful check emits Verilog; the outer evaluator runs simulation."
        if language == "lean" else
        "Write complete synthesizable SystemVerilog-2012, including all public parameters and required child "
        "modules. Stop after writing the candidate; the outer evaluator performs compilation and simulation."
    )
    return (
        f"## Problem: {prob_id}\n\n"
        f"### Evaluation Top-Module Label\n\n`{info.design_name}`\n\n"
        "This label routes the generated design to the evaluator; it does not override the public interface or behavior.\n\n"
        + PUBLIC_SPEC_RULES + "\n" + public_packet(info)
        + f"\n\n### Implementation Task\n\n{rules}\nWrite only `{target}`. "
        "No hidden simulation feedback is available.\n"
    )


def _diagnostic_items(result: dict[str, Any]) -> list[dict[str, Any]]:
    diagnostics = result.get("lean_diagnostics") or {}
    if isinstance(diagnostics, list):
        return [item for item in diagnostics if isinstance(item, dict)]
    if not isinstance(diagnostics, dict):
        return []
    items: list[dict[str, Any]] = []
    for key in ("errors", "messages"):
        for item in diagnostics.get(key, []) or []:
            if isinstance(item, dict):
                items.append(item)
            elif isinstance(item, str):
                items.append({"message": item})
    return items


def _lean_compile_guidance(items: list[dict[str, Any]]) -> str:
    codes = {str(item.get("code", "")) for item in items}
    text = "\n".join(
        str(item.get("message") or item.get("data") or item.get("summary") or "")
        for item in items
    ).lower()
    hints: list[str] = []
    if "retained_parameter_not_top_level" in codes:
        hints.append(
            "Retained public parameters must be direct top-level Nat binders of the synthesized definition, "
            "for example `def dut {dom : DomainConfig} {WIDTH : Nat} ...`; the parameterized synthesis command "
            "must reference that same definition. Do not replace retained parameters with fixed-width aliases."
        )
    if "lean_typeclass_stuck" in codes:
        hints.append(
            "A stuck shift/concat/operator typeclass usually means a width or domain is still a metavariable. "
            "Add explicit `Signal dom (BitVec W)` annotations to shift amounts, concat operands, mux branches, "
            "and intermediate bindings before rechecking."
        )
    if "lean_hardware_type_inference" in codes:
        hints.append(
            "`Cannot infer hardware type` usually needs concrete state/output types. Annotate `Signal.loop` "
            "state, register payloads, mux branches, and tuple outputs; build declared tuple outputs with nested "
            "`bundle2` or sized `++`, not an untyped polymorphic helper."
        )
    if "unsupported_hardware_definition" in codes or "nat.rec" in text or "brec" in text or "fail to show termination" in text:
        hints.append(
            "Do not use ordinary recursive Lean helpers, `Nat.rec`, termination-dependent functions, or partial "
            "defs whose result contains `Signal`. Rewrite as a supported Signal expression, a fixed mux/tree for "
            "small public widths, or a checked Sparkle RTL helper such as `mapBits`, `generateBitsWithIndex`, "
            "`mapChunksWithIndex`, `popCount`, or the Hamming layout helpers when applicable."
        )
    if "unknown_lean_api" in codes or "unknown identifier" in text:
        hints.append(
            "Unknown identifiers are candidate-source errors. Define the missing local binding before use, import "
            "the public Sparkle helper that actually exists, or remove placeholder names; do not leave `sorry`, "
            "`admit`, or dummy undefined helper modules."
        )
    if "lean_syntax_error" in codes:
        hints.append(
            "Repair the Lean syntax first. The target file must be a complete Lean module with imports, one top-level "
            "synthesized definition, and the required synthesis command."
        )
    if "expected register reset literal" in text or "register reset literal" in text:
        hints.append(
            "Register power-up/reset initializers must be concrete payload literals such as `0#W` or `false`; do not "
            "use a retained parameter or `Signal` expression as the `dff`/register initializer. Load parameterized "
            "reset values through next-state logic after reset instead."
        )
    if "expected nat" in text and "_uniq" in text:
        hints.append(
            "An `Expected Nat, got _uniq.*` diagnostic usually means a hardware `Signal` value leaked into a type-level "
            "width or parameter position. Keep Nat parameters and hardware signals separate; compute widths only from "
            "top-level Nat binders and use `Signal.mux` for data-dependent choices."
        )
    if not hints:
        return ""
    return "### Targeted Lean/Sparkle Repair Guidance\n" + "\n".join(f"- {hint}" for hint in hints)


def _parameter_contract_guidance(result: dict[str, Any]) -> str:
    detail = str(result.get("detail") or "")
    hints = [
        "Keep every retained public parameter as a direct top-level Nat binder of the synthesized definition.",
        "Make public port widths syntactically derive from those binders, including derived dimensions such as `$clog2(...)` or products/sums of retained parameters.",
        "Do not expose derived constants as output ports, duplicate a name as both parameter and port, or collapse a parameterized packed output to one concrete width.",
    ]
    names = sorted(set(re.findall(r"public port `([A-Za-z_][A-Za-z0-9_]*)`", detail)))
    if names:
        hints.append(
            "The checker could not validate parameter-derived widths for public port(s): "
            + ", ".join(f"`{name}`" for name in names)
            + "."
        )
    if "could not map every parameter, input, and output" in detail:
        hints.append(
            "The wrapper could not map all public parameters/inputs/outputs; check exact public names, directions, and packed output construction."
        )
    return "### Public Parameter Contract Repair Guidance\n" + "\n".join(f"- {hint}" for hint in hints)


def compile_feedback(result: dict[str, Any], *, candidate_sv: str | None = None, top: str | None = None) -> str:
    """Use structured candidate compiler diagnostics, never log/XML/VCD readers."""
    if result.get("sim_status") in {"sim_pass", "sim_fail"}:
        raise ValueError("Simulation results cannot enter public-spec compile feedback")
    if result.get("failure_category") == "missing_source":
        return (
            "No Lean candidate source was written to the required target file. Create the complete "
            "Generated/<prob_id>.lean source before running the compiler check. This is a delivery "
            "failure, not simulation evidence."
        )
    if result.get("failure_stage") == "parameter_contract":
        return (
            "The exported parameterized interface failed compatibility checking. Reconcile all ports, "
            "parameter-dependent widths/depths, derived dimensions, and packed-output fields with the public input. "
            "No private parameter case or expected simulation value is provided.\n\n"
            + _parameter_contract_guidance(result)
        )
    messages = []
    diagnostic_items = _diagnostic_items(result)
    for item in diagnostic_items:
        if item.get("severity", "error") != "error":
            continue
        message = item.get("data") or item.get("message") or item.get("summary")
        if isinstance(message, str):
            messages.append(message)
    if messages:
        guidance = _lean_compile_guidance(diagnostic_items)
        return (
            "Candidate Lean/Sparkle compiler errors:\n"
            + "\n".join(dict.fromkeys(messages))
            + ("\n\n" + guidance if guidance else "")
        )
    if candidate_sv is not None:
        if not candidate_sv.strip():
            return "No SystemVerilog candidate has been written. Write the complete module before compilation."
        if not top:
            raise ValueError("Standalone SV compilation requires the evaluation top-module label")
        with tempfile.TemporaryDirectory(prefix="ckt-public-compile-") as tmp:
            source = Path(tmp) / "candidate.sv"
            source.write_text(candidate_sv)
            try:
                checked = subprocess.run(
                    ["iverilog", "-g2012", "-s", top, "-o", str(Path(tmp) / "compiled"), str(source)],
                    capture_output=True, text=True, timeout=60,
                )
            except (OSError, subprocess.TimeoutExpired):
                return "Standalone candidate compiler was unavailable or timed out; no simulation evidence is provided."
        if checked.returncode:
            return "Standalone candidate compiler diagnostics (no testbench or wrapper):\n" + checked.stderr
        return (
            "The candidate compiles standalone. The outer build was not completed. Recheck the public "
            "top-module interface, parameters, and self-contained module definitions. No private build trace is provided."
        )
    # The evaluator's free-form detail may include private wrapper or simulation
    # text. Without a structured candidate diagnostic, do not relay that field.
    return (
        "The candidate did not complete compilation/export. Check the complete source and required synthesis "
        "command using the permitted compiler tool. Reconcile the design with the full public specification."
    )


def repair_prompt(
    prob_id: str,
    info: Any,
    language: str,
    candidate: str,
    feedback: str,
    *,
    extra_instruction: str = "",
) -> str:
    fence = "lean" if language == "lean" else "systemverilog"
    instruction = (extra_instruction.strip() + "\n\n") if extra_instruction.strip() else ""
    return (
        generation_prompt(prob_id, info, language)
        + f"\n### Current Candidate (May Be Incorrect)\n\n```{fence}\n{candidate}\n```\n\n"
        + "### Compile Feedback Only\n\n" + feedback
        + "\n\n### Repair Instruction\n\n" + instruction
        + "Repair compilation and public-interface consistency. Do not run simulation.\n"
    )

def save_prompt_receipt(run_dir: Path, prob_id: str, stage: str, info: Any, system: str, prompt: str) -> None:
    folder = run_dir / "prompt_receipts" / prob_id
    folder.mkdir(parents=True, exist_ok=True)
    packet = public_packet(info)
    (folder / f"{stage}.json").write_text(json.dumps({
        "policy": POLICY, "top_module_label": info.design_name,
        "public_packet_sha256": hashlib.sha256(packet.encode()).hexdigest(),
        "system_prompt": system, "user_prompt": prompt,
    }, indent=2) + "\n")
