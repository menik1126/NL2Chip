from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "agent"))

import cvdp_devtests as devtests  # noqa: E402
import evaluator as evaluator_module  # noqa: E402
from evaluator import Evaluator, parse_module_ports  # noqa: E402


PUBLIC_CVDP12 = (
    PROJECT_ROOT
    / "experiments"
    / "cvdp12_dataset"
    / "cvdp_v1.1.0_nonagentic_code_generation_no_commercial.jsonl"
)


def _real_generated_suite() -> devtests.GeneratedDevSuite:
    if not PUBLIC_CVDP12.exists():
        pytest.skip("public CVDP12 fixture is not installed")
    problem_id = "cvdp_copilot_word_reducer_0008"
    public_input = devtests.load_cvdp_public_input(PUBLIC_CVDP12, problem_id)
    suite = devtests.build_cvdp_public_dev_suite(
        public_input.prob_id,
        public_input.prompt_text,
        public_input.input_context_files,
    )
    assert type(suite) is devtests.GeneratedDevSuite
    assert suite.supported, suite.reason
    suite.validate()
    return suite


class _HiddenDatasetMustNotLoad:
    def load_problem(self, _prob_id: str):
        raise AssertionError("public dev evaluation attempted to load hidden dataset data")


def test_evaluate_public_dev_forwards_only_validated_sanitized_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    suite = _real_generated_suite()
    evaluator = Evaluator(
        project_root=tmp_path,
        dataset="cvdp",
        dataset_obj=_HiddenDatasetMustNotLoad(),
    )
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def fake_evaluate(*args, **kwargs):
        calls.append((args, kwargs))
        return {"sim_status": "sim_pass", "functional_only": True}

    monkeypatch.setattr(evaluator, "evaluate", fake_evaluate)

    result = evaluator.evaluate_public_dev(suite.prob_id, tmp_path / "run", suite)

    assert result == {
        "sim_status": "sim_pass",
        "functional_only": True,
        "prob_id": suite.prob_id,
        "source": suite.source,
        "public_dev_seed": suite.seed,
        "public_dev_suite_sha256": suite.sha256,
        "public_dev_suite_version": suite.version,
    }
    assert calls == [
        (
            (suite.prob_id, tmp_path / "run"),
            {
                "benchmark_ports": list(suite.benchmark_ports),
                "problem_info": suite.public_info,
                "functional_only": True,
            },
        )
    ]
    assert suite.public_info is not None
    assert suite.public_info.ref_code == ""
    assert suite.public_info.ref_path is None
    assert suite.public_info.metadata["public_dev_generated"] is True
    assert "cvdp_row" not in suite.public_info.metadata


def test_evaluate_public_dev_fails_closed_on_type_tampering_identity_and_dataset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    suite = _real_generated_suite()
    evaluator = Evaluator(project_root=tmp_path, dataset="cvdp")
    monkeypatch.setattr(
        evaluator,
        "evaluate",
        lambda *_args, **_kwargs: pytest.fail("invalid suite reached evaluation"),
    )

    with pytest.raises(TypeError, match="requires GeneratedDevSuite"):
        evaluator.evaluate_public_dev(suite.prob_id, tmp_path, dataclasses.asdict(suite))

    tampered = dataclasses.replace(suite, sha256="0" * 64)
    with pytest.raises(ValueError, match="metadata does not match|digest mismatch"):
        evaluator.evaluate_public_dev(suite.prob_id, tmp_path, tampered)

    with pytest.raises(ValueError, match="problem id does not match"):
        evaluator.evaluate_public_dev("cvdp_other_problem", tmp_path, suite)

    wrong_dataset = Evaluator(project_root=tmp_path, dataset="verilogeval")
    monkeypatch.setattr(
        wrong_dataset,
        "evaluate",
        lambda *_args, **_kwargs: pytest.fail("wrong dataset reached evaluation"),
    )
    with pytest.raises(ValueError, match="requires dataset_name='cvdp'"):
        wrong_dataset.evaluate_public_dev(suite.prob_id, tmp_path, suite)


def test_generated_public_info_forces_local_sim_even_when_process_mode_is_docker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    suite = _real_generated_suite()
    assert suite.public_info is not None
    evaluator = Evaluator(
        project_root=tmp_path,
        dataset="cvdp",
        dataset_obj=_HiddenDatasetMustNotLoad(),
    )
    local_calls: list[tuple[Path, bool]] = []

    def fake_local(sim_dir: Path, *, public_dev_generated: bool = False):
        local_calls.append((sim_dir, public_dev_generated))
        return "sim_pass", 0, "generated public suite ran locally"

    def forbid_process(*_args, **_kwargs):
        raise AssertionError("generated public suite attempted docker-compose")

    monkeypatch.setenv("CVDP_SIM_MODE", "docker")
    monkeypatch.setattr(evaluator, "_run_sim_cvdp_local", fake_local)
    monkeypatch.setattr(evaluator_module.subprocess, "run", forbid_process)

    sv_code = """
    module Bit_Difference_Counter #(
        parameter BIT_WIDTH = 4
    ) (
        input  logic [BIT_WIDTH-1:0] input_A,
        input  logic [BIT_WIDTH-1:0] input_B,
        output logic [$clog2(BIT_WIDTH + 1)-1:0] bit_difference_count
    );
        assign bit_difference_count = '0;
    endmodule
    """
    module_name, ports = parse_module_ports(
        sv_code, module_name=suite.public_info.design_name
    )

    result = evaluator._run_sim_cvdp(
        suite.prob_id,
        sv_code,
        module_name,
        ports,
        tmp_path / "run",
        benchmark_ports=list(suite.benchmark_ports),
        direct_top=True,
        problem_info=suite.public_info,
    )

    expected_sim_dir = tmp_path / "run" / "cvdp_sim" / suite.prob_id
    assert result == ("sim_pass", 0, "generated public suite ran locally")
    assert local_calls == [(expected_sim_dir, True)]
    assert (expected_sim_dir / "src" / "test_generated.py").is_file()
    assert not (expected_sim_dir / "docker-compose.yml").exists()


@pytest.mark.parametrize(
    ("output", "expected_status", "detail_fragment"),
    [
        (
            "AssertionError: CVDP_PUBLIC_DEV_MISMATCH expected=3 actual=2\n"
            "ERROR Failed 1 of 1 tests",
            "sim_fail",
            "public dev-test mismatch",
        ),
        (
            "ValueError: Bad period: unable to represent 10(ns)\n"
            "ERROR Failed 1 of 1 tests",
            "sim_error",
            "harness error",
        ),
    ],
)
def test_generated_public_failures_require_explicit_mismatch_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    output: str,
    expected_status: str,
    detail_fragment: str,
):
    sim_dir = tmp_path / "sim"
    (sim_dir / "src").mkdir(parents=True)
    (sim_dir / "src" / ".env").write_text("SIM=icarus\n")
    (sim_dir / "src" / "test_runner.py").write_text("# fixture\n")

    class FakeProcess:
        returncode = 1
        pid = 12345

        def communicate(self, timeout=None):
            return output, ""

    monkeypatch.setattr(
        evaluator_module.subprocess,
        "Popen",
        lambda *_args, **_kwargs: FakeProcess(),
    )
    evaluator = Evaluator(project_root=tmp_path, dataset="cvdp")

    status, mismatches, detail = evaluator._run_sim_cvdp_local(
        sim_dir,
        public_dev_generated=True,
    )

    assert status == expected_status
    assert mismatches == -1
    assert detail_fragment in detail


def test_evaluate_public_dev_rejects_conflicting_result_provenance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    suite = _real_generated_suite()
    evaluator = Evaluator(project_root=tmp_path, dataset="cvdp")
    monkeypatch.setattr(
        evaluator,
        "evaluate",
        lambda *_args, **_kwargs: {
            "prob_id": "different_problem",
            "source": "hidden_holdout",
            "sim_status": "sim_fail",
        },
    )

    with pytest.raises(ValueError, match="conflicting prob_id"):
        evaluator.evaluate_public_dev(suite.prob_id, tmp_path / "run", suite)
