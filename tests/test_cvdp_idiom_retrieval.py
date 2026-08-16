from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "agent"))

import cvdp_idioms as idioms  # noqa: E402
from cvdp_idioms import (  # noqa: E402
    CVDPIdiomQuery,
    classify_cvdp_idiom_feedback,
    idiom_catalog_sha256,
    render_cvdp_idiom_context,
    select_cvdp_idioms,
    verified_idiom_ids,
)


EXPECTED_IDS = {
    "symbolic_constants",
    "derived_width",
    "slice_resize",
    "bounded_stages",
    "named_packed_outputs",
    "packed_state_high",
    "packed_state_low",
    "memory_1r1w",
}
TASK_LEAK_STEMS = {
    "cvdp_copilot_",
    "word_reducer",
    "data_reduction",
    "nbit_swizzling",
    "sync_lifo",
    "gf_multiplier",
    "car_parking_management",
    "car_parking_system",
    "hamming_code_tx_and_rx",
    "hamming_rx",
    "hamming_tx",
    "square_root",
    "square_root_seq",
    "digital_dice_roller",
    "restoring_division",
    "restore_division",
    "axil_precision_counter",
    "precision_counter_axi",
    "filo_rtl",
    "bit_difference_counter",
    "gf_mac",
}


def _ids(cards):
    return tuple(card.idiom_id for card in cards)


def test_catalog_matches_markers_and_exposes_expression_bodies_only():
    regions = idioms._load_idiom_regions()

    assert set(verified_idiom_ids()) == EXPECTED_IDS
    assert set(regions) == EXPECTED_IDS
    source = idioms.IDIOM_SOURCE_PATH.read_text(encoding="utf-8")
    assert source.count(idioms.IDIOM_BEGIN) == len(EXPECTED_IDS)
    assert source.count(idioms.IDIOM_END) == len(EXPECTED_IDS)
    for idiom_id, body in regions.items():
        lowered = body.lower()
        assert body.strip()
        assert "#synthesizeverilog" not in lowered
        assert not lowered.startswith("def ")
        assert "\ndef " not in lowered
        assert not idioms._FORBIDDEN_BODY_RE.search(body), idiom_id
        assert not idioms._FORBIDDEN_REGION_RE.search(body), idiom_id
        assert not idioms._SIMPLE_NOOP_MUX_RE.search(body), idiom_id
        assert not idioms._PAREN_NOOP_MUX_RE.search(body), idiom_id
        assert not any(stem in lowered for stem in TASK_LEAK_STEMS), idiom_id


def test_catalog_sha_is_exact_source_content_hash():
    expected = hashlib.sha256(idioms.IDIOM_SOURCE_PATH.read_bytes()).hexdigest()

    assert idiom_catalog_sha256() == expected
    assert len(expected) == 64


def test_initial_selection_is_deterministic_grouped_and_hard_bounded():
    query = CVDPIdiomQuery(
        has_parameters=True,
        has_derived_widths=True,
        is_sequential=True,
        has_reset=True,
        reset_polarity="active-low",
        has_multiple_outputs=True,
        uses_memory=True,
        uses_bit_network=True,
        uses_width_transform=True,
    )

    first = select_cvdp_idioms(query, max_cards=99, max_chars=99_999)
    second = select_cvdp_idioms(query, max_cards=99, max_chars=99_999)
    rendered = render_cvdp_idiom_context(first)

    assert first == second
    assert len(first) <= 3
    assert len(rendered) <= 3800
    assert "packed_state_low" in _ids(first)
    assert "packed_state_high" not in _ids(first)
    assert sum(card_id.startswith("packed_state_") for card_id in _ids(first)) <= 1


@pytest.mark.parametrize(
    ("polarity", "expected", "excluded"),
    [
        ("active-high", "packed_state_high", "packed_state_low"),
        ("active-low", "packed_state_low", "packed_state_high"),
    ],
)
def test_packed_state_selection_tracks_reset_polarity(
    polarity: str,
    expected: str,
    excluded: str,
):
    query = CVDPIdiomQuery(
        is_sequential=True,
        has_reset=True,
        reset_polarity=polarity,
    )

    initial = select_cvdp_idioms(query)
    repair = select_cvdp_idioms(query, feedback="error in Signal.loop body state type")

    assert _ids(initial) == (expected,)
    assert _ids(repair) == (expected,)
    assert excluded not in _ids(initial)
    assert excluded not in _ids(repair)


def test_unknown_or_mixed_reset_polarity_fails_closed_for_state_card():
    for polarity in ("none", "mixed"):
        query = CVDPIdiomQuery(
            is_sequential=True,
            has_reset=True,
            reset_polarity=polarity,
        )
        assert select_cvdp_idioms(query) == ()
        assert select_cvdp_idioms(query, feedback="Signal.loop body error") == ()


def test_reset_polarity_without_reset_port_fails_closed_for_state_card():
    query = CVDPIdiomQuery(reset_polarity="active-high", has_reset=False)
    assert select_cvdp_idioms(query) == ()
    assert select_cvdp_idioms(query, feedback="Signal.loop body error") == ()


def test_reset_polarity_aliases_are_canonical_and_invalid_values_fail():
    assert CVDPIdiomQuery(reset_polarity="ACTIVE_LOW").reset_polarity == "active-low"
    assert CVDPIdiomQuery(reset_polarity="high").reset_polarity == "active-high"
    with pytest.raises(ValueError, match="unsupported reset polarity"):
        CVDPIdiomQuery(reset_polarity="falling-edge")


@pytest.mark.parametrize(
    ("feedback", "expected"),
    [
        ("adapter provenance failure: output could not be mapped", "named_packed_outputs"),
        ("failure_category: adapter_contract_error", "named_packed_outputs"),
        ("CVDP_ADAPTER_ERROR: output provenance failed", "named_packed_outputs"),
        (
            "harness handle is not declared by the exact reference top interface",
            "named_packed_outputs",
        ),
        (
            "harness handle has no unique exact-core mapping",
            "named_packed_outputs",
        ),
        (
            "harness-observed output cannot be safely mapped",
            "named_packed_outputs",
        ),
        ("output could not be mapped safely", "named_packed_outputs"),
        ("frontend rejected List.foldl recursion", "bounded_stages"),
        ("failed to synthesize OfNat for a numeric literal", "symbolic_constants"),
        ("unresolved outW metavariable after slice", "slice_resize"),
        ("unresolved dimension around clog2Nat", "derived_width"),
        ("unsupported array in tuple loop state", "memory_1r1w"),
    ],
)
def test_repair_classifier_selects_one_precise_root_card(
    feedback: str,
    expected: str,
):
    query = CVDPIdiomQuery(reset_polarity="active-high")

    assert classify_cvdp_idiom_feedback(query, feedback) == expected
    cards = select_cvdp_idioms(
        query,
        feedback=feedback,
        max_cards=99,
        max_chars=99_999,
    )
    rendered = render_cvdp_idiom_context(cards, repair=True)

    assert _ids(cards) == (expected,)
    assert len(cards) <= 1
    assert len(rendered) <= 1400


def test_repair_classifier_uses_earliest_root_error_not_later_noise():
    query = CVDPIdiomQuery(reset_polarity="active-high")
    feedback = "first error: List.fold is unsupported\nsecondary error: OfNat failed"

    assert classify_cvdp_idiom_feedback(query, feedback) == "bounded_stages"


def test_repair_classifier_avoids_old_broad_false_positive_terms():
    query = CVDPIdiomQuery(reset_polarity="active-high")
    feedback = "parameter Prod outputs were mentioned without a diagnostic"

    assert classify_cvdp_idiom_feedback(query, feedback) is None
    assert select_cvdp_idioms(query, feedback=feedback) == ()


def test_repair_renderer_never_contains_definition_shell():
    query = CVDPIdiomQuery(
        is_sequential=True,
        has_reset=True,
        reset_polarity="active-low",
    )
    cards = select_cvdp_idioms(query, feedback="Signal.loop body error")
    rendered = render_cvdp_idiom_context(cards, repair=True)

    assert len(rendered) <= 1400
    assert "#synthesizeVerilog" not in rendered
    assert "def promptIdiom" not in rendered
    assert "packed_state_low" in rendered
    assert "Signal.loop" in rendered


def test_requested_smaller_limits_are_respected():
    query = CVDPIdiomQuery(has_parameters=True, uses_width_transform=True)

    assert select_cvdp_idioms(query, max_cards=0) == ()
    assert select_cvdp_idioms(query, max_chars=10) == ()
    one = select_cvdp_idioms(query, max_cards=1)
    assert len(one) <= 1


def test_repair_renderer_rejects_ambiguous_multi_card_input():
    query = CVDPIdiomQuery(has_parameters=True, uses_width_transform=True)
    cards = select_cvdp_idioms(query)
    assert len(cards) == 2

    with pytest.raises(ValueError, match="exactly one"):
        render_cvdp_idiom_context(cards, repair=True)


@pytest.mark.skipif(shutil.which("lake") is None, reason="Lake is not installed")
def test_verified_catalog_elaborates_and_extracts_verilog():
    result = subprocess.run(
        ["lake", "env", "lean", "Benchmark/PromptIdioms.lean"],
        cwd=PROJECT_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=180,
        check=False,
    )

    assert result.returncode == 0, result.stdout
    assert "error:" not in result.stdout.lower()
    for module_name in (
        "promptIdiomSymbolicConstants",
        "promptIdiomDerivedWidth",
        "promptIdiomSliceResize",
        "promptIdiomBoundedStages",
        "promptIdiomNamedPackedOutputs",
        "promptIdiomPackedStateHigh",
        "promptIdiomPackedStateLow",
        "promptIdiomMemory1R1W",
    ):
        assert module_name in result.stdout
