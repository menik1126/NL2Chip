from __future__ import annotations

import json
from threading import Lock
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


_append_jsonl_lock = Lock()


@dataclass
class AgentStats:
    # Backward-compatible total: uncached + cache creation + cache read.
    input_tokens: int = 0
    output_tokens: int = 0
    turns: int = 0
    compile_checks: int = 0
    tool_counts: dict[str, int] = field(default_factory=dict)
    session_id: str | None = None
    uncached_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0

    def add_usage(self, usage: dict[str, int]) -> None:
        self.uncached_input_tokens += int(usage["uncached_input_tokens"])
        self.cache_creation_input_tokens += int(usage["cache_creation_input_tokens"])
        self.cache_read_input_tokens += int(usage["cache_read_input_tokens"])
        self.input_tokens += int(usage["input_tokens_total"])
        self.output_tokens += int(usage["output_tokens"])

    def set_usage(self, usage: dict[str, int]) -> None:
        self.uncached_input_tokens = int(usage["uncached_input_tokens"])
        self.cache_creation_input_tokens = int(usage["cache_creation_input_tokens"])
        self.cache_read_input_tokens = int(usage["cache_read_input_tokens"])
        self.input_tokens = int(usage["input_tokens_total"])
        self.output_tokens = int(usage["output_tokens"])

    def usage_dict(self) -> dict[str, int]:
        uncached = self.uncached_input_tokens
        accounted = uncached + self.cache_creation_input_tokens + self.cache_read_input_tokens
        if accounted != self.input_tokens:
            # Preserve totals from legacy callers that only populated input_tokens.
            uncached = max(0, self.input_tokens - self.cache_creation_input_tokens - self.cache_read_input_tokens)
        return {
            "input_tokens": uncached,
            "uncached_input_tokens": uncached,
            "cache_creation_input_tokens": self.cache_creation_input_tokens,
            "cache_read_input_tokens": self.cache_read_input_tokens,
            "input_tokens_total": self.input_tokens,
            "output_tokens": self.output_tokens,
        }


def _usage_value(usage: Any, key: str) -> Any:
    if isinstance(usage, dict):
        return usage.get(key)
    return getattr(usage, key, None)


def normalize_token_usage(usage: Any) -> dict[str, int]:
    """Normalize Anthropic/Archon usage while preserving prompt-cache billing."""
    raw_input = int(_usage_value(usage, "input_tokens") or 0)
    explicit_uncached = _usage_value(usage, "uncached_input_tokens")
    uncached = int(explicit_uncached if explicit_uncached is not None else raw_input)
    cache_creation = int(_usage_value(usage, "cache_creation_input_tokens") or 0)
    cache_read = int(_usage_value(usage, "cache_read_input_tokens") or 0)
    component_total = uncached + cache_creation + cache_read
    explicit_total = _usage_value(usage, "input_tokens_total")
    if explicit_total is not None:
        total = int(explicit_total or 0)
        if component_total != total:
            # input_tokens is provider-dependent when an explicit total exists.
            # Keep cache buckets authoritative and place the remainder in uncached.
            if cache_creation + cache_read <= total:
                uncached = total - cache_creation - cache_read
            else:
                uncached = total
                cache_creation = 0
                cache_read = 0
    else:
        total = component_total
    return {
        "input_tokens": uncached,
        "uncached_input_tokens": uncached,
        "cache_creation_input_tokens": cache_creation,
        "cache_read_input_tokens": cache_read,
        "input_tokens_total": total,
        "output_tokens": int(_usage_value(usage, "output_tokens") or 0),
    }


def _row_usage(row: dict[str, Any]) -> dict[str, Any]:
    usage = dict(row.get("usage") or {})
    for key in (
        "input_tokens",
        "uncached_input_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
        "input_tokens_total",
        "output_tokens",
    ):
        if key not in usage and row.get(key) is not None:
            usage[key] = row[key]
    return usage


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(row, ensure_ascii=False, default=str) + "\n"
    with _append_jsonl_lock:
        with path.open("a") as f:
            f.write(line)


def parse_agent_log(path: Path) -> AgentStats:
    """Parse cktarchon/Archon-like JSONL into legacy NL2Chip counters."""
    stats = AgentStats()
    if not path.exists():
        return stats
    for line in path.read_text(errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        event = row.get("event") or row.get("type")
        if event in {"assistant", "assistant_turn"}:
            stats.turns = max(stats.turns, int(row.get("turn", -1)) + 1)
            stats.add_usage(normalize_token_usage(_row_usage(row)))
        elif event == "turn_usage":
            stats.add_usage(normalize_token_usage(_row_usage(row)))
        elif event == "tool_call":
            name = str(row.get("name") or row.get("tool") or row.get("tool_name") or "unknown")
            stats.tool_counts[name] = stats.tool_counts.get(name, 0) + 1
            name_l = name.lower()
            tool_input = str(row.get("input") or row.get("command") or "")
            if name_l == "lean_check":
                stats.compile_checks += 1
            elif name_l == "bash" and ("lake" in tool_input or "cktarchon.tools lean-check" in tool_input):
                stats.compile_checks += 1
        elif event in {"session_meta", "thread.started"}:
            stats.session_id = row.get("session_id") or row.get("thread_id") or stats.session_id
        elif event == "session_end":
            usage = _row_usage(row)
            has_breakdown = any(
                usage.get(key) is not None
                for key in (
                    "uncached_input_tokens",
                    "cache_creation_input_tokens",
                    "cache_read_input_tokens",
                )
            )
            if has_breakdown:
                stats.set_usage(normalize_token_usage(usage))
            elif usage.get("input_tokens_total") is not None:
                total = int(usage.get("input_tokens_total") or 0)
                known_cache = stats.cache_creation_input_tokens + stats.cache_read_input_tokens
                if known_cache <= total:
                    stats.uncached_input_tokens = total - known_cache
                    stats.input_tokens = total
                else:
                    stats.set_usage(normalize_token_usage(usage))
                if usage.get("output_tokens") is not None:
                    stats.output_tokens = int(usage.get("output_tokens") or 0)
            elif not stats.input_tokens and usage.get("input_tokens") is not None:
                stats.set_usage(normalize_token_usage(usage))
            elif usage.get("output_tokens") is not None and not stats.output_tokens:
                stats.output_tokens = int(usage.get("output_tokens") or 0)
            stats.turns = max(stats.turns, int(row.get("turns") or row.get("num_turns") or row.get("num_items") or 0))
    return stats
