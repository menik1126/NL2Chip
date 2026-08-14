from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "agent"))

import evaluator as evaluator_module  # noqa: E402
from evaluator import (  # noqa: E402
    Evaluator,
    UNIVERSAL_THEOREM_MARKER,
    _extract_universal_theorem_evidence,
)
from universal_theorem import independently_revalidate_universal_theorems  # noqa: E402
from search import (  # noqa: E402
    ArchCandidate,
    _has_kernel_checked_design_contract,
    _has_kernel_checked_universal_theorem,
    select_best_candidate,
)


def _marker(**updates) -> str:
    record = {
        "schema_version": 1,
        "evidence_kind": "universal_lean_theorem",
        "status": "proved",
        "theorem": "Tests.generic_correct",
        "parameters": ["W"],
        "proposition": "∀ W : Nat, ValidConfig W → Correct W",
        "domain": [{
            "binder_info": "explicit",
            "name": "legal",
            "role": "premise",
            "type": "ValidConfig W",
        }],
        "binders": [{"name": "W", "type": "Nat"}],
        "conclusion": "ValidConfig W → Correct W",
        "kernel_checked": True,
        "axioms": [],
        "compiler_correctness_claimed": False,
    }
    record.update(updates)
    return UNIVERSAL_THEOREM_MARKER + json.dumps(record, separators=(",", ":"))


def test_candidate_certificate_marker_is_a_request_until_independently_revalidated():
    source = "#sparkleUniversalTheorem Tests.generic_correct parameters [W]"
    evidence = _extract_universal_theorem_evidence(
        "info: Generated/Test.lean:4:0: " + _marker(),
        source,
        "Generated/Test.lean",
    )

    assert evidence == [{
        "kind": "universal_lean_theorem",
        "status": "proved",
        "scope": "lean_source_semantics",
        "theorem": "Tests.generic_correct",
        "parameters": ["W"],
        "domain_predicate": "ValidConfig W",
        "domain": [{
            "binder_info": "explicit",
            "name": "legal",
            "role": "premise",
            "type": "ValidConfig W",
        }],
        "proposition": "∀ W : Nat, ValidConfig W → Correct W",
        "binders": [{"name": "W", "type": "Nat"}],
        "conclusion": "ValidConfig W → Correct W",
        "kernel_checked": True,
        "axioms": [],
        "source_artifact": "Generated/Test.lean",
        "compiler_correctness_claimed": False,
    }]
    assert _has_kernel_checked_universal_theorem({
        "verification_evidence": evidence
    }) is False


def test_printed_or_mismatched_marker_cannot_forge_universal_evidence():
    marker = _marker()
    assert _extract_universal_theorem_evidence(
        marker,
        "#eval IO.println \"fake marker\"",
        "Generated/Fake.lean",
    ) == []
    assert _extract_universal_theorem_evidence(
        marker,
        "#sparkleUniversalTheorem Tests.generic_correct parameters [N]",
        "Generated/Fake.lean",
    ) == []
    assert _extract_universal_theorem_evidence(
        "info: " + _marker(axioms=["sorryAx"]),
        "#sparkleUniversalTheorem Tests.generic_correct parameters [W]",
        "Generated/Fake.lean",
    ) == []


def test_no_sorry_without_certificate_is_not_a_universal_theorem():
    assert _has_kernel_checked_universal_theorem({
        "has_sorry": False,
        "lean_source_status": "complete",
        "verification_evidence": [],
    }) is False


def test_unrelated_source_theorem_does_not_bias_architecture_selection():
    evidence = _extract_universal_theorem_evidence(
        "info: " + _marker(),
        "#sparkleUniversalTheorem Tests.generic_correct parameters [W]",
        "Generated/Test.lean",
    )
    evidence[0]["revalidated_by"] = "sparkle-certify"
    result = {"verification_evidence": evidence}
    assert _has_kernel_checked_universal_theorem(result) is True
    assert _has_kernel_checked_design_contract(result) is False

    unrelated = ArchCandidate(
        index=0,
        code="theorem only",
        ppa={"area_um2": 100.0, "cell_count": 100},
        sim_pass=True,
        verified=_has_kernel_checked_design_contract(result),
        description="unrelated theorem",
        verification_evidence=evidence,
    )
    smaller = ArchCandidate(
        index=1,
        code="smaller",
        ppa={"area_um2": 1.0, "cell_count": 1},
        sim_pass=True,
        verified=False,
        description="smaller",
    )

    assert select_best_candidate([unrelated, smaller], {}).index == 1


def test_custom_axiom_marker_is_rejected_even_if_it_claims_kernel_checked():
    evidence = _extract_universal_theorem_evidence(
        "info: " + _marker(axioms=["Bad.magic"]),
        "#sparkleUniversalTheorem Tests.generic_correct parameters [W]",
        "Generated/Bad.lean",
    )
    assert evidence == []


def test_evaluator_collects_repl_certificate_without_confusing_it_with_sv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    generated = tmp_path / "Generated"
    generated.mkdir()
    (generated / "certified.lean").write_text(
        "#sparkleUniversalTheorem Tests.generic_correct parameters [W]"
    )
    verilog = "module certified(input logic x, output logic y); assign y=x; endmodule"

    class ReplStub:
        def check_file(self, _path: Path):
            return SimpleNamespace(
                passed=True,
                complete=True,
                infos=[{"data": _marker()}],
                verilog=verilog,
                error_text="",
                env=17,
            )

        def check_code_incremental(self, code: str, env: int | None = None):
            assert env == 17
            assert "Sparkle.Compiler.Elab.certifyUniversalTheorem" in code
            nonce_match = re.search(r'some "([0-9a-f]{32})"', code)
            assert nonce_match is not None
            return SimpleNamespace(
                passed=True,
                infos=[{
                    "data": _marker(
                        verification_nonce=nonce_match.group(1)
                    )
                }],
            )

    evaluator = Evaluator(project_root=tmp_path, lean_repl=ReplStub())
    monkeypatch.setattr(
        evaluator, "_materialize_certificate_module", lambda *_args: True
    )
    def revalidate_with_trusted_stub(*args, **kwargs):
        def certify(_module, _theorem, _parameters, nonce):
            return json.loads(
                _marker(verification_nonce=nonce).split(
                    UNIVERSAL_THEOREM_MARKER, 1
                )[1]
            )

        return independently_revalidate_universal_theorems(
            *args, **kwargs, certifier=certify
        )

    monkeypatch.setattr(
        evaluator_module,
        "independently_revalidate_universal_theorems",
        revalidate_with_trusted_stub,
    )
    monkeypatch.setattr(evaluator, "_run_lint", lambda _path: True)
    monkeypatch.setattr(
        evaluator,
        "_run_sim",
        lambda *_args: ("sim_pass", 0, "passed"),
    )

    result = evaluator.evaluate("certified", tmp_path / "run")

    assert result["sv_extracted"] is True
    assert result["sim_status"] == "sim_pass"
    assert result["lean_source_status"] == "complete"
    assert result["verification_evidence"][0]["theorem"] == "Tests.generic_correct"
    assert result["verification_evidence"][0]["kernel_checked"] is True
    assert result["verification_evidence"][0]["revalidated_by"] == "sparkle-certify"


def test_candidate_marker_is_only_discovery_and_cannot_spoof_recheck(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    generated = tmp_path / "Generated"
    generated.mkdir()
    (generated / "spoofed.lean").write_text(
        "def quoted := `(command| "
        "#sparkleUniversalTheorem Ghost.missing parameters [W])\n"
        "#eval Lean.logInfo (\"SPARKLE_\" ++ \"UNIVERSAL_THEOREM_JSON:...\")"
    )
    verilog = "module spoofed(input logic x, output logic y); assign y=x; endmodule"

    class SpoofedReplStub:
        def check_file(self, _path: Path):
            return SimpleNamespace(
                passed=True,
                complete=True,
                infos=[{"data": _marker(theorem="Ghost.missing")}],
                verilog=verilog,
                error_text="",
                env=23,
            )

        def check_code_incremental(self, code: str, env: int | None = None):
            assert env == 23
            assert "Ghost.missing" in code
            # The evaluator-owned command actually resolves/checks the theorem;
            # the candidate's fabricated info line is never consumed as proof.
            return SimpleNamespace(passed=False, infos=[])

    evaluator = Evaluator(project_root=tmp_path, lean_repl=SpoofedReplStub())
    monkeypatch.setattr(
        evaluator, "_materialize_certificate_module", lambda *_args: True
    )
    def reject_spoof(*args, **kwargs):
        return independently_revalidate_universal_theorems(
            *args, **kwargs, certifier=lambda *_request: None
        )

    monkeypatch.setattr(
        evaluator_module,
        "independently_revalidate_universal_theorems",
        reject_spoof,
    )
    monkeypatch.setattr(evaluator, "_run_lint", lambda _path: True)
    monkeypatch.setattr(
        evaluator,
        "_run_sim",
        lambda *_args: ("sim_pass", 0, "passed"),
    )

    result = evaluator.evaluate("spoofed", tmp_path / "run")

    assert result["compile_pass"] is True
    assert result["sim_status"] == "sim_pass"
    assert result["verification_evidence"] == []
