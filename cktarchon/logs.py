from __future__ import annotations

import json
from threading import Lock
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


_append_jsonl_lock = Lock()


@dataclass
class AgentStats:
    input_tokens: int = 0
    output_tokens: int = 0
    turns: int = 0
    compile_checks: int = 0
    tool_counts: dict[str, int] = field(default_factory=dict)
    session_id: str | None = None


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
            usage = row.get("usage") or {}
            stats.input_tokens += int(usage.get("input_tokens") or row.get("input_tokens") or 0)
            stats.output_tokens += int(usage.get("output_tokens") or row.get("output_tokens") or 0)
        elif event == "turn_usage":
            stats.input_tokens += int(row.get("input_tokens") or 0)
            stats.input_tokens += int(row.get("cache_read_input_tokens") or 0)
            stats.output_tokens += int(row.get("output_tokens") or 0)
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
            usage = row.get("usage") or {}
            input_total = row.get("input_tokens_total")
            if input_total is not None:
                stats.input_tokens = int(input_total or 0)
            elif not stats.input_tokens:
                stats.input_tokens = int(usage.get("input_tokens") or row.get("input_tokens") or 0)
                stats.input_tokens += int(row.get("cache_read_input_tokens") or 0)
            if row.get("output_tokens") is not None:
                stats.output_tokens = int(row.get("output_tokens") or 0)
            elif not stats.output_tokens:
                stats.output_tokens = int(usage.get("output_tokens") or 0)
            stats.turns = max(stats.turns, int(row.get("turns") or row.get("num_turns") or row.get("num_items") or 0))
    return stats
