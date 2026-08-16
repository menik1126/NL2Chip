"""
Lean 4 REPL client — persistent process with JSON protocol.

Wraps `lake exe repl` (leanprover-community/repl) to provide fast incremental
Lean compilation. The prelude (import Sparkle + opens) is loaded once and cached
as `env=0`; subsequent code is verified incrementally against that env.

Thread-safety: each LeanREPL instance manages its own subprocess. For concurrent
workers, create one instance per worker or use LeanREPLPool.
"""
from __future__ import annotations

import json
import os
import queue
import signal
import shutil
import threading
import time
import subprocess
from pathlib import Path
from dataclasses import dataclass, field


_LAKE_FALLBACK_PATHS = (
    Path.home() / ".elan" / "bin" / "lake",
    # Compatibility with the original experiment environment. This is a
    # validated fallback, never an unconditional host-specific default.
    Path("/home/sgli/.elan/bin/lake"),
)


def _usable_executable(candidate: str) -> str | None:
    """Resolve a command or path only when it names an executable file."""
    expanded = os.path.expandvars(os.path.expanduser(candidate.strip()))
    if not expanded:
        return None
    resolved = shutil.which(expanded)
    return os.path.abspath(resolved) if resolved is not None else None


def resolve_lake_path() -> str:
    """Locate Lake without assuming the original experiment host's home."""
    configured = os.environ.get("LAKE_PATH", "")
    resolved = _usable_executable(configured)
    if resolved is not None:
        return resolved

    resolved = shutil.which("lake")
    if resolved is not None:
        return os.path.abspath(resolved)

    for fallback in _LAKE_FALLBACK_PATHS:
        resolved = _usable_executable(str(fallback))
        if resolved is not None:
            return resolved

    configured_detail = (
        f" LAKE_PATH={configured!r} is not executable."
        if configured.strip()
        else ""
    )
    raise RuntimeError(
        "Unable to locate an executable Lake binary."
        f"{configured_detail} Set LAKE_PATH to an executable path or add "
        "`lake` to PATH."
    )


SPARKLE_PRELUDE = """\
import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal
"""

# Default timeout for a single REPL command (seconds)
DEFAULT_TIMEOUT = 120

# A timed-out exchange is never safe to reuse: a late response would otherwise
# be indistinguishable from the response to the next request. Keep process
# retirement short because it runs on the caller's error path, but wait long
# enough for killed pipes to wake the request's I/O thread.
_PROCESS_SHUTDOWN_TIMEOUT = 1.0
_IO_THREAD_JOIN_TIMEOUT = 1.0


@dataclass
class REPLResult:
    """Parsed result from a REPL command."""
    passed: bool           # No errors
    complete: bool         # No errors AND no sorry
    errors: list[dict]     # Error messages
    warnings: list[dict]   # Warning messages
    infos: list[dict]      # Info messages (includes generated Verilog)
    sorries: list[dict]    # Sorry placeholders (with goal states)
    tactics: list[dict]    # Per-tactic info (when allTactics=True)
    env: int | None        # Environment ID for incremental use
    verilog: str | None    # Extracted Verilog output (if any)
    elapsed: float         # Time in seconds
    raw: dict | None       # Raw JSON response

    @property
    def error_text(self) -> str:
        """Format all errors as a single string."""
        lines = []
        for err in self.errors:
            pos = err.get("pos", {})
            loc = f"{pos.get('line', '?')}:{pos.get('column', '?')}"
            lines.append(f"[error {loc}] {err.get('data', '')}")
        return "\n".join(lines)

    @property
    def summary(self) -> str:
        """One-line summary for logging."""
        if self.passed:
            tag = "COMPLETE" if self.complete else "OK (has sorry)"
            return f"✓ {tag} ({self.elapsed:.1f}s, env={self.env})"
        return f"✗ FAILED ({self.elapsed:.1f}s, {len(self.errors)} errors)"


def _parse_raw(raw: dict, elapsed: float) -> REPLResult:
    """Parse raw JSON from the REPL into a REPLResult."""
    messages = raw.get("messages", [])
    errors = [m for m in messages if m.get("severity") == "error"]
    warnings = [m for m in messages if m.get("severity") == "warning"]
    infos = [m for m in messages if m.get("severity") == "info"]
    sorries = raw.get("sorries", [])
    tactics = raw.get("tactics", [])

    passed = len(errors) == 0
    complete = passed and not sorries and not any(
        "declaration uses 'sorry'" in w.get("data", "")
        or "failed" in w.get("data", "")
        for w in warnings
    )

    # Extract Verilog from info messages.  A hierarchical synthesis command may
    # report child and top modules in separate info records, so retain every
    # Verilog-bearing record instead of silently selecting the first one.
    verilog_chunks = []
    for info in infos:
        data = info.get("data", "")
        if "module " in data or "Generated by Sparkle" in data:
            verilog_chunks.append(data)
    verilog = "\n".join(verilog_chunks) if verilog_chunks else None

    return REPLResult(
        passed=passed, complete=complete,
        errors=errors, warnings=warnings, infos=infos,
        sorries=sorries, tactics=tactics, env=raw.get("env"),
        verilog=verilog, elapsed=elapsed, raw=raw,
    )


def _error_result(msg: str, elapsed: float) -> REPLResult:
    """Create an error result when the REPL process fails."""
    return REPLResult(
        passed=False, complete=False,
        errors=[{"severity": "error", "data": msg, "pos": {"line": 0, "column": 0}}],
        warnings=[], infos=[], sorries=[], tactics=[],
        env=None, verilog=None, elapsed=elapsed, raw=None,
    )


@dataclass(frozen=True)
class _ExchangeOutcome:
    """Result produced by the process-bound I/O worker."""

    kind: str
    raw: dict | None = None
    text: str = ""
    error: BaseException | None = None


def _exchange_with_process(
    proc: subprocess.Popen,
    msg: str,
    outcomes: queue.Queue[_ExchangeOutcome],
) -> None:
    """Write one request and read one response using only ``proc``.

    Both writing and reading can block indefinitely on a wedged REPL. They
    therefore run in one process-bound worker while the caller enforces the
    deadline. Crucially, the worker never dereferences ``self._proc``: after a
    timeout it cannot accidentally attach to a replacement process or consume
    that process's response.
    """
    try:
        if proc.stdin is None:
            raise BrokenPipeError("REPL stdin is unavailable")
        proc.stdin.write(msg)
        proc.stdin.flush()
    except (BrokenPipeError, OSError, ValueError) as exc:
        outcomes.put(_ExchangeOutcome(kind="write_error", error=exc))
        return

    buf: list[str] = []
    try:
        if proc.stdout is None:
            raise OSError("REPL stdout is unavailable")
        while True:
            line = proc.stdout.readline()
            if not line:
                text = "".join(buf).strip()
                outcomes.put(_ExchangeOutcome(kind="eof", text=text))
                return
            buf.append(line)
            text = "".join(buf).strip()
            if not text:
                continue
            try:
                raw = json.loads(text)
            except json.JSONDecodeError:
                continue
            if not isinstance(raw, dict):
                outcomes.put(
                    _ExchangeOutcome(
                        kind="read_error",
                        text=text,
                        error=TypeError("REPL response is not a JSON object"),
                    )
                )
                return
            outcomes.put(_ExchangeOutcome(kind="response", raw=raw, text=text))
            return
    except (OSError, ValueError) as exc:
        outcomes.put(
            _ExchangeOutcome(
                kind="read_error", text="".join(buf).strip(), error=exc
            )
        )


class LeanREPL:
    """Manages a single persistent `lake exe repl` process.

    Usage:
        repl = LeanREPL(project_dir="/path/to/sparkle")
        # loads prelude, caches env
        result = repl.check_code("def foo := 42")
        print(result.passed, result.error_text)
        repl.close()
    """

    def __init__(
        self,
        project_dir: str | Path = ".",
        timeout: int = DEFAULT_TIMEOUT,
        prelude: str = SPARKLE_PRELUDE,
    ):
        self.project_dir = Path(project_dir).resolve()
        self.timeout = timeout
        self.prelude = prelude
        self.lake_path = resolve_lake_path()
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()  # protects _proc access
        self._prelude_env: int | None = None
        self._generation = 0
        self._env_bindings: dict[int, tuple[int, int]] = {}
        self._env_handles: dict[tuple[int, int], int] = {}
        self._next_env_handle = 1 << 62
        self._start()
        self._load_prelude()

    def _start(self) -> None:
        """Start (or restart) the REPL subprocess."""
        self._close_proc()
        self._proc = subprocess.Popen(
            [self.lake_path, "exe", "repl"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=str(self.project_dir),
            # Lake may launch the REPL as a child process. A private process
            # group lets timeout recovery kill every process that still owns
            # one of our pipes, so a blocked writer/reader is reliably woken.
            start_new_session=(os.name == "posix"),
        )
        self._generation += 1
        self._prelude_env = None

    @staticmethod
    def _signal_proc(proc: subprocess.Popen, sig: signal.Signals) -> None:
        """Signal the REPL and any Lake child that inherited its pipes."""
        if os.name == "posix":
            try:
                os.killpg(proc.pid, sig)
                return
            except ProcessLookupError:
                return
            except OSError:
                # Fall back to signalling the direct child below.
                pass
        if proc.poll() is not None:
            return
        try:
            if sig == signal.SIGKILL:
                proc.kill()
            else:
                proc.terminate()
        except OSError:
            pass

    @staticmethod
    def _close_streams(proc: subprocess.Popen) -> None:
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            if stream is None:
                continue
            try:
                stream.close()
            except (OSError, ValueError):
                pass

    def _retire_proc(
        self,
        proc: subprocess.Popen,
        io_thread: threading.Thread | None = None,
        *,
        force: bool,
    ) -> bool:
        """Permanently retire ``proc`` and wait for its I/O worker.

        Returns whether the worker stopped. ``self._proc`` is cleared before
        signalling so no later request can reuse a poisoned stream.
        """
        if self._proc is proc:
            self._proc = None
            self._prelude_env = None

        first_signal = signal.SIGKILL if force else signal.SIGTERM
        self._signal_proc(proc, first_signal)
        try:
            proc.wait(timeout=_PROCESS_SHUTDOWN_TIMEOUT)
        except subprocess.TimeoutExpired:
            self._signal_proc(proc, signal.SIGKILL)
            try:
                proc.wait(timeout=_PROCESS_SHUTDOWN_TIMEOUT)
            except (subprocess.TimeoutExpired, OSError):
                pass
        except OSError:
            pass

        if io_thread is not None:
            io_thread.join(timeout=_IO_THREAD_JOIN_TIMEOUT)
        self._close_streams(proc)
        if io_thread is not None and io_thread.is_alive():
            io_thread.join(timeout=_IO_THREAD_JOIN_TIMEOUT)
        return io_thread is None or not io_thread.is_alive()

    def _close_proc(self) -> None:
        """Terminate the subprocess if alive and invalidate its environment."""
        proc = self._proc
        if proc is None:
            self._prelude_env = None
            return
        self._retire_proc(proc, force=False)

    def _register_env(self, raw_env: int) -> int:
        """Return a process-generation-bound public handle for a raw env ID."""
        raw_env = int(raw_env)
        key = (self._generation, raw_env)
        existing = self._env_handles.get(key)
        if existing is not None:
            return existing

        handle = raw_env
        if handle in self._env_bindings:
            handle = self._next_env_handle
            while handle in self._env_bindings:
                handle += 1
            self._next_env_handle = handle + 1

        self._env_handles[key] = handle
        self._env_bindings[handle] = key
        return handle

    def _resolve_env(self, handle: int) -> tuple[int | None, str | None]:
        """Resolve an env handle only when it belongs to the current process."""
        binding = self._env_bindings.get(int(handle))
        if binding is None:
            return None, (
                f"Unknown REPL environment handle {handle}; restart from env=None"
            )
        generation, raw_env = binding
        if generation != self._generation:
            return None, (
                f"Stale REPL environment handle {handle} belongs to process "
                f"generation {generation}, not current generation "
                f"{self._generation}; restart from env=None"
            )
        return raw_env, None

    def _load_prelude(self) -> None:
        """Load the Sparkle prelude and cache its env."""
        result = self._send_raw(self.prelude, env=None, restart_if_needed=False)
        if result.passed and result.env is not None:
            self._prelude_env = result.env
        else:
            raise RuntimeError(
                f"Failed to load Sparkle prelude:\n{result.error_text}"
            )

    def _send_raw(
        self,
        code: str,
        env: int | None = None,
        all_tactics: bool = False,
        use_prelude: bool = False,
        restart_if_needed: bool = True,
    ) -> REPLResult:
        """Send a command to the REPL and read back the JSON response."""
        if self._proc is None or self._proc.poll() is not None:
            if not restart_if_needed:
                self._close_proc()
                return _error_result(
                    "REPL process exited while loading the prelude", 0.0
                )
            self._start()
            self._load_prelude()

        # Bind the whole exchange to one immutable process reference. Public
        # callers hold self._lock; this additionally prevents a timed-out I/O
        # worker from observing a subsequently started REPL.
        proc = self._proc
        if proc is None:  # Defensive: _start above either succeeds or raises.
            return _error_result("REPL process failed to start", 0.0)

        if use_prelude:
            env = self._prelude_env
        command = {
            "cmd": code,
            "allTactics": all_tactics,
            "ast": False,
            "tactics": all_tactics,
            "premises": False,
        }
        if env is not None:
            raw_env, env_error = self._resolve_env(env)
            if env_error is not None:
                return _error_result(env_error, 0.0)
            command["env"] = raw_env

        msg = json.dumps(command, ensure_ascii=False) + "\r\n\r\n"
        start = time.time()
        outcomes: queue.Queue[_ExchangeOutcome] = queue.Queue(maxsize=1)
        io_thread = threading.Thread(
            target=_exchange_with_process,
            args=(proc, msg, outcomes),
            daemon=True,
            name=f"LeanREPL-io-{proc.pid}",
        )
        io_thread.start()

        try:
            outcome = outcomes.get(timeout=max(0.0, float(self.timeout)))
        except queue.Empty:
            elapsed = time.time() - start
            worker_stopped = self._retire_proc(proc, io_thread, force=True)
            detail = f"Timeout after {self.timeout}s"
            if not worker_stopped:
                detail += " (REPL I/O worker did not stop cleanly)"
            return _error_result(detail, elapsed)
        except BaseException:
            self._retire_proc(proc, io_thread, force=True)
            raise

        io_thread.join()
        elapsed = time.time() - start
        if outcome.kind == "response" and outcome.raw is not None:
            result = _parse_raw(outcome.raw, elapsed)
            if result.env is not None:
                result.env = self._register_env(result.env)
            return result

        # EOF, malformed data, and write/read errors all leave stream framing
        # uncertain. Retire the process just as aggressively as on timeout.
        self._retire_proc(proc, io_thread, force=True)
        if outcome.kind == "write_error":
            return _error_result(
                f"REPL process write failed: {outcome.error}", elapsed
            )
        if outcome.kind == "read_error":
            return _error_result(
                f"REPL process read failed: {outcome.error}", elapsed
            )
        if outcome.text:
            return _error_result(
                f"Incomplete JSON: {outcome.text[:300]}", elapsed
            )
        return _error_result(
            "REPL process closed stdout before responding", elapsed
        )

    def check_code(self, code: str) -> REPLResult:
        """Verify Lean code incrementally against the cached prelude env.

        This is the main entry point for the agent. The code should NOT include
        `import Sparkle` or `open` statements — those are in the prelude.

        Args:
            code: Lean 4 code to verify (definitions, #synthesizeVerilog, etc.)

        Returns:
            REPLResult with pass/fail, errors, and generated Verilog.
        """
        with self._lock:
            return self._send_raw(code, use_prelude=True)

    def check_code_incremental(self, code: str, env: int | None = None) -> REPLResult:
        """Verify Lean code incrementally with custom env and tactic info.

        Used for interactive proof mode: each call can build on a previous env,
        and returns per-sorry goal states + per-tactic goals.

        Args:
            code: Lean 4 code (defs, theorems, tactics, etc.)
            env: Environment ID from a previous call. None = use prelude env.

        Returns:
            REPLResult with goal states in `sorries` and tactic trace in `tactics`.
        """
        with self._lock:
            return self._send_raw(
                code,
                env=env,
                all_tactics=True,
                use_prelude=env is None,
            )

    def check_file(self, filepath: str | Path) -> REPLResult:
        """Verify a .lean file, stripping prelude imports.

        Reads the file, removes import/open lines that are already in the
        prelude, and sends the remaining code to the REPL.
        """
        content = Path(filepath).read_text()
        # Strip lines that are already in the prelude
        stripped_lines = []
        for line in content.splitlines():
            stripped = line.strip()
            if stripped in ("import Sparkle", "import Sparkle.Compiler.Elab",
                            "open Sparkle.Core.Domain", "open Sparkle.Core.Signal",
                            ""):
                continue
            stripped_lines.append(line)
        code = "\n".join(stripped_lines)
        return self.check_code(code)

    def restart(self) -> None:
        """Restart the REPL process and reload prelude."""
        with self._lock:
            self._start()
            self._load_prelude()

    def close(self) -> None:
        """Shutdown the REPL process."""
        with self._lock:
            self._close_proc()

    @property
    def prelude_env(self) -> int | None:
        """The cached prelude environment ID."""
        return self._prelude_env

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class LeanREPLPool:
    """Pool of REPL instances for concurrent workers.

    Each worker gets its own REPL subprocess to avoid contention.
    """

    def __init__(
        self,
        size: int,
        project_dir: str | Path = ".",
        timeout: int = DEFAULT_TIMEOUT,
        prelude_timeout: int = 300,
    ):
        self.project_dir = Path(project_dir).resolve()
        self.timeout = timeout
        self.prelude_timeout = prelude_timeout
        self._pool: list[LeanREPL] = []
        self._available: list[LeanREPL] = []
        self._lock = threading.Lock()
        self._size = size
        # Eagerly init all instances sequentially to avoid concurrent
        # prelude loading which causes timeouts under resource contention.
        for i in range(size):
            repl = LeanREPL(
                project_dir=self.project_dir,
                timeout=self.prelude_timeout,
            )
            # Reset timeout to normal after prelude is loaded
            repl.timeout = self.timeout
            self._pool.append(repl)
            self._available.append(repl)

    def acquire(self) -> LeanREPL:
        """Get a REPL instance (blocks if none available)."""
        while True:
            with self._lock:
                if self._available:
                    return self._available.pop()
            time.sleep(0.1)

    def release(self, repl: LeanREPL) -> None:
        """Return a REPL instance to the pool."""
        with self._lock:
            self._available.append(repl)

    def close_all(self) -> None:
        """Shutdown all REPL instances."""
        with self._lock:
            for repl in self._pool:
                repl.close()
            self._pool.clear()
            self._available.clear()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close_all()
