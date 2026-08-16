from __future__ import annotations

import fnmatch
import json
import os
import re
import signal
import shlex
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import anthropic

from .env import ensure_runtime_env
from .logs import AgentStats, append_jsonl

_ANTHROPIC_API_KEY: str | None = None
_ANTHROPIC_BASE_URL: str | None = None

def configure_anthropic_credentials_from_env() -> None:
    global _ANTHROPIC_API_KEY, _ANTHROPIC_BASE_URL
    api_key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
    base_url = os.environ.get("ANTHROPIC_BASE_URL")
    if api_key:
        _ANTHROPIC_API_KEY = api_key
    if base_url:
        _ANTHROPIC_BASE_URL = base_url
    for secret_name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL"):
        os.environ.pop(secret_name, None)

configure_anthropic_credentials_from_env()

BASH_TIMEOUT = 180
MAX_BASH_OUTPUT = 12000
MAX_FILE_SIZE = 120000
MAX_GREP_RESULTS = 120
MAX_GLOB_RESULTS = 300
API_MAX_RETRIES = max(1, int(os.environ.get("SPARKLE_API_MAX_RETRIES", "20")))
API_BASE_DELAY = int(os.environ.get("SPARKLE_API_BASE_DELAY", "30"))
API_MAX_DELAY = int(os.environ.get("SPARKLE_API_MAX_DELAY", "300"))
RETRIABLE_PROVIDER_ERROR_PATTERNS = (
    "价格尚未由管理员配置",
    "has not been priced by administrator",
    "has not been priced by the administrator",
    "upstream request failed",
    "provider error",
)


class RequestTimeoutError(TimeoutError):
    """Raised by the outer CktArchon API watchdog."""


def _raise_request_timeout(signum: int, frame: Any) -> None:
    raise RequestTimeoutError("Anthropic messages.create exceeded cktarchon api_timeout")


def _error_text(exc: Exception) -> str:
    pieces = [str(exc)]
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            pieces.extend(str(v) for v in err.values() if v)
        elif err:
            pieces.append(str(err))
    return " ".join(p for p in pieces if p).lower()


def _is_retriable_api_error(exc: Exception) -> bool:
    if isinstance(exc, (anthropic.RateLimitError, anthropic.APIConnectionError)):
        return True
    if not isinstance(exc, anthropic.APIStatusError):
        return False
    if exc.status_code >= 500 or exc.status_code == 429:
        return True
    if exc.status_code == 400:
        text = _error_text(exc)
        return any(pattern in text for pattern in RETRIABLE_PROVIDER_ERROR_PATTERNS)
    return False


def _retry_delay_seconds(attempt: int) -> int:
    return min(API_BASE_DELAY * (2**attempt), API_MAX_DELAY)


def _strip_lean_comments(source: str) -> str:
    text = re.sub(r"/\-.*?\-/", " ", str(source or ""), flags=re.DOTALL)
    return re.sub(r"--[^\n]*", " ", text)


def sparkle_candidate_hints(source: str) -> list[str]:
    """Return short deterministic recipes for common Sparkle frontend traps."""

    text = _strip_lean_comments(source)
    issues: list[str] = []

    def add(message: str) -> None:
        if message not in issues:
            issues.append(message)

    if "CKTARCHON_IMPLEMENTATION_REQUIRED" in source:
        add("The compile-safe scaffold is not a solution; implement every named output, then remove CKTARCHON_IMPLEMENTATION_REQUIRED.")
    if re.search(r"\bNat\.(?:clog2|ceilLog2)\b", text):
        add("Replace Nat.clog2/Nat.ceilLog2 with Sparkle.IR.Type.DimExpr.clog2Nat.")
    if re.search(r"\bNat\.log2\b", text):
        add("Do not use floor Nat.log2 for a hardware width; use Nat.max 1 (Sparkle.IR.Type.DimExpr.clog2Nat n).")
    if re.search(r"\bSignal\.(?:mapBV|uge|ule|ugt|sgt|sge|sle)\b", text):
        add("Use only checked Sparkle APIs: Signal.ult/Signal.slt plus Signal.mux; mapBV/uge/ule/ugt/sge/sle are unavailable.")
    if re.search(r"\bList\.enum\b", text):
        add("List.enum is unavailable here; use a fixed checked expression or List.range only outside synthesized signal logic.")
    if re.search(r"\b(?:True|False)\b", text):
        add("Lean Bool literals are lowercase true/false; uppercase True/False are propositions and break Signal types.")
    if re.search(r"\blet\s+\w+\s*:=\s*[^\n]+\s:\s*Signal\b", text):
        add("Typed lets must be `let x : Signal dom T := expr`, never `let x := expr : Signal dom T`.")
    if re.search(r"\b(?:sorry|admit)\b", text):
        add("Remove sorry/admit; synthesis rejects sorryAx even if Lean accepts the declaration.")

    nat_names: set[str] = set()
    for match in re.finditer(r"[\{(]\s*([A-Za-z_]\w*(?:\s+[A-Za-z_]\w*)*)\s*:\s*Nat\b", text):
        nat_names.update(match.group(1).split())
    if nat_names:
        param_pattern = "|".join(re.escape(name) for name in sorted(nat_names))
        if re.search(rf"\bif\b[^\n]*(?:{param_pattern})\b", text):
            add("A top-level Nat parameter controls a Lean host `if`; replace it with a closed dimension expression or hardware Signal.mux.")
    if "Signal.loop" in text:
        add("Signal.loop is a fixed point `Signal A -> Signal A`: return exactly the same state type (usually `dff init next`) and derive visible outputs outside the loop.")
    if re.search(r"\b(?:dff|resetHigh|resetLow)\s*\(\s*(?:Signal\.|bundle)", text):
        add("dff/resetHigh/resetLow initial/reset values are bare BitVec/Bool values, not Signal or bundle2 results.")
    if re.search(r"\b(?:zext|trunc)\s+\(?\s*[0-9]+#", text):
        add("zext/trunc operate on Signal values; wrap a literal with an explicitly typed Signal.pure or use BitVec.ofNat at a typed boundary.")
    return issues[:6]


def format_sparkle_candidate_hints(source: str) -> str:
    issues = sparkle_candidate_hints(source)
    if not issues:
        return ""
    return (
        "\n\nLocal deterministic Sparkle preflight:\n"
        + "\n".join(f"{index}. {issue}" for index, issue in enumerate(issues, 1))
        + "\nRun lean_check immediately after the smallest edit."
    )


TOOLS: list[dict[str, Any]] = [
    {
        "name": "bash",
        "description": "Run a shell command from the NL2Chip project root. Use for lake build, tests, and inspection; not for writing source files.",
        "input_schema": {
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
    },
    {
        "name": "read_file",
        "description": "Read a file relative to the NL2Chip project root.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "offset": {"type": "integer"},
                "limit": {"type": "integer"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "Write an allowed output file. For circuit generation this is normally Generated/<prob_id>.lean.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"],
        },
    },
    {
        "name": "edit_file",
        "description": "Replace one unique string in an allowed output file.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "old_string": {"type": "string"},
                "new_string": {"type": "string"},
            },
            "required": ["path", "old_string", "new_string"],
        },
    },
    {
        "name": "grep",
        "description": "Search text files under a path relative to the project root.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string"},
                "path": {"type": "string"},
                "include": {"type": "string"},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "glob",
        "description": "List files matching a glob relative to the project root.",
        "input_schema": {
            "type": "object",
            "properties": {"pattern": {"type": "string"}},
            "required": ["pattern"],
        },
    },
    {
        "name": "list_directory",
        "description": "List directory entries relative to the project root.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    {
        "name": "lean_check",
        "description": (
            "Compile-check Lean code using the persistent Lean REPL when available. "
            "Prefer the `code` argument for iterative checks; use `path` to check a file on disk."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": (
                        "Lean 4 code to verify. Do not include import/open lines when following the NL2Chip prompt; "
                        "the REPL prelude is already loaded."
                    ),
                },
                "path": {"type": "string", "description": "Optional path relative to the project root."},
            },
        },
    },
]


@dataclass
class PathGuard:
    project_root: Path
    prob_id: str
    extra_write_globs: tuple[str, ...] = ()
    strict_exact_generated: bool = False

    def resolve(self, rel_path: str | Path) -> Path:
        path = (self.project_root / rel_path).resolve()
        root = self.project_root.resolve()
        if path != root and root not in path.parents:
            raise ValueError(f"Path escapes project root: {rel_path}")
        return path

    def _resolved_write_target(self, rel_path: str | Path) -> Path | None:
        try:
            path = self.resolve(rel_path)
            normalized = path.relative_to(self.project_root.resolve()).as_posix()
        except (OSError, TypeError, ValueError):
            return None
        exact_generated = f"Generated/{self.prob_id}.lean"
        if self.strict_exact_generated and normalized.startswith("Generated/"):
            return path if normalized == exact_generated else None

        allowed = [
            exact_generated,
            f"Generated/{self.prob_id}_*.lean",
            f"cktarchon_work/{self.prob_id}/**",
            *self.extra_write_globs,
        ]
        if not any(fnmatch.fnmatch(normalized, pat) for pat in allowed):
            return None
        return path

    def is_write_allowed(self, rel_path: str | Path) -> bool:
        return self._resolved_write_target(rel_path) is not None

    def is_parallel_generated_candidate(self, rel_path: str | Path) -> bool:
        """Hide unverified candidates produced by other concurrent workers."""

        try:
            path = self.resolve(os.fspath(rel_path))
            normalized = path.relative_to(self.project_root.resolve()).as_posix()
        except (TypeError, ValueError):
            return True
        if not normalized.startswith("Generated/") or not normalized.endswith(".lean"):
            return False
        return normalized != f"Generated/{self.prob_id}.lean"

    def require_write_allowed(self, rel_path: str | Path) -> Path:
        path = self._resolved_write_target(rel_path)
        if path is None:
            raise PermissionError(
                f"Write denied for {rel_path}. Allowed outputs are Generated/{self.prob_id}.lean and cktarchon_work/{self.prob_id}/."
            )
        return path


@dataclass
class AnthropicHarnessRunner:
    project_root: Path
    prob_id: str
    model: str
    role: str
    log_base: Path
    system_prompt: str
    max_tokens: int = 16384
    lean_repl: Any | None = None
    api_timeout: float | None = 300.0
    extra_write_globs: tuple[str, ...] = ()
    local_guardrails: bool = False
    tool_counts: dict[str, int] = field(default_factory=dict)
    compile_checks: int = 0
    _tool_sequence: int = field(default=0, init=False, repr=False)
    _last_complete_code: str | None = field(default=None, init=False, repr=False)
    _last_complete_sequence: int = field(default=-1, init=False, repr=False)

    def __post_init__(self) -> None:
        ensure_runtime_env()
        self.project_root = self.project_root.resolve()
        self.guard = PathGuard(
            self.project_root,
            self.prob_id,
            self.extra_write_globs,
            strict_exact_generated=self.local_guardrails,
        )
        kwargs: dict[str, Any] = {}
        api_key = _ANTHROPIC_API_KEY
        base_url = _ANTHROPIC_BASE_URL
        if api_key:
            kwargs["api_key"] = api_key
        if base_url:
            kwargs["base_url"] = base_url
        if self.api_timeout is not None:
            kwargs["timeout"] = self.api_timeout
        self.client = anthropic.Anthropic(**kwargs)

    @property
    def log_path(self) -> Path:
        return Path(str(self.log_base) + ".jsonl")

    def run(self, prompt: str, *, max_turns: int) -> AgentStats:
        messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
        stats = AgentStats()
        started = time.monotonic()
        append_jsonl(self.log_path, {
            "event": "session_start",
            "role": self.role,
            "model": self.model,
            "prob_id": self.prob_id,
            "max_turns": max_turns,
            "max_tokens": self.max_tokens,
        })
        append_jsonl(self.log_path, {"event": "prompt", "prompt": prompt})
        self._seed_compile_safe_candidate()
        request_tools = (
            [tool for tool in TOOLS if tool["name"] != "bash"]
            if self.local_guardrails
            else TOOLS
        )

        for turn in range(max_turns):
            try:
                response = self._create_message_with_retries(
                    model=self.model,
                    max_tokens=self.max_tokens,
                    system=self.system_prompt,
                    tools=request_tools,
                    messages=messages,
                )
            except Exception:
                self._autosave_last_complete_candidate()
                raise
            usage = {
                "input_tokens": getattr(response.usage, "input_tokens", 0),
                "output_tokens": getattr(response.usage, "output_tokens", 0),
            }
            stats.input_tokens += int(usage["input_tokens"] or 0)
            stats.output_tokens += int(usage["output_tokens"] or 0)
            stats.turns = turn + 1
            append_jsonl(self.log_path, {
                "event": "assistant",
                "turn": turn,
                "stop_reason": response.stop_reason,
                "usage": usage,
                "content": _jsonable_blocks(response.content),
            })
            messages.append({"role": "assistant", "content": response.content})

            tool_results: list[dict[str, Any]] = []
            for block in response.content:
                if getattr(block, "type", None) != "tool_use":
                    continue
                name = block.name
                tool_input = dict(block.input or {})
                self._tool_sequence += 1
                self.tool_counts[name] = self.tool_counts.get(name, 0) + 1
                if name == "lean_check" or (name == "bash" and "lake" in str(tool_input.get("command", ""))):
                    self.compile_checks += 1
                append_jsonl(self.log_path, {
                    "event": "tool_call",
                    "turn": turn,
                    "id": block.id,
                    "name": name,
                    "input": tool_input,
                })
                result = self._execute_tool(name, tool_input)
                append_jsonl(self.log_path, {
                    "event": "tool_result",
                    "turn": turn,
                    "id": block.id,
                    "name": name,
                    "result_preview": result[:4000],
                    "result_bytes": len(result.encode()),
                })
                tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": result})

            if not tool_results:
                if (
                    self.local_guardrails
                    and self._last_complete_code is None
                    and turn + 1 < max_turns
                ):
                    messages.append({
                        "role": "user",
                        "content": (
                            "No tool was used, so the candidate is not verified. Read/edit the target "
                            "and run lean_check now; a marked scaffold or prose-only answer is incomplete."
                        ),
                    })
                    continue
                break
            messages.append({"role": "user", "content": tool_results})

        self._autosave_last_complete_candidate()
        stats.tool_counts = dict(self.tool_counts)
        stats.compile_checks = self.compile_checks
        append_jsonl(self.log_path, {
            "event": "session_end",
            "role": self.role,
            "prob_id": self.prob_id,
            "turns": stats.turns,
            "usage": {"input_tokens": stats.input_tokens, "output_tokens": stats.output_tokens},
            "tool_counts": stats.tool_counts,
            "compile_checks": stats.compile_checks,
            "elapsed_seconds": round(time.monotonic() - started, 3),
        })
        return stats

    def _create_message_with_retries(self, **kwargs: Any) -> Any:
        last_exc: Exception | None = None
        for attempt in range(API_MAX_RETRIES):
            try:
                return self._messages_create_with_alarm(**kwargs)
            except (anthropic.RateLimitError, anthropic.APIStatusError, anthropic.APIConnectionError, RequestTimeoutError) as exc:
                last_exc = exc
                if not isinstance(exc, RequestTimeoutError) and not _is_retriable_api_error(exc):
                    raise
                if attempt == API_MAX_RETRIES - 1:
                    raise
                delay = _retry_delay_seconds(attempt)
                append_jsonl(self.log_path, {
                    "event": "api_retry",
                    "attempt": attempt + 1,
                    "delay_seconds": delay,
                    "error_type": type(exc).__name__,
                    "status_code": getattr(exc, "status_code", None),
                    "error_preview": str(exc)[:1000],
                })
                time.sleep(delay)
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("client.messages.create failed without raising an exception")

    def _messages_create_with_alarm(self, **kwargs: Any) -> Any:
        if self.api_timeout is None or self.api_timeout <= 0:
            return self.client.messages.create(**kwargs)
        try:
            old_handler = signal.getsignal(signal.SIGALRM)
            old_timer = signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, _raise_request_timeout)
            signal.setitimer(signal.ITIMER_REAL, float(self.api_timeout))
        except ValueError:
            return self.client.messages.create(**kwargs)
        try:
            return self.client.messages.create(**kwargs)
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, old_handler)
            if old_timer[0] > 0:
                signal.setitimer(signal.ITIMER_REAL, old_timer[0], old_timer[1])

    def _execute_tool(self, name: str, inputs: dict[str, Any]) -> str:
        try:
            if name == "bash":
                return self._bash(str(inputs.get("command", "")))
            if name == "read_file":
                return self._read_file(str(inputs["path"]), inputs.get("offset"), inputs.get("limit"))
            if name == "write_file":
                return self._write_file(str(inputs["path"]), str(inputs.get("content", "")))
            if name == "edit_file":
                return self._edit_file(str(inputs["path"]), str(inputs["old_string"]), str(inputs["new_string"]))
            if name == "grep":
                return self._grep(str(inputs["pattern"]), str(inputs.get("path") or "."), inputs.get("include"))
            if name == "glob":
                return self._glob(str(inputs["pattern"]))
            if name == "list_directory":
                return self._list_directory(str(inputs.get("path") or "."))
            if name == "lean_check":
                return self._lean_check(path=inputs.get("path"), code=inputs.get("code"))
            return f"Error: unknown tool {name}"
        except Exception as exc:
            return f"Error: {type(exc).__name__}: {exc}"

    def _bash(self, command: str) -> str:
        if self.local_guardrails:
            return (
                "Error: bash is disabled in local guardrails mode. "
                "Use read_file, grep, glob, list_directory, edit_file, and lean_check instead."
            )
        if not command.strip():
            return "Error: empty command"
        blocked = re.compile(r"\b(rm\s+-rf|git\s+reset|git\s+checkout|pkill|killall|sudo|scp|ssh)\b")
        if blocked.search(command):
            return "Error: command rejected by cktarchon safety policy"
        try:
            proc = subprocess.run(
                ["bash", "-lc", command],
                cwd=self.project_root,
                env={**os.environ, "LC_ALL": "C.UTF-8"},
                capture_output=True,
                text=True,
                timeout=BASH_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            return f"Error: command timed out after {BASH_TIMEOUT}s"
        output = (proc.stdout or "")
        if proc.stderr:
            output += ("\n" if output else "") + "STDERR:\n" + proc.stderr
        if proc.returncode:
            output += f"\n(exit code: {proc.returncode})"
        if len(output) > MAX_BASH_OUTPUT:
            output = output[:MAX_BASH_OUTPUT] + f"\n\n... [truncated at {MAX_BASH_OUTPUT} chars]"
        return output or "(no output)"

    def _read_file(self, rel_path: str, offset: Any = None, limit: Any = None) -> str:
        if self.local_guardrails and self.guard.is_parallel_generated_candidate(rel_path):
            return (
                "Error: reading another worker's unverified Generated candidate is blocked; "
                "use the typed scaffold and curated Benchmark examples."
            )
        path = self.guard.resolve(rel_path)
        if path.exists() and path.is_dir():
            return self._list_directory(rel_path)
        if not path.exists() or not path.is_file():
            return f"Error: file not found: {rel_path}"
        text = path.read_text(errors="replace")
        if offset is not None or limit is not None:
            lines = text.splitlines(keepends=True)
            start = max(0, int(offset or 1) - 1)
            end = start + int(limit) if limit else len(lines)
            text = "".join(f"{start + idx + 1}\t{line}" for idx, line in enumerate(lines[start:end]))
        if len(text) > MAX_FILE_SIZE:
            text = text[:MAX_FILE_SIZE] + f"\n\n... [truncated at {MAX_FILE_SIZE} chars]"
        return text

    def _write_file(self, rel_path: str, content: str) -> str:
        path = self.guard.require_write_allowed(rel_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return (
            f"Wrote {len(content)} chars to {rel_path}"
            + (format_sparkle_candidate_hints(content)
               if self.local_guardrails else "")
        )

    def _edit_file(self, rel_path: str, old: str, new: str) -> str:
        path = self.guard.require_write_allowed(rel_path)
        if not path.exists():
            return f"Error: file not found: {rel_path}"
        text = path.read_text(errors="replace")
        count = text.count(old)
        if count != 1:
            return f"Error: old_string matched {count} times; expected exactly 1"
        updated = text.replace(old, new, 1)
        path.write_text(updated)
        return (
            f"Edited {rel_path}: replaced {len(old)} chars with {len(new)} chars"
            + (format_sparkle_candidate_hints(updated)
               if self.local_guardrails else "")
        )

    def _is_generated_target(self, rel_path: str | Path) -> bool:
        try:
            path = self.guard.resolve(rel_path)
            target = self.guard.resolve(f"Generated/{self.prob_id}.lean")
        except (OSError, TypeError, ValueError):
            return False
        return path == target

    def _autosave_last_complete_candidate(self) -> None:
        if self._last_complete_code is None:
            return
        rel_path = f"Generated/{self.prob_id}.lean"
        path = self.guard.require_write_allowed(rel_path)
        content = _with_sparkle_prelude(self._last_complete_code)
        if path.exists() and path.read_text(errors="replace") == content:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        append_jsonl(self.log_path, {
            "event": "auto_saved_candidate",
            "path": rel_path,
            "source": "last_complete_lean_check",
            "chars": len(content),
            "tool_sequence": self._last_complete_sequence,
        })

    def _seed_compile_safe_candidate(self) -> None:
        if self.lean_repl is None:
            return
        rel_path = f"Generated/{self.prob_id}.lean"
        path = self.guard.resolve(rel_path)
        if not path.exists() or not path.is_file():
            return
        try:
            code = path.read_text(errors="replace")
            result = self.lean_repl.check_file(path)
        except Exception as exc:
            append_jsonl(self.log_path, {
                "event": "compile_safe_seed_error",
                "path": rel_path,
                "error": f"{type(exc).__name__}: {exc}"[:1000],
            })
            return
        self._remember_complete_candidate(code, result)
        if self._last_complete_code is not None:
            append_jsonl(self.log_path, {
                "event": "seeded_compile_safe_candidate",
                "path": rel_path,
                "chars": len(code),
            })

    def _grep(self, pattern: str, rel_path: str, include: Any = None) -> str:
        root = self.guard.resolve(rel_path)
        if not root.exists():
            return f"Error: path not found: {rel_path}"
        try:
            regex = re.compile(pattern)
        except re.error as exc:
            return f"Error: invalid regex: {exc}"
        files = [root] if root.is_file() else [p for p in root.rglob("*") if p.is_file()]
        rows: list[str] = []
        for path in sorted(files):
            rel = path.relative_to(self.project_root)
            if any(part in {".git", ".lake", ".venv", "__pycache__"} for part in rel.parts):
                continue
            if self.local_guardrails and self.guard.is_parallel_generated_candidate(rel):
                continue
            if include and not fnmatch.fnmatch(path.name, str(include)):
                continue
            try:
                text = path.read_text(errors="replace")
            except OSError:
                continue
            for line_no, line in enumerate(text.splitlines(), 1):
                if regex.search(line):
                    rows.append(f"{rel}:{line_no}: {line.rstrip()}")
                    if len(rows) >= MAX_GREP_RESULTS:
                        rows.append(f"... [truncated at {MAX_GREP_RESULTS} matches]")
                        return "\n".join(rows)
        return "\n".join(rows) if rows else f"No matches for {pattern!r}"

    def _glob(self, pattern: str) -> str:
        rows = []
        for path in sorted(self.project_root.glob(pattern)):
            try:
                rel = path.relative_to(self.project_root)
            except ValueError:
                continue
            if any(part in {".git", ".lake", ".venv", "__pycache__"} for part in rel.parts):
                continue
            if self.local_guardrails and self.guard.is_parallel_generated_candidate(rel):
                continue
            rows.append(str(rel))
            if len(rows) >= MAX_GLOB_RESULTS:
                rows.append(f"... [truncated at {MAX_GLOB_RESULTS} matches]")
                break
        return "\n".join(rows) if rows else f"No files matching {pattern!r}"

    def _list_directory(self, rel_path: str) -> str:
        path = self.guard.resolve(rel_path)
        if not path.exists() or not path.is_dir():
            return f"Error: directory not found: {rel_path}"
        rows = []
        for entry in sorted(path.iterdir()):
            if entry.name.startswith(".") or entry.name == "__pycache__":
                continue
            try:
                entry_rel = entry.relative_to(self.project_root)
            except ValueError:
                continue
            if self.local_guardrails and self.guard.is_parallel_generated_candidate(entry_rel):
                continue
            suffix = "/" if entry.is_dir() else ""
            rows.append(f"{entry.name}{suffix}")
        return "\n".join(rows) if rows else "(empty directory)"

    def _lean_check(self, path: Any = None, code: Any = None) -> str:
        if code is not None and str(code).strip():
            return self._lean_check_code(str(code))
        rel_path = str(path or f"Generated/{self.prob_id}.lean")
        if self.local_guardrails and self.guard.is_parallel_generated_candidate(rel_path):
            return (
                "Error: lean_check cannot inspect another worker's unverified candidate; "
                "check only the current target or inline code."
            )
        path = self.guard.resolve(rel_path)
        if not path.exists():
            return f"Error: file not found: {rel_path}"
        if self.lean_repl is not None:
            try:
                result = self.lean_repl.check_file(path)
                if self._is_generated_target(rel_path):
                    source = path.read_text(errors="replace")
                    self._remember_complete_candidate(source, result)
                else:
                    source = ""
                return self._format_lean_result(result) + (
                    format_sparkle_candidate_hints(source)
                    if self.local_guardrails else ""
                )
            except Exception as exc:
                return f"REPL failed, falling back unavailable in-tool: {type(exc).__name__}: {exc}"
        module = rel_path.replace("/", ".").removesuffix(".lean")
        return self._bash(f"lake build {module}")

    def _lean_check_code(self, code: str) -> str:
        if self.lean_repl is not None:
            try:
                result = self.lean_repl.check_code(_with_repl_opens(code))
                self._remember_complete_candidate(code, result)
                return self._format_lean_result(result) + (
                    format_sparkle_candidate_hints(code)
                    if self.local_guardrails else ""
                )
            except Exception as exc:
                return f"Error: REPL failed: {type(exc).__name__}: {exc}"
        temp_rel = f"cktarchon_work/{self.prob_id}/lean_check.lean"
        temp_path = self.guard.require_write_allowed(temp_rel)
        temp_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path.write_text(_with_sparkle_prelude(code), encoding="utf-8")
        return self._bash(f"lake env lean {shlex.quote(temp_rel)}")

    def _remember_complete_candidate(self, code: str, result: Any) -> None:
        generated_verilog = str(getattr(result, "verilog", "") or "")
        if not (
            bool(getattr(result, "passed", False))
            and bool(getattr(result, "complete", False))
            and "#synthesizeVerilog" in code
            and generated_verilog.strip()
            and "CKTARCHON_IMPLEMENTATION_REQUIRED" not in code
        ):
            return
        self._last_complete_code = code
        self._last_complete_sequence = self._tool_sequence
        self._autosave_last_complete_candidate()

    def _format_lean_result(self, result: Any) -> str:
        summary = getattr(result, "summary", None)
        if summary:
            lines = [str(summary)]
        else:
            passed = bool(getattr(result, "passed", False))
            complete = bool(getattr(result, "complete", False))
            elapsed = float(getattr(result, "elapsed", 0.0) or 0.0)
            if passed:
                tag = "COMPLETE" if complete else "OK (has sorry)"
                lines = [f"PASS {tag} ({elapsed:.2f}s)"]
            else:
                errors = getattr(result, "errors", []) or []
                lines = [f"FAIL ({elapsed:.2f}s, {len(errors)} errors)"]
        error_text = getattr(result, "error_text", "")
        errors = getattr(result, "errors", []) or []
        if error_text and not (self.local_guardrails and errors):
            lines.append(str(error_text)[:4000])
        seen_errors: set[tuple[str, str]] = set()
        error_limit = 4 if self.local_guardrails else 12
        for err in errors:
            pos = err.get("pos", {}) if isinstance(err, dict) else {}
            loc = f"line {pos.get('line', '?')}:{pos.get('column', '?')}"
            data = err.get("data", err) if isinstance(err, dict) else err
            key = (loc, re.sub(r"\s+", " ", str(data)).strip())
            if key in seen_errors:
                continue
            seen_errors.add(key)
            lines.append(f"[error {loc}] {data}")
            if len(seen_errors) >= error_limit:
                break
        warning_limit = 3 if self.local_guardrails else 8
        for warning in (getattr(result, "warnings", []) or [])[:warning_limit]:
            pos = warning.get("pos", {}) if isinstance(warning, dict) else {}
            loc = f"line {pos.get('line', '?')}:{pos.get('column', '?')}"
            data = warning.get("data", warning) if isinstance(warning, dict) else warning
            lines.append(f"[warning {loc}] {data}")
        verilog = getattr(result, "verilog", "")
        if verilog:
            if self.local_guardrails:
                lines.append(
                    f"PASS: generated Verilog is available ({len(str(verilog))} chars); inspect it only if needed."
                )
            else:
                lines.append("=== Generated Verilog ===")
                lines.append(str(verilog)[:5000])
        return "\n".join(lines)


@dataclass
class AnthropicTextRunner:
    """One-shot text-only runner for public-spec planning within the turn budget."""

    model: str
    role: str
    log_base: Path
    system_prompt: str
    max_tokens: int = 8192
    api_timeout: float | None = 300.0

    def __post_init__(self) -> None:
        ensure_runtime_env()
        kwargs: dict[str, Any] = {}
        api_key = _ANTHROPIC_API_KEY
        base_url = _ANTHROPIC_BASE_URL
        if api_key:
            kwargs["api_key"] = api_key
        if base_url:
            kwargs["base_url"] = base_url
        if self.api_timeout is not None:
            kwargs["timeout"] = self.api_timeout
        self.client = anthropic.Anthropic(**kwargs)

    @property
    def log_path(self) -> Path:
        return Path(str(self.log_base) + ".jsonl")

    def run(self, prompt: str, *, max_turns: int = 1) -> tuple[str, AgentStats]:
        if max_turns <= 0:
            return "", AgentStats()
        started = time.monotonic()
        append_jsonl(self.log_path, {
            "event": "session_start",
            "role": self.role,
            "model": self.model,
            "max_turns": 1,
            "max_tokens": self.max_tokens,
        })
        append_jsonl(self.log_path, {"event": "prompt", "prompt": prompt})
        response = self._messages_create_with_retries(
            model=self.model,
            max_tokens=self.max_tokens,
            system=self.system_prompt,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "\n".join(
            str(getattr(block, "text", ""))
            for block in response.content
            if getattr(block, "type", None) == "text"
        ).strip()
        usage = {
            "input_tokens": getattr(response.usage, "input_tokens", 0),
            "output_tokens": getattr(response.usage, "output_tokens", 0),
        }
        stats = AgentStats(
            input_tokens=int(usage["input_tokens"] or 0),
            output_tokens=int(usage["output_tokens"] or 0),
            turns=1,
        )
        append_jsonl(self.log_path, {
            "event": "assistant",
            "turn": 0,
            "stop_reason": response.stop_reason,
            "usage": usage,
            "content": _jsonable_blocks(response.content),
        })
        append_jsonl(self.log_path, {
            "event": "session_end",
            "role": self.role,
            "turns": 1,
            "usage": usage,
            "elapsed_seconds": round(time.monotonic() - started, 3),
        })
        return text, stats

    def _messages_create_with_retries(self, **kwargs: Any) -> Any:
        last_exc: Exception | None = None
        for attempt in range(API_MAX_RETRIES):
            try:
                return self._messages_create_with_alarm(**kwargs)
            except (anthropic.RateLimitError, anthropic.APIStatusError, anthropic.APIConnectionError, RequestTimeoutError) as exc:
                last_exc = exc
                if not isinstance(exc, RequestTimeoutError) and not _is_retriable_api_error(exc):
                    raise
                if attempt == API_MAX_RETRIES - 1:
                    raise
                delay = _retry_delay_seconds(attempt)
                append_jsonl(self.log_path, {
                    "event": "api_retry",
                    "attempt": attempt + 1,
                    "delay_seconds": delay,
                    "error_type": type(exc).__name__,
                    "status_code": getattr(exc, "status_code", None),
                    "error_preview": str(exc)[:1000],
                })
                time.sleep(delay)
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("client.messages.create failed without raising an exception")

    def _messages_create_with_alarm(self, **kwargs: Any) -> Any:
        if self.api_timeout is None or self.api_timeout <= 0:
            return self.client.messages.create(**kwargs)
        try:
            old_handler = signal.getsignal(signal.SIGALRM)
            old_timer = signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, _raise_request_timeout)
            signal.setitimer(signal.ITIMER_REAL, float(self.api_timeout))
        except ValueError:
            return self.client.messages.create(**kwargs)
        try:
            return self.client.messages.create(**kwargs)
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, old_handler)
            if old_timer[0] > 0:
                signal.setitimer(signal.ITIMER_REAL, old_timer[0], old_timer[1])


def _jsonable_blocks(blocks: Any) -> list[dict[str, Any]]:
    rows = []
    for block in blocks:
        if hasattr(block, "model_dump"):
            rows.append(block.model_dump())
        elif hasattr(block, "dict"):
            rows.append(block.dict())
        else:
            rows.append({"repr": repr(block)})
    return rows


def _with_sparkle_prelude(code: str) -> str:
    stripped = code.lstrip()
    if stripped.startswith("import "):
        return code
    return (
        "import Sparkle\n"
        "import Sparkle.Compiler.Elab\n\n"
        "open Sparkle.Core.Domain\n"
        "open Sparkle.Core.Signal\n\n"
        "open Sparkle.Library.RTL\n\n"
        + code
        + ("\n" if not code.endswith("\n") else "")
    )


def _with_repl_opens(code: str) -> str:
    if "open Sparkle.Library.RTL" in code or code.lstrip().startswith("import "):
        return code
    return "open Sparkle.Library.RTL\n\n" + code
