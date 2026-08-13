from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "agent"))

import search  # noqa: E402
from dataset import ProblemInfo  # noqa: E402
from report import generate_html  # noqa: E402


def _info(harness: str, ref_code: str = "") -> ProblemInfo:
    return ProblemInfo(
        prob_id="parameterized_dut",
        design_name="dut",
        prompt_text="",
        ref_code=ref_code,
        testbench_path=Path("dummy.jsonl"),
        ref_path=None,
        metadata={
            "dataset": "cvdp",
            "harness_files": {"src/test_runner.py": harness},
        },
    )


class _DirectSVEvaluatorStub:
    dataset_name = "cvdp"
    enable_synth = True
    enable_pnr = True

    def __init__(self) -> None:
        self.synth_calls = 0
        self.pnr_calls = 0
        self.synth_tops: list[str] = []
        self.pnr_tops: list[str] = []

    def _run_lint(self, _sv_file: Path) -> bool:
        return True

    def _run_sim_cvdp(self, *_args, **kwargs):
        assert kwargs["direct_top"] is True
        return "sim_pass", 0, "all parameter configurations passed"

    def _run_synthesis(self, *_args):
        self.synth_calls += 1
        self.synth_tops.append(_args[2])
        return {
            "synth_pass": True,
            "area_um2": 12.5,
            "cell_count": 7,
            "wns_ns": 0.2,
            "power_uw": 1.5,
        }

    def _run_pnr(self, *_args):
        self.pnr_calls += 1
        self.pnr_tops.append(_args[2])
        return {"pnr_pass": True}


@pytest.mark.parametrize(
    "harness",
    [
        'runner.build(parameters={"WIDTH": WIDTH})',
        "runner.build(parameters=params)",
    ],
    ids=["known-parameter", "unresolved-parameter-dict"],
)
def test_direct_sv_parameter_sweep_keeps_simulation_but_skips_ppa(
    tmp_path: Path,
    harness: str,
):
    evaluator = _DirectSVEvaluatorStub()
    code = """
    module dut #(parameter WIDTH = 8) (
        input logic [WIDTH-1:0] data_in,
        output logic [WIDTH-1:0] data_out
    );
        assign data_out = data_in;
    endmodule
    """

    result = search.evaluate_verilog_candidate(
        "parameterized_dut",
        _info(harness),
        code,
        evaluator,
        tmp_path,
    )

    assert result["sim_status"] == "sim_pass"
    assert result["sim_mismatches"] == 0
    assert result["parameterized_ppa_unsupported"] is True
    assert result["synth_status"] == "not_run_parameterized_sweep"
    assert result["ppa_status"] == "unsupported_parameter_sweep"
    assert result["synth_pass"] is False
    assert result["pnr_pass"] is False
    assert result["area_um2"] is None
    assert result["cell_count"] is None
    assert result["wns_ns"] is None
    assert result["power_uw"] is None
    assert "module defaults" in result["ppa_error"]
    assert evaluator.synth_calls == 0
    assert evaluator.pnr_calls == 0


def test_direct_sv_without_harness_parameters_still_runs_synthesis_and_pnr(
    tmp_path: Path,
):
    evaluator = _DirectSVEvaluatorStub()
    code = "module dut(input logic data_in, output logic data_out); assign data_out = data_in; endmodule"

    result = search.evaluate_verilog_candidate(
        "parameterized_dut",
        _info("runner.build()"),
        code,
        evaluator,
        tmp_path,
    )

    assert result["sim_status"] == "sim_pass"
    assert result["parameterized_ppa_unsupported"] is False
    assert result["synth_pass"] is True
    assert result["pnr_pass"] is True
    assert result["area_um2"] == 12.5
    assert evaluator.synth_calls == 1
    assert evaluator.pnr_calls == 1


def test_direct_hierarchical_sv_synthesizes_the_cvdp_top_not_first_helper(
    tmp_path: Path,
):
    evaluator = _DirectSVEvaluatorStub()
    code = """
    module child(input logic x, output logic y);
        assign y = x;
    endmodule
    module dut(input logic data_in, output logic data_out);
        child u_child(.x(data_in), .y(data_out));
    endmodule
    """

    result = search.evaluate_verilog_candidate(
        "parameterized_dut",
        _info("runner.build()"),
        code,
        evaluator,
        tmp_path,
    )

    assert result["sim_status"] == "sim_pass"
    assert result["synth_pass"] is True
    assert evaluator.synth_tops == ["dut"]
    assert evaluator.pnr_tops == ["dut"]


def test_native_top_parameter_skips_ppa_even_without_harness_override(
    tmp_path: Path,
):
    evaluator = _DirectSVEvaluatorStub()
    code = """
    module dut #(parameter WIDTH = 8) (
        input logic [WIDTH-1:0] data_in,
        output logic [WIDTH-1:0] data_out
    );
        assign data_out = data_in;
    endmodule
    """

    result = search.evaluate_verilog_candidate(
        "parameterized_dut",
        _info("runner.build()"),
        code,
        evaluator,
        tmp_path,
    )

    assert result["sim_status"] == "sim_pass"
    assert result["parameterized_ppa_unsupported"] is True
    assert result["area_um2"] is None
    assert evaluator.synth_calls == 0
    assert evaluator.pnr_calls == 0


def test_reference_parameter_skips_ppa_when_build_alias_is_indirect(
    tmp_path: Path,
):
    evaluator = _DirectSVEvaluatorStub()
    reference = """
    module dut #(parameter WIDTH = 8) (
        output logic [WIDTH-1:0] data_out
    );
    endmodule
    """
    harness = """
    compile_dut = getattr(runner, "build")
    kwargs = {"parameters": {"WIDTH": 8}}
    compile_dut(**kwargs)
    """

    result = search.evaluate_verilog_candidate(
        "parameterized_dut",
        _info(harness, reference),
        reference,
        evaluator,
        tmp_path,
    )

    assert result["parameterized_ppa_unsupported"] is True
    assert evaluator.synth_calls == 0


def test_parameterized_ppa_skip_does_not_enter_synthesis_feedback():
    args = SimpleNamespace(synth_feedback=True)
    result = {
        "sim_status": "sim_pass",
        "synth_pass": False,
        "parameterized_ppa_unsupported": True,
    }

    assert search._should_run_synth_feedback(args, {}, result) is False
    result["parameterized_ppa_unsupported"] = False
    assert search._should_run_synth_feedback(args, {}, result) is True


def test_parameterized_verilog_ppa_loop_returns_before_llm(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    evaluator = _DirectSVEvaluatorStub()
    result = {
        "sim_status": "sim_pass",
        "synth_pass": True,
        "pnr_pass": True,
        "gds_generated": True,
        "drc_pass": True,
        "drc_violations": 0,
        "lvs_pass": True,
        "gls_synth_status": "sim_pass",
        "gls_synth_mismatches": 0,
        "gls_pnr_status": "sim_pass",
        "gls_pnr_mismatches": 0,
        "area_um2": 99.0,
        "cell_count": 20,
        "wns_ns": -0.1,
        "power_uw": 3.0,
    }

    def fail_before_llm(*_args, **_kwargs):
        raise AssertionError("parameterized PPA loop must skip before loading LLM credentials")

    monkeypatch.setattr(search, "load_env", fail_before_llm)

    returned, history = search.run_verilog_ppa_loop(
        "parameterized_dut",
        _info('runner.build(parameters={"WIDTH": 8})'),
        SimpleNamespace(),
        evaluator,
        tmp_path,
        result,
        {},
    )

    assert returned is result
    assert history == []
    assert returned["parameterized_ppa_unsupported"] is True
    assert returned["synth_status"] == "not_run_parameterized_sweep"
    assert returned["ppa_status"] == "unsupported_parameter_sweep"
    assert returned["area_um2"] is None
    assert returned["gds_generated"] is False
    assert returned["drc_pass"] is None
    assert returned["lvs_pass"] is None
    assert returned["gls_synth_status"] == "not_run"
    assert returned["gls_pnr_status"] == "not_run"


def test_report_renders_parameterized_ppa_as_skipped(tmp_path: Path):
    html = generate_html(
        {
            "attempted": 1,
            "synth_enabled": True,
            "pnr_enabled": True,
        },
        [{
            "prob_id": "parameterized_dut",
            "compile_pass": True,
            "lint_pass": True,
            "sim_status": "sim_pass",
            "parameterized_ppa_unsupported": True,
            "synth_status": "not_run_parameterized_sweep",
            "ppa_status": "unsupported_parameter_sweep",
        }],
        tmp_path,
    )

    assert html.count('class="badge na">Skipped</span>') == 2
    assert '<div class="card-value">0<small>/0</small></div>' in html
