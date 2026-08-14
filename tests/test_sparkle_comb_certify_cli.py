from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CERTIFIER = PROJECT_ROOT / ".lake" / "build" / "bin" / "sparkle-comb-certify"
NONCE = "fedcba9876543210fedcba9876543210"


@pytest.fixture(scope="module", autouse=True)
def _build_certifier_and_proofs() -> None:
    subprocess.run(
        ["lake", "build", "sparkle-comb-certify", "Tests.CombCertificateTests"],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=300,
    )


def _run(*args: str) -> tuple[subprocess.CompletedProcess[str], dict]:
    completed = subprocess.run(
        [str(CERTIFIER), *args],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.stdout.count("\n") == 1
    assert completed.stderr == ""
    return completed, json.loads(completed.stdout)


def test_certifies_only_the_fixed_supported_comb_compiler_scope() -> None:
    completed, record = _run("--nonce", NONCE)

    assert completed.returncode == 0
    assert record["status"] == "proved"
    assert record["schema_version"] == 2
    assert record["evidence_kind"] == "compiler_correctness_lean_theorem"
    assert record["scope"] == "supported_comb_ast_to_core_ir"
    assert record["kernel_checked"] is True
    assert record["compiler_correctness_claimed"] is True
    assert record["comb_ast_to_core_ir_correctness_claimed"] is True
    assert record["non_vacuity_checked"] is True
    assert record["non_vacuity_scope"] == "all_positive_natural_widths"
    assert record["domain_witness_shape_checked"] is True
    assert record["signal_frontend_correctness_claimed"] is False
    assert record["optimizer_correctness_claimed"] is False
    assert record["verilog_emitter_correctness_claimed"] is False
    assert record["verification_nonce"] == NONCE
    allowed_axioms = {"propext", "Classical.choice", "Quot.sound"}
    axiom_fields = (
        "axioms",
        "totality_axioms",
        "domain_inhabited_axioms",
        "domain_witness_supported_axioms",
        "production_correctness_axioms",
        "production_totality_axioms",
        "production_well_formed_axioms",
    )
    for field in axiom_fields:
        assert set(record[field]) <= allowed_axioms
        assert "sorryAx" not in record[field]
    assert record["theorem"].endswith(".compileComb_correct")
    assert record["totality_theorem"].endswith(".compileComb_correct_total")
    assert record["domain_inhabited_statement"].endswith(
        ".CompilerCorrectnessDomainInhabitedStatement"
    )
    assert record["domain_inhabited_theorem"].endswith(
        ".compilerCorrectnessDomain_inhabited"
    )
    assert record["domain_witness"].endswith(".compilerCorrectnessWitness")
    assert record["domain_witness_supported_theorem"].endswith(
        ".compilerCorrectnessWitness_supported"
    )
    assert record["production_entry"].endswith(".compileSupportedComb")
    assert record["production_correctness_theorem"].endswith(
        ".compileSupportedComb_correct"
    )
    assert record["production_totality_theorem"].endswith(
        ".compileSupportedComb_correct_total"
    )
    assert record["production_well_formed_theorem"].endswith(
        ".compileSupportedComb_wellFormed"
    )

    constants = {item["role"]: item["name"] for item in record["trusted_constants"]}
    assert constants["compiler"].endswith(".compileComb")
    assert constants["source_semantics"].endswith(".evalSourceDesign")
    assert constants["core_semantics"].endswith(".evalCoreModule")
    assert constants["valid_inputs"].endswith(".ValidInputs")
    assert constants["production_entry"].endswith(".compileSupportedComb")
    assert constants["domain_witness"].endswith(".compilerCorrectnessWitness")
    assert constants["domain_witness_supported_theorem"].endswith(
        ".compilerCorrectnessWitness_supported"
    )
    assert constants["domain_inhabited_theorem"].endswith(
        ".compilerCorrectnessDomain_inhabited"
    )

    executable_tcb = set(record["executable_tcb"])
    assert any(name.endswith(".compileExpr") for name in executable_tcb)
    assert any(name.endswith(".evalDim") for name in executable_tcb)
    assert any(name.endswith(".evalCoreExpr") for name in executable_tcb)
    assert any(
        name.endswith(".compilerCorrectnessWitnessConfig")
        for name in executable_tcb
    )


@pytest.mark.parametrize(
    "args",
    [
        (),
        ("--nonce", "BAD"),
        ("--nonce", NONCE, "--nonce", NONCE),
        ("--module", "Tests.SparkleCertifyFixture", "--nonce", NONCE),
        ("--theorem", "Nat.add_zero", "--nonce", NONCE),
        ("--parameters", "W", "--nonce", NONCE),
    ],
)
def test_rejects_missing_invalid_or_caller_selected_trust_inputs(
    args: tuple[str, ...],
) -> None:
    completed, record = _run(*args)

    assert completed.returncode == 2
    assert record["status"] == "error"
    assert record["error_kind"] == "usage"
    assert "sparkle-comb-certify --nonce" in record["message"]
