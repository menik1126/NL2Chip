from __future__ import annotations

import ast
import dataclasses
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "agent"))

import cvdp_devtests as devtests  # noqa: E402
from evaluator import (  # noqa: E402
    Evaluator,
    _cvdp_parameter_override_analysis,
    _cvdp_structured_harness_clocks,
    parse_module_ports,
)


PUBLIC_CVDP12 = (
    PROJECT_ROOT
    / "experiments"
    / "cvdp12_dataset"
    / "cvdp_v1.1.0_nonagentic_code_generation_no_commercial.jsonl"
)


def _installed_input(problem_id: str) -> devtests.PublicCVDPInput:
    if not PUBLIC_CVDP12.exists():
        pytest.skip("public CVDP12 fixture is not installed")
    return devtests.load_cvdp_public_input(PUBLIC_CVDP12, problem_id)


def _installed_suite(problem_id: str) -> devtests.GeneratedDevSuite:
    item = _installed_input(problem_id)
    return devtests.build_cvdp_public_dev_suite(
        item.prob_id,
        item.prompt_text,
        item.input_context_files,
    )


def _runner_matrix(suite: devtests.GeneratedDevSuite) -> list[dict[str, int]]:
    source = suite.harness_files["src/test_runner.py"]
    match = re.search(r"PARAMETER_MATRIX = json\.loads\((['\"])(.*?)\1\)", source)
    assert match
    # The generated repr is a JSON string without single quotes.
    return json.loads(match.group(2))



def _runtime_python() -> str:
    if shutil.which("iverilog") is None:
        pytest.skip("Icarus Verilog is not installed")
    candidates = (PROJECT_ROOT / ".venv" / "bin" / "python", Path(sys.executable))
    for candidate in candidates:
        if not candidate.exists():
            continue
        probe = subprocess.run(
            [str(candidate), "-c", "import cocotb, pytest"],
            capture_output=True,
            text=True,
            check=False,
        )
        if probe.returncode == 0:
            return str(candidate)
    pytest.skip("no Python environment with cocotb and pytest is installed")


def _run_generated_suite(
    tmp_path: Path,
    suite: devtests.GeneratedDevSuite,
    design_source: str,
    *,
    timeout: int = 180,
    case_index: int | None = None,
) -> subprocess.CompletedProcess[str]:
    assert suite.supported and suite.public_info is not None
    (tmp_path / "test_generated.py").write_text(
        suite.harness_files["src/test_generated.py"]
    )
    runner_path = tmp_path / "test_runner.py"
    runner_path.write_text(suite.harness_files["src/test_runner.py"])

    local_sources: list[str] = []
    compiled_context = suite.public_info.metadata["input_context_files"]
    for index, source in enumerate(suite.verilog_sources):
        assert source.startswith("/code/")
        relative = source[len("/code/"):]
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if index == 0:
            destination.write_text(design_source.strip() + "\n")
        else:
            destination.write_text(compiled_context[relative])
        local_sources.append(str(destination))

    env = os.environ.copy()
    env["SIM"] = "icarus"
    env["VERILOG_SOURCES"] = " ".join(local_sources)
    test_target = str(runner_path)
    if case_index is not None:
        test_target += f"::test_generated_public_dev_{case_index:03d}"
    return subprocess.run(
        [_runtime_python(), "-m", "pytest", "-q", test_target],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )


def _assert_runtime_pass(completed: subprocess.CompletedProcess[str]) -> None:
    assert completed.returncode == 0, completed.stdout + completed.stderr


def _assert_runtime_mutant_rejected(
    completed: subprocess.CompletedProcess[str], label: str
) -> None:
    output = completed.stdout + completed.stderr
    assert completed.returncode != 0, output
    assert "CVDP_PUBLIC_DEV_MISMATCH" in output, output
    assert label in output, output


def test_catalog_is_exactly_the_audited_cvdp12_slice():
    assert len(devtests.SUPPORTED_CVDP12_PROBLEMS) == 12
    assert len(set(devtests.SUPPORTED_CVDP12_PROBLEMS)) == 12
    assert devtests.PUBLIC_SPEC_SOURCE == "public_spec"
    assert devtests.CVDP_PUBLIC_DEV_SOURCE == devtests.PUBLIC_SPEC_SOURCE


@pytest.mark.parametrize("problem_id", devtests.SUPPORTED_CVDP12_PROBLEMS)
def test_real_public_inputs_build_validate_and_python_compile(problem_id: str):
    public_input = _installed_input(problem_id)
    suite = devtests.build_cvdp_public_dev_suite(
        public_input.prob_id,
        public_input.prompt_text,
        public_input.input_context_files,
    )

    assert suite.supported, suite.reason
    assert suite.reason is None
    assert suite.source == devtests.PUBLIC_SPEC_SOURCE
    assert suite.public_info is not None
    assert suite.public_info.ref_code == ""
    assert suite.public_info.ref_path is None
    assert suite.verilog_sources[0] == f"/code/src/{suite.public_info.design_name}.sv"
    assert suite.benchmark_ports
    assert _runner_matrix(suite)
    suite.validate()

    generated_test = suite.harness_files["src/test_generated.py"]
    generated_runner = suite.harness_files["src/test_runner.py"]
    ast.parse(generated_test)
    ast.parse(generated_runner)
    assert generated_test.count("@cocotb.test()") == 1
    assert "CVDP_PUBLIC_DEV_MISMATCH" in generated_test
    assert "_expect(" in generated_test
    # This is the source-level non-vacuity gate: an all-zero/no-handshake
    # implementation reaches a nonzero assertion or an explicit timeout/range
    # failure in every template.
    assert any(
        marker in generated_test
        for marker in (
            "bit_count()", "_swizzle(", "values[-1]", "_gf_mac(",
            "parking_counts", "corrected_data_out", "math.isqrt(",
            "data_out_feedthrough", "dice_range", "valid_exact_latency",
            "_hamming_encode(", "axi_write_handshake",
        )
    )


_EXPECTED_PARAMETER_NAMES = {
    "cvdp_copilot_word_reducer_0008": {"BIT_WIDTH"},
    "cvdp_copilot_nbit_swizzling_0001": {"DATA_WIDTH"},
    "cvdp_copilot_sync_lifo_0001": {"ADDR_WIDTH", "DATA_WIDTH"},
    "cvdp_copilot_gf_multiplier_0021": {"WIDTH"},
    "cvdp_copilot_car_parking_management_0001": {"TOTAL_SPACES"},
    "cvdp_copilot_hamming_code_tx_and_rx_0011": {"DATA_WIDTH", "PARITY_BIT"},
    "cvdp_copilot_square_root_0003": {"WIDTH"},
    "cvdp_copilot_filo_0005": {"DATA_WIDTH", "FILO_DEPTH"},
    "cvdp_copilot_digital_dice_roller_0004": {"DICE_MAX", "NUM_DICE"},
    "cvdp_copilot_restoring_division_0001": {"WIDTH"},
    "cvdp_copilot_hamming_code_tx_and_rx_0009": {"DATA_WIDTH", "PARITY_BIT"},
    "cvdp_copilot_axil_precision_counter_0001": {
        "C_S_AXI_ADDR_WIDTH", "C_S_AXI_DATA_WIDTH"
    },
}


@pytest.mark.parametrize("problem_id", devtests.SUPPORTED_CVDP12_PROBLEMS)
def test_generated_runners_have_only_definite_literal_parameter_overrides(problem_id: str):
    suite = _installed_suite(problem_id)
    analysis = _cvdp_parameter_override_analysis(suite.harness_files)

    assert analysis.has_definite_overrides is True
    assert analysis.may_have_overrides is True
    assert analysis.unresolved is False
    assert analysis.unresolved_reasons == ()
    assert analysis.parameter_names == frozenset(_EXPECTED_PARAMETER_NAMES[problem_id])
    assert analysis.build_call_count == len(_runner_matrix(suite))


_EXPECTED_TEMPORAL_CLOCKS = {
    "cvdp_copilot_sync_lifo_0001": "clock",
    "cvdp_copilot_car_parking_management_0001": "clk",
    "cvdp_copilot_square_root_0003": "clk",
    "cvdp_copilot_filo_0005": "clk",
    "cvdp_copilot_digital_dice_roller_0004": "clk",
    "cvdp_copilot_restoring_division_0001": "clk",
    "cvdp_copilot_axil_precision_counter_0001": "axi_aclk",
}


@pytest.mark.parametrize(
    ("problem_id", "clock_name"), _EXPECTED_TEMPORAL_CLOCKS.items()
)
def test_temporal_harnesses_have_direct_literal_clock_evidence(
    problem_id: str, clock_name: str
):
    suite = _installed_suite(problem_id)
    py_files = {
        path: str(content)
        for path, content in suite.harness_files.items()
        if path.endswith(".py")
    }

    clocks, unresolved = _cvdp_structured_harness_clocks(py_files)

    assert clocks == {clock_name}
    assert unresolved == set()
    generated_test = py_files["src/test_generated.py"]
    assert "_start_clock" not in generated_test
    assert f'Clock(dut.{clock_name}, 10, unit="ns").start()' in generated_test


def _sparkle_abi_probe(suite: devtests.GeneratedDevSuite) -> str:
    assert suite.public_info is not None
    defaults = _runner_matrix(suite)[0]
    parameter_declarations = [
        f"    parameter integer {name} = {value}"
        for name, value in sorted(defaults.items())
    ]
    parameter_block = ""
    if parameter_declarations:
        parameter_block = " #(\n" + ",\n".join(parameter_declarations) + "\n)"

    # Model source-level Signal binders exactly as Sparkle's generated ABI.
    core_ports = [
        (direction, width, f"_gen_{name}")
        for direction, width, name in suite.benchmark_ports
    ]
    port_names = {name for _, _, name in core_ports}
    assert len(port_names) == len(core_ports)

    # Keep raw clk/rst distinct from source-level generated clock/reset ports.
    for raw_name in ("clk", "rst"):
        if raw_name not in port_names:
            core_ports.append(("input", "logic", raw_name))
            port_names.add(raw_name)

    port_block = ",\n".join(
        f"    {direction} {width} {name}"
        for direction, width, name in core_ports
    )
    return (
        f"module {suite.public_info.design_name}{parameter_block} (\n"
        f"{port_block}\n"
        ");\n"
        "endmodule\n"
    )


@pytest.mark.parametrize(
    "problem_id",
    (
        "cvdp_copilot_car_parking_management_0001",
        "cvdp_copilot_square_root_0003",
        "cvdp_copilot_digital_dice_roller_0004",
    ),
)
def test_generated_temporal_suite_passes_sparkle_clock_adapter_preflight(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    problem_id: str,
):
    suite = _installed_suite(problem_id)
    assert suite.supported and suite.public_info is not None
    source = _sparkle_abi_probe(suite)
    design_name = suite.public_info.design_name
    module_name, ports = parse_module_ports(source, module_name=design_name)
    assert module_name == design_name

    evaluator = Evaluator(project_root=tmp_path, dataset="cvdp")
    captured: dict[str, object] = {}

    def fake_local(
        sim_dir: Path, *, public_dev_generated: bool = False
    ) -> tuple[str, int, str]:
        relative_source = suite.verilog_sources[0].removeprefix("/code/")
        captured["source"] = (sim_dir / relative_source).read_text()
        captured["public_dev_generated"] = public_dev_generated
        return "sim_pass", 0, "adapter preflight"

    monkeypatch.setattr(evaluator, "_run_sim_cvdp_local", fake_local)
    result = evaluator._run_sim_cvdp(
        suite.prob_id,
        source,
        module_name,
        ports,
        tmp_path / "run",
        benchmark_ports=list(suite.benchmark_ports),
        direct_top=False,
        problem_info=suite.public_info,
    )

    assert result == ("sim_pass", 0, "adapter preflight")
    assert captured["public_dev_generated"] is True
    wrapped_source = str(captured["source"])
    assert f"module {design_name}_sparkle_inner" in wrapped_source
    assert "assign _cvdp_input_clk_wire = clk;" in wrapped_source
    assert ".clk(_cvdp_input_clk_wire)" in wrapped_source


def test_generated_word_reducer_runner_executes_with_icarus(tmp_path):
    suite = _installed_suite("cvdp_copilot_word_reducer_0008")
    completed = _run_generated_suite(
        tmp_path,
        suite,
        """
module Bit_Difference_Counter #(
    parameter integer BIT_WIDTH = 8
) (
    input  logic [BIT_WIDTH-1:0] input_A,
    input  logic [BIT_WIDTH-1:0] input_B,
    output logic [$clog2(BIT_WIDTH + 1)-1:0] bit_difference_count
);
    integer i;
    always_comb begin
        bit_difference_count = '0;
        for (i = 0; i < BIT_WIDTH; i = i + 1)
            bit_difference_count = bit_difference_count + (input_A[i] ^ input_B[i]);
    end
endmodule
""",
    )
    _assert_runtime_pass(completed)
    assert "4 passed" in completed.stdout


@pytest.mark.parametrize("problem_id", devtests.SUPPORTED_CVDP12_PROBLEMS)
def test_suite_generation_is_deterministic_and_seeded(problem_id: str):
    item = _installed_input(problem_id)
    first = devtests.build_cvdp_public_dev_suite(
        problem_id, item.prompt_text, item.input_context_files
    )
    second = devtests.build_cvdp_public_dev_suite(
        problem_id, item.prompt_text, item.input_context_files
    )
    changed_seed = devtests.build_cvdp_public_dev_suite(
        problem_id,
        item.prompt_text,
        item.input_context_files,
        seed=devtests.DEFAULT_CVDP_DEV_SEED + 1,
    )

    assert first.sha256 == second.sha256
    assert first.harness_files == second.harness_files
    assert changed_seed.supported
    assert changed_seed.sha256 != first.sha256
    changed_seed.validate()


def test_public_loader_does_not_decode_or_return_hidden_canaries(tmp_path, monkeypatch):
    canary = "HIDDEN_HARNESS_CANARY_DO_NOT_DECODE"
    row = (
        '{"id":"target","input":{"prompt":"public","context":{"rtl/a.sv":"module a; endmodule"}},'
        '"harness":{"files":{"secret":"' + canary + '"}},'
        '"output":{"context":{"secret":"' + canary + '"}}}'
    )
    path = tmp_path / "fixture.jsonl"
    path.write_text(row + "\n")

    original = json.JSONDecoder.raw_decode

    def guarded_decode(self, text, index=0):
        hidden_prefixes = ('{"files":{"secret"', '{"context":{"secret"')
        if any(text.startswith(prefix, index) for prefix in hidden_prefixes):
            raise AssertionError("hidden value was decoded")
        return original(self, text, index)

    monkeypatch.setattr(json.JSONDecoder, "raw_decode", guarded_decode)
    result = devtests.load_cvdp_public_input(path, "target")

    assert result.prompt_text == "public"
    assert dict(result.input_context_files) == {"rtl/a.sv": "module a; endmodule"}
    assert canary not in repr(result)
    with pytest.raises(TypeError):
        result.input_context_files["rtl/b.sv"] = "module b; endmodule"  # type: ignore[index]


def test_prompt_and_context_single_character_mutations_fail_closed():
    problem_id = "cvdp_copilot_word_reducer_0008"
    item = _installed_input(problem_id)

    prompt_mutant = devtests.build_cvdp_public_dev_suite(
        problem_id, item.prompt_text + " ", item.input_context_files
    )
    assert not prompt_mutant.supported
    assert "fingerprint" in (prompt_mutant.reason or "")
    prompt_mutant.validate()

    context = dict(item.input_context_files)
    path = next(iter(context))
    context[path] += " "
    context_mutant = devtests.build_cvdp_public_dev_suite(
        problem_id, item.prompt_text, context
    )
    assert not context_mutant.supported
    assert "fingerprint" in (context_mutant.reason or "")
    context_mutant.validate()


def test_generated_suite_mappings_are_deeply_immutable():
    suite = _installed_suite("cvdp_copilot_word_reducer_0008")
    assert suite.public_info is not None

    with pytest.raises(TypeError, match="immutable"):
        suite.harness_files["src/test_generated.py"] = "forged"
    with pytest.raises(TypeError, match="immutable"):
        suite.public_info.metadata["cvdp_row"] = {"hidden": True}
    with pytest.raises(TypeError, match="immutable"):
        suite.public_info.metadata["harness_files"]["src/test_generated.py"] = "forged"
    with pytest.raises(TypeError, match="immutable"):
        suite.public_info.metadata["input_context_files"]["rtl/extra.sv"] = "forged"


def _forge_self_consistent_suite(
    suite: devtests.GeneratedDevSuite,
    *,
    harness_files=None,
    verilog_sources=None,
    benchmark_ports=None,
):
    assert suite.public_info is not None
    info = suite.public_info
    metadata = dict(info.metadata)
    raw_context = dict(metadata["public_input_context_files"])
    compiled_context = dict(metadata["input_context_files"])
    harness = dict(harness_files or suite.harness_files)
    sources = tuple(verilog_sources or suite.verilog_sources)
    ports = tuple(benchmark_ports or suite.benchmark_ports)
    digest = devtests._supported_digest(
        prob_id=suite.prob_id,
        design_name=info.design_name,
        prompt_text=metadata["public_prompt_text"],
        seed=suite.seed,
        context_hashes={
            path: hashlib.sha256(content.encode("utf-8")).hexdigest()
            for path, content in raw_context.items()
        },
        compilation_context=compiled_context,
        harness_files=harness,
        verilog_sources=sources,
        benchmark_ports=ports,
    )
    metadata["public_dev_sha256"] = digest
    metadata["harness_files"] = harness
    metadata["verilog_sources"] = sources
    metadata["benchmark_ports"] = ports
    forged_info = dataclasses.replace(info, metadata=metadata)
    return dataclasses.replace(
        suite,
        public_info=forged_info,
        harness_files=harness,
        verilog_sources=sources,
        benchmark_ports=ports,
        sha256=digest,
    )


def test_validate_rejects_self_consistent_digest_forgery_against_trusted_template():
    suite = _installed_suite("cvdp_copilot_word_reducer_0008")

    harness = dict(suite.harness_files)
    harness["src/test_generated.py"] += "\n# forged oracle\n"
    with pytest.raises(ValueError, match="trusted template"):
        _forge_self_consistent_suite(suite, harness_files=harness).validate()

    sources = (*suite.verilog_sources, "/code/rtl/forged_helper.sv")
    with pytest.raises(ValueError, match="trusted template"):
        _forge_self_consistent_suite(suite, verilog_sources=sources).validate()

    ports = (*suite.benchmark_ports[:-1], ("output", "logic", "forged_count"))
    with pytest.raises(ValueError, match="trusted template"):
        _forge_self_consistent_suite(suite, benchmark_ports=ports).validate()

    assert suite.public_info is not None
    design_forgery = dataclasses.replace(
        suite, public_info=dataclasses.replace(suite.public_info, design_name="forged_top")
    )
    with pytest.raises(ValueError, match="identity/reference"):
        design_forgery.validate()

    source_forgery = dataclasses.replace(suite, source="hidden_holdout")
    with pytest.raises(ValueError, match="source mismatch"):
        source_forgery.validate()


def test_target_context_stays_public_and_only_target_module_is_stripped():
    problem_id = "cvdp_copilot_gf_multiplier_0021"
    item = _installed_input(problem_id)
    suite = _installed_suite(problem_id)
    assert suite.public_info is not None

    for path, content in item.input_context_files.items():
        assert path in suite.public_info.metadata["public_input_context_files"]
        assert content in suite.public_info.prompt_text
    compiled = suite.public_info.metadata["input_context_files"]["rtl/gf_mac.sv"]
    assert not re.search(r"\bmodule\s+gf_mac\b", compiled)
    assert re.search(r"\bmodule\s+gf_multiplier\b", compiled)
    assert "/code/rtl/gf_mac.sv" in suite.verilog_sources
    suite.validate()


def test_target_stripper_preserves_helpers_before_and_after_target():
    source = """
module helper_before; endmodule
module target(input logic a); endmodule : target
module helper_after; endmodule
"""
    stripped = devtests._strip_target_module(source, "target")
    assert "module helper_before" in stripped
    assert "module helper_after" in stripped
    assert not re.search(r"\bmodule\s+target\b", stripped)


def test_derived_port_widths_are_self_contained_without_reference_rtl():
    word = _installed_suite("cvdp_copilot_word_reducer_0008")
    rx = _installed_suite("cvdp_copilot_hamming_code_tx_and_rx_0011")
    tx = _installed_suite("cvdp_copilot_hamming_code_tx_and_rx_0009")
    dice = _installed_suite("cvdp_copilot_digital_dice_roller_0004")

    assert "$clog2(BIT_WIDTH + 1)" in word.benchmark_ports[-1][1]
    assert "DATA_WIDTH + PARITY_BIT + 1" in rx.benchmark_ports[0][1]
    assert "DATA_WIDTH + PARITY_BIT + 1" in tx.benchmark_ports[-1][1]
    assert "$clog2(DICE_MAX) + 1" in dice.benchmark_ports[-1][1]
    assert all(suite.public_info and not suite.public_info.ref_code for suite in (word, rx, tx, dice))


def test_public_default_parameters_and_non_overflow_parking_matrix_are_present():
    word_matrix = _runner_matrix(_installed_suite("cvdp_copilot_word_reducer_0008"))
    gf_matrix = _runner_matrix(_installed_suite("cvdp_copilot_gf_multiplier_0021"))
    filo_matrix = _runner_matrix(_installed_suite("cvdp_copilot_filo_0005"))
    parking_matrix = _runner_matrix(_installed_suite("cvdp_copilot_car_parking_management_0001"))

    assert {"BIT_WIDTH": 3} in word_matrix
    assert {"WIDTH": 32} in gf_matrix
    assert {"DATA_WIDTH": 8, "FILO_DEPTH": 16} in filo_matrix
    assert {"TOTAL_SPACES": 4} not in parking_matrix
    assert all(
        total < (1 << (total - 1).bit_length())
        for total in (row["TOTAL_SPACES"] for row in parking_matrix)
    )


@pytest.mark.parametrize(
    "context",
    [
        {"../hidden.sv": "module x; endmodule"},
        {"/absolute.sv": "module x; endmodule"},
        {"src/test_runner.py": "hidden"},
    ],
)
def test_unsafe_or_non_hdl_public_context_fails_closed(context):
    problem_id = "cvdp_copilot_word_reducer_0008"
    item = _installed_input(problem_id)
    suite = devtests.build_cvdp_public_dev_suite(problem_id, item.prompt_text, context)
    assert not suite.supported
    suite.validate()


def test_unknown_problem_and_invalid_seed_fail_closed_without_executable_files():
    unknown = devtests.build_cvdp_public_dev_suite("unknown", "public")
    invalid_seed = devtests.build_cvdp_public_dev_suite(
        "cvdp_copilot_word_reducer_0008", "public", seed=-1
    )
    for suite in (unknown, invalid_seed):
        assert not suite.supported
        assert suite.public_info is None
        assert not suite.harness_files
        assert not suite.verilog_sources
        suite.validate()


_GF_HELPER_CONSUMER = r"""
module gf_mac #(
    parameter integer WIDTH = 32
) (
    input wire [WIDTH-1:0] a,
    input wire [WIDTH-1:0] b,
    output reg [7:0] result,
    output wire error_flag,
    output wire valid_result
);
    assign error_flag = (WIDTH % 8) != 0;
    assign valid_result = (WIDTH % 8) == 0;
    wire [7:0] partial_results [0:(WIDTH/8)-1];
    genvar j;
    generate
        for (j = 0; j < WIDTH/8; j = j + 1) begin : segments
            gf_multiplier multiply_segment(
                .A(a[(j+1)*8-1:j*8]),
                .B(b[(j+1)*8-1:j*8]),
                .result(partial_results[j])
            );
        end
        if ((WIDTH % 8) == 0) begin : valid_width
            integer i;
            always @(*) begin
                result = 8'b0;
                for (i = 0; i < WIDTH/8; i = i + 1)
                    result = result ^ partial_results[i];
            end
        end else begin : invalid_width
            always_comb result = 8'b0;
        end
    endgenerate
endmodule
"""


_SQRT_CONFORMING = r"""
module square_root_seq #(
    parameter integer WIDTH = 16
) (
    input wire [WIDTH-1:0] num,
    input wire clk,
    input wire rst,
    input wire start,
    output reg [WIDTH/2-1:0] final_root,
    output reg done
);
    reg busy;
    reg [WIDTH:0] remainder;
    reg [WIDTH:0] odd;
    reg [WIDTH/2:0] working_root;
    always @(posedge clk or posedge rst) begin
        if (rst) begin
            busy <= 1'b0;
            remainder <= '0;
            odd <= '0;
            working_root <= '0;
            final_root <= '0;
            done <= 1'b0;
        end else begin
            done <= 1'b0;
            if (!busy && start) begin
                busy <= 1'b1;
                remainder <= num;
                odd <= 1;
                working_root <= 0;
            end else if (busy) begin
                if (remainder >= odd) begin
                    remainder <= remainder - odd;
                    odd <= odd + 2;
                    working_root <= working_root + 1;
                end else begin
                    final_root <= working_root[WIDTH/2-1:0];
                    done <= 1'b1;
                    busy <= 1'b0;
                end
            end
        end
    end
endmodule
"""


_DICE_CONFORMING = r"""
module digital_dice_roller #(
    parameter integer DICE_MAX = 6,
    parameter integer NUM_DICE = 2,
    parameter integer BIT_WIDTH = $clog2(DICE_MAX) + 1
) (
    input wire clk,
    input wire reset,
    input wire button,
    output reg [(NUM_DICE * BIT_WIDTH)-1:0] dice_values
);
    reg rolling;
    reg [15:0] seeds [0:NUM_DICE-1];
    reg [BIT_WIDTH-1:0] counters [0:NUM_DICE-1];
    integer i;
    function automatic [15:0] next_lfsr(input [15:0] current);
        next_lfsr = {current[14:0],
                     current[15] ^ current[4] ^ current[3] ^ current[2]};
    endfunction
    always @(posedge clk or negedge reset) begin
        if (!reset) begin
            rolling <= 1'b0;
            dice_values <= '0;
            for (i = 0; i < NUM_DICE; i = i + 1) begin
                seeds[i] <= i + 1;
                counters[i] <= 1;
            end
        end else if (button) begin
            rolling <= 1'b1;
            for (i = 0; i < NUM_DICE; i = i + 1) begin
                seeds[i] <= next_lfsr(seeds[i]);
                counters[i] <= (next_lfsr(seeds[i]) % DICE_MAX) + 1;
            end
        end else begin
            if (rolling) begin
                for (i = 0; i < NUM_DICE; i = i + 1)
                    dice_values[((NUM_DICE-i)*BIT_WIDTH)-1 -: BIT_WIDTH]
                        <= counters[i];
            end
            rolling <= 1'b0;
        end
    end
endmodule
"""


_DIVISION_CONFORMING = r"""
module restoring_division #(
    parameter integer WIDTH = 6
) (
    input wire clk,
    input wire rst,
    input wire start,
    input wire [WIDTH-1:0] dividend,
    input wire [WIDTH-1:0] divisor,
    output reg [WIDTH-1:0] quotient,
    output reg [WIDTH-1:0] remainder,
    output reg valid
);
    localparam integer LATENCY = ((WIDTH & (WIDTH - 1)) == 0) ? WIDTH : WIDTH + 1;
    integer cycle_count;
    reg busy;
    reg [WIDTH-1:0] pending_quotient;
    reg [WIDTH-1:0] pending_remainder;
    always @(posedge clk or negedge rst) begin
        if (!rst) begin
            cycle_count <= 0;
            busy <= 1'b0;
            quotient <= '0;
            remainder <= '0;
            pending_quotient <= '0;
            pending_remainder <= '0;
            valid <= 1'b0;
        end else begin
            valid <= 1'b0;
            if (!busy && start) begin
                pending_quotient <= dividend / divisor;
                pending_remainder <= dividend % divisor;
                cycle_count <= 1;
                busy <= 1'b1;
            end else if (busy) begin
                if (cycle_count == LATENCY - 1) begin
                    quotient <= pending_quotient;
                    remainder <= pending_remainder;
                    valid <= 1'b1;
                    busy <= 1'b0;
                end else begin
                    cycle_count <= cycle_count + 1;
                end
            end
        end
    end
endmodule
"""


_FILO_CONFORMING = r"""
module FILO_RTL #(
    parameter integer DATA_WIDTH = 8,
    parameter integer FILO_DEPTH = 16
) (
    input wire clk,
    input wire reset,
    input wire push,
    input wire pop,
    input wire [DATA_WIDTH-1:0] data_in,
    output reg [DATA_WIDTH-1:0] data_out,
    output wire full,
    output wire empty
);
    reg [DATA_WIDTH-1:0] memory [0:FILO_DEPTH-1];
    integer count;
    assign full = (count == FILO_DEPTH);
    assign empty = (count == 0);
    always @(posedge clk or posedge reset) begin
        if (reset) begin
            count <= 0;
            data_out <= '0;
        end else if (push && pop && count == 0) begin
            data_out <= data_in;
        end else if (push && !pop && count < FILO_DEPTH) begin
            memory[count] <= data_in;
            count <= count + 1;
        end else if (pop && !push && count > 0) begin
            data_out <= memory[count - 1];
            count <= count - 1;
        end
    end
endmodule
"""


@pytest.mark.parametrize(
    ("problem_id", "source"),
    [
        ("cvdp_copilot_square_root_0003", _SQRT_CONFORMING),
        ("cvdp_copilot_filo_0005", _FILO_CONFORMING),
        ("cvdp_copilot_digital_dice_roller_0004", _DICE_CONFORMING),
        ("cvdp_copilot_restoring_division_0001", _DIVISION_CONFORMING),
    ],
)
def test_strengthened_oracles_accept_public_conforming_designs(
    tmp_path, problem_id, source
):
    completed = _run_generated_suite(tmp_path, _installed_suite(problem_id), source)
    _assert_runtime_pass(completed)


def test_gf_target_stripping_preserves_helper_for_real_compilation(tmp_path):
    completed = _run_generated_suite(
        tmp_path,
        _installed_suite("cvdp_copilot_gf_multiplier_0021"),
        _GF_HELPER_CONSUMER,
    )
    _assert_runtime_pass(completed)


def test_dice_constant_one_no_state_mutant_is_rejected_at_runtime(tmp_path):
    mutant = r"""
module digital_dice_roller #(
    parameter integer DICE_MAX = 6,
    parameter integer NUM_DICE = 2,
    parameter integer BIT_WIDTH = $clog2(DICE_MAX) + 1
) (
    input wire clk, input wire reset, input wire button,
    output wire [(NUM_DICE * BIT_WIDTH)-1:0] dice_values
);
    localparam [BIT_WIDTH-1:0] ONE = {{(BIT_WIDTH-1){1'b0}}, 1'b1};
    assign dice_values = {NUM_DICE{ONE}};
endmodule
"""
    completed = _run_generated_suite(
        tmp_path,
        _installed_suite("cvdp_copilot_digital_dice_roller_0004"),
        mutant,
        case_index=0,
    )
    _assert_runtime_mutant_rejected(completed, "dice_independent_variation")


def test_dice_synchronous_reset_mutant_is_rejected_at_runtime(tmp_path):
    mutant = _DICE_CONFORMING.replace(
        "always @(posedge clk or negedge reset)",
        "always @(posedge clk)",
    )
    completed = _run_generated_suite(
        tmp_path,
        _installed_suite("cvdp_copilot_digital_dice_roller_0004"),
        mutant,
        case_index=0,
    )
    _assert_runtime_mutant_rejected(
        completed, "dice_async_reset_reproducible"
    )


def test_sqrt_immediate_combinational_done_mutant_is_rejected_at_runtime(tmp_path):
    mutant = r"""
module square_root_seq #(parameter integer WIDTH = 16) (
    input wire [WIDTH-1:0] num, input wire clk, input wire rst, input wire start,
    output wire [WIDTH/2-1:0] final_root, output wire done
);
    assign final_root = '0;
    assign done = start;
endmodule
"""
    completed = _run_generated_suite(
        tmp_path,
        _installed_suite("cvdp_copilot_square_root_0003"),
        mutant,
        case_index=0,
    )
    _assert_runtime_mutant_rejected(completed, "done_not_early")


def test_division_immediate_operators_mutant_is_rejected_at_runtime(tmp_path):
    mutant = r"""
module restoring_division #(parameter integer WIDTH = 6) (
    input wire clk, input wire rst, input wire start,
    input wire [WIDTH-1:0] dividend, input wire [WIDTH-1:0] divisor,
    output wire [WIDTH-1:0] quotient, output wire [WIDTH-1:0] remainder,
    output wire valid
);
    assign quotient = !rst ? '0 : dividend / divisor;
    assign remainder = !rst ? '0 : dividend % divisor;
    assign valid = rst && start;
endmodule
"""
    completed = _run_generated_suite(
        tmp_path,
        _installed_suite("cvdp_copilot_restoring_division_0001"),
        mutant,
        case_index=0,
    )
    _assert_runtime_mutant_rejected(completed, "valid_not_early")


@pytest.mark.parametrize(
    ("mutant", "label"),
    [
        (
            _FILO_CONFORMING.replace(
                "integer count;", "integer count = 0;"
            ).replace(
                "always @(posedge clk or posedge reset)",
                "always @(posedge clk)",
            ),
            "empty_midrun_async_reset",
        ),
        (
            _FILO_CONFORMING.replace(
                "push && !pop && count < FILO_DEPTH", "push && !pop"
            ),
            "full_after_overflow",
        ),
        (
            _FILO_CONFORMING.replace(
                "pop && !push && count > 0", "pop && !push"
            ),
            "empty_after_underflow",
        ),
    ],
)
def test_filo_reset_overflow_and_underflow_mutants_are_rejected_at_runtime(
    tmp_path, mutant, label
):
    completed = _run_generated_suite(
        tmp_path,
        _installed_suite("cvdp_copilot_filo_0005"),
        mutant,
        case_index=0,
    )
    _assert_runtime_mutant_rejected(completed, label)


_AXI_EDGE_READY_CONFORMING = r"""
module precision_counter_axi #(
    parameter integer C_S_AXI_DATA_WIDTH = 32,
    parameter integer C_S_AXI_ADDR_WIDTH = 8
) (
    input wire axi_aclk,
    input wire axi_aresetn,
    input wire [C_S_AXI_ADDR_WIDTH-1:0] axi_awaddr,
    input wire axi_awvalid,
    input wire [C_S_AXI_DATA_WIDTH-1:0] axi_wdata,
    input wire [(C_S_AXI_DATA_WIDTH/8)-1:0] axi_wstrb,
    input wire axi_wvalid,
    input wire axi_bready,
    input wire [C_S_AXI_ADDR_WIDTH-1:0] axi_araddr,
    input wire axi_arvalid,
    input wire axi_rready,
    output wire axi_awready,
    output wire axi_wready,
    output reg [1:0] axi_bresp,
    output reg axi_bvalid,
    output wire axi_arready,
    output reg [C_S_AXI_DATA_WIDTH-1:0] axi_rdata,
    output reg [1:0] axi_rresp,
    output reg axi_rvalid,
    output reg axi_ap_done,
    output reg irq
);
    reg [31:0] slv_reg_ctl;
    reg [31:0] slv_reg_t;
    reg [31:0] slv_reg_v;
    reg [31:0] slv_reg_irq_mask;
    reg [31:0] slv_reg_irq_thresh;

    // READY is high only until the transfer edge.  Registering BVALID/RVALID
    // on that edge makes READY drop before a post-edge-only driver can sample it.
    assign axi_awready = axi_aresetn && !axi_bvalid;
    assign axi_wready = axi_aresetn && !axi_bvalid;
    assign axi_arready = axi_aresetn && !axi_rvalid;

    always @(posedge axi_aclk or negedge axi_aresetn) begin
        if (!axi_aresetn) begin
            slv_reg_ctl <= 0;
            slv_reg_t <= 0;
            slv_reg_v <= 0;
            slv_reg_irq_mask <= 0;
            slv_reg_irq_thresh <= 0;
            axi_bresp <= 0;
            axi_bvalid <= 0;
            axi_rdata <= 0;
            axi_rresp <= 0;
            axi_rvalid <= 0;
            axi_ap_done <= 0;
            irq <= 0;
        end else begin
            if (axi_bvalid && axi_bready)
                axi_bvalid <= 0;
            if (!axi_bvalid && axi_awvalid && axi_wvalid) begin
                axi_bvalid <= 1;
                axi_bresp <= 0;
                case (axi_awaddr)
                    8'h00: begin slv_reg_ctl <= axi_wdata; slv_reg_t <= 0; end
                    8'h10: slv_reg_t <= axi_wdata;
                    8'h20: slv_reg_v <= axi_wdata;
                    8'h24: slv_reg_irq_mask <= axi_wdata;
                    8'h28: slv_reg_irq_thresh <= axi_wdata;
                    default: axi_bresp <= 2'b10;
                endcase
            end

            if (axi_rvalid && axi_rready)
                axi_rvalid <= 0;
            if (!axi_rvalid && axi_arvalid) begin
                axi_rvalid <= 1;
                axi_rresp <= 0;
                case (axi_araddr)
                    8'h00: axi_rdata <= slv_reg_ctl;
                    8'h0c: axi_rdata <= axi_ap_done;
                    8'h10: axi_rdata <= slv_reg_t;
                    8'h20: axi_rdata <= slv_reg_v;
                    8'h24: axi_rdata <= slv_reg_irq_mask;
                    8'h28: axi_rdata <= slv_reg_irq_thresh;
                    default: begin axi_rdata <= 0; axi_rresp <= 2'b10; end
                endcase
            end

            if (slv_reg_ctl[0]) begin
                if (slv_reg_v != 0) begin
                    irq <= slv_reg_irq_mask[0]
                        && (slv_reg_v == slv_reg_irq_thresh);
                    if (slv_reg_v == 1) begin
                        slv_reg_v <= 0;
                        axi_ap_done <= 1;
                    end else begin
                        slv_reg_v <= slv_reg_v - 1;
                    end
                end else begin
                    irq <= 0;
                    slv_reg_t <= slv_reg_t + 1;
                end
            end else begin
                irq <= 0;
            end
        end
    end
endmodule
"""


def test_axi_driver_samples_ready_on_the_transfer_edge_at_runtime(tmp_path):
    completed = _run_generated_suite(
        tmp_path,
        _installed_suite("cvdp_copilot_axil_precision_counter_0001"),
        _AXI_EDGE_READY_CONFORMING,
    )
    _assert_runtime_pass(completed)
