from __future__ import annotations

import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "agent"))

from dataset import Dataset, ProblemInfo  # noqa: E402
from search import (  # noqa: E402
    _parse_prompt_interface,
    _cvdp_scaffold_defaults,
    _cvdp_scaffold_parameters,
    build_cvdp_idiom_query,
    build_compact_repair_prompt,
    build_cvdp_typed_scaffold,
    build_user_message,
    format_benchmark_interface_contract,
)


def _info(
    *,
    prompt: str,
    ref_code: str,
    harness: str,
    design_name: str,
) -> ProblemInfo:
    return ProblemInfo(
        prob_id="cvdp_contract_test",
        design_name=design_name,
        prompt_text=prompt,
        ref_code=ref_code,
        testbench_path=Path("dummy.jsonl"),
        ref_path=None,
        metadata={
            "dataset": "cvdp",
            "harness_files": {
                "src/.env": f"TOPLEVEL={design_name}\n",
                "src/test.py": harness,
            },
        },
    )


def test_prompt_parser_accepts_styled_numbered_declarations_but_not_examples():
    parsed = _parse_prompt_interface(
        """
        ## Interface
        ### Parameters
        - **WIDTH**: packed width
        2. `DEPTH`: storage depth
        * **`LANES`**: lane count

        ### Inputs
        1. input_A [WIDTH-1:0]
        2) input_B([WIDTH-1:0])
        - `[DEPTH-1:0] input_C`: third operand

        ### Outputs
        - **result [WIDTH-1:0]**: result

        ## Example
        ### Inputs
        - `input_A = 4'b1011`
        - **input_B = 4'b1101**
        ### Output
        - `result = 3'b010`

        ## Waveform
        ### Outputs
        - `trace = 8'b00110011`
        """
    )

    assert parsed.parameters == {"WIDTH", "DEPTH", "LANES"}
    assert parsed.ports == [
        ("input", "logic [WIDTH-1:0]", "input_A"),
        ("input", "logic [WIDTH-1:0]", "input_B"),
        ("input", "logic [DEPTH-1:0]", "input_C"),
        ("output", "logic [WIDTH-1:0]", "result"),
    ]
    assert not {"b1011", "b1101", "b010", "trace"} & {
        name for _, _, name in parsed.ports
    }


def test_public_cvdp12_word_contract_keeps_symbolic_widths_and_ignores_literals():
    info = _info(
        design_name="Bit_Difference_Counter",
        ref_code="module Data_Reduction(input data_in, output reduced_data_out); endmodule",
        prompt="""
        ## Interface
        ### Parameters:
        - **BIT_WIDTH**: Defines the width of the input vectors.
        - **COUNT_WIDTH**: Calculated output count width.
        ### Inputs:
        - **input_A [BIT_WIDTH-1:0]**: First vector.
        - **input_B [BIT_WIDTH-1:0]**: Second vector.
        ### Outputs:
        - **bit_difference_count [COUNT_WIDTH-1:0]**: Hamming distance.

        ## Example
        #### Parameters:
        - `BIT_WIDTH = 4`
        #### Inputs:
        - `input_A = 4'b1011`
        - `input_B = 4'b1101`
        #### Output:
        - `bit_difference_count = 3'b010`
        """,
        harness="""
        runner.build(parameters={"BIT_WIDTH": BIT_WIDTH})
        width = int(dut.COUNT_WIDTH.value)
        dut.input_A.value = 0
        dut.input_B.value = 0
        int(dut.bit_difference_count.value)
        """,
    )

    contract = format_benchmark_interface_contract(info)

    assert "Benchmark parameters required by reference/harness: BIT_WIDTH" in contract
    assert "Derived interface symbols (not independent sweep parameters): COUNT_WIDTH" in contract
    assert "input input_A: logic [BIT_WIDTH-1:0]" in contract
    assert "input input_B: logic [BIT_WIDTH-1:0]" in contract
    assert "output bit_difference_count: logic [COUNT_WIDTH-1:0]" in contract
    assert "b1011" not in contract
    assert "b1101" not in contract
    assert "b010" not in contract


def test_public_cvdp12_gf_contract_extends_exact_baseline_instead_of_closing_it():
    info = _info(
        design_name="gf_mac",
        ref_code="""
        module gf_mac #(parameter WIDTH = 32) (
            input logic [WIDTH-1:0] a,
            input logic [WIDTH-1:0] b,
            output logic [7:0] result
        ); endmodule
        """,
        prompt="""
        Introduce a new output signal, `error_flag`, into the design.
        Introduce another output signal, `valid_result`, into the design.
        ## Module Behavior
        ### When `WIDTH` Is a Multiple of 8
        - **Output Signals:**
          - Set `valid_result` to 1 and `error_flag` to 0.
        """,
        harness="""
        runner.build(parameters={"WIDTH": WIDTH})
        dut.a.value = 0
        dut.b.value = 0
        int(dut.result.value)
        int(dut.error_flag.value)
        int(dut.valid_result.value)
        """,
    )

    contract = format_benchmark_interface_contract(info)

    assert "Benchmark parameters required by reference/harness: WIDTH" in contract
    assert "input a: logic [WIDTH-1:0]" in contract
    assert "input b: logic [WIDTH-1:0]" in contract
    assert "output result: logic [7:0] (8 bits)" in contract
    assert "output error_flag: logic" in contract
    assert "output valid_result: logic" in contract


def test_public_cvdp12_dice_contract_replaces_modified_output_with_symbolic_bus():
    info = _info(
        design_name="digital_dice_roller",
        ref_code="""
        module digital_dice_roller #(
            parameter int DICE_MAX = 6,
            parameter int BIT_WIDTH = $clog2(DICE_MAX) + 1
        ) (
            input logic clk,
            input logic reset,
            input logic button,
            output logic [BIT_WIDTH-1:0] dice_value
        ); endmodule
        """,
        prompt="""
        ### Design Specifications
        **Parameters:**
        1. `DICE_MAX`: maximum value.
        2. `BIT_WIDTH`:
            - Dynamically calculated as $clog2(`DICE_MAX`) + 1.
        3. `NUM_DICE`: number of dice.
        **Input Signals:**
        1. `clk`: clock.
        2. `reset`: active-low reset.
        3. `button`: control.
        **Output Signal:**
        1. `dice_values`:
            - Defined as [(`NUM_DICE` * `BIT_WIDTH`) - 1:0].

        ### Example operation
        - `NUM_DICE = 2`
        - `dice_values = 6'b101100`
        """,
        harness="""
        runner.build(parameters={"DICE_MAX": DICE_MAX, "NUM_DICE": NUM_DICE})
        int(dut.NUM_DICE.value)
        dut.clk.value = 0
        dut.reset.value = 0
        dut.button.value = 0
        int(dut.dice_values.value)
        """,
    )

    contract = format_benchmark_interface_contract(info)

    assert "Benchmark parameters required by reference/harness: DICE_MAX, NUM_DICE" in contract
    assert "Derived interface symbols (not independent sweep parameters): BIT_WIDTH" in contract
    assert "output dice_values: logic [(NUM_DICE * BIT_WIDTH) - 1:0]" in contract
    assert "output dice_value:" not in contract


def test_exact_baseline_port_direction_wins_over_harness_helper_heuristic():
    info = _info(
        design_name="dut",
        ref_code="module dut(input logic d, output logic y); endmodule",
        prompt="",
        harness="""
        await drive(dut.d)
        int(dut.y.value)
        """,
    )

    contract = format_benchmark_interface_contract(info)

    assert "Expected inputs: input d: logic" in contract
    assert "Expected outputs: output y: logic" in contract
    assert "output d:" not in contract


def test_baseline_addition_keeps_unmentioned_port_and_rejects_internal_hierarchy():
    info = _info(
        design_name="dut",
        ref_code="""
        module dut(input logic a, output logic legacy);
          logic internal_state;
          logic [7:0] internal_memory [0:3];
          typedef enum logic [1:0] {IDLE, RUN} state_t;
          state_t current_state;
          byte scratch [0:3];
        endmodule
        """,
        prompt="Introduce a new output signal, `status`, into the design.",
        harness="""
        dut.a.value = 0
        int(dut.legacy.value)
        int(dut.status.value)
        int(dut.internal_state.value)
        int(dut.internal_memory[0].value)
        await inspect(dut.internal_memory)
        int(dut.current_state.value)
        int(dut.scratch.value)
        # Stale documentation: int(dut.comment_fake.value)
        '''int(dut.docstring_fake.value)'''
        print("int(dut.string_fake.value)")
        """,
    )

    contract = format_benchmark_interface_contract(info)

    assert "output legacy: logic" in contract
    assert "output status: logic" in contract
    assert "internal_state" not in contract
    assert "internal_memory" not in contract
    assert "current_state" not in contract
    assert "scratch" not in contract
    assert "comment_fake" not in contract
    assert "docstring_fake" not in contract
    assert "string_fake" not in contract


def test_pseudo_example_sections_and_decimal_values_never_define_ports():
    parsed = _parse_prompt_interface(
        """
        ### Outputs
        - `result`: actual result
        **Example:**
        **Inputs:**
        - `sample_in`: 13
        **Outputs:**
        - `sample_out`: 42

        ### Interface
        ### Outputs
        - `status`: status bit
        For example:
        - `trace`: 7
        """
    )

    assert parsed.ports == [
        ("output", "logic", "result"),
        ("output", "logic", "status"),
    ]


def test_complete_prompt_direction_rejects_harness_only_hierarchy_without_exact_ref():
    info = _info(
        design_name="dut",
        ref_code="module helper(input logic unused); endmodule",
        prompt="""
        ### Inputs
        - `a`: input
        ### Outputs
        - `y`: output
        """,
        harness="""
        dut.a.value = 0
        int(dut.y.value)
        int(dut.state.value)
        """,
    )

    contract = format_benchmark_interface_contract(info)

    assert "input a: logic" in contract
    assert "output y: logic" in contract
    assert "output state:" not in contract


def test_value_ranges_and_literal_examples_are_not_port_widths_or_ports():
    parsed = _parse_prompt_interface(
        """
        ### Inputs
        - `count`: value constrained to [0, MAX]
        ### Outputs
        - `result`: result
        For example:
        - `expected`: 8'b00000000
        - `hex_value`: 0xFF
        - `binary_value`: 0b1010
        - `sv_zero`: '0
        - `boolean_value`: false
        - `null_value`: null
        - `hex_with_note`: 0xFF (all ones)
        - `zero_with_note`: '0 (reset value)
        - `bool_with_note`: true (valid)
        - `decimal_with_unit`: 42 cycles
        - `clock_cycles`: 42 clock cycles
        - `illustrative`: illustrative waveform
        ### Interface
        ### Outputs
        - `status`: actual status
        """
    )

    assert parsed.ports == [
        ("input", "logic", "count"),
        ("output", "logic", "result"),
        ("output", "logic", "status"),
    ]


def test_bold_example_scope_blocks_nested_markdown_interface_labels():
    parsed = _parse_prompt_interface(
        """
        ### Outputs
        - `result`: actual result
        **Example:**
        ### Inputs
        - `sample`: illustrative operand
        ### Outputs
        - `expected`: illustrative result

        ### Interface
        ### Outputs
        - `status`: actual status
        **Waveform:**
        ### Outputs
        - `trace`: waveform bus
        """
    )

    assert parsed.ports == [
        ("output", "logic", "result"),
        ("output", "logic", "status"),
    ]


def test_symbolic_text_width_requires_declared_or_explicit_symbol():
    parsed = _parse_prompt_interface(
        """
        ### Parameters
        - `width`: configurable data width
        ### Inputs
        - `data`: width bits
        - `quoted_data`: `OTHER_WIDTH` bits
        ### Outputs
        - `valid`: Status bit indicating completion
        """
    )

    assert parsed.ports == [
        ("input", "logic [width-1:0]", "data"),
        ("input", "logic [OTHER_WIDTH-1:0]", "quoted_data"),
        ("output", "logic", "valid"),
    ]


def test_square_behavior_text_does_not_hide_later_symbolic_outputs():
    parsed = _parse_prompt_interface(
        """
        #### Interface Specifications
        **Parameters:**
        - `WIDTH`: operand width.
        **I/O Ports:**
        - **Inputs:**
          - `[WIDTH-1:0] num`: operand.
            - **Behavior**: Remains stable throughout the computation.
          - `clk`: clock.
        - **Outputs:**
          - `[WIDTH/2-1:0] final_root`: root.
          - `done`: completion.
        """
    )

    assert parsed.ports == [
        ("input", "logic [WIDTH-1:0]", "num"),
        ("input", "logic", "clk"),
        ("output", "logic [WIDTH/2-1:0]", "final_root"),
        ("output", "logic", "done"),
    ]


def test_hamming_examples_do_not_hide_later_derived_parameters():
    info = _info(
        design_name="hamming_rx",
        ref_code="module old_receiver(input logic old_in); endmodule",
        prompt="""
        ### Parameterization:
        - **DATA_WIDTH**: User-configurable width.
        - **PARITY_BIT**: User-configurable parity count.
          For example, if `m = 4`:
          - `p = 0` is invalid.
        - **ENCODED_DATA**: Calculated as `PARITY_BIT + DATA_WIDTH + 1`.
        - **ENCODED_DATA_BIT**: Calculated as the index width of `ENCODED_DATA`.
        ### Input/Output Specifications:
        - **Inputs:**
          - `data_in[ENCODED_DATA-1:0]`: encoded input.
        - **Outputs:**
          - `data_out[DATA_WIDTH-1:0]`: corrected data.
        """,
        harness="""
        runner.build(parameters={"DATA_WIDTH": DATA_WIDTH, "PARITY_BIT": PARITY_BIT})
        int(dut.ENCODED_DATA.value)
        dut.data_in.value = 0
        int(dut.data_out.value)
        """,
    )

    contract = format_benchmark_interface_contract(info)

    assert "Benchmark parameters required by reference/harness: DATA_WIDTH, PARITY_BIT" in contract
    assert "Derived interface symbols (not independent sweep parameters): ENCODED_DATA, ENCODED_DATA_BIT" in contract
    assert "input data_in: logic [ENCODED_DATA-1:0]" in contract
    assert "output data_out: logic [DATA_WIDTH-1:0]" in contract
    assert "output ENCODED_DATA:" not in contract
    assert " p," not in contract


def test_exact_ref_localparam_handle_is_derived_symbol_not_output_port():
    info = _info(
        design_name="dut",
        ref_code="""
        module dut #(
            parameter W = 8,
            localparam CW = $clog2(W + 1)
        ) (
            input logic [W-1:0] a,
            output logic [CW-1:0] y
        ); endmodule
        """,
        prompt="",
        harness="""
        runner.build(parameters={"W": W})
        dut.a.value = 0
        int(dut.y.value)
        int(dut.CW.value)
        """,
    )

    contract = format_benchmark_interface_contract(info)

    assert "Benchmark parameters required by reference/harness: W" in contract
    assert "Derived interface symbols (not independent sweep parameters): CW" in contract
    assert "output y: logic [CW-1:0]" in contract
    assert "output CW:" not in contract


_PUBLIC_CVDP12 = (
    PROJECT_ROOT
    / "benchmarks"
    / "cvdp-benchmark-dataset"
    / "cvdp_v1.1.0_nonagentic_code_generation_no_commercial.jsonl"
)


@pytest.mark.skipif(not _PUBLIC_CVDP12.exists(), reason="public CVDP12 fixture is not installed")
@pytest.mark.parametrize(
    ("problem_id", "required", "forbidden"),
    [
        (
            "cvdp_copilot_word_reducer_0008",
            [
                "Benchmark parameters required by reference/harness: BIT_WIDTH",
                "Derived interface symbols (not independent sweep parameters): COUNT_WIDTH",
                "input input_A: logic [BIT_WIDTH-1:0]",
                "output bit_difference_count: logic [COUNT_WIDTH-1:0]",
            ],
            ["b1011", "b1101", "b010"],
        ),
        (
            "cvdp_copilot_gf_multiplier_0021",
            [
                "output result: logic [7:0] (8 bits)",
                "output error_flag: logic",
                "output valid_result: logic",
            ],
            [],
        ),
        (
            "cvdp_copilot_digital_dice_roller_0004",
            [
                "Benchmark parameters required by reference/harness: DICE_MAX, NUM_DICE",
                "Derived interface symbols (not independent sweep parameters): BIT_WIDTH",
                "output dice_values: logic [(NUM_DICE * BIT_WIDTH) - 1:0]",
            ],
            ["output dice_value:"],
        ),
        (
            "cvdp_copilot_square_root_0003",
            ["output final_root: logic [WIDTH/2-1:0]", "output done: logic"],
            ["output Behavior:"],
        ),
        (
            "cvdp_copilot_hamming_code_tx_and_rx_0011",
            [
                "Derived interface symbols (not independent sweep parameters): ENCODED_DATA, ENCODED_DATA_BIT",
                "input data_in: logic [ENCODED_DATA-1:0]",
                "output data_out: logic [DATA_WIDTH-1:0]",
            ],
            ["output ENCODED_DATA:"],
        ),
    ],
)
def test_installed_public_cvdp12_contracts(
    problem_id: str,
    required: list[str],
    forbidden: list[str],
):
    info = Dataset("cvdp", PROJECT_ROOT).load_problem(problem_id)
    contract = format_benchmark_interface_contract(info)

    for fragment in required:
        assert fragment in contract
    for fragment in forbidden:
        assert fragment not in contract


def _hamming_guardrail_info() -> ProblemInfo:
    return _info(
        design_name="hamming_tx",
        prompt="""
        ## Interface
        ### Parameters
        - **DATA_WIDTH**: User-configurable data width.
        - **PARITY_BIT**: User-configurable parity width.
        - **ENCODED_DATA**: Calculated as `DATA_WIDTH + PARITY_BIT + 1`.
        ### Inputs
        - **data_in [DATA_WIDTH-1:0]**: Input word.
        ### Outputs
        - **data_out [ENCODED_DATA-1:0]**: Encoded word.
        """,
        ref_code="""
        module hamming_tx #(
          parameter DATA_WIDTH = 4,
          parameter PARITY_BIT = 3,
          localparam ENCODED_DATA = DATA_WIDTH + PARITY_BIT + 1
        ) (
          input logic [DATA_WIDTH-1:0] data_in,
          output logic [ENCODED_DATA-1:0] data_out
        );
          // REFERENCE_SENTINEL
        endmodule
        """,
        harness="""
        parameter_combinations = [(4, 3), (8, 4), (16, 5)]
        parameters = {
            "DATA_WIDTH": DATA_WIDTH,
            "PARITY_BIT": PARITY_BIT,
        }

        @pytest.mark.parametrize(
            "DATA_WIDTH,PARITY_BIT", parameter_combinations
        )
        def test_hamming(dut, DATA_WIDTH, PARITY_BIT):
            dut.data_in.value = 0
            int(dut.data_out.value)
        """,
    )


def _hamming_rx_guardrail_info() -> ProblemInfo:
    return _info(
        design_name="hamming_rx",
        prompt="""
        ## Interface
        ### Parameters
        - **DATA_WIDTH**: User-configurable data width.
        - **PARITY_BIT**: User-configurable parity width.
        - **ENCODED_DATA**: Calculated as `DATA_WIDTH + PARITY_BIT + 1`.
        ### Inputs
        - **data_in [ENCODED_DATA-1:0]**: Encoded input word.
        ### Outputs
        - **data_out [DATA_WIDTH-1:0]**: Corrected data word.
        """,
        ref_code="""
        module hamming_rx #(
          parameter DATA_WIDTH = 4,
          parameter PARITY_BIT = 3,
          localparam ENCODED_DATA = DATA_WIDTH + PARITY_BIT + 1
        ) (
          input logic [ENCODED_DATA-1:0] data_in,
          output logic [DATA_WIDTH-1:0] data_out
        );
        endmodule
        """,
        harness="""
        parameter_combinations = [(4, 3), (8, 4), (16, 5)]
        parameters = {
            "DATA_WIDTH": DATA_WIDTH,
            "PARITY_BIT": PARITY_BIT,
        }

        @pytest.mark.parametrize(
            "DATA_WIDTH,PARITY_BIT", parameter_combinations
        )
        def test_hamming(dut, DATA_WIDTH, PARITY_BIT):
            dut.data_in.value = 0
            int(dut.ENCODED_DATA.value)
            int(dut.data_out.value)
        """,
    )


def test_scaffold_defaults_select_a_complete_hamming_sweep_tuple():
    info = _hamming_guardrail_info()
    parameters, _, sweep_values, sweep_combinations = (
        _cvdp_scaffold_parameters(info)
    )

    assert sweep_combinations == [
        {"DATA_WIDTH": "4", "PARITY_BIT": "3"},
        {"DATA_WIDTH": "8", "PARITY_BIT": "4"},
        {"DATA_WIDTH": "16", "PARITY_BIT": "5"},
    ]
    assert _cvdp_scaffold_defaults(
        parameters,
        "unmapped_hamming_design",
        sweep_values,
        sweep_combinations,
    ) == {"DATA_WIDTH": 4, "PARITY_BIT": 3}
    assert (
        "[DATA_WIDTH := 4, PARITY_BIT := 3]"
        in build_cvdp_typed_scaffold(info)
    )


def test_hamming_rx_scaffold_implements_adapter_metadata_without_a_todo():
    info = _hamming_rx_guardrail_info()
    contract = format_benchmark_interface_contract(info)
    scaffold = build_cvdp_typed_scaffold(info)
    initial_prompt = build_user_message(
        "guardrail_problem",
        has_repl=True,
        info=info,
        dataset_name="cvdp",
        include_cvdp_scaffold=True,
    )

    assert "Adapter-observable derived metadata exception" in contract
    assert "not an independent sweep parameter or functional data output" in contract
    assert "32-bit metadata leaf" in contract
    assert "`DATA_WIDTH + PARITY_BIT + 1`" in contract
    assert "let ENCODED_DATA_metadata : Signal dom (BitVec ((31) + 1))" in scaffold
    assert (
        "Signal.pure (BitVec.ofNat ((31) + 1) "
        "(DATA_WIDTH + PARITY_BIT + 1))" in scaffold
    )
    assert (
        "Signal.mux scaffold_condition ENCODED_DATA_metadata ENCODED_DATA_metadata"
        in scaffold
    )
    assert "TODO: implement `ENCODED_DATA`" not in scaffold
    assert "TODO: implement `data_out`" in scaffold
    assert "Adapter-observable derived metadata exception" in initial_prompt


def test_initial_scaffold_prompt_is_target_first_without_generic_template():
    prompt = build_user_message(
        "guardrail_problem",
        has_repl=True,
        info=_hamming_guardrail_info(),
        dataset_name="cvdp",
        include_cvdp_scaffold=True,
    )

    assert "### Deterministic Typed Sparkle Scaffold" in prompt
    assert "1. Read `Generated/guardrail_problem.lean` first" in prompt
    assert "Consult at most one or two relevant Benchmark examples" in prompt
    assert "1. Start by reading a few Benchmark/*.lean examples" not in prompt
    assert "The file must follow this exact structure:" not in prompt
    assert "(<inputs>) : <output_type>" not in prompt


def test_scaffold_prompt_features_remain_opt_in_by_default():
    info = _hamming_guardrail_info()
    implicit_initial = build_user_message(
        "guardrail_problem",
        has_repl=True,
        info=info,
        dataset_name="cvdp",
    )
    explicit_initial = build_user_message(
        "guardrail_problem",
        has_repl=True,
        info=info,
        dataset_name="cvdp",
        include_cvdp_scaffold=False,
        include_cvdp_verified_idioms=False,
    )

    assert implicit_initial == explicit_initial
    assert "1. Start by reading a few Benchmark/*.lean examples" in implicit_initial
    assert "The file must follow this exact structure:" in implicit_initial
    assert "### Deterministic Typed Sparkle Scaffold" not in implicit_initial


def test_compact_scaffold_repair_reads_target_without_repeating_code():
    info = _hamming_guardrail_info()
    current_lean = "-- CURRENT_LEAN_SENTINEL\n" * 500
    arguments = {
        "prob_id": "guardrail_problem",
        "info": info,
        "dataset_name": "cvdp",
        "has_repl": True,
        "phase": "simulation",
        "iteration": 2,
        "current_lean": current_lean,
        "latest_feedback": "LATEST_DIAGNOSTIC_SENTINEL",
        "recent_attempts": [],
    }
    implicit_default = build_compact_repair_prompt(**arguments)
    explicit_default = build_compact_repair_prompt(
        **arguments,
        include_cvdp_scaffold=False,
        include_cvdp_verified_idioms=False,
    )
    guarded = build_compact_repair_prompt(
        **arguments,
        include_cvdp_scaffold=True,
    )

    assert implicit_default == explicit_default
    assert "REFERENCE_SENTINEL" in implicit_default
    assert "CURRENT_LEAN_SENTINEL" in implicit_default

    assert "REFERENCE_SENTINEL" not in guarded
    assert "CURRENT_LEAN_SENTINEL" not in guarded
    assert "### Reference Verilog / Interface Context" not in guarded
    assert "### Deterministic Typed Sparkle Scaffold" not in guarded
    assert "Signal.pure (BitVec.ofNat" not in guarded
    assert "### Benchmark Interface Contract" in guarded
    assert "LATEST_DIAGNOSTIC_SENTINEL" in guarded
    assert "`read_file` on `Generated/guardrail_problem.lean`" in guarded
    assert len(guarded) < len(implicit_default)


def test_verified_idiom_initial_context_is_opt_in_body_only_and_scaffold_first():
    info = _hamming_guardrail_info()
    default_prompt = build_user_message(
        "guardrail_problem",
        has_repl=True,
        info=info,
        dataset_name="cvdp",
        include_cvdp_scaffold=True,
    )
    treatment_prompt = build_user_message(
        "guardrail_problem",
        has_repl=True,
        info=info,
        dataset_name="cvdp",
        include_cvdp_scaffold=True,
        include_cvdp_verified_idioms=True,
    )

    heading = "### Retrieved Verified Sparkle Idioms"
    assert heading not in default_prompt
    assert heading in treatment_prompt
    assert treatment_prompt.index("### Deterministic Typed Sparkle Scaffold") < treatment_prompt.index(heading)
    assert treatment_prompt.index(heading) < treatment_prompt.index("### Reference Verilog")
    assert "def promptIdiom" not in treatment_prompt
    assert "#synthesizeVerilog promptIdiom" not in treatment_prompt
    assert "Use only the retrieved verified idiom bodies" in treatment_prompt
    assert "Read `Benchmark/RTLIdioms.lean`" not in treatment_prompt
    assert "The file must follow this exact structure:" not in treatment_prompt


def test_verified_idiom_compact_repair_adds_exactly_one_bounded_card():
    info = _hamming_guardrail_info()
    prompt = build_compact_repair_prompt(
        prob_id="guardrail_problem",
        info=info,
        dataset_name="cvdp",
        has_repl=True,
        phase="simulation",
        iteration=3,
        current_lean="-- CURRENT_LEAN_SENTINEL\n" * 100,
        latest_feedback=(
            "CVDP_ADAPTER_ERROR: output has no unique exact-core mapping; "
            "failure_category=adapter_contract_error"
        ),
        recent_attempts=[],
        include_cvdp_scaffold=True,
        include_cvdp_verified_idioms=True,
    )

    heading = "### Verified repair idiom `named_packed_outputs`"
    assert prompt.count("### Verified repair idiom") == 1
    assert heading in prompt
    assert "### Retrieved Verified Sparkle Idioms" not in prompt
    assert "def promptIdiom" not in prompt
    assert "#synthesizeVerilog promptIdiom" not in prompt
    assert "REFERENCE_SENTINEL" not in prompt
    assert "CURRENT_LEAN_SENTINEL" not in prompt
    card = prompt.split(heading, 1)[1].split("### Additional Input Context Files", 1)[0]
    assert len(heading) + len(card) <= 1402


def test_idiom_query_does_not_match_ram_inside_parameter():
    query = build_cvdp_idiom_query(_hamming_guardrail_info())

    assert query.has_parameters
    assert not query.uses_memory
