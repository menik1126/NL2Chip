from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "agent"))

from dataset import ProblemInfo  # noqa: E402
from cvdp_harness_adapter import adapt_python_harness  # noqa: E402
import evaluator as evaluator_module  # noqa: E402
from evaluator import (  # noqa: E402
    Evaluator,
    _classify_cvdp_local_timeout,
    _cvdp_case_random_seed,
    _cvdp_collected_case_ids,
    _simulation_diagnostic_stage,
    generate_cvdp_wrapper,
    parse_module_ports,
)
from search import (  # noqa: E402
    build_sim_feedback,
    compact_repair_feedback,
    extract_cvdp_progress_snapshots,
    format_benchmark_interface_contract,
    semantic_repair_hints,
    summarize_eval_result,
)


def _cvdp_info(
    ref_code: str,
    harness_py: str,
    design_name: str = "dut",
    prompt_text: str = "",
) -> ProblemInfo:
    return ProblemInfo(
        prob_id="cvdp_test",
        design_name=design_name,
        prompt_text=prompt_text,
        ref_code=ref_code,
        testbench_path=Path("dummy.jsonl"),
        ref_path=None,
        metadata={
            "dataset": "cvdp",
            "harness_files": {
                "src/.env": f"TOPLEVEL={design_name}\n",
                "src/test_runner.py": harness_py,
            },
            "verilog_sources": [f"/code/rtl/{design_name}.sv"],
        },
    )


@pytest.mark.parametrize(
    ("profile", "phase_adapter", "reset_alignment", "changed_files"),
    [
        ("official", False, False, 0),
        ("race-safe-v1", True, True, 1),
    ],
)
def test_cvdp_harness_profiles_are_applied_and_audited(
    tmp_path: Path,
    monkeypatch,
    profile: str,
    phase_adapter: bool,
    reset_alignment: bool,
    changed_files: int,
):
    for name in (
        "CVDP_HARNESS_RESET_NORMALIZATION",
        "CVDP_COCOTB_PHASE_ADAPTER",
        "CVDP_COCOTB_INPUT_INITIALIZATION",
        "CVDP_COCOTB_RESET_RELEASE_ALIGNMENT",
        "CVDP_COCOTB_PROGRESS_MONITOR",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CVDP_HARNESS_PROFILE", profile)
    info = _cvdp_info(
        "module dut(input logic clk, output logic valid); endmodule",
        "import cocotb\n"
        "from cocotb.triggers import RisingEdge\n\n"
        "@cocotb.test()\n"
        "async def test_dut(dut):\n"
        "    await RisingEdge(dut.clk)\n"
        "    assert dut.valid.value == 1\n",
    )
    info.metadata["native_parameter_sweep_plan"] = {
        "expected_ports": [
            ["input", "logic", "clk"],
            ["output", "logic", "valid"],
        ],
        "reset_polarities": {},
    }
    evaluator = Evaluator(project_root=tmp_path)
    evaluator.dataset_obj = SimpleNamespace(load_problem=lambda _: info)
    monkeypatch.setattr(
        evaluator,
        "_run_sim_cvdp_local",
        lambda _sim_dir, **_kwargs: ("sim_pass", 0, "smoke passed"),
    )

    status, mismatches, _ = evaluator._run_sim_cvdp(
        "cvdp_profile_smoke",
        "module dut(input logic clk, output logic valid); "
        "assign valid = 1'b1; endmodule",
        "dut",
        [],
        tmp_path,
        direct_top=True,
        problem_info=info,
    )

    assert (status, mismatches) == ("sim_pass", 0)
    sim_dir = tmp_path / "cvdp_sim" / "cvdp_profile_smoke"
    manifest = json.loads(
        (sim_dir / "cvdp_harness_adapter.json").read_text()
    )
    source = (sim_dir / "src" / "test_runner.py").read_text()
    assert manifest["harness_profile"] == profile
    assert manifest["harness_profile_overrides"] == {}
    assert manifest["effective_options"] == {
        "normalize_reset_helpers": False,
        "stabilize_cocotb_edges": phase_adapter,
        "initialize_cocotb_inputs": False,
        "align_reset_release": reset_alignment,
        "emit_progress_monitor": False,
        "emit_assertion_trace": False,
    }
    assert manifest["changed_file_count"] == changed_files
    assert ("await Timer(1, unit='step')" in source) is phase_adapter


def test_cvdp_timeout_after_test_start_is_repairable_progress_failure(
    tmp_path: Path,
):
    output = """
    0.00ns INFO cocotb Running tests
    0.00ns INFO cocotb.regression running test_divider.test_divider (1/1)
    """

    status, mismatches, detail = _classify_cvdp_local_timeout(output, 180)

    assert status == "sim_fail"
    assert mismatches == -1
    assert "functional progress failure" in detail
    assert "done/valid" in detail
    assert _simulation_diagnostic_stage(status, detail) == "simulation_mismatch"

    feedback = build_sim_feedback(
        prob_id="cvdp_test",
        result={
            "compile_pass": True,
            "sv_extracted": True,
            "lint_pass": True,
            "sim_status": status,
            "sim_mismatches": mismatches,
            "synth_pass": False,
            "detail": detail,
        },
        iteration=3,
        history=[],
        run_dir=tmp_path,
    )
    compact = compact_repair_feedback(feedback)

    assert "### Cleaned Simulator Diagnostics" in compact
    assert "simulation timed out after 180s" in compact
    assert "done/valid" in compact


def test_cvdp_timeout_before_test_start_remains_infrastructure_error():
    status, mismatches, detail = _classify_cvdp_local_timeout(
        "pytest session started but simulator never initialized",
        180,
    )

    assert status == "sim_error"
    assert mismatches == -1
    assert detail == "CVDP local simulation timeout after 180s"
    assert _simulation_diagnostic_stage(status, detail) == "infrastructure"


def test_cvdp_progress_snapshots_survive_compact_repair_feedback(tmp_path: Path):
    info = _cvdp_info(
        """
        module axi_demo(
            input logic axi_aclk,
            input logic axi_arvalid,
            output logic axi_arready,
            input logic axi_rready,
            output logic axi_rvalid
        ); endmodule
        """,
        """
        async def test_axi(dut):
            dut.axi_arvalid.value = 1
            dut.axi_rready.value = 1
            int(dut.axi_arready.value)
            int(dut.axi_rvalid.value)
        """,
        design_name="axi_demo",
    )
    sim_dir = tmp_path / "cvdp_sim" / "cvdp_test"
    sim_dir.mkdir(parents=True)
    simulator_output = """
[CVDP_PROGRESS after=50ns] axi_arvalid=1 axi_arready=1 axi_rready=1 axi_rvalid=0
[CVDP_PROGRESS after=100ns] axi_arvalid=0 axi_arready=1 axi_rready=1 axi_rvalid=0
"""
    (sim_dir / "cvdp_local_output.txt").write_text(simulator_output)
    result = {
        "compile_pass": True,
        "sv_extracted": True,
        "lint_pass": True,
        "sim_status": "sim_fail",
        "sim_mismatches": 1,
        "synth_pass": False,
        "detail": "CVDP simulation timed out after 45s",
    }

    feedback = build_sim_feedback(
        prob_id="cvdp_test",
        result=result,
        iteration=0,
        history=[],
        run_dir=tmp_path,
        info=info,
    )
    compact = compact_repair_feedback(feedback)

    assert "### Protocol Progress Snapshots" in compact
    assert "axi_arvalid=1 axi_arready=1" in compact
    assert "### Targeted Semantic Repair" in compact
    assert "hold response data/valid" in compact


def test_semantic_repair_hints_identify_reset_gating_and_lifo_boundary():
    info = _cvdp_info(
        """
        module stack_demo(
            input logic clock,
            input logic reset,
            input logic write_en,
            input logic read_en,
            input logic [7:0] data_in,
            output logic [7:0] data_out,
            output logic empty,
            output logic full
        ); endmodule
        """,
        """
        async def test_stack(dut):
            dut.write_en.value = 1
            dut.read_en.value = 1
            dut.data_in.value = 3
            int(dut.data_out.value)
            int(dut.empty.value)
            int(dut.full.value)
        """,
        design_name="stack_demo",
    )
    result = {
        "detail": (
            "Elapsed time register after reset: Expected 0, got 9\n"
            "Expected 11, got 5"
        )
    }

    hints = semantic_repair_hints(result, info, "")

    assert "gate its normal evolution" in hints
    assert "all-ones pointer as a full sentinel" in hints
    assert "pointer-1" in hints
    assert "combinational `regFile1R1W` result directly" in hints
    assert "every parameter width returns zero" in hints


def test_semantic_repair_hints_distinguish_axi_datapath_from_handshake_timeout():
    info = _cvdp_info(
        """
        module axi_demo(
            input logic axi_aclk,
            input logic axi_awvalid,
            output logic axi_awready,
            input logic axi_wvalid,
            output logic axi_wready,
            input logic axi_arvalid,
            output logic axi_arready,
            input logic axi_rready,
            output logic axi_rvalid
        ); endmodule
        """,
        """
        async def test_axi(dut):
            dut.axi_awvalid.value = 1
            dut.axi_wvalid.value = 1
            dut.axi_arvalid.value = 1
            dut.axi_rready.value = 1
            int(dut.axi_awready.value)
            int(dut.axi_wready.value)
            int(dut.axi_arready.value)
            int(dut.axi_rvalid.value)
        """,
        design_name="axi_demo",
    )
    result = {
        "detail": "Countdown value mismatch: wrote 664960494, read 0",
    }

    hints = semantic_repair_hints(
        result,
        info,
        "[CVDP_PROGRESS after=100ns] axi_awready=1 axi_wready=1",
    )

    assert "write-commit/register-map datapath failure" in hints
    assert "independently captured AW and W" in hints
    assert "honor WSTRB" in hints


def test_progress_snapshot_extractor_deduplicates_result_and_log_copies():
    line = "[CVDP_PROGRESS after=100ns] ready=1 valid=0"

    assert extract_cvdp_progress_snapshots(f"{line}\nnoise\n{line}") == line


def test_eval_summary_does_not_call_disabled_synthesis_a_failure():
    summary = summarize_eval_result({
        "compile_pass": True,
        "sv_extracted": True,
        "lint_pass": True,
        "sim_status": "sim_fail",
        "synth_pass": False,
        "ppa_status": "not_run",
    })

    assert "Synthesis:" not in summary


def test_eval_summary_reports_an_attempted_synthesis_failure():
    summary = summarize_eval_result({
        "compile_pass": True,
        "sv_extracted": True,
        "lint_pass": True,
        "sim_status": "sim_pass",
        "synth_attempted": True,
        "synth_pass": False,
        "ppa_status": "failed",
    })

    assert "- Synthesis: fail" in summary


def test_cvdp_collect_parser_returns_absolute_parameter_case_ids(tmp_path: Path):
    runner = tmp_path / "src" / "test_runner.py"
    output = """
../src/test_runner.py::test_width[2]
../src/test_runner.py::test_width[4]

2 tests collected in 0.01s
"""

    assert _cvdp_collected_case_ids(output, runner) == [
        f"{runner}::test_width[2]",
        f"{runner}::test_width[4]",
    ]


def test_cvdp_case_seed_is_path_independent_stable_and_case_distinct():
    first = _cvdp_case_random_seed(
        "/tmp/run_a/src/test_runner.py::test_width[2]", 1729
    )
    replay = _cvdp_case_random_seed(
        "/different/run_b/src/test_runner.py::test_width[2]", 1729
    )
    other_case = _cvdp_case_random_seed(
        "/tmp/run_a/src/test_runner.py::test_width[4]", 1729
    )

    assert first == replay
    assert first != other_case
    assert 0 < first < 2**32


def test_cvdp_isolated_random_parameter_cases_are_reproducible(
    tmp_path: Path,
    monkeypatch,
):
    sim_dir = tmp_path / "sim"
    (sim_dir / "src").mkdir(parents=True)
    (sim_dir / "src" / ".env").write_text("PYTHONPATH=/code/src\n")
    (sim_dir / "src" / "test_runner.py").write_text(
        "import random\n"
        "import pytest\n\n"
        "WIDTHS = [random.randint(2, 64) for _ in range(4)]\n\n"
        "@pytest.mark.parametrize('width', WIDTHS)\n"
        "def test_width(width):\n"
        "    assert 2 <= width <= 64\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CVDP_LOCAL_RANDOM_SEED", "1729")
    monkeypatch.setenv("CVDP_LOCAL_TIMEOUT", "30")
    monkeypatch.setenv("CVDP_LOCAL_CASE_TIMEOUT", "5")

    result = Evaluator(project_root=tmp_path)._run_sim_cvdp_local(sim_dir)

    assert result[0] == "sim_pass", result[2]
    manifest = json.loads((sim_dir / "cvdp_case_results.json").read_text())
    assert manifest["collection_random_seed"] == 1729
    assert len(manifest["cases"]) == 4
    assert {row["status"] for row in manifest["cases"]} == {"sim_pass"}


def test_cvdp_slow_case_retries_with_remaining_global_budget(
    tmp_path: Path,
    monkeypatch,
):
    sim_dir = tmp_path / "sim"
    (sim_dir / "src").mkdir(parents=True)
    (sim_dir / "src" / ".env").write_text(
        "PYTHONPATH=/code/src\nWAVE=True\n"
    )
    test_runner = sim_dir / "src" / "test_runner.py"
    test_runner.write_text("def test_width(): pass\n")
    case_id = f"{test_runner}::test_width"

    def fake_collect(*args, **kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout="../src/test_runner.py::test_width\n\n1 test collected\n",
            stderr="",
        )

    attempts = []
    wave_values = []
    case_seeds = []

    def fake_case(command, *, cwd, env, timeout_s):
        attempts.append(timeout_s)
        wave_values.append(env.get("WAVE"))
        case_seeds.append(env.get("COCOTB_RANDOM_SEED"))
        if len(attempts) == 1:
            return None, "0.00ns INFO cocotb Running tests\n", True
        return 0, "1 passed\n", False

    monkeypatch.setattr(evaluator_module.subprocess, "run", fake_collect)
    monkeypatch.setattr(evaluator_module, "_run_cvdp_pytest_command", fake_case)
    monkeypatch.setenv("CVDP_LOCAL_TIMEOUT", "30")
    monkeypatch.setenv("CVDP_LOCAL_CASE_TIMEOUT", "5")

    result = Evaluator(project_root=tmp_path)._run_sim_cvdp_local(sim_dir)

    assert result[0] == "sim_pass"
    assert len(attempts) == 2
    assert attempts[0] == 5
    assert attempts[1] > attempts[0]
    assert wave_values == [None, None]
    assert case_seeds[0] == case_seeds[1]
    manifest = json.loads((sim_dir / "cvdp_case_results.json").read_text())
    assert manifest["waves_enabled"] is False
    assert manifest["progress_timeout_patience"] == 2
    assert manifest["cases"] == [{
        "case": case_id,
        "status": "sim_pass",
        "returncode": 0,
        "timed_out": False,
        "timeout_seconds": 5,
        "random_seed": _cvdp_case_random_seed(case_id, 0),
        "attempts": 2,
        "detail": "CVDP case passed",
        "retry_timeout_seconds": attempts[1],
    }]


def test_cvdp_stops_sweep_after_repeated_no_progress_timeouts(
    tmp_path: Path,
    monkeypatch,
):
    sim_dir = tmp_path / "sim"
    (sim_dir / "src").mkdir(parents=True)
    (sim_dir / "src" / ".env").write_text("PYTHONPATH=/code/src\n")
    test_runner = sim_dir / "src" / "test_runner.py"
    test_runner.write_text("def test_width(): pass\n")
    case_ids = [f"{test_runner}::test_width[{width}]" for width in (8, 16, 32)]

    def fake_collect(*args, **kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout="\n".join(
                f"../src/test_runner.py::test_width[{width}]"
                for width in (8, 16, 32)
            ),
            stderr="",
        )

    attempts = []

    def fake_case(command, *, cwd, env, timeout_s):
        attempts.append(command[-1])
        return None, "0.00ns INFO cocotb Running tests\n", True

    monkeypatch.setattr(evaluator_module.subprocess, "run", fake_collect)
    monkeypatch.setattr(evaluator_module, "_run_cvdp_pytest_command", fake_case)
    monkeypatch.setenv("CVDP_LOCAL_TIMEOUT", "60")
    monkeypatch.setenv("CVDP_LOCAL_CASE_TIMEOUT", "5")

    result = Evaluator(project_root=tmp_path)._run_sim_cvdp_local(sim_dir)

    assert result[0] == "sim_fail"
    assert attempts == case_ids[:2]
    manifest = json.loads((sim_dir / "cvdp_case_results.json").read_text())
    assert [row["status"] for row in manifest["cases"]] == [
        "sim_fail",
        "sim_fail",
        "not_run",
    ]
    assert "2 consecutive functional progress timeouts" in (
        manifest["cases"][2]["detail"]
    )


def test_cvdp_timeout_diagnostic_preserves_scored_harness_and_score(
    tmp_path: Path,
    monkeypatch,
):
    info = _cvdp_info(
        "module dut(input logic clk, input logic start, output logic valid); endmodule",
        "import cocotb\n"
        "from cocotb.triggers import RisingEdge\n\n"
        "@cocotb.test()\n"
        "async def test_dut(dut):\n"
        "    dut.start.value = 1\n"
        "    await RisingEdge(dut.clk)\n"
        "    assert dut.valid.value == 1\n",
    )
    evaluator = Evaluator(project_root=tmp_path)
    evaluator.dataset_obj = SimpleNamespace(load_problem=lambda _: info)
    calls = []

    def fake_local(sim_dir, **kwargs):
        calls.append((sim_dir, kwargs))
        source = (sim_dir / "src" / "test_runner.py").read_text()
        if len(calls) == 1:
            assert "[CVDP_PROGRESS after=" not in source
            assert "await Timer(1, unit='step')" in source
            (sim_dir / "cvdp_local_output.txt").write_text("scored timeout\n")
            return "sim_fail", 1, "CVDP simulation timed out after 45s"
        assert "[CVDP_PROGRESS after=" in source
        assert "await ReadOnly()" in source
        (sim_dir / "cvdp_local_output.txt").write_text(
            "[CVDP_PROGRESS after=10ns] clk=0 start=0 valid=0\n"
        )
        return "sim_fail", 1, "diagnostic timeout"

    monkeypatch.setattr(evaluator, "_run_sim_cvdp_local", fake_local)
    status, mismatches, detail = evaluator._run_sim_cvdp(
        "cvdp_test",
        "module dut(input logic clk, input logic start, output logic valid); "
        "assign valid = start; endmodule",
        "dut",
        [],
        tmp_path,
        direct_top=True,
        problem_info=info,
    )

    assert (status, mismatches) == ("sim_fail", 1)
    assert "not used for scoring" in detail.lower()
    assert "valid=0" in detail
    assert len(calls) == 2
    assert calls[1][1]["max_cases"] == 1
    diagnostic_source = (
        calls[1][0] / "src" / "test_runner.py"
    ).read_text()
    assert "('clk', 'start', 'valid')" in diagnostic_source
    scored_dir = tmp_path / "cvdp_sim" / "cvdp_test"
    scored_manifest = json.loads(
        (scored_dir / "cvdp_harness_adapter.json").read_text()
    )
    diagnostic_manifest = json.loads(
        (scored_dir / "cvdp_timeout_diagnostic.json").read_text()
    )
    assert scored_manifest["harness_profile"] == "race-safe-v1"
    assert scored_manifest["harness_profile_overrides"] == {}
    assert scored_manifest["effective_options"] == {
        "normalize_reset_helpers": False,
        "stabilize_cocotb_edges": True,
        "initialize_cocotb_inputs": False,
        "align_reset_release": True,
        "emit_progress_monitor": False,
        "emit_assertion_trace": False,
    }
    assert scored_manifest["changed_file_count"] == 1
    assert diagnostic_manifest["score_unchanged"] is True
    assert diagnostic_manifest["snapshot_count"] == 1


def test_cvdp_mismatch_diagnostic_replays_public_ports_without_changing_score(
    tmp_path: Path,
    monkeypatch,
):
    info = _cvdp_info(
        "module dut(input logic clk, input logic start, output logic valid); endmodule",
        "import cocotb\n"
        "from cocotb.triggers import RisingEdge\n\n"
        "@cocotb.test()\n"
        "async def test_dut(dut):\n"
        "    dut.start.value = 1\n"
        "    await RisingEdge(dut.clk)\n"
        "    assert dut.valid.value == 1\n",
    )
    info.metadata["native_parameter_sweep_plan"] = {
        "expected_ports": [
            ["input", "logic", "clk"],
            ["input", "logic", "start"],
            ["output", "logic", "valid"],
        ],
        "reset_polarities": {},
    }
    evaluator = Evaluator(project_root=tmp_path)
    evaluator.dataset_obj = SimpleNamespace(load_problem=lambda _: info)
    calls = []

    def fake_local(sim_dir, **kwargs):
        calls.append((sim_dir, kwargs))
        source = (sim_dir / "src" / "test_runner.py").read_text()
        if len(calls) == 1:
            assert "[CVDP_PROGRESS after=" not in source
            assert "__cvdp_record_assertion" not in source
            (sim_dir / "cvdp_local_output.txt").write_text(
                "AssertionError: assert 0 == 1\n"
            )
            return "sim_fail", 1, "CVDP assertion mismatch"
        assert "[CVDP_PROGRESS after=" in source
        assert "assert dut.valid.value == 1" not in source
        assert "__cvdp_record_assertion" in source
        (sim_dir / "cvdp_local_output.txt").write_text(
            "[CVDP_PROGRESS after=10ns] clk=0 start=1 valid=0\n"
            "[CVDP_ASSERTION_TRACE time=20.0ns line=8 got=0 expected=1] "
            "clk=0 start=1 valid=0\n"
        )
        return "sim_fail", 1, "diagnostic mismatch"

    monkeypatch.setattr(evaluator, "_run_sim_cvdp_local", fake_local)
    status, mismatches, detail = evaluator._run_sim_cvdp(
        "cvdp_test",
        "module dut(input logic clk, input logic start, output logic valid); "
        "assign valid = 1'b0; endmodule",
        "dut",
        [],
        tmp_path,
        direct_top=True,
        problem_info=info,
    )

    assert (status, mismatches) == ("sim_fail", 1)
    assert "not used for scoring" in detail.lower()
    assert "start=1 valid=0" in detail
    assert "got=0 expected=1" in detail
    assert len(calls) == 2
    assert calls[1][1]["max_cases"] == 1
    audit = json.loads(
        (tmp_path / "cvdp_sim" / "cvdp_test" / "cvdp_mismatch_diagnostic.json").read_text()
    )
    assert audit["score_unchanged"] is True
    assert audit["public_ports_only"] is True
    assert audit["snapshot_count"] == 1
    assert audit["assertion_trace_count"] == 1


def test_cvdp_assertion_trace_preserves_truthiness_and_evaluates_once():
    source = """
import cocotb

@cocotb.test()
async def test_dut(dut):
    assert observe_actual()
    assert observe_left() == observe_right()

def helper_without_dut():
    assert untouched()
"""

    adapted, transformations, warnings = adapt_python_harness(
        source,
        path="src/test_runner.py",
        normalize_reset_helpers=False,
        stabilize_cocotb_edges=False,
        initialize_cocotb_inputs=False,
        align_reset_release=False,
        emit_progress_monitor=False,
        emit_assertion_trace=True,
        observed_ports={"out"},
    )

    assert warnings == []
    assert len(transformations) == 1
    assert transformations[0]["assertions"] == 2
    assert adapted.count("observe_actual()") == 1
    assert adapted.count("observe_left()") == 1
    assert adapted.count("observe_right()") == 1
    assert "if not __cvdp_actual_1:" in adapted
    assert "if __cvdp_actual_2 != __cvdp_expected_2:" in adapted
    assert "assert untouched()" in adapted
    assert "get_sim_time('ns')" in adapted


def test_cvdp_contract_lists_parameter_sweep_values():
    info = _cvdp_info(
        """
        module FILO_RTL #(
            parameter DATA_WIDTH = 8,
            parameter FILO_DEPTH = 8
        ) (
            input logic clk,
            input logic [DATA_WIDTH-1:0] data_in,
            output logic [DATA_WIDTH-1:0] data_out
        );
        endmodule
        """,
        """
        def test_runner(DATA_WIDTH: int = 8, FILO_DEPTH: int = 8):
            parameter = {"DATA_WIDTH": DATA_WIDTH, "FILO_DEPTH": FILO_DEPTH}
            runner.build(parameters=parameter)

        @pytest.mark.parametrize("DATA_WIDTH", [10, 12])
        @pytest.mark.parametrize("FILO_DEPTH", [12, 16])
        def test_filo(DATA_WIDTH, FILO_DEPTH):
            test_runner(DATA_WIDTH=DATA_WIDTH, FILO_DEPTH=FILO_DEPTH)
        """,
        design_name="FILO_RTL",
    )

    contract = format_benchmark_interface_contract(info)

    assert (
        "Benchmark parameters referenced by harness/validated plan: "
        "DATA_WIDTH, FILO_DEPTH"
    ) in contract
    assert "DATA_WIDTH={8, 10, 12}" in contract
    assert "FILO_DEPTH={8, 12, 16}" in contract
    assert "(DATA_WIDTH=8, FILO_DEPTH=8)" in contract
    assert "(DATA_WIDTH=10, FILO_DEPTH=12)" in contract
    assert "(DATA_WIDTH=10, FILO_DEPTH=16)" in contract
    assert "(DATA_WIDTH=12, FILO_DEPTH=12)" in contract
    assert "(DATA_WIDTH=12, FILO_DEPTH=16)" in contract
    assert "do not merely expose Verilog parameters" in contract


def test_cvdp_contract_uses_validated_plan_instead_of_helper_tuple_values():
    info = _cvdp_info(
        """
        module square_root_seq #(
            parameter WIDTH = 16
        ) (
            input logic [WIDTH-1:0] num,
            output logic [WIDTH/2-1:0] final_root
        ); endmodule
        """,
        """
        pairs = [(4, 3), (8, 4), (16, 5), (32, 6)]

        def runner(WIDTH):
            runner.build(parameters={"WIDTH": WIDTH})

        @pytest.mark.parametrize("WIDTH", [2, 4, 8, 16])
        def test(WIDTH):
            runner(WIDTH)
        """,
        design_name="square_root_seq",
    )
    info.metadata["native_parameter_sweep_plan"] = {
        "schema_version": 1,
        "mode": "native_parameter_sweep",
        "source": "public_cvdp_harness",
        "design_name": "square_root_seq",
        "parameter_names": ["WIDTH"],
        "cases": [
            {"parameters": {"WIDTH": width}}
            for width in (2, 4, 8, 16)
        ],
    }

    contract = format_benchmark_interface_contract(info)

    assert "Benchmark parameters referenced by harness/validated plan: WIDTH" in contract
    assert "Observed parameter sweep values: WIDTH={2, 4, 8, 16}" in contract
    assert "WIDTH={(4, 3)" not in contract


def test_cvdp_contract_discovers_scalar_power_helper_without_configured_plan():
    info = _cvdp_info(
        "module square_root_seq #(parameter WIDTH = 16) (output logic done); endmodule",
        """
        def runner(WIDTH):
            runner.build(parameters={"WIDTH": WIDTH})

        def get_powers_of_two_pairs(iterations):
            value = 2
            pairs = []
            for _ in range(iterations):
                pairs.append(value)
                value *= 2
            return pairs

        pairs = get_powers_of_two_pairs(4)

        @pytest.mark.parametrize("WIDTH", pairs)
        def test(WIDTH):
            runner(WIDTH)
        """,
        design_name="square_root_seq",
    )

    contract = format_benchmark_interface_contract(info)

    assert "Observed parameter sweep values: WIDTH={2, 4, 8, 16}" in contract
    assert "WIDTH={(4, 3)" not in contract


def test_cvdp_contract_evaluates_conditional_reset_helper_call_argument():
    info = _cvdp_info(
        """
        module sync_lifo(
            input logic clock,
            input logic reset,
            input logic write_en,
            output logic empty
        );
        endmodule
        """,
        """
        from cocotb.triggers import Timer

        async def reset_dut(reset_n, duration_ns=25, active: bool=False):
            reset_n.value = 0 if active else 1
            await Timer(duration_ns, unit="ns")
            reset_n.value = 1 if active else 0

        async def test_dut(dut):
            dut.write_en.value = 0
            await reset_dut(dut.reset, active=False)
            int(dut.empty.value)
        """,
        design_name="sync_lifo",
    )

    contract = format_benchmark_interface_contract(info)

    assert "input reset: logic (reset, active-high)" in contract
    assert "reset=reset (active-high)" in contract


def test_cvdp_contract_recognizes_hamming_pair_helper():
    info = _cvdp_info(
        "module hamming_rx #(parameter DATA_WIDTH = 8, parameter PARITY_BIT = 4) (output logic data_out); endmodule",
        """
        def runner(DATA_WIDTH, PARITY_BIT):
            runner.build(parameters={"DATA_WIDTH": DATA_WIDTH, "PARITY_BIT": PARITY_BIT})

        pairs = get_powers_of_two_pairs(3)

        @pytest.mark.parametrize("DATA_WIDTH, PARITY_BIT", pairs)
        def test(DATA_WIDTH, PARITY_BIT):
            runner(DATA_WIDTH, PARITY_BIT)
        """,
        design_name="hamming_rx",
    )

    contract = format_benchmark_interface_contract(info)

    assert "Observed parameter combinations" in contract
    assert "(DATA_WIDTH=4, PARITY_BIT=3)" in contract
    assert "(DATA_WIDTH=16, PARITY_BIT=5)" in contract


def test_cvdp_contract_does_not_treat_prompt_parameters_as_output_ports():
    info = _cvdp_info(
        "(no public reference Verilog available)",
        """
        runner.build(parameters={"N": N})
        dut.bits.value = 0
        int(dut.I.value)
        int(dut.Q.value)
        int(dut.IN_WIDTH.value)
        int(dut.OUT_WIDTH.value)
        """,
        design_name="qam16_mapper_interpolated",
        prompt_text="""
        ## Parameters
        | Parameter | Description | Default |
        |-----------|-------------|---------|
        | `N` | Number of symbols | 4 |
        | `IN_WIDTH` | Input width | 4 |
        | `OUT_WIDTH` | Output width | 3 |

        ### Inputs
        | Name | Width | Description |
        |------|-------|-------------|
        | `bits` | `N*IN_WIDTH` | Packed symbols |

        ### Outputs
        | Name | Width | Description |
        |------|-------|-------------|
        | `I` | `(N + N/2)*OUT_WIDTH` | Real components |
        | `Q` | `(N + N/2)*OUT_WIDTH` | Imaginary components |
        """,
    )

    contract = format_benchmark_interface_contract(info)

    assert (
        "Benchmark parameters referenced by harness/validated plan: "
        "IN_WIDTH, N, OUT_WIDTH"
    ) in contract
    assert "Expected inputs: input bits: logic [N*IN_WIDTH-1:0]" in contract
    assert "Expected outputs: output I:" in contract
    assert "output Q:" in contract
    assert "output IN_WIDTH" not in contract
    assert "output OUT_WIDTH" not in contract


def test_cvdp_contract_preserves_symbolic_width_ending_in_constant():
    info = _cvdp_info(
        "(no public reference Verilog available)",
        """
        runner.build(parameters={"DATA_WIDTH": DATA_WIDTH})
        dut.x.value = 0
        int(dut.result.value)
        int(dut.internal_state.value)
        """,
        design_name="symbolic_accumulator",
        prompt_text="""
        ## Parameters
        | Parameter | Description | Default |
        |-----------|-------------|---------|
        | `DATA_WIDTH` | Input width | 16 |

        ### Inputs
        | Port | Size | Description |
        |------|------|-------------|
        | `x` | `DATA_WIDTH` bits | Signed input |

        ### Outputs
        | Port | Size | Description |
        |------|------|-------------|
        | `result` | `DATA_WIDTH+1` bits | Widened signed sum |
        """,
    )

    contract = format_benchmark_interface_contract(info)

    assert "input x: logic [DATA_WIDTH-1:0]" in contract
    assert "output result: logic [DATA_WIDTH+1-1:0]" in contract
    assert "output result: logic;" not in contract
    assert (
        "output internal_state: width unspecified by the public interface; "
        "infer it from the behavioral specification instead of assuming 1 bit"
    ) in contract


def test_cvdp_contract_parses_bullet_ports_but_not_later_register_table():
    info = _cvdp_info(
        "(no public reference Verilog available)",
        """
        runner.build(parameters={
            "C_S_AXI_ADDR_WIDTH": addr_width,
            "C_S_AXI_DATA_WIDTH": data_width,
        })
        dut.axi_aclk.value = 0
        dut.axi_aresetn.value = 0
        dut.axi_awaddr.value = 0
        int(dut.axi_awready.value)
        int(dut.axi_bresp.value)
        int(dut.irq.value)
        """,
        design_name="precision_counter_axi",
        prompt_text="""
        ### Interface
        #### Parameters
        - `C_S_AXI_DATA_WIDTH`: AXI data width.
        - `C_S_AXI_ADDR_WIDTH`: AXI address width.

        #### Inputs
        - **Clock and Reset:**
          - `axi_aclk`: Clock.
          - `axi_aresetn`: Active-low reset.
        - **Write Address:**
          - `[C_S_AXI_ADDR_WIDTH-1:0] axi_awaddr`: Address.

        #### Outputs
        - `axi_awready`: Address ready.
        - `[1:0] axi_bresp`: Write response.
        - `irq`: Interrupt.

        ### Register Map
        | Register | Offset | Description | Bit Width |
        |----------|--------|-------------|-----------|
        | `slv_reg_ctl` | 0x00 | Control register | 32 |
        """,
    )

    contract = format_benchmark_interface_contract(info)

    assert "input axi_aclk: logic (clock)" in contract
    assert "input axi_aresetn: logic (reset, active-low)" in contract
    assert "input axi_awaddr: logic [C_S_AXI_ADDR_WIDTH-1:0]" in contract
    assert "output axi_awready: logic" in contract
    assert "output axi_bresp: logic [1:0] (2 bits)" in contract
    assert "output irq: logic" in contract
    assert "output Register" not in contract
    assert "slv_reg_ctl" not in contract


def test_cvdp_contract_prefers_direction_table_and_ignores_prose_ref_code():
    info = _cvdp_info(
        """
        # Documentation
        The module also accepts an input representing data and outputs a result.
        """,
        """
        dut.clk.value = 0
        dut.rst.value = 1
        dut.s_axis_tdata_1.value = 0
        int(dut.s_axis_tready_1.value)
        int(dut.m_axis_tdata.value)
        """,
        design_name="axis_joiner",
        prompt_text="""
        #### Output Port
        - The protocol includes `tdata`, `tvalid`, and `tready`.

        Port Name | Direction | Width | Description
        -- | -- | -- | --
        clk | Input | 1 bit | Clock
        rst | Input | 1 bit | Active-high reset
        s_axis_tdata_1 | Input | 8-bit | Input data
        s_axis_tready_1 | Output | 1 bit | Input ready
        m_axis_tdata | Output | 8-bit | Output data

        ### Register Map
        | Register | Offset | Description |
        |----------|--------|-------------|
        | `state` | 0x00 | Current state |
        """,
    )

    contract = format_benchmark_interface_contract(info)

    assert "input clk: logic (clock)" in contract
    assert "input rst: logic (reset, active-high)" in contract
    assert "input s_axis_tdata_1: logic [7:0] (8 bits)" in contract
    assert "output s_axis_tready_1: logic" in contract
    assert "output m_axis_tdata: logic [7:0] (8 bits)" in contract
    assert "output tdata" not in contract
    assert "logic [Input-1:0]" not in contract
    assert "input representing" not in contract
    assert "output result" not in contract


def test_cvdp_contract_parses_width_after_backticked_bullet_name():
    info = _cvdp_info(
        "(no public reference Verilog available)",
        """
        dut.clk.value = 0
        dut.rst.value = 0
        int(dut.ms_hr.value)
        """,
        design_name="bcd_counter",
        prompt_text="""
        ### Inputs:
        - `clk`: Clock input.
        - `rst`: Active-low reset.

        ### Outputs:
        - `ms_hr` (4-bit) - most significant hour digit.
        """,
    )

    contract = format_benchmark_interface_contract(info)

    assert "output ms_hr: logic [3:0] (4 bits)" in contract


def test_cvdp_contract_does_not_promote_internal_reference_signal_to_port():
    info = _cvdp_info(
        """
        module fifo_policy #(
            parameter NWAYS = 4,
            parameter NINDEXES = 32
        ) (
            input logic clock,
            input logic reset,
            output logic [$clog2(NWAYS)-1:0] way_replace
        );
            logic [$clog2(NWAYS)-1:0] fifo_array [NINDEXES-1:0];
        endmodule
        """,
        """
        dut.clock.value = 0
        dut.reset.value = 1
        int(dut.way_replace.value)
        int(dut.fifo_array[0].value)
        """,
        design_name="fifo_policy",
    )

    contract = format_benchmark_interface_contract(info)

    assert "output way_replace:" in contract
    assert "output fifo_array:" not in contract


def test_cvdp_wrapper_maps_visible_concat_fields_by_name_and_width():
    wrapper = generate_cvdp_wrapper(
        design_name="dut",
        sparkle_mod_name="sparkle_inner",
        sparkle_ports=[
            ("input", "logic", "_gen_a"),
            ("output", "logic [4:0]", "_gen_out"),
        ],
        ref_code="""
        module dut(
            input logic a,
            output logic [2:0] sum,
            output logic carry,
            output logic parity
        );
        endmodule
        """,
        harness_files={
            "src/test.py": """
            dut.a.value = 1
            assert int(dut.sum.value) == 0
            assert int(dut.carry.value) == 0
            assert int(dut.parity.value) == 0
            """,
        },
        sv_code="""
        module sparkle_inner(input logic _gen_a, output logic [4:0] _gen_out);
            logic [2:0] _gen_sum;
            logic _gen_carry;
            logic _gen_parity;
            assign _gen_out = {_gen_sum, _gen_carry, _gen_parity};
        endmodule
        """,
    )

    assert wrapper is not None
    assert "assign {sum, carry, parity} = _gen_out_wire;" in wrapper


def test_cvdp_wrapper_normalizes_scalar_data_inputs_to_two_state_bool(
    monkeypatch,
):
    monkeypatch.delenv("CVDP_TWO_STATE_INPUT_NORMALIZATION", raising=False)
    wrapper = generate_cvdp_wrapper(
        design_name="square_root_seq",
        sparkle_mod_name="square_root_seq_sparkle_inner",
        sparkle_ports=[
            ("input", "logic [WIDTH-1:0]", "_gen_num"),
            ("input", "logic", "_gen_start"),
            ("input", "logic", "clk"),
            ("input", "logic", "rst"),
            ("output", "logic [WIDTH/2:0]", "out"),
        ],
        ref_code="""
        module square_root_seq #(parameter WIDTH = 2) (
            input logic clk,
            input logic [WIDTH-1:0] num,
            input logic rst,
            input logic start,
            output logic done,
            output logic [WIDTH/2-1:0] final_root
        ); endmodule
        """,
        harness_files={
            "src/test.py": """
            cocotb.start_soon(Clock(dut.clk, 10, unit='ns').start())
            dut.rst.value = 1
            dut.start.value = 1
            dut.num.value = 3
            int(dut.done.value)
            int(dut.final_root.value)
            """,
        },
        sv_code="""
        module square_root_seq_sparkle_inner #(
            parameter WIDTH = 2
        ) (
            input logic [WIDTH-1:0] _gen_num,
            input logic _gen_start,
            input logic clk,
            input logic rst,
            output logic [WIDTH/2:0] out
        );
            logic _gen_done;
            logic [WIDTH/2-1:0] _gen_final_root;
            assign out = {_gen_done, _gen_final_root};
        endmodule
        """,
        expected_ports_override=[
            ("input", "logic", "clk"),
            ("input", "logic [WIDTH-1:0]", "num"),
            ("input", "logic", "rst"),
            ("input", "logic", "start"),
            ("output", "logic", "done"),
            ("output", "logic [WIDTH/2-1:0]", "final_root"),
        ],
        strict_mapping=True,
        parameter_cases=[{"WIDTH": 2}, {"WIDTH": 4}],
    )

    assert wrapper is not None
    assert "._gen_start((start === 1'b1))" in wrapper
    assert "._gen_num(num)" in wrapper
    assert ".clk(clk)" in wrapper
    assert ".rst(rst)" in wrapper
    assert "0/X/Z=false): start" in wrapper


def test_cvdp_wrapper_can_disable_two_state_input_normalization(monkeypatch):
    monkeypatch.setenv("CVDP_TWO_STATE_INPUT_NORMALIZATION", "0")
    wrapper = generate_cvdp_wrapper(
        design_name="dut",
        sparkle_mod_name="sparkle_inner",
        sparkle_ports=[
            ("input", "logic", "_gen_start"),
            ("output", "logic", "out"),
        ],
        ref_code="module dut(input logic start, output logic out); endmodule",
        harness_files={
            "src/test.py": "dut.start.value = 1; int(dut.out.value)",
        },
        sv_code=(
            "module sparkle_inner(input logic _gen_start, output logic out); "
            "assign out = _gen_start; endmodule"
        ),
    )

    assert wrapper is not None
    assert "._gen_start(start)" in wrapper
    assert "=== 1'b1" not in wrapper


def test_cvdp_wrapper_never_normalizes_clock_by_name_without_harness_clock_hint(
    monkeypatch,
):
    monkeypatch.delenv("CVDP_TWO_STATE_INPUT_NORMALIZATION", raising=False)
    wrapper = generate_cvdp_wrapper(
        design_name="dut",
        sparkle_mod_name="sparkle_inner",
        sparkle_ports=[
            ("input", "logic", "clock"),
            ("input", "logic", "_gen_start"),
            ("output", "logic", "out"),
        ],
        ref_code=(
            "module dut(input logic clock, input logic start, output logic out); "
            "endmodule"
        ),
        harness_files={"src/test.py": "dut.start.value = 1; int(dut.out.value)"},
        sv_code=(
            "module sparkle_inner(input logic clock, input logic _gen_start, "
            "output logic out); assign out = _gen_start; endmodule"
        ),
    )

    assert wrapper is not None
    assert ".clock(clock)" in wrapper
    assert ".clock((clock === 1'b1))" not in wrapper
    assert "._gen_start((start === 1'b1))" in wrapper


def test_cvdp_wrapper_does_not_blindly_slice_without_concat_fields():
    wrapper = generate_cvdp_wrapper(
        design_name="dut",
        sparkle_mod_name="sparkle_inner",
        sparkle_ports=[("output", "logic [3:0]", "_gen_out")],
        ref_code="""
        module dut(
            output logic [1:0] left,
            output logic [1:0] right
        );
        endmodule
        """,
        harness_files={
            "src/test.py": """
            assert int(dut.left.value) == 0
            assert int(dut.right.value) == 0
            """,
        },
        sv_code="module sparkle_inner(output logic [3:0] _gen_out); endmodule",
    )

    assert wrapper is not None
    assert "assign left = _gen_out_wire[3:2];" not in wrapper
    assert "assign right = _gen_out_wire[1:0];" not in wrapper
    assert "CVDP adapter fallback: output left was not mapped" in wrapper
    assert "assign left = '0;" in wrapper


def test_cvdp_wrapper_notes_fixed_width_core_for_parameterized_port():
    wrapper = generate_cvdp_wrapper(
        design_name="dut",
        sparkle_mod_name="sparkle_inner",
        sparkle_ports=[
            ("input", "logic [7:0]", "_gen_data_in"),
            ("output", "logic [7:0]", "_gen_data_out"),
        ],
        ref_code="""
        module dut #(
            parameter WIDTH = 8
        ) (
            input logic [WIDTH-1:0] data_in,
            output logic [WIDTH-1:0] data_out
        );
        endmodule
        """,
        harness_files={
            "src/test.py": """
            def test_runner(WIDTH):
                runner.build(parameters={"WIDTH": WIDTH})
            dut.data_in.value = 0
            assert int(dut.data_out.value) == 0
            """,
        },
        sv_code="module sparkle_inner(input logic [7:0] _gen_data_in, output logic [7:0] _gen_data_out); endmodule",
    )

    assert wrapper is not None
    assert "Sparkle core port _gen_data_in has fixed type logic [7:0]" in wrapper
    assert "benchmark port data_in is parameterized as logic [WIDTH-1:0]" in wrapper


def test_cvdp_wrapper_inherits_native_core_parameter_without_reference_rtl(tmp_path):
    core_sv = """
        module signed_comparator_sparkle_inner #(
            parameter integer WIDTH = 5
        ) (
            input logic [WIDTH-1:0] _gen_lhs,
            input logic [WIDTH-1:0] _gen_rhs,
            output logic out
        );
            assign out = $signed(_gen_lhs) < $signed(_gen_rhs);
        endmodule
        """
    wrapper = generate_cvdp_wrapper(
        design_name="signed_comparator",
        sparkle_mod_name="signed_comparator_sparkle_inner",
        sparkle_ports=[
            ("input", "logic [WIDTH-1:0]", "_gen_lhs"),
            ("input", "logic [WIDTH-1:0]", "_gen_rhs"),
            ("output", "logic", "out"),
        ],
        ref_code="(no public reference Verilog available)",
        harness_files={
            "src/test.py": """
            dut.lhs.value = 1
            dut.rhs.value = 2
            assert int(dut.out.value) == 1
            """,
        },
        sv_code=core_sv,
    )

    assert wrapper is not None
    assert "module signed_comparator\n #(\n    parameter integer WIDTH = 5\n)" in wrapper
    assert ".WIDTH(WIDTH)" in wrapper
    assert "input logic [WIDTH-1:0] lhs" in wrapper
    assert "input logic [WIDTH-1:0] rhs" in wrapper

    iverilog = shutil.which("iverilog")
    if iverilog is None:
        pytest.skip("iverilog is unavailable")
    design = tmp_path / "native_wrapper.sv"
    design.write_text(f"{core_sv}\n{wrapper}\n", encoding="utf-8")
    compiled = subprocess.run(
        [iverilog, "-g2012", "-s", "signed_comparator", str(design)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr


def test_cvdp_wrapper_maps_symbolic_camelcase_bundle_without_reference_rtl():
    core_sv = """
    module mapper_sparkle_inner #(
        parameter integer N = 4,
        parameter integer IN_WIDTH = 4,
        parameter integer OUT_WIDTH = 3
    ) (
        input logic [(N * IN_WIDTH)-1:0] _gen_bits,
        output logic [2 * ((N + N / 2) * OUT_WIDTH)-1:0] out
    );
        logic [((N + N / 2) * OUT_WIDTH)-1:0] _gen_iOut;
        logic [((N + N / 2) * OUT_WIDTH)-1:0] _gen_qOut;
        assign _gen_iOut = '0;
        assign _gen_qOut = '0;
        assign out = {_gen_iOut, _gen_qOut};
    endmodule
    """
    wrapper = generate_cvdp_wrapper(
        design_name="mapper",
        sparkle_mod_name="mapper_sparkle_inner",
        sparkle_ports=[
            ("input", "logic [(N * IN_WIDTH)-1:0]", "_gen_bits"),
            (
                "output",
                "logic [2 * ((N + N / 2) * OUT_WIDTH)-1:0]",
                "out",
            ),
        ],
        ref_code="(no public reference Verilog available)",
        harness_files={
            "src/test.py": """
            N = int(dut.N.value)
            IN_WIDTH = int(dut.IN_WIDTH.value)
            OUT_WIDTH = int(dut.OUT_WIDTH.value)
            dut.bits.value = 0
            int(dut.I.value)
            int(dut.Q.value)
            """,
        },
        sv_code=core_sv,
    )

    assert wrapper is not None
    assert "parameter integer N = 4" in wrapper
    assert "parameter integer IN_WIDTH = 4" in wrapper
    assert "parameter integer OUT_WIDTH = 3" in wrapper
    assert "output logic [((N + N / 2) * OUT_WIDTH)-1:0] I" in wrapper
    assert "output logic [((N + N / 2) * OUT_WIDTH)-1:0] Q" in wrapper
    assert "assign {I, Q} = out_wire;" in wrapper
    assert "CVDP adapter fallback: output I" not in wrapper
    assert "CVDP adapter fallback: output Q" not in wrapper


def test_cvdp_wrapper_maps_symbolic_output_subset_from_larger_bundle():
    core_sv = """
    module demapper_sparkle_inner #(
        parameter integer N = 4,
        parameter integer OUT_WIDTH = 4
    ) (
        input logic [(N * OUT_WIDTH)-1:0] _gen_samples,
        output logic [((N * OUT_WIDTH) + 2)-1:0] out
    );
        logic [(N * OUT_WIDTH)-1:0] _gen_bits;
        logic _gen_error_flag;
        logic _gen_internal_threshold;
        assign _gen_bits = _gen_samples;
        assign _gen_error_flag = 1'b0;
        assign _gen_internal_threshold = 1'b1;
        assign out = {_gen_bits, {_gen_error_flag, _gen_internal_threshold}};
    endmodule
    """
    wrapper = generate_cvdp_wrapper(
        design_name="demapper",
        sparkle_mod_name="demapper_sparkle_inner",
        sparkle_ports=[
            ("input", "logic [(N * OUT_WIDTH)-1:0]", "_gen_samples"),
            ("output", "logic [((N * OUT_WIDTH) + 2)-1:0]", "out"),
        ],
        ref_code="""
        module demapper #(
            parameter integer N = 4,
            parameter integer OUT_WIDTH = 4
        ) (
            input logic [(N * OUT_WIDTH)-1:0] samples,
            output logic [(N * OUT_WIDTH)-1:0] bits,
            output logic error_flag
        );
        endmodule
        """,
        harness_files={
            "src/test.py": """
            dut.samples.value = 0
            int(dut.bits.value)
            int(dut.error_flag.value)
            """,
        },
        sv_code=core_sv,
    )

    assert wrapper is not None
    assert "assign {_cvdp_bundle_field_0, _cvdp_bundle_field_1, _cvdp_bundle_field_2} = out_wire;" in wrapper
    assert "assign bits = _cvdp_bundle_field_0;" in wrapper
    assert "assign error_flag = _cvdp_bundle_field_1;" in wrapper
    assert "CVDP adapter fallback: output bits" not in wrapper
    assert "CVDP adapter fallback: output error_flag" not in wrapper


def test_cvdp_wrapper_bridges_observed_internal_memory_without_making_it_a_port():
    wrapper = generate_cvdp_wrapper(
        design_name="fifo_policy",
        sparkle_mod_name="fifo_policy_sparkle_inner",
        sparkle_ports=[
            ("input", "logic [4:0]", "_gen_index"),
            ("input", "logic", "clk"),
            ("input", "logic", "rst"),
            ("output", "logic [1:0]", "out"),
        ],
        ref_code="""
        module fifo_policy #(
            parameter NWAYS = 4,
            parameter NINDEXES = 32
        ) (
            input logic clock,
            input logic reset,
            input logic [$clog2(NINDEXES)-1:0] index,
            output logic [$clog2(NWAYS)-1:0] way_replace
        );
            logic [$clog2(NWAYS)-1:0] fifo_array [NINDEXES-1:0];
        endmodule
        """,
        harness_files={
            "src/test.py": """
            dut.clock.value = 0
            int(dut.way_replace.value)
            int(dut.fifo_array[0].value)
            """,
        },
        sv_code="""
        module fifo_policy_sparkle_inner(
            input logic [4:0] _gen_index,
            input logic clk,
            input logic rst,
            output logic [1:0] out
        );
            logic [1:0] _gen_current_way [0:31];
            assign out = _gen_current_way[_gen_index];
        endmodule
        """,
    )

    assert wrapper is not None
    port_block = wrapper.split(");", 1)[0]
    assert "fifo_array" not in port_block
    assert "logic [$clog2(NWAYS)-1:0] fifo_array [NINDEXES-1:0];" in wrapper
    assert "assign way_replace = out_wire;" in wrapper
    assert (
        "for (genvar _cvdp_bridge_fifo_array_i = 0; "
        "_cvdp_bridge_fifo_array_i <= NINDEXES-1;"
    ) in wrapper
    assert (
        "assign fifo_array[_cvdp_bridge_fifo_array_i] = "
        "sparkle_dut._gen_current_way[_cvdp_bridge_fifo_array_i];"
    ) in wrapper


def test_cvdp_wrapper_selects_unique_output_backed_memory_when_names_are_ambiguous():
    wrapper = generate_cvdp_wrapper(
        design_name="fifo_policy",
        sparkle_mod_name="fifo_policy_sparkle_inner",
        sparkle_ports=[
            ("input", "logic [4:0]", "_gen_index"),
            ("input", "logic", "clk"),
            ("input", "logic", "rst"),
            ("output", "logic [1:0]", "out"),
        ],
        ref_code="""
        module fifo_policy #(
            parameter NWAYS = 4,
            parameter NINDEXES = 32
        ) (
            input logic clock,
            input logic reset,
            input logic [$clog2(NINDEXES)-1:0] index,
            output logic [$clog2(NWAYS)-1:0] way_replace
        );
            logic [$clog2(NWAYS)-1:0] fifo_array [NINDEXES-1:0];
        endmodule
        """,
        harness_files={
            "src/test.py": """
            int(dut.way_replace.value)
            int(dut.fifo_array[0].value)
            """,
        },
        sv_code="""
        module fifo_policy_sparkle_inner(
            input logic [4:0] _gen_index,
            input logic clk,
            input logic rst,
            output logic [1:0] out
        );
            logic [1:0] _gen_shadow [0:31];
            logic [1:0] _gen_policy_state [0:31];
            logic [1:0] _gen_shadow_rdata;
            logic [1:0] _gen_policy_rdata;
            logic [1:0] _gen_unused_next;
            assign _gen_shadow_rdata = _gen_shadow[_gen_index];
            assign _gen_unused_next = _gen_shadow_rdata + 2'd1;
            assign _gen_policy_rdata = _gen_policy_state[_gen_index];
            assign out = _gen_policy_rdata;
        endmodule
        """,
    )

    assert wrapper is not None
    assert "could not be mapped uniquely" not in wrapper
    assert (
        "assign fifo_array[_cvdp_bridge_fifo_array_i] = "
        "sparkle_dut._gen_policy_state[_cvdp_bridge_fifo_array_i];"
    ) in wrapper
    assert "sparkle_dut._gen_shadow[_cvdp_bridge_fifo_array_i]" not in wrapper


def test_cvdp_direct_top_is_evaluated_without_sparkle_wrapper(tmp_path, monkeypatch):
    code = """
    module dut #(
        parameter WIDTH = 4
    ) (
        input logic clk,
        output logic [WIDTH-1:0] data_out
    );
        assign data_out = {WIDTH{clk}};
    endmodule
    """
    info = _cvdp_info(code, "", design_name="dut")

    class DatasetStub:
        def load_problem(self, prob_id: str) -> ProblemInfo:
            assert prob_id == "cvdp_test"
            return info

    evaluator = Evaluator(project_root=tmp_path, dataset="cvdp", dataset_obj=DatasetStub())
    captured = {}

    def fake_local_sim(sim_dir: Path):
        captured["source"] = (sim_dir / "rtl" / "dut.sv").read_text()
        return "sim_pass", 0, "ok"

    monkeypatch.setenv("CVDP_SIM_MODE", "local")
    monkeypatch.setattr(evaluator, "_run_sim_cvdp_local", fake_local_sim)
    module_name, ports = parse_module_ports(code, module_name="dut")

    status, mismatches, detail = evaluator._run_sim_cvdp(
        "cvdp_test",
        code,
        module_name,
        ports,
        tmp_path,
        direct_top=True,
    )

    assert (status, mismatches, detail) == ("sim_pass", 0, "ok")
    assert captured["source"] == code
    assert "dut_sparkle_inner" not in captured["source"]


def test_evaluator_preserves_hierarchical_verilog_and_selects_cvdp_top(
    tmp_path,
    monkeypatch,
):
    prob_id = "hierarchical_design"
    generated = tmp_path / "Generated"
    generated.mkdir()
    (generated / f"{prob_id}.lean").write_text(
        "#synthesizeParameterizedVerilogDesign hierarchical_top [W := 4]\n",
        encoding="utf-8",
    )
    child = """
    // Generated by Sparkle HDL
    module lane_child(input logic x, output logic y);
        assign y = x;
    endmodule
    """
    helper = """
    // Generated by Sparkle HDL
    module lane_helper(input logic x, output logic y);
        lane_child child(.x(x), .y(y));
    endmodule
    """
    top = """
    // Generated by Sparkle HDL
    module hierarchical_top(input logic data_in, output logic data_out);
        lane_helper helper(.x(data_in), .y(data_out));
    endmodule
    """
    repl_result = SimpleNamespace(
        passed=True,
        complete=True,
        errors=[],
        error_text="",
        verilog=child,
        verilog_modules=[child, helper, top],
    )
    lean_repl = SimpleNamespace(check_file=lambda _path: repl_result)
    info = _cvdp_info(
        "(no public reference Verilog available)",
        "dut.data_in.value = 1\nassert int(dut.data_out.value) == 1\n",
        design_name="hierarchical_top",
    )
    evaluator = Evaluator(
        project_root=tmp_path,
        lean_repl=lean_repl,
        dataset="cvdp",
    )
    monkeypatch.setattr(evaluator, "_run_lint", lambda _path: True)
    captured = {}

    def fake_sim(prob, sv_code, module_name, ports, run_dir):
        captured.update({
            "prob_id": prob,
            "sv_code": sv_code,
            "module_name": module_name,
            "ports": ports,
            "run_dir": run_dir,
        })
        return "sim_pass", 0, "ok"

    monkeypatch.setattr(evaluator, "_run_sim", fake_sim)

    result = evaluator.evaluate(prob_id, tmp_path / "run", problem_info=info)

    assert result["sim_status"] == "sim_pass"
    assert captured["module_name"] == "hierarchical_top"
    assert [name for name, _, _ in evaluator_module._module_records(
        captured["sv_code"]
    )] == ["lane_child", "lane_helper", "hierarchical_top"]
    saved_sv = (tmp_path / "run" / "sv" / f"{prob_id}.sv").read_text()
    assert "module lane_child" in saved_sv
    assert "module lane_helper" in saved_sv
    assert "module hierarchical_top" in saved_sv
