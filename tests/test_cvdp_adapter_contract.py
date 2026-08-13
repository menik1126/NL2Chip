from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "agent"))

from dataset import ProblemInfo  # noqa: E402
from evaluator import (  # noqa: E402
    CVDP_PARAMETERIZATION_UNSUPPORTED,
    CVDPAdapterContractError,
    Evaluator,
    _cvdp_parameter_override_analysis,
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
    assert "logic [WIDTH-1:0] _gen_data_out_wire;" in wrapper


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
    "harness",
    [
        'runner.build(parameters={"WIDTH": WIDTH})',
        "runner.build()",
    ],
    ids=["explicit-sweep", "native-reference-parameter"],
)
def test_native_parameter_sweep_keeps_sim_result_but_skips_default_ppa(
    tmp_path, monkeypatch, harness
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

    def fake_synthesis(*_args):
        nonlocal synthesis_called
        synthesis_called = True
        return {"synth_pass": True, "area_um2": 1.0}

    monkeypatch.setattr(evaluator, "_run_synthesis", fake_synthesis)

    result = evaluator.evaluate("cvdp_test", tmp_path / "run")

    assert result["sim_status"] == "sim_pass"
    assert result["sim_mismatches"] == 0
    assert result["parameterized_ppa_unsupported"] is True
    assert result["synth_status"] == "not_run_parameterized_sweep"
    assert result["ppa_status"] == "unsupported_parameter_sweep"
    assert result["synth_pass"] is False
    assert result["area_um2"] is None
    assert result["terminal_capability_error"] is False
    assert "module-default configuration" in result["ppa_error"]
    assert not synthesis_called


def test_cvdp_wrapper_bridges_observed_internal_memory_without_making_it_a_port():
    wrapper = generate_cvdp_wrapper(
        design_name="fifo_policy",
        sparkle_mod_name="fifo_policy_sparkle_inner",
        sparkle_ports=[
            ("input", "logic [$clog2(NINDEXES)-1:0]", "_gen_index"),
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
            input logic clk,
            input logic rst,
            output logic [$clog2(NWAYS)-1:0] out
        );
            logic [$clog2(NWAYS)-1:0] _gen_current_way [0:NINDEXES-1];
            assign out = _gen_current_way[_gen_index];
        endmodule
        """,
    )

    assert wrapper is not None
    port_block = wrapper.split(");", 1)[0]
    assert "fifo_array" not in port_block
    assert "logic [$clog2(NWAYS)-1:0] fifo_array [NINDEXES-1:0];" in wrapper
    assert (
        "fifo_policy_sparkle_inner #(\n"
        "        .NWAYS(NWAYS),\n"
        "        .NINDEXES(NINDEXES)\n"
        "    ) sparkle_dut ("
    ) in wrapper
    assert "assign way_replace = out_wire;" in wrapper
    assert (
        "for (genvar _cvdp_bridge_fifo_array_i = 0; "
        "_cvdp_bridge_fifo_array_i <= NINDEXES-1;"
    ) in wrapper
    assert (
        "assign fifo_array[_cvdp_bridge_fifo_array_i] = "
        "sparkle_dut._gen_current_way[_cvdp_bridge_fifo_array_i];"
    ) in wrapper


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
