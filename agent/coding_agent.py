"""
Coding Agent — LLM agent with bash/read/write/edit/grep/glob tools.

Adapted from harbor/nano-meta-harness/coding_agent.py for the Sparkle HDL pipeline.
Uses the Anthropic API with tool_use to give the agent real coding capabilities.
"""
from __future__ import annotations

import fnmatch
import json
import os
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import anthropic

# ── Tool definitions (Anthropic function-calling schema) ──────────────

CODING_TOOLS = [
    {
        "name": "bash",
        "description": (
            "Execute a bash command. Use for running lake build, iverilog, "
            "checking syntax, diffing files, or any shell operation. "
            "Commands run with a 120s timeout. Working directory is the project root."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "The bash command to execute."},
            },
            "required": ["command"],
        },
    },
    {
        "name": "read_file",
        "description": (
            "Read a file's contents. Returns content with line numbers. "
            "Large files (>100KB) are truncated."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path relative to the project root."},
                "offset": {"type": "integer", "description": "Line number to start reading from (1-based)."},
                "limit": {"type": "integer", "description": "Maximum number of lines to read."},
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "Write content to a file (create or overwrite). Path must be within the project.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path relative to the project root."},
                "content": {"type": "string", "description": "The full content to write."},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "edit_file",
        "description": (
            "Edit a file by replacing an exact string match with new content. "
            "The old_string must match exactly (including whitespace)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path relative to the project root."},
                "old_string": {"type": "string", "description": "Exact string to find and replace (must be unique)."},
                "new_string": {"type": "string", "description": "The replacement string."},
            },
            "required": ["path", "old_string", "new_string"],
        },
    },
    {
        "name": "grep",
        "description": "Search file contents with a regex pattern. Returns matching lines with file paths and line numbers.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Regular expression pattern to search for."},
                "path": {"type": "string", "description": "Directory or file to search in (default: '.')."},
                "include": {"type": "string", "description": "Glob pattern to filter files (e.g. '*.lean')."},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "glob",
        "description": "Find files matching a glob pattern. Returns matching file paths.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Glob pattern (e.g. '**/*.lean', 'Benchmark/*.lean')."},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "list_directory",
        "description": "List files and subdirectories with type indicators and sizes.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path relative to the project root."},
            },
            "required": ["path"],
        },
    },
    {
        "name": "lean_check",
        "description": (
            "Instantly verify Lean 4 code using the persistent REPL. "
            "Much faster than `lake build` (~0.1s vs ~10s). "
            "The Sparkle prelude (import Sparkle, open Signal/Domain) is already loaded. "
            "Send ONLY the def/theorem/#synthesizeVerilog code — do NOT include import/open lines. "
            "Returns compilation result: errors, warnings, and generated Verilog (if any). "
            "Use this INSTEAD of `bash lake build` for checking Lean code during development."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": (
                        "Lean 4 code to verify. Do NOT include 'import Sparkle' or 'open' lines — "
                        "those are pre-loaded. Just the definitions and #synthesizeVerilog command."
                    ),
                },
            },
            "required": ["code"],
        },
    },
    {
        "name": "lean_proof_step",
        "description": (
            "Interactive tactic proof mode using the persistent REPL. "
            "Send Lean code incrementally — each call builds on a previous env. "
            "Workflow: (1) send defs with lean_proof_step → get env=N, "
            "(2) send theorem with `sorry` using env=N → see proof goals, "
            "(3) replace sorry with tactics → see updated goals, "
            "(4) repeat until no sorry remains (COMPLETE). "
            "Each proof attempt should reuse the env from step 1 (the defs), not from a failed proof. "
            "Returns: errors, proof goal states for each sorry, and the env ID."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": (
                        "Lean 4 code to send. Do NOT include import/open lines. "
                        "Can be definitions, theorems, or theorem with sorry placeholders."
                    ),
                },
                "env": {
                    "type": "integer",
                    "description": (
                        "Environment ID from a previous lean_proof_step call. "
                        "Omit to start from the Sparkle prelude env."
                    ),
                },
            },
            "required": ["code"],
        },
    },
]

MAX_FILE_SIZE = 100_000
MAX_BASH_OUTPUT = 50_000
MAX_GREP_RESULTS = 200
MAX_GLOB_RESULTS = 500
BASH_TIMEOUT = 120
MAX_TURNS = 80
API_MAX_RETRIES = 10
API_BASE_DELAY = 30


def load_env(path: Path) -> dict[str, str]:
    """Parse a simple key=value .env file."""
    env = {}
    if not path.exists():
        return env
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            v = v.strip().strip("'\"")
            env[k.strip()] = v
    return env


class CodingAgent:
    """Full coding agent with bash, read, write, edit, grep, glob, lean_check tools."""

    def __init__(
        self,
        model: str = "claude-haiku-4-5-20251001",
        max_tokens: int = 16384,
        project_root: Path = Path("."),
        log_dir: Path | None = None,
        lean_repl=None,
    ):
        self.model = model
        self.max_tokens = max_tokens
        self.project_root = project_root.resolve()
        self.log_dir = log_dir
        self.lean_repl = lean_repl  # Optional LeanREPL instance for fast compilation

        # Load API config from key.env
        env = load_env(self.project_root / "key.env")
        api_key = env.get("ANTHROPIC_API_KEY", os.environ.get("ANTHROPIC_API_KEY", ""))
        base_url = env.get("ANTHROPIC_BASE_URL", os.environ.get("ANTHROPIC_BASE_URL"))

        kwargs = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self.client = anthropic.Anthropic(**kwargs)

    def run(
        self,
        system_prompt: str,
        user_message: str,
        *,
        max_turns: int = MAX_TURNS,
    ) -> dict:
        """Run the coding agent with a system prompt and user message.

        Returns dict with usage stats: {input_tokens, output_tokens, turns}.
        """
        messages = [{"role": "user", "content": user_message}]
        total_input = 0
        total_output = 0

        # Select tools: include lean_check only if REPL is available
        tools = self._get_tools()

        log_file = None
        if self.log_dir:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            log_file = self.log_dir / "agent_session.jsonl"
            self._log_event(log_file, {
                "type": "session_start",
                "model": self.model,
                "max_tokens": self.max_tokens,
                "user_message": user_message[:500],
            })

        for turn in range(max_turns):
            for attempt in range(API_MAX_RETRIES):
                try:
                    response = self.client.messages.create(
                        model=self.model,
                        max_tokens=self.max_tokens,
                        system=system_prompt,
                        tools=tools,
                        messages=messages,
                    )
                    break
                except (anthropic.RateLimitError, anthropic.APIStatusError, anthropic.APIConnectionError) as e:
                    if isinstance(e, anthropic.APIStatusError) and e.status_code < 500 and e.status_code != 429:
                        raise
                    if attempt == API_MAX_RETRIES - 1:
                        raise
                    delay = min(API_BASE_DELAY * (2 ** attempt), 300)
                    print(f"  [Agent] {getattr(e, 'status_code', '?')} error, retrying in {delay}s...")
                    time.sleep(delay)

            total_input += response.usage.input_tokens
            total_output += response.usage.output_tokens
            messages.append({"role": "assistant", "content": response.content})

            if log_file:
                self._log_assistant_turn(log_file, turn, response)

            if response.stop_reason == "end_turn":
                break

            # Process tool calls
            tool_results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                result = self._execute_tool(block.name, block.input)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result,
                })
                if log_file:
                    self._log_event(log_file, {
                        "type": "tool_execution",
                        "turn": turn,
                        "tool_name": block.name,
                        "tool_input": block.input,
                        "tool_result_preview": result[:2000],
                    })

            if not tool_results:
                break
            messages.append({"role": "user", "content": tool_results})

        stats = {
            "input_tokens": total_input,
            "output_tokens": total_output,
            "turns": turn + 1,
            "messages": messages,
        }
        if log_file:
            self._log_event(log_file, {"type": "session_end",
                                       "input_tokens": total_input,
                                       "output_tokens": total_output,
                                       "turns": turn + 1})
        return stats

    def resume(
        self,
        system_prompt: str,
        messages: list[dict],
        feedback_message: str,
        *,
        max_turns: int = MAX_TURNS,
    ) -> dict:
        """Resume an existing agent conversation with a new feedback message.

        Args:
            system_prompt: The system prompt (same as original run).
            messages: Previous conversation messages (from run()'s returned stats).
            feedback_message: New user message with PPA feedback.
            max_turns: Max additional turns for this optimization round.

        Returns dict with: {input_tokens, output_tokens, turns, messages}.
        """
        # Append the feedback as a new user turn
        messages = list(messages)  # shallow copy to avoid mutating caller's list
        messages.append({"role": "user", "content": feedback_message})

        total_input = 0
        total_output = 0

        # Select tools: include lean_check only if REPL is available
        tools = self._get_tools()

        log_file = None
        if self.log_dir:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            log_file = self.log_dir / "agent_session.jsonl"
            self._log_event(log_file, {
                "type": "resume_start",
                "model": self.model,
                "feedback_preview": feedback_message[:500],
            })

        for turn in range(max_turns):
            for attempt in range(API_MAX_RETRIES):
                try:
                    response = self.client.messages.create(
                        model=self.model,
                        max_tokens=self.max_tokens,
                        system=system_prompt,
                        tools=tools,
                        messages=messages,
                    )
                    break
                except (anthropic.RateLimitError, anthropic.APIStatusError, anthropic.APIConnectionError) as e:
                    if isinstance(e, anthropic.APIStatusError) and e.status_code < 500 and e.status_code != 429:
                        raise
                    if attempt == API_MAX_RETRIES - 1:
                        raise
                    delay = min(API_BASE_DELAY * (2 ** attempt), 300)
                    print(f"  [Agent] {getattr(e, 'status_code', '?')} error, retrying in {delay}s...")
                    time.sleep(delay)

            total_input += response.usage.input_tokens
            total_output += response.usage.output_tokens
            messages.append({"role": "assistant", "content": response.content})

            if log_file:
                self._log_assistant_turn(log_file, turn, response)

            if response.stop_reason == "end_turn":
                break

            # Process tool calls
            tool_results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                result = self._execute_tool(block.name, block.input)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result,
                })
                if log_file:
                    self._log_event(log_file, {
                        "type": "tool_execution",
                        "turn": turn,
                        "tool_name": block.name,
                        "tool_input": block.input,
                        "tool_result_preview": result[:2000],
                    })

            if not tool_results:
                break
            messages.append({"role": "user", "content": tool_results})

        stats = {
            "input_tokens": total_input,
            "output_tokens": total_output,
            "turns": turn + 1,
            "messages": messages,
        }
        if log_file:
            self._log_event(log_file, {"type": "resume_end",
                                       "input_tokens": total_input,
                                       "output_tokens": total_output,
                                       "turns": turn + 1})
        return stats

    # ── Logging helpers ──────────────────────────────────────────

    @staticmethod
    def _log_event(log_file: Path, event: dict) -> None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        event["timestamp"] = datetime.now(timezone.utc).isoformat()
        with open(log_file, "a") as f:
            f.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")

    @staticmethod
    def _log_assistant_turn(log_file: Path, turn: int, response) -> None:
        text_parts = []
        tool_calls = []
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append({"id": block.id, "name": block.name, "input": block.input})
        event = {
            "type": "assistant_turn", "turn": turn,
            "stop_reason": response.stop_reason,
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
        }
        if text_parts:
            event["text"] = "\n".join(text_parts)
        if tool_calls:
            event["tool_calls"] = tool_calls
        with open(log_file, "a") as f:
            f.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")

    # ── Tool selection ──────────────────────────────────────────────

    def _get_tools(self) -> list[dict]:
        """Return tool list, excluding lean_check/lean_proof_step if no REPL is available."""
        if self.lean_repl is not None:
            return CODING_TOOLS
        return [t for t in CODING_TOOLS if t["name"] not in ("lean_check", "lean_proof_step")]

    # ── Tool dispatch ─────────────────────────────────────────────

    def _execute_tool(self, name: str, inputs: dict) -> str:
        try:
            if name == "bash":
                return self._tool_bash(inputs["command"])
            elif name == "read_file":
                return self._tool_read_file(inputs["path"], inputs.get("offset"), inputs.get("limit"))
            elif name == "write_file":
                return self._tool_write_file(inputs["path"], inputs["content"])
            elif name == "edit_file":
                return self._tool_edit_file(inputs["path"], inputs["old_string"], inputs["new_string"])
            elif name == "grep":
                return self._tool_grep(inputs["pattern"], inputs.get("path", "."), inputs.get("include"))
            elif name == "glob":
                return self._tool_glob(inputs["pattern"])
            elif name == "list_directory":
                return self._tool_list_directory(inputs["path"])
            elif name == "lean_check":
                return self._tool_lean_check(inputs["code"])
            elif name == "lean_proof_step":
                return self._tool_lean_proof_step(inputs["code"], inputs.get("env"))
            else:
                return f"Error: unknown tool '{name}'"
        except Exception as e:
            return f"Error: {type(e).__name__}: {e}"

    # ── Path security ─────────────────────────────────────────────

    def _resolve_path(self, rel_path: str) -> Path:
        resolved = (self.project_root / rel_path).resolve()
        if not str(resolved).startswith(str(self.project_root)):
            raise ValueError(f"Path escapes project root: {rel_path}")
        return resolved

    # ── Tool implementations ──────────────────────────────────────

    def _tool_bash(self, command: str) -> str:
        try:
            result = subprocess.run(
                ["bash", "-c", command],
                capture_output=True, text=True,
                timeout=BASH_TIMEOUT,
                cwd=str(self.project_root),
                env={**os.environ, "LC_ALL": "C.UTF-8"},
            )
        except subprocess.TimeoutExpired:
            return f"Error: command timed out after {BASH_TIMEOUT}s"

        output = ""
        if result.stdout:
            output += result.stdout
        if result.stderr:
            if output:
                output += "\n"
            output += f"STDERR:\n{result.stderr}"
        if result.returncode != 0:
            output += f"\n(exit code: {result.returncode})"
        if len(output) > MAX_BASH_OUTPUT:
            output = output[:MAX_BASH_OUTPUT] + f"\n\n... [truncated at {MAX_BASH_OUTPUT} bytes]"
        return output if output else "(no output)"

    def _tool_read_file(self, rel_path: str, offset: int | None = None, limit: int | None = None) -> str:
        path = self._resolve_path(rel_path)
        if not path.exists():
            return f"Error: file not found: {rel_path}"
        if not path.is_file():
            return f"Error: not a file: {rel_path}"
        try:
            content = path.read_text(errors="replace")
        except Exception as e:
            return f"Error reading file: {e}"
        if offset is not None or limit is not None:
            lines = content.splitlines(keepends=True)
            start = (offset - 1) if offset and offset > 0 else 0
            end = (start + limit) if limit else len(lines)
            lines = lines[start:end]
            numbered = [f"{start + i + 1}\t{line}" for i, line in enumerate(lines)]
            content = "".join(numbered)
        if len(content) > MAX_FILE_SIZE:
            content = content[:MAX_FILE_SIZE] + f"\n\n... [truncated at {MAX_FILE_SIZE} bytes]"
        return content

    def _tool_write_file(self, rel_path: str, content: str) -> str:
        path = self._resolve_path(rel_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return f"Wrote {len(content)} bytes to {rel_path}"

    def _tool_edit_file(self, rel_path: str, old_string: str, new_string: str) -> str:
        path = self._resolve_path(rel_path)
        if not path.exists():
            return f"Error: file not found: {rel_path}"
        content = path.read_text(errors="replace")
        count = content.count(old_string)
        if count == 0:
            return f"Error: old_string not found in {rel_path}"
        if count > 1:
            return f"Error: old_string found {count} times in {rel_path}. It must be unique."
        new_content = content.replace(old_string, new_string, 1)
        path.write_text(new_content)
        return f"Edited {rel_path}: replaced 1 occurrence ({len(old_string)} -> {len(new_string)} chars)"

    def _tool_grep(self, pattern: str, path: str = ".", include: str | None = None) -> str:
        search_path = self._resolve_path(path)
        if not search_path.exists():
            return f"Error: path not found: {path}"
        try:
            regex = re.compile(pattern)
        except re.error as e:
            return f"Error: invalid regex: {e}"

        results = []
        files_to_search = []
        if search_path.is_file():
            files_to_search = [search_path]
        else:
            for root, dirs, files in os.walk(search_path):
                dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("__pycache__", ".lake", "build")]
                for fname in files:
                    if include and not fnmatch.fnmatch(fname, include):
                        continue
                    files_to_search.append(Path(root) / fname)

        for fpath in sorted(files_to_search):
            try:
                text = fpath.read_text(errors="replace")
            except Exception:
                continue
            rel = fpath.relative_to(self.project_root)
            for line_no, line in enumerate(text.splitlines(), 1):
                if regex.search(line):
                    results.append(f"{rel}:{line_no}: {line.rstrip()}")
                    if len(results) >= MAX_GREP_RESULTS:
                        results.append(f"\n... [truncated at {MAX_GREP_RESULTS} matches]")
                        return "\n".join(results)
        return "\n".join(results) if results else f"No matches for pattern '{pattern}'"

    def _tool_glob(self, pattern: str) -> str:
        matches = []
        for path in sorted(self.project_root.glob(pattern)):
            try:
                rel = path.relative_to(self.project_root)
            except ValueError:
                continue
            parts = rel.parts
            if any(p.startswith(".") or p == "__pycache__" for p in parts):
                continue
            matches.append(str(rel))
            if len(matches) >= MAX_GLOB_RESULTS:
                break
        return "\n".join(matches) if matches else f"No files matching '{pattern}'"

    def _tool_list_directory(self, rel_path: str) -> str:
        path = self._resolve_path(rel_path or ".")
        if not path.exists():
            return f"Error: directory not found: {rel_path}"
        if not path.is_dir():
            return f"Error: not a directory: {rel_path}"
        entries = []
        for entry in sorted(path.iterdir()):
            if entry.name.startswith(".") or entry.name == "__pycache__":
                continue
            if entry.is_dir():
                entries.append(f"  {entry.name}/")
            else:
                size = entry.stat().st_size
                if size < 1024:
                    size_str = f"{size}B"
                elif size < 1024 * 1024:
                    size_str = f"{size // 1024}KB"
                else:
                    size_str = f"{size // (1024 * 1024)}MB"
                entries.append(f"  {entry.name}  ({size_str})")
        return "\n".join(entries) if entries else "(empty directory)"

    def _tool_lean_check(self, code: str) -> str:
        """Verify Lean code via the persistent REPL."""
        if self.lean_repl is None:
            return "Error: Lean REPL not available. Use `bash lake build` instead."

        try:
            result = self.lean_repl.check_code(code)
        except Exception as e:
            return f"Error: REPL failed: {e}"

        lines = []

        if result.passed:
            tag = "COMPLETE" if result.complete else "OK (has sorry)"
            lines.append(f"✓ {tag} ({result.elapsed:.2f}s)")
        else:
            lines.append(f"✗ FAILED ({result.elapsed:.2f}s, {len(result.errors)} errors)")

        # Show errors
        for err in result.errors:
            pos = err.get("pos", {})
            loc = f"line {pos.get('line', '?')}:{pos.get('column', '?')}"
            lines.append(f"  [error {loc}] {err.get('data', '')}")

        # Show warnings (useful for DRC, sorry, etc.)
        for w in result.warnings:
            pos = w.get("pos", {})
            loc = f"line {pos.get('line', '?')}:{pos.get('column', '?')}"
            lines.append(f"  [warning {loc}] {w.get('data', '')}")

        # Show generated Verilog
        if result.verilog:
            lines.append("")
            lines.append("=== Generated Verilog ===")
            lines.append(result.verilog)

        return "\n".join(lines)

    def _tool_lean_proof_step(self, code: str, env: int | None = None) -> str:
        """Interactive proof step via the persistent REPL with incremental env."""
        if self.lean_repl is None:
            return "Error: Lean REPL not available."

        try:
            result = self.lean_repl.check_code_incremental(code, env=env)
        except Exception as e:
            return f"Error: REPL failed: {e}"

        lines = []

        if result.passed:
            tag = "COMPLETE" if result.complete else "OK (has sorry)"
            lines.append(f"✓ {tag} ({result.elapsed:.2f}s)")
        else:
            lines.append(f"✗ FAILED ({result.elapsed:.2f}s, {len(result.errors)} errors)")

        # Show errors
        for err in result.errors:
            pos = err.get("pos", {})
            loc = f"line {pos.get('line', '?')}:{pos.get('column', '?')}"
            lines.append(f"  [error {loc}] {err.get('data', '')}")

        # Show warnings
        for w in result.warnings:
            pos = w.get("pos", {})
            loc = f"line {pos.get('line', '?')}:{pos.get('column', '?')}"
            lines.append(f"  [warning {loc}] {w.get('data', '')}")

        # Show proof goals from sorry placeholders
        if result.sorries:
            lines.append("")
            lines.append("=== Proof Goals ===")
            for i, s in enumerate(result.sorries):
                pos = s.get("pos", {})
                loc = f"line {pos.get('line', '?')}:{pos.get('column', '?')}"
                goal = s.get("goal", "(no goal)")
                lines.append(f"Goal {i} ({loc}):")
                for gl in goal.splitlines():
                    lines.append(f"  {gl}")

        # Show generated Verilog
        if result.verilog:
            lines.append("")
            lines.append("=== Generated Verilog ===")
            lines.append(result.verilog)

        # Always show env for chaining
        lines.append("")
        lines.append(f"env: {result.env}")

        return "\n".join(lines)