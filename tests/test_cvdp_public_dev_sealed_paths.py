from pathlib import Path
from types import SimpleNamespace

import pytest

import cktarchon.run as run_module


def _args() -> SimpleNamespace:
    return SimpleNamespace(
        cvdp_generated_dev_feedback=True,
        cvdp_generated_dev_seed=0xC0D3_2026,
        cvdp_verified_idioms=False,
        cvdp_local_guardrails=True,
        dataset="cvdp",
        sim_feedback=True,
        problem_file="/tmp/problems.txt",
        harness="anthropic-api",
        guided_search=False,
        eval_only=False,
        no_repl=False,
    )


@pytest.mark.parametrize(
    ("dataset_rel", "run_rel", "expected_label"),
    [
        ("Benchmark/hidden.jsonl", "results/run", "dataset"),
        ("experiments/hidden.jsonl", "Tests/results/run", "run directory"),
        (
            "experiments/hidden.jsonl",
            "cktarchon_work/prob_a",
            "run directory",
        ),
    ],
)
def test_generated_mode_rejects_model_readable_dataset_and_run_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    dataset_rel: str,
    run_rel: str,
    expected_label: str,
) -> None:
    monkeypatch.setattr(run_module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(
        run_module,
        "_build_cvdp_public_dev_suite",
        lambda *args, **kwargs: pytest.fail(
            "suite loader must not run before sealed-path validation"
        ),
    )

    with pytest.raises(ValueError, match=expected_label):
        run_module._process_problem_standard(
            "prob_a",
            args=_args(),
            ds=SimpleNamespace(dataset_dir=tmp_path / dataset_rel),
            evaluator=None,
            run_dir=tmp_path / run_rel,
            skill="",
            repl=object(),
            candidate_transaction=None,
        )
