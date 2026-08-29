from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "agent"))

from dataset import ProblemInfo  # noqa: E402
from evaluator import _cvdp_classify_local_failure, generate_cvdp_wrapper  # noqa: E402
from search import format_benchmark_interface_contract  # noqa: E402


def _cvdp_info(ref_code: str, harness_py: str, design_name: str = "dut") -> ProblemInfo:
    return ProblemInfo(
        prob_id="cvdp_test",
        design_name=design_name,
        prompt_text="",
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

    assert "Benchmark parameters referenced by harness: DATA_WIDTH, FILO_DEPTH" in contract
    assert "DATA_WIDTH={10, 12}" in contract
    assert "FILO_DEPTH={12, 16}" in contract
    assert "do not merely expose Verilog parameters" in contract


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
    assert "assign sum = _gen_out_wire[4:2];" in wrapper
    assert "assign carry = _gen_out_wire[1];" in wrapper
    assert "assign parity = _gen_out_wire[0];" in wrapper


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


def test_cvdp_wrapper_forwards_parameters_supported_by_core():
    wrapper = generate_cvdp_wrapper(
        design_name="dut",
        sparkle_mod_name="sparkle_inner",
        sparkle_ports=[
            ("input", "logic [WIDTH-1:0]", "_gen_data_in"),
            ("output", "logic [WIDTH-1:0]", "data_out"),
        ],
        ref_code="""
        module dut #(
            parameter WIDTH = 8,
            parameter DEPTH = 4
        ) (
            input logic [WIDTH-1:0] data_in,
            output logic [WIDTH-1:0] data_out
        );
        endmodule
        """,
        harness_files={
            "src/test.py": """
            runner.build(parameters={"WIDTH": WIDTH, "DEPTH": DEPTH})
            dut.data_in.value = 0
            assert int(dut.data_out.value) == 0
            """,
        },
        sv_code="""
        module sparkle_inner #(
            parameter integer WIDTH = 8,
            parameter integer DEPTH = 4
        ) (
            input logic [WIDTH-1:0] _gen_data_in,
            output logic [WIDTH-1:0] data_out
        );
        endmodule
        """,
    )

    assert wrapper is not None
    assert "sparkle_inner #(\n" in wrapper
    assert ".DEPTH(DEPTH)" in wrapper
    assert ".WIDTH(WIDTH)" in wrapper


def test_cvdp_wrapper_inherits_missing_parameter_default_from_core():
    wrapper = generate_cvdp_wrapper(
        design_name="fifo_async",
        sparkle_mod_name="sparkle_fifo",
        sparkle_ports=[
            ("input", "logic [DATA_WIDTH-1:0]", "_gen_w_data"),
            ("output", "logic [DATA_WIDTH-1:0]", "r_data"),
        ],
        ref_code="module fifo_async(input logic w_data, output logic r_data); endmodule",
        harness_files={
            "src/test.py": """
            runner.build(parameters={"DATA_WIDTH": DATA_WIDTH, "DEPTH": DEPTH})
            dut.w_data.value = 0
            assert int(dut.r_data.value) == 0
            """,
        },
        sv_code="""
        module sparkle_fifo #(
            parameter integer DATA_WIDTH = 32,
            parameter integer DEPTH = 8
        ) (
            input logic [DATA_WIDTH-1:0] _gen_w_data,
            output logic [DATA_WIDTH-1:0] r_data
        );
        endmodule
        """,
    )

    assert wrapper is not None
    assert "parameter DATA_WIDTH = 32" in wrapper
    assert "parameter DEPTH = 8" in wrapper


def test_cvdp_wrapper_inherits_parameterized_width_for_scalar_reference_port():
    wrapper = generate_cvdp_wrapper(
        design_name="cdc_pulse_synchronizer",
        sparkle_mod_name="sparkle_cdc",
        sparkle_ports=[
            ("input", "logic [NUM_CHANNELS-1:0]", "_gen_src_pulse"),
            ("input", "logic", "src_clock"),
            ("input", "logic", "des_clock"),
            ("input", "logic", "rst_in"),
            ("output", "logic [NUM_CHANNELS-1:0]", "des_pulse"),
        ],
        ref_code="""
        module cdc_pulse_synchronizer(
            input logic src_clock,
            input logic des_clock,
            input logic rst_in,
            input logic src_pulse,
            output logic des_pulse
        );
        endmodule
        """,
        harness_files={
            "src/test_runner.py": """
            def test_runner(NUM_CHANNELS=4):
                runner.build(parameters={"NUM_CHANNELS": NUM_CHANNELS})
                dut.src_pulse.value = 1 << (NUM_CHANNELS - 1)
                assert int(dut.des_pulse.value) >= 0
            """,
        },
        sv_code="""
        module sparkle_cdc #(
            parameter integer NUM_CHANNELS = 8
        ) (
            input logic [NUM_CHANNELS-1:0] _gen_src_pulse,
            input logic src_clock,
            input logic des_clock,
            input logic rst_in,
            output logic [NUM_CHANNELS-1:0] des_pulse
        );
        endmodule
        """,
    )

    assert wrapper is not None
    assert "input logic [NUM_CHANNELS-1:0] src_pulse" in wrapper
    assert "output logic [NUM_CHANNELS-1:0] des_pulse" in wrapper
    assert ".NUM_CHANNELS(NUM_CHANNELS)" in wrapper
    assert "benchmark port src_pulse inherits parameterized core type" in wrapper


def test_cvdp_local_failure_classification_separates_infra_and_semantics():
    tool_status, tool_label = _cvdp_classify_local_failure(
        "FAILED test_runner.py::test_runner",
        "sh: /toolcache/ivl/ivlpp: not found\nexit status 127",
    )
    compile_status, compile_label = _cvdp_classify_local_failure(
        "Command '['iverilog', 'dut.sv']' returned non-zero exit status 1",
        "dut.sv:12: syntax error",
    )
    functional_status, functional_label = _cvdp_classify_local_failure(
        "FAILED test_runner.py::test_runner\nAssertionError: expected 3, got 2"
    )

    assert (tool_status, tool_label) == (
        "sim_error",
        "CVDP local simulator/tool error",
    )
    assert (compile_status, compile_label) == (
        "sim_error",
        "CVDP local Verilog compile error",
    )
    assert (functional_status, functional_label) == (
        "sim_fail",
        "CVDP local harness failed",
    )


def test_cvdp_wrapper_treats_observed_internal_reset_as_core_output():
    wrapper = generate_cvdp_wrapper(
        design_name="cdc",
        sparkle_mod_name="sparkle_cdc",
        sparkle_ports=[
            ("input", "logic", "rst_in"),
            ("output", "logic", "rst_src_sync"),
        ],
        ref_code="""
        module cdc(input logic rst_in);
          logic rst_src_sync;
        endmodule
        """,
        harness_files={
            "src/test.py": """
            dut.rst_in.value = 1
            await FallingEdge(dut.rst_src_sync)
            """,
        },
        sv_code="""
        module sparkle_cdc(
            input logic rst_in,
            output logic rst_src_sync
        );
        endmodule
        """,
    )

    assert wrapper is not None
    assert "output logic rst_src_sync" in wrapper
    assert "assign rst_src_sync = rst_src_sync_wire;" in wrapper
