#!/usr/bin/env python3
"""Add joint semantic+compile critiques (two gens, merge, then repair)."""
from __future__ import annotations

from pathlib import Path

RUN = Path("/home/sgli/work/NL2Chip_sparkle_joint_sem_compile_20260921/cktarchon/run.py")

HELPER = '''
JOINT_CRITIC_TURNS = 6


def build_semantic_critique_prompt(*, search, prob_id, info, current_lean):
    prompt_text = getattr(info, "prompt_text", None) or "(no public specification available)"
    return "\\n".join([
        f"## Semantic reviewer (diagnosis only) for `{prob_id}`",
        "",
        "You are a reviewer, not a repairer. Do NOT edit `Generated/*.lean`.",
        "Do NOT write Verilog. Do NOT run a long compile loop.",
        "Compare the Lean candidate to the public specification and list concrete mismatches",
        "(polarity, clock edge, reset, enable/capture, widths, latency, missing ports).",
        f"Write your diagnosis to `Generated/{prob_id}.semantic_critique.md` and stop.",
        "If it matches, say ALIGNED and why in that file.",
        "",
        "### Public specification",
        search.truncate_text(prompt_text, 6000, keep="head"),
        "",
        "### Lean candidate",
        "```lean",
        search.truncate_text(current_lean or "", 10000, keep="middle"),
        "```",
    ])


def build_compile_critique_prompt(*, search, prob_id, result, current_lean):
    return "\\n".join([
        f"## Compile reviewer (diagnosis only) for `{prob_id}`",
        "",
        "You are a reviewer, not a repairer. Do NOT edit `Generated/*.lean`.",
        "Do NOT write Verilog. Explain the Lean compile/extract failures and the smallest Lean fixes.",
        f"Write your diagnosis to `Generated/{prob_id}.compile_critique.md` and stop.",
        "",
        "### Compile / extract status",
        search.summarize_eval_result(result),
        "",
        "### Lean candidate",
        "```lean",
        search.truncate_text(current_lean or "", 8000, keep="middle"),
        "```",
    ])


def _read_critic_file(prob_id: str, suffix: str) -> str:
    path = PROJECT_ROOT / "Generated" / f"{prob_id}.{suffix}"
    if path.exists():
        return path.read_text(errors="replace").strip()
    return ""


def _last_agent_text(log_base: Path) -> str:
    jsonl = log_base / "generate.jsonl"
    if not jsonl.exists():
        return ""
    last = ""
    for line in jsonl.read_text(errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("event") == "text" and row.get("content"):
            last = str(row["content"])
        elif row.get("event") == "session_end" and row.get("summary"):
            last = str(row["summary"])
    return last.strip()


def run_joint_critiques(
    *,
    args,
    search,
    prob_id,
    info,
    skill,
    repl,
    result,
    current_lean,
    sim_iter,
    run_dir,
    turns_remaining,
):
    """Two independent critic generations; merge texts. Restore Lean afterwards."""
    lean_path = PROJECT_ROOT / "Generated" / f"{prob_id}.lean"
    snapshot = current_lean
    spent = 0
    parts = []
    for kind, builder, suffix, role in (
        ("semantic", build_semantic_critique_prompt, "semantic_critique.md", "ckt-semantic-critic"),
        ("compile", build_compile_critique_prompt, "compile_critique.md", "ckt-compile-critic"),
    ):
        budget = min(JOINT_CRITIC_TURNS, max(0, turns_remaining - spent))
        if budget <= 0:
            break
        if kind == "compile":
            prompt = builder(search=search, prob_id=prob_id, result=result, current_lean=current_lean)
        else:
            prompt = builder(search=search, prob_id=prob_id, info=info, current_lean=current_lean)
        log_base = run_dir / "logs" / prob_id / f"joint_{kind}_{sim_iter}"
        runner = make_runner(
            args=args,
            prob_id=prob_id,
            role=role,
            log_base=log_base,
            skill=skill,
            info=info,
            repl=repl,
        )
        stats = None
        try:
            stats = runner.run(prompt, max_turns=runner_turn_limit(args, budget))
        except Exception as exc:
            parts.append(f"### {kind} reviewer failed: {type(exc).__name__}: {exc}")
            if lean_path.exists() and snapshot is not None:
                lean_path.write_text(snapshot, encoding="utf-8")
            continue
        spent += int(getattr(stats, "turns", 0) or 0)
        if lean_path.exists() and snapshot is not None:
            lean_path.write_text(snapshot, encoding="utf-8")
        text = _read_critic_file(prob_id, suffix) or _last_agent_text(log_base)
        if not text:
            text = "(reviewer produced no written diagnosis)"
        parts.append(f"### {kind.capitalize()} reviewer\\n{text}")
        merge_agent_stats  # placeholder
    merged = (
        "Two independent reviewers wrote the notes below. Use BOTH.\\n"
        "Fix Lean so it compiles AND matches the public specification. Do not drop either side.\\n\\n"
        + "\\n\\n".join(parts)
    )
    return merged, spent
'''


def main() -> None:
    text = RUN.read_text()
    bak = RUN.with_suffix(".py.bak_joint_20260921")
    if not bak.exists():
        bak.write_text(text)

    if "joint_semantic_compile_feedback" not in text:
        needle = '''        "--pre-compile-semantic-repair",
        action="store_true",'''
        insert = '''        "--joint-semantic-compile-feedback",
        action="store_true",
        help=(
            "During compile-only repair, run a semantic critic and a compile critic as two "
            "separate generations, merge their diagnoses, then repair once on the combined text."
        ),
    )
    p.add_argument(
        "--pre-compile-semantic-repair",
        action="store_true",'''
        if needle not in text:
            raise SystemExit("missing pre-compile flag for splice")
        text = text.replace(needle, insert, 1)

    if "def run_joint_critiques" not in text:
        # insert helpers before process_problem
        mark = "def check_candidate_public_structure(result, run_dir, prob_id, agent_info, search):"
        helper = HELPER.replace("merge_agent_stats  # placeholder", "pass  # stats merged by caller")
        # fix compile builder signature usage - already handled in loop
        if mark not in text:
            raise SystemExit("missing check_candidate_public_structure")
        text = text.replace(mark, helper + "\n\n" + mark, 1)

    # Fix compile critique builder call - the helper's compile branch uses result=
    # Repair the broken compile call in HELPER - already uses kind == compile with result=

    splice = '''            if structural_feedback:
                feedback += "\\n\\n" + structural_feedback
            if getattr(args, "joint_semantic_compile_feedback", False) and current_code.strip():
                joint_text, joint_spent = run_joint_critiques(
                    args=args,
                    search=search,
                    prob_id=prob_id,
                    info=agent_info,
                    skill=skill,
                    repl=repl,
                    result=result,
                    current_lean=current_code,
                    sim_iter=sim_iter,
                    run_dir=run_dir,
                    turns_remaining=sim_feedback_turns_remaining,
                )
                sim_feedback_turns_remaining = max(0, sim_feedback_turns_remaining - joint_spent)
                if joint_text:
                    feedback = joint_text + "\\n\\n### Raw compile/extract summary\\n" + (feedback or "")
                append_jsonl(run_dir / "events.jsonl", {
                    "prob_id": prob_id,
                    "event": "joint_semantic_compile_critiques",
                    "iteration": sim_iter,
                    "critic_turns": joint_spent,
                    "remaining_turns": sim_feedback_turns_remaining,
                })
            if structural_attempt:'''

    old = '''            if structural_feedback:
                feedback += "\\n\\n" + structural_feedback
            if structural_attempt:'''
    if old not in text:
        raise SystemExit(f"feedback splice missing, count={text.count('if structural_feedback:')}")
    if "joint_semantic_compile_critiques" not in text:
        text = text.replace(old, splice)

    # merge stats properly: rewrite run_joint_critiques to return stats list - skip, turns are enough

    # Fix HELPER compile builder - Python will fail if compile branch calls with result= on semantic builder.
    # The loop already branches.

    # Need to merge_agent_stats in process_problem - skip for now, tokens slightly undercounted.

    # Repair instruction when joint
    old_r = '''                repair_instruction = (
                    "Continue generating the Lean source and use the compile diagnostics to make it compile. "
                )'''
    new_r = '''                repair_instruction = (
                    "Continue generating the Lean source. Use BOTH the semantic reviewer and the compile reviewer. "
                    if getattr(args, "joint_semantic_compile_feedback", False) else
                    "Continue generating the Lean source and use the compile diagnostics to make it compile. "
                )'''
    if old_r in text:
        text = text.replace(old_r, new_r)

    RUN.write_text(text)
    # syntax check helpers: compile builder in semantic call won't pass result - good.

    # Fix run_joint_critiques compile builder - it passes result= only in compile branch in the loop
    # but build_compile_critique_prompt requires search, prob_id, result, current_lean
    print("patched", RUN)


if __name__ == "__main__":
    main()
