from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CERTIFIER = PROJECT_ROOT / ".lake" / "build" / "bin" / "sparkle-signal-comb-certify"
NONCE = "0123456789abcdef0123456789abcdef"


@pytest.fixture(scope="module", autouse=True)
def _build_certifier_and_proofs() -> None:
    subprocess.run(
        [
            "lake",
            "build",
            "sparkle-signal-comb-certify",
            "Tests.SignalCombCertificateTests",
        ],
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


def test_certifies_only_original_indexed_signal_comb_subset() -> None:
    completed, record = _run("--nonce", NONCE)

    assert completed.returncode == 0
    assert record["status"] == "proved"
    assert record["schema_version"] == 2
    assert record["scope"] == "proof_carrying_signal_comb_subset_to_core_ir"
    assert record["verification_nonce"] == NONCE
    assert record["kernel_checked"] is True
    assert record["kernel_bridge_checked"] is True
    assert record["comb_ast_to_core_ir_certificate_reused"] is True
    assert record["extractor_output_requires_kernel_checked_bridge"] is True
    assert record["meta_reifier_trusted"] is False
    assert record["meta_reifier_correctness_claimed"] is False
    assert record["original_indexed_carriers_checked"] is True
    assert record["unary_original_index_checked"] is True
    assert record["binary_original_index_checked"] is True
    assert record["proof_closure_checked"] is True
    assert record["domain_witness_definitions_checked"] is True
    assert record[
        "ordinary_signal_proof_carrying_comb_subset_correctness_claimed"
    ] is True
    assert record["all_certificates_nonvacuity_checked"] is True
    assert record["non_vacuity_checked"] is True
    assert record["arbitrary_positive_width_checked"] is True
    assert record["non_vacuity_scope"] == (
        "all_certificates_all_positive_natural_widths_all_times"
    )

    excluded_claims = (
        "signal_frontend_correctness_claimed",
        "general_signal_frontend_correctness_claimed",
        "sequential_signal_correctness_claimed",
        "memory_correctness_claimed",
        "hierarchy_correctness_claimed",
        "optimizer_correctness_claimed",
        "verilog_emitter_correctness_claimed",
    )
    for field in excluded_claims:
        assert record[field] is False

    allowed_axioms = {"propext", "Classical.choice", "Quot.sound"}
    for field in (
        "axioms",
        "width_axioms",
        "all_certificates_nonvacuity_axioms",
        "domain_inhabited_axioms",
        "domain_witness_axioms",
        "unary_domain_witness_axioms",
        "binary_domain_witness_axioms",
        "unary_domain_witness_bridge_axioms",
        "binary_domain_witness_bridge_axioms",
        "production_well_formed_axioms",
    ):
        assert set(record[field]) <= allowed_axioms
        assert "sorryAx" not in record[field]

    constants = {item["role"]: item["name"]
                 for item in record["trusted_constants"]}
    expected_helper_constants = {
        "same_width_parameter_name": (
            "Sparkle.Compiler.SignalCombCorrectness.sameWidthParameterName"
        ),
        "same_width_dimension": (
            "Sparkle.Compiler.SignalCombCorrectness.sameWidthDim"
        ),
        "same_width_configuration": (
            "Sparkle.Compiler.SignalCombCorrectness.sameWidthConfig"
        ),
        "unary_same_width_design": (
            "Sparkle.Compiler.SignalCombCorrectness.unarySameWidthDesign"
        ),
        "binary_same_width_design": (
            "Sparkle.Compiler.SignalCombCorrectness.binarySameWidthDesign"
        ),
        "packed_signal_sample": (
            "Sparkle.Compiler.SignalCombCorrectness.packedSignalSample"
        ),
        "unary_same_width_input_environment": (
            "Sparkle.Compiler.SignalCombCorrectness.unarySameWidthInputEnv"
        ),
        "binary_same_width_input_environment": (
            "Sparkle.Compiler.SignalCombCorrectness.binarySameWidthInputEnv"
        ),
    }
    for role, name in expected_helper_constants.items():
        assert constants[role] == name

    assert record["unary_domain_witness"].endswith(".unarySignalCombWitness")
    assert record["binary_domain_witness"].endswith(".binarySignalCombWitness")
    assert len(record["production_entries"]) == 2
    assert any(name.endswith(".CertifiedUnarySignalCombDesign.compile")
               for name in record["production_entries"])
    assert any(name.endswith(".CertifiedBinarySignalCombDesign.compile")
               for name in record["production_entries"])

    base = record["base_comb_certificate"]
    assert base["status"] == "proved"
    assert base["scope"] == "supported_comb_ast_to_core_ir"
    assert base["signal_frontend_correctness_claimed"] is False
    assert record["theorem"].endswith(".signalCombCompiler_correct")
    assert record["width_theorem"].endswith(
        ".signalCombCompiler_correct_for_width"
    )
    assert record["all_certificates_nonvacuity_theorem"].endswith(
        ".signalCombAllCertificates_nonvacuous"
    )
    assert record["domain_inhabited_theorem"].endswith(
        ".signalCombCorrectnessDomain_inhabited"
    )


@pytest.mark.parametrize(
    "args",
    [
        (),
        ("--nonce", "BAD"),
        ("--nonce", NONCE, "--nonce", NONCE),
        ("--module", "Tests.SparkleCertifyFixture", "--nonce", NONCE),
        ("--theorem", "Nat.add_zero", "--nonce", NONCE),
        ("--reifier", "untrusted", "--nonce", NONCE),
    ],
)
def test_rejects_missing_invalid_or_caller_selected_trust_inputs(
    args: tuple[str, ...],
) -> None:
    completed, record = _run(*args)

    assert completed.returncode == 2
    assert record["status"] == "error"
    assert record["error_kind"] == "usage"
    assert "sparkle-signal-comb-certify --nonce" in record["message"]
