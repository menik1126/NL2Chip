from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "agent"))

from dataset import ProblemInfo  # noqa: E402
from evaluator import (  # noqa: E402
    Evaluator,
    _classify_cvdp_local_timeout,
    _simulation_diagnostic_stage,
    generate_cvdp_wrapper,
    parse_module_ports,
)
from search import (  # noqa: E402
    build_sim_feedback,
    compact_repair_feedback,
    format_benchmark_interface_contract,
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

    assert "Benchmark parameters referenced by harness: IN_WIDTH, N, OUT_WIDTH" in contract
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
