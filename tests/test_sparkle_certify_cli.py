from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CERTIFIER = PROJECT_ROOT / ".lake" / "build" / "bin" / "sparkle-certify"
MODULE = "Tests.SparkleCertifyFixture"
NONCE = "0123456789abcdef0123456789abcdef"


@pytest.fixture(scope="module", autouse=True)
def _build_certifier_and_fixture() -> None:
    subprocess.run(
        ["lake", "build", "sparkle-certify", MODULE],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=300,
    )


def _run(
    theorem: str, parameters: str = "W"
) -> tuple[subprocess.CompletedProcess[str], dict]:
    completed = subprocess.run(
        [
            str(CERTIFIER),
            "--module",
            MODULE,
            "--theorem",
            theorem,
            "--parameters",
            parameters,
            "--nonce",
            NONCE,
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    # The stdout contract is one JSON value and no marker/log preamble.
    assert completed.stdout.count("\n") == 1
    assert completed.stderr == ""
    return completed, json.loads(completed.stdout)


def test_certifies_kernel_checked_universal_theorem() -> None:
    completed, record = _run(f"{MODULE}.arbitraryWidth")

    assert completed.returncode == 0
    assert completed.stderr == ""
    assert record["status"] == "proved"
    assert record["evidence_kind"] == "universal_lean_theorem"
    assert record["kernel_checked"] is True
    assert record["theorem"] == f"{MODULE}.arbitraryWidth"
    assert record["parameters"] == ["W"]
    assert record["verification_nonce"] == NONCE
    assert record["axioms"] == []
    assert "Error pretty printing" not in record["proposition"]


def test_rejects_nonexistent_theorem_with_structured_error() -> None:
    completed, record = _run(f"{MODULE}.doesNotExist")

    assert completed.returncode == 1
    assert record["status"] == "error"
    assert record["error_kind"] == "certificate"
    assert record["verification_nonce"] == NONCE
    assert "not present" in record["message"]


def test_rejects_theorem_that_depends_on_custom_axiom() -> None:
    completed, record = _run(f"{MODULE}.customAxiomWidth")

    assert completed.returncode == 1
    assert record["status"] == "error"
    assert record["error_kind"] == "certificate"
    assert record["verification_nonce"] == NONCE
    assert "non-allowlisted axiom" in record["message"]
    assert f"{MODULE}.magic" in record["message"]


def test_rejects_theorem_owned_by_an_imported_dependency() -> None:
    completed, record = _run("Nat.add_zero", parameters="n")

    assert completed.returncode == 1
    assert record["status"] == "error"
    assert record["error_kind"] == "certificate"
    assert "not candidate module" in record["message"]
