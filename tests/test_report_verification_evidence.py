from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "agent"))

from report import _render_verification_evidence, generate_html  # noqa: E402


def _result(**updates) -> dict:
    result = {
        "prob_id": "parameterized_dut",
        "compile_pass": True,
        "lint_pass": True,
        "sim_status": "sim_pass",
        "sim_mismatches": 0,
        "detail": "",
    }
    result.update(updates)
    return result


def test_finite_sweep_reports_only_the_enumerated_configurations():
    cell = _render_verification_evidence({
        "verification_evidence": [{
            "kind": "finite_parameter_sweep",
            "status": "passed",
            "configurations": [
                [["W", 8]],
                [["W", 4]],
                # Duplicate cache hits do not enlarge the finite scope.
                [["W", 8]],
            ],
        }],
        "has_sorry": False,
    })

    assert "Finite sweep passed &middot; 2 configs" in cell
    assert "W=4; W=8" in cell
    assert "not a theorem over all legal parameter values" in cell
    assert "Universal Lean source theorem" not in cell
    assert "Lean source complete (legacy" not in cell


def test_universal_theorem_requires_explicit_typed_evidence():
    no_theorem = _render_verification_evidence({"has_sorry": False})
    assert "Lean source complete (legacy; no theorem claim)" in no_theorem
    assert "Universal Lean source theorem" not in no_theorem

    theorem = _render_verification_evidence({
        "verification_evidence": {
            "kind": "universal_lean_theorem",
            "status": "proved",
            "scope": "lean_source_semantics",
            "kernel_checked": True,
            "theorem": "GenericAdd.correct_for_all_legal_W",
            "parameters": ["W"],
            "domain_predicate": "GenericAdd.ValidConfig W",
            "proposition": "forall W, GenericAdd.ValidConfig W -> correct W",
            "source_artifact": "Generated/GenericAdd.lean",
            "axioms": [],
            "compiler_correctness_claimed": False,
            "revalidated_by": "sparkle-certify",
        },
        "has_sorry": False,
    })

    assert "Universal Lean source theorem" in theorem
    assert "GenericAdd.correct_for_all_legal_W" in theorem
    assert "forall W" in theorem
    assert "domain: GenericAdd.ValidConfig W" in theorem
    assert "Finite sweep" not in theorem
    assert "legacy" not in theorem


def test_untrusted_axiom_never_gets_a_green_universal_badge():
    cell = _render_verification_evidence({
        "verification_evidence": {
            "kind": "universal_lean_theorem",
            "status": "proved",
            "scope": "lean_source_semantics",
            "kernel_checked": True,
            "theorem": "Bad.magicWidth",
            "parameters": ["W"],
            "proposition": "forall W, Correct W",
            "source_artifact": "Generated/Bad.lean",
            "axioms": ["Bad.magic"],
            "compiler_correctness_claimed": False,
            "revalidated_by": "sparkle-certify",
        },
    })

    assert "Universal Lean theorem evidence incomplete" in cell
    assert "Universal Lean source theorem &middot;" not in cell
    assert "untrusted axioms: Bad.magic" in cell


def test_finite_and_universal_evidence_remain_two_independent_badges():
    cell = _render_verification_evidence({
        "verification_evidence": [
            {
                "kind": "finite_sweep",
                "configurations": [["W", 3]],
            },
            {
                "kind": "universal_lean_theorem",
                "theorem": "width_correct",
                "parameters": ["W"],
            },
        ],
    })

    assert cell.count('class="badge ') == 2
    assert "Finite sweep &middot; 1 config" in cell
    assert "Universal Lean theorem evidence incomplete &middot; width_correct" in cell


def test_parameterized_ppa_results_keep_cache_as_provenance_only():
    cell = _render_verification_evidence({
        "parameterized_ppa_results": [
            {
                "config": {"W": 4},
                "success": True,
                "cache_hit": True,
                "evidence": {
                    "kind": "finite_parameter_sweep",
                    "configurations": [{"W": 4}],
                    "theorem": None,
                },
            },
            {
                "config": {"W": 8},
                "success": True,
                "cache_hit": False,
                "evidence": {
                    "kind": "finite_parameter_sweep",
                    "configurations": [{"W": 8}],
                    "theorem": None,
                },
            },
        ],
    })

    assert "Finite sweep passed &middot; 2 configs" in cell
    assert "Cache hits: 1/2" in cell
    assert "Universal Lean source theorem" not in cell


def test_legacy_ppa_verified_is_not_rendered_as_a_proof(tmp_path: Path):
    final = _result(
        ppa_history=[
            {"area_um2": 10.0, "cell_count": 10, "wns_ns": 0.1, "power_uw": 1.0},
            {"area_um2": 8.0, "cell_count": 8, "wns_ns": 0.1, "power_uw": 0.8},
        ],
    )
    legacy_iteration = {
        "prob_id": "parameterized_dut",
        "ppa_iteration": 1,
        "ppa_verified": True,
    }

    html = generate_html(
        {"attempted": 1, "ppa_opt_enabled": True},
        [legacy_iteration, final],
        tmp_path,
    )

    assert "Lean source complete (legacy; no theorem claim)" in html
    assert ">Verified<" not in html
    assert ">Unverified<" not in html
    assert "<th>Proof</th>" not in html
    assert "<th>Evidence</th>" in html
    assert "Verification Evidence" in html


def test_evidence_strings_are_html_escaped():
    cell = _render_verification_evidence({
        "verification_evidence": [
            {
                "kind": "finite_parameter_sweep",
                "configurations": [{"W": '\"><script>bad()</script>'}],
            },
            {
                "kind": "universal_lean_theorem",
                "theorem": "<script>bad()</script>",
            },
        ],
    })

    assert "<script>" not in cell
    assert "&lt;script&gt;bad()&lt;/script&gt;" in cell


def test_parameter_sweep_pvt_corners_expand_per_configuration(tmp_path: Path):
    html = generate_html(
        {"attempted": 1, "pnr_enabled": True},
        [_result(
            pvt_corners=[{
                "config": {"W": 8},
                "corners": [{
                    "corner": "tt",
                    "label": "TT",
                    "wns_ns": 0.125,
                    "whs_ns": 0.050,
                    "power_uw": 1.25,
                }],
            }],
            pvt_worst_wns_ns=0.125,
            pvt_worst_whs_ns=0.050,
            pvt_worst_power_uw=1.25,
        )],
        tmp_path,
    )

    assert "TT [W=8]" in html
    assert "0.125" in html
    assert "0.050" in html
    assert "1.2500" in html
