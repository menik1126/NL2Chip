from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "agent"))

from cktarchon.diagnostics import (  # noqa: E402
    build_lean_diagnostics,
    format_lean_diagnostics,
    lean_diagnostic_signature,
    parse_lean_error_text,
)
from cktarchon.harness import AnthropicHarnessRunner  # noqa: E402
from evaluator import _record_lean_compile_failure  # noqa: E402
from search import build_sim_feedback, compact_repair_feedback, lean_repair_playbook  # noqa: E402


OPAQUE_LOWERING_ERROR = """Cannot instantiate List.foldl.match_1: not a hardware module definition
  List.foldl.match_1.{v, max (succ u) (succ v), u} _uniq.591805 _uniq.591806
  (fun (x._@.Init.Prelude.1478953397._hygCtx._hyg.16 : List.{v} _uniq.591806) =>
    _uniq.591805 -> _uniq.591805)
Action: replace the List.foldl helper with a synthesizable fixed-shape construction.
"""


def _opaque_errors():
    return [{
        "severity": "error",
        "pos": {"line": 64, "column": 0},
        "data": OPAQUE_LOWERING_ERROR,
    }]


def test_opaque_lowering_diagnostic_keeps_cause_and_omits_internal_term():
    records = build_lean_diagnostics(_opaque_errors())
    rendered = format_lean_diagnostics(records)

    assert records[0]["code"] == "unsupported_hardware_definition"
    assert "Cannot instantiate List.foldl.match_1" in rendered
    assert "[internal Lean term omitted]" in rendered
    assert "_hygCtx" not in rendered
    assert len(rendered) < 1200


def test_loop_diagnostic_gets_structural_repair_guidance():
    hint = lean_repair_playbook([{"code": "invalid_signal_loop"}])

    assert "return only its feedback register" in hint
    assert "outside that loop" in hint


def test_hardware_type_diagnostic_rejects_bundleall_top_level_output():
    hint = lean_repair_playbook([{"code": "lean_hardware_type_inference"}])

    assert "bundleAll!" in hint
    assert "bundle2" in hint


def test_raw_error_parser_deduplicates_repl_location_aliases():
    raw = """[error 60:21] typeclass instance problem is stuck
  HXor (Signal dom (BitVec 8)) rhs out
[error line 60:21] typeclass instance problem is stuck
  HXor (Signal dom (BitVec 8)) rhs out
"""
    records = parse_lean_error_text(raw)

    assert len(records) == 1
    assert records[0]["location"] == "60:21"
    assert records[0]["code"] == "lean_typeclass_stuck"
    assert lean_diagnostic_signature(records)


def test_evaluator_preserves_raw_compile_error_and_records_concise_sidecar(tmp_path: Path):
    result = {"diagnostics": []}
    raw = "[error 64:0] " + OPAQUE_LOWERING_ERROR

    _record_lean_compile_failure(
        result,
        run_dir=tmp_path,
        prob_id="hamming_tx",
        raw_text=raw,
        errors=_opaque_errors(),
    )

    assert result["failure_stage"] == "lean_elaboration"
    assert result["diagnostic_signature"]
    assert "_hygCtx" not in result["detail"]
    raw_path = Path(result["raw_diagnostic_path"])
    assert raw_path.read_text(encoding="utf-8") == raw
    assert result["diagnostics"][-1]["code"] == "lean_compile_failed"

    next_result = {"diagnostics": []}
    _record_lean_compile_failure(
        next_result,
        run_dir=tmp_path,
        prob_id="hamming_tx",
        raw_text=raw,
        errors=_opaque_errors(),
    )
    assert Path(next_result["raw_diagnostic_path"]).name == "lean_compile_02.txt"


def test_repair_feedback_prioritizes_actionable_lean_diagnostics(tmp_path: Path):
    records = build_lean_diagnostics(_opaque_errors())
    result = {
        "compile_pass": False,
        "sv_extracted": False,
        "lint_pass": False,
        "sim_status": "not_run",
        "sim_mismatches": -1,
        "synth_pass": False,
        "detail": "Compile failed:\n" + format_lean_diagnostics(records),
        "lean_diagnostics": records,
    }

    feedback = build_sim_feedback(
        prob_id="hamming_tx",
        result=result,
        iteration=2,
        history=[],
        run_dir=tmp_path,
    )
    compact = compact_repair_feedback(feedback)

    assert "### Actionable Lean Diagnostics" in compact
    assert "Cannot instantiate List.foldl.match_1" in compact
    assert "_hygCtx" not in compact


def test_lean_check_output_does_not_duplicate_structured_errors():
    result = SimpleNamespace(
        passed=False,
        complete=False,
        elapsed=0.1,
        summary="FAILED",
        error_text="[error 64:0] " + OPAQUE_LOWERING_ERROR,
        errors=_opaque_errors(),
        warnings=[],
    )

    runner = SimpleNamespace(required_verilog_modules=())
    rendered = AnthropicHarnessRunner._format_lean_result(runner, result)
    assert rendered.count("Cannot instantiate List.foldl.match_1") == 1
    assert "_hygCtx" not in rendered


def test_raw_error_parser_extracts_lake_build_error_after_warnings():
    raw = """warning: ignored\nerror: Generated/demo.lean:50:24: expected ';' or line break\nerror: Generated/demo.lean:81:9: Unknown constant `Signal.not`\n"""
    records = parse_lean_error_text(raw)

    assert [record["location"] for record in records] == ["50:24", "81:9"]
    assert records[0]["code"] == "lean_syntax_error"
    assert "warning: ignored" not in records[0]["message"]

def test_hardware_type_feedback_marks_generated_lean_source(tmp_path: Path):
    errors = [{
        "pos": {"line": 4, "column": 0},
        "data": "Cannot infer hardware type from _uniq.17",
    }]
    records = build_lean_diagnostics(errors)
    result = {
        "compile_pass": False,
        "sv_extracted": False,
        "lint_pass": False,
        "sim_status": "not_run",
        "sim_mismatches": -1,
        "detail": "Compile failed",
        "lean_diagnostics": records,
    }
    feedback = build_sim_feedback(
        prob_id="demo",
        result=result,
        iteration=0,
        history=[],
        run_dir=tmp_path,
        current_lean="line one\nline two\nline three\nstate expression\nline five",
    )

    assert records[0]["code"] == "lean_hardware_type_inference"
    assert "### Lean Source Context" in feedback
    compact = compact_repair_feedback(feedback)
    assert "### Lean Source Context" in compact
    assert ">    4 | state expression" in compact
