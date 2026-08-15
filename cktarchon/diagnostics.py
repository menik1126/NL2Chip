from __future__ import annotations

import hashlib
import re
from typing import Any, Iterable


_INTERNAL_TERM_MARKERS = (
    "_uniq.",
    "_hygCtx",
    "List.below.",
    "List.foldl.match_",
    "Eq.ndrec",
    "@Init.Prelude",
)
_MAX_MESSAGE_LINES = 12
_MAX_LINE_CHARS = 320
_MAX_MESSAGE_CHARS = 1800


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 20)].rstrip() + " ... [truncated]"


def _stable_text(text: str) -> str:
    text = re.sub(r"_uniq\.\d+", "_uniq.*", text)
    text = re.sub(r"\?m\.\d+", "?m.*", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def classify_lean_diagnostic(message: str) -> str:
    text = str(message or "")
    lowered = text.lower()
    if "requested retained hardware parameter" in lowered and "top-level nat binder" in lowered:
        return "retained_parameter_not_top_level"
    if "zero-width default" in lowered:
        return "zero_width_parameter_default"
    if "not a hardware module definition" in lowered:
        return "unsupported_hardware_definition"
    if "if-then-else" in lowered and "cannot be synthesized" in lowered:
        return "unsupported_compile_time_branch"
    if "signal.loop argument must be a lambda" in lowered:
        return "invalid_signal_loop"
    if "cannot infer hardware type" in lowered:
        return "lean_hardware_type_inference"
    if "typeclass instance problem is stuck" in lowered:
        return "lean_typeclass_stuck"
    if "type mismatch" in lowered or "application type mismatch" in lowered:
        return "lean_type_mismatch"
    if "unknown constant" in lowered or "unknown identifier" in lowered:
        return "unknown_lean_api"
    if "unbound variable" in lowered and "_uniq." in text:
        return "symbolic_lowering_unbound_variable"
    if "expected ';' or line break" in lowered or "unexpected token" in lowered:
        return "lean_syntax_error"
    return "lean_compile_error"


def compact_lean_message(message: str) -> str:
    lines: list[str] = []
    omitted_internal = False
    for raw_line in str(message or "").splitlines():
        line = raw_line.rstrip()
        if not line:
            if lines and lines[-1] != "":
                lines.append("")
            continue
        internal = any(marker in line for marker in _INTERNAL_TERM_MARKERS)
        if internal:
            if not any(lines):
                lines.append(_truncate(line, _MAX_LINE_CHARS))
                continue
            if not omitted_internal:
                lines.append("  [internal Lean term omitted]")
                omitted_internal = True
            continue
        lines.append(_truncate(line, _MAX_LINE_CHARS))
        if len(lines) >= _MAX_MESSAGE_LINES:
            lines.append("... [additional diagnostic lines omitted]")
            break
    while lines and not lines[-1]:
        lines.pop()
    return _truncate("\n".join(lines), _MAX_MESSAGE_CHARS)


def _normalize_location(pos: Any) -> str:
    if not isinstance(pos, dict):
        return "?:?"
    return f"{pos.get('line', '?')}:{pos.get('column', '?')}"


def build_lean_diagnostics(
    errors: Iterable[Any],
    *,
    max_entries: int = 8,
) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for raw_error in errors:
        if isinstance(raw_error, dict):
            location = _normalize_location(raw_error.get("pos"))
            raw_message = str(raw_error.get("data", ""))
        else:
            location = "?:?"
            raw_message = str(raw_error)
        message = compact_lean_message(raw_message)
        if not message:
            continue
        stable = _stable_text(message)
        key = (location.replace("line ", ""), stable)
        if key in seen:
            continue
        seen.add(key)
        code = classify_lean_diagnostic(raw_message)
        records.append({
            "location": location,
            "code": code,
            "summary": message.splitlines()[0],
            "message": message,
        })
        if len(records) >= max_entries:
            break
    return records


def parse_lean_error_text(text: str, *, max_entries: int = 8) -> list[dict[str, str]]:
    source = str(text or "")
    matches = list(re.finditer(r"(?m)^\[error\s+([^\]]+)\]\s*", source))
    errors: list[dict[str, Any]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(source)
        location = match.group(1).strip().removeprefix("line ")
        line, _, column = location.partition(":")
        errors.append({
            "pos": {"line": line or "?", "column": column or "?"},
            "data": source[match.end():end].strip(),
        })
    if not errors:
        lake_matches = list(re.finditer(r"(?m)^error: (.+?):(\d+):(\d+): (.*)$", source))
        for index, match in enumerate(lake_matches):
            end = lake_matches[index + 1].start() if index + 1 < len(lake_matches) else len(source)
            errors.append({
                "pos": {"line": match.group(2), "column": match.group(3)},
                "data": source[match.start():end].strip(),
            })
    if not errors:
        errors = [{"pos": {}, "data": source.strip()}]
    return build_lean_diagnostics(errors, max_entries=max_entries)


def format_lean_diagnostics(records: Iterable[dict[str, str]], *, max_chars: int = 4000) -> str:
    blocks = []
    for record in records:
        blocks.append(
            f"[{record.get('code', 'lean_compile_error')}] "
            f"[error {record.get('location', '?:?')}] {record.get('message', '')}"
        )
    return _truncate("\n".join(blocks), max_chars)


def lean_diagnostic_signature(records: Iterable[dict[str, str]]) -> str:
    parts = [
        f"{record.get('code', '')}:{_stable_text(record.get('summary', ''))}"
        for record in records
    ]
    payload = "\n".join(parts).encode("utf-8", errors="replace")
    return hashlib.sha256(payload).hexdigest()[:16] if parts else ""
