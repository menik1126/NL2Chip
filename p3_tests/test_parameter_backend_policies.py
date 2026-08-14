from __future__ import annotations

import sys
import shutil
import subprocess
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "agent"))

from cvdp_specialization import FiniteParameterPlan, SpecializationCase  # noqa: E402
from parameter_backends import (  # noqa: E402
    bind_top_parameter_defaults,
    evaluate_formal_parameter_policy,
    formal_policy_is_required_failure,
    ppa_manifest_failure_stage,
    ppa_policy_is_required_failure,
    run_ppa_parameter_policy,
)


def _plan() -> FiniteParameterPlan:
    return FiniteParameterPlan(
        design_name="generic_xor",
        parameter_names=("WIDTH",),
        cases=tuple(
            SpecializationCase(
                parameters=(("WIDTH", width),),
                module_name=f"unused_{width}",
            )
            for width in (3, 17, 65)
        ),
    )


def test_auto_formal_policy_does_not_call_elaboration_a_functional_proof():
    manifest = evaluate_formal_parameter_policy(
        lean_source="def generic_xor {WIDTH : Nat} := WIDTH",
        lean_complete=True,
        plan=_plan(),
        requested_policy="auto",
        contract=None,
    )
    assert manifest["status"] == "unsupported"
    assert manifest["coverage"] == "none"
    assert manifest["family_covered"] is False
    assert "Lean elaboration alone is not functional correctness evidence" in (
        manifest["diagnostics"][0]
    )
    assert formal_policy_is_required_failure(manifest) is False


def test_generic_theorem_covers_the_whole_family_when_lean_verified_it():
    source = """
        theorem generic_xor_correct {WIDTH : Nat} (x : BitVec WIDTH) : x = x := by
          rfl
    """
    manifest = evaluate_formal_parameter_policy(
        lean_source=source,
        lean_complete=True,
        plan=_plan(),
        requested_policy="generic",
        contract={
            "scope": "identity_functional_correctness",
            "generic_theorem": "generic_xor_correct",
        },
    )
    assert manifest["status"] == "passed"
    assert manifest["coverage"] == "full_parameter_family"
    assert manifest["family_covered"] is True
    assert {row["formal_status"] for row in manifest["cases"]} == {
        "covered_by_generic_theorem"
    }
    assert formal_policy_is_required_failure(manifest) is False


def test_generic_theorem_must_actually_reference_every_parameter():
    manifest = evaluate_formal_parameter_policy(
        lean_source="theorem width8_correct : (8 : Nat) = 8 := by rfl",
        lean_complete=True,
        plan=_plan(),
        requested_policy="generic",
        contract={"generic_theorem": "width8_correct"},
    )
    assert manifest["status"] == "incomplete"
    assert manifest["family_covered"] is False
    assert "does not quantify/reference" in manifest["diagnostics"][0]
    assert formal_policy_is_required_failure(manifest) is True


def test_unresolved_sorry_invalidates_all_formal_coverage():
    source = """
        theorem generic_xor_correct {WIDTH : Nat} (x : BitVec WIDTH) : x = x := by
          sorry
    """
    manifest = evaluate_formal_parameter_policy(
        lean_source=source,
        lean_complete=False,
        plan=_plan(),
        requested_policy="generic",
        contract={"generic_theorem": "generic_xor_correct"},
    )
    assert manifest["status"] == "incomplete"
    assert manifest["coverage"] == "none"
    assert all(row["formal_status"] == "not_run" for row in manifest["cases"])


def test_per_configuration_policy_requires_a_theorem_for_every_public_case():
    source = """
        theorem xor_w3_correct : True := by trivial
        theorem xor_w17_correct : True := by trivial
        theorem xor_w65_correct : True := by trivial
    """
    manifest = evaluate_formal_parameter_policy(
        lean_source=source,
        lean_complete=True,
        plan=_plan(),
        requested_policy="per_configuration",
        contract={"case_theorem_template": "xor_w{WIDTH}_correct"},
    )
    assert manifest["status"] == "passed"
    assert manifest["coverage"] == "all_public_configurations"
    assert [row["theorem"] for row in manifest["cases"]] == [
        "xor_w3_correct", "xor_w17_correct", "xor_w65_correct",
    ]


def test_partial_per_configuration_proofs_are_reported_as_partial_not_family():
    source = """
        theorem xor_w3_correct : True := by trivial
        theorem xor_w17_correct : True := by trivial
    """
    manifest = evaluate_formal_parameter_policy(
        lean_source=source,
        lean_complete=True,
        plan=_plan(),
        requested_policy="per_configuration",
        contract={"case_theorem_template": "xor_w{WIDTH}_correct"},
    )
    assert manifest["status"] == "incomplete"
    assert manifest["coverage"] == "partial_configuration_set"
    assert manifest["family_covered"] is False
    assert [row["formal_status"] for row in manifest["cases"]] == [
        "passed", "passed", "missing",
    ]


@pytest.mark.skipif(shutil.which("lake") is None, reason="Lean/lake unavailable")
def test_formal_policy_fixture_is_verified_by_the_real_lean_checker():
    proof_file = PROJECT_ROOT / "p3_tests" / "FormalPolicyProofs.lean"
    proc = subprocess.run(
        ["lake", "env", "lean", str(proof_file)],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    source = proof_file.read_text()

    generic = evaluate_formal_parameter_policy(
        lean_source=source,
        lean_complete=True,
        plan=_plan(),
        requested_policy="generic",
        contract={"generic_theorem": "p3_identity_generic_correct"},
    )
    assert generic["status"] == "passed"
    assert generic["coverage"] == "full_parameter_family"

    concrete = evaluate_formal_parameter_policy(
        lean_source=source,
        lean_complete=True,
        plan=_plan(),
        requested_policy="per_configuration",
        contract={"case_theorem_template": "p3_identity_w{WIDTH}_correct"},
    )
    assert concrete["status"] == "passed"
    assert concrete["coverage"] == "all_public_configurations"


GENERIC_PPA_SV = """
module generic_xor_inner #(
    parameter integer WIDTH = 99
) (
    input logic [WIDTH-1:0] lhs,
    output logic [WIDTH-1:0] out
);
    assign out = lhs;
endmodule

module generic_xor #(
    parameter integer WIDTH = 8,
    parameter integer FIXED = 4
) (
    input logic [WIDTH-1:0] lhs,
    output logic [WIDTH-1:0] out
);
    generic_xor_inner #(.WIDTH(WIDTH)) impl (.lhs(lhs), .out(out));
endmodule
"""


def test_ppa_binding_changes_only_the_selected_top_default():
    bound, diagnostics = bind_top_parameter_defaults(
        GENERIC_PPA_SV,
        top_module="generic_xor",
        parameters={"WIDTH": 17},
    )
    assert diagnostics == []
    assert bound is not None
    assert "module generic_xor_inner #(\n    parameter integer WIDTH = 99" in bound
    top = bound[bound.index("module generic_xor #("):]
    assert "parameter integer WIDTH = 17" in top
    assert "parameter integer FIXED = 4" in top
    assert ".WIDTH(WIDTH)" in top

    with_decoy = "// module generic_xor #(parameter WIDTH = 1)\n" + GENERIC_PPA_SV
    rebound, diagnostics = bind_top_parameter_defaults(
        with_decoy,
        top_module="generic_xor",
        parameters={"WIDTH": 65},
    )
    assert diagnostics == []
    assert rebound is not None
    assert rebound.startswith("// module generic_xor #(parameter WIDTH = 1)")
    assert "parameter integer WIDTH = 65" in rebound[
        rebound.rindex("module generic_xor #("):
    ]


def test_ppa_policy_runs_every_case_in_an_isolated_directory(tmp_path: Path):
    synth_calls = []
    pnr_calls = []

    def synth(case_id, concrete_sv, top_module, case_dir):
        width = _plan().cases[len(synth_calls)].values["WIDTH"]
        synth_calls.append((case_id, concrete_sv, top_module, case_dir))
        assert f"parameter integer WIDTH = {width}" in concrete_sv[
            concrete_sv.index("module generic_xor #("):
        ]
        return {
            "synth_pass": True,
            "area_um2": float(width * 10),
            "cell_count": width,
            "wns_ns": None,
            "power_uw": None,
        }

    def pnr(case_id, concrete_sv, top_module, case_dir):
        width = _plan().cases[len(pnr_calls)].values["WIDTH"]
        pnr_calls.append((case_id, concrete_sv, top_module, case_dir))
        return {
            "pnr_pass": True,
            "drc_pass": True,
            "lvs_pass": True,
            "wns_ns": -width / 100.0,
            "power_uw": float(width),
        }

    manifest = run_ppa_parameter_policy(
        sv_code=GENERIC_PPA_SV,
        top_module="generic_xor",
        prob_id="ppa_xor",
        plan=_plan(),
        output_dir=tmp_path / "ppa",
        synth_runner=synth,
        pnr_runner=pnr,
        require_drc=True,
        require_lvs=True,
    )

    assert manifest["status"] == "passed"
    assert manifest["coverage"] == "all_public_configurations"
    assert manifest["family_covered"] is True
    assert manifest["synthesis_family_covered"] is True
    assert manifest["pnr_family_covered"] is True
    assert len(synth_calls) == len(pnr_calls) == 3
    assert len({call[3] for call in synth_calls}) == 3
    assert len({row["source_sv_sha256"] for row in manifest["cases"]}) == 1
    assert len({row["concrete_sv_sha256"] for row in manifest["cases"]}) == 3
    assert [row["area_um2"] for row in manifest["cases"]] == [30.0, 170.0, 650.0]
    assert manifest["metric_ranges"]["area_um2"] == {"min": 30.0, "max": 650.0}
    assert all(Path(row["artifact_dir"]).is_dir() for row in manifest["cases"])
    assert all(
        (Path(row["artifact_dir"]) / "bound_design.sv").exists()
        for row in manifest["cases"]
    )


def test_partial_ppa_never_reuses_a_passing_case_metric(tmp_path: Path):
    widths = iter((3, 17, 65))

    def synth(_case_id, _concrete_sv, _top_module, _case_dir):
        width = next(widths)
        if width == 17:
            return {"synth_pass": False, "synth_error": "intentional failure"}
        return {
            "synth_pass": True,
            "area_um2": float(width),
            "cell_count": width,
        }

    manifest = run_ppa_parameter_policy(
        sv_code=GENERIC_PPA_SV,
        top_module="generic_xor",
        prob_id="ppa_xor",
        plan=_plan(),
        output_dir=tmp_path / "ppa",
        synth_runner=synth,
        required=True,
    )

    assert manifest["status"] == "partial"
    assert manifest["coverage"] == "partial_configuration_set"
    assert manifest["family_covered"] is False
    assert manifest["covered_case_count"] == 2
    assert manifest["cases"][1]["synth_status"] == "failed"
    assert manifest["cases"][1]["area_um2"] is None
    assert manifest["metric_ranges"]["area_um2"] == {"min": 3.0, "max": 65.0}
    assert ppa_policy_is_required_failure(manifest) is True
    assert manifest["cases"][1]["diagnostic"]["stage"] == "unsupported_backend"
    assert ppa_manifest_failure_stage(manifest) == "unsupported_backend"


def test_ppa_diagnostics_preserve_infrastructure_failures(tmp_path: Path):
    manifest = run_ppa_parameter_policy(
        sv_code=GENERIC_PPA_SV,
        top_module="generic_xor",
        prob_id="ppa_xor",
        plan=_plan(),
        output_dir=tmp_path / "ppa",
        synth_runner=lambda *_args: {
            "synth_pass": False,
            "synth_error": "ORFS Docker execution failed: docker was not found",
        },
        required=True,
    )
    assert manifest["status"] == "failed"
    assert {row["diagnostic"]["stage"] for row in manifest["cases"]} == {
        "infrastructure"
    }
    assert ppa_manifest_failure_stage(manifest) == "infrastructure"

    def crash(*_args):
        raise RuntimeError("runner unavailable")

    crashed = run_ppa_parameter_policy(
        sv_code=GENERIC_PPA_SV,
        top_module="generic_xor",
        prob_id="ppa_xor",
        plan=_plan(),
        output_dir=tmp_path / "crashed_ppa",
        synth_runner=crash,
        required=True,
    )
    assert crashed["status"] == "failed"
    assert ppa_manifest_failure_stage(crashed) == "infrastructure"


def test_disabled_ppa_policy_does_not_run_the_default_configuration(tmp_path: Path):
    def unexpected_runner(*_args):
        raise AssertionError("disabled policy must not synthesize")

    manifest = run_ppa_parameter_policy(
        sv_code=GENERIC_PPA_SV,
        top_module="generic_xor",
        prob_id="ppa_xor",
        plan=_plan(),
        output_dir=tmp_path / "ppa",
        synth_runner=unexpected_runner,
        requested_policy="off",
    )
    assert manifest["status"] == "not_run"
    assert manifest["coverage"] == "none"
    assert manifest["cases"] == []
