from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

import cktarchon.run as run_module
from cktarchon.logs import AgentStats


SCAFFOLD = """import Sparkle
-- CKTARCHON_IMPLEMENTATION_REQUIRED
#synthesizeVerilog dut
"""


def _args(**overrides):
    values = {
        "eval_only": False,
        "sim_feedback": True,
        "sim_feedback_turn_budget": 2,
        "sim_feedback_turns_per_iter": 1,
        "sim_feedback_max_iters": 2,
        "sim_feedback_patience": 1,
        "max_turns": 1,
        "prompt_profile": "compact",
        "cvdp_verified_idioms": False,
        "dataset": "cvdp",
        "guided_search": False,
        "no_repl": False,
        "harness": "anthropic-api",
        "model": "claude-opus-4-6",
        "cvdp_local_guardrails": True,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _result(*, compile_pass: bool, sim_status: str, detail: str):
    return {
        "prob_id": "prob_a",
        "compile_pass": compile_pass,
        "sv_extracted": compile_pass,
        "lint_pass": compile_pass,
        "sim_status": sim_status,
        "sim_mismatches": 0 if sim_status == "sim_pass" else -1,
        "detail": detail,
    }


def _fake_search(
    feedback_seen: list[str],
    repair_prompts: list[dict],
    idiom_feedback_seen: list[str] | None = None,
):
    module = ModuleType("search")
    module.build_cvdp_typed_scaffold = lambda info: SCAFFOLD
    module.build_user_message = lambda *args, **kwargs: "initial"
    module.build_sim_feedback = lambda **kwargs: (
        feedback_seen.append(kwargs["result"]["detail"])
        or kwargs["result"]["detail"]
    )
    module.build_compact_repair_prompt = lambda **kwargs: (
        repair_prompts.append(kwargs.copy()) or "repair"
    )
    module.eval_progress_key = lambda result: (
        int(bool(result.get("compile_pass"))),
        int(bool(result.get("sv_extracted"))),
        {"not_run": 0, "sim_fail": 2, "sim_pass": 3}.get(
            result.get("sim_status"), 0
        ),
    )
    module.summarize_eval_result = lambda result: str(result)
    module.classify_failure_record = lambda result: {}
    module.build_cvdp_idiom_query = lambda info: SimpleNamespace(
        is_sequential=True, reset_polarity="active-high"
    )
    module.cvdp_verified_idiom_catalog_sha256 = lambda: "a" * 64
    def verified_idiom_ids(*args, **kwargs):
        feedback = kwargs.get("feedback", "")
        if kwargs.get("repair") and idiom_feedback_seen is not None:
            idiom_feedback_seen.append(feedback)
        return ("packed_state_high",)

    module.cvdp_verified_idiom_ids = verified_idiom_ids
    module.format_cvdp_verified_idioms = lambda *args, **kwargs: "VERIFIED_BODY"
    return module


class _PassingRepl:
    def check_file(self, path: Path):
        return SimpleNamespace(
            passed=True,
            complete=True,
            verilog="module dut; endmodule",
            error_text="",
        )


def test_marker_fail_closed():
    passed = _result(
        compile_pass=True, sim_status="sim_pass", detail="all tests passed"
    )
    guarded = run_module.reject_incomplete_cvdp_scaffold(passed, SCAFFOLD)

    assert guarded["sim_status"] == "sim_fail"
    assert guarded["sim_mismatches"] == 1
    assert guarded["scaffold_incomplete"] is True
    assert passed["sim_status"] == "sim_pass"


def test_standard_loop_rolls_back_broken_edits_and_keeps_latest_diagnostics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    feedback_seen: list[str] = []
    repair_prompts: list[dict] = []
    fake_search = _fake_search(feedback_seen, repair_prompts)
    monkeypatch.setitem(sys.modules, "search", fake_search)
    monkeypatch.setattr(run_module, "PROJECT_ROOT", tmp_path)

    target = tmp_path / "Generated" / "prob_a.lean"

    class InitialRunner:
        def run(self, prompt: str, *, max_turns: int):
            target.write_text("BROKEN_INITIAL", encoding="utf-8")
            return AgentStats(turns=1)

    class FirstRepairRunner:
        def run(self, prompt: str, *, max_turns: int):
            target.write_text("BROKEN_REPAIR", encoding="utf-8")
            return AgentStats(turns=1)

    class SecondRepairRunner:
        def run(self, prompt: str, *, max_turns: int):
            target.write_text("GOOD", encoding="utf-8")
            return AgentStats(turns=1)

    runners = iter([InitialRunner(), FirstRepairRunner(), SecondRepairRunner()])
    monkeypatch.setattr(run_module, "make_runner", lambda **kwargs: next(runners))

    evaluated_sources: list[str] = []

    def evaluate(prob_id: str, run_dir: Path):
        source = target.read_text(encoding="utf-8")
        evaluated_sources.append(source)
        if source == "GOOD":
            return _result(
                compile_pass=True, sim_status="sim_pass", detail="good pass"
            )
        return _result(
            compile_pass=False,
            sim_status="not_run",
            detail=(
                "initial compile diagnostic"
                if source == "BROKEN_INITIAL"
                else "repair compile diagnostic"
            ),
        )

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    record = run_module._process_problem_standard(
        "prob_a",
        args=_args(),
        ds=SimpleNamespace(load_problem=lambda prob_id: SimpleNamespace()),
        evaluator=SimpleNamespace(dataset_name="cvdp", evaluate=evaluate),
        run_dir=run_dir,
        skill="",
        repl=_PassingRepl(),
        candidate_transaction=None,
    )

    assert evaluated_sources == ["BROKEN_INITIAL", "BROKEN_REPAIR", "GOOD"]
    assert feedback_seen == [
        "initial compile diagnostic",
        "repair compile diagnostic",
    ]
    assert repair_prompts[0]["current_lean"] == SCAFFOLD
    assert "rolled back" in repair_prompts[0]["extra_constraints"]
    assert repair_prompts[1]["current_lean"] == SCAFFOLD
    assert repair_prompts[1]["include_cvdp_scaffold"] is True
    assert record["sim_status"] == "sim_pass"
    assert record["sim_feedback_iterations"] == 2
    assert record["sim_feedback_history"][0]["accepted_candidate"] is False
    assert record["sim_feedback_history"][0]["candidate_changed"] is True
    assert record["sim_feedback_history"][0]["scaffold_unchanged"] is False
    assert record["sim_feedback_history"][0]["patience_consumed"] is False
    assert record["no_write_repairs"] == 0
    assert target.read_text(encoding="utf-8") == "GOOD"


def test_scaffold_no_write_does_not_consume_patience_and_keeps_idiom_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    feedback_seen: list[str] = []
    repair_prompts: list[dict] = []
    idiom_feedback_seen: list[str] = []
    fake_search = _fake_search(
        feedback_seen, repair_prompts, idiom_feedback_seen
    )
    monkeypatch.setitem(sys.modules, "search", fake_search)
    monkeypatch.setattr(run_module, "PROJECT_ROOT", tmp_path)

    target = tmp_path / "Generated" / "prob_a.lean"

    class NoWriteRunner:
        def run(self, prompt: str, *, max_turns: int):
            return AgentStats(turns=1)

    class GoodRepairRunner:
        def run(self, prompt: str, *, max_turns: int):
            target.write_text("GOOD", encoding="utf-8")
            return AgentStats(turns=1)

    runners = iter([NoWriteRunner(), NoWriteRunner(), GoodRepairRunner()])
    monkeypatch.setattr(
        run_module, "make_runner", lambda **kwargs: next(runners)
    )

    evaluated_sources: list[str] = []

    def evaluate(prob_id: str, run_dir: Path):
        source = target.read_text(encoding="utf-8")
        evaluated_sources.append(source)
        return _result(
            compile_pass=True,
            sim_status="sim_pass" if source == "GOOD" else "sim_fail",
            detail="good pass" if source == "GOOD" else "unexpected",
        )

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    record = run_module._process_problem_standard(
        "prob_a",
        args=_args(
            cvdp_verified_idioms=True,
            prompt_profile="cvdp-skill-fewshot",
            sim_feedback_patience=1,
        ),
        ds=SimpleNamespace(load_problem=lambda prob_id: SimpleNamespace()),
        evaluator=SimpleNamespace(dataset_name="cvdp", evaluate=evaluate),
        run_dir=run_dir,
        skill="",
        repl=_PassingRepl(),
        candidate_transaction=None,
    )

    assert evaluated_sources == ["GOOD"]
    assert record["sim_status"] == "sim_pass"
    assert record["sim_feedback_iterations"] == 2
    assert record["no_write_repairs"] == 1
    first_attempt = record["sim_feedback_history"][0]
    assert first_attempt["candidate_changed"] is False
    assert first_attempt["scaffold_unchanged"] is True
    assert first_attempt["patience_consumed"] is False
    assert first_attempt["accepted_candidate"] is False
    assert len(idiom_feedback_seen) == 2
    assert '"scaffold_incomplete":true' in idiom_feedback_seen[0]
    assert (
        '"cvdp_verified_idiom_initial_selected_ids":["packed_state_high"]'
        in idiom_feedback_seen[0]
    )
    assert '"is_sequential":true' in idiom_feedback_seen[0]
    assert repair_prompts[0]["latest_feedback"] == idiom_feedback_seen[0]


def test_no_write_still_consumes_patience_when_verified_idioms_are_off(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    feedback_seen: list[str] = []
    repair_prompts: list[dict] = []
    monkeypatch.setitem(
        sys.modules, "search", _fake_search(feedback_seen, repair_prompts)
    )
    monkeypatch.setattr(run_module, "PROJECT_ROOT", tmp_path)
    target = tmp_path / "Generated" / "prob_a.lean"

    class NoWriteRunner:
        def run(self, prompt: str, *, max_turns: int):
            return AgentStats(turns=1)

    runners = iter([NoWriteRunner(), NoWriteRunner()])
    monkeypatch.setattr(
        run_module, "make_runner", lambda **kwargs: next(runners)
    )
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    record = run_module._process_problem_standard(
        "prob_a",
        args=_args(cvdp_verified_idioms=False, sim_feedback_patience=1),
        ds=SimpleNamespace(load_problem=lambda prob_id: SimpleNamespace()),
        evaluator=SimpleNamespace(
            dataset_name="cvdp",
            evaluate=lambda *_args, **_kwargs: pytest.fail(
                "unchanged scaffold must short-circuit evaluator"
            ),
        ),
        run_dir=run_dir,
        skill="",
        repl=_PassingRepl(),
        candidate_transaction=None,
    )

    assert record["sim_feedback_iterations"] == 1
    first_attempt = record["sim_feedback_history"][0]
    assert first_attempt["candidate_changed"] is False
    assert first_attempt["accepted_candidate"] is True
    assert first_attempt["patience_consumed"] is True
    assert target.read_text(encoding="utf-8") == SCAFFOLD


def test_main_rejects_non_anthropic_guardrail_harness(monkeypatch):
    args = SimpleNamespace(
        key_env="/nonexistent",
        cvdp_local_guardrails=True,
        dataset="cvdp",
        harness="codex-agent",
        eval_only=False,
        guided_search=False,
        no_repl=False,
    )
    monkeypatch.setattr(run_module, "parse_args", lambda: args)
    monkeypatch.setattr(run_module, "load_env_file", lambda path: {})
    monkeypatch.setattr(
        run_module, "configure_anthropic_credentials_from_env", lambda: None
    )
    monkeypatch.setattr(run_module, "ensure_runtime_env", lambda: None)

    with pytest.raises(SystemExit, match="requires --harness anthropic-api"):
        run_module.main()


def test_verified_idiom_mode_fails_closed_outside_supported_configuration():
    args = _args(
        cvdp_verified_idioms=True,
        prompt_profile="cvdp-skill-fewshot",
    )

    run_module.validate_cvdp_verified_idiom_mode(args, active_repl=True)

    invalid_cases = (
        ("dataset", "verilogeval"),
        ("cvdp_local_guardrails", False),
        ("harness", "codex-agent"),
        ("guided_search", True),
        ("eval_only", True),
        ("no_repl", True),
        ("prompt_profile", "compact"),
    )
    for field, value in invalid_cases:
        bad = _args(
            cvdp_verified_idioms=True,
            prompt_profile="cvdp-skill-fewshot",
        )
        setattr(bad, field, value)
        with pytest.raises(ValueError, match="fail-closed"):
            run_module.validate_cvdp_verified_idiom_mode(
                bad, active_repl=True
            )

    with pytest.raises(ValueError, match="active Lean REPL"):
        run_module.validate_cvdp_verified_idiom_mode(
            args, active_repl=False
        )


def test_verified_idiom_system_prompt_replaces_static_skill_context():
    baseline = run_module.build_system_prompt(
        "SKILL_SENTINEL",
        "prob_a",
        prompt_profile="cvdp-skill-fewshot",
        cvdp_local_guardrails=True,
    )
    treatment = run_module.build_system_prompt(
        "SKILL_SENTINEL",
        "prob_a",
        prompt_profile="cvdp-skill-fewshot",
        cvdp_local_guardrails=True,
        cvdp_verified_idioms=True,
    )

    assert "SKILL_SENTINEL" in baseline
    assert "Curated Sparkle Skill" in baseline
    assert "SKILL_SENTINEL" not in treatment
    assert "Retrieved Verified Sparkle Idioms" in treatment
    assert "static skill.txt reference is intentionally not appended" in treatment
