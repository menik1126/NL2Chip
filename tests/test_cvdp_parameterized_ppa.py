from __future__ import annotations

import sys
import threading
from collections import Counter
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "agent"))

import search  # noqa: E402
from dataset import ProblemInfo  # noqa: E402
from evaluator import Evaluator, _cvdp_exact_parameter_sweep_plan  # noqa: E402
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
        self.sweep_calls: list[tuple[tuple[tuple[str, int], ...], ...]] = []
        self.synth_parameters: list[dict[str, int]] = []

    def _run_lint(self, _sv_file: Path) -> bool:
        return True

    def _run_sim_cvdp(self, *_args, **kwargs):
        assert kwargs["direct_top"] is True
        return "sim_pass", 0, "all parameter configurations passed"

    def _run_synthesis(self, *_args, **kwargs):
        self.synth_calls += 1
        self.synth_tops.append(_args[2])
        self.synth_parameters.append(dict(kwargs.get("parameters") or {}))
        return {
            "synth_pass": True,
            "area_um2": 12.5,
            "cell_count": 7,
            "wns_ns": 0.2,
            "power_uw": 1.5,
        }

    def _run_pnr(self, *_args, **_kwargs):
        self.pnr_calls += 1
        self.pnr_tops.append(_args[2])
        return {"pnr_pass": True}

    def run_parameterized_ppa(
        self,
        *,
        sv_code: str,
        top_module: str,
        configurations: tuple[tuple[tuple[str, int], ...], ...],
    ) -> dict:
        self.sweep_calls.append(configurations)
        points = []
        for config in configurations:
            metrics = self._run_synthesis(
                top_module,
                sv_code,
                top_module,
                Path("."),
                parameters=dict(config),
            )
            if self.enable_pnr:
                metrics.update(self._run_pnr(
                    top_module,
                    sv_code,
                    top_module,
                    Path("."),
                    parameters=dict(config),
                ))
            points.append({
                "config": dict(config),
                "success": True,
                "metrics": metrics,
                "cache_hit": False,
            })
        return {
            "parameterized_ppa_unsupported": False,
            "parameter_sweep_results": points,
            "parameterized_ppa_results": points,
            "verification_evidence": [{
                "kind": "finite_parameter_sweep",
                "status": "passed",
                "scope": "enumerated_configurations_only",
            }],
            "synth_status": "finite_parameter_sweep_passed",
            "ppa_status": "finite_parameter_sweep",
            "synth_pass": True,
            "pnr_pass": self.enable_pnr,
            "area_um2": 12.5,
            "cell_count": 7,
            "wns_ns": 0.2,
            "power_uw": 1.5,
            "ppa_error": None,
        }


@pytest.mark.parametrize(
    "harness",
    [
        'runner.build(parameters={"WIDTH": WIDTH})',
        "runner.build(parameters=params)",
    ],
    ids=["unresolved-symbolic-value", "unresolved-parameter-dict"],
)
def test_unresolved_direct_sv_parameter_sweep_keeps_simulation_but_skips_ppa(
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


def test_direct_sv_literal_parameter_configuration_runs_finite_ppa(
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
        _info('runner.build(parameters={"WIDTH": 16})'),
        code,
        evaluator,
        tmp_path,
    )

    assert result["sim_status"] == "sim_pass"
    assert result["parameterized_ppa_unsupported"] is False
    assert result["synth_status"] == "finite_parameter_sweep_passed"
    assert result["ppa_status"] == "finite_parameter_sweep"
    assert result["synth_pass"] is True
    assert result["pnr_pass"] is True
    assert result["parameter_sweep_results"][0]["config"] == {"WIDTH": 16}
    assert evaluator.sweep_calls == [((('WIDTH', 16),),)]
    assert evaluator.synth_parameters == [{"WIDTH": 16}]
    assert evaluator.synth_calls == 1
    assert evaluator.pnr_calls == 1


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


def test_native_top_parameter_runs_default_configuration_without_override(
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
    assert result["parameterized_ppa_unsupported"] is False
    assert result["synth_status"] == "finite_parameter_sweep_passed"
    assert result["parameter_sweep_results"][0]["config"] == {}
    assert result["area_um2"] == 12.5
    assert evaluator.sweep_calls == [(((),))]
    assert evaluator.synth_parameters == [{}]
    assert evaluator.synth_calls == 1
    assert evaluator.pnr_calls == 1


def test_reference_parameter_runs_exact_literal_build_alias_configuration(
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

    assert result["parameterized_ppa_unsupported"] is False
    assert result["synth_status"] == "finite_parameter_sweep_passed"
    assert evaluator.sweep_calls == [((('WIDTH', 8),),)]
    assert evaluator.synth_parameters == [{"WIDTH": 8}]
    assert evaluator.synth_calls == 1


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


def test_unresolved_parameterized_verilog_ppa_loop_returns_before_llm(
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
        _info("runner.build(parameters=params_from_environment)"),
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


def test_exact_sweep_plan_preserves_correlated_multi_parameter_rows():
    plan = _cvdp_exact_parameter_sweep_plan(
        {
            "src/test_runner.py": """
            @pytest.mark.parametrize("WIDTH,DEPTH", [(8, 2), (16, 4)])
            def test_dut(WIDTH, DEPTH):
                runner.build(parameters={"WIDTH": WIDTH, "DEPTH": DEPTH})
            """,
        },
        {"WIDTH", "DEPTH"},
    )

    assert plan.unresolved is False
    assert plan.configurations == (
        (("DEPTH", 2), ("WIDTH", 8)),
        (("DEPTH", 4), ("WIDTH", 16)),
    )
    assert (("DEPTH", 4), ("WIDTH", 8)) not in plan.configurations
    assert (("DEPTH", 2), ("WIDTH", 16)) not in plan.configurations


@pytest.mark.parametrize(
    ("harness", "expected"),
    [
        (
            """
            for width, depth in [(4, 1), (12, 3)]:
                runner.build(parameters={"WIDTH": width, "DEPTH": depth})
            """,
            (
                (("DEPTH", 1), ("WIDTH", 4)),
                (("DEPTH", 3), ("WIDTH", 12)),
            ),
        ),
        (
            """
            @pytest.mark.parametrize("WIDTH", [4, 12])
            def test_dut(WIDTH):
                runner.build(parameters={"WIDTH": WIDTH, "DEPTH": WIDTH // 4})
            """,
            (
                (("DEPTH", 1), ("WIDTH", 4)),
                (("DEPTH", 3), ("WIDTH", 12)),
            ),
        ),
    ],
    ids=["literal-loop", "pytest-parametrize"],
)
def test_exact_sweep_plan_expands_static_loop_and_parametrize(
    harness: str,
    expected: tuple[tuple[tuple[str, int], ...], ...],
):
    plan = _cvdp_exact_parameter_sweep_plan(
        {"src/test_runner.py": harness},
        {"WIDTH", "DEPTH"},
    )

    assert plan.unresolved is False
    assert plan.configurations == expected


def test_exact_sweep_plan_is_fail_closed_if_any_build_is_unresolved():
    plan = _cvdp_exact_parameter_sweep_plan(
        {
            "src/test_runner.py": """
            runner.build(parameters={"WIDTH": 8, "DEPTH": 2})
            runner.build(parameters=configuration_from_environment)
            """,
        },
        {"WIDTH", "DEPTH"},
    )

    # The known row may be retained for diagnostics, but the plan as a whole
    # must be non-executable.  A partial sweep is not complete evidence.
    assert plan.configurations == ((("DEPTH", 2), ("WIDTH", 8)),)
    assert plan.unresolved is True
    assert any("could not be resolved exactly" in reason for reason in plan.unresolved_reasons)


@pytest.mark.parametrize(
    "harness",
    [
        'cache_builder.build()',
        'cache_builder.build(parameters={"WIDTH": 99})',
    ],
    ids=["default-parameters", "explicit-parameters"],
)
def test_exact_sweep_plan_does_not_treat_unknown_build_receiver_as_cvdp_runner(
    harness: str,
):
    plan = _cvdp_exact_parameter_sweep_plan(
        {"src/test_runner.py": harness},
        {"WIDTH"},
    )

    assert plan.unresolved is True
    assert plan.configurations == ()
    assert any("unknown receiver" in reason for reason in plan.unresolved_reasons)


@pytest.mark.parametrize(
    "harness",
    [
        """
        compile_dut = runner.build
        compile_dut(parameters={"WIDTH": 8})
        """,
        """
        from cocotb_tools.runner import get_runner
        cocotb_runner = get_runner(simulator="icarus")
        compile_dut = getattr(cocotb_runner, "build")
        compile_dut(parameters={"WIDTH": 8})
        """,
    ],
    ids=["bound-build-alias", "cocotb-factory-and-getattr-alias"],
)
def test_exact_sweep_plan_tracks_only_proven_cvdp_runner_aliases(harness: str):
    plan = _cvdp_exact_parameter_sweep_plan(
        {"src/test_runner.py": harness},
        {"WIDTH"},
    )

    assert plan.unresolved is False
    assert plan.configurations == ((("WIDTH", 8),),)


@pytest.mark.parametrize(
    "shadow",
    [
        "import cache_builder as runner",
        "from cache_builder import runner",
        "def runner(): pass",
    ],
    ids=["import-as", "from-import", "function-definition"],
)
def test_exact_sweep_plan_rejects_shadowed_conventional_runner(shadow: str):
    plan = _cvdp_exact_parameter_sweep_plan(
        {
            "src/test_runner.py": f"""
            {shadow}
            runner.build(parameters={{"WIDTH": 99}})

            from cocotb_tools.runner import get_runner
            real_runner = get_runner(simulator="icarus")
            real_runner.build(parameters={{"WIDTH": 8}})
            """,
        },
        {"WIDTH"},
    )

    assert plan.unresolved is True
    assert plan.configurations == ((('WIDTH', 8),),)
    assert any("unknown receiver" in reason for reason in plan.unresolved_reasons)


def test_exact_sweep_plan_rejects_duplicate_parameters_across_kwargs():
    plan = _cvdp_exact_parameter_sweep_plan(
        {
            "src/test_runner.py": """
            runner.build(
                parameters={"WIDTH": 8},
                **{"parameters": {"WIDTH": 16}},
            )
            """,
        },
        {"WIDTH"},
    )

    # Python raises TypeError before invoking build; neither mapping is an
    # executed sweep point.
    assert plan.unresolved is True
    assert plan.configurations == ()
    assert any("duplicate build() keyword" in reason for reason in plan.unresolved_reasons)


@pytest.mark.parametrize(
    "harness",
    [
        """
        WIDTH = 8
        [(WIDTH := 16) for _ in []]
        runner.build(parameters={"WIDTH": WIDTH})
        """,
        """
        WIDTH = 8
        pending = ((WIDTH := 16) for _ in [1])
        runner.build(parameters={"WIDTH": WIDTH})
        """,
        """
        WIDTH = 8
        callback = lambda value=(WIDTH := 16): value
        runner.build(parameters={"WIDTH": WIDTH})
        """,
    ],
    ids=["empty-list-comprehension", "unconsumed-generator", "lambda-default"],
)
def test_exact_sweep_plan_rejects_deferred_python_expression_scopes(harness: str):
    plan = _cvdp_exact_parameter_sweep_plan(
        {"src/test_runner.py": harness},
        {"WIDTH"},
    )

    assert plan.unresolved is True
    assert plan.configurations == ()
    assert any(
        expression in " ".join(plan.unresolved_reasons)
        for expression in ("ListComp", "GeneratorExp", "Lambda")
    )


@pytest.mark.parametrize(
    "harness",
    [
        """
        WIDTH = 8
        ((WIDTH := 16) and print)()
        runner.build(parameters={"WIDTH": WIDTH})
        """,
        """
        WIDTH = 8
        values = [0]
        values[(WIDTH := 16) - 16] = 1
        runner.build(parameters={"WIDTH": WIDTH})
        """,
        """
        WIDTH = 8
        for _ in ([1] if (WIDTH := 16) else []):
            pass
        runner.build(parameters={"WIDTH": WIDTH})
        """,
        """
        WIDTH = 8
        def test_dut(value=(WIDTH := 16)):
            runner.build(parameters={"WIDTH": WIDTH})
        """,
        """
        WIDTH = 8
        @pytest.mark.skipif((WIDTH := 16) and False, reason="not skipped")
        def test_dut():
            runner.build(parameters={"WIDTH": WIDTH})
        """,
    ],
    ids=[
        "callable-expression",
        "assignment-index",
        "loop-iterable",
        "function-default",
        "decorator-condition",
    ],
)
def test_exact_sweep_plan_rejects_named_expressions_in_all_contexts(harness: str):
    plan = _cvdp_exact_parameter_sweep_plan(
        {"src/test_runner.py": harness},
        {"WIDTH"},
    )

    assert plan.unresolved is True
    assert plan.configurations == ()
    assert any("NamedExpr" in reason for reason in plan.unresolved_reasons)


def test_exact_sweep_plan_rejects_shadowed_interpreter_builtin():
    plan = _cvdp_exact_parameter_sweep_plan(
        {
            "src/test_runner.py": """
            def dict(**kwargs):
                return {"WIDTH": 16}

            runner.build(parameters=dict(WIDTH=8))
            """,
        },
        {"WIDTH"},
    )

    assert plan.unresolved is True
    assert plan.configurations == ()
    assert any("builtin" in reason and "dict" in reason for reason in plan.unresolved_reasons)


def test_exact_sweep_plan_respects_python_function_local_scope():
    plan = _cvdp_exact_parameter_sweep_plan(
        {
            "src/test_runner.py": """
            WIDTH = 8

            def test_dut():
                runner.build(parameters={"WIDTH": WIDTH})
                WIDTH = 16
            """,
        },
        {"WIDTH"},
    )

    # Python treats WIDTH as local throughout test_dut, so the build raises
    # UnboundLocalError instead of observing the module-level value.
    assert plan.unresolved is True
    assert plan.configurations == ()
    assert plan.unresolved_reasons


def test_exact_sweep_plan_rejects_unmarked_async_pytest_function():
    plan = _cvdp_exact_parameter_sweep_plan(
        {
            "src/test_runner.py": """
            async def test_dut():
                runner.build(parameters={"WIDTH": 8})
            """,
        },
        {"WIDTH"},
    )

    assert plan.unresolved is True
    assert plan.configurations == ()
    assert any("async test" in reason for reason in plan.unresolved_reasons)


def test_exact_sweep_plan_rejects_duplicate_pytest_parametrization_name():
    plan = _cvdp_exact_parameter_sweep_plan(
        {
            "src/test_runner.py": """
            @pytest.mark.parametrize("WIDTH", [8])
            @pytest.mark.parametrize("WIDTH", [16])
            def test_dut(WIDTH):
                runner.build(parameters={"WIDTH": WIDTH})
            """,
        },
        {"WIDTH"},
    )

    assert plan.unresolved is True
    assert plan.configurations == ()
    assert any("parametrized more than once" in reason for reason in plan.unresolved_reasons)


def test_exact_sweep_plan_does_not_count_build_in_short_circuited_boolop():
    plan = _cvdp_exact_parameter_sweep_plan(
        {
            "src/test_runner.py": """
            False and runner.build(parameters={"WIDTH": 99})
            runner.build(parameters={"WIDTH": 8})
            """,
        },
        {"WIDTH"},
    )

    assert plan.unresolved is False
    assert plan.configurations == ((('WIDTH', 8),),)


def test_exact_sweep_plan_does_not_apply_short_circuited_dict_update():
    plan = _cvdp_exact_parameter_sweep_plan(
        {
            "src/test_runner.py": """
            parameters = {"WIDTH": 8}
            False and parameters.update({"WIDTH": 99})
            runner.build(parameters=parameters)
            """,
        },
        {"WIDTH"},
    )

    assert plan.unresolved is False
    assert plan.configurations == ((('WIDTH', 8),),)


@pytest.mark.parametrize(
    "harness",
    [
        """
        for width in [8, 16]:
            break
            runner.build(parameters={"WIDTH": width})
        runner.build(parameters={"WIDTH": 4})
        """,
        """
        for width in [8, 16]:
            continue
            runner.build(parameters={"WIDTH": width})
        runner.build(parameters={"WIDTH": 4})
        """,
        """
        def test_dead_build():
            return
            runner.build(parameters={"WIDTH": 8})
        runner.build(parameters={"WIDTH": 4})
        """,
    ],
    ids=["break", "continue", "return"],
)
def test_exact_sweep_plan_never_claims_unreachable_post_terminator_builds(
    harness: str,
):
    plan = _cvdp_exact_parameter_sweep_plan(
        {"src/test_runner.py": harness},
        {"WIDTH"},
    )

    # Supporting the terminator precisely and conservatively rejecting that
    # control flow are both sound.  Silently reporting dead configurations as
    # executed sweep points is not.
    if plan.unresolved:
        assert plan.unresolved_reasons
    else:
        assert plan.configurations == ((('WIDTH', 4),),)


def test_exact_sweep_plan_propagates_loop_dict_mutation_to_following_build():
    plan = _cvdp_exact_parameter_sweep_plan(
        {
            "src/test_runner.py": """
            parameters = {"WIDTH": 4}
            for width in [8, 16]:
                parameters["WIDTH"] = width
            runner.build(parameters=parameters)
            """,
        },
        {"WIDTH"},
    )

    assert plan.unresolved is False
    assert plan.configurations == ((('WIDTH', 16),),)


def test_exact_sweep_plan_rejects_pytest_indirect_parameterization():
    plan = _cvdp_exact_parameter_sweep_plan(
        {
            "src/test_runner.py": """
            @pytest.mark.parametrize("WIDTH", [8, 16], indirect=True)
            def test_dut(WIDTH):
                runner.build(parameters={"WIDTH": WIDTH})
            """,
        },
        {"WIDTH"},
    )

    # With indirect=True pytest passes each literal through a fixture.  The
    # fixture can transform it arbitrarily, so [8, 16] is not an exact build
    # matrix unless fixture semantics are also interpreted.
    assert plan.unresolved is True
    assert plan.unresolved_reasons


def test_exact_sweep_plan_preserves_dict_constructor_keyword_precedence():
    plan = _cvdp_exact_parameter_sweep_plan(
        {
            "src/test_runner.py": """
            parameters = dict({"WIDTH": 8}, WIDTH=16)
            runner.build(parameters=parameters)
            """,
        },
        {"WIDTH"},
    )

    assert plan.unresolved is False
    assert plan.configurations == ((('WIDTH', 16),),)


@pytest.mark.parametrize(
    "harness",
    [
        """
        widths = [8]
        widths.append(16)
        runner.build(parameters={"WIDTH": widths[-1]})
        """,
        """
        parameters = {"WIDTH": 8}
        helper.mutate(parameters)
        runner.build(parameters=parameters)
        """,
        """
        for width in {10, 1}:
            pass
        runner.build(parameters={"WIDTH": width})
        """,
        """
        WIDTH = 8
        def mutate_width():
            global WIDTH
            WIDTH = 16
        mutate_width()
        runner.build(parameters={"WIDTH": WIDTH})
        """,
        """
        WIDTHS = [8]
        def test_first():
            runner.build(parameters={"WIDTH": WIDTHS[0]})
            WIDTHS[0] = 16
        def test_second():
            runner.build(parameters={"WIDTH": WIDTHS[0]})
        """,
    ],
    ids=[
        "list-mutation",
        "unknown-mapping-method",
        "set-order",
        "local-global-helper",
        "cross-test-global-list",
    ],
)
def test_exact_sweep_plan_fails_closed_on_unmodeled_mutable_state(harness: str):
    plan = _cvdp_exact_parameter_sweep_plan(
        {"src/test_runner.py": harness},
        {"WIDTH"},
    )

    assert plan.unresolved is True
    assert plan.unresolved_reasons


def test_parameterized_top_without_executable_build_is_not_called_a_default_sweep():
    plan = _cvdp_exact_parameter_sweep_plan(
        {"src/test_runner.py": "def test_unrelated(): assert True"},
        {"WIDTH"},
    )

    assert plan.unresolved is True
    assert plan.configurations == ()


def test_real_evaluator_parameter_sweep_passes_each_config_and_reuses_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    evaluator = Evaluator(
        project_root=PROJECT_ROOT,
        enable_synth=True,
        enable_pnr=False,
        ppa_cache_dir=tmp_path / "ppa-cache",
        ppa_workers=2,
    )
    both_started = threading.Barrier(2, timeout=5)
    calls: Counter[tuple[tuple[str, int], ...]] = Counter()
    workspaces: dict[tuple[tuple[str, int], ...], Path] = {}
    call_lock = threading.Lock()

    def fake_run_synthesis(
        prob_id: str,
        sv_code: str,
        top_module: str,
        run_dir: Path,
        *,
        parameters: dict[str, int] | None = None,
        synth_dir: Path | None = None,
    ) -> dict:
        config = tuple(sorted((parameters or {}).items()))
        assert prob_id == "dut"
        assert top_module == "dut"
        assert "module dut" in sv_code
        assert synth_dir is not None
        assert run_dir == synth_dir.parent
        with call_lock:
            calls[config] += 1
            workspaces[config] = synth_dir
        # This is a deterministic concurrency assertion: serialized execution
        # times out instead of merely becoming a slow test.
        both_started.wait()
        width = dict(config)["WIDTH"]
        return {
            "synth_pass": True,
            "area_um2": float(width),
            "cell_count": width * 2,
            "wns_ns": 1.0 / width,
            "power_uw": float(width) / 10,
        }

    monkeypatch.setattr(evaluator, "_run_synthesis", fake_run_synthesis)
    configurations = (
        (("WIDTH", 8), ("DEPTH", 2)),
        (("WIDTH", 16), ("DEPTH", 4)),
    )
    rtl = """
    module dut #(parameter WIDTH = 8, DEPTH = 2) (
        input logic [WIDTH-1:0] x,
        output logic [WIDTH-1:0] y
    );
        assign y = x;
    endmodule
    """

    try:
        first = evaluator.run_parameterized_ppa(
            sv_code=rtl,
            top_module="dut",
            configurations=configurations,
        )

        expected_configs = {
            (("DEPTH", 2), ("WIDTH", 8)),
            (("DEPTH", 4), ("WIDTH", 16)),
        }
        assert set(calls) == expected_configs
        assert all(count == 1 for count in calls.values())
        assert set(workspaces) == expected_configs
        assert len(set(workspaces.values())) == 2
        assert first["synth_pass"] is True
        assert first["synth_status"] == "finite_parameter_sweep_passed"
        assert first["area_um2"] == 16.0
        assert first["parameter_sweep_cache_hits"] == 0
        assert first["verification_evidence"][0]["kind"] == "finite_parameter_sweep"
        assert all(
            point["evidence"]["kind"] == "finite_parameter_sweep"
            and point["evidence"]["theorem"] is None
            for point in first["parameter_sweep_results"]
        )

        second = evaluator.run_parameterized_ppa(
            sv_code=rtl,
            top_module="dut",
            configurations=tuple(reversed(configurations)),
        )

        assert all(count == 1 for count in calls.values())
        assert second["synth_pass"] is True
        assert second["parameter_sweep_cache_hits"] == 2
        assert all(point["cache_hit"] for point in second["parameter_sweep_results"])
    finally:
        evaluator.parameterized_ppa_runner.close()


def test_corners_imply_effective_pnr_for_cli_and_evaluator():
    args = SimpleNamespace(
        corners=True,
        pnr=False,
        synth=False,
        synth_feedback=False,
        ppa_opt=False,
        arch_explore=False,
        gls=False,
        drc=False,
        lvs=False,
    )

    normalized = search._apply_implied_stage_flags(args)

    assert normalized is args
    assert args.pnr is True
    assert args.synth is True
    # All existing CLI display/statistics/summary paths consume args.pnr, so
    # normalization must happen before any of them run.
    assert args.pnr or args.drc or args.lvs

    evaluator = Evaluator(
        project_root=PROJECT_ROOT,
        enable_corners=True,
    )
    assert evaluator.enable_synth is True
    assert evaluator.enable_pnr is True
    assert evaluator.enable_corners is True


@pytest.mark.parametrize(
    ("sv_code", "expected_clock"),
    [
        (
            """
            module dut(input logic clock, input logic x, output logic y);
                assign y = x;
            endmodule
            """,
            "clock",
        ),
        (
            """
            module dut(input logic aclk, input logic x, output logic y);
                assign y = x;
            endmodule
            """,
            "aclk",
        ),
        (
            """
            module helper(input logic clk, input logic x, output logic y);
                assign y = x;
            endmodule
            module dut(input logic x, output logic y);
                assign y = x;
            endmodule
            """,
            None,
        ),
    ],
    ids=("clock", "aclk", "helper-clk-only"),
)
def test_multi_corner_sta_uses_selected_top_clock_detection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    sv_code: str,
    expected_clock: str | None,
):
    def fake_run_docker_command(**_kwargs):
        return {"success": True, "stdout": "mock STA", "stderr": ""}

    tools_module = ModuleType("tools")
    tools_module.__path__ = []  # type: ignore[attr-defined]
    run_docker_module = ModuleType("tools.run_docker")
    run_docker_module.run_docker_command = fake_run_docker_command  # type: ignore[attr-defined]
    tools_module.run_docker = run_docker_module  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "tools", tools_module)
    monkeypatch.setitem(sys.modules, "tools.run_docker", run_docker_module)
    monkeypatch.setattr(
        sys.modules[Evaluator.__module__],
        "PVT_CORNERS",
        [{"name": "tt", "label": "TT", "lib": "dummy.lib"}],
    )

    evaluator = Evaluator(project_root=PROJECT_ROOT, enable_corners=True)
    workspace = tmp_path / "pvt-point"
    workspace.mkdir()

    result = evaluator._run_multi_corner_sta(
        "dut", sv_code, "dut", workspace, []
    )

    assert evaluator._selected_top_clock_port(sv_code, "dut") == expected_clock
    assert result["corners_pass"] is True
    tcl = (workspace / "sta_tt.tcl").read_text()
    assert ("report_checks -path_delay max" in tcl) is (expected_clock is not None)
    assert ("report_checks -path_delay min" in tcl) is (expected_clock is not None)


def test_run_synthesis_writes_sorted_orfs_top_parameter_overrides(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    captured: dict[str, object] = {}

    def fake_run_docker_command(**kwargs):
        captured.update(kwargs)
        return {"success": True, "stdout": "mock synthesis", "stderr": ""}

    tools_module = ModuleType("tools")
    tools_module.__path__ = []  # type: ignore[attr-defined]
    run_docker_module = ModuleType("tools.run_docker")
    run_docker_module.run_docker_command = fake_run_docker_command  # type: ignore[attr-defined]
    tools_module.run_docker = run_docker_module  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "tools", tools_module)
    monkeypatch.setitem(sys.modules, "tools.run_docker", run_docker_module)

    evaluator = Evaluator(project_root=PROJECT_ROOT)
    workspace = tmp_path / "synth-point"
    result = evaluator._run_synthesis(
        "dut",
        "module dut #(parameter WIDTH = 8, DEPTH = 2) (); endmodule",
        "dut",
        tmp_path,
        parameters={"WIDTH": 16, "DEPTH": 4},
        synth_dir=workspace,
    )

    assert result["synth_pass"] is True
    config = (workspace / "config.mk").read_text()
    assert "export VERILOG_TOP_PARAMS = DEPTH 4 WIDTH 16\n" in config
    assert captured["command"] == "make -B DESIGN_CONFIG=/workspace/config.mk synth"
    assert captured["workspace_path"] == str(workspace)


def test_run_pnr_preserves_sorted_orfs_top_parameter_overrides(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    captured: dict[str, object] = {}

    def fake_run_docker_command(**kwargs):
        captured.update(kwargs)
        return {"success": True, "stdout": "mock pnr", "stderr": ""}

    tools_module = ModuleType("tools")
    tools_module.__path__ = []  # type: ignore[attr-defined]
    run_docker_module = ModuleType("tools.run_docker")
    run_docker_module.run_docker_command = fake_run_docker_command  # type: ignore[attr-defined]
    tools_module.run_docker = run_docker_module  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "tools", tools_module)
    monkeypatch.setitem(sys.modules, "tools.run_docker", run_docker_module)

    evaluator = Evaluator(project_root=PROJECT_ROOT)
    workspace = tmp_path / "pnr-point"
    workspace.mkdir()
    result = evaluator._run_pnr(
        "dut",
        "module dut #(parameter WIDTH = 8, DEPTH = 2) (); endmodule",
        "dut",
        tmp_path,
        parameters={"WIDTH": 16, "DEPTH": 4},
        synth_dir=workspace,
    )

    assert result["pnr_pass"] is True
    config = (workspace / "config.mk").read_text()
    assert "export VERILOG_TOP_PARAMS = DEPTH 4 WIDTH 16\n" in config
    assert captured["command"] == "make DESIGN_CONFIG=/workspace/config.mk finish"
    assert captured["workspace_path"] == str(workspace)


@pytest.mark.parametrize("stage", ["synth", "pnr"])
def test_orfs_top_parameter_with_dollar_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
):
    captured: dict[str, object] = {}

    def fake_run_docker_command(**kwargs):
        captured.update(kwargs)
        return {"success": True, "stdout": "mock pnr", "stderr": ""}

    tools_module = ModuleType("tools")
    tools_module.__path__ = []  # type: ignore[attr-defined]
    run_docker_module = ModuleType("tools.run_docker")
    run_docker_module.run_docker_command = fake_run_docker_command  # type: ignore[attr-defined]
    tools_module.run_docker = run_docker_module  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "tools", tools_module)
    monkeypatch.setitem(sys.modules, "tools.run_docker", run_docker_module)

    evaluator = Evaluator(project_root=PROJECT_ROOT)
    workspace = tmp_path / f"{stage}-point"
    workspace.mkdir()
    method = evaluator._run_synthesis if stage == "synth" else evaluator._run_pnr
    result = method(
        "dut",
        "module dut #(parameter WIDTH$RAW = 8, DEPTH = 2) (); endmodule",
        "dut",
        tmp_path,
        parameters={"WIDTH$RAW": 16, "DEPTH": 4},
        synth_dir=workspace,
    )

    error_key = "synth_error" if stage == "synth" else "pnr_error"
    assert "contains '$'" in result[error_key]
    assert captured == {}
    assert not (workspace / "config.mk").exists()


@pytest.mark.parametrize(
    ("stage", "stage_result"),
    [
        ("gds", {"gds_generated": False}),
        ("drc", {"drc_pass": False, "drc_violations": 1}),
        ("lvs", {"lvs_pass": False, "lvs_error": "mock mismatch"}),
        ("corners", {"corners_pass": False}),
    ],
)
def test_configured_physical_verification_failure_is_not_cached(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
    stage_result: dict[str, object],
):
    evaluator = Evaluator(
        project_root=PROJECT_ROOT,
        enable_synth=True,
        enable_pnr=True,
        enable_drc=stage == "drc",
        enable_lvs=stage == "lvs",
        enable_corners=stage == "corners",
        ppa_cache_dir=tmp_path / f"{stage}-cache",
        ppa_workers=1,
    )
    calls = Counter()

    def fake_synthesis(*_args, **_kwargs):
        calls["synth"] += 1
        return {"synth_pass": True, "area_um2": 8.0}

    def fake_pnr(*_args, **_kwargs):
        calls["pnr"] += 1
        return {
            "pnr_pass": True,
            "gds_generated": True,
            **stage_result,
        }

    monkeypatch.setattr(evaluator, "_run_synthesis", fake_synthesis)
    monkeypatch.setattr(evaluator, "_run_pnr", fake_pnr)
    kwargs = {
        "sv_code": (
            "module dut #(parameter WIDTH = 8) (); endmodule"
        ),
        "top_module": "dut",
        "configurations": ((('WIDTH', 8),),),
    }

    try:
        first = evaluator.run_parameterized_ppa(**kwargs)
        second = evaluator.run_parameterized_ppa(**kwargs)

        assert first["parameter_sweep_results"][0]["success"] is False
        assert first["parameter_sweep_results"][0]["cache_hit"] is False
        assert first["verification_evidence"][0]["status"] == "failed"
        assert second["parameter_sweep_results"][0]["success"] is False
        assert second["parameter_sweep_results"][0]["cache_hit"] is False
        assert second["parameter_sweep_cache_hits"] == 0
        assert calls == Counter({"synth": 2, "pnr": 2})
    finally:
        evaluator.parameterized_ppa_runner.close()


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
