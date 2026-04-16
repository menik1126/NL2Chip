#!/usr/bin/env python3
"""Interactive Lean 4 verification server for Sparkle chip design.

Uses a persistent `lake exe repl` process (JSON protocol) for incremental
verification — each command builds on the previous environment, so you
don't re-import Sparkle every time.

Usage:
  python interactive_lean.py                    # interactive REPL
  python interactive_lean.py file.lean          # verify a file, then enter REPL
  python interactive_lean.py --watch file.lean  # watch file for changes

Commands inside the REPL:
  :load <file>    Load and verify a .lean file (fresh env)
  :edit           Open $EDITOR to write code, verify on save
  :last           Show last verification result
  :verilog        Show last generated Verilog (if any)
  :env            Show current env id
  :reset          Restart REPL process and re-import prelude
  :clear          Clear the code input buffer
  :help           Show this help
  :quit / :q      Exit
  Ctrl+D          Exit

Anything else is appended to the code buffer.
A blank line triggers verification of the current buffer.
"""

import os
import sys
import json
import time
import tempfile
import threading
import subprocess
import argparse
import hashlib
from pathlib import Path

LAKE_PATH = os.path.expanduser("~/.elan/bin/lake")
PROJECT_DIR = Path(__file__).resolve().parent

PRELUDE = """\
import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal
"""

RED = "\033[91m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"


# ── Persistent REPL process ────────────────────────────────────────

class LeanRepl:
    """Manages a long-lived `lake exe repl` process."""

    def __init__(self, timeout: int = 120):
        self.timeout = timeout
        self.proc = None
        self._start()

    def _start(self):
        """Start (or restart) the REPL subprocess."""
        self.close()
        self.proc = subprocess.Popen(
            [LAKE_PATH, "exe", "repl"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=str(PROJECT_DIR),
        )

    def send(self, code: str, env: int | None = None) -> dict:
        """Send a command and read back the JSON response."""
        if self.proc is None or self.proc.poll() is not None:
            self._start()

        command = {"cmd": code, "allTactics": False, "ast": False,
                   "tactics": False, "premises": False}
        if env is not None:
            command["env"] = env

        msg = json.dumps(command, ensure_ascii=False) + "\r\n\r\n"
        start = time.time()

        try:
            self.proc.stdin.write(msg)
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError) as e:
            return _error_result(f"REPL process died: {e}", time.time() - start)

        # Read response: accumulate until we get valid JSON
        # The REPL outputs one JSON object per command, terminated by newline
        buf = []
        deadline = start + self.timeout
        while time.time() < deadline:
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            # Use a thread to read with timeout
            line_result = [None]
            def read_line():
                try:
                    line_result[0] = self.proc.stdout.readline()
                except:
                    pass
            t = threading.Thread(target=read_line, daemon=True)
            t.start()
            t.join(timeout=remaining)
            if line_result[0] is None:
                # Timeout or error
                break
            line = line_result[0]
            if not line:  # EOF
                break
            buf.append(line)
            # Try to parse accumulated output as JSON
            text = "".join(buf).strip()
            if text:
                try:
                    raw = json.loads(text)
                    return _parse_raw(raw, time.time() - start)
                except json.JSONDecodeError:
                    continue  # need more lines

        elapsed = time.time() - start
        text = "".join(buf).strip()
        if text:
            try:
                raw = json.loads(text)
                return _parse_raw(raw, elapsed)
            except json.JSONDecodeError:
                return _error_result(f"Incomplete JSON: {text[:300]}", elapsed)
        return _error_result(f"Timeout after {self.timeout}s", elapsed)

    def restart(self):
        self._start()

    def close(self):
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.stdin.close()
                self.proc.terminate()
                self.proc.wait(timeout=5)
            except:
                try:
                    self.proc.kill()
                except:
                    pass
        self.proc = None


def _parse_raw(raw: dict, elapsed: float) -> dict:
    messages = raw.get("messages", [])
    errors = [m for m in messages if m["severity"] == "error"]
    warnings = [m for m in messages if m["severity"] == "warning"]
    infos = [m for m in messages if m["severity"] == "info"]
    sorries = raw.get("sorries", [])

    passed = len(errors) == 0
    complete = passed and not sorries and not any(
        "declaration uses 'sorry'" in w.get("data", "") or "failed" in w.get("data", "")
        for w in warnings
    )

    verilog = None
    for info in infos:
        data = info.get("data", "")
        if "module " in data or "Generated by Sparkle" in data:
            verilog = data
            break

    return {
        "pass": passed, "complete": complete,
        "errors": errors, "warnings": warnings, "infos": infos,
        "sorries": sorries, "env": raw.get("env"),
        "verilog": verilog, "time": elapsed, "raw": raw,
    }


def _error_result(msg: str, elapsed: float) -> dict:
    return {
        "pass": False, "complete": False,
        "errors": [{"severity": "error", "data": msg, "pos": {"line": 0, "column": 0}}],
        "warnings": [], "infos": [], "sorries": [],
        "env": None, "verilog": None, "time": elapsed, "raw": None,
    }


# ── Display ─────────────────────────────────────────────────────────

def format_result(result: dict) -> str:
    lines = []
    elapsed = f"{result['time']:.1f}s"
    env_str = f"env={result['env']}" if result.get("env") is not None else "no env"

    if result["pass"]:
        tag = f"{GREEN}✓ COMPLETE{RESET}" if result["complete"] else f"{GREEN}✓ OK (has sorry){RESET}"
        lines.append(f"{tag} {DIM}({elapsed}, {env_str}){RESET}")
    else:
        lines.append(f"{RED}✗ FAILED{RESET} {DIM}({elapsed}, {env_str}){RESET}")

    for err in result["errors"]:
        pos = err.get("pos", {})
        loc = f"{pos.get('line', '?')}:{pos.get('column', '?')}"
        lines.append(f"  {RED}[error {loc}]{RESET} {err['data']}")

    for w in result["warnings"]:
        pos = w.get("pos", {})
        loc = f"{pos.get('line', '?')}:{pos.get('column', '?')}"
        lines.append(f"  {YELLOW}[warn {loc}]{RESET} {w['data']}")

    for info in result["infos"]:
        data = info["data"]
        if result.get("verilog") and data == result["verilog"]:
            for vline in data.splitlines():
                lines.append(f"  {CYAN}{vline}{RESET}")
        else:
            pos = info.get("pos", {})
            loc = f"{pos.get('line', '?')}:{pos.get('column', '?')}"
            lines.append(f"  {DIM}[info {loc}]{RESET} {data}")

    if result["sorries"]:
        lines.append(f"  {YELLOW}[sorries: {len(result['sorries'])}]{RESET}")

    return "\n".join(lines)


# ── Watch mode ──────────────────────────────────────────────────────

def watch_file(filepath: str, timeout: int = 120):
    path = Path(filepath).resolve()
    if not path.exists():
        print(f"{RED}File not found: {path}{RESET}")
        return

    print(f"{CYAN}Watching {path} for changes... (Ctrl+C to stop){RESET}")
    lean = LeanRepl(timeout=timeout)
    last_hash = None

    try:
        while True:
            content = path.read_text()
            h = hashlib.md5(content.encode()).hexdigest()
            if h != last_hash:
                last_hash = h
                print(f"\n{DIM}{'─' * 60}{RESET}")
                print(f"{CYAN}[{time.strftime('%H:%M:%S')}] Verifying {path.name}...{RESET}")
                # Full file → fresh env each time
                lean.restart()
                result = lean.send(content)
                print(format_result(result))
            time.sleep(1)
    except KeyboardInterrupt:
        print(f"\n{DIM}Stopped watching.{RESET}")
    finally:
        lean.close()


# ── Interactive REPL ────────────────────────────────────────────────

PLACEHOLDER_CONTINUE = "<!-- CONTINUE -->"


def repl(initial_code: str = "", timeout: int = 120):
    """Interactive REPL with persistent process and incremental env."""
    lean = LeanRepl(timeout=timeout)
    current_env = None
    buf = ""
    last_result = None

    print(f"{BOLD}Sparkle Lean 4 Interactive Verifier{RESET}")
    print(f"{DIM}Persistent `lake exe repl` — incremental env across commands.{RESET}")
    print(f"{DIM}Type Lean code, blank line to verify. :help for commands.{RESET}")
    print()

    # Auto-load prelude
    print(f"{CYAN}Loading Sparkle prelude...{RESET}")
    r = lean.send(PRELUDE)
    print(format_result(r))
    if r["pass"] and r["env"] is not None:
        current_env = r["env"]
    print()

    # If initial code provided, verify on top of prelude env
    if initial_code.strip():
        code = initial_code
        for line in PRELUDE.splitlines():
            code = code.replace(line + "\n", "")
        code = code.strip()
        if code:
            print(f"{CYAN}Verifying initial code...{RESET}")
            last_result = lean.send(code, env=current_env)
            print(format_result(last_result))
            if last_result["pass"] and last_result["env"] is not None:
                current_env = last_result["env"]
            print()

    try:
        while True:
            try:
                prompt = f"{GREEN}lean[{current_env if current_env is not None else '?'}]>{RESET} "
                line = input(prompt)
            except (EOFError, KeyboardInterrupt):
                print(f"\n{DIM}Bye!{RESET}")
                break

            stripped = line.strip()

            if stripped in (":quit", ":q"):
                print(f"{DIM}Bye!{RESET}")
                break

            elif stripped == ":help":
                print(__doc__)

            elif stripped == ":clear":
                buf = ""
                print(f"{DIM}Buffer cleared.{RESET}")

            elif stripped == ":last":
                if last_result:
                    print(format_result(last_result))
                else:
                    print(f"{DIM}No results yet.{RESET}")

            elif stripped == ":verilog":
                if last_result and last_result.get("verilog"):
                    print(last_result["verilog"])
                else:
                    print(f"{DIM}No Verilog in last result.{RESET}")

            elif stripped == ":env":
                print(f"Current env: {current_env}")

            elif stripped == ":reset":
                print(f"{CYAN}Restarting REPL process...{RESET}")
                lean.restart()
                r = lean.send(PRELUDE)
                print(format_result(r))
                current_env = r["env"] if r["pass"] else None
                buf = ""

            elif stripped.startswith(":load "):
                fpath = stripped[6:].strip()
                p = Path(fpath).resolve()
                if not p.exists():
                    print(f"{RED}File not found: {fpath}{RESET}")
                    continue
                code = p.read_text()
                print(f"{CYAN}Loaded {p.name} ({len(code)} bytes). Fresh env...{RESET}")
                lean.restart()
                last_result = lean.send(code)
                print(format_result(last_result))
                if last_result["pass"] and last_result["env"] is not None:
                    current_env = last_result["env"]
                buf = ""

            elif stripped == ":edit":
                editor = os.environ.get("EDITOR", "vim")
                with tempfile.NamedTemporaryFile(suffix=".lean", mode="w", delete=False) as f:
                    f.write(buf if buf else "-- Write your Lean code here\n")
                    tmppath = f.name
                subprocess.run([editor, tmppath])
                buf = Path(tmppath).read_text()
                os.unlink(tmppath)
                if buf.strip():
                    print(f"{CYAN}Verifying...{RESET}")
                    last_result = lean.send(buf, env=current_env)
                    print(format_result(last_result))
                    if last_result["pass"] and last_result["env"] is not None:
                        current_env = last_result["env"]
                    buf = ""

            elif stripped == "" and buf.strip():
                print(f"{CYAN}Verifying...{RESET}")
                last_result = lean.send(buf, env=current_env)
                print(format_result(last_result))
                if last_result["pass"] and last_result["env"] is not None:
                    current_env = last_result["env"]
                buf = ""

            elif stripped == "":
                pass

            else:
                buf += line + "\n"
                n = len([l for l in buf.splitlines() if l.strip()])
                print(f"{DIM}  [{n} lines in buffer]{RESET}")
    finally:
        lean.close()


def main():
    parser = argparse.ArgumentParser(
        description="Interactive Lean 4 verifier for Sparkle chip design")
    parser.add_argument("file", nargs="?", help="Lean file to verify")
    parser.add_argument("--watch", "-w", action="store_true",
                        help="Watch file for changes")
    parser.add_argument("--timeout", "-t", type=int, default=120,
                        help="Verification timeout in seconds (default: 120)")
    args = parser.parse_args()

    if args.file and args.watch:
        watch_file(args.file, timeout=args.timeout)
    elif args.file:
        path = Path(args.file).resolve()
        if not path.exists():
            print(f"{RED}File not found: {path}{RESET}")
            sys.exit(1)
        repl(initial_code=path.read_text(), timeout=args.timeout)
    else:
        repl(timeout=args.timeout)


if __name__ == "__main__":
    main()
