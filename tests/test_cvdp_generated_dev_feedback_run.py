from __future__ import annotations

import hashlib
import importlib
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
HIDDEN_CANARY = "HIDDEN_HOLDOUT_CANARY_MUST_NOT_REACH_REPAIR"


def _args(**overrides):
    values = {
        "eval_only": False,
        "sim_feedback": True,
        "sim_feedback_turn_budget": 2,
        "sim_feedback_turns_per_iter": 1,
        "sim_feedback_max_iters": 2,
        "sim_feedback_patience": 2,
        "max_turns": 1,
        "prompt_profile": "compact",
        "cvdp_verified_idioms": False,
        "dataset": "cvdp",
        "guided_search": False,
        "no_repl": False,
        "harness": "anthropic-api",
        "model": "claude-opus-4-6",
        "cvdp_local_guardrails": True,
        "cvdp_generated_dev_feedback": True,
        "cvdp_generated_dev_seed": 0xC0D3_2026,
        "problem_file": "/tmp/cvdp12.txt",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _result(*, status: str, mismatches: int, detail: str) -> dict:
    return {
        "prob_id": "prob_a",
        "compile_pass": True,
        "sv_extracted": True,
        "lint_pass": True,
        "sim_status": status,
        "sim_mismatches": mismatches,
        "detail": detail,
    }


class _PassingRepl:
    def check_file(self, path: Path):
        return SimpleNamespace(
            passed=True,
            complete=True,
            verilog="module dut; endmodule",
            error_text="",
        )


class _PublicSuite:
    prob_id = "prob_a"
    source = "public_spec"
    seed = 0xC0D3_2026
    sha256 = "d" * 64
    version = "test-public-dev-v1"
    supported = True
    reason = None
    benchmark_ports = (("input", "logic", "a"), ("output", "logic", "y"))

    def __init__(self) -> None:
        self.public_info = SimpleNamespace(
            prob_id="prob_a",
            design_name="dut",
            prompt_text="PUBLIC SPEC ONLY: y follows a",
            ref_code="",
            ref_path=None,
            testbench_path=Path("/public/generated/test_generated.py"),
            metadata={
                "input_context_files": {},
                "benchmark_ports": list(self.benchmark_ports),
                "public_dev_source": self.source,
            },
        )

    def validate(self) -> None:
        assert self.source == "public_spec"
        assert self.public_info.ref_code == ""
        assert "cvdp_row" not in self.public_info.metadata
        assert HIDDEN_CANARY not in repr(self.public_info)


def _real_search_module():
    run_module._add_legacy_agent_path()
    return importlib.import_module("search")


def _fake_search(
    suite: _PublicSuite,
    *,
    feedback_calls: list[dict],
    prompt_calls: list[dict],
    ranking_inputs: list[dict],
    timeline: list[str],
) -> ModuleType:
    real_search = _real_search_module()
    module = ModuleType("search")
    module.build_cvdp_typed_scaffold = lambda info: (
        pytest.fail("scaffold received non-public ProblemInfo")
        if info is not suite.public_info
        else SCAFFOLD
    )

    def build_user_message(*args, **kwargs):
        assert kwargs["info"] is suite.public_info
        assert HIDDEN_CANARY not in repr(kwargs)
        timeline.append("initial_prompt")
        return "PUBLIC INITIAL PROMPT"

    module.build_user_message = build_user_message
    module.build_sim_feedback = lambda **kwargs: pytest.fail(
        "hidden-capable build_sim_feedback must not run in generated-dev mode"
    )

    def build_public_dev_feedback(**kwargs):
        assert HIDDEN_CANARY not in repr(kwargs)
        feedback_calls.append(
            {
                "iteration": kwargs["iteration"],
                "result": dict(kwargs["result"]),
                "history": [dict(row) for row in kwargs["history"]],
                "provenance": dict(kwargs["provenance"]),
            }
        )
        timeline.append(f"public_feedback_{kwargs['iteration'] + 1}")
        return real_search.build_public_dev_feedback(**kwargs)

    module.build_public_dev_feedback = build_public_dev_feedback

    def build_compact_repair_prompt(**kwargs):
        assert kwargs["info"] is suite.public_info
        assert HIDDEN_CANARY not in repr(kwargs)
        prompt = real_search.build_compact_repair_prompt(**kwargs)
        assert HIDDEN_CANARY not in prompt
        prompt_calls.append({**kwargs, "rendered": prompt})
        timeline.append(f"repair_prompt_{kwargs['iteration']}")
        return prompt

    module.build_compact_repair_prompt = build_compact_repair_prompt

    def eval_progress_key(result):
        assert HIDDEN_CANARY not in repr(result)
        ranking_inputs.append(dict(result) if result else {})
        return real_search.eval_progress_key(result)

    module.eval_progress_key = eval_progress_key
    module.summarize_eval_result = real_search.summarize_eval_result
    module.classify_failure_record = lambda result: {}
    module._benchmark_expected_ports = lambda info: (
        [("input", "logic", "hidden_port")]
        if HIDDEN_CANARY in repr(info)
        else pytest.fail("public iterations must use suite.benchmark_ports directly")
    )
    return module


def _run_generated_dev_flow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    mutate_during_holdout: bool = False,
    conflicting_holdout_audit: bool = False,
) -> tuple[dict, dict]:
    suite = _PublicSuite()
    target = tmp_path / "Generated" / "prob_a.lean"
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    feedback_calls: list[dict] = []
    prompt_calls: list[dict] = []
    ranking_inputs: list[dict] = []
    public_eval_calls: list[dict] = []
    runner_prompts: list[str] = []
    timeline: list[str] = []
    load_calls: list[str] = []
    frozen_sha_at_load: list[str] = []

    fake_search = _fake_search(
        suite,
        feedback_calls=feedback_calls,
        prompt_calls=prompt_calls,
        ranking_inputs=ranking_inputs,
        timeline=timeline,
    )
    monkeypatch.setitem(sys.modules, "search", fake_search)
    monkeypatch.setattr(run_module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(
        run_module,
        "_build_cvdp_public_dev_suite",
        lambda prob_id, dataset_path, *, seed: suite,
    )

    writes = iter(("PUBLIC_INITIAL", "PUBLIC_REPAIR_ONE", "FROZEN_FINAL"))

    class WriterRunner:
        def __init__(self, source: str, role: str) -> None:
            self.source = source
            self.role = role

        def run(self, prompt: str, *, max_turns: int):
            assert HIDDEN_CANARY not in prompt
            runner_prompts.append(prompt)
            timeline.append(f"runner_{self.role}")
            target.write_text(self.source, encoding="utf-8")
            return AgentStats(turns=1)

    def make_runner(**kwargs):
        assert kwargs["info"] is suite.public_info
        assert HIDDEN_CANARY not in repr(kwargs)
        return WriterRunner(next(writes), kwargs["role"])

    monkeypatch.setattr(run_module, "make_runner", make_runner)

    hidden_info = SimpleNamespace(
        prob_id="prob_a",
        design_name="dut",
        prompt_text=HIDDEN_CANARY,
        ref_code=HIDDEN_CANARY,
        metadata={"cvdp_row": HIDDEN_CANARY},
    )

    def load_problem(prob_id: str):
        assert prob_id == "prob_a"
        assert target.read_text(encoding="utf-8") == "FROZEN_FINAL"
        assert len(public_eval_calls) == 3
        assert len(runner_prompts) == 3
        events = (run_dir / "events.jsonl").read_text(encoding="utf-8")
        assert "cvdp_candidate_frozen_for_hidden_holdout" in events
        load_calls.append(prob_id)
        frozen_sha_at_load.append(hashlib.sha256(target.read_bytes()).hexdigest())
        timeline.append("hidden_loaded_after_freeze")
        return hidden_info

    ds = SimpleNamespace(
        dataset_dir=tmp_path / "public-only.jsonl",
        load_problem=load_problem,
    )

    public_results = {
        "PUBLIC_INITIAL": _result(
            status="sim_fail", mismatches=3, detail="PUBLIC_COUNTEREXAMPLE_0"
        ),
        "PUBLIC_REPAIR_ONE": _result(
            status="sim_fail", mismatches=1, detail="PUBLIC_COUNTEREXAMPLE_1"
        ),
        "FROZEN_FINAL": _result(
            status="sim_pass", mismatches=0, detail="PUBLIC_DEV_ALL_PASS"
        ),
    }
    hidden_result = _result(
        status="sim_fail", mismatches=7, detail=HIDDEN_CANARY
    )
    if conflicting_holdout_audit:
        hidden_result["hidden_holdout_feedback_exposed"] = True

    class Evaluator:
        dataset_name = "cvdp"

        def evaluate_public_dev(self, prob_id: str, dev_run_dir: Path, supplied_suite):
            assert not load_calls
            assert supplied_suite is suite
            assert dev_run_dir == run_dir / "public_dev"
            source = target.read_text(encoding="utf-8")
            public_eval_calls.append(
                {"source": source, "run_dir": dev_run_dir, "suite": supplied_suite}
            )
            timeline.append(f"public_eval_{source}")
            return dict(public_results[source])

        def evaluate(self, prob_id: str, holdout_run_dir: Path, **kwargs):
            assert load_calls == ["prob_a"]
            assert kwargs["problem_info"] is hidden_info
            assert kwargs["benchmark_ports"] == [
                ("input", "logic", "hidden_port")
            ]
            assert holdout_run_dir == run_dir
            assert target.read_text(encoding="utf-8") == "FROZEN_FINAL"
            timeline.append("hidden_holdout")
            if mutate_during_holdout:
                target.write_text("MUTATED_BY_HOLDOUT", encoding="utf-8")
            return dict(hidden_result)

    context = {
        "suite": suite,
        "target": target,
        "feedback_calls": feedback_calls,
        "prompt_calls": prompt_calls,
        "ranking_inputs": ranking_inputs,
        "public_eval_calls": public_eval_calls,
        "runner_prompts": runner_prompts,
        "timeline": timeline,
        "load_calls": load_calls,
        "frozen_sha_at_load": frozen_sha_at_load,
        "hidden_result": hidden_result,
    }
    record = run_module._process_problem_standard(
        "prob_a",
        args=_args(),
        ds=ds,
        evaluator=Evaluator(),
        run_dir=run_dir,
        skill="",
        repl=_PassingRepl(),
        candidate_transaction=None,
    )
    return record, context


def test_generated_dev_repairs_are_public_only_then_holdout_runs_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record, ctx = _run_generated_dev_flow(tmp_path, monkeypatch)

    assert [call["source"] for call in ctx["public_eval_calls"]] == [
        "PUBLIC_INITIAL",
        "PUBLIC_REPAIR_ONE",
        "FROZEN_FINAL",
    ]
    assert ctx["load_calls"] == ["prob_a"]
    assert ctx["timeline"].index("hidden_loaded_after_freeze") > ctx["timeline"].index(
        "public_eval_FROZEN_FINAL"
    )
    assert ctx["timeline"][-1] == "hidden_holdout"

    assert len(ctx["feedback_calls"]) == 2
    assert ctx["feedback_calls"][0]["history"] == []
    second_history = ctx["feedback_calls"][1]["history"]
    assert len(second_history) == 1
    assert second_history[0]["feedback_source"] == "public_spec"
    assert second_history[0]["public_dev_seed"] == _PublicSuite.seed
    assert second_history[0]["public_dev_suite_sha256"] == _PublicSuite.sha256
    assert second_history[0]["public_dev_suite_version"] == _PublicSuite.version
    assert "Previous Public Dev-Test Repair Attempts" in ctx["prompt_calls"][1][
        "latest_feedback"
    ]

    assert all(HIDDEN_CANARY not in repr(item) for item in ctx["feedback_calls"])
    assert all(HIDDEN_CANARY not in repr(item) for item in ctx["prompt_calls"])
    assert all(HIDDEN_CANARY not in repr(item) for item in ctx["ranking_inputs"])
    assert all(HIDDEN_CANARY not in prompt for prompt in ctx["runner_prompts"])

    assert record["sim_status"] == "sim_fail"
    assert record["detail"] == HIDDEN_CANARY
    assert record["public_dev_best_result"]["sim_status"] == "sim_pass"
    assert record["public_dev_best_result"]["source"] == "public_spec"
    assert record["hidden_holdout_calls"] == 1
    assert record["hidden_holdout_feedback_exposed"] is False
    assert record["hidden_holdout_candidate_unchanged"] is True
    assert record["frozen_candidate_sha256"] == ctx["frozen_sha_at_load"][0]
    assert record["post_holdout_candidate_sha256"] == record[
        "frozen_candidate_sha256"
    ]
    assert ctx["target"].read_text(encoding="utf-8") == "FROZEN_FINAL"


def test_generated_dev_rejects_holdout_candidate_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(
        RuntimeError, match="Hidden holdout mutated the frozen Lean candidate"
    ):
        _run_generated_dev_flow(
            tmp_path, monkeypatch, mutate_during_holdout=True
        )


def test_generated_dev_mode_requires_guardrails_problem_file_and_active_repl():
    run_module.validate_cvdp_generated_dev_feedback_mode(
        _args(), active_repl=True
    )

    for field, value, expected in (
        ("cvdp_local_guardrails", False, "--cvdp-local-guardrails"),
        ("problem_file", None, "--problem-file"),
    ):
        args = _args()
        setattr(args, field, value)
        with pytest.raises(ValueError, match=expected):
            run_module.validate_cvdp_generated_dev_feedback_mode(
                args, active_repl=True
            )

    with pytest.raises(ValueError, match="active Lean REPL"):
        run_module.validate_cvdp_generated_dev_feedback_mode(
            _args(), active_repl=False
        )


def test_public_dev_counterexample_survives_compact_repair_prompt() -> None:
    search = _real_search_module()
    result = _result(
        status="sim_fail",
        mismatches=1,
        detail=(
            "AssertionError: CVDP_PUBLIC_DEV_MISMATCH label=popcount "
            "input={\"a\": 7} expected=3 actual=2"
        ),
    )
    result.update({
        "source": "public_spec",
        "public_dev_seed": _PublicSuite.seed,
        "public_dev_suite_sha256": _PublicSuite.sha256,
        "public_dev_suite_version": _PublicSuite.version,
    })
    full = search.build_public_dev_feedback(
        prob_id="prob_a",
        result=result,
        iteration=0,
        history=[],
        provenance={
            "source": "public_spec",
            "derivation": "public_spec",
            "seed": _PublicSuite.seed,
            "suite_sha256": _PublicSuite.sha256,
            "suite_version": _PublicSuite.version,
        },
    )
    compact = search.compact_repair_feedback(full)

    assert "### Public Dev-Test Diagnostics" in compact
    assert "### First Public Dev-Test Failures" in compact
    assert "expected=3 actual=2" in compact


def test_public_feedback_rejects_cross_problem_and_stale_history() -> None:
    search = _real_search_module()
    provenance = {
        "source": "public_spec",
        "derivation": "public_spec",
        "seed": _PublicSuite.seed,
        "suite_sha256": _PublicSuite.sha256,
        "suite_version": _PublicSuite.version,
    }
    result = _result(status="sim_fail", mismatches=1, detail="public mismatch")
    result.update({
        "source": "public_spec",
        "public_dev_seed": _PublicSuite.seed,
        "public_dev_suite_sha256": _PublicSuite.sha256,
        "public_dev_suite_version": _PublicSuite.version,
    })

    with pytest.raises(ValueError, match="problem id"):
        search.build_public_dev_feedback(
            prob_id="different_problem",
            result=result,
            iteration=0,
            history=[],
            provenance=provenance,
        )

    stale_history = [{
        "feedback_source": "public_spec",
        "public_dev_seed": _PublicSuite.seed,
        "public_dev_suite_sha256": "stale-suite",
        "public_dev_suite_version": _PublicSuite.version,
    }]
    with pytest.raises(ValueError, match="mismatched"):
        search.build_public_dev_feedback(
            prob_id="prob_a",
            result=result,
            iteration=1,
            history=stale_history,
            provenance=provenance,
        )


def test_generated_dev_rejects_holdout_audit_field_overwrite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(
        RuntimeError,
        match="conflicting generated-dev audit field",
    ):
        _run_generated_dev_flow(
            tmp_path,
            monkeypatch,
            conflicting_holdout_audit=True,
        )
