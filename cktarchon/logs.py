from __future__ import annotations

import json
import os
import re
import shlex
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
    budget_exhausted: bool = False
    usage_accounting_complete: bool = True
    usage_accounting_notes: list[str] = field(default_factory=list)


def _tool_command(row: dict[str, Any]) -> str:
    tool_input = row.get("input")
    if isinstance(tool_input, dict):
        for key in ("command", "cmd", "script"):
            value = tool_input.get(key)
            if isinstance(value, str):
                return value
        return ""
    if isinstance(tool_input, str):
        return tool_input
    command = row.get("command")
    return command if isinstance(command, str) else ""


def _command_tokens(command: str) -> list[list[str]]:
    """Return shell command token lists, unwrapping the common sh -c form.

    This is intentionally a conservative recognizer rather than a shell
    interpreter. An unparseable or structurally ambiguous command is not
    credited as a compile check.
    """

    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|")
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return []

    segments: list[list[str]] = []
    current: list[str] = []
    for token in tokens:
        if token and all(char in ";&|" for char in token):
            if current:
                segments.append(current)
                current = []
        else:
            current.append(token)
    if current:
        segments.append(current)

    expanded: list[list[str]] = []
    for segment in segments:
        if not segment:
            continue
        executable = os.path.basename(segment[0])
        if executable in {"bash", "sh", "dash", "zsh"}:
            for index, token in enumerate(segment[1:], start=1):
                if token in {"-c", "-lc"} and index + 1 < len(segment):
                    expanded.extend(_command_tokens(segment[index + 1]))
                    break
            else:
                expanded.append(segment)
        else:
            expanded.append(segment)
    return expanded


def _is_compile_command(command: str) -> bool:
    """Recognize an actual CktArchon/Lean compile invocation.

    Merely mentioning .lake in a grep/find argument is not a compile check. We
    only credit commands whose executable/arguments invoke
    cktarchon.tools lean-check, lake build, or lake env lean.
    """

    assignment = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=.*$")
    for original in _command_tokens(command):
        tokens = list(original)
        while tokens and assignment.match(tokens[0]):
            tokens.pop(0)
        if tokens and os.path.basename(tokens[0]) == "env":
            tokens.pop(0)
            while tokens and (tokens[0].startswith("-") or assignment.match(tokens[0])):
                tokens.pop(0)
        if not tokens:
            continue

        executable = os.path.basename(tokens[0])
        if re.fullmatch(r"python(?:3(?:\.\d+)*)?", executable):
            try:
                module_index = tokens.index("-m")
            except ValueError:
                module_index = -1
            if (
                module_index >= 0
                and tokens[module_index + 1 : module_index + 3]
                == ["cktarchon.tools", "lean-check"]
            ):
                return True
            continue

        if executable == "lean-check":
            return True
        if executable != "lake":
            continue

        args = [token for token in tokens[1:] if not token.startswith("-")]
        if args and args[0] == "build":
            return True
        if len(args) >= 2 and args[:2] == ["env", "lean"]:
            return True
    return False


def _add_accounting_note(stats: AgentStats, note: str) -> None:
    stats.usage_accounting_complete = False
    if note and note not in stats.usage_accounting_notes:
        stats.usage_accounting_notes.append(note)


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
            if name_l == "lean_check":
                stats.compile_checks += 1
            elif name_l in {"bash", "shell", "exec_command"} and _is_compile_command(_tool_command(row)):
                stats.compile_checks += 1
        elif event in {"session_meta", "thread.started"}:
            stats.session_id = row.get("session_id") or row.get("thread_id") or stats.session_id
        elif event == "cktarchon_budget_exceeded":
            stats.budget_exhausted = True
            _add_accounting_note(
                stats,
                "Codex was cancelled at its bounded action limit; final provider usage may be unavailable.",
            )
        elif event == "cktarchon_accounting_incomplete":
            _add_accounting_note(
                stats,
                str(row.get("detail") or "Agent usage accounting is incomplete."),
            )
        elif event == "session_end":
            usage = row.get("usage") or {}
            input_total = row.get("input_tokens_total")
            if input_total is not None:
                parsed_total = int(input_total or 0)
                if parsed_total or not stats.input_tokens:
                    stats.input_tokens = parsed_total
            elif not stats.input_tokens:
                stats.input_tokens = int(usage.get("input_tokens") or row.get("input_tokens") or 0)
                stats.input_tokens += int(row.get("cache_read_input_tokens") or 0)
            if row.get("output_tokens") is not None:
                parsed_total = int(row.get("output_tokens") or 0)
                if parsed_total or not stats.output_tokens:
                    stats.output_tokens = parsed_total
            elif not stats.output_tokens:
                stats.output_tokens = int(usage.get("output_tokens") or 0)
            stats.turns = max(stats.turns, int(row.get("turns") or row.get("num_turns") or row.get("num_items") or 0))
    return stats
