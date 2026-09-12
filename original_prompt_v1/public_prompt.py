"""Opt-in, lossless public-spec prompts shared by both CVDP generators.

The evaluation top-module label is retained as routing metadata. Port facts,
reset polarity, parameter cases, and behavior are never inferred from a harness.
"""

import copy
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
from typing import Any


POLICY = "public-spec-v1"
PUBLIC_SPEC_RULES = """## Public specification interpretation
- Derive the complete interface and behavior from the full public specification and public context below. There is no authoritative machine-inferred port summary.
- For modification tasks, explicit requested changes take precedence over the affected parts of older context RTL. Preserve unaffected behavior. Implement newly requested ports and functionality even when absent from the old module header or an example.
- An interface list in one paragraph may be incomplete: reconcile it with the entire behavioral description and public examples. Do not silently delete a specified feature to fit an incomplete list.
- Verilog numeric literals in prose or examples are values, not port declarations. Do not invent ports from literals, helper variable names, or headings.
- Obtain widths, signedness, reset assertion/deassertion levels, active clock edges, and cycle latency from explicit public behavior. A name suffix is only a hint and cannot override that behavior. If prose is contradictory, state the ambiguity and implement the interpretation supported by the full public context.
- Preserve real parameterization for every public width/depth/configuration. Keep derived widths as expressions of their source parameters; do not turn them into unrelated fixed defaults or enumerate a private test set.
- Before writing the candidate, briefly inventory the public inputs/outputs, parameter relations, reset/clock semantics, requested additions, and latency. Reconcile that inventory with the specification before the first successful compile; this is a source review, not a request to run tests.
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
        raise ValueError("Expected a public-spec-v1 problem view")
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


def compile_feedback(result: dict[str, Any], *, candidate_sv: str | None = None, top: str | None = None) -> str:
    """Use structured candidate compiler diagnostics, never log/XML/VCD readers."""
    if result.get("sim_status") in {"sim_pass", "sim_fail"}:
        raise ValueError("Simulation results cannot enter public-spec compile feedback")
    if result.get("failure_stage") == "parameter_contract":
        return (
            "The exported parameterized interface failed compatibility checking. Reconcile all ports, "
            "parameter-dependent widths/depths, derived dimensions, and packed-output fields with the public input. "
            "No private parameter case or expected simulation value is provided."
        )
    messages = []
    diagnostics = result.get("lean_diagnostics") or {}
    if isinstance(diagnostics, list):
        diagnostics = {"messages": diagnostics}
    if isinstance(diagnostics, dict):
        for key in ("errors", "messages"):
            for item in diagnostics.get(key, []) or []:
                if isinstance(item, dict):
                    if item.get("severity", "error") != "error":
                        continue
                    message = item.get("data") or item.get("message")
                    if isinstance(message, str):
                        messages.append(message)
                elif isinstance(item, str):
                    messages.append(item)
    if messages:
        return "Candidate Lean/Sparkle compiler errors:\n" + "\n".join(dict.fromkeys(messages))
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


def repair_prompt(prob_id: str, info: Any, language: str, candidate: str, feedback: str) -> str:
    fence = "lean" if language == "lean" else "systemverilog"
    return (
        generation_prompt(prob_id, info, language)
        + f"\n### Current Candidate (May Be Incorrect)\n\n```{fence}\n{candidate}\n```\n\n"
        + "### Compile Feedback Only\n\n" + feedback
        + "\n\nRepair compilation and public-interface consistency. Do not run simulation.\n"
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
