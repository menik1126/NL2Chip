from __future__ import annotations

from contextlib import contextmanager
import json
import os
import pwd
import shutil
import shlex
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .env import ensure_runtime_env
from .logs import AgentStats, append_jsonl, parse_agent_log
from .responses_chat_proxy import DEFAULT_BASE_URL_ENV, DEFAULT_KEY_ENV, auto_proxy_env

# Official Archon checkout; override with ARCHON_SRC or --archon-src.
DEFAULT_ARCHON_SRC = Path(
    os.environ.get("ARCHON_SRC")
    or Path(__file__).resolve().parents[2] / "archon-official" / "src"
)


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
    recover_usage_from_rollout: bool = True
    public_only: bool = False
    execution_user: str | None = None
    direct_verilog: bool = False
    native_login_only: bool = False
    interface_prompt_policy: str = "legacy"

    @property
    def log_path(self) -> Path:
        return Path(str(self.log_base) + ".jsonl")

    def run(self, prompt: str, *, max_turns: int) -> AgentStats:
        ensure_runtime_env()
        self.project_root = self.project_root.resolve()
        self._ensure_archon_importable()
        codex_bin = self._resolve_codex_bin()
        codex_bin = self._wrap_codex_bin_for_execution_user(codex_bin)

        from archon.agents.codex import CodexAgent
        from archon.commands.tooling.project_config import HarnessDescriptor

        use_proxy = self._should_auto_proxy()
        with auto_proxy_env(enabled=use_proxy, timeout_s=self.chat_proxy_timeout_s) as proxy_env, self._last_message_capture() as last_message:
            env_overrides = self._build_agent_env(proxy_env)
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
            agent_class = CodexAgent
            if self.recover_usage_from_rollout or last_message is not None:
                # Codex reports aggregate usage only when a turn completes.
                # A strict action-budget kill can happen before that event, but
                # the persistent rollout records incremental token_count rows.
                # Archon currently hard-codes --ephemeral, so remove only that
                # flag while retaining the official runner and parser.
                class PersistentCodexAgent(CodexAgent):
                    def build_argv(inner_self, *args, **kwargs):
                        if last_message is not None:
                            kwargs["last_message_path"] = last_message
                        argv = super().build_argv(*args, **kwargs)
                        return [arg for arg in argv if arg != "--ephemeral"] if self.recover_usage_from_rollout else argv

                agent_class = PersistentCodexAgent
            agent = agent_class(descriptor=descriptor, role=self.role)
            full_prompt = self._codex_prompt(prompt, max_turns=max_turns)
            cancel_event = threading.Event()
            monitor = threading.Thread(
                target=self._watch_turn_budget,
                args=(max_turns, cancel_event),
                name=f"cktarchon-codex-budget-{self.prob_id}",
                daemon=True,
            )
            monitor.start()
            ok = False
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
                if self.recover_usage_from_rollout:
                    self._recover_rollout_usage(env_overrides)
        stats = parse_agent_log(self.log_path)
        if not ok:
            if self._budget_exceeded_logged():
                append_jsonl(self.log_path, {
                    "event": "cktarchon_budget_stop",
                    "runner": "codex-agent",
                    "prob_id": self.prob_id,
                    "detail": f"official Archon CodexAgent stopped after CktArchon max-turns budget {max_turns}",
                })
                return stats
            append_jsonl(self.log_path, {
                "event": "cktarchon_error",
                "runner": "codex-agent",
                "prob_id": self.prob_id,
                "detail": "official Archon CodexAgent returned non-zero",
            })
            raise RuntimeError("official Archon CodexAgent returned non-zero")
        return stats

    @contextmanager
    def _last_message_capture(self):
        """Keep private run directories unreadable while allowing CLI output."""
        if not self.execution_user:
            yield None
            return
        account = pwd.getpwnam(self.execution_user)
        work = self.project_root / "cktarchon_work"
        work.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix=".codex-final-", dir=work))
        os.chown(temporary, account.pw_uid, account.pw_gid)
        target = temporary / "last_message.txt"
        try:
            yield target
        finally:
            try:
                if target.is_file():
                    self.log_base.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(target, Path(str(self.log_base) + ".last_message.txt"))
            finally:
                shutil.rmtree(temporary)

    def _build_agent_env(self, proxy_env: dict[str, str]) -> dict[str, str]:
        """Build the environment inherited by the model-facing Codex process."""
        env_overrides = os.environ.copy()
        env_overrides.update(proxy_env)
        if self.native_login_only:
            for name in (
                DEFAULT_BASE_URL_ENV,
                DEFAULT_KEY_ENV,
                "OPENAI_API_KEY",
                "OPENAI_BASE_URL",
                "ANTHROPIC_API_KEY",
                "ANTHROPIC_AUTH_TOKEN",
                "ANTHROPIC_BASE_URL",
            ):
                env_overrides.pop(name, None)
        if self.public_only:
            # The parent evaluator retains this location; the generation and
            # repair subprocess must not receive a route to hidden CVDP data.
            env_overrides.pop("CVDP_DATASET_FILE", None)
            env_overrides.pop("CVDP_HARNESS_PROFILE", None)
        isolated_root = env_overrides.get("NL2CHIP_ISOLATION_ROOT")
        if isolated_root:
            env_overrides["NL2CHIP_TASK_ID"] = self.prob_id
            env_overrides["CODEX_HOME"] = str(Path(isolated_root) / self.prob_id / "codex")
        return env_overrides

    def _recover_rollout_usage(self, env: dict[str, str]) -> None:
        """Recover usage after a strict-budget stop, then remove the rollout.

        Codex's stdout exposes usage only in ``turn.completed``.  Its local
        persistent rollout additionally emits cumulative ``token_count``
        records after each provider response, including responses followed by
        a tool call.  Reading the last cumulative row preserves exact usage
        even when the action watcher terminates the session mid-turn.
        """
        session_id = self._logged_session_id()
        if not session_id:
            if self._budget_exceeded_logged() or not self._log_has_usage():
                self._record_incomplete_token_accounting(
                    "Codex thread id was not recorded; rollout usage unavailable"
                )
            return

        codex_home = Path(env.get("CODEX_HOME") or Path.home() / ".codex").expanduser()
        sessions_root = codex_home / "sessions"
        rollout = None
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            try:
                matches = list(sessions_root.rglob(f"*{session_id}*.jsonl"))
            except OSError:
                matches = []
            if matches:
                rollout = max(matches, key=lambda path: path.stat().st_mtime)
                break
            time.sleep(0.1)

        if rollout is None:
            if self._budget_exceeded_logged() or not self._log_has_usage():
                self._record_incomplete_token_accounting(
                    f"Codex rollout for thread {session_id} was not found"
                )
            return

        total_usage: dict[str, object] | None = None
        try:
            for line in rollout.read_text(errors="replace").splitlines():
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                payload = row.get("payload") or {}
                if (
                    row.get("type") == "event_msg"
                    and payload.get("type") == "token_count"
                ):
                    info = payload.get("info") or {}
                    candidate = info.get("total_token_usage")
                    if isinstance(candidate, dict):
                        total_usage = candidate
        except OSError as exc:
            self._record_incomplete_token_accounting(
                f"Could not read Codex rollout for thread {session_id}: {exc}"
            )
            return
        finally:
            keep = str(env.get("CKTARCHON_KEEP_CODEX_ROLLOUT", "")).lower()
            if keep not in {"1", "true", "yes", "on"}:
                try:
                    rollout.unlink()
                except OSError:
                    pass

        if not total_usage:
            if self._budget_exceeded_logged() or not self._log_has_usage():
                self._record_incomplete_token_accounting(
                    f"Codex rollout for thread {session_id} had no token_count row"
                )
            return

        input_total = int(total_usage.get("input_tokens") or 0)
        cache_read = int(total_usage.get("cached_input_tokens") or 0)
        cache_creation = int(total_usage.get("cache_write_input_tokens") or 0)
        uncached = max(0, input_total - cache_read - cache_creation)
        output = int(total_usage.get("output_tokens") or 0)
        if input_total <= 0 and output <= 0:
            self._record_incomplete_token_accounting(
                f"Codex rollout for thread {session_id} reported zero usage"
            )
            return
        append_jsonl(
            self.log_path,
            {
                "event": "session_usage_recovered",
                "runner": "codex-agent",
                "prob_id": self.prob_id,
                "session_id": session_id,
                "source": "codex_rollout_token_count",
                "input_tokens": uncached,
                "uncached_input_tokens": uncached,
                "cache_creation_input_tokens": cache_creation,
                "cache_read_input_tokens": cache_read,
                "input_tokens_total": input_total,
                "output_tokens": output,
            },
        )

    def _logged_session_id(self) -> str | None:
        try:
            for line in self.log_path.read_text(errors="replace").splitlines():
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("event") in {"session_meta", "thread.started"}:
                    value = row.get("session_id") or row.get("thread_id")
                    if value:
                        return str(value)
        except OSError:
            return None
        return None

    def _log_has_usage(self) -> bool:
        try:
            for line in self.log_path.read_text(errors="replace").splitlines():
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                usage = row.get("usage") if isinstance(row.get("usage"), dict) else row
                if int(usage.get("input_tokens_total") or 0) > 0:
                    return True
                if int(usage.get("input_tokens") or 0) > 0:
                    return True
                if int(usage.get("output_tokens") or 0) > 0:
                    return True
        except OSError:
            return False
        return False

    def _record_incomplete_token_accounting(self, detail: str) -> None:
        append_jsonl(
            self.log_path,
            {
                "event": "cktarchon_token_accounting_incomplete",
                "runner": "codex-agent",
                "prob_id": self.prob_id,
                "detail": detail,
            },
        )

    def _should_auto_proxy(self) -> bool:
        return (
            not self.native_login_only
            and self.auto_chat_proxy
            and self.wire_api == "responses"
            and not self.base_url_env
        )

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

    def _wrap_codex_bin_for_execution_user(self, codex_bin: str) -> str:
        if not self.execution_user:
            return codex_bin
        try:
            account = pwd.getpwnam(self.execution_user)
        except KeyError as exc:
            raise RuntimeError(
                f"Codex execution user '{self.execution_user}' does not exist"
            ) from exc
        runuser = shutil.which("runuser")
        if not runuser:
            raise RuntimeError(
                "--codex-agent-user requires the local runuser executable"
            )
        wrapper = self.log_path.parent / f".codex-run-as-{self.execution_user}"
        stderr_log = self.log_path.with_suffix(".codex.stderr.log")
        wrapper.parent.mkdir(parents=True, exist_ok=True)
        wrapper.write_text(
            "#!/bin/sh\n"
            f"export HOME={shlex.quote(account.pw_dir)}\n"
            f"export XDG_CACHE_HOME={shlex.quote(account.pw_dir + '/.cache')}\n"
            f"exec {shlex.quote(runuser)} --preserve-environment --user "
            f"{shlex.quote(self.execution_user)} -- {shlex.quote(codex_bin)} \"$@\" "
            f"2>{shlex.quote(str(stderr_log))}\n",
            encoding="utf-8",
        )
        wrapper.chmod(0o755)
        return str(wrapper)

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
            time.sleep(0.1)

    def _codex_prompt(self, prompt: str, *, max_turns: int) -> str:
        if self.direct_verilog:
            target = f"cktarchon_work/{self.prob_id}/candidate.sv"
            public_only_note = (
                "- Public-only evaluation is enabled. Hidden CVDP harnesses, "
                "expected traces, and evaluator data are not available to you; "
                "do not attempt to locate them.\n"
                if self.public_only
                else ""
            )
            return (
                self.system_prompt.rstrip()
                + "\n\n## Codex-agent execution notes\n"
                + public_only_note
                + f"- Target action budget: about {max_turns} tool/model steps; "
                f"stop once `{target}` is a complete synthesizable SystemVerilog module.\n"
                + f"- Only edit `{target}` and scratch files under "
                f"`cktarchon_work/{self.prob_id}/`.\n"
                + "- Do not read prior benchmark candidates or run artifacts, "
                "including `results*`, `preexisting_generated`, or another task's "
                "candidate. They are evaluation leakage, not examples.\n"
                + "- Leave benchmark, Sparkle, evaluator, and harness files unchanged.\n"
                + "- Do not run simulation, pytest, cocotb, synthesis, or PPA checks. "
                "The outer evaluator performs compile and simulation, then supplies "
                "only feedback allowed by the selected feedback mode.\n\n"
                + "## NL2Chip problem prompt\n"
                + prompt
                + "\n\n## Final CktArchon override\n"
                + f"- The final answer should be brief. Ensure `{target}` contains "
                "the complete implementation before stopping.\n"
            )

        required_args = "".join(
            f" --require-module {shlex.quote(name)}"
            for name in self.required_verilog_modules
        )
        lean_check_command = (
            ".venv/bin/python -m cktarchon.tools lean-check "
            f"Generated/{self.prob_id}.lean{required_args}"
        )
        public_only_note = (
            "- Public-only evaluation is enabled. Hidden CVDP harnesses, expected traces, and evaluator data are not available to you; do not attempt to locate them.\n"
            if self.public_only
            else ""
        )
        compiler_tool_note = (
            "- The Lean compiler entry point is the CLI command below; check the saved complete candidate file.\n"
            if self.interface_prompt_policy == "public-spec-v2" else
            "- In this Codex path there is no direct `lean_check` function tool. Ignore instructions that say to pass code to `lean_check`.\n"
        )
        search_note = (
            "- Use the available `grep` and `find` commands for permitted source searches.\n"
            if self.interface_prompt_policy == "public-spec-v2" else
            "- Use `grep`/`find` rather than `rg`; `rg` may not be installed.\n"
        )
        return (
            self.system_prompt.rstrip()
            + public_only_note
            + "\n\n## Codex-agent execution notes\n"
            + f"- Target action budget: about {max_turns} tool/model steps; stop once `Generated/{self.prob_id}.lean` compiles.\n"
            + f"- Only edit `Generated/{self.prob_id}.lean` and scratch files under `cktarchon_work/{self.prob_id}/`.\n"
            + compiler_tool_note
            + f"- For Lean feedback, run `{lean_check_command}` from the repository root.\n"
            + "- `cktarchon.tools lean-check` checks the file path argument only; it does not read candidate code from stdin. Write the target file before checking it.\n"
            + search_note
            + "- Never read prior benchmark candidates or run artifacts, including `experiments/p3_replay_candidates`, `results*`, `preexisting_generated`, candidate snapshots, or another task's `Generated/cvdp_*` file. They are evaluation leakage, not examples.\n"
            + "- Leave benchmark, Sparkle, evaluator, and harness files unchanged.\n"
            + "- Do not run simulation, pytest, cocotb, or a final `lake build` after lean-check succeeds; the outer evaluator does that.\n\n"
            + "## NL2Chip problem prompt\n"
            + prompt
            + "\n\n## Final CktArchon override\n"
            + f"- The final answer should be brief. After `{lean_check_command}` reports success, stop immediately.\n"
            + "- Do not perform extra Verilog review, simulation, pytest, cocotb, synthesis, or PPA checks inside CodexAgent.\n"
            + "- These instructions override any earlier generic workflow text that asks for final Verilog inspection.\n"
        )
