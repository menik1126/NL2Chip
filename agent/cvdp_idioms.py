"""Verified, specification-neutral Sparkle idioms for CVDP prompts.

The catalog is deliberately small and fail closed. Its source lives in
``Benchmark/PromptIdioms.lean`` and is compiled as ordinary Lean/Sparkle code.
Only the expression body of each marked definition is exposed to the model;
definition names and ``#synthesizeVerilog`` commands never enter a prompt.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import hashlib
from pathlib import Path
import re
import textwrap


PROJECT_ROOT = Path(__file__).resolve().parents[1]
IDIOM_SOURCE_PATH = PROJECT_ROOT / "Benchmark" / "PromptIdioms.lean"
IDIOM_BEGIN = "CKTARCHON_IDIOM_BEGIN"
IDIOM_END = "CKTARCHON_IDIOM_END"

_INITIAL_MAX_CARDS = 3
_INITIAL_MAX_CHARS = 3800
_REPAIR_MAX_CARDS = 1
_REPAIR_MAX_CHARS = 1400
_RESET_POLARITIES = frozenset({"none", "active-high", "active-low", "mixed"})
_RESET_POLARITY_ALIASES = {
    "": "none",
    "none": "none",
    "unknown": "none",
    "high": "active-high",
    "active-high": "active-high",
    "activehigh": "active-high",
    "low": "active-low",
    "active-low": "active-low",
    "activelow": "active-low",
    "mixed": "mixed",
}


@dataclass(frozen=True)
class CVDPIdiomQuery:
    """Public structural facts used for deterministic idiom retrieval."""

    has_parameters: bool = False
    has_derived_widths: bool = False
    is_sequential: bool = False
    has_reset: bool = False
    reset_polarity: str = "none"
    has_multiple_outputs: bool = False
    uses_memory: bool = False
    uses_bit_network: bool = False
    uses_width_transform: bool = False

    def __post_init__(self) -> None:
        raw = str(self.reset_polarity or "").strip().lower().replace("_", "-")
        normalized = _RESET_POLARITY_ALIASES.get(raw)
        if normalized not in _RESET_POLARITIES:
            allowed = ", ".join(sorted(_RESET_POLARITIES))
            raise ValueError(
                f"unsupported reset polarity {self.reset_polarity!r}; "
                f"expected one of: {allowed}"
            )
        object.__setattr__(self, "reset_polarity", normalized)


@dataclass(frozen=True)
class VerifiedSparkleIdiom:
    idiom_id: str
    title: str
    guidance: str
    source: str


@dataclass(frozen=True)
class _IdiomSpec:
    title: str
    guidance: str
    order: int
    group: str | None = None


_IDIOM_SPECS: dict[str, _IdiomSpec] = {
    "symbolic_constants": _IdiomSpec(
        "Symbolic-width constants",
        "Use BitVec.ofNat with the Nat width; do not rely on a fixed-width literal.",
        50,
    ),
    "derived_width": _IdiomSpec(
        "Positive derived widths",
        "Keep clog2-derived dimensions symbolic; avoid a parameter-dependent Lean branch.",
        40,
    ),
    "slice_resize": _IdiomSpec(
        "Explicit slice/resize result types",
        "Annotate the result width so the frontend cannot invent an unresolved outW.",
        60,
    ),
    "bounded_stages": _IdiomSpec(
        "Bounded straight-line hardware stages",
        "Use named hardware stages instead of host recursion, List.fold, or a Lean if.",
        30,
    ),
    "named_packed_outputs": _IdiomSpec(
        "Exact named packed outputs",
        "Keep exact output lets and concatenate in the scaffold's MSB-to-LSB order.",
        10,
    ),
    "packed_state_high": _IdiomSpec(
        "Packed feedback state with active-high reset",
        "Return one packed state Signal from Signal.loop; unpack fields after the loop.",
        20,
        group="packed_state",
    ),
    "packed_state_low": _IdiomSpec(
        "Packed feedback state with active-low reset",
        "Return one packed state Signal from Signal.loop; keep reset outermost on the D path.",
        21,
        group="packed_state",
    ),
    "memory_1r1w": _IdiomSpec(
        "Checked 1R1W storage helper",
        "Use regFile1R1W/syncRam1R1W instead of an array or memory in tuple loop state.",
        0,
    ),
}


# These are exact task/design stems from the evaluation subset, not generic HDL
# vocabulary. A match means the neutral catalog has leaked an answer cue.
_BENCHMARK_LEAK_RE = re.compile(
    r"(?:cvdp_copilot_|\b(?:"
    r"word_reducer|data_reduction|nbit_swizzling|sync_lifo|gf_multiplier|"
    r"car_parking_management|car_parking_system|hamming_code_tx_and_rx|"
    r"hamming_rx|hamming_tx|square_root|square_root_seq|"
    r"digital_dice_roller|restoring_division|restore_division|"
    r"axil_precision_counter|precision_counter_axi|filo_rtl|"
    r"bit_difference_counter|gf_mac"
    r")\b)",
    flags=re.IGNORECASE,
)
_FORBIDDEN_REGION_RE = re.compile(
    r"(?:\b(?:sorry|admit|axiom|unsafe|opaque)\b|#eval\b|\bTODO\b)",
    flags=re.IGNORECASE,
)
_FORBIDDEN_BODY_RE = re.compile(
    r"(?:^|\s)(?:def\b|#synthesizeVerilog\b|import\b|namespace\b)",
    flags=re.MULTILINE,
)
_SIMPLE_NOOP_MUX_RE = re.compile(
    r"\bSignal\.mux\s+\S+\s+([A-Za-z_][A-Za-z0-9_'.]*)\s+\1(?=\s|$|\))"
)
_PAREN_NOOP_MUX_RE = re.compile(
    r"\bSignal\.mux\s+\S+\s+\(([^()\n]+)\)\s+\(\1\)(?=\s|$|\))"
)


def _validate_idiom_region(idiom_id: str, region: str, body: str) -> None:
    """Reject unchecked, answer-bearing, or non-useful prompt material."""

    if not body.strip():
        raise ValueError(f"verified Sparkle idiom {idiom_id!r} has an empty body")
    if _FORBIDDEN_REGION_RE.search(region):
        raise ValueError(f"verified Sparkle idiom {idiom_id!r} uses a forbidden construct")
    if _BENCHMARK_LEAK_RE.search(region):
        raise ValueError(f"verified Sparkle idiom {idiom_id!r} leaks a benchmark identifier")
    if _FORBIDDEN_BODY_RE.search(body):
        raise ValueError(
            f"verified Sparkle idiom {idiom_id!r} exposed definition-level syntax"
        )
    if _SIMPLE_NOOP_MUX_RE.search(body) or _PAREN_NOOP_MUX_RE.search(body):
        raise ValueError(
            f"verified Sparkle idiom {idiom_id!r} contains an identical-branch mux"
        )


@lru_cache(maxsize=1)
def _load_idiom_regions() -> dict[str, str]:
    text = IDIOM_SOURCE_PATH.read_text(encoding="utf-8")
    begin_ids = re.findall(
        rf"{IDIOM_BEGIN}\s+([a-z0-9_]+)",
        text,
    )
    end_ids = re.findall(
        rf"{IDIOM_END}\s+([a-z0-9_]+)",
        text,
    )
    expected_marker_ids = sorted(_IDIOM_SPECS)
    if sorted(begin_ids) != expected_marker_ids or sorted(end_ids) != expected_marker_ids:
        raise ValueError(
            "verified Sparkle idiom markers/catalog mismatch: "
            f"begin={sorted(begin_ids)}, end={sorted(end_ids)}, "
            f"expected={expected_marker_ids}"
        )
    marker_pattern = re.compile(
        rf"/-\s*{IDIOM_BEGIN}\s+([a-z0-9_]+)\s*-/(.*?)"
        rf"/-\s*{IDIOM_END}\s+\1\s*-/",
        flags=re.DOTALL,
    )
    body_pattern = re.compile(
        r"\bdef\s+[A-Za-z_][A-Za-z0-9_']*\b.*?\s:=\s*\n"
        r"(?P<body>.*?)\n\s*#synthesizeVerilog\b",
        flags=re.DOTALL,
    )
    regions: dict[str, str] = {}
    for match in marker_pattern.finditer(text):
        idiom_id = match.group(1)
        if idiom_id in regions:
            raise ValueError(f"duplicate verified Sparkle idiom region: {idiom_id}")
        region = match.group(2).strip()
        body_matches = tuple(body_pattern.finditer(region))
        if len(body_matches) != 1:
            raise ValueError(
                f"verified Sparkle idiom {idiom_id!r} must contain exactly one "
                "definition followed by #synthesizeVerilog"
            )
        body = textwrap.dedent(body_matches[0].group("body")).strip()
        _validate_idiom_region(idiom_id, region, body)
        regions[idiom_id] = body

    expected = set(_IDIOM_SPECS)
    actual = set(regions)
    if actual != expected:
        raise ValueError(
            "verified Sparkle idiom source/catalog mismatch: "
            f"missing={sorted(expected - actual)}, extra={sorted(actual - expected)}"
        )
    return regions


def idiom_catalog_sha256() -> str:
    """Return the content identity recorded with every idiom-assisted run."""

    return hashlib.sha256(IDIOM_SOURCE_PATH.read_bytes()).hexdigest()


def _packed_state_id(query: CVDPIdiomQuery) -> str | None:
    if not query.has_reset:
        return None
    if query.reset_polarity == "active-low":
        return "packed_state_low"
    if query.reset_polarity == "active-high":
        return "packed_state_high"
    # Unknown/mixed reset polarity cannot safely choose a reset-bearing card.
    return None


def _base_scores(query: CVDPIdiomQuery) -> dict[str, int]:
    scores = {idiom_id: 0 for idiom_id in _IDIOM_SPECS}
    if query.has_parameters:
        scores["symbolic_constants"] += 45
    if query.has_derived_widths:
        scores["derived_width"] += 90
    if query.is_sequential and query.has_reset:
        packed_id = _packed_state_id(query)
        if packed_id is not None:
            scores[packed_id] += 130
    if query.has_multiple_outputs:
        scores["named_packed_outputs"] += 120
    if query.uses_memory:
        scores["memory_1r1w"] += 140
    if query.uses_bit_network:
        scores["bounded_stages"] += 85
    if query.uses_width_transform:
        scores["slice_resize"] += 80
    return scores


@dataclass(frozen=True)
class _FeedbackRule:
    tag: str
    patterns: tuple[re.Pattern[str], ...]
    priority: int


def _patterns(*patterns: str) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(pattern, flags=re.IGNORECASE) for pattern in patterns)


# Precise terms only: generic words such as "parameter", "Prod", and "outputs"
# previously selected unrelated cards.
_FEEDBACK_RULES = (
    _FeedbackRule(
        "named_packed_outputs",
        _patterns(
            r"\badapter_contract_error\b",
            r"\bCVDP_ADAPTER_ERROR\b",
            r"adapter[^\n]{0,100}\bprovenance\b",
            r"\bcould not (?:be )?map(?:ped)?\b",
            r"\b(?:cannot|could not) be (?:safely mapped|mapped safely)\b",
            r"\bunmapped (?:output|port)\b",
            r"\bmissing exact output (?:let|name)\b",
            r"\bno unique exact-core\s+mapping\b",
            r"\bnot declared by (?:the )?exact\s+reference top interface\b",
        ),
        0,
    ),
    _FeedbackRule(
        "packed_state",
        _patterns(
            r"\bSignal\.loop\b",
            r"\bloop body\b",
            r"\bloop state\b",
            r"\bstate type\b",
            r"\bProd\.mk\b",
            r"\btuple (?:loop|state)\b",
        ),
        1,
    ),
    _FeedbackRule(
        "bounded_stages",
        _patterns(
            r"\bList\.fold(?:l|r)?\b",
            r"\bbrec(?:on)?\b",
            r"\bDecidable\.rec\b",
            r"\b(?:recursive|recursion)\b",
            r"\bhost (?:if|recursion|control flow)\b",
        ),
        2,
    ),
    _FeedbackRule(
        "symbolic_constants",
        _patterns(
            r"\bOfNat\b",
            r"\bHAdd\.hAdd\b",
            r"\bfixed-width literal\b",
            r"\bnumeric literal\b",
            r"\boperands? (?:must|need to) (?:have|use) the same width\b",
            r"\bsame-width operands?\b",
        ),
        3,
    ),
    _FeedbackRule(
        "slice_resize",
        _patterns(
            r"\boutW\b",
            r"\bunresolved (?:result )?width metavariable\b",
            r"\b(?:slice|extract|truncate|trunc|zext|zero-extend)[^\n]{0,80}"
            r"(?:width|metavariable|mismatch)\b",
        ),
        4,
    ),
    _FeedbackRule(
        "derived_width",
        _patterns(
            r"\bunresolved dimension\b",
            r"\bclog2(?:Nat)?\b",
            r"\bomega[^\n]{0,80}(?:width|dimension|arithmetic|prove)\b",
            r"\bdeclaration-only (?:symbol|parameter)\b",
            r"\bdon't know how to synthesize implicit argument\b",
        ),
        5,
    ),
    _FeedbackRule(
        "memory_1r1w",
        _patterns(
            r"\bregFile1R1W\b",
            r"\bsyncRam1R1W\b",
            r"\b(?:array|memory) in (?:a )?(?:tuple )?loop state\b",
            r"\bunsupported (?:array|memory) state\b",
        ),
        6,
    ),
)


# Backend evaluation deliberately rejects the compile-safe typed scaffold before
# simulation. That rejection has no frontend diagnostic to classify, so reuse
# the task's strongest initial structural signal instead of emitting no repair
# card. Keep these markers specific to an explicitly unfinished implementation;
# ordinary diagnostic prose must still fail closed.
_SCAFFOLD_INCOMPLETE_PATTERNS = _patterns(
    r"\bscaffold[_ -]?incomplete\b",
    r"\bscaffold(?:-only)?\s+fallback\b",
    r"\btyped scaffold fallback\b",
    r"\bbehavioral TODOs? remain\b",
    r"\bCKTARCHON_IMPLEMENTATION_REQUIRED\b",
    r"\bimplementation required\b",
    r"\b(?:not implemented|unimplemented)\b",
    r"TODO[^\n]{0,100}\b(?:remain|implement|behavior|zero)\b",
    r"\bsorryAx\b",
    r"未实现",
)


def _ranked_initial_ids(query: CVDPIdiomQuery) -> list[str]:
    """Return the initial-card ranking shared by initial and fallback repair."""

    scores = _base_scores(query)
    return sorted(
        (idiom_id for idiom_id, score in scores.items() if score > 0),
        key=lambda idiom_id: (
            -scores[idiom_id],
            _IDIOM_SPECS[idiom_id].order,
            idiom_id,
        ),
    )


def classify_cvdp_idiom_feedback(
    query: CVDPIdiomQuery,
    feedback: str,
) -> str | None:
    """Map the earliest recognized root diagnostic to exactly one card."""

    if not feedback.strip():
        return None
    matches: list[tuple[int, int, str]] = []
    for rule in _FEEDBACK_RULES:
        positions = [
            match.start()
            for pattern in rule.patterns
            if (match := pattern.search(feedback)) is not None
        ]
        if positions:
            matches.append((min(positions), rule.priority, rule.tag))
    if matches:
        tag = min(matches)[2]
        if tag == "packed_state":
            return _packed_state_id(query)
        return tag

    if any(pattern.search(feedback) for pattern in _SCAFFOLD_INCOMPLETE_PATTERNS):
        ranked = _ranked_initial_ids(query)
        return ranked[0] if ranked else None
    return None


def _make_card(idiom_id: str, regions: dict[str, str]) -> VerifiedSparkleIdiom:
    spec = _IDIOM_SPECS[idiom_id]
    return VerifiedSparkleIdiom(
        idiom_id=idiom_id,
        title=spec.title,
        guidance=spec.guidance,
        source=regions[idiom_id],
    )


def _render_repair_context(card: VerifiedSparkleIdiom) -> str:
    # Terse enough that the packed-state body fits without truncation.
    return "\n".join(
        [
            f"### Verified repair idiom `{card.idiom_id}`",
            "",
            "Required: adapt this body into the typed scaffold; edit first, then check. Do not only read.",
            "",
            "```lean",
            card.source,
            "```",
        ]
    )


def render_cvdp_idiom_context(
    cards: tuple[VerifiedSparkleIdiom, ...],
    *,
    repair: bool = False,
) -> str:
    if not cards:
        return ""
    if repair:
        if len(cards) != 1:
            raise ValueError("repair idiom context must contain exactly one card")
        return _render_repair_context(cards[0])

    sections = [
        "### Retrieved Verified Sparkle Idioms",
        "",
        "Legal, specification-neutral expression shapes only. The typed task "
        "scaffold controls names, ports, widths, output order, reset, and timing.",
    ]
    for card in cards:
        sections.extend(
            [
                "",
                f"#### `{card.idiom_id}` — {card.title}",
                "",
                card.guidance,
                "",
                "```lean",
                card.source,
                "```",
            ]
        )
    return "\n".join(sections)


def select_cvdp_idioms(
    query: CVDPIdiomQuery,
    *,
    feedback: str = "",
    max_cards: int = _INITIAL_MAX_CARDS,
    max_chars: int = _INITIAL_MAX_CHARS,
) -> tuple[VerifiedSparkleIdiom, ...]:
    """Select bounded, deterministic, verified expression bodies.

    Initial retrieval is hard-capped at three cards / 3,800 rendered chars.
    Non-empty feedback is repair mode: one root-error card / 1,400 chars.
    """

    repair = bool(feedback.strip())
    card_limit = min(max_cards, _REPAIR_MAX_CARDS if repair else _INITIAL_MAX_CARDS)
    char_limit = min(max_chars, _REPAIR_MAX_CHARS if repair else _INITIAL_MAX_CHARS)
    if card_limit <= 0 or char_limit <= 0:
        return ()

    regions = _load_idiom_regions()
    if repair:
        repair_id = classify_cvdp_idiom_feedback(query, feedback)
        candidate_ids = [repair_id] if repair_id is not None else []
    else:
        candidate_ids = _ranked_initial_ids(query)

    selected: list[VerifiedSparkleIdiom] = []
    selected_groups: set[str] = set()
    for idiom_id in candidate_ids:
        spec = _IDIOM_SPECS[idiom_id]
        if spec.group is not None and spec.group in selected_groups:
            continue
        card = _make_card(idiom_id, regions)
        trial = tuple([*selected, card])
        rendered = render_cvdp_idiom_context(trial, repair=repair)
        if len(rendered) > char_limit:
            continue
        selected.append(card)
        if spec.group is not None:
            selected_groups.add(spec.group)
        if len(selected) >= card_limit:
            break
    return tuple(selected)


def verified_idiom_ids() -> tuple[str, ...]:
    """Return all checked marker IDs in stable catalog order."""

    _load_idiom_regions()
    return tuple(
        sorted(
            _IDIOM_SPECS,
            key=lambda idiom_id: (_IDIOM_SPECS[idiom_id].order, idiom_id),
        )
    )
