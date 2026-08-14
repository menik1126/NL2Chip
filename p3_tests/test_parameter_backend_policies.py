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
    evaluate_formal_parameter_policy,
    formal_policy_is_required_failure,
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
