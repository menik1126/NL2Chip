from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "agent"))

import evaluator as evaluator_module  # noqa: E402
from evaluator import (  # noqa: E402
    CVDP_ADAPTER_ERROR,
    Evaluator,
)
from search import (  # noqa: E402
    build_sim_feedback,
    classify_failure_record,
    extract_sim_diagnostics,
    read_simulator_output,
)


PROB_ID = "cvdp_copilot_word_reducer_0008"
ICARUS_LOG = """\
/tmp/Data_Reduction.sv:152: error: Unable to bind parameter `TOTAL_INPUT_WIDTH' in `Bit_Difference_Counter'
/tmp/Data_Reduction.sv:152: error: Dimensions must be constant.
/tmp/Data_Reduction.sv:153: error: Unable to bind parameter `DATA_WIDTH' in `Bit_Difference_Counter'
4 error(s) during elaboration.
"""


def _write_sim_artifacts(run_dir: Path, pytest_output: str) -> None:
    sim_root = run_dir / "cvdp_sim" / PROB_ID
    rundir = sim_root / "rundir"
    rundir.mkdir(parents=True)
    (rundir / "sim.log").write_text(ICARUS_LOG)
    (sim_root / "cvdp_local_output.txt").write_text(pytest_output)


def test_read_simulator_output_puts_icarus_build_log_before_pytest(tmp_path):
    _write_sim_artifacts(
        tmp_path,
        "FAILED test_runner.py::test_width - subprocess.CalledProcessError",
    )

    output = read_simulator_output(PROB_ID, tmp_path)

    assert output.index("[Icarus build log]") < output.index(
        "[CVDP pytest/cocotb output]"
    )
    assert "Unable to bind parameter `TOTAL_INPUT_WIDTH'" in output
    assert "subprocess.CalledProcessError" in output


def test_extract_sim_diagnostics_prioritizes_icarus_over_traceback_noise():
    traceback_noise = "\n".join(
        f"trace line {index}: except TimeoutExpired as exc: failed"
        for index in range(120)
    )

    diagnostics = extract_sim_diagnostics(
        traceback_noise + "\n" + ICARUS_LOG,
        max_chars=1200,
    )

    assert "Unable to bind parameter `TOTAL_INPUT_WIDTH'" in diagnostics
    assert "Dimensions must be constant" in diagnostics
    assert "during elaboration" in diagnostics
    assert "trace line" not in diagnostics


def test_build_sim_feedback_includes_redirected_icarus_diagnostics(tmp_path):
    _write_sim_artifacts(
        tmp_path,
        "\n".join(
            f"trace line {index}: except TimeoutExpired as exc: failed"
            for index in range(120)
        ),
    )
    result = {
        "compile_pass": True,
        "sv_extracted": True,
        "lint_pass": True,
        "sim_status": "sim_fail",
        "sim_mismatches": -1,
        "detail": "CVDP local harness failed: subprocess.CalledProcessError",
    }

    feedback = build_sim_feedback(
        prob_id=PROB_ID,
        result=result,
        iteration=0,
        history=[],
        run_dir=tmp_path,
    )

    assert "### Cleaned Simulator Diagnostics" in feedback
    assert "Unable to bind parameter `TOTAL_INPUT_WIDTH'" in feedback
    assert "Dimensions must be constant" in feedback
    assert "during elaboration" in feedback


class _FakePytestProcess:
    def __init__(self, output: str, returncode: int = 1):
        self.output = output
        self.returncode = returncode
        self.pid = 12345

    def communicate(self, timeout=None):
        return self.output, ""


def _run_local_harness_failure(
    tmp_path: Path,
    monkeypatch,
    *,
    pytest_output: str,
    sim_log: str = "",
):
    sim_dir = tmp_path / "sim"
    src = sim_dir / "src"
    rundir = sim_dir / "rundir"
    src.mkdir(parents=True)
    rundir.mkdir(parents=True)
    (src / ".env").write_text("SIM=icarus\n")
    (src / "test_runner.py").write_text("# fake harness\n")
    if sim_log:
        (rundir / "sim.log").write_text(sim_log)

    process = _FakePytestProcess(pytest_output)
    monkeypatch.setattr(
        evaluator_module.subprocess,
        "Popen",
        lambda *_args, **_kwargs: process,
    )
    evaluator = Evaluator(project_root=tmp_path, dataset="cvdp")
    return evaluator._run_sim_cvdp_local(sim_dir)


def test_local_cvdp_iverilog_elaboration_error_is_not_functional_failure(
    tmp_path, monkeypatch
):
    status, mismatches, detail = _run_local_harness_failure(
        tmp_path,
        monkeypatch,
        pytest_output=(
            "FAILED test_runner.py::test_width - subprocess.CalledProcessError\n"
            "E subprocess.CalledProcessError: Command ['iverilog', '-g2012'] "
            "returned non-zero exit status 4.\n"
        ),
        sim_log=ICARUS_LOG,
    )

    assert status == "sim_error"
    assert mismatches == -1
    assert "iverilog compile failed during build/elaboration" in detail
    assert "TOTAL_INPUT_WIDTH" in detail
    assert "Dimensions must be constant" in detail
    assert classify_failure_record({
        "compile_pass": True,
        "sim_status": status,
        "detail": detail,
    })["failure_category"] == "rtl_compile_or_interface_error"


def test_local_cvdp_pytest_assertion_remains_functional_failure(
    tmp_path, monkeypatch
):
    status, _, detail = _run_local_harness_failure(
        tmp_path,
        monkeypatch,
        pytest_output=(
            "AssertionError: Expected 3, Got 2\n"
            "** test_counter.test_count   FAIL   10.00ns **\n"
        ),
    )

    assert status == "sim_fail"
    assert classify_failure_record({
        "compile_pass": True,
        "sim_status": status,
        "detail": detail,
    })["failure_category"] == "functional_mismatch"


def test_local_cvdp_cocotb_system_exit_remains_functional_failure(
    tmp_path, monkeypatch
):
    status, _, _ = _run_local_harness_failure(
        tmp_path,
        monkeypatch,
        pytest_output=(
            "runner.test(hdl_toplevel=toplevel, test_module=module)\n"
            "E SystemExit: 1\n"
            "ERROR Icarus:runner.py:572 ERROR: Failed 1 of 1 tests.\n"
        ),
    )

    assert status == "sim_fail"


def test_local_cvdp_actual_subprocess_timeout_is_simulation_error(
    tmp_path, monkeypatch
):
    status, _, detail = _run_local_harness_failure(
        tmp_path,
        monkeypatch,
        pytest_output=(
            "E subprocess.TimeoutExpired: Command ['vvp'] timed out after 30 seconds\n"
            "FAILED test_runner.py::test_wait\n"
        ),
    )

    assert status == "sim_error"
    assert "simulation timeout" in detail
    assert classify_failure_record({
        "compile_pass": True,
        "sim_status": status,
        "detail": detail,
    })["failure_category"] == "simulation_timeout"


def test_local_cvdp_adapter_contract_marker_is_not_functional_failure(
    tmp_path, monkeypatch
):
    status, _, detail = _run_local_harness_failure(
        tmp_path,
        monkeypatch,
        pytest_output=(
            CVDP_ADAPTER_ERROR
            + ": derived parameter COUNT_WIDTH is unavailable\n"
            + "FAILED test_runner.py::test_width\n"
        ),
    )

    assert status == "sim_error"
    assert CVDP_ADAPTER_ERROR in detail
    assert classify_failure_record({
        "compile_pass": True,
        "sim_status": status,
        "detail": detail,
    }) == {
        "failure_stage": "simulation",
        "failure_category": "adapter_contract_error",
        "failure_family": "interface",
    }
