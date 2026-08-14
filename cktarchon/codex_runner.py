from __future__ import annotations

import os
import shutil
import shlex
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .env import ensure_runtime_env
from .logs import AgentStats, append_jsonl, parse_agent_log
from .responses_chat_proxy import DEFAULT_BASE_URL_ENV, DEFAULT_KEY_ENV, auto_proxy_env

DEFAULT_ARCHON_SRC = Path("/home/sgli/work/archon-official/src")


@dataclass
class CodexAgentHarnessRunner:
    """Run a problem through official Archon's CodexAgent backend.

    This is the native Archon path for machines that have the ``codex`` CLI
    installed. It deliberately fails loud when the CLI is missing instead of
    falling back to the Anthropic API loop, because those are different agents.
    """

    project_root: Path
    prob_id: str
    model: str
    role: str
    log_base: Path
    system_prompt: str
    archon_src: Path = DEFAULT_ARCHON_SRC
    codex_bin: str | None = None
    effort: str | None = None
    sandbox: str = "danger-full-access"
    idle_timeout_s: float = 900
    max_attempts: int = 3
    base_url_env: str | None = None
    key_env: str | None = None
    wire_api: str = "responses"
    auto_chat_proxy: bool = True
    chat_proxy_timeout_s: float = 300.0
    required_verilog_modules: tuple[str, ...] = ()

    @property
    def log_path(self) -> Path:
        return Path(str(self.log_base) + ".jsonl")

    def run(self, prompt: str, *, max_turns: int) -> AgentStats:
        ensure_runtime_env()
        self.project_root = self.project_root.resolve()
        self._ensure_archon_importable()
        codex_bin = self._resolve_codex_bin()

        from archon.agents.codex import CodexAgent
        from archon.commands.tooling.project_config import HarnessDescriptor

        use_proxy = self._should_auto_proxy()
        with auto_proxy_env(enabled=use_proxy, timeout_s=self.chat_proxy_timeout_s) as proxy_env:
            env_overrides = os.environ.copy()
            env_overrides.update(proxy_env)
            base_url_env = self.base_url_env or (DEFAULT_BASE_URL_ENV if use_proxy else None)
            key_env = self.key_env or (DEFAULT_KEY_ENV if use_proxy else None)

            if use_proxy:
                append_jsonl(
                    self.log_path,
                    {
                        "event": "cktarchon_proxy",
                        "runner": "codex-agent",
                        "prob_id": self.prob_id,
                        "detail": "started local Responses-to-Chat proxy for Codex CLI",
                    },
                )

            raw: dict[str, object] = {}
            if codex_bin:
                raw["bin"] = codex_bin
            descriptor = HarnessDescriptor(
                name="cktarchon-codex",
                runner="codex",
                model=self.model,
                effort=self.effort,
                sandbox=self.sandbox,
                base_url_env=base_url_env,
                key_env=key_env,
                wire_api=self.wire_api,
                raw=raw,
            )
            agent = CodexAgent(descriptor=descriptor, role=self.role)
            full_prompt = self._codex_prompt(prompt, max_turns=max_turns)
            cancel_event = threading.Event()
            monitor = threading.Thread(
                target=self._watch_turn_budget,
                args=(max_turns, cancel_event),
                name=f"cktarchon-codex-budget-{self.prob_id}",
                daemon=True,
            )
            monitor.start()
            try:
                ok = agent.run(
                    full_prompt,
                    cwd=self.project_root,
                    log_base=self.log_base,
                    env_overrides=env_overrides,
                    idle_timeout_s=self.idle_timeout_s,
                    max_attempts=self.max_attempts,
                    cancel_event=cancel_event,
                )
            finally:
                cancel_event.set()
                monitor.join(timeout=5)
        stats = parse_agent_log(self.log_path)
        if not ok:
            if self._budget_exceeded_logged():
                append_jsonl(self.log_path, {
                    "event": "cktarchon_error",
                    "runner": "codex-agent",
                    "prob_id": self.prob_id,
                    "detail": f"official Archon CodexAgent stopped after CktArchon max-turns budget {max_turns}",
                })
                raise RuntimeError(f"official Archon CodexAgent exceeded max-turns budget {max_turns}")
            append_jsonl(self.log_path, {
                "event": "cktarchon_error",
                "runner": "codex-agent",
                "prob_id": self.prob_id,
                "detail": "official Archon CodexAgent returned non-zero",
            })
            raise RuntimeError("official Archon CodexAgent returned non-zero")
        return stats

    def _should_auto_proxy(self) -> bool:
        return self.auto_chat_proxy and self.wire_api == "responses" and not self.base_url_env

    def _ensure_archon_importable(self) -> None:
        if str(self.archon_src) not in sys.path:
            sys.path.insert(0, str(self.archon_src))
        try:
            import archon.agents.codex  # noqa: F401
        except Exception as exc:
            raise RuntimeError(f"official Archon CodexAgent import failed from {self.archon_src}: {exc}") from exc

    def _resolve_codex_bin(self) -> str:
        if self.codex_bin:
            return self.codex_bin
        from_env = os.environ.get("ARCHON_CODEX_BIN")
        if from_env:
            return from_env
        found = shutil.which("codex")
        if found:
            return found
        raise RuntimeError(
            "codex-agent harness requested, but no `codex` CLI is installed or exposed via ARCHON_CODEX_BIN. "
            "Install Codex CLI/Node on this host, or use --harness anthropic-api for Claude API runs."
        )

    def _budget_exceeded_logged(self) -> bool:
        try:
            for line in self.log_path.read_text(errors="replace").splitlines():
                if '"event": "cktarchon_budget_exceeded"' in line:
                    return True
        except OSError:
            return False
        return False

    def _watch_turn_budget(self, max_turns: int, cancel_event: threading.Event) -> None:
        """Cancel Codex once the normalized Archon event budget is exhausted.

        Official Archon's CodexAgent exposes idle and retry supervision, but
        Codex CLI has no native max-turn flag. CktArchon treats assistant text
        plus tool-call events as the comparable action budget for NL2Chip
        experiments. Tool results are excluded because they are environment
        feedback rather than extra model decisions.
        """
        if max_turns <= 0:
            return
        emitted = False
        while not cancel_event.is_set():
            count = 0
            saw_end = False
            try:
                with self.log_path.open(errors="replace") as f:
                    for line in f:
                        if not line.strip():
                            continue
                        if '"event": "session_end"' in line:
                            saw_end = True
                        if '"event": "text"' in line or '"event": "tool_call"' in line:
                            count += 1
            except OSError:
                time.sleep(1.0)
                continue
            if saw_end:
                return
            if count >= max_turns:
                if not emitted:
                    append_jsonl(
                        self.log_path,
                        {
                            "event": "cktarchon_budget_exceeded",
                            "runner": "codex-agent",
                            "prob_id": self.prob_id,
                            "max_turns": max_turns,
                            "counted_events": count,
                        },
                    )
                    emitted = True
                cancel_event.set()
                return
            time.sleep(2.0)

    def _codex_prompt(self, prompt: str, *, max_turns: int) -> str:
        required_args = "".join(
            f" --require-module {shlex.quote(name)}"
            for name in self.required_verilog_modules
        )
        lean_check_command = (
            ".venv/bin/python -m cktarchon.tools lean-check "
            f"Generated/{self.prob_id}.lean{required_args}"
        )
        return (
            self.system_prompt.rstrip()
            + "\n\n## Codex-agent execution notes\n"
            + f"- Target action budget: about {max_turns} tool/model steps; stop once `Generated/{self.prob_id}.lean` compiles.\n"
            + f"- Only edit `Generated/{self.prob_id}.lean` and scratch files under `cktarchon_work/{self.prob_id}/`.\n"
            + "- In this Codex path there is no direct `lean_check` function tool. Ignore instructions that say to pass code to `lean_check`.\n"
            + f"- For Lean feedback, run `{lean_check_command}` from the repository root.\n"
            + "- `cktarchon.tools lean-check` checks the file path argument only; it does not read candidate code from stdin. Write the target file before checking it.\n"
            + "- Use `grep`/`find` rather than `rg`; `rg` is not installed on this H20 image.\n"
            + "- Leave benchmark, Sparkle, evaluator, and harness files unchanged.\n"
            + "- Do not run simulation, pytest, cocotb, or a final `lake build` after lean-check succeeds; the outer evaluator does that.\n\n"
            + "## NL2Chip problem prompt\n"
            + prompt
            + "\n\n## Final CktArchon override\n"
            + f"- The final answer should be brief. After `{lean_check_command}` reports success, stop immediately.\n"
            + "- Do not perform extra Verilog review, simulation, pytest, cocotb, synthesis, or PPA checks inside CodexAgent.\n"
            + "- These instructions override any earlier generic workflow text that asks for final Verilog inspection.\n"
        )
