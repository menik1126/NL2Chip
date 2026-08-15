from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "agent"))

from dataset import ProblemInfo  # noqa: E402
from evaluator import (  # noqa: E402
    CVDP_PARAMETERIZATION_UNSUPPORTED,
    CVDP_ADAPTER_ERROR,
    CVDPAdapterContractError,
    Evaluator,
    _cvdp_parameter_override_analysis,
    _parse_ref_module_ports_strict,
    generate_cvdp_wrapper,
    parse_module_ports,
)
from search import classify_failure_record, format_benchmark_interface_contract  # noqa: E402


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


def test_parse_module_ports_preserves_multiple_symbolic_dimensions():
    module_name, ports = parse_module_ports(
        """
        module generic_vector #(parameter size = 4) (
            input logic [7:0] [(size - 1):0] _gen_sig,
            output logic [7:0] [(size - 1):0] out
        );
        endmodule
        """
    )

    assert module_name == "generic_vector"
    assert ports == [
        ("input", "logic [7:0] [(size - 1):0]", "_gen_sig"),
        ("output", "logic [7:0] [(size - 1):0]", "out"),
    ]


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

    assert "Benchmark parameters required by reference/harness: DATA_WIDTH, FILO_DEPTH" in contract
    assert "DATA_WIDTH={10, 12}" in contract
    assert "FILO_DEPTH={12, 16}" in contract
    assert "fixed Sparkle core behind a parameterized wrapper is not a valid implementation" in contract
    assert "#synthesizeVerilog <design> parameters [PARAM := <nonnegative-default>]" in contract
    assert "native SystemVerilog module parameter" in contract


def test_cvdp_contract_includes_reference_parameter_without_harness_override():
    info = _cvdp_info(
        """
        module dut #(parameter WIDTH = 8) (
            output logic [WIDTH-1:0] data_out
        );
        endmodule
        """,
        "runner.build()",
    )

    contract = format_benchmark_interface_contract(info)

    assert "Benchmark parameters required by reference/harness: WIDTH" in contract
    assert "parameters [PARAM := <nonnegative-default>]" in contract


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

    assert "Benchmark parameters required by reference/harness: IN_WIDTH, N, OUT_WIDTH" in contract
    assert "Expected inputs: input bits: logic [N*IN_WIDTH-1:0]" in contract
    assert "Expected outputs: output I:" in contract
    assert "output Q:" in contract
    assert "output IN_WIDTH" not in contract
    assert "output OUT_WIDTH" not in contract


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
    assert "$bits(_gen_out_wire) - 1 -: $bits(_cvdp_shape__gen_out_0)" in wrapper
    assert "-: $bits(_cvdp_shape__gen_out_1)" in wrapper
    assert "-: $bits(_cvdp_shape__gen_out_2)" in wrapper
    assert "_cvdp_bad_width_bundle__gen_out" in wrapper
    assert "packed field width mismatch for sum" in wrapper
    assert "assign sum = '0;" not in wrapper
    assert "assign carry = '0;" not in wrapper


def test_cvdp_wrapper_does_not_blindly_slice_without_concat_fields():
    error = pytest.raises(CVDPAdapterContractError, generate_cvdp_wrapper,
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

    assert CVDP_ADAPTER_ERROR in str(error.value)


def test_cvdp_wrapper_rejects_parameterized_adapter_around_fixed_core():
    with pytest.raises(CVDPAdapterContractError, match=CVDP_PARAMETERIZATION_UNSUPPORTED):
        generate_cvdp_wrapper(
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


def test_cvdp_wrapper_forwards_native_sparkle_parameters():
    core = """
    module sparkle_inner #(
        parameter int WIDTH = 8,
        DEPTH = 4
    ) (
        input logic [WIDTH-1:0] _gen_data_in,
        output logic [WIDTH-1:0] _gen_data_out
    );
        logic [WIDTH-1:0] storage [0:DEPTH-1];
        assign _gen_data_out = _gen_data_in;
    endmodule
    """
    wrapper = generate_cvdp_wrapper(
        design_name="dut",
        sparkle_mod_name="sparkle_inner",
        sparkle_ports=[
            ("input", "logic [WIDTH-1:0]", "_gen_data_in"),
            ("output", "logic [WIDTH-1:0]", "_gen_data_out"),
        ],
        ref_code="""
        module dut #(
            parameter int WIDTH = 8,
            DEPTH = 4
        ) (
            input logic [WIDTH-1:0] data_in,
            output logic [WIDTH-1:0] data_out
        );
        endmodule
        """,
        harness_files={
            "src/test.py": """
            def test_runner(WIDTH, DEPTH):
                runner.build(parameters={"WIDTH": WIDTH, "DEPTH": DEPTH})
            dut.data_in.value = 0
            int(dut.data_out.value)
            """,
        },
        sv_code=core,
    )

    assert wrapper is not None
    assert "parameter int WIDTH = 8" in wrapper
    assert "parameter int DEPTH = 4" in wrapper
    assert "sparkle_inner #(\n        .WIDTH(WIDTH),\n        .DEPTH(DEPTH)\n    ) sparkle_dut (" in wrapper
    assert "localparam int _cvdp_core_WIDTH = WIDTH" in wrapper
    assert "logic [_cvdp_core_WIDTH-1:0] _gen_data_out_wire;" in wrapper


def test_cvdp_wrapper_localizes_unoverridden_derived_reference_width():
    wrapper = generate_cvdp_wrapper(
        design_name="digital_dice_roller",
        sparkle_mod_name="sparkle_inner",
        sparkle_ports=[
            (
                "output",
                "logic [(NUM_DICE * ($clog2(DICE_MAX) + 1))-1:0]",
                "dice_values",
            ),
        ],
        ref_code="""
        module digital_dice_roller #(
            parameter int DICE_MAX = 6,
            parameter int BIT_WIDTH = $clog2(DICE_MAX) + 1,
            parameter int NUM_DICE = 2
        ) (
            output logic [(NUM_DICE * BIT_WIDTH)-1:0] dice_values
        );
        endmodule
        """,
        harness_files={
            "src/test.py": """
            runner.build(
                parameters={"DICE_MAX": DICE_MAX, "NUM_DICE": NUM_DICE}
            )
            int(dut.dice_values.value)
            """,
        },
        sv_code="""
        module sparkle_inner #(
            parameter int DICE_MAX = 6,
            parameter int NUM_DICE = 2
        ) (
            output logic [(NUM_DICE * ($clog2(DICE_MAX) + 1))-1:0] dice_values
        );
            assign dice_values = '0;
        endmodule
        """,
    )

    assert wrapper is not None
    assert "localparam int BIT_WIDTH = $clog2(DICE_MAX) + 1" in wrapper
    assert ".DICE_MAX(DICE_MAX)" in wrapper
    assert ".NUM_DICE(NUM_DICE)" in wrapper
    assert ".BIT_WIDTH(" not in wrapper


def test_cvdp_wrapper_requires_derived_reference_width_when_harness_overrides_it():
    with pytest.raises(
        CVDPAdapterContractError,
        match=CVDP_PARAMETERIZATION_UNSUPPORTED,
    ) as error:
        generate_cvdp_wrapper(
            design_name="digital_dice_roller",
            sparkle_mod_name="sparkle_inner",
            sparkle_ports=[
                (
                    "output",
                    "logic [(NUM_DICE * ($clog2(DICE_MAX) + 1))-1:0]",
                    "dice_values",
                ),
            ],
            ref_code="""
            module digital_dice_roller #(
                parameter int DICE_MAX = 6,
                parameter int BIT_WIDTH = $clog2(DICE_MAX) + 1,
                parameter int NUM_DICE = 2
            ) (
                output logic [(NUM_DICE * BIT_WIDTH)-1:0] dice_values
            );
            endmodule
            """,
            harness_files={
                "src/test.py": """
                runner.build(parameters={
                    "DICE_MAX": DICE_MAX,
                    "BIT_WIDTH": BIT_WIDTH,
                    "NUM_DICE": NUM_DICE,
                })
                int(dut.dice_values.value)
                """,
            },
            sv_code="""
            module sparkle_inner #(
                parameter int DICE_MAX = 6,
                parameter int NUM_DICE = 2
            ) (
                output logic [(NUM_DICE * ($clog2(DICE_MAX) + 1))-1:0] dice_values
            );
                assign dice_values = '0;
            endmodule
            """,
        )

    assert "BIT_WIDTH" in str(error.value)


def test_cvdp_wrapper_rejects_declared_parameter_around_fixed_width_ports():
    core = """
    module sparkle_inner #(parameter WIDTH = 8) (
        input logic [7:0] _gen_data_in,
        output logic [7:0] _gen_data_out
    );
        // WIDTH affects real logic, so the separate mapped-port check must
        // still reject the fixed 8-bit interface.
        assign _gen_data_out = _gen_data_in << (WIDTH - 8);
    endmodule
    """

    with pytest.raises(CVDPAdapterContractError, match="fixed-width datapath"):
        generate_cvdp_wrapper(
            design_name="dut",
            sparkle_mod_name="sparkle_inner",
            sparkle_ports=[
                ("input", "logic [7:0]", "_gen_data_in"),
                ("output", "logic [7:0]", "_gen_data_out"),
            ],
            ref_code="""
            module dut #(parameter WIDTH = 8) (
                input logic [WIDTH-1:0] data_in,
                output logic [WIDTH-1:0] data_out
            );
            endmodule
            """,
            harness_files={
                "src/test.py": 'runner.build(parameters={"WIDTH": WIDTH})'
            },
            sv_code=core,
        )


def test_cvdp_wrapper_rejects_declaration_only_internal_parameter():
    with pytest.raises(CVDPAdapterContractError, match="does not use"):
        generate_cvdp_wrapper(
            design_name="dut",
            sparkle_mod_name="sparkle_inner",
            sparkle_ports=[("output", "logic", "empty")],
            ref_code="""
            module dut #(parameter DEPTH = 8) (output logic empty);
            endmodule
            """,
            harness_files={
                "src/test.py": 'runner.build(parameters={"DEPTH": DEPTH})'
            },
            sv_code="""
            module sparkle_inner #(parameter DEPTH = 8) (output logic empty);
                assign empty = 1'b1;
            endmodule
            """,
        )


def test_cvdp_wrapper_rejects_parameter_used_only_by_dead_localparam():
    with pytest.raises(CVDPAdapterContractError, match="does not use"):
        generate_cvdp_wrapper(
            design_name="dut",
            sparkle_mod_name="sparkle_inner",
            sparkle_ports=[("output", "logic", "empty")],
            ref_code="""
            module dut #(parameter DEPTH = 8) (output logic empty);
            endmodule
            """,
            harness_files={
                "src/test.py": 'runner.build(parameters={"DEPTH": DEPTH})'
            },
            sv_code="""
            module sparkle_inner #(parameter DEPTH = 8) (output logic empty);
                localparam integer UNUSED = DEPTH;
                assign empty = 1'b1;
            endmodule
            """,
        )


def test_cvdp_wrapper_accepts_live_localparam_parameter_dependency():
    wrapper = generate_cvdp_wrapper(
        design_name="dut",
        sparkle_mod_name="sparkle_inner",
        sparkle_ports=[("output", "logic [DEPTH-1:0]", "data")],
        ref_code="""
        module dut #(parameter DEPTH = 8) (
            output logic [DEPTH-1:0] data
        ); endmodule
        """,
        harness_files={
            "src/test.py": 'runner.build(parameters={"DEPTH": DEPTH})'
        },
        sv_code="""
        module sparkle_inner #(parameter DEPTH = 8) (output logic [DEPTH-1:0] data);
            localparam integer LIVE = DEPTH;
            logic [LIVE-1:0] storage;
            assign data = storage;
        endmodule
        """,
    )
    assert wrapper is not None


def test_cvdp_wrapper_ignores_sparkle_parameter_contract_guards():
    with pytest.raises(CVDPAdapterContractError, match="does not use"):
        generate_cvdp_wrapper(
            design_name="dut",
            sparkle_mod_name="sparkle_inner",
            sparkle_ports=[("output", "logic", "empty")],
            ref_code="""
            module dut #(parameter DEPTH = 8) (output logic empty);
            endmodule
            """,
            harness_files={
                "src/test.py": 'runner.build(parameters={"DEPTH": DEPTH})'
            },
            sv_code="""
            module sparkle_inner #(parameter DEPTH = 8) (output logic empty);
                generate if (!(DEPTH >= 0)) begin : sparkle_invalid_nat_parameter_0
                    initial $fatal(1, "Sparkle Nat parameter DEPTH must be nonnegative");
                end endgenerate
                assign empty = 1'b1;
            endmodule
            """,
        )


def test_cvdp_wrapper_rejects_parameter_used_only_by_dead_default_dependency():
    with pytest.raises(CVDPAdapterContractError, match="does not use"):
        generate_cvdp_wrapper(
            design_name="dut",
            sparkle_mod_name="sparkle_inner",
            sparkle_ports=[("output", "logic", "empty")],
            ref_code="""
            module dut #(parameter DEPTH = 8) (output logic empty);
            endmodule
            """,
            harness_files={
                "src/test.py": 'runner.build(parameters={"DEPTH": DEPTH})'
            },
            sv_code="""
            module sparkle_inner #(
                parameter DEPTH = 8,
                parameter UNUSED = DEPTH
            ) (output logic empty);
                assign empty = 1'b1;
            endmodule
            """,
        )


def test_cvdp_wrapper_accepts_transitively_live_parameter_dependency():
    wrapper = generate_cvdp_wrapper(
        design_name="dut",
        sparkle_mod_name="sparkle_inner",
        sparkle_ports=[("output", "logic [DEPTH-1:0]", "data_out")],
        ref_code="""
        module dut #(parameter WIDTH = 4) (output logic [(2*WIDTH)-1:0] data_out);
        endmodule
        """,
        harness_files={
            "src/test.py": 'runner.build(parameters={"WIDTH": WIDTH})'
        },
        sv_code="""
        module sparkle_inner #(
            parameter WIDTH = 4,
            parameter DEPTH = 2 * WIDTH
        ) (output logic [DEPTH-1:0] data_out);
            assign data_out = '0;
        endmodule
        """,
    )

    assert wrapper is not None
    assert ".WIDTH(WIDTH)" in wrapper


def test_cvdp_wrapper_accepts_parameter_used_by_internal_memory():
    wrapper = generate_cvdp_wrapper(
        design_name="dut",
        sparkle_mod_name="sparkle_inner",
        sparkle_ports=[("output", "logic", "empty")],
        ref_code="""
        module dut #(parameter DEPTH = 8) (output logic empty);
        endmodule
        """,
        harness_files={
            "src/test.py": 'runner.build(parameters={"DEPTH": DEPTH})'
        },
        sv_code="""
        module sparkle_inner #(parameter DEPTH = 8) (output logic empty);
            logic storage [0:DEPTH-1];
            assign empty = storage[0];
        endmodule
        """,
    )

    assert wrapper is not None
    assert ".DEPTH(DEPTH)" in wrapper


def test_cvdp_wrapper_rejects_fixed_core_for_reference_parameter_without_override():
    with pytest.raises(CVDPAdapterContractError, match=CVDP_PARAMETERIZATION_UNSUPPORTED) as error:
        generate_cvdp_wrapper(
            design_name="dut",
            sparkle_mod_name="sparkle_inner",
            sparkle_ports=[("output", "logic [7:0]", "data_out")],
            ref_code="""
            module dut #(parameter WIDTH = 8) (
                output logic [WIDTH-1:0] data_out
            );
            endmodule
            """,
            harness_files={"src/test.py": "int(dut.data_out.value)"},
            sv_code="module sparkle_inner(output logic [7:0] data_out); endmodule",
        )

    assert "WIDTH" in str(error.value)


def test_cvdp_wrapper_rejects_partially_parameterized_core():
    with pytest.raises(CVDPAdapterContractError, match=CVDP_PARAMETERIZATION_UNSUPPORTED) as error:
        generate_cvdp_wrapper(
            design_name="dut",
            sparkle_mod_name="sparkle_inner",
            sparkle_ports=[("output", "logic [WIDTH-1:0]", "data_out")],
            ref_code="""
            module dut #(
                parameter WIDTH = 8,
                parameter DEPTH = 4
            ) (output logic [WIDTH-1:0] data_out);
            endmodule
            """,
            harness_files={"src/test.py": "int(dut.data_out.value)"},
            sv_code="""
            module sparkle_inner #(parameter WIDTH = 8) (
                output logic [WIDTH-1:0] data_out
            );
            endmodule
            """,
        )

    assert "DEPTH" in str(error.value)


@pytest.mark.parametrize(
    ("harness", "expected_name", "unresolved"),
    [
        (
            """
            def test_runner(W):
                p = {}
                p["WIDTH"] = W
                runner.build(parameters=p)
            """,
            "WIDTH",
            False,
        ),
        (
            """
            def test_runner(W):
                p = {}
                p.update({"WIDTH": W})
                runner.build(parameters=p)
            """,
            "WIDTH",
            False,
        ),
        (
            """
            def test_runner(W):
                runner.build(**{"parameters": {"WIDTH": W}})
            """,
            "WIDTH",
            False,
        ),
        (
            """
            def test_runner(**kwargs):
                runner.build(**kwargs)
            """,
            None,
            True,
        ),
        (
            """
            def test_runner(W):
                p = {}
                (p.update({"WIDTH": W}), runner.build(parameters=p))
            """,
            None,
            True,
        ),
        (
            """
            def test_runner(configure):
                p = {}
                configure(p)
                runner.build(parameters=p)
            """,
            None,
            True,
        ),
        (
            """
            def test_runner(W):
                kwargs = {"parameters": {}}
                kwargs["parameters"]["WIDTH"] = W
                runner.build(**kwargs)
            """,
            None,
            True,
        ),
        (
            """
            def test_runner(W):
                kwargs = {"parameters": {}}
                kwargs["parameters"].update({"WIDTH": W})
                runner.build(**kwargs)
            """,
            None,
            True,
        ),
    ],
)
def test_cvdp_fixed_core_parameter_detection_is_fail_closed(
    harness, expected_name, unresolved
):
    harness_files = {"src/test.py": harness}
    analysis = _cvdp_parameter_override_analysis(harness_files)

    assert analysis.may_have_overrides is True
    assert analysis.unresolved is unresolved
    assert analysis.build_call_count == 1
    if expected_name is not None:
        assert expected_name in analysis.parameter_names

    with pytest.raises(CVDPAdapterContractError, match=CVDP_PARAMETERIZATION_UNSUPPORTED):
        generate_cvdp_wrapper(
            design_name="dut",
            sparkle_mod_name="sparkle_inner",
            sparkle_ports=[("output", "logic", "out")],
            ref_code="module dut(output logic done); endmodule",
            harness_files=harness_files,
            sv_code="module sparkle_inner(output logic out); endmodule",
        )


def test_cvdp_parameter_analysis_keeps_same_named_locals_in_their_function_scope():
    harness_files = {
        "src/test.py": """
        def unrelated_helper(W):
            p = {"GHOST_WIDTH": W}
            return p

        def actual_runner():
            p = {}
            runner.build(parameters=p)
        """,
    }

    analysis = _cvdp_parameter_override_analysis(harness_files)

    assert analysis.may_have_overrides is False
    assert analysis.parameter_names == frozenset()
    wrapper = generate_cvdp_wrapper(
        design_name="dut",
        sparkle_mod_name="sparkle_inner",
        sparkle_ports=[("output", "logic", "out")],
        ref_code="module dut(output logic done); endmodule",
        harness_files=harness_files,
        sv_code="module sparkle_inner(output logic out); endmodule",
    )
    assert wrapper is not None


def test_cvdp_parameter_analysis_ignores_unrelated_build_without_parameters():
    analysis = _cvdp_parameter_override_analysis({
        "src/test.py": """
        def unrelated_helper():
            cache_builder.build(cache_key="GHOST_WIDTH")

        def test_runner(params):
            runner.build(parameters=params)
        """,
    })

    assert analysis.may_have_overrides is True
    assert analysis.unresolved is True
    assert analysis.parameter_names == frozenset()


@pytest.mark.parametrize(
    "harness",
    [
        """
        def test_runner(W):
            r = get_runner(simulator="icarus")
            r.build(parameters={"WIDTH": W})
        """,
        """
        def test_runner(W):
            get_runner(simulator="icarus").build(parameters={"WIDTH": W})
        """,
    ],
)
def test_cvdp_parameter_analysis_does_not_depend_on_runner_variable_name(harness):
    analysis = _cvdp_parameter_override_analysis({"src/test.py": harness})

    assert analysis.may_have_overrides is True
    assert analysis.parameter_names == frozenset({"WIDTH"})


@pytest.mark.parametrize(
    "harness",
    [
        """
        p = {}
        discarded = p.update({"WIDTH": 8})
        runner.build(parameters=p)
        """,
        """
        p = {}
        if p.update({"WIDTH": 8}):
            pass
        runner.build(parameters=p)
        """,
    ],
)
def test_cvdp_parameter_analysis_applies_embedded_mapping_mutations(harness):
    analysis = _cvdp_parameter_override_analysis({"src/test.py": harness})

    assert analysis.may_have_overrides is True
    assert analysis.unresolved is False
    assert analysis.parameter_names == frozenset({"WIDTH"})


@pytest.mark.parametrize(
    "harness",
    [
        """
        compile_dut = runner.build
        compile_dut(parameters={"WIDTH": 8})
        """,
        """
        compile_dut = runner.build
        kwargs = {"parameters": {"WIDTH": 8}}
        compile_dut(**kwargs)
        """,
        """
        from functools import partial
        compile_dut = partial(runner.build, parameters={"WIDTH": 8})
        compile_dut()
        """,
    ],
)
def test_cvdp_parameter_analysis_tracks_bound_build_aliases(harness):
    analysis = _cvdp_parameter_override_analysis({"src/test.py": harness})

    assert analysis.may_have_overrides is True
    assert "WIDTH" in analysis.parameter_names


@pytest.mark.parametrize(
    "harness",
    [
        """
        compile_dut = getattr(runner, "build")
        kwargs = {"parameters": {"WIDTH": 8}}
        compile_dut(**kwargs)
        """,
        """
        holder.compile_dut = runner.build
        kwargs = {"parameters": {"WIDTH": 8}}
        holder.compile_dut(**kwargs)
        """,
        """
        compile_dut, = (runner.build,)
        kwargs = {"parameters": {"WIDTH": 8}}
        compile_dut(**kwargs)
        """,
        """
        def select_build():
            return runner.build

        compile_dut = select_build()
        kwargs = {"parameters": {"WIDTH": 8}}
        compile_dut(**kwargs)
        """,
    ],
)
def test_cvdp_parameter_analysis_tracks_indirect_build_aliases(harness):
    analysis = _cvdp_parameter_override_analysis({"src/test.py": harness})

    assert analysis.may_have_overrides is True
    assert analysis.parameter_names == frozenset({"WIDTH"})


def test_cvdp_parameter_analysis_ignores_unrelated_parameters_keyword():
    analysis = _cvdp_parameter_override_analysis({
        "src/test.py": """
        cache.configure(parameters={"THREADS": 4})
        runner.build()
        """,
    })

    assert analysis.may_have_overrides is False
    assert analysis.parameter_names == frozenset()


def test_cvdp_parameter_analysis_fails_closed_on_called_closure_mutation():
    analysis = _cvdp_parameter_override_analysis({
        "src/test.py": """
        parameters = {}

        def configure():
            parameters["WIDTH"] = 8

        configure()
        runner.build(parameters=parameters)
        """,
    })
    shadowed = _cvdp_parameter_override_analysis({
        "src/test.py": """
        parameters = {}

        def configure():
            parameters = {"GHOST_WIDTH": 99}
            return parameters

        configure()
        runner.build(parameters=parameters)
        """,
    })

    assert analysis.may_have_overrides is True
    assert analysis.unresolved is True
    assert shadowed.may_have_overrides is False


def test_cvdp_parameter_analysis_models_dict_copy_as_an_independent_mapping():
    original_kept = _cvdp_parameter_override_analysis({
        "src/test.py": """
        def test_runner():
            p = {"WIDTH": 8}
            q = p.copy()
            q.clear()
            runner.build(parameters=p)
        """,
    })
    original_empty = _cvdp_parameter_override_analysis({
        "src/test.py": """
        def test_runner():
            p = {}
            q = p.copy()
            q.update({"WIDTH": 8})
            runner.build(parameters=p)
        """,
    })

    assert original_kept.may_have_overrides is True
    assert original_kept.parameter_names == frozenset({"WIDTH"})
    assert original_empty.may_have_overrides is False
    assert original_empty.parameter_names == frozenset()


def test_cvdp_sim_rejects_fixed_core_before_running_parameter_harness(tmp_path, monkeypatch):
    info = _cvdp_info(
        """
        module dut #(parameter WIDTH = 8) (
            input logic [WIDTH-1:0] data_in,
            output logic [WIDTH-1:0] data_out
        );
        endmodule
        """,
        """
        def test_runner(WIDTH):
            runner.build(parameters={"WIDTH": WIDTH})
        """,
    )

    class DatasetStub:
        def load_problem(self, prob_id: str) -> ProblemInfo:
            return info

    evaluator = Evaluator(project_root=tmp_path, dataset="cvdp", dataset_obj=DatasetStub())
    local_sim_called = False

    def fake_local_sim(_sim_dir: Path):
        nonlocal local_sim_called
        local_sim_called = True
        return "sim_pass", 0, "unexpected"

    monkeypatch.setenv("CVDP_SIM_MODE", "local")
    monkeypatch.setattr(evaluator, "_run_sim_cvdp_local", fake_local_sim)
    code = "module sparkle_inner(input logic [7:0] _gen_data_in, output logic [7:0] out); endmodule"
    module_name, ports = parse_module_ports(code)

    status, mismatches, detail = evaluator._run_sim_cvdp(
        "cvdp_test", code, module_name, ports, tmp_path
    )

    assert status == "sim_error"
    assert mismatches == -1
    assert CVDP_PARAMETERIZATION_UNSUPPORTED in detail
    assert "WIDTH" in detail
    assert not local_sim_called
    assert not (tmp_path / "cvdp_sim" / "cvdp_test").exists()


def test_cvdp_sim_allows_native_sparkle_parameter_forwarding(tmp_path, monkeypatch):
    info = _cvdp_info(
        """
        module dut #(parameter WIDTH = 8) (
            input logic [WIDTH-1:0] data_in,
            output logic [WIDTH-1:0] data_out
        );
        endmodule
        """,
        """
        def test_runner(WIDTH):
            runner.build(parameters={"WIDTH": WIDTH})
        """,
    )

    class DatasetStub:
        def load_problem(self, prob_id: str) -> ProblemInfo:
            return info

    evaluator = Evaluator(project_root=tmp_path, dataset="cvdp", dataset_obj=DatasetStub())
    captured = {}

    def fake_local_sim(sim_dir: Path):
        captured["source"] = (sim_dir / "rtl" / "dut.sv").read_text()
        return "sim_pass", 0, "native parameter sweep accepted"

    monkeypatch.setenv("CVDP_SIM_MODE", "local")
    monkeypatch.setattr(evaluator, "_run_sim_cvdp_local", fake_local_sim)
    code = """
    module dut #(parameter WIDTH = 8) (
        input logic [WIDTH-1:0] _gen_data_in,
        output logic [WIDTH-1:0] _gen_data_out
    );
        assign _gen_data_out = _gen_data_in;
    endmodule
    """
    module_name, ports = parse_module_ports(code)

    status, mismatches, detail = evaluator._run_sim_cvdp(
        "cvdp_test", code, module_name, ports, tmp_path
    )

    assert (status, mismatches, detail) == (
        "sim_pass",
        0,
        "native parameter sweep accepted",
    )
    assert "module dut_sparkle_inner #(parameter WIDTH = 8)" in captured["source"]
    assert "dut_sparkle_inner #(\n        .WIDTH(WIDTH)\n    ) sparkle_dut (" in captured["source"]


def test_cvdp_sim_allows_dynamic_parameter_dict_with_reference_contract(
    tmp_path, monkeypatch
):
    info = _cvdp_info(
        "module dut #(parameter WIDTH = 8) (output logic [WIDTH-1:0] data_out); endmodule",
        """
        def test_runner(params):
            runner.build(parameters=params)
        """,
    )

    class DatasetStub:
        def load_problem(self, prob_id: str) -> ProblemInfo:
            return info

    evaluator = Evaluator(project_root=tmp_path, dataset="cvdp", dataset_obj=DatasetStub())
    local_sim_called = False

    def fake_local_sim(_sim_dir: Path):
        nonlocal local_sim_called
        local_sim_called = True
        return "sim_pass", 0, "dynamic parameter dictionary accepted"

    monkeypatch.setenv("CVDP_SIM_MODE", "local")
    monkeypatch.setattr(evaluator, "_run_sim_cvdp_local", fake_local_sim)
    code = """
    module sparkle_inner #(parameter WIDTH = 8) (
        output logic [WIDTH-1:0] data_out
    );
        assign data_out = '0;
    endmodule
    """
    module_name, ports = parse_module_ports(code)

    status, mismatches, detail = evaluator._run_sim_cvdp(
        "cvdp_test", code, module_name, ports, tmp_path
    )

    assert (status, mismatches, detail) == (
        "sim_pass",
        0,
        "dynamic parameter dictionary accepted",
    )
    assert local_sim_called


def test_cvdp_sim_rejects_internal_structure_parameter_even_with_fixed_ports(tmp_path):
    info = _cvdp_info(
        """
        module dut #(parameter DEPTH = 8) (
            input logic clk,
            output logic empty
        );
        endmodule
        """,
        """
        def test_runner(DEPTH):
            runner.build(parameters={"DEPTH": DEPTH})
        """,
    )

    class DatasetStub:
        def load_problem(self, prob_id: str) -> ProblemInfo:
            return info

    evaluator = Evaluator(project_root=tmp_path, dataset="cvdp", dataset_obj=DatasetStub())
    code = "module sparkle_inner(input logic clk, output logic out); endmodule"
    module_name, ports = parse_module_ports(code)

    status, _, detail = evaluator._run_sim_cvdp(
        "cvdp_test", code, module_name, ports, tmp_path
    )

    assert status == "sim_error"
    assert CVDP_PARAMETERIZATION_UNSUPPORTED in detail
    assert "DEPTH" in detail


def test_cvdp_sim_rejects_unresolved_dynamic_parameter_dictionary(tmp_path):
    info = _cvdp_info(
        "module dut #(parameter WIDTH = 8) (output logic done); endmodule",
        """
        def test_runner(params):
            runner.build(parameters=params)
        """,
    )

    class DatasetStub:
        def load_problem(self, prob_id: str) -> ProblemInfo:
            return info

    evaluator = Evaluator(project_root=tmp_path, dataset="cvdp", dataset_obj=DatasetStub())
    code = "module sparkle_inner(output logic out); endmodule"
    module_name, ports = parse_module_ports(code)

    status, _, detail = evaluator._run_sim_cvdp(
        "cvdp_test", code, module_name, ports, tmp_path
    )

    assert status == "sim_error"
    assert CVDP_PARAMETERIZATION_UNSUPPORTED in detail
    assert "full parameter set could not be resolved" in detail


def test_unsupported_parameterization_is_repairable_and_skips_synthesis(tmp_path, monkeypatch):
    generated = tmp_path / "Generated"
    generated.mkdir()
    (generated / "cvdp_test.lean").write_text("def placeholder := 0")
    sv_code = "module sparkle_inner(input logic [7:0] data_in, output logic [7:0] out); endmodule"

    class ReplStub:
        def check_file(self, _path: Path):
            return SimpleNamespace(
                passed=True,
                complete=True,
                verilog=sv_code,
                error_text="",
            )

    evaluator = Evaluator(
        project_root=tmp_path,
        dataset="cvdp",
        enable_synth=True,
        lean_repl=ReplStub(),
    )
    synthesis_called = False

    monkeypatch.setattr(evaluator, "_run_lint", lambda _path: True)
    monkeypatch.setattr(
        evaluator,
        "_run_sim",
        lambda *_args: (
            "sim_error",
            -1,
            f"{CVDP_PARAMETERIZATION_UNSUPPORTED}: fixed core",
        ),
    )

    def fake_synthesis(*_args):
        nonlocal synthesis_called
        synthesis_called = True
        return {"synth_pass": True}

    monkeypatch.setattr(evaluator, "_run_synthesis", fake_synthesis)

    result = evaluator.evaluate("cvdp_test", tmp_path / "run")

    assert result["unsupported_parameterization"] is True
    assert result["terminal_capability_error"] is False
    assert result["repairable_parameterization_error"] is True
    assert result["synth_pass"] is False
    assert result["area_um2"] is None
    assert not synthesis_called
    assert classify_failure_record(result) == {
        "failure_stage": "simulation",
        "failure_category": "parameterization_contract",
        "failure_family": "interface",
    }


@pytest.mark.parametrize(
    ("harness", "expected_config"),
    [
        ('runner.build(parameters={"WIDTH": WIDTH})', None),
        ("runner.build()", {}),
    ],
    ids=["unresolved-symbolic-sweep", "exact-default-configuration"],
)
def test_native_parameter_sweep_runs_only_when_configuration_is_exact(
    tmp_path, monkeypatch, harness, expected_config
):
    generated = tmp_path / "Generated"
    generated.mkdir()
    (generated / "cvdp_test.lean").write_text("def placeholder := 0")
    sv_code = """
    module sparkle_inner #(parameter WIDTH = 8) (
        input logic [WIDTH-1:0] data_in,
        output logic [WIDTH-1:0] data_out
    );
        assign data_out = data_in;
    endmodule
    """
    info = _cvdp_info(
        """
        module dut #(parameter WIDTH = 8) (
            input logic [WIDTH-1:0] data_in,
            output logic [WIDTH-1:0] data_out
        );
        endmodule
        """,
        harness,
    )

    class ReplStub:
        def check_file(self, _path: Path):
            return SimpleNamespace(
                passed=True,
                complete=True,
                verilog=sv_code,
                error_text="",
            )

    class DatasetStub:
        def load_problem(self, prob_id: str) -> ProblemInfo:
            return info

    evaluator = Evaluator(
        project_root=tmp_path,
        dataset="cvdp",
        dataset_obj=DatasetStub(),
        enable_synth=True,
        lean_repl=ReplStub(),
    )
    synthesis_called = False

    monkeypatch.setattr(evaluator, "_run_lint", lambda _path: True)
    monkeypatch.setattr(
        evaluator,
        "_run_sim",
        lambda *_args: ("sim_pass", 0, "all native parameter configurations passed"),
    )

    def fake_synthesis(*_args, **_kwargs):
        nonlocal synthesis_called
        synthesis_called = True
        return {"synth_pass": True, "area_um2": 1.0}

    monkeypatch.setattr(evaluator, "_run_synthesis", fake_synthesis)

    result = evaluator.evaluate("cvdp_test", tmp_path / "run")

    assert result["sim_status"] == "sim_pass"
    assert result["sim_mismatches"] == 0
    assert result["terminal_capability_error"] is False
    if expected_config is None:
        assert result["parameterized_ppa_unsupported"] is True
        assert result["synth_status"] == "not_run_parameterized_sweep"
        assert result["ppa_status"] == "unsupported_parameter_sweep"
        assert result["synth_pass"] is False
        assert result["area_um2"] is None
        assert "module-default configuration" in result["ppa_error"]
        assert not synthesis_called
    else:
        assert result["parameterized_ppa_unsupported"] is False
        assert result["synth_status"] == "finite_parameter_sweep_passed"
        assert result["ppa_status"] == "finite_parameter_sweep"
        assert result["synth_pass"] is True
        assert result["area_um2"] == 1.0
        assert result["parameter_sweep_results"][0]["config"] == expected_config
        assert synthesis_called
        evaluator.parameterized_ppa_runner.close()


def test_cvdp_wrapper_rejects_unique_but_semantically_unproven_internal_memory():
    error = pytest.raises(CVDPAdapterContractError, generate_cvdp_wrapper,
        design_name="fifo_policy",
        sparkle_mod_name="fifo_policy_sparkle_inner",
        sparkle_ports=[
            ("input", "logic [$clog2(NINDEXES)-1:0]", "_gen_index"),
            ("input", "logic", "_gen_reset"),
            ("input", "logic", "clk"),
            ("input", "logic", "rst"),
            ("output", "logic [$clog2(NWAYS)-1:0]", "out"),
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
            from cocotb.clock import Clock
            Clock(dut.clock, 10, unit="ns")
            dut.clock.value = 0
            int(dut.way_replace.value)
            int(dut.fifo_array[0].value)
            """,
        },
        sv_code="""
        module fifo_policy_sparkle_inner #(
            parameter NWAYS = 4,
            parameter NINDEXES = 32
        ) (
            input logic [$clog2(NINDEXES)-1:0] _gen_index,
            input logic _gen_reset,
            input logic clk,
            input logic rst,
            output logic [$clog2(NWAYS)-1:0] out
        );
            logic [$clog2(NWAYS)-1:0] _gen_current_way [0:NINDEXES-1];
            assign out = _gen_current_way[_gen_index];
        endmodule
        """,
    )

    assert "could not be mapped uniquely" in str(error.value)


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
    info = _cvdp_info(
        code,
        'runner.build(parameters={"WIDTH": 4})',
        design_name="dut",
    )

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


def test_cvdp_direct_top_must_declare_harness_parameters(tmp_path):
    code = "module dut(output logic done); assign done = 1'b1; endmodule"
    info = _cvdp_info(
        code,
        'runner.build(parameters={"WIDTH": 4})',
        design_name="dut",
    )

    class DatasetStub:
        def load_problem(self, prob_id: str) -> ProblemInfo:
            return info

    evaluator = Evaluator(project_root=tmp_path, dataset="cvdp", dataset_obj=DatasetStub())
    module_name, ports = parse_module_ports(code, module_name="dut")

    status, _, detail = evaluator._run_sim_cvdp(
        "cvdp_test",
        code,
        module_name,
        ports,
        tmp_path,
        direct_top=True,
    )

    assert status == "sim_error"
    assert CVDP_PARAMETERIZATION_UNSUPPORTED in detail
    assert "does not declare harness parameter(s): WIDTH" in detail


def test_cvdp_direct_top_passes_unresolved_dynamic_parameters_to_simulator(
    tmp_path, monkeypatch
):
    code = """
    module dut #(parameter WIDTH = 4) (output logic [WIDTH-1:0] done);
        assign done = '0;
    endmodule
    """
    info = _cvdp_info(
        code,
        """
        def test_runner(params):
            runner.build(parameters=params)
        """,
        design_name="dut",
    )

    class DatasetStub:
        def load_problem(self, prob_id: str) -> ProblemInfo:
            return info

    evaluator = Evaluator(project_root=tmp_path, dataset="cvdp", dataset_obj=DatasetStub())
    module_name, ports = parse_module_ports(code, module_name="dut")
    local_sim_called = False

    def fake_local_sim(_sim_dir: Path):
        nonlocal local_sim_called
        local_sim_called = True
        return "sim_pass", 0, "simulator accepted dynamic parameters"

    monkeypatch.setenv("CVDP_SIM_MODE", "local")
    monkeypatch.setattr(evaluator, "_run_sim_cvdp_local", fake_local_sim)

    status, mismatches, detail = evaluator._run_sim_cvdp(
        "cvdp_test",
        code,
        module_name,
        ports,
        tmp_path,
        direct_top=True,
    )

    assert (status, mismatches, detail) == (
        "sim_pass",
        0,
        "simulator accepted dynamic parameters",
    )
    assert local_sim_called

def _make_symbolic_bundle_wrapper(
    *,
    parameter_name: str,
    bundle_type: str,
    output_declarations: tuple[str, ...],
    field_declarations: tuple[str, ...],
) -> str:
    output_names = [declaration.rsplit(" ", 1)[1] for declaration in output_declarations]
    ref_ports = ",\n".join(
        f"        output {declaration}" for declaration in output_declarations
    )
    field_signals = "\n".join(
        f"        {declaration};" for declaration in field_declarations
    )
    field_names = [declaration.rsplit(" ", 1)[1] for declaration in field_declarations]
    harness = (
        f'runner.build(parameters={{"{parameter_name}": {parameter_name}}})\n'
        + "\n".join(f"int(dut.{name}.value)" for name in output_names)
    )
    core = f"""
    module sparkle_inner #(parameter {parameter_name} = 8) (
        output {bundle_type} _gen_out
    );
{field_signals}
        assign _gen_out = {{{", ".join(field_names)}}};
    endmodule
    """
    wrapper = generate_cvdp_wrapper(
        design_name="dut",
        sparkle_mod_name="sparkle_inner",
        sparkle_ports=[("output", bundle_type, "_gen_out")],
        ref_code=f"""
        module dut #(parameter {parameter_name} = 8) (
{ref_ports}
        );
        endmodule
        """,
        harness_files={"src/test.py": harness},
        sv_code=core,
    )
    assert wrapper is not None
    return wrapper


@pytest.mark.parametrize(
    (
        "parameter_name",
        "bundle_type",
        "output_declarations",
        "field_declarations",
    ),
    [
        (
            "DATA_WIDTH",
            "logic [DATA_WIDTH+2-1:0]",
            (
                "logic [DATA_WIDTH-1:0] data_out",
                "logic empty",
                "logic full",
            ),
            (
                "logic [DATA_WIDTH-1:0] _gen_data_out",
                "logic _gen_empty",
                "logic _gen_full",
            ),
        ),
        (
            "DATA_WIDTH",
            "logic [(2*DATA_WIDTH+32)-1:0]",
            (
                "logic [DATA_WIDTH-1:0] data_out",
                "logic [DATA_WIDTH+32-1:0] encoded_data",
            ),
            (
                "logic [DATA_WIDTH-1:0] _gen_data_out",
                "logic [DATA_WIDTH+32-1:0] _gen_encoded_data",
            ),
        ),
        (
            "WIDTH",
            "logic [(WIDTH/2)+1-1:0]",
            (
                "logic [WIDTH/2-1:0] root",
                "logic done",
            ),
            (
                "logic [WIDTH/2-1:0] _gen_root",
                "logic _gen_done",
            ),
        ),
        (
            "WIDTH",
            "logic [WIDTH+(WIDTH+1)-1:0]",
            (
                "logic [WIDTH-1:0] quotient",
                "logic [WIDTH:0] remainder",
            ),
            (
                "logic [WIDTH-1:0] _gen_quotient",
                "logic [WIDTH:0] _gen_remainder",
            ),
        ),
    ],
)
def test_cvdp_wrapper_supports_common_symbolic_bundle_widths(
    parameter_name,
    bundle_type,
    output_declarations,
    field_declarations,
):
    wrapper = _make_symbolic_bundle_wrapper(
        parameter_name=parameter_name,
        bundle_type=bundle_type,
        output_declarations=output_declarations,
        field_declarations=field_declarations,
    )

    for index, declaration in enumerate(field_declarations):
        field_type = declaration.rsplit(" ", 1)[0]
        mirrored_field_type = re.sub(
            rf"\b{re.escape(parameter_name)}\b",
            f"_cvdp_core_{parameter_name}",
            field_type,
        )
        assert (
            f"{mirrored_field_type} _cvdp_shape__gen_out_{index};" in wrapper
        )
    assert "_cvdp_bad_width_bundle__gen_out" in wrapper
    assert "assign data_out = '0;" not in wrapper


def test_cvdp_wrapper_ignores_helper_ports_when_target_reference_is_absent():
    wrapper = generate_cvdp_wrapper(
        design_name="Bit_Difference_Counter",
        sparkle_mod_name="sparkle_inner",
        sparkle_ports=[
            ("input", "logic [BIT_WIDTH-1:0]", "_gen_input_A"),
            ("input", "logic [BIT_WIDTH-1:0]", "_gen_input_B"),
            (
                "output",
                "logic [$clog2(BIT_WIDTH+1)-1:0]",
                "_gen_out",
            ),
        ],
        ref_code="""
        module Data_Reduction(
            input logic [TOTAL_INPUT_WIDTH-1:0] data_in,
            output logic [DATA_WIDTH-1:0] reduced_data_out
        );
        endmodule
        """,
        harness_files={
            "src/test.py": """
            runner.build(parameters={"BIT_WIDTH": BIT_WIDTH})
            dut.input_A.value = 0
            dut.input_B.value = 0
            int(dut.bit_difference_count.value)
            int(dut.COUNT_WIDTH.value)
            """,
        },
        sv_code="""
        module sparkle_inner #(parameter BIT_WIDTH = 8) (
            input logic [BIT_WIDTH-1:0] _gen_input_A,
            input logic [BIT_WIDTH-1:0] _gen_input_B,
            output logic [$clog2(BIT_WIDTH+1)-1:0] _gen_out
        );
            logic [$clog2(BIT_WIDTH+1)-1:0] _gen_bit_difference_count;
            assign _gen_out = {_gen_bit_difference_count};
        endmodule
        """,
    )

    assert wrapper is not None
    assert "TOTAL_INPUT_WIDTH" not in wrapper
    assert "DATA_WIDTH" not in wrapper
    assert "data_in" not in wrapper
    assert "reduced_data_out" not in wrapper
    assert "localparam integer COUNT_WIDTH = $bits(bit_difference_count);" in wrapper
    assert "$bits(_cvdp_shape__gen_out_0)" in wrapper


def test_cvdp_wrapper_preserves_reference_header_localparam_before_ports():
    wrapper = generate_cvdp_wrapper(
        design_name="dut",
        sparkle_mod_name="sparkle_inner",
        sparkle_ports=[
            (
                "output",
                "logic [$clog2(WIDTH+1)-1:0]",
                "count",
            ),
        ],
        ref_code="""
        module dut #(
            parameter WIDTH = 8,
            localparam integer COUNT_WIDTH = $clog2(WIDTH+1)
        ) (
            output logic [COUNT_WIDTH-1:0] count
        );
        endmodule
        """,
        harness_files={
            "src/test.py": """
            runner.build(parameters={"WIDTH": WIDTH})
            int(dut.count.value)
            int(dut.COUNT_WIDTH.value)
            """,
        },
        sv_code="""
        module sparkle_inner #(parameter WIDTH = 8) (
            output logic [$clog2(WIDTH+1)-1:0] count
        );
            assign count = '0;
        endmodule
        """,
    )

    assert wrapper is not None
    assert "localparam integer COUNT_WIDTH = $clog2(WIDTH+1)" in wrapper
    assert "localparam integer COUNT_WIDTH = $bits(count)" not in wrapper
    assert "output logic [COUNT_WIDTH-1:0] count" in wrapper


def test_cvdp_wrapper_keeps_reference_types_and_guards_direct_symbolic_widths():
    wrapper = generate_cvdp_wrapper(
        design_name="dut",
        sparkle_mod_name="sparkle_inner",
        sparkle_ports=[
            ("input", "logic [2*WIDTH-1:0]", "_gen_data_in"),
            ("output", "logic [2*WIDTH-1:0]", "_gen_data_out"),
        ],
        ref_code="""
        module dut #(parameter WIDTH = 8) (
            input logic [WIDTH-1:0] data_in,
            output logic [WIDTH-1:0] data_out
        );
        endmodule
        """,
        harness_files={
            "src/test.py": """
            runner.build(parameters={"WIDTH": WIDTH})
            dut.data_in.value = 0
            int(dut.data_out.value)
            """,
        },
        sv_code="""
        module sparkle_inner #(parameter WIDTH = 8) (
            input logic [2*WIDTH-1:0] _gen_data_in,
            output logic [2*WIDTH-1:0] _gen_data_out
        );
            assign _gen_data_out = _gen_data_in;
        endmodule
        """,
    )

    assert wrapper is not None
    assert "input logic [WIDTH-1:0] data_in" in wrapper
    assert "output logic [WIDTH-1:0] data_out" in wrapper
    assert "_cvdp_bad_width_input_data_in" in wrapper
    assert "_cvdp_bad_width_output_data_out" in wrapper


@pytest.mark.parametrize(
    ("ref_inputs", "core_inputs", "detail"),
    [
        (("a", "b"), ("_gen_a",), "are not consumed exactly once"),
        (("a",), ("_gen_a", "_gen_secret"), "Sparkle input '_gen_secret'"),
        (("a",), ("_gen_a", "a"), "Sparkle input 'a'"),
    ],
)
def test_cvdp_wrapper_rejects_non_bijective_input_mapping(
    ref_inputs,
    core_inputs,
    detail,
):
    ref_port_text = ",\n".join(
        [*(f"input logic {name}" for name in ref_inputs), "output logic y"]
    )
    core_port_text = ",\n".join(
        [*(f"input logic {name}" for name in core_inputs), "output logic y"]
    )
    sparkle_ports = [
        *(("input", "logic", name) for name in core_inputs),
        ("output", "logic", "y"),
    ]
    harness = "\n".join(
        [*(f"dut.{name}.value = 0" for name in ref_inputs), "int(dut.y.value)"]
    )

    error = pytest.raises(
        CVDPAdapterContractError,
        generate_cvdp_wrapper,
        design_name="dut",
        sparkle_mod_name="sparkle_inner",
        sparkle_ports=sparkle_ports,
        ref_code=f"module dut({ref_port_text}); endmodule",
        harness_files={"src/test.py": harness},
        sv_code=f"module sparkle_inner({core_port_text}); assign y = 1'b0; endmodule",
    )

    assert detail in str(error.value)


@pytest.mark.parametrize(
    ("field_name", "output_name"),
    [
        ("_gen_not_ready", "ready"),
        ("_gen_invalid", "valid"),
        ("_tmp_hidden", "visible"),
    ],
)
def test_cvdp_wrapper_rejects_semantically_unproven_bundle_names(
    field_name,
    output_name,
):
    error = pytest.raises(
        CVDPAdapterContractError,
        generate_cvdp_wrapper,
        design_name="dut",
        sparkle_mod_name="sparkle_inner",
        sparkle_ports=[("output", "logic", "_gen_out")],
        ref_code=f"module dut(output logic {output_name}); endmodule",
        harness_files={"src/test.py": f"int(dut.{output_name}.value)"},
        sv_code=f"""
        module sparkle_inner(output logic _gen_out);
            logic {field_name};
            assign _gen_out = {{{field_name}}};
        endmodule
        """,
    )

    assert CVDP_ADAPTER_ERROR in str(error.value)


def test_cvdp_wrapper_scopes_concat_provenance_to_exact_sparkle_module():
    wrapper = generate_cvdp_wrapper(
        design_name="dut",
        sparkle_mod_name="sparkle_inner",
        sparkle_ports=[("output", "logic", "_gen_out")],
        ref_code="module dut(output logic b); endmodule",
        harness_files={"src/test.py": "int(dut.b.value)"},
        sv_code="""
        module helper(output logic _gen_out);
            logic _gen_a;
            assign _gen_out = {_gen_a};
        endmodule
        module sparkle_inner(output logic _gen_out);
            logic _gen_b;
            assign _gen_out = {_gen_b};
        endmodule
        """,
    )

    assert wrapper is not None
    assert "$bits(_cvdp_shape__gen_out_0)" in wrapper
    assert "sparkle_dut._gen_a" not in wrapper


def test_cvdp_wrapper_rejects_dynamic_index_in_hierarchical_tuple_field():
    error = pytest.raises(
        CVDPAdapterContractError,
        generate_cvdp_wrapper,
        design_name="dut",
        sparkle_mod_name="sparkle_inner",
        sparkle_ports=[("output", "logic", "_gen_out")],
        ref_code="module dut(output logic mem); endmodule",
        harness_files={"src/test.py": "int(dut.mem.value)"},
        sv_code="""
        module sparkle_inner(output logic _gen_out);
            logic [1:0] mem;
            logic _gen_idx;
            assign _gen_out = {mem[_gen_idx]};
        endmodule
        """,
    )

    assert CVDP_ADAPTER_ERROR in str(error.value)


def test_cvdp_wrapper_emits_total_bundle_width_guard():
    wrapper = generate_cvdp_wrapper(
        design_name="dut",
        sparkle_mod_name="sparkle_inner",
        sparkle_ports=[("output", "logic [WIDTH:0]", "_gen_out")],
        ref_code="""
        module dut #(parameter WIDTH = 8) (
            output logic [WIDTH-1:0] data_out
        );
        endmodule
        """,
        harness_files={
            "src/test.py": """
            runner.build(parameters={"WIDTH": WIDTH})
            int(dut.data_out.value)
            """,
        },
        sv_code="""
        module sparkle_inner #(parameter WIDTH = 8) (
            output logic [WIDTH:0] _gen_out
        );
            logic [WIDTH-1:0] _gen_data_out;
            assign _gen_out = {_gen_data_out};
        endmodule
        """,
    )

    assert wrapper is not None
    assert "$bits(_gen_out_wire) != ($bits(_cvdp_shape__gen_out_0))" in wrapper
    assert "packed bundle width mismatch for _gen_out" in wrapper


def test_cvdp_wrapper_bridges_only_exact_symbolic_internal_memory_contract():
    wrapper = generate_cvdp_wrapper(
        design_name="dut",
        sparkle_mod_name="sparkle_inner",
        sparkle_ports=[("output", "logic [WIDTH-1:0]", "data_out")],
        ref_code="""
        module dut #(
            parameter WIDTH = 8,
            parameter DEPTH = 4
        ) (
            output logic [WIDTH-1:0] data_out
        );
            logic [WIDTH-1:0] fifo_array [DEPTH-1:0];
        endmodule
        """,
        harness_files={
            "src/test.py": """
            runner.build(parameters={"WIDTH": WIDTH, "DEPTH": DEPTH})
            int(dut.data_out.value)
            int(dut.fifo_array[0].value)
            """,
        },
        sv_code="""
        module sparkle_inner #(
            parameter WIDTH = 8,
            parameter DEPTH = 4
        ) (
            output logic [WIDTH-1:0] data_out
        );
            logic [WIDTH-1:0] _gen_fifo_array [DEPTH-1:0];
            assign data_out = _gen_fifo_array[0];
        endmodule
        """,
    )

    assert wrapper is not None
    assert "_cvdp_bad_memory_fifo_array" in wrapper
    assert "$size(fifo_array) != $size(sparkle_dut._gen_fifo_array)" in wrapper
    assert (
        "assign fifo_array[_cvdp_bridge_fifo_array_i] = "
        "sparkle_dut._gen_fifo_array[_cvdp_bridge_fifo_array_i];"
    ) in wrapper

def test_cvdp_strict_port_parser_is_balanced_and_all_or_nothing():
    ports = _parse_ref_module_ports_strict(
        """
        module dut(
            input logic [F(A, B)-1:0] x,
            output logic unsigned [15:0] y
        );
        endmodule
        """,
        "dut",
    )

    assert ports == [
        ("input", " [F(A, B)-1:0]", "x"),
        ("output", " unsigned [15:0]", "y"),
    ]
    assert _parse_ref_module_ports_strict(
        "module dut(input integer x, output logic y); endmodule",
        "dut",
    ) is None


def test_cvdp_exact_old_baseline_allows_new_exact_core_port():
    wrapper = generate_cvdp_wrapper(
        design_name="dut",
        sparkle_mod_name="sparkle_inner",
        sparkle_ports=[
            ("output", "logic [7:0]", "result"),
            ("output", "logic", "valid_result"),
        ],
        ref_code="module dut(output logic [7:0] result); endmodule",
        harness_files={
            "src/test.py": """
            int(dut.result.value)
            int(dut.valid_result.value)
            """,
        },
        sv_code="""
        module sparkle_inner(
            output logic [7:0] result,
            output logic valid_result
        );
            assign result = 8'h00;
            assign valid_result = 1'b1;
        endmodule
        """,
    )

    assert wrapper is not None
    assert "output logic valid_result" in wrapper
    assert "assign valid_result = valid_result_wire;" in wrapper


def test_cvdp_exact_old_gf_baseline_allows_complete_named_bundle_extension():
    core = """
    module sparkle_inner #(parameter WIDTH = 32) (
        input logic [WIDTH-1:0] _gen_a,
        input logic [WIDTH-1:0] _gen_b,
        output logic [9:0] out
    );
        logic [7:0] _gen_result;
        logic _gen_error_flag;
        logic _gen_valid_result;
        logic [1:0] _tmp_status;
        logic [9:0] _tmp_result;
        assign _gen_result = _gen_a[7:0] ^ _gen_b[7:0];
        assign _gen_error_flag = (WIDTH % 8) != 0;
        assign _gen_valid_result = !_gen_error_flag;
        assign _tmp_status = {_gen_error_flag, _gen_valid_result};
        assign _tmp_result = {_gen_result, _tmp_status};
        assign out = _tmp_result;
    endmodule
    """
    wrapper = generate_cvdp_wrapper(
        design_name="dut",
        sparkle_mod_name="sparkle_inner",
        sparkle_ports=[
            ("input", "logic [WIDTH-1:0]", "_gen_a"),
            ("input", "logic [WIDTH-1:0]", "_gen_b"),
            ("output", "logic [9:0]", "out"),
        ],
        ref_code="""
        module dut #(parameter WIDTH = 32) (
            input logic [WIDTH-1:0] a,
            input logic [WIDTH-1:0] b,
            output logic [7:0] result
        );
        endmodule
        """,
        harness_files={
            "src/test.py": """
            runner.build(parameters={"WIDTH": WIDTH})
            dut.a.value = 0
            dut.b.value = 0
            int(dut.result.value)
            int(dut.error_flag.value)
            int(dut.valid_result.value)
            """,
        },
        sv_code=core,
    )

    assert wrapper is not None
    assert "output logic error_flag" in wrapper
    assert "output logic valid_result" in wrapper
    assert "assign result = out_wire[" in wrapper
    assert "assign error_flag = out_wire[" in wrapper
    assert "assign valid_result = out_wire[" in wrapper
    assert "packed bundle width mismatch for out" in wrapper


def test_cvdp_no_exact_top_single_plain_output_mirrors_symbolic_core_type():
    wrapper = generate_cvdp_wrapper(
        design_name="dut",
        sparkle_mod_name="sparkle_inner",
        sparkle_ports=[("output", "logic [2*WIDTH-1:0]", "out")],
        ref_code="module old_helper(input logic helper_in); endmodule",
        harness_files={
            "src/test.py": """
            runner.build(parameters={"WIDTH": WIDTH})
            int(dut.data_out.value)
            """,
        },
        sv_code="""
        module sparkle_inner #(parameter WIDTH = 8) (
            output logic [2*WIDTH-1:0] out
        );
            assign out = '0;
        endmodule
        """,
    )

    assert wrapper is not None
    assert "output logic [2*_cvdp_core_WIDTH-1:0] data_out" in wrapper
    assert "output logic data_out" not in wrapper
    assert "assign data_out = out_wire;" in wrapper


def _cvdp_filo_hidden_status_wrapper() -> tuple[str, str]:
    core = """
    module sparkle_inner #(
        parameter integer DATA_WIDTH = 8,
        parameter integer FILO_DEPTH = 16
    ) (
        input logic _gen_reset,
        input logic _gen_push,
        input logic _gen_pop,
        input logic [DATA_WIDTH-1:0] _gen_data_in,
        input logic clk,
        input logic rst,
        output logic [DATA_WIDTH+1:0] out
    );
        logic [DATA_WIDTH-1:0] _gen_data_out;
        logic _gen_full;
        logic _gen_empty;
        assign _gen_data_out = _gen_data_in;
        assign _gen_full = _gen_push && (FILO_DEPTH > 0);
        assign _gen_empty = _gen_pop;
        assign out = {_gen_data_out, _gen_full, _gen_empty};
    endmodule
    """
    wrapper = generate_cvdp_wrapper(
        design_name="FILO_RTL",
        sparkle_mod_name="sparkle_inner",
        sparkle_ports=[
            ("input", "logic", "_gen_reset"),
            ("input", "logic", "_gen_push"),
            ("input", "logic", "_gen_pop"),
            ("input", "logic [DATA_WIDTH-1:0]", "_gen_data_in"),
            ("input", "logic", "clk"),
            ("input", "logic", "rst"),
            ("output", "logic [DATA_WIDTH+1:0]", "out"),
        ],
        ref_code="module old_helper(input logic helper_in); endmodule",
        harness_files={
            "src/test.py": """
            runner.build(parameters={
                "DATA_WIDTH": DATA_WIDTH,
                "FILO_DEPTH": FILO_DEPTH,
            })
            dut.clk.value = 0
            dut.reset.value = 0
            dut.push.value = 0
            dut.pop.value = 0
            dut.data_in.value = 0
            int(dut.data_out.value)
            """,
        },
        sv_code=core,
    )
    assert wrapper is not None
    return core, wrapper


def test_cvdp_filo_allows_named_unobserved_status_fields_with_full_guards():
    _, wrapper = _cvdp_filo_hidden_status_wrapper()

    assert "assign data_out = out_wire[" in wrapper
    assert "output logic full" not in wrapper
    assert "output logic empty" not in wrapper
    assert (
        "$bits(_cvdp_shape_out_0) + $bits(_cvdp_shape_out_1) + "
        "$bits(_cvdp_shape_out_2)"
    ) in wrapper
    assert (
        "$bits(sparkle_dut._gen_data_out) + "
        "$bits(sparkle_dut._gen_full) + "
        "$bits(sparkle_dut._gen_empty)"
    ) in wrapper
    for field_index in range(3):
        assert (
            f"_cvdp_bad_width_bundle_child_out_{field_index}"
            in wrapper
        )


@pytest.mark.skipif(
    shutil.which("iverilog") is None or shutil.which("vvp") is None,
    reason="Icarus Verilog is required for per-child guard regression",
)
def test_cvdp_hidden_child_width_guard_rejects_compensating_mirror_errors(
    tmp_path,
):
    core = """
    module sparkle_inner(output logic [3:0] out);
        function automatic logic unused;
            logic [1:0] _gen_hidden_before;
            logic _gen_hidden_after;
            begin
                unused = _gen_hidden_before[0] ^ _gen_hidden_after;
            end
        endfunction
        logic _gen_hidden_before;
        logic _gen_visible;
        logic [1:0] _gen_hidden_after;
        assign _gen_hidden_before = 1'b0;
        assign _gen_visible = 1'b1;
        assign _gen_hidden_after = 2'b00;
        assign out = {
            _gen_hidden_before,
            _gen_visible,
            _gen_hidden_after
        };
    endmodule
    """
    wrapper = generate_cvdp_wrapper(
        design_name="dut",
        sparkle_mod_name="sparkle_inner",
        sparkle_ports=[("output", "logic [3:0]", "out")],
        ref_code="module old_helper(input logic helper_in); endmodule",
        harness_files={"src/test.py": "int(dut.visible.value)"},
        sv_code=core,
    )

    assert wrapper is not None
    assert (
        "$bits(_cvdp_shape_out_0) != "
        "$bits(sparkle_dut._gen_hidden_before)"
    ) in wrapper
    assert (
        "$bits(_cvdp_shape_out_2) != "
        "$bits(sparkle_dut._gen_hidden_after)"
    ) in wrapper
    result = _run_iverilog_wrapper(tmp_path, core + "\n" + wrapper)
    assert result.returncode != 0
    assert "packed bundle child width mismatch for out[" in (
        result.stdout + result.stderr
    )


@pytest.mark.skipif(
    (
        shutil.which("iverilog") is None
        or shutil.which("vvp") is None
        or shutil.which("yosys") is None
    ),
    reason="Icarus and Yosys are required for FILO adapter regression",
)
def test_cvdp_filo_hidden_status_wrapper_compiles_in_icarus_and_yosys(
    tmp_path,
):
    core, wrapper = _cvdp_filo_hidden_status_wrapper()
    source = core + "\n" + wrapper

    icarus_result = _run_iverilog_wrapper(tmp_path, source, top="FILO_RTL")
    assert icarus_result.returncode == 0, (
        icarus_result.stdout + icarus_result.stderr
    )
    yosys_result = _run_yosys_wrapper(
        tmp_path,
        source,
        "filo_hidden_status",
        top="FILO_RTL",
    )
    assert yosys_result.returncode == 0, (
        yosys_result.stdout + yosys_result.stderr
    )


@pytest.mark.parametrize(
    ("declarations", "hidden_field", "extra_assignments"),
    [
        ("", "1'b0", ""),
        ("logic _tmp_hidden;", "_tmp_hidden", ""),
        ("logic _gen_hidden;", "~_gen_hidden", ""),
        ("logic _gen_hidden;", "_gen_hidden[0]", ""),
        ("", "_gen_visible", ""),
        (
            "logic _gen_hidden;",
            "_gen_hidden",
            "assign _gen_hidden = _gen_visible;",
        ),
    ],
    ids=[
        "constant-marker",
        "anonymous-temp",
        "operation",
        "select",
        "duplicate",
        "observed-alias-provenance",
    ],
)
def test_cvdp_no_exact_top_rejects_unsafe_unobserved_bundle_field(
    declarations,
    hidden_field,
    extra_assignments,
):
    error = pytest.raises(
        CVDPAdapterContractError,
        generate_cvdp_wrapper,
        design_name="dut",
        sparkle_mod_name="sparkle_inner",
        sparkle_ports=[("output", "logic [1:0]", "out")],
        ref_code="module old_helper(input logic helper_in); endmodule",
        harness_files={"src/test.py": "int(dut.visible.value)"},
        sv_code=f"""
        module sparkle_inner(output logic [1:0] out);
            logic _gen_visible;
            {declarations}
            {extra_assignments}
            assign out = {{_gen_visible, {hidden_field}}};
        endmodule
        """,
    )

    assert "could not be mapped safely" in str(error.value)


def test_cvdp_new_parameter_handle_requires_real_core_constant_not_output():
    error = pytest.raises(
        CVDPAdapterContractError,
        generate_cvdp_wrapper,
        design_name="dut",
        sparkle_mod_name="sparkle_inner",
        sparkle_ports=[
            ("input", "logic", "clk"),
            ("output", "logic [BIT_WIDTH-1:0]", "out"),
        ],
        ref_code="""
        module dut #(
            parameter DICE_MAX = 6,
            parameter BIT_WIDTH = 4
        ) (
            input logic clk,
            output logic [BIT_WIDTH-1:0] dice_value
        );
        endmodule
        """,
        harness_files={
            "src/test.py": """
            runner.build(parameters={"DICE_MAX": DICE_MAX, "NUM_DICE": NUM_DICE})
            int(dut.NUM_DICE.value)
            int(dut.dice_values.value)
            """,
        },
        sv_code="""
        module sparkle_inner #(
            parameter DICE_MAX = 6,
            parameter BIT_WIDTH = 4,
            parameter NUM_DICE = 2
        ) (
            input logic clk,
            output logic [BIT_WIDTH-1:0] out
        );
            logic [NUM_DICE-1:0] _parameter_use;
            assign _parameter_use = '0;
            assign out = DICE_MAX + _parameter_use[0];
        endmodule
        """,
    )

    detail = str(error.value)
    assert "dice_values" in detail
    assert "NUM_DICE" not in detail


def test_cvdp_new_core_localparam_handle_is_mirrored_not_made_an_output():
    wrapper = generate_cvdp_wrapper(
        design_name="dut",
        sparkle_mod_name="sparkle_inner",
        sparkle_ports=[
            ("output", "logic [COUNT_WIDTH-1:0]", "count"),
        ],
        ref_code="""
        module dut #(parameter WIDTH = 8) (
            output logic [$clog2(WIDTH+1)-1:0] count
        );
        endmodule
        """,
        harness_files={
            "src/test.py": """
            runner.build(parameters={"WIDTH": WIDTH})
            int(dut.COUNT_WIDTH.value)
            int(dut.count.value)
            """,
        },
        sv_code="""
        module sparkle_inner #(
            parameter WIDTH = 8,
            localparam integer COUNT_WIDTH = $clog2(WIDTH + 1)
        ) (
            output logic [COUNT_WIDTH-1:0] count
        );
            assign count = '0;
        endmodule
        """,
    )

    assert wrapper is not None
    assert (
        "localparam integer _cvdp_core_COUNT_WIDTH = "
        "$clog2(_cvdp_core_WIDTH + 1)" in wrapper
    )
    assert (
        "localparam integer COUNT_WIDTH = _cvdp_core_COUNT_WIDTH"
        in wrapper
    )
    assert "output logic COUNT_WIDTH" not in wrapper


def test_cvdp_unknown_uppercase_handle_is_not_invented_as_parameter_or_output():
    error = pytest.raises(
        CVDPAdapterContractError,
        generate_cvdp_wrapper,
        design_name="dut",
        sparkle_mod_name="sparkle_inner",
        sparkle_ports=[("output", "logic", "done")],
        ref_code="module dut(output logic done); endmodule",
        harness_files={"src/test.py": "int(dut.NUM_DICE.value)"},
        sv_code="""
        module sparkle_inner(output logic done);
            assign done = 1'b1;
        endmodule
        """,
    )

    assert "NUM_DICE" in str(error.value)


def test_cvdp_wrapper_rejects_harness_handle_absent_from_exact_top():
    error = pytest.raises(
        CVDPAdapterContractError,
        generate_cvdp_wrapper,
        design_name="dut",
        sparkle_mod_name="sparkle_inner",
        sparkle_ports=[("output", "logic [7:0]", "secret")],
        ref_code="module dut(output logic [3:0] visible); endmodule",
        harness_files={"src/test.py": "int(dut.hidden.value)"},
        sv_code="""
        module sparkle_inner(output logic [7:0] secret);
            assign secret = '0;
        endmodule
        """,
    )

    assert "not declared by the exact reference top" in str(error.value)


@pytest.mark.parametrize("rhs", ["~_gen_ready", "_gen_ready + 1'b1"])
def test_cvdp_wrapper_rejects_transform_as_output_provenance(rhs):
    error = pytest.raises(
        CVDPAdapterContractError,
        generate_cvdp_wrapper,
        design_name="dut",
        sparkle_mod_name="sparkle_inner",
        sparkle_ports=[("output", "logic", "_gen_out")],
        ref_code="module dut(output logic ready); endmodule",
        harness_files={"src/test.py": "int(dut.ready.value)"},
        sv_code=f"""
        module sparkle_inner(output logic _gen_out);
            logic _gen_ready;
            logic _gen_field;
            assign _gen_field = {rhs};
            assign _gen_out = {{_gen_field}};
        endmodule
        """,
    )

    assert CVDP_ADAPTER_ERROR in str(error.value)


def test_cvdp_wrapper_follows_pure_alias_to_plain_output_name():
    wrapper = generate_cvdp_wrapper(
        design_name="dut",
        sparkle_mod_name="sparkle_inner",
        sparkle_ports=[("output", "logic", "_gen_out")],
        ref_code="module dut(output logic ready); endmodule",
        harness_files={"src/test.py": "int(dut.ready.value)"},
        sv_code="""
        module sparkle_inner(output logic _gen_out);
            logic ready;
            logic _gen_field;
            assign _gen_field = ready;
            assign _gen_out = {_gen_field};
        endmodule
        """,
    )

    assert wrapper is not None
    assert "$bits(_cvdp_shape__gen_out_0)" in wrapper


@pytest.mark.parametrize(
    ("core_name", "reference_names", "detail"),
    [
        (
            "clk",
            ("clk_a", "clk_b"),
            "ambiguous structured harness Clock evidence",
        ),
        (
            "rst",
            ("reset_req", "reset_state"),
            "are not consumed exactly once",
        ),
    ],
)
def test_cvdp_wrapper_rejects_ambiguous_clock_reset_aliases(
    core_name,
    reference_names,
    detail,
):
    ref_ports = ", ".join(
        [*(f"input logic {name}" for name in reference_names), "output logic y"]
    )
    clock_lines = []
    if core_name == "clk":
        clock_lines = [
            "from cocotb.clock import Clock",
            *(f"Clock(dut.{name}, 10, unit='ns')" for name in reference_names),
        ]
    harness = "\n".join([
        *clock_lines,
        *(f"dut.{name}.value = 0" for name in reference_names),
        "int(dut.y.value)",
    ])
    error = pytest.raises(
        CVDPAdapterContractError,
        generate_cvdp_wrapper,
        design_name="dut",
        sparkle_mod_name="sparkle_inner",
        sparkle_ports=[
            ("input", "logic", core_name),
            ("output", "logic", "y"),
        ],
        ref_code=f"module dut({ref_ports}); endmodule",
        harness_files={"src/test.py": harness},
        sv_code=f"""
        module sparkle_inner(input logic {core_name}, output logic y);
            assign y = {core_name};
        endmodule
        """,
    )

    assert detail in str(error.value)


def test_cvdp_wrapper_keeps_safe_same_name_single_concat_output_direct():
    wrapper = generate_cvdp_wrapper(
        design_name="dut",
        sparkle_mod_name="sparkle_inner",
        sparkle_ports=[("output", "logic [1:0]", "_gen_data_out")],
        ref_code="module dut(output logic [1:0] data_out); endmodule",
        harness_files={"src/test.py": "int(dut.data_out.value)"},
        sv_code="""
        module sparkle_inner(output logic [1:0] _gen_data_out);
            logic _gen_high;
            logic _gen_low;
            assign _gen_data_out = {_gen_high, _gen_low};
        endmodule
        """,
    )

    assert wrapper is not None
    assert "assign data_out = _gen_data_out_wire;" in wrapper
    assert "_cvdp_bad_width_output_data_out" in wrapper

def _run_iverilog_wrapper(
    tmp_path: Path,
    source: str,
    *,
    top: str = "dut",
) -> subprocess.CompletedProcess:
    source_path = tmp_path / "adapter_probe.sv"
    output_path = tmp_path / "adapter_probe.vvp"
    source_path.write_text(source)
    compile_result = subprocess.run(
        [
            "iverilog",
            "-g2012",
            "-s",
            top,
            "-o",
            str(output_path),
            str(source_path),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert compile_result.returncode == 0, compile_result.stderr
    return subprocess.run(
        ["vvp", str(output_path)],
        text=True,
        capture_output=True,
        check=False,
    )


@pytest.mark.skipif(
    shutil.which("iverilog") is None or shutil.which("vvp") is None,
    reason="Icarus Verilog is required for clock-bridge regression",
)
def test_cvdp_axi_clock_bridge_fans_out_and_clocks_register(tmp_path):
    core = """
    module sparkle_inner (
        input logic _gen_axi_aclk,
        input logic _gen_axi_aresetn,
        input logic _gen_d,
        input logic clk,
        input logic rst,
        output logic q
    );
        always_ff @(posedge clk or posedge rst) begin
            if (rst)
                q <= 1'b0;
            else if (!_gen_axi_aresetn)
                q <= 1'b0;
            else
                q <= _gen_d;
        end
    endmodule
    """
    wrapper = generate_cvdp_wrapper(
        design_name="dut",
        sparkle_mod_name="sparkle_inner",
        sparkle_ports=[
            ("input", "logic", "_gen_axi_aclk"),
            ("input", "logic", "_gen_axi_aresetn"),
            ("input", "logic", "_gen_d"),
            ("input", "logic", "clk"),
            ("input", "logic", "rst"),
            ("output", "logic", "q"),
        ],
        ref_code="""
        module dut (
            input logic axi_aclk,
            input logic axi_aresetn,
            input logic d,
            output logic q
        );
        endmodule
        """,
        harness_files={
            "src/test.py": """
            from cocotb.clock import Clock as CClock
            CClock(dut.axi_aclk, 10, unit="ns")
            dut.axi_aclk.value = 0
            dut.axi_aresetn.value = 0
            dut.d.value = 0
            int(dut.q.value)
            """,
        },
        sv_code=core,
    )

    assert wrapper is not None
    assert (
        "assign _cvdp_input__gen_axi_aclk_wire = axi_aclk;"
        in wrapper
    )
    assert "assign _cvdp_input_clk_wire = axi_aclk;" in wrapper
    assert (
        "assign _cvdp_input__gen_axi_aresetn_wire = axi_aresetn;"
        in wrapper
    )
    assert "assign _cvdp_input_rst_wire = 1'b0;" in wrapper
    testbench = """
    module clock_bridge_tb;
        logic axi_aclk = 1'b0;
        logic axi_aresetn = 1'b0;
        logic d = 1'b0;
        logic q;

        dut dut_i (
            .axi_aclk(axi_aclk),
            .axi_aresetn(axi_aresetn),
            .d(d),
            .q(q)
        );

        always #5 axi_aclk = ~axi_aclk;

        initial begin
            @(posedge axi_aclk);
            #1;
            if (q !== 1'b0)
                $fatal(1, "clock bridge did not initialize register");
            axi_aresetn = 1'b1;
            d = 1'b1;
            @(posedge axi_aclk);
            #1;
            if (q !== 1'b1)
                $fatal(1, "clock bridge left register X or stale");
            $display("CVDP_CLOCK_BRIDGE_PASS");
            $finish;
        end
    endmodule
    """
    result = _run_iverilog_wrapper(
        tmp_path,
        core + "\n" + wrapper + "\n" + testbench,
        top="clock_bridge_tb",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "CVDP_CLOCK_BRIDGE_PASS" in result.stdout



@pytest.mark.skipif(
    shutil.which("iverilog") is None or shutil.which("vvp") is None,
    reason="Icarus Verilog is required for structured-clock regression",
)
def test_cvdp_structured_clock_c_beats_clk_en_data_and_clocks_register(
    tmp_path,
):
    core = """
    module sparkle_inner (
        input logic _gen_c,
        input logic _gen_clk_en,
        input logic _gen_d,
        input logic clk,
        input logic rst,
        output logic q
    );
        always_ff @(posedge clk)
            q <= _gen_d;
    endmodule
    """
    wrapper = generate_cvdp_wrapper(
        design_name="dut",
        sparkle_mod_name="sparkle_inner",
        sparkle_ports=[
            ("input", "logic", "_gen_c"),
            ("input", "logic", "_gen_clk_en"),
            ("input", "logic", "_gen_d"),
            ("input", "logic", "clk"),
            ("input", "logic", "rst"),
            ("output", "logic", "q"),
        ],
        ref_code="""
        module dut (
            input logic c,
            input logic clk_en,
            input logic d,
            output logic q
        );
        endmodule
        """,
        harness_files={
            "src/test.py": """
            import cocotb
            from cocotb.clock import Clock as CClock
            fake_call = "Clock(dut.clk_en, 1, unit='ns')"
            # Clock(dut.clk_en, 1, unit='ns')
            cocotb.start_soon(CClock(dut.c, 10, unit='ns').start())
            dut.clk_en.value = 0
            dut.d.value = 1
            int(dut.q.value)
            """,
        },
        sv_code=core,
    )

    assert wrapper is not None
    assert "assign _cvdp_input__gen_c_wire = c;" in wrapper
    assert "assign _cvdp_input__gen_clk_en_wire = clk_en;" in wrapper
    assert "assign _cvdp_input_clk_wire = c;" in wrapper
    assert "assign _cvdp_input_clk_wire = clk_en;" not in wrapper
    testbench = """
    module structured_clock_tb;
        logic c = 1'b0;
        logic clk_en = 1'b0;
        logic d = 1'b1;
        logic q;

        dut dut_i (.c(c), .clk_en(clk_en), .d(d), .q(q));
        always #5 c = ~c;

        initial begin
            @(posedge c);
            #1;
            if (q !== 1'b1)
                $fatal(1, "structured clock did not clock register");
            $display("CVDP_STRUCTURED_CLOCK_PASS");
            $finish;
        end
    endmodule
    """
    result = _run_iverilog_wrapper(
        tmp_path,
        core + "\n" + wrapper + "\n" + testbench,
        top="structured_clock_tb",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "CVDP_STRUCTURED_CLOCK_PASS" in result.stdout


@pytest.mark.parametrize(
    "clock_harness",
    [
        """
        from cocotb.clock import Clock
        Clock(dut.c, 10, unit="ns")
        """,
        """
        from cocotb.clock import Clock as DriveClock
        DriveClock(dut._id("c", extended=False), 10, unit="ns")
        """,
        """
        import cocotb.clock as clock_module
        clock_module.Clock(dut["c"], 10, unit="ns")
        """,
        """
        import cocotb as cb
        cb.clock.Clock(dut.c, 10, unit="ns")
        """,
    ],
)
def test_cvdp_structured_clock_accepts_known_import_and_handle_forms(
    clock_harness,
):
    wrapper = generate_cvdp_wrapper(
        design_name="dut",
        sparkle_mod_name="sparkle_inner",
        sparkle_ports=[
            ("input", "logic", "_gen_c"),
            ("input", "logic", "clk"),
            ("input", "logic", "rst"),
            ("output", "logic", "q"),
        ],
        ref_code="module dut(input logic c, output logic q); endmodule",
        harness_files={
            "src/test.py": clock_harness + "\n        int(dut.q.value)",
        },
        sv_code="""
        module sparkle_inner (
            input logic _gen_c,
            input logic clk,
            input logic rst,
            output logic q
        );
            assign q = _gen_c;
        endmodule
        """,
    )

    assert wrapper is not None
    assert "assign _cvdp_input_clk_wire = c;" in wrapper


def test_cvdp_structured_clock_ignores_comments_and_strings():
    error = pytest.raises(
        CVDPAdapterContractError,
        generate_cvdp_wrapper,
        design_name="dut",
        sparkle_mod_name="sparkle_inner",
        sparkle_ports=[
            ("input", "logic", "_gen_c"),
            ("input", "logic", "_gen_clk_en"),
            ("input", "logic", "clk"),
            ("input", "logic", "rst"),
            ("output", "logic", "q"),
        ],
        ref_code="""
        module dut (
            input logic c,
            input logic clk_en,
            output logic q
        );
        endmodule
        """,
        harness_files={
            "src/test.py": """
            from cocotb.clock import Clock
            fake_call = "Clock(dut.c, 10, unit='ns')"
            # Clock(dut.clk_en, 10, unit='ns')
            dut.c.value = 0
            dut.clk_en.value = 0
            int(dut.q.value)
            """,
        },
        sv_code="""
        module sparkle_inner (
            input logic _gen_c,
            input logic _gen_clk_en,
            input logic clk,
            input logic rst,
            output logic q
        );
            assign q = _gen_c & _gen_clk_en;
        endmodule
        """,
    )

    assert "no structured harness Clock" in str(error.value)


def test_cvdp_structured_clock_rejects_dynamic_dut_target():
    error = pytest.raises(
        CVDPAdapterContractError,
        generate_cvdp_wrapper,
        design_name="dut",
        sparkle_mod_name="sparkle_inner",
        sparkle_ports=[
            ("input", "logic", "_gen_c"),
            ("input", "logic", "clk"),
            ("input", "logic", "rst"),
            ("output", "logic", "q"),
        ],
        ref_code="module dut(input logic c, output logic q); endmodule",
        harness_files={
            "src/test.py": """
            from cocotb.clock import Clock
            Clock(getattr(dut, "c"), 10, unit="ns")
            dut.c.value = 0
            int(dut.q.value)
            """,
        },
        sv_code="""
        module sparkle_inner (
            input logic _gen_c,
            input logic clk,
            input logic rst,
            output logic q
        );
            assign q = _gen_c;
        endmodule
        """,
    )

    assert "unresolved structured harness Clock evidence" in str(error.value)


def test_cvdp_structured_clock_rejects_unknown_clock_import():
    error = pytest.raises(
        CVDPAdapterContractError,
        generate_cvdp_wrapper,
        design_name="dut",
        sparkle_mod_name="sparkle_inner",
        sparkle_ports=[
            ("input", "logic", "_gen_c"),
            ("input", "logic", "clk"),
            ("input", "logic", "rst"),
            ("output", "logic", "q"),
        ],
        ref_code="module dut(input logic c, output logic q); endmodule",
        harness_files={
            "src/test.py": """
            from unrelated.clock import Clock
            Clock(dut.c, 10, unit="ns")
            dut.c.value = 0
            int(dut.q.value)
            """,
        },
        sv_code="""
        module sparkle_inner (
            input logic _gen_c,
            input logic clk,
            input logic rst,
            output logic q
        );
            assign q = _gen_c;
        endmodule
        """,
    )

    assert "unresolved structured harness Clock evidence" in str(error.value)


def test_cvdp_structured_clock_must_name_expected_input():
    error = pytest.raises(
        CVDPAdapterContractError,
        generate_cvdp_wrapper,
        design_name="dut",
        sparkle_mod_name="sparkle_inner",
        sparkle_ports=[
            ("input", "logic", "_gen_c"),
            ("input", "logic", "clk"),
            ("input", "logic", "rst"),
            ("output", "logic", "q"),
        ],
        ref_code="module dut(input logic c, output logic q); endmodule",
        harness_files={
            "src/test.py": """
            from cocotb.clock import Clock
            Clock(dut.q, 10, unit="ns")
            dut.c.value = 0
            int(dut.q.value)
            """,
        },
        sv_code="""
        module sparkle_inner (
            input logic _gen_c,
            input logic clk,
            input logic rst,
            output logic q
        );
            assign q = _gen_c;
        endmodule
        """,
    )

    assert "structured harness clock 'q' is not a benchmark input" in str(
        error.value
    )


def test_cvdp_manual_filo_clock_gen_uses_exact_clk_contract():
    wrapper = generate_cvdp_wrapper(
        design_name="dut",
        sparkle_mod_name="sparkle_inner",
        sparkle_ports=[
            ("input", "logic", "_gen_clk"),
            ("input", "logic", "_gen_d"),
            ("input", "logic", "clk"),
            ("input", "logic", "rst"),
            ("output", "logic", "q"),
        ],
        ref_code="""
        module dut (
            input logic clk,
            input logic d,
            output logic q
        );
        endmodule
        """,
        harness_files={
            "src/test_filo.py": """
            import cocotb
            from cocotb.triggers import Timer

            async def clock_gen(dut):
                while True:
                    dut.clk.value = 0
                    await Timer(5, unit="ns")
                    dut.clk.value = 1
                    await Timer(5, unit="ns")

            @cocotb.test()
            async def test_filo(dut):
                cocotb.start_soon(clock_gen(dut))
                dut.d.value = 1
                int(dut.q.value)
            """,
        },
        sv_code="""
        module sparkle_inner (
            input logic _gen_clk,
            input logic _gen_d,
            input logic clk,
            input logic rst,
            output logic q
        );
            always_ff @(posedge clk)
                q <= _gen_d;
        endmodule
        """,
    )

    assert wrapper is not None
    assert "assign _cvdp_input__gen_clk_wire = clk;" in wrapper
    assert "assign _cvdp_input_clk_wire = clk;" in wrapper


def test_cvdp_manual_toggling_clk_en_is_not_a_clock_contract():
    error = pytest.raises(
        CVDPAdapterContractError,
        generate_cvdp_wrapper,
        design_name="dut",
        sparkle_mod_name="sparkle_inner",
        sparkle_ports=[
            ("input", "logic", "_gen_clk_en"),
            ("input", "logic", "clk"),
            ("input", "logic", "rst"),
            ("output", "logic", "q"),
        ],
        ref_code="""
        module dut (
            input logic clk_en,
            output logic q
        );
        endmodule
        """,
        harness_files={
            "src/test.py": """
            from cocotb.triggers import Timer

            async def clock_gen(dut):
                while True:
                    dut.clk_en.value = 0
                    await Timer(5, unit="ns")
                    dut.clk_en.value = 1
                    await Timer(5, unit="ns")

            int(dut.q.value)
            """,
        },
        sv_code="""
        module sparkle_inner (
            input logic _gen_clk_en,
            input logic clk,
            input logic rst,
            output logic q
        );
            assign q = _gen_clk_en;
        endmodule
        """,
    )

    detail = str(error.value)
    assert "no structured harness Clock" in detail
    assert "no unique exact-name clock contract" in detail


def test_cvdp_sync_clock_bridge_keeps_explicit_reset_bijective():
    wrapper = generate_cvdp_wrapper(
        design_name="dut",
        sparkle_mod_name="sparkle_inner",
        sparkle_ports=[
            ("input", "logic", "_gen_clock"),
            ("input", "logic", "_gen_reset"),
            ("input", "logic", "_gen_d"),
            ("input", "logic", "clk"),
            ("input", "logic", "rst"),
            ("output", "logic", "q"),
        ],
        ref_code="""
        module dut (
            input logic clock,
            input logic reset,
            input logic d,
            output logic q
        );
        endmodule
        """,
        harness_files={
            "src/test.py": """
            import cocotb.clock as clock_module
            clock_module.Clock(dut.clock, 10, unit="ns")
            dut.clock.value = 0
            dut.reset.value = 0
            dut.d.value = 0
            int(dut.q.value)
            """,
        },
        sv_code="""
        module sparkle_inner (
            input logic _gen_clock,
            input logic _gen_reset,
            input logic _gen_d,
            input logic clk,
            input logic rst,
            output logic q
        );
            always_ff @(posedge clk) begin
                if (_gen_reset)
                    q <= 1'b0;
                else
                    q <= _gen_d;
            end
        endmodule
        """,
    )

    assert wrapper is not None
    assert "assign _cvdp_input__gen_clock_wire = clock;" in wrapper
    assert "assign _cvdp_input_clk_wire = clock;" in wrapper
    assert "assign _cvdp_input__gen_reset_wire = reset;" in wrapper
    assert "assign _cvdp_input_rst_wire = 1'b0;" in wrapper


def test_cvdp_clock_bridge_rejects_ambiguity_after_ordinary_consumption():
    error = pytest.raises(
        CVDPAdapterContractError,
        generate_cvdp_wrapper,
        design_name="dut",
        sparkle_mod_name="sparkle_inner",
        sparkle_ports=[
            ("input", "logic", "_gen_clk"),
            ("input", "logic", "_gen_scan_clk"),
            ("input", "logic", "_gen_d"),
            ("input", "logic", "clk"),
            ("input", "logic", "rst"),
            ("output", "logic", "q"),
        ],
        ref_code="""
        module dut (
            input logic clk,
            input logic scan_clk,
            input logic d,
            output logic q
        );
        endmodule
        """,
        harness_files={
            "src/test.py": """
            from cocotb.clock import Clock
            Clock(dut.clk, 10, unit="ns")
            Clock(dut.scan_clk, 10, unit="ns")
            dut.clk.value = 0
            dut.scan_clk.value = 0
            dut.d.value = 0
            int(dut.q.value)
            """,
        },
        sv_code="""
        module sparkle_inner (
            input logic _gen_clk,
            input logic _gen_scan_clk,
            input logic _gen_d,
            input logic clk,
            input logic rst,
            output logic q
        );
            assign q = _gen_d;
        endmodule
        """,
    )

    assert "ambiguous structured harness Clock evidence" in str(error.value)


def test_cvdp_generated_clk_is_not_implicit_abi_or_tied_off():
    error = pytest.raises(
        CVDPAdapterContractError,
        generate_cvdp_wrapper,
        design_name="dut",
        sparkle_mod_name="sparkle_inner",
        sparkle_ports=[
            ("input", "logic", "_gen_d"),
            ("input", "logic", "_gen_clk"),
            ("output", "logic", "q"),
        ],
        ref_code="module dut(input logic d, output logic q); endmodule",
        harness_files={
            "src/test.py": "dut.d.value = 0\nint(dut.q.value)",
        },
        sv_code="""
        module sparkle_inner (
            input logic _gen_d,
            input logic _gen_clk,
            output logic q
        );
            assign q = _gen_d;
        endmodule
        """,
    )

    assert "Sparkle input '_gen_clk'" in str(error.value)
    assert "unique benchmark input mapping" in str(error.value)


def test_cvdp_two_generated_clock_consumers_cannot_share_public_clock():
    error = pytest.raises(
        CVDPAdapterContractError,
        generate_cvdp_wrapper,
        design_name="dut",
        sparkle_mod_name="sparkle_inner",
        sparkle_ports=[
            ("input", "logic", "_gen_clock"),
            ("input", "logic", "_gen_clk"),
            ("input", "logic", "_gen_d"),
            ("input", "logic", "clk"),
            ("input", "logic", "rst"),
            ("output", "logic", "q"),
        ],
        ref_code="""
        module dut (
            input logic clock,
            input logic d,
            output logic q
        );
        endmodule
        """,
        harness_files={
            "src/test.py": """
            dut.clock.value = 0
            dut.d.value = 0
            int(dut.q.value)
            """,
        },
        sv_code="""
        module sparkle_inner (
            input logic _gen_clock,
            input logic _gen_clk,
            input logic _gen_d,
            input logic clk,
            input logic rst,
            output logic q
        );
            assign q = _gen_d;
        endmodule
        """,
    )

    assert "Sparkle input '_gen_clk'" in str(error.value)
    assert "unique benchmark input mapping" in str(error.value)


def _run_yosys_wrapper(
    tmp_path: Path,
    source: str,
    stem: str,
    *,
    top: str = "dut",
) -> subprocess.CompletedProcess:
    source_path = tmp_path / f"{stem}.sv"
    source_path.write_text(source)
    return subprocess.run(
        [
            "yosys",
            "-q",
            "-p",
            (
                f"read_verilog -sv {source_path}; "
                f"hierarchy -check -top {top}; prep -top {top}"
            ),
        ],
        text=True,
        capture_output=True,
        check=False,
    )


@pytest.mark.skipif(
    (
        shutil.which("iverilog") is None
        or shutil.which("vvp") is None
        or shutil.which("yosys") is None
    ),
    reason="Icarus and Yosys are required for current adapter fixtures",
)
def test_cvdp_current_scalar_and_gf_extensions_compile_in_icarus_and_yosys(
    tmp_path,
):
    scalar_core = """
    module sparkle_inner #(parameter DATA_WIDTH = 64) (
        input logic [DATA_WIDTH-1:0] _gen_data_in,
        output logic [DATA_WIDTH-1:0] out
    );
        assign out = _gen_data_in;
    endmodule
    """
    scalar_wrapper = generate_cvdp_wrapper(
        design_name="dut",
        sparkle_mod_name="sparkle_inner",
        sparkle_ports=[
            ("input", "logic [DATA_WIDTH-1:0]", "_gen_data_in"),
            ("output", "logic [DATA_WIDTH-1:0]", "out"),
        ],
        ref_code="module old_baseline_helper(input logic helper); endmodule",
        harness_files={
            "src/test.py": """
            runner.build(parameters={"DATA_WIDTH": DATA_WIDTH})
            dut.data_in.value = 0
            int(dut.data_out.value)
            """,
        },
        sv_code=scalar_core,
    )
    assert scalar_wrapper is not None
    scalar_source = scalar_core + "\n" + scalar_wrapper

    gf_core = """
    module sparkle_gf #(parameter WIDTH = 32) (
        input logic [WIDTH-1:0] _gen_a,
        input logic [WIDTH-1:0] _gen_b,
        output logic [9:0] out
    );
        logic [7:0] _gen_result;
        logic _gen_error_flag;
        logic _gen_valid_result;
        logic [1:0] _tmp_status;
        logic [9:0] _tmp_result;
        assign _gen_result = _gen_a[7:0] ^ _gen_b[7:0];
        assign _gen_error_flag = (WIDTH % 8) != 0;
        assign _gen_valid_result = !_gen_error_flag;
        assign _tmp_status = {_gen_error_flag, _gen_valid_result};
        assign _tmp_result = {_gen_result, _tmp_status};
        assign out = _tmp_result;
    endmodule
    """
    gf_wrapper = generate_cvdp_wrapper(
        design_name="dut",
        sparkle_mod_name="sparkle_gf",
        sparkle_ports=[
            ("input", "logic [WIDTH-1:0]", "_gen_a"),
            ("input", "logic [WIDTH-1:0]", "_gen_b"),
            ("output", "logic [9:0]", "out"),
        ],
        ref_code="""
        module dut #(parameter WIDTH = 32) (
            input logic [WIDTH-1:0] a,
            input logic [WIDTH-1:0] b,
            output logic [7:0] result
        );
        endmodule
        """,
        harness_files={
            "src/test.py": """
            runner.build(parameters={"WIDTH": WIDTH})
            dut.a.value = 0
            dut.b.value = 0
            int(dut.result.value)
            int(dut.error_flag.value)
            int(dut.valid_result.value)
            """,
        },
        sv_code=gf_core,
    )
    assert gf_wrapper is not None
    gf_source = gf_core + "\n" + gf_wrapper

    for stem, source in (
        ("scalar_adapter", scalar_source),
        ("gf_adapter", gf_source),
    ):
        icarus_result = _run_iverilog_wrapper(tmp_path, source)
        assert icarus_result.returncode == 0, (
            icarus_result.stdout + icarus_result.stderr
        )
        yosys_result = _run_yosys_wrapper(tmp_path, source, stem)
        assert yosys_result.returncode == 0, (
            yosys_result.stdout + yosys_result.stderr
        )



@pytest.mark.skipif(
    shutil.which("iverilog") is None or shutil.which("vvp") is None,
    reason="Icarus Verilog is required for adapter guard regression",
)
def test_cvdp_runtime_width_guard_fails_symbolic_direct_mismatch(tmp_path):
    core = """
    module sparkle_inner #(parameter WIDTH = 8) (
        output logic [2*WIDTH-1:0] data_out
    );
        assign data_out = '0;
    endmodule
    """
    wrapper = generate_cvdp_wrapper(
        design_name="dut",
        sparkle_mod_name="sparkle_inner",
        sparkle_ports=[("output", "logic [2*WIDTH-1:0]", "data_out")],
        ref_code="""
        module dut #(parameter WIDTH = 8) (
            output logic [WIDTH-1:0] data_out
        );
        endmodule
        """,
        harness_files={
            "src/test.py": """
            runner.build(parameters={"WIDTH": WIDTH})
            int(dut.data_out.value)
            """,
        },
        sv_code=core,
    )

    assert wrapper is not None
    result = _run_iverilog_wrapper(tmp_path, core + "\n" + wrapper)
    assert result.returncode != 0
    assert "direct output width mismatch for data_out" in (
        result.stdout + result.stderr
    )



@pytest.mark.skipif(
    shutil.which("iverilog") is None or shutil.which("vvp") is None,
    reason="Icarus Verilog is required for core localparam mirror regression",
)
def test_cvdp_core_body_localparam_chain_is_mirrored_and_compiles(tmp_path):
    core = """
    module sparkle_inner #(parameter integer W = 8) (
        output logic [W+1:0] _gen_out
    );
        localparam integer FW = W + 1;
        localparam integer FW2 = FW + 1;
        logic [FW2-1:0] _gen_data;
        assign _gen_data = '0;
        assign _gen_out = {_gen_data};
    endmodule
    """
    wrapper = generate_cvdp_wrapper(
        design_name="dut",
        sparkle_mod_name="sparkle_inner",
        sparkle_ports=[("output", "logic [W+1:0]", "_gen_out")],
        ref_code="""
        module dut #(
            parameter integer W = 8,
            localparam integer FW = W
        ) (
            output logic [W+1:0] data
        );
        endmodule
        """,
        harness_files={
            "src/test.py": """
            runner.build(parameters={"W": W})
            int(dut.data.value)
            """
        },
        sv_code=core,
    )

    assert wrapper is not None
    assert "localparam integer _cvdp_core_W = W" in wrapper
    assert (
        "localparam integer _cvdp_core_FW = _cvdp_core_W + 1"
        in wrapper
    )
    assert (
        "localparam integer _cvdp_core_FW2 = _cvdp_core_FW + 1"
        in wrapper
    )
    assert "logic [_cvdp_core_FW2-1:0] _cvdp_shape__gen_out_0;" in wrapper
    assert "logic [FW2-1:0] _cvdp_shape__gen_out_0;" not in wrapper
    result = _run_iverilog_wrapper(tmp_path, core + "\n" + wrapper)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(
    shutil.which("iverilog") is None or shutil.which("vvp") is None,
    reason="Icarus Verilog is required for header localparam shadow regression",
)
def test_cvdp_core_header_localparam_shadow_fails_real_width_guard(tmp_path):
    core = """
    module sparkle_inner #(
        parameter integer W = 8,
        localparam integer CW = 2 * W
    ) (
        input logic [CW-1:0] data_in,
        output logic [CW-1:0] data_out
    );
        assign data_out = data_in;
    endmodule
    """
    wrapper = generate_cvdp_wrapper(
        design_name="dut",
        sparkle_mod_name="sparkle_inner",
        sparkle_ports=[
            ("input", "logic [CW-1:0]", "data_in"),
            ("output", "logic [CW-1:0]", "data_out"),
        ],
        ref_code="""
        module dut #(
            parameter integer W = 8,
            localparam integer CW = W
        ) (
            input logic [CW-1:0] data_in,
            output logic [CW-1:0] data_out
        );
        endmodule
        """,
        harness_files={
            "src/test.py": """
            runner.build(parameters={"W": W})
            dut.data_in.value = 0
            int(dut.data_out.value)
            """
        },
        sv_code=core,
    )

    assert wrapper is not None
    assert (
        "localparam integer _cvdp_core_CW = 2 * _cvdp_core_W"
        in wrapper
    )
    assert "logic [_cvdp_core_CW-1:0] data_out_wire;" in wrapper
    assert "logic [CW-1:0] data_out_wire;" not in wrapper
    result = _run_iverilog_wrapper(tmp_path, core + "\n" + wrapper)
    assert result.returncode != 0
    assert "direct input width mismatch for data_in" in (
        result.stdout + result.stderr
    ) or "direct output width mismatch for data_out" in (
        result.stdout + result.stderr
    )


@pytest.mark.skipif(
    shutil.which("iverilog") is None or shutil.which("vvp") is None,
    reason="Icarus Verilog is required for parameter-default mirror regression",
)
def test_cvdp_nonforwarded_core_parameter_default_is_mirrored(tmp_path):
    core = """
    module sparkle_inner #(
        parameter integer W = 8,
        parameter integer EXTRA = W + 1
    ) (
        output logic [EXTRA-1:0] data_out
    );
        assign data_out = '0;
    endmodule
    """
    wrapper = generate_cvdp_wrapper(
        design_name="dut",
        sparkle_mod_name="sparkle_inner",
        sparkle_ports=[("output", "logic [EXTRA-1:0]", "data_out")],
        ref_code="""
        module dut #(parameter integer W = 8) (
            output logic [W:0] data_out
        );
        endmodule
        """,
        harness_files={
            "src/test.py": """
            runner.build(parameters={"W": W})
            int(dut.data_out.value)
            """
        },
        sv_code=core,
    )

    assert wrapper is not None
    assert (
        "localparam integer _cvdp_core_EXTRA = _cvdp_core_W + 1"
        in wrapper
    )
    assert ".EXTRA(" not in wrapper
    result = _run_iverilog_wrapper(tmp_path, core + "\n" + wrapper)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(
    shutil.which("yosys") is None,
    reason="Yosys is required for core localparam mirror regression",
)
def test_cvdp_core_body_localparam_mirror_is_yosys_compatible(tmp_path):
    core = """
    module sparkle_inner #(parameter integer W = 8) (
        output logic [W:0] _gen_out
    );
        localparam integer FW = W + 1;
        logic [FW-1:0] _gen_data_out;
        assign _gen_data_out = '0;
        assign _gen_out = {_gen_data_out};
    endmodule
    """
    wrapper = generate_cvdp_wrapper(
        design_name="dut",
        sparkle_mod_name="sparkle_inner",
        sparkle_ports=[("output", "logic [W:0]", "_gen_out")],
        ref_code="""
        module dut #(parameter integer W = 8) (
            output logic [W:0] data_out
        );
        endmodule
        """,
        harness_files={
            "src/test.py": """
            runner.build(parameters={"W": W})
            int(dut.data_out.value)
            """
        },
        sv_code=core,
    )
    assert wrapper is not None
    assert (
        "localparam integer _cvdp_core_FW = _cvdp_core_W + 1"
        in wrapper
    )
    assert "logic [_cvdp_core_FW-1:0] _cvdp_shape__gen_out_0;" in wrapper
    source_path = tmp_path / "adapter_yosys_probe.sv"
    source_path.write_text(core + "\n" + wrapper)
    result = subprocess.run(
        [
            "yosys",
            "-q",
            "-p",
            (
                f"read_verilog -sv {source_path}; "
                "hierarchy -check -top dut; prep -top dut"
            ),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(
    (
        shutil.which("iverilog") is None
        or shutil.which("vvp") is None
        or shutil.which("yosys") is None
    ),
    reason="Icarus and Yosys are required for inferred ANSI mirror regression",
)
def test_cvdp_helper_only_inferred_bundle_type_uses_header_mirror(tmp_path):
    core = """
    module sparkle_inner #(parameter integer W = 8) (
        output logic [W:0] _gen_out
    );
        localparam integer FW = W + 1;
        logic [FW-1:0] _gen_data;
        assign _gen_data = '0;
        assign _gen_out = {_gen_data};
    endmodule
    """
    wrapper = generate_cvdp_wrapper(
        design_name="dut",
        sparkle_mod_name="sparkle_inner",
        sparkle_ports=[("output", "logic [W:0]", "_gen_out")],
        ref_code="""
        module unrelated_helper(input logic helper_in);
        endmodule
        """,
        harness_files={
            "src/test.py": """
            runner.build(parameters={"W": W})
            int(dut.data.value)
            """
        },
        sv_code=core,
    )

    assert wrapper is not None
    mirror = "localparam integer _cvdp_core_FW = _cvdp_core_W + 1"
    inferred_port = "output logic [_cvdp_core_FW-1:0] data"
    assert mirror in wrapper
    assert inferred_port in wrapper
    assert "output logic [FW-1:0] data" not in wrapper
    assert wrapper.index(mirror) < wrapper.index(inferred_port)

    icarus_result = _run_iverilog_wrapper(
        tmp_path, core + "\n" + wrapper
    )
    assert icarus_result.returncode == 0, (
        icarus_result.stdout + icarus_result.stderr
    )

    source_path = tmp_path / "adapter_inferred_yosys_probe.sv"
    source_path.write_text(core + "\n" + wrapper)
    yosys_result = subprocess.run(
        [
            "yosys",
            "-q",
            "-p",
            (
                f"read_verilog -sv {source_path}; "
                "hierarchy -check -top dut; prep -top dut"
            ),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert yosys_result.returncode == 0, (
        yosys_result.stdout + yosys_result.stderr
    )


def test_cvdp_core_type_mirror_rejects_user_function_dependency():
    error = pytest.raises(
        CVDPAdapterContractError,
        generate_cvdp_wrapper,
        design_name="dut",
        sparkle_mod_name="sparkle_inner",
        sparkle_ports=[("output", "logic [FW-1:0]", "data_out")],
        ref_code="""
        module dut #(parameter W = 8) (
            output logic [W-1:0] data_out
        );
        endmodule
        """,
        harness_files={
            "src/test.py": """
            runner.build(parameters={"W": W})
            int(dut.data_out.value)
            """
        },
        sv_code="""
        module sparkle_inner #(parameter W = 8) (
            output logic [FW-1:0] data_out
        );
            localparam integer FW = user_width(W);
            assign data_out = '0;
        endmodule
        """,
    )
    assert "unsupported identifier(s) user_width" in str(error.value)


@pytest.mark.skipif(
    shutil.which("iverilog") is None or shutil.which("vvp") is None,
    reason="Icarus Verilog is required for adapter memory regression",
)
def test_cvdp_runtime_memory_guard_compiles_and_passes_equal_contract(tmp_path):
    core = """
    module sparkle_inner #(
        parameter WIDTH = 8,
        parameter DEPTH = 4
    ) (
        output logic [WIDTH-1:0] data_out
    );
        logic [WIDTH-1:0] _gen_fifo_array [DEPTH-1:0];
        assign data_out = _gen_fifo_array[0];
    endmodule
    """
    wrapper = generate_cvdp_wrapper(
        design_name="dut",
        sparkle_mod_name="sparkle_inner",
        sparkle_ports=[("output", "logic [WIDTH-1:0]", "data_out")],
        ref_code="""
        module dut #(
            parameter WIDTH = 8,
            parameter DEPTH = 4
        ) (
            output logic [WIDTH-1:0] data_out
        );
            logic [WIDTH-1:0] fifo_array [DEPTH-1:0];
        endmodule
        """,
        harness_files={
            "src/test.py": """
            runner.build(parameters={"WIDTH": WIDTH, "DEPTH": DEPTH})
            int(dut.data_out.value)
            int(dut.fifo_array[0].value)
            """,
        },
        sv_code=core,
    )

    assert wrapper is not None
    result = _run_iverilog_wrapper(tmp_path, core + "\n" + wrapper)
    assert result.returncode == 0, result.stdout + result.stderr

def _generate_dice_whole_output_alias_wrapper(core: str) -> str:
    _, ports = parse_module_ports(core, module_name="sparkle_inner")
    wrapper = generate_cvdp_wrapper(
        design_name="digital_dice_roller",
        sparkle_mod_name="sparkle_inner",
        sparkle_ports=ports,
        ref_code="""
        module digital_dice_roller #(
            parameter int DICE_MAX = 6,
            parameter int BIT_WIDTH = $clog2(DICE_MAX) + 1
        ) (output logic [BIT_WIDTH-1:0] dice_value);
        endmodule
        """,
        harness_files={
            "src/test.py": """
            runner.build(parameters={
                "DICE_MAX": DICE_MAX, "NUM_DICE": NUM_DICE
            })
            int(dut.dice_values.value)
            """,
        },
        sv_code=core,
        benchmark_ports=[(
            "output",
            "logic [(NUM_DICE * BIT_WIDTH)-1:0]",
            "dice_values",
        )],
    )
    assert wrapper is not None
    return wrapper


def test_cvdp_exact_old_dice_output_accepts_unique_plain_semantic_alias():
    core = """
    module sparkle_inner #(
        parameter int DICE_MAX = 6,
        parameter int NUM_DICE = 2
    ) (
        output logic [(NUM_DICE * ($clog2(DICE_MAX) + 1))-1:0] out
    );
        logic [(NUM_DICE * ($clog2(DICE_MAX) + 1))-1:0] _gen_dice_values;
        assign _gen_dice_values = '0;
        assign out = _gen_dice_values;
    endmodule
    """

    wrapper = _generate_dice_whole_output_alias_wrapper(core)

    assert "assign dice_values = out_wire;" in wrapper
    assert re.search(r"\bdice_value\b", wrapper) is None
    assert ".BIT_WIDTH(" not in wrapper
    assert "_cvdp_bad_width_alias_wrapper_dice_values" in wrapper
    assert "_cvdp_bad_width_alias_core_dice_values" in wrapper
    assert "_cvdp_bad_width_alias_semantic_dice_values" in wrapper
    assert (
        "$bits(sparkle_dut.out) != "
        "$bits(sparkle_dut._gen_dice_values)"
    ) in wrapper



_DICE_OUTPUT_DECL = (
    "output logic [(NUM_DICE * ($clog2(DICE_MAX) + 1))-1:0] out"
)
_DICE_SCOPE_UNSAFE_ALIAS_CASES = (
    (
        "initial begin assign out = _gen_dice_values; end",
        "initial-procedural-assign",
    ),
    (
        "always @* begin assign out = _gen_dice_values; end",
        "always-procedural-assign",
    ),
    (
        "always_comb begin assign out = _gen_dice_values; end",
        "always-comb-procedural-assign",
    ),
    (
        "always_latch begin assign out = _gen_dice_values; end",
        "always-latch-procedural-assign",
    ),
    (
        "always_ff @(posedge _gen_dice_values[0]) begin\n"
        "  assign out = _gen_dice_values;\n"
        "end",
        "always-ff-procedural-assign",
    ),
    (
        "if (NUM_DICE > 0) begin : branch\n"
        "  assign out = _gen_dice_values;\n"
        "end",
        "implicit-generate-branch",
    ),
    (
        "if (NUM_DICE > 0) begin : outer\n"
        "  if (DICE_MAX > 0) begin : inner\n"
        "    assign out = _gen_dice_values;\n"
        "  end\n"
        "end",
        "nested-implicit-generate-branch",
    ),
    (
        "logic [(NUM_DICE * ($clog2(DICE_MAX) + 1))-1:0] alias_wire;\n"
        "assign out = alias_wire;\n"
        "initial begin assign alias_wire = _gen_dice_values; end",
        "procedural-intermediate-driver",
    ),
    (
        "initial begin\n"
        "  assign out = _gen_dice_values;\n"
        "  #1 deassign out;\n"
        "end",
        "procedural-deassign",
    ),
    (
        "assign out = _gen_dice_values;\n"
        "initial begin\n"
        "  force out = _gen_dice_values;\n"
        "  #1 release out;\n"
        "end",
        "additional-force-driver",
    ),
)


@pytest.mark.parametrize(
    ("outputs", "body"),
    [
        ("output logic [(NUM_DICE * ($clog2(DICE_MAX) + 1))-1:0] out", "assign out = ~_gen_dice_values;"),
        ("output logic [(NUM_DICE * ($clog2(DICE_MAX) + 1))-1:0] out", "assign out = _gen_dice_values[5:0];"),
        (
            "output logic [(NUM_DICE * ($clog2(DICE_MAX) + 1))-1:0] out",
            "assign out = _gen_dice_values;\n"
            "assign out = _gen_dice_values;",
        ),
        (
            "output logic [(NUM_DICE * ($clog2(DICE_MAX) + 1))-1:0] out_a, output logic [(NUM_DICE * ($clog2(DICE_MAX) + 1))-1:0] out_b",
            "assign out_a = _gen_dice_values;\n"
            "assign out_b = _gen_dice_values;",
        ),
        (
            "output logic [(NUM_DICE * ($clog2(DICE_MAX) + 1))-1:0] out",
            "logic [(NUM_DICE * ($clog2(DICE_MAX) + 1))-1:0] reused;\n"
            "assign out = _gen_dice_values;\n"
            "assign reused = _gen_dice_values;",
        ),
        (
            "output logic [(NUM_DICE * ($clog2(DICE_MAX) + 1))-1:0] out",
            "generate\n"
            "  if (NUM_DICE > 0) begin : branch\n"
            "    assign out = _gen_dice_values;\n"
            "  end\n"
            "endgenerate",
        ),
        *[
            (_DICE_OUTPUT_DECL, body)
            for body, _ in _DICE_SCOPE_UNSAFE_ALIAS_CASES
        ],
    ],
    ids=[
        "operation",
        "slice",
        "duplicate-driver",
        "ambiguous-core-outputs",
        "alias-reuse",
        "generate-branch",
        *[case_id for _, case_id in _DICE_SCOPE_UNSAFE_ALIAS_CASES],
    ],
)
def test_cvdp_exact_old_dice_output_rejects_unsafe_alias(outputs, body):
    core = f"""
    module sparkle_inner #(
        parameter int DICE_MAX = 6,
        parameter int NUM_DICE = 2
    ) ({outputs});
        logic [(NUM_DICE * ($clog2(DICE_MAX) + 1))-1:0] _gen_dice_values;
        assign _gen_dice_values = '0;
        {body}
    endmodule
    """

    with pytest.raises(CVDPAdapterContractError, match=CVDP_ADAPTER_ERROR):
        _generate_dice_whole_output_alias_wrapper(core)


@pytest.mark.skipif(
    shutil.which("iverilog") is None or shutil.which("vvp") is None,
    reason="Icarus Verilog is required for alias-scope regression",
)
@pytest.mark.parametrize(
    "body",
    [body for body, _ in _DICE_SCOPE_UNSAFE_ALIAS_CASES],
    ids=[case_id for _, case_id in _DICE_SCOPE_UNSAFE_ALIAS_CASES],
)
def test_cvdp_rejected_alias_scopes_are_valid_systemverilog(tmp_path, body):
    core = f"""
    module sparkle_inner #(
        parameter int DICE_MAX = 6,
        parameter int NUM_DICE = 2
    ) ({_DICE_OUTPUT_DECL});
        logic [(NUM_DICE * ($clog2(DICE_MAX) + 1))-1:0] _gen_dice_values;
        assign _gen_dice_values = '0;
        {body}
    endmodule
    """

    result = _run_iverilog_wrapper(tmp_path, core, top="sparkle_inner")

    assert result.returncode == 0, result.stdout + result.stderr
    with pytest.raises(CVDPAdapterContractError, match=CVDP_ADAPTER_ERROR):
        _generate_dice_whole_output_alias_wrapper(core)


@pytest.mark.skipif(
    shutil.which("iverilog") is None or shutil.which("vvp") is None,
    reason="Icarus Verilog is required for alias width-guard regression",
)
def test_cvdp_whole_output_alias_semantic_width_guard_fails(tmp_path):
    core = """
    module sparkle_inner(output logic [5:0] out);
        logic [6:0] _gen_dice_values;
        assign _gen_dice_values = '0;
        assign out = _gen_dice_values;
    endmodule
    """
    wrapper = generate_cvdp_wrapper(
        design_name="digital_dice_roller",
        sparkle_mod_name="sparkle_inner",
        sparkle_ports=[("output", "logic [5:0]", "out")],
        ref_code="""
        module digital_dice_roller(output logic [5:0] dice_value);
        endmodule
        """,
        harness_files={"src/test.py": "int(dut.dice_values.value)"},
        sv_code=core,
        benchmark_ports=[("output", "logic [5:0]", "dice_values")],
    )

    assert wrapper is not None
    result = _run_iverilog_wrapper(
        tmp_path,
        core + "\n" + wrapper,
        top="digital_dice_roller",
    )
    assert result.returncode != 0
    assert "whole-output semantic alias width mismatch for dice_values" in (
        result.stdout + result.stderr
    )



@pytest.mark.skipif(
    shutil.which("iverilog") is None or shutil.which("vvp") is None,
    reason="Icarus Verilog is required for alias-hop width regression",
)
def test_cvdp_whole_output_alias_intermediate_width_guard_fails(tmp_path):
    core = """
    module sparkle_inner(output logic [5:0] out);
        logic [6:0] _gen_alias;
        logic [5:0] _gen_dice_values;
        assign _gen_dice_values = 0;
        assign _gen_alias = _gen_dice_values;
        assign out = _gen_alias;
    endmodule
    """
    wrapper = generate_cvdp_wrapper(
        design_name="digital_dice_roller",
        sparkle_mod_name="sparkle_inner",
        sparkle_ports=[("output", "logic [5:0]", "out")],
        ref_code="""
        module digital_dice_roller(output logic [5:0] dice_value);
        endmodule
        """,
        harness_files={"src/test.py": "int(dut.dice_values.value)"},
        sv_code=core,
        benchmark_ports=[("output", "logic [5:0]", "dice_values")],
    )

    assert wrapper is not None
    assert (
        "$bits(sparkle_dut.out) != $bits(sparkle_dut._gen_alias)"
        in wrapper
    )
    assert (
        "$bits(sparkle_dut._gen_alias) != "
        "$bits(sparkle_dut._gen_dice_values)"
    ) in wrapper
    result = _run_iverilog_wrapper(
        tmp_path,
        core + "\n" + wrapper,
        top="digital_dice_roller",
    )
    assert result.returncode != 0
    assert "whole-output semantic alias width mismatch for dice_values" in (
        result.stdout + result.stderr
    )
