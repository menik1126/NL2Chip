#!/usr/bin/env python3
"""Patch a repaired Sparkle copy so semantic gating runs before RTL compile."""
from __future__ import annotations

from pathlib import Path

EVAL_TREE = Path("/home/sgli/work/NL2Chip_sparkle_precompile_semantic_20260920")
RUN = EVAL_TREE / "cktarchon" / "run.py"


def main() -> None:
    text = RUN.read_text()
    bak = RUN.with_suffix(".py.bak_precompile_semantic_20260920")
    if not bak.exists():
        bak.write_text(text)

    old_flag = '''        "--pre-sim-semantic-repair",
        action="store_true",
        help=(
            "After compile/extract, compare generated Lean to the public problem spec "
            "and repair Lean on clear deviation. Shares leftover turns with compile-only "
            "feedback; pass through when already aligned. Hidden testbenches are not used."
        ),
    )'''
    new_flag = '''        "--pre-sim-semantic-repair",
        action="store_true",
        help=(
            "After compile/extract, compare generated Lean to the public problem spec "
            "and repair Lean on clear deviation. Shares leftover turns with compile-only "
            "feedback; pass through when already aligned. Hidden testbenches are not used."
        ),
    )
    p.add_argument(
        "--pre-compile-semantic-repair",
        action="store_true",
        help=(
            "After generation and before RTL compile/extract, compare generated Lean to "
            "the public spec and repair Lean on clear deviation. Hidden testbenches are "
            "not used. Shares leftover turns under the same total-turn-budget."
        ),
    )'''
    if "--pre-compile-semantic-repair" not in text:
        if old_flag not in text:
            raise SystemExit("missing pre-sim-semantic-repair flag block")
        text = text.replace(old_flag, new_flag, 1)

    old_sig = '''    gate_only: bool,
    current_lean: str = "",
) -> str:
    prompt_text = getattr(info, "prompt_text", None) or "(no public specification available)"
    lean_code = current_lean
    if not lean_code:
        lean_path = PROJECT_ROOT / "Generated" / f"{prob_id}.lean"
        lean_code = lean_path.read_text(errors="replace") if lean_path.exists() else ""
    compile_ok = bool(result.get("compile_pass") and result.get("sv_extracted"))
    lines = [
        f"## Pre-Sim Semantic Check - Session {iteration + 1}",
        "",
        f"Judge whether `Generated/{prob_id}.lean` matches the public problem semantics.",
        "Do not treat compiled SystemVerilog as the object of this check.",
        "RTL simulation has not been run yet. Hidden testbenches and gold RTL are not allowed.",
        "This pass shares leftover turns with compile-only repair under the same total-turn-budget.",
        "If you repair, edit Lean only; never write a Verilog patch.",
        "",
        "### Compile / Extract Status",
        search.summarize_eval_result(result),
        "(Compile/extract is a prerequisite, not the semantic criterion.)",
        "",
        "### Public Specification",
        search.truncate_text(prompt_text, 8000, keep="head"),
        "",
        "### Judge checklist (find mismatches; do not rewrite a working spec as a tutorial)",
        "- Compiled / extracted Verilog is not evidence of alignment.",
'''
    new_sig = '''    gate_only: bool,
    current_lean: str = "",
    pre_compile: bool = False,
) -> str:
    prompt_text = getattr(info, "prompt_text", None) or "(no public specification available)"
    lean_code = current_lean
    if not lean_code:
        lean_path = PROJECT_ROOT / "Generated" / f"{prob_id}.lean"
        lean_code = lean_path.read_text(errors="replace") if lean_path.exists() else ""
    compile_ok = bool(result.get("compile_pass") and result.get("sv_extracted"))
    title = "Pre-Compile Semantic Check" if pre_compile else "Pre-Sim Semantic Check"
    lines = [
        f"## {title} - Session {iteration + 1}",
        "",
        f"Judge whether `Generated/{prob_id}.lean` matches the public problem semantics.",
        "The object of this check is Lean/CktLean, not compiled SystemVerilog.",
        (
            "RTL compile/extract and simulation have not been run yet."
            if pre_compile else
            "RTL simulation has not been run yet."
        ),
        "Hidden testbenches and gold RTL are not allowed.",
        "This pass shares leftover turns under the same total-turn-budget.",
        "If you repair, edit Lean only; never write a Verilog patch.",
        "",
    ]
    if pre_compile:
        lines.extend([
            "### Compile / Extract Status",
            "Not run yet. Do not try to compile Verilog in this session. Judge Lean against the spec.",
            "",
        ])
    else:
        lines.extend([
            "### Compile / Extract Status",
            search.summarize_eval_result(result),
            "(Compile/extract is a prerequisite, not the semantic criterion.)",
            "",
        ])
    lines.extend([
        "### Public Specification",
        search.truncate_text(prompt_text, 8000, keep="head"),
        "",
        "### Judge checklist (find mismatches; do not rewrite a working spec as a tutorial)",
        "- Compiled / extracted Verilog is not evidence of alignment and may not exist yet.",
'''
    if "pre_compile: bool = False" not in text:
        if old_sig not in text:
            raise SystemExit("missing build_semantic_feedback signature block")
        text = text.replace(old_sig, new_sig, 1)

    old_rule = '''        f"- `ALIGNED` only if Lean matches the spec on polarity, clock edge, reset, enable/capture, widths, and latency. Then leave `Generated/{prob_id}.lean` unchanged.",
        "- `DEVIATION` if Lean compiles but any of those are wrong, or if meaning is uncertain. Repair `Generated/{prob_id}.lean` in the same session.",
        "- Compile/extract success is not ALIGNED. A story that restates the spec is not ALIGNED.",'''
    new_rule = '''        f"- `ALIGNED` only if Lean matches the spec on polarity, clock edge, reset, enable/capture, widths, and latency. Then leave `Generated/{prob_id}.lean` unchanged.",
        "- `DEVIATION` if any of those are wrong, or if meaning is uncertain. Repair `Generated/{prob_id}.lean` in the same session.",
        "- Compile/extract success is not ALIGNED. A missing compile is not DEVIATION by itself. A story that restates the spec is not ALIGNED.",'''
    if old_rule in text:
        text = text.replace(old_rule, new_rule, 1)

    old_fs = '''Restating a Lean docstring is not a judgment. Compile/extract is not ALIGNED.'''
    new_fs = '''Restating a Lean docstring is not a judgment. Compile/extract is not ALIGNED and is not required for this gate.'''
    if old_fs in text:
        text = text.replace(old_fs, new_fs, 1)

    old_skip = '''    skip_sim = bool(getattr(args, "pre_sim_semantic_repair", False))
'''
    new_skip = '''    skip_sim = bool(getattr(args, "pre_sim_semantic_repair", False))
    pre_compile_semantic = bool(getattr(args, "pre_compile_semantic_repair", False))
    if pre_compile_semantic:
        skip_sim = True
'''
    if "pre_compile_semantic = bool" not in text:
        if old_skip not in text:
            raise SystemExit("missing skip_sim assignment")
        text = text.replace(old_skip, new_skip, 1)

    old_eval = '''    eval_t0 = time.monotonic()
    generated_target = PROJECT_ROOT / "Generated" / f"{prob_id}.lean"
    if agent_error and generated_target.exists():
        result = eval_candidate()
        result["agent_error"] = agent_error
        if result.get("detail"):
            result["detail"] = f"Agent ended with {agent_error}; evaluated generated file anyway.\\n{result['detail']}"
        else:
            result["detail"] = f"Agent ended with {agent_error}; evaluated generated file anyway."
    elif agent_error:
        result = {
            "prob_id": prob_id,
            "compile_pass": False,
            "sv_extracted": False,
            "lint_pass": False,
            "sim_status": "agent_error",
            "sim_mismatches": -1,
            "detail": agent_error,
            "agent_error": agent_error,
        }
    else:
        result = eval_candidate()
    if use_public_prompt and not args.disable_public_structural_repair:
        structural_issues = check_candidate_public_structure(result, run_dir, prob_id, agent_info, search)
'''
    new_eval = '''    eval_t0 = time.monotonic()
    generated_target = PROJECT_ROOT / "Generated" / f"{prob_id}.lean"
    defer_compile = bool(pre_compile_semantic and not args.eval_only and not agent_error)
    if agent_error and generated_target.exists():
        result = eval_candidate()
        result["agent_error"] = agent_error
        if result.get("detail"):
            result["detail"] = f"Agent ended with {agent_error}; evaluated generated file anyway.\\n{result['detail']}"
        else:
            result["detail"] = f"Agent ended with {agent_error}; evaluated generated file anyway."
    elif agent_error:
        result = {
            "prob_id": prob_id,
            "compile_pass": False,
            "sv_extracted": False,
            "lint_pass": False,
            "sim_status": "agent_error",
            "sim_mismatches": -1,
            "detail": agent_error,
            "agent_error": agent_error,
        }
    elif defer_compile:
        result = {
            "prob_id": prob_id,
            "compile_pass": False,
            "sv_extracted": False,
            "lint_pass": False,
            "sim_status": "not_run",
            "sim_mismatches": -1,
            "detail": "Pre-compile semantic: RTL compile/extract not run yet.",
        }
    else:
        result = eval_candidate()
    if use_public_prompt and not args.disable_public_structural_repair and not defer_compile:
        structural_issues = check_candidate_public_structure(result, run_dir, prob_id, agent_info, search)
'''
    if "defer_compile = bool" not in text:
        if old_eval not in text:
            raise SystemExit("missing initial eval block")
        text = text.replace(old_eval, new_eval, 1)

    old_fb = '''        args.sim_feedback
        and not args.eval_only
        and not agent_error
        and repair_eligible(result, structural_issues, structural_used, is_feedback_repairable(result, args.feedback_mode))
    ):'''
    new_fb = '''        args.sim_feedback
        and not pre_compile_semantic
        and not args.eval_only
        and not agent_error
        and repair_eligible(result, structural_issues, structural_used, is_feedback_repairable(result, args.feedback_mode))
    ):'''
    if "and not pre_compile_semantic" not in text:
        if old_fb not in text:
            raise SystemExit("missing sim_feedback guard")
        text = text.replace(old_fb, new_fb, 1)

    old_sem = '''            feedback = build_semantic_feedback(
                search=search,
                prob_id=prob_id,
                result=result,
                iteration=sem_iter - 1,
                history=semantic_history,
                run_dir=run_dir,
                info=agent_info,
                gate_only=gate_only,
                current_lean=current_code,
            )'''
    new_sem = '''            feedback = build_semantic_feedback(
                search=search,
                prob_id=prob_id,
                result=result,
                iteration=sem_iter - 1,
                history=semantic_history,
                run_dir=run_dir,
                info=agent_info,
                gate_only=gate_only,
                current_lean=current_code,
                pre_compile=pre_compile_semantic,
            )'''
    if "pre_compile=pre_compile_semantic" not in text:
        if old_sem not in text:
            raise SystemExit("missing build_semantic_feedback call")
        text = text.replace(old_sem, new_sem, 1)

    old_loop_eval = '''            after_code = lean_file.read_text(errors="replace") if lean_file.exists() else ""
            lean_changed = after_code != current_code
            gate = read_semantic_gate(prob_id)
            repair_eval_t0 = time.monotonic()
            new_result = eval_candidate(skip=True)
            eval_elapsed += time.monotonic() - repair_eval_t0
            compile_ok_after = bool(new_result.get("compile_pass") and new_result.get("sv_extracted"))
            pass_through = compile_ok_after and gate == "ALIGNED"'''
    new_loop_eval = '''            after_code = lean_file.read_text(errors="replace") if lean_file.exists() else ""
            lean_changed = after_code != current_code
            gate = read_semantic_gate(prob_id)
            if pre_compile_semantic:
                new_result = result
                compile_ok_after = True
                pass_through = gate == "ALIGNED"
            else:
                repair_eval_t0 = time.monotonic()
                new_result = eval_candidate(skip=True)
                eval_elapsed += time.monotonic() - repair_eval_t0
                compile_ok_after = bool(new_result.get("compile_pass") and new_result.get("sv_extracted"))
                pass_through = compile_ok_after and gate == "ALIGNED"'''
    if "if pre_compile_semantic:" not in text or "pass_through = gate == \"ALIGNED\"" not in text:
        if old_loop_eval not in text:
            raise SystemExit("missing semantic loop eval")
        text = text.replace(old_loop_eval, new_loop_eval, 1)

    old_end = '''        if lean_file.exists() and best_code:
            lean_file.write_text(best_code, encoding="utf-8")
            result = eval_candidate(skip=True)
'''
    new_end = '''        if lean_file.exists() and best_code:
            lean_file.write_text(best_code, encoding="utf-8")
            if not pre_compile_semantic:
                result = eval_candidate(skip=True)
'''
    if "if not pre_compile_semantic:\n                result = eval_candidate(skip=True)" not in text:
        if old_end not in text:
            raise SystemExit("missing semantic end eval")
        text = text.replace(old_end, new_end, 1)

    RUN.write_text(text)
    print("patched", RUN)


if __name__ == "__main__":
    main()
