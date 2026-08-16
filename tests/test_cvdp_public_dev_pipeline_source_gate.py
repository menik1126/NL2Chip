from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType

import pytest

import cktarchon.harness as harness


def _load_generated_flow_tests_with_two_public_evals():
    path = Path(__file__).with_name("test_cvdp_generated_dev_feedback_run.py")
    source = path.read_text(encoding="utf-8").replace(
        "assert len(public_eval_calls) == 3",
        "assert len(public_eval_calls) == 2",
    )
    name = "_cvdp_generated_flow_fixture_for_source_gate"
    module = ModuleType(name)
    module.__file__ = str(path)
    sys.modules[name] = module
    exec(compile(source, str(path), "exec"), module.__dict__)
    return module


def test_generated_pipeline_rejects_source_before_public_evaluator_and_recovers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    flow = _load_generated_flow_tests_with_two_public_evals()
    inspected: list[str] = []

    def reject_initial(source: str) -> str | None:
        inspected.append(source)
        return "test meta/IO violation" if source == "PUBLIC_INITIAL" else None

    monkeypatch.setattr(harness, "public_dev_lean_source_violation", reject_initial)

    record, context = flow._run_generated_dev_flow(tmp_path, monkeypatch)

    assert [call["source"] for call in context["public_eval_calls"]] == [
        "PUBLIC_REPAIR_ONE",
        "FROZEN_FINAL",
    ]
    assert inspected[:3] == [
        "PUBLIC_INITIAL",
        "PUBLIC_REPAIR_ONE",
        "FROZEN_FINAL",
    ]
    assert record["public_dev_best_result"]["sim_status"] == "sim_pass"
    assert record["hidden_holdout_calls"] == 1
