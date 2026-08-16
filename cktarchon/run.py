from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
import fcntl
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from .env import ensure_runtime_env, load_env_file, model_alias
from .harness import AnthropicHarnessRunner, AnthropicTextRunner, configure_anthropic_credentials_from_env
from .logs import AgentStats, append_jsonl, parse_agent_log
from .search_strategy import (
    CandidateTracker,
    SelfTestGuide,
    SelfTestResult,
    TurnBudget,
    build_self_test_planner_prompt,
    format_self_test_guidance,
    parse_self_test_guide,
    run_generated_self_test,
    validate_self_test_guide,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CVDP_SCAFFOLD_MARKER = "CKTARCHON_IMPLEMENTATION_REQUIRED"

COMPACT_SPARKLE_GENERATION_SKILL = """You are an expert hardware engineer translating natural-language RTL specifications into Sparkle HDL, a Lean 4 hardware DSL.

## Goal
Produce one Lean file that compiles, synthesizes SystemVerilog with `#synthesizeVerilog`, and is behaviorally faithful to the benchmark spec.

## File Template
```lean
import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal
open Sparkle.Library.RTL

/-- <one-line description> -/
def <target_module> {dom : DomainConfig}
    (<inputs>) : <output_type> :=
  <implementation>

#synthesizeVerilog <target_module>
```

## Core Types
- `Signal dom (BitVec N)` is an N-bit hardware signal.
- `Signal dom Bool` is a hardware condition signal.
- Sparkle's hardware clock is implicit in `DomainConfig` and synthesizes as the core `clk` port. Never declare a clock `Signal` binder in the Lean function, even when the Benchmark Interface Contract lists a public clock; the CVDP wrapper maps that public clock to implicit `clk`.
- A benchmark-visible reset remains an explicit input: declare the exact benchmark reset name as a `Signal dom Bool` binder and apply `resetHigh` for active-high or `resetLow` for active-low to the register D-path. The CVDP wrapper maps this binder and holds Sparkle's separate implicit ABI `rst` deasserted so the selected D-path semantics are preserved.
- Multi-output Sparkle functions return tuple signals, e.g. `Signal dom (BitVec 8 × BitVec 1)` with `bundle2`.

## Stable Sparkle Operators
- Bitwise/arithmetic: `~~~a`, `a &&& b`, `a ||| b`, `a ^^^ b`, `a + b`, `a - b`, `a * b`.
- Equality: `a === b` returns `Signal dom Bool`.
- Concatenation: `a ++ b`; first operand becomes the high bits.
- Shifts: `a <<< n`, `a >>> n`, where `n` is a same-width `BitVec` literal or signal.
- Mux: `Signal.mux cond trueValue falseValue`.
- Constants: use `N#W` directly with Signal operators or `Signal.pure (N#W)`.

## RTL Helpers
Use helpers from `Sparkle.Library.RTL` when they match the task:
- `slice x lo` with a type annotation for the result width.
- `trunc x`, `zext x`.
- `bit x i`, `bitBool x i`.
- `boolToBV1 b`, `bv1ToBool x`.
- `isZero x`, `nonZero x`, `allOnes x`.
- `dff`, `dffe`, `resetHigh`, `resetLow`.
- `syncRam1R1W`, `regFile1R1W`.
- `reverseBits8/16/32`, `popCount8/16/32`, `priorityEncodeLsb8/16/32`.

## Workflow
1. Read at most two small examples, preferably `Benchmark/RTLIdioms.lean` plus one similar `Benchmark/*.lean`.
2. Write a complete candidate to the required `Generated/<prob_id>.lean` file.
3. Run the harness-provided Lean check command.
4. Fix compiler/synthesis errors from that output.
5. Stop immediately after the Lean check reports success; the outer evaluator will run lint and simulation.
"""


def _add_legacy_agent_path() -> None:
    agent_dir = PROJECT_ROOT / "agent"
    if str(agent_dir) not in sys.path:
        sys.path.insert(0, str(agent_dir))


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="CktArchon: Archon-style NL2Chip benchmark runner")
    p.add_argument("--dataset", default="cvdp", choices=["verilogeval", "rtllm", "resbench", "cvdp", "realbench"])
    p.add_argument("--problem-file", type=str, default=None)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--filter", type=str, default=None)
    p.add_argument("--model", default="claude-sonnet-4.5")
    p.add_argument("--max-turns", type=int, default=80)
    p.add_argument("--max-tokens", type=int, default=16384)
    p.add_argument(
        "--prompt-profile",
        choices=["compact", "cvdp-skill-fewshot"],
        default="compact",
        help=(
            "Lean generation prompt profile. `compact` preserves the historical "
            "prompt; `cvdp-skill-fewshot` adds the curated Sparkle skill pack and "
            "non-evaluation few-shot examples."
        ),
    )
    p.add_argument(
        "--cvdp-local-guardrails",
        action="store_true",
        help=(
            "Use a compile-checked typed CVDP scaffold, local diagnostics, isolated candidates, and strict rollback."
        ),
    )
    p.add_argument(
        "--cvdp-verified-idioms",
        action="store_true",
        help=(
            "Retrieve a small deterministic set of compile-verified Sparkle idioms for the guarded CVDP standard loop."
        ),
    )
    p.add_argument("--results-dir", type=str, required=True)
    p.add_argument("--harness", default="anthropic-api", choices=["anthropic-api", "codex-agent", "archon-native"])
    p.add_argument("--key-env", default=str(PROJECT_ROOT / "key.env"))
    p.add_argument("--resume", action="store_true")
    p.add_argument("--resume-mode", choices=["passed", "completed"], default="completed")
    p.add_argument("--no-repl", action="store_true")
    p.add_argument("--eval-only", action="store_true", help="Skip agent generation and only evaluate existing Generated/<prob_id>.lean files.")
    p.add_argument("--workers", type=int, default=1, help="Concurrent problem workers; each receives an isolated Lean REPL.")
    p.add_argument(
        "--archon-src",
        default=None,
        help=(
            "Official Archon src directory for codex-agent/archon-native harnesses. "
            "Overrides the ARCHON_SRC environment variable; one of them is required "
            "for those harnesses."
        ),
    )
    p.add_argument("--codex-bin", default=None, help="Optional absolute path to the codex CLI for --harness codex-agent.")
    p.add_argument("--codex-effort", default=None, help="Optional model_reasoning_effort passed to codex exec.")
    p.add_argument("--codex-sandbox", default="danger-full-access", help="Codex sandbox mode.")
    p.add_argument("--codex-idle-timeout", type=float, default=900.0, help="Seconds of no JSONL activity before Archon restarts codex.")
    p.add_argument("--codex-max-attempts", type=int, default=3, help="Archon CodexAgent retry attempts after idle timeouts.")
    p.add_argument("--codex-base-url-env", default=None, help="Env var containing a Codex-compatible gateway base URL.")
    p.add_argument("--codex-key-env", default=None, help="Env var containing the Codex-compatible gateway API key.")
    p.add_argument("--codex-wire-api", default="responses", choices=["responses", "chat"], help="Codex custom-provider wire API.")
    p.add_argument("--no-codex-chat-proxy", action="store_true", help="Disable the local Responses-to-Chat proxy used when no Codex gateway is configured.")
    p.add_argument("--api-timeout", type=float, default=300.0, help="Per-request timeout for --harness anthropic-api.")
    p.add_argument("--sim-feedback", action="store_true", help="Repair Lean after RTL compile/simulation failures using compact simulator feedback.")
    p.add_argument("--sim-feedback-max-iters", type=int, default=3, help="Max simulation-feedback repair attempts per problem.")
    p.add_argument("--sim-feedback-turn-budget", type=int, default=None, help="Total extra agent turns available for simulation-feedback repairs per problem. Defaults to the old shared remaining budget.")
    p.add_argument("--sim-feedback-turns-per-iter", type=int, default=None, help="Max agent turns for each individual simulation-feedback repair attempt.")
    p.add_argument("--sim-feedback-patience", type=int, default=2, help="Stop after this many non-improving feedback repairs; set 0 to disable.")
    p.add_argument(
        "--guided-search",
        action="store_true",
        help=(
            "Enable public-spec self-test guidance plus stagnation-triggered fresh candidates. "
            "All planner/generation/repair calls share one turn budget."
        ),
    )
    p.add_argument(
        "--search-total-turn-budget",
        type=int,
        default=None,
        help=(
            "Total turns shared by self-test planning, initial generation, repairs, and fresh candidates. "
            "Defaults to max-turns plus an explicit sim-feedback-turn-budget."
        ),
    )
    p.add_argument("--self-test-planner-turns", type=int, default=1, help="Turns reserved from the shared budget for public-spec self-test planning.")
    p.add_argument(
        "--disable-guided-self-test",
        action="store_true",
        help="Disable public-spec test planning, advisory TB execution, and self-test feedback while retaining guided candidate search.",
    )
    p.add_argument("--candidate-search-max", type=int, default=3, help="Maximum independent Lean candidate lineages in guided search.")
    p.add_argument("--guided-self-test-mode", choices=["guidance", "execute"], default="guidance", help="Use one public-spec TB as prompt guidance only (default), or run the legacy advisory-TB execution ablation.")
    p.add_argument("--candidate-stagnation-patience", type=int, default=2, help="Start a fresh candidate after this many non-improving attempts.")
    return p.parse_args()


def validate_cvdp_verified_idiom_mode(
    args: argparse.Namespace,
    *,
    active_repl: bool | None = None,
) -> None:
    """Reject every unsupported verified-idiom execution mode."""

    if not bool(getattr(args, "cvdp_verified_idioms", False)):
        return
    requirements = (
        (getattr(args, "dataset", None) == "cvdp", "--dataset cvdp"),
        (bool(getattr(args, "cvdp_local_guardrails", False)), "--cvdp-local-guardrails"),
        (getattr(args, "harness", None) == "anthropic-api", "--harness anthropic-api"),
        (not bool(getattr(args, "guided_search", False)), "the standard (non-guided) loop"),
        (not bool(getattr(args, "eval_only", False)), "generation mode (not --eval-only)"),
        (not bool(getattr(args, "no_repl", False)), "the Lean REPL (remove --no-repl)"),
        (
            getattr(args, "prompt_profile", None) == "cvdp-skill-fewshot",
            "--prompt-profile cvdp-skill-fewshot",
        ),
    )
    missing = [description for satisfied, description in requirements if not satisfied]
    if active_repl is False:
        missing.append("an active Lean REPL")
    if missing:
        raise ValueError(
            "--cvdp-verified-idioms is fail-closed and requires "
            + ", ".join(missing)
        )


def discover_problems(args: argparse.Namespace, ds: Any) -> list[str]:
    if args.problem_file:
        path = Path(args.problem_file)
        problems = [line.strip() for line in path.read_text().splitlines() if line.strip() and not line.lstrip().startswith("#")]
        if args.filter:
            pattern = re.compile(args.filter)
            problems = [pid for pid in problems if pattern.search(pid)]
        # Preserve the first occurrence so repeated lines cannot schedule two
        # workers against the same Generated/<prob_id>.lean target.
        problems = list(dict.fromkeys(problems))
        if args.limit:
            problems = problems[: args.limit]
        return problems
    problems = ds.discover_problems(limit=None, filter_re=args.filter)
    problems = list(dict.fromkeys(problems))
    if args.limit:
        problems = problems[: args.limit]
    return problems


def already_done(run_parent: Path, prob_id: str, mode: str) -> bool:
    for results in run_parent.glob("*/results.jsonl"):
        try:
            for line in results.read_text(errors="replace").splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                if row.get("prob_id") != prob_id:
                    continue
                if mode == "passed" and row.get("sim_status") == "sim_pass":
                    return True
                agent_error = str(row.get("agent_error") or "")
                if mode == "completed" and (not agent_error or "max-turns budget" in agent_error):
                    return True
        except Exception:
            continue
    return False


def generated_candidate_available(target: Path) -> bool:
    """Return whether *target* contains a usable, non-whitespace candidate."""

    try:
        return bool(target.read_text(errors="replace").strip())
    except OSError:
        return False


def reject_incomplete_cvdp_scaffold(
    result: dict[str, Any], source: str
) -> dict[str, Any]:
    """Fail closed when the behavior-neutral starter marker remains."""

    if CVDP_SCAFFOLD_MARKER not in source:
        return result
    result = dict(result)
    result["scaffold_incomplete"] = True
    marker_detail = (
        "Typed scaffold is still marked CKTARCHON_IMPLEMENTATION_REQUIRED; "
        "implement every TODO output and remove the marker before completion."
    )
    detail = str(result.get("detail", "") or "")
    if marker_detail not in detail:
        result["detail"] = marker_detail + (("\n" + detail) if detail else "")
    if result.get("sim_status") == "sim_pass":
        result["sim_status"] = "sim_fail"
        mismatches = result.get("sim_mismatches")
        result["sim_mismatches"] = (
            max(1, mismatches) if isinstance(mismatches, int) else 1
        )
    return result


class GeneratedCandidateTransaction:
    """Serialize and protect one problem's ``Generated`` candidate lifecycle.

    The per-problem advisory lock is held from before the old candidate is
    staged through generation and evaluation. The old file is moved atomically
    within ``Generated``; the results-directory copy is only an audit backup.
    """

    def __init__(
        self,
        project_root: Path,
        run_dir: Path,
        prob_id: str,
        *,
        read_only: bool = False,
    ):
        self.project_root = project_root
        self.run_dir = run_dir
        self.prob_id = prob_id
        self.target = project_root / "Generated" / f"{prob_id}.lean"
        self.read_only = read_only
        self.backup_path: Path | None = None
        self._staged_original: Path | None = None
        self._lock_file: Any | None = None
        self._lock_acquired = False
        self._entered = False
        self._had_preexisting = False
        self.restored = False
        self.restore_error: str | None = None
        self._discarded = False

    @property
    def backup(self) -> str | None:
        return str(self.backup_path) if self.backup_path is not None else None

    def __enter__(self) -> GeneratedCandidateTransaction:
        self.target.parent.mkdir(parents=True, exist_ok=True)
        lock_dir = self.target.parent / ".cktarchon_locks"
        lock_dir.mkdir(parents=True, exist_ok=True)
        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", self.prob_id).strip("._")[:48]
        digest = hashlib.sha256(self.prob_id.encode("utf-8")).hexdigest()[:16]
        lock_path = lock_dir / f"{safe_name or 'problem'}-{digest}.lock"
        self._lock_file = lock_path.open("a+b")
        try:
            fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_EX)
            self._lock_acquired = True
            self._entered = True
            if not self.read_only:
                self._stage_preexisting()
        except BaseException:
            try:
                if self._staged_original is not None:
                    self._restore_staged_original()
            finally:
                self._release_lock()
            raise
        return self

    def _stage_preexisting(self) -> None:
        if not os.path.lexists(self.target):
            return
        self._had_preexisting = True
        staged = self.target.with_name(
            f".{self.target.name}.preexisting-{uuid.uuid4().hex}"
        )
        self._staged_original = staged
        os.replace(self.target, staged)

        backup_dir = self.run_dir / "preexisting_generated"
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup = backup_dir / f"{self.prob_id}.lean"
        if backup.exists():
            backup = backup_dir / f"{self.prob_id}_{uuid.uuid4().hex}.lean"
        fd, temporary_name = tempfile.mkstemp(
            dir=backup_dir,
            prefix=f".{backup.name}.copy-",
        )
        os.close(fd)
        temporary = Path(temporary_name)
        try:
            shutil.copy2(staged, temporary)
            os.replace(temporary, backup)
        finally:
            temporary.unlink(missing_ok=True)
        self.backup_path = backup

    @staticmethod
    def _path_has_nonempty_candidate(path: Path) -> bool:
        try:
            return bool(path.read_text(errors="replace").strip())
        except FileNotFoundError:
            return False
        except OSError:
            raise

    def has_new_candidate(self) -> bool:
        """Strictly check the current target without treating I/O errors as empty."""

        return self._path_has_nonempty_candidate(self.target)

    def _archive_detached_candidate(self, detached: Path) -> None:
        """Keep a raced inode linked on the target filesystem for late writers."""

        conflict_dir = self.target.parent / ".cktarchon_conflicts"
        conflict_dir.mkdir(parents=True, exist_ok=True)
        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", self.prob_id).strip("._")[:48]
        destination = conflict_dir / (
            f"{safe_name or 'problem'}-{uuid.uuid4().hex}.lean"
        )
        # Same-filesystem rename preserves a directory entry even when an
        # uncooperative writer still holds the detached inode open and writes
        # only after our first content check.
        os.replace(detached, destination)

    def restore_preexisting(self) -> bool:
        """Restore the staged original without clobbering a non-empty new file."""

        if self.has_new_candidate():
            return False

        # Never unlink the path after a separate availability check. Atomically
        # detach exactly the inode currently at the target, then classify that
        # detached file. A writer that swapped in a non-empty candidate during
        # the race therefore has its file reinstalled or archived, not deleted.
        detached: Path | None = None
        if os.path.lexists(self.target):
            detached = self.target.with_name(
                f".{self.target.name}.restore-observed-{uuid.uuid4().hex}"
            )
            try:
                os.replace(self.target, detached)
            except FileNotFoundError:
                detached = None
        if detached is not None:
            if self._path_has_nonempty_candidate(detached):
                try:
                    os.link(detached, self.target)
                except FileExistsError:
                    self._archive_detached_candidate(detached)
                else:
                    detached.unlink()
                return False
            # Even an artifact that is blank *now* may have a writer holding an
            # open descriptor. Archive its inode before restoring the old path;
            # a late write then remains recoverable instead of targeting an
            # already-unlinked inode.
            self._archive_detached_candidate(detached)

        restored = self._restore_staged_original()
        self.restored = restored
        return restored

    def _restore_staged_original(self) -> bool:
        staged = self._staged_original
        if staged is None or not staged.exists():
            return False
        try:
            # link(2) fails with EEXIST instead of overwriting a candidate that
            # appeared concurrently between the availability check and install.
            os.link(staged, self.target)
        except FileExistsError:
            if self.has_new_candidate():
                return False
            raise RuntimeError(
                f"refused to overwrite concurrent Generated candidate: {self.target}"
            )
        staged.unlink()
        self._staged_original = None
        return True

    def discard_preexisting(self) -> None:
        """Commit the historical clear-only helper's removal semantics."""

        if self._staged_original is not None:
            self._staged_original.unlink(missing_ok=True)
            self._staged_original = None
        self._discarded = True

    def _release_lock(self) -> None:
        if self._lock_file is None:
            return
        try:
            if self._lock_acquired:
                fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_UN)
        finally:
            self._lock_file.close()
            self._lock_file = None
            self._lock_acquired = False
            self._entered = False

    def __exit__(self, exc_type: Any, exc: BaseException | None, traceback: Any) -> bool:
        cleanup_error: Exception | None = None
        try:
            if self.read_only:
                return False
            if not self._discarded and self._staged_original is not None:
                if self.has_new_candidate():
                    self._staged_original.unlink(missing_ok=True)
                    self._staged_original = None
                else:
                    try:
                        self.restore_preexisting()
                    except Exception as restore_exc:
                        self.restore_error = f"{type(restore_exc).__name__}: {restore_exc}"
                        cleanup_error = restore_exc
            elif (
                self._staged_original is None
                and not self._had_preexisting
                and not self.has_new_candidate()
            ):
                # Use the same detach/archive path; never check then unlink.
                self.restore_preexisting()
        finally:
            self._release_lock()

        if cleanup_error is not None:
            if exc is not None and hasattr(exc, "add_note"):
                exc.add_note(f"candidate restoration also failed: {cleanup_error}")
                return False
            raise cleanup_error
        return False


def clear_generated_target(project_root: Path, run_dir: Path, prob_id: str) -> str | None:
    """Atomically clear one target under the same transaction lock as generation."""

    with GeneratedCandidateTransaction(project_root, run_dir, prob_id) as transaction:
        backup = transaction.backup
        transaction.discard_preexisting()
        return backup


def build_system_prompt(
    skill: str,
    prob_id: str,
    info: Any | None = None,
    prompt_profile: str = "compact",
    cvdp_local_guardrails: bool = False,
    cvdp_verified_idioms: bool = False,
) -> str:
    design_name = getattr(info, "design_name", None) if info is not None else None
    design_rule = ""
    if design_name:
        design_rule = (
            f"- The output file is `Generated/{prob_id}.lean`, but the Lean function/top module must be `{design_name}`.\n"
            f"- Use `#synthesizeVerilog {design_name}`. Do not name the synthesized function `{prob_id}` unless the problem explicitly says that is the target module.\n"
        )
    skill_section = ""
    if cvdp_verified_idioms:
        if prompt_profile != "cvdp-skill-fewshot":
            raise ValueError(
                "verified CVDP idioms require prompt_profile='cvdp-skill-fewshot'"
            )
        skill_section = (
            "\n## Retrieved Verified Sparkle Idioms (authoritative)\n"
            "The user prompt contains a small deterministic selection from the "
            "compile-verified, specification-neutral idiom catalog. Use those "
            "retrieved bodies only as expression-shape guides; the benchmark "
            "contract and typed scaffold remain authoritative. The static "
            "skill.txt reference is intentionally not appended in this mode.\n"
        )
    elif prompt_profile == "cvdp-skill-fewshot":
        skill_section = (
            "\n## Curated Sparkle Skill and Few-Shot Reference\n"
            "The following compact reference has been verified on non-evaluation "
            "examples. Apply its patterns, but implement the current contract "
            "rather than copying a mismatched interface.\n\n"
            + skill.strip()
            + "\n"
        )
    local_guardrail_section = ""
    if cvdp_local_guardrails:
        local_guardrail_section = (
            "\n## Local CVDP guardrails (authoritative)\n"
            f"- `Generated/{prob_id}.lean` already contains a compile-checked typed scaffold. Read and edit it instead of guessing a new signature.\n"
            "- Preserve its Nat parameters, Signal binders, return width, named MSB-to-LSB packing, and synthesis command.\n"
            "- The marker CKTARCHON_IMPLEMENTATION_REQUIRED means the implementation is incomplete even when it compiles. Implement every TODO and remove the marker only after real logic is Lean-checked.\n"
            "- Use a read/edit/lean_check tool on every unfinished turn; a prose-only answer is not progress.\n"
            "- Never inspect another Generated candidate. Concurrent candidates are unverified and isolated.\n"
            "- Signal.loop is a fixed point: its body returns exactly the state Signal type (usually dff init next); derive visible outputs outside the loop.\n"
            "- Compiler success is a checkpoint, not the finish condition; the outer evaluator still requires adapter, lint, and simulation success.\n"
        )
    return (
        COMPACT_SPARKLE_GENERATION_SKILL.rstrip()
        + skill_section
        + "\n\n## CktArchon harness rules\n"
        + f"- You are running under the Archon-style CktArchon harness for `{prob_id}`.\n"
        + f"- Write only `Generated/{prob_id}.lean` using the `write_file`/`edit_file` tools.\n"
        + design_rule
        + "- Treat the user's `Benchmark Interface Contract` as authoritative over guesses from examples or file names.\n"
        + "- For CVDP parameters, use a top-level Lean `Nat` binder with the exact benchmark parameter name and `#synthesizeVerilog <design> parameters [PARAM := <nonnegative-default>]`. Sparkle emits the native SystemVerilog module parameter; it must actually determine the relevant datapath, state, memory, or logic. Defaults may be zero for offsets, but every derived hardware width and array length must remain positive. The evaluator rejects declaration-only parameters and fixed-width cores hidden behind an adapter.\n"
        + "- Match benchmark output names exactly. If you must return a packed output internally, construct an explicit named MSB-to-LSB concat so the CVDP wrapper can recover each output field.\n"
        + "- For CVDP, a benchmark-visible clock is supplied only through Sparkle's implicit domain `clk`, which the wrapper maps to the public clock. Never declare a clock `Signal` binder, even when the public interface lists one; doing so creates a duplicate clock input.\n"
        + "- A benchmark-visible reset is different: declare its exact public name as a `Signal dom Bool` binder and apply `resetHigh` or `resetLow` according to polarity before `dff`/`dffe`. The wrapper maps that explicit reset binder while holding Sparkle's separate implicit ABI `rst` deasserted, preserving D-path reset semantics.\n"
        + "- Preserve benchmark reset polarity and cycle latency exactly; the cocotb harness checks protocol timing, not just combinational truth tables.\n"
        + "- Use `lean_check` frequently; it uses the persistent Lean REPL when available. Every inline `code` check must include the complete module body and `#synthesizeVerilog`; a check is usable only when it also returns `Generated Verilog`. The harness automatically saves the latest such compile-safe candidate.\n"
        + "- Keep repository exploration short: read at most three examples, then write a complete candidate and iterate from compiler feedback.\n"
        + "- This H20 host may not have `rg`; use `grep` and `find` for repository searches.\n"
        + "- If you need a directory listing, use `list_directory`; do not call `read_file` on directories.\n"
        + "- The key Sparkle synthesis rules are included below. Do not `cat` all of `docs/Troubleshooting_Synthesis.md`; if you need more detail, use `grep` for a narrow pattern.\n"
        + "- Prefer simple `Signal dom ... -> Signal dom ...` combinational helpers. Avoid pure helper functions with `match`, `if`, tuples, or recursion when their result depends on hardware signals.\n"
        + "- Use `Signal.mux` for all data-dependent choices. Sparkle cannot synthesize Lean `if`/`match`/`ite` over signal-derived values or inside `Signal.map` lambdas.\n"
        + "- Use supported binary Signal operators directly (`+`, `-`, `*`, `&&&`, `|||`, `^^^`, `===`, `++`, `<<<`, `>>>`). Avoid multi-argument lambdas such as `(fun a b => ...) <$> x <*> y`.\n"
        + "- For Signal shifts, the shift amount must be a same-width `BitVec` literal or Signal, e.g. `x >>> 1#8` for `Signal dom (BitVec 8)` and `x <<< 2#16` for `Signal dom (BitVec 16)`. Never write `x >>> 1` or an undersized amount such as `x >>> 1#3`.\n"
        + "- For comparisons other than equality, use Sparkle helpers such as `Signal.ult` or `Signal.slt` with same-width operands; do not use Lean `<`, `>`, `<=`, or `>=` on `Signal` values.\n"
        + "- For `Signal dom Bool`, avoid Lean `||` and `&&` over signals. Implement OR as `Signal.mux a (Signal.pure true) b` and AND as `Signal.mux a b (Signal.pure false)` unless a checked local example shows a better pattern.\n"
        + "- For tuple-valued signals, project with `.fst`/`.snd` or `projN!`; do not destructure with `let (a, b) := ...` in synthesizable code.\n"
        + "- For packed `BitVec` outputs, prefer explicit `++` concatenation of sized Signal operands. Do not use `bundleAll!` to build a packed bit-vector result.\n"
        + "- For RTL bit manipulation, use `Sparkle.Library.RTL` helpers such as `bit`, `slice`, `zext`, and `trunc` when a local checked example confirms the expected type.\n"
        + "- Do not leave placeholders such as `sorry`, `admit`, or dummy zero outputs in the synthesized implementation.\n"
        + "- Do not modify benchmark sources, Sparkle library code, or other Generated files.\n"
        + "- Stop once the generated Lean file compiles; the CktArchon evaluator will run Verilog extraction, lint, and simulation.\n"
        + local_guardrail_section
    )


def configured_archon_src(cli_value: str | Path | None) -> Path | None:
    """Resolve explicit CLI-over-environment Archon source configuration."""

    value = cli_value or os.environ.get("ARCHON_SRC")
    if not value:
        return None
    return Path(value).expanduser()


def run_archon_native_unavailable(archon_src: str | Path | None) -> None:
    # The shape is explicit so future CLI-backed Archon runners can plug in here.
    source = configured_archon_src(archon_src)
    if source is None:
        raise RuntimeError(
            "archon-native harness requires an explicit official Archon src directory; "
            "pass --archon-src PATH or set ARCHON_SRC"
        )
    source = source.resolve()
    if not source.is_dir():
        raise RuntimeError(f"official Archon src directory does not exist: {source}")
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))
    try:
        from archon.agent import build_runner  # noqa: F401
    except Exception as exc:
        raise RuntimeError(f"Official Archon import failed: {exc}") from exc
    raise RuntimeError(
        "archon-native harness requested, but this H20 image has no `claude`/`codex` CLI. "
        "Use --harness anthropic-api now, or install the native CLI and wire this adapter."
    )


def make_runner(
    *,
    args: argparse.Namespace,
    prob_id: str,
    role: str,
    log_base: Path,
    skill: str,
    info: Any | None,
    repl: Any | None,
    cvdp_verified_idioms: bool | None = None,
) -> Any:
    if cvdp_verified_idioms is None:
        cvdp_verified_idioms = bool(
            getattr(args, "cvdp_verified_idioms", False)
        )
    if cvdp_verified_idioms:
        validate_cvdp_verified_idiom_mode(args, active_repl=repl is not None)
    if args.harness == "archon-native":
        run_archon_native_unavailable(getattr(args, "archon_src", None))
    if args.harness == "codex-agent":
        from .codex_runner import CodexAgentHarnessRunner

        return CodexAgentHarnessRunner(
            project_root=PROJECT_ROOT,
            prob_id=prob_id,
            model=model_alias(args.model),
            role=role,
            log_base=log_base,
            system_prompt=build_system_prompt(
                skill,
                prob_id,
                info,
                args.prompt_profile,
                getattr(args, "cvdp_local_guardrails", False),
                cvdp_verified_idioms,
            ),
            archon_src=configured_archon_src(getattr(args, "archon_src", None)),
            codex_bin=args.codex_bin,
            effort=args.codex_effort,
            sandbox=args.codex_sandbox,
            idle_timeout_s=args.codex_idle_timeout,
            max_attempts=args.codex_max_attempts,
            base_url_env=args.codex_base_url_env,
            key_env=args.codex_key_env,
            wire_api=args.codex_wire_api,
            auto_chat_proxy=not args.no_codex_chat_proxy,
            chat_proxy_timeout_s=args.api_timeout,
        )
    return AnthropicHarnessRunner(
        project_root=PROJECT_ROOT,
        prob_id=prob_id,
        model=model_alias(args.model),
        role=role,
        log_base=log_base,
        system_prompt=build_system_prompt(
            skill,
            prob_id,
            info,
            args.prompt_profile,
            getattr(args, "cvdp_local_guardrails", False),
            cvdp_verified_idioms,
        ),
        max_tokens=args.max_tokens,
        lean_repl=repl,
        api_timeout=args.api_timeout,
        local_guardrails=getattr(args, "cvdp_local_guardrails", False),
    )


def merge_agent_stats(total: AgentStats, extra: AgentStats) -> None:
    total.input_tokens += extra.input_tokens
    total.output_tokens += extra.output_tokens
    total.turns += extra.turns
    total.compile_checks += extra.compile_checks
    for name, count in extra.tool_counts.items():
        total.tool_counts[name] = total.tool_counts.get(name, 0) + count
    total.budget_exhausted = total.budget_exhausted or extra.budget_exhausted
    total.usage_accounting_complete = (
        total.usage_accounting_complete and extra.usage_accounting_complete
    )
    for note in extra.usage_accounting_notes:
        if note not in total.usage_accounting_notes:
            total.usage_accounting_notes.append(note)


def agent_stats_observed(stats: AgentStats) -> bool:
    return bool(stats.turns or stats.input_tokens or stats.output_tokens or stats.tool_counts) or (
        stats.budget_exhausted or not stats.usage_accounting_complete
    )


def rounded_agent_elapsed(seconds: float) -> float:
    rounded = round(seconds, 3)
    if seconds > 0 and rounded == 0:
        return 0.001
    return rounded


def accumulate_summary_record(summary: dict[str, Any], record: dict[str, Any]) -> None:
    """Add one final problem record to the run summary.

    Failure classification is authoritative for generation failures. Such
    records commonly have sim_status=not_run because no simulator was reached;
    counting them as sim_error hides the real agent failure rate.
    """

    if record.get("compile_pass"):
        summary["compile_pass"] += 1
    summary["sim_feedback_attempts"] += int(record.get("sim_feedback_iterations") or 0)
    if record.get("sim_feedback_success"):
        summary["sim_feedback_success"] += 1

    status = record.get("sim_status")
    is_agent_error = (
        record.get("failure_category") == "agent_error"
        or bool(record.get("agent_error"))
        or status == "agent_error"
    )
    if is_agent_error:
        summary["agent_error"] += 1
    elif status == "sim_pass":
        summary["sim_pass"] += 1
    elif status == "sim_fail":
        summary["sim_fail"] += 1
    else:
        summary["sim_error"] += 1


def search_total_turn_budget(args: argparse.Namespace) -> int:
    if args.search_total_turn_budget is not None:
        return max(0, int(args.search_total_turn_budget))
    feedback = args.sim_feedback_turn_budget
    return max(0, int(args.max_turns)) + (max(0, int(feedback)) if feedback is not None else 0)


def save_self_test_guide(run_dir: Path, prob_id: str, guide: SelfTestGuide) -> None:
    guide_dir = run_dir / "self_test_guides" / prob_id
    guide_dir.mkdir(parents=True, exist_ok=True)
    (guide_dir / "test_plan.md").write_text(guide.test_plan, encoding="utf-8")
    if guide.testbench_sv:
        (guide_dir / "self_test.sv").write_text(guide.testbench_sv, encoding="utf-8")
    (guide_dir / "planner_response.txt").write_text(guide.raw_response, encoding="utf-8")


def self_test_feedback(result: SelfTestResult | None) -> str:
    if result is None or result.status in {"not_run", "unavailable", "invalid", "guidance_only"}:
        return ""
    return (
        "### Latest Advisory Self-Test Result\n\n"
        f"- Status: {result.status}\n"
        "- This test was generated only from the public specification. The benchmark evaluator remains authoritative.\n"
        "```text\n"
        f"{result.detail[-8000:]}\n"
        "```"
    )


def build_fresh_candidate_prompt(
    *,
    search: Any,
    prob_id: str,
    info: Any,
    dataset_name: str,
    has_repl: bool,
    candidate_id: int,
    guide_text: str,
    prior_result: dict | None,
    recent_attempts: list[dict[str, Any]],
    latest_self_test: SelfTestResult | None,
) -> str:
    base = search.build_user_message(
        prob_id,
        has_repl=has_repl,
        info=info,
        dataset_name=dataset_name,
        condition_sv=None,
    )
    prior_summary = search.summarize_eval_result(prior_result)
    attempts = search.summarize_recent_attempts(recent_attempts)
    advisory = self_test_feedback(latest_self_test)
    return (
        base
        + "\n\n"
        + guide_text
        + "\n\n## Fresh Candidate Search\n\n"
        + f"This is independent candidate `{candidate_id}`. `Generated/{prob_id}.lean` has been cleared. "
        + "Design a complete implementation from the original specification instead of reconstructing or locally patching the previous Lean architecture. "
        + "Use a materially different state representation, pipeline/latency structure, or combinational decomposition where appropriate.\n\n"
        + "### Constraints Learned From Earlier Candidates\n\n"
        + prior_summary
        + "\n\n### Earlier Candidate Summaries\n\n"
        + attempts
        + ("\n\n" + advisory if advisory else "")
        + "\n\nLean-check the complete fresh candidate including `#synthesizeVerilog`. Stop only when the check also returns generated Verilog; the harness will save that compile-safe candidate."
    )


def process_problem_guided(
    prob_id: str,
    *,
    args: argparse.Namespace,
    ds: Any,
    evaluator: Any,
    run_dir: Path,
    skill: str,
    repl: Any | None,
    candidate_transaction: GeneratedCandidateTransaction,
) -> dict[str, Any]:
    """Run public-spec-guided, multi-candidate search under one turn ledger."""

    _add_legacy_agent_path()
    import search

    problem_t0 = time.monotonic()
    info = ds.load_problem(prob_id)
    benchmark_port_resolver = getattr(search, "_benchmark_expected_ports", None)
    benchmark_ports = (
        benchmark_port_resolver(info) if benchmark_port_resolver is not None else None
    )

    def evaluate_candidate():
        if benchmark_ports is None:
            return evaluator.evaluate(prob_id, run_dir)
        return evaluator.evaluate(
            prob_id, run_dir, benchmark_ports=benchmark_ports
        )
    has_repl = repl is not None
    generated_target = candidate_transaction.target
    preexisting_generated_backup = candidate_transaction.backup
    preexisting_generated_restored = False
    preexisting_generated_restore_error: str | None = None
    budget = TurnBudget(search_total_turn_budget(args))
    planner_stats = AgentStats()
    generation_stats = AgentStats()
    repair_stats_total = AgentStats()
    agent_elapsed = 0.0
    eval_elapsed = 0.0
    agent_errors: list[str] = []
    search_history: list[dict[str, Any]] = []
    self_test_history: list[dict[str, Any]] = []
    sim_feedback_iterations = 0
    sim_feedback_success = False

    interface_contract = search.format_benchmark_interface_contract(info)
    public_context = search.format_context_files(info)
    fallback_plan = (
        "Derive expected behavior only from the natural-language specification and the interface contract. "
        "Check reset polarity, cycle latency, boundary values, state transitions, output ordering, and every listed parameter setting."
    )
    guide = SelfTestGuide(test_plan=fallback_plan, testbench_sv="")
    planner_error: str | None = None
    self_test_validation_error: str | None = None
    self_test_enabled = not args.disable_guided_self_test
    self_test_mode = args.guided_self_test_mode if self_test_enabled else "disabled"
    planner_limit = budget.session_limit(args.self_test_planner_turns) if self_test_enabled else 0
    if planner_limit > 0 and args.harness == "anthropic-api":
        planner_prompt = build_self_test_planner_prompt(
            prob_id=prob_id,
            design_name=info.design_name,
            spec=info.prompt_text,
            interface_contract=interface_contract,
            public_context=public_context,
        )
        planner = AnthropicTextRunner(
            model=model_alias(args.model),
            role="ckt-public-self-test-planner",
            log_base=run_dir / "logs" / prob_id / "self_test_planner",
            system_prompt=(
                "You are a hardware verification planner. Use only the specification and public interface "
                "metadata in the user message. Never request, search for, or reconstruct hidden benchmark files."
            ),
            max_tokens=min(args.max_tokens, 8192),
            api_timeout=args.api_timeout,
        )
        planner_t0 = time.monotonic()
        try:
            response_text, planner_stats = planner.run(planner_prompt, max_turns=planner_limit)
            budget.consume(planner_stats.turns)
            parsed = parse_self_test_guide(response_text)
            if parsed.test_plan:
                guide, self_test_validation_error = validate_self_test_guide(
                    parsed,
                    design_name=info.design_name,
                    interface_contract=interface_contract,
                )
                if not guide.test_plan:
                    guide = SelfTestGuide(
                        test_plan=fallback_plan,
                        testbench_sv="",
                        raw_response=guide.raw_response,
                    )
        except Exception as exc:
            planner_error = f"{type(exc).__name__}: {exc}"
            try:
                parsed_stats = parse_agent_log(planner.log_path)
                if agent_stats_observed(parsed_stats):
                    planner_stats = parsed_stats
                    budget.consume(planner_stats.turns)
            except Exception as parse_exc:
                planner_error += (
                    "; planner log parsing also failed: "
                    f"{type(parse_exc).__name__}: {parse_exc}"
                )
        agent_elapsed += time.monotonic() - planner_t0
    elif args.harness != "anthropic-api":
        planner_error = "LLM self-test planner skipped because guided planning currently uses the Anthropic API harness."
    if self_test_enabled:
        save_self_test_guide(run_dir, prob_id, guide)

    full_guide_text = format_self_test_guidance(guide, include_testbench=True) if self_test_enabled else ""
    plan_only_text = format_self_test_guidance(guide, include_testbench=False) if self_test_enabled else ""

    generation_limit = budget.session_limit(args.max_turns)
    generation_error: str | None = None
    if generation_limit > 0:
        log_base = run_dir / "logs" / prob_id / "generate"
        generation_t0 = time.monotonic()
        try:
            user_message = search.build_user_message(
                prob_id,
                has_repl=has_repl,
                info=info,
                dataset_name=evaluator.dataset_name,
                condition_sv=None,
            ) + (("\n\n" + full_guide_text) if full_guide_text else "")
            runner = make_runner(
                args=args,
                prob_id=prob_id,
                role="ckt-generator-candidate-1",
                log_base=log_base,
                skill=skill,
                info=info,
                repl=repl,
            )
            generation_stats = runner.run(user_message, max_turns=generation_limit)
        except Exception as exc:
            generation_error = f"{type(exc).__name__}: {exc}"
            agent_errors.append(f"initial generation: {generation_error}")
            try:
                parsed_stats = parse_agent_log(Path(str(log_base) + ".jsonl"))
                if agent_stats_observed(parsed_stats):
                    generation_stats = parsed_stats
            except Exception as parse_exc:
                generation_error += (
                    "; agent log parsing also failed: "
                    f"{type(parse_exc).__name__}: {parse_exc}"
                )
                agent_errors[-1] = f"initial generation: {generation_error}"
        budget.consume(generation_stats.turns)
        agent_elapsed += time.monotonic() - generation_t0
    else:
        generation_error = "Initial generation received no turn budget."
        agent_errors.append(f"initial generation: {generation_error}")

    initial_candidate_available = candidate_transaction.has_new_candidate()
    if not initial_candidate_available and generation_error is None:
        generation_error = "Initial generation did not create a non-empty Lean candidate."
        agent_errors.append(f"initial generation: {generation_error}")

    eval_t0 = time.monotonic()
    if initial_candidate_available:
        result = evaluate_candidate()
        if generation_error:
            result["generation_error"] = generation_error
    else:
        result = {
            "prob_id": prob_id,
            "compile_pass": False,
            "sv_extracted": False,
            "lint_pass": False,
            "sim_status": "not_run",
            "sim_mismatches": -1,
            "detail": generation_error or "Initial generation did not create a Lean candidate.",
        }
    eval_elapsed += time.monotonic() - eval_t0

    code = generated_target.read_text(errors="replace") if initial_candidate_available else None
    tracker = CandidateTracker(
        snapshot_root=run_dir / "candidates",
        prob_id=prob_id,
        progress_key=search.eval_progress_key,
        max_candidates=args.candidate_search_max,
        patience=args.candidate_stagnation_patience,
    )
    initial_observation = tracker.start_candidate(result, code, reason="initial generation")
    search_history.append({
        "phase": "generation",
        "iteration": 0,
        **initial_observation.__dict__,
        "result_summary": search.summarize_eval_result(result),
        "turns": generation_stats.turns,
        "remaining_turns": budget.remaining,
    })

    def run_self_test_for(current_result: dict, candidate_id: int, attempt: int) -> SelfTestResult:
        if not self_test_enabled:
            return SelfTestResult("disabled", "Guided self-test is disabled for this run.")
        if self_test_mode != "execute":
            return SelfTestResult("guidance_only", "Public-spec TB guidance is injected into prompts but is not executed or used as an oracle.")
        if not current_result.get("sv_extracted") or not guide.testbench_sv:
            return SelfTestResult("not_run", "Self-test requires extracted SystemVerilog and a generated advisory TB.")
        return run_generated_self_test(
            prob_id=prob_id,
            run_dir=run_dir,
            verilog_sources=list(info.metadata.get("verilog_sources") or []),
            testbench_sv=guide.testbench_sv,
            candidate_id=candidate_id,
            attempt=attempt,
        )

    latest_self_test = run_self_test_for(result, tracker.candidate_id, 0)
    active_feedback = search.build_sim_feedback(
        prob_id=prob_id,
        result=result,
        iteration=0,
        history=search_history,
        run_dir=run_dir,
        info=info,
    )
    if self_test_enabled:
        self_test_history.append({
            "candidate_id": tracker.candidate_id,
            "attempt": 0,
            **latest_self_test.__dict__,
        })

    while (
        args.sim_feedback
        and result.get("sim_status") != "sim_pass"
        and not result.get("terminal_capability_error")
        and budget.remaining > 0
        and sim_feedback_iterations < max(0, args.sim_feedback_max_iters)
    ):
        sim_feedback_iterations += 1
        fresh_candidate = tracker.is_stagnant and tracker.can_restart
        attempt_limit = args.sim_feedback_turns_per_iter
        if attempt_limit is None:
            attempt_limit = args.max_turns
        turn_limit = budget.session_limit(attempt_limit)
        if turn_limit <= 0:
            break

        if fresh_candidate:
            previous_candidate = tracker.candidate_id
            previous_result = tracker.active_best_result or result
            tracker.restore_active(generated_target)
            generated_target.unlink(missing_ok=True)
            next_candidate = tracker.candidate_id + 1
            prompt = build_fresh_candidate_prompt(
                search=search,
                prob_id=prob_id,
                info=info,
                dataset_name=evaluator.dataset_name,
                has_repl=has_repl,
                candidate_id=next_candidate,
                guide_text=full_guide_text,
                prior_result=previous_result,
                recent_attempts=search_history,
                latest_self_test=latest_self_test,
            )
            phase = "fresh_candidate"
            role = f"ckt-generator-candidate-{next_candidate}"
            log_name = f"candidate_{next_candidate}_generate"
            search_history.append({
                "phase": "candidate_restart",
                "iteration": sim_feedback_iterations,
                "candidate_id": previous_candidate,
                "next_candidate_id": next_candidate,
                "note": (
                    f"Started a fresh candidate after {tracker.stagnation_count} non-improving attempts; "
                    "the previous candidate remains available as the global-best snapshot."
                ),
                "remaining_turns": budget.remaining,
            })
            current_feedback_for_attempt = ""
        else:
            tracker.restore_active(generated_target)
            current_code = generated_target.read_text(errors="replace") if generated_target.exists() else ""
            active_result = tracker.active_best_result or result
            feedback = active_feedback
            if full_guide_text:
                feedback += "\n\n" + full_guide_text
            advisory = self_test_feedback(latest_self_test) if self_test_enabled else ""
            if advisory:
                feedback += "\n\n" + advisory
            continuing_generation = not bool(active_result.get("compile_pass"))
            if not current_code.strip():
                repair_instruction = "Create the complete Lean source before checking it. "
            elif continuing_generation:
                repair_instruction = "Continue the Lean implementation and fix its compile diagnostics. "
            else:
                repair_instruction = "Use the evaluator diagnostics and the public-spec test plan/TB guidance to repair the Lean source. "
            prompt = search.build_compact_repair_prompt(
                prob_id=prob_id,
                info=info,
                dataset_name=evaluator.dataset_name,
                has_repl=has_repl,
                phase=("Lean generation/compile repair" if continuing_generation else "guided semantic repair"),
                iteration=sim_feedback_iterations,
                current_lean=current_code,
                latest_feedback=feedback,
                recent_attempts=search_history,
                extra_constraints=(
                    repair_instruction
                    + "Remain within the current candidate architecture unless a fresh-candidate restart is explicitly requested. "
                    + "Before ending, Lean-check the complete candidate including `#synthesizeVerilog` and require generated Verilog; the outer evaluator will rerun simulation."
                ),
            )
            phase = "guided_repair"
            role = f"ckt-repair-candidate-{tracker.candidate_id}"
            log_name = f"candidate_{tracker.candidate_id}_repair_{sim_feedback_iterations}"

        log_base = run_dir / "logs" / prob_id / log_name
        attempt_error: str | None = None
        attempt_t0 = time.monotonic()
        attempt_stats = AgentStats()
        try:
            attempt_runner = make_runner(
                args=args,
                prob_id=prob_id,
                role=role,
                log_base=log_base,
                skill=skill,
                info=info,
                repl=repl,
            )
            attempt_stats = attempt_runner.run(prompt, max_turns=turn_limit)
        except Exception as exc:
            attempt_error = f"{type(exc).__name__}: {exc}"
            agent_errors.append(f"{phase} {sim_feedback_iterations}: {attempt_error}")
            try:
                parsed_stats = parse_agent_log(Path(str(log_base) + ".jsonl"))
                if agent_stats_observed(parsed_stats):
                    attempt_stats = parsed_stats
            except Exception as parse_exc:
                attempt_error += (
                    "; agent log parsing also failed: "
                    f"{type(parse_exc).__name__}: {parse_exc}"
                )
                agent_errors[-1] = (
                    f"{phase} {sim_feedback_iterations}: {attempt_error}"
                )
        merge_agent_stats(repair_stats_total, attempt_stats)
        budget.consume(attempt_stats.turns)
        agent_elapsed += time.monotonic() - attempt_t0

        attempt_candidate_available = candidate_transaction.has_new_candidate()
        if not attempt_candidate_available and attempt_error is None:
            attempt_error = "Agent attempt did not create a non-empty Lean candidate."
            agent_errors.append(f"{phase} {sim_feedback_iterations}: {attempt_error}")

        attempt_eval_t0 = time.monotonic()
        if attempt_candidate_available:
            new_result = evaluate_candidate()
            if attempt_error:
                new_result["generation_error"] = attempt_error
        else:
            new_result = {
                "prob_id": prob_id,
                "compile_pass": False,
                "sv_extracted": False,
                "lint_pass": False,
                "sim_status": "not_run",
                "sim_mismatches": -1,
                "detail": attempt_error or "Agent attempt did not create a Lean candidate.",
            }
        eval_elapsed += time.monotonic() - attempt_eval_t0
        new_code = (
            generated_target.read_text(errors="replace")
            if attempt_candidate_available
            else None
        )

        if fresh_candidate:
            observation = tracker.start_candidate(
                new_result,
                new_code,
                reason=f"stagnation restart at outer iteration {sim_feedback_iterations}",
            )
        else:
            observation = tracker.observe(
                new_result,
                new_code,
                reason=f"guided repair at outer iteration {sim_feedback_iterations}",
            )
            if not observation.accepted:
                tracker.restore_active(generated_target)

        result = tracker.active_best_result or new_result
        if observation.accepted or fresh_candidate:
            latest_self_test = run_self_test_for(
                new_result,
                tracker.candidate_id,
                observation.attempt,
            )
            active_feedback = search.build_sim_feedback(
                prob_id=prob_id,
                result=new_result,
                iteration=sim_feedback_iterations,
                history=search_history,
                run_dir=run_dir,
                info=info,
            )
            if self_test_enabled:
                self_test_history.append({
                    "candidate_id": tracker.candidate_id,
                    "attempt": observation.attempt,
                    **latest_self_test.__dict__,
                })
        recorded_self_test_status = (
            "disabled"
            if not self_test_enabled
            else latest_self_test.status if observation.accepted or fresh_candidate else "not_run_rejected"
        )
        history_row = {
            "phase": phase,
            "iteration": sim_feedback_iterations,
            **observation.__dict__,
            "result_summary": search.summarize_eval_result(new_result),
            "repair_turns": attempt_stats.turns,
            "repair_turn_limit": turn_limit,
            "remaining_turns": budget.remaining,
            "repair_input_tokens": attempt_stats.input_tokens,
            "repair_output_tokens": attempt_stats.output_tokens,
            "repair_compile_checks": attempt_stats.compile_checks,
            "self_test_status": recorded_self_test_status,
        }
        if attempt_error:
            history_row["agent_error"] = attempt_error
        search_history.append(history_row)
        append_jsonl(run_dir / "events.jsonl", {
            "prob_id": prob_id,
            "event": phase,
            "iteration": sim_feedback_iterations,
            "candidate_id": observation.candidate_id,
            "sim_status": new_result.get("sim_status"),
            "compile_pass": new_result.get("compile_pass"),
            "remaining_turns": budget.remaining,
            "improved_candidate": observation.improved_candidate,
            "improved_global": observation.improved_global,
            "stagnation_count": observation.stagnation_count,
            "self_test_status": recorded_self_test_status,
        })

        if result.get("sim_status") == "sim_pass":
            sim_feedback_success = True
            break

    tracker.restore_global(generated_target)
    if tracker.global_best_result is not None:
        result = tracker.global_best_result

    if not candidate_transaction.has_new_candidate():
        terminal_generation_error = (
            generation_error or "Guided search did not create a non-empty Lean candidate."
        )
        result["agent_error"] = terminal_generation_error
        result["generation_error"] = terminal_generation_error
        result["detail"] = terminal_generation_error
        result["sim_status"] = "agent_error"
        try:
            preexisting_generated_restored = candidate_transaction.restore_preexisting()
        except Exception as exc:
            preexisting_generated_restore_error = f"{type(exc).__name__}: {exc}"
            result["agent_error"] += (
                "; failed to restore preexisting Generated candidate: "
                f"{preexisting_generated_restore_error}"
            )
            result["generation_error"] = result["agent_error"]
            result["detail"] = result["agent_error"]

    all_stats = AgentStats()
    merge_agent_stats(all_stats, planner_stats)
    merge_agent_stats(all_stats, generation_stats)
    merge_agent_stats(all_stats, repair_stats_total)
    record = {
        "prob_id": prob_id,
        "agent_turns": generation_stats.turns,
        "agent_input_tokens": all_stats.input_tokens,
        "agent_output_tokens": all_stats.output_tokens,
        "agent_compile_checks": all_stats.compile_checks,
        "agent_tool_counts": all_stats.tool_counts,
        "agent_budget_exhausted": all_stats.budget_exhausted,
        "agent_usage_accounting_complete": all_stats.usage_accounting_complete,
        "agent_usage_accounting_notes": all_stats.usage_accounting_notes,
        "agent_turn_budget": args.max_turns,
        "prompt_profile": args.prompt_profile,
        "agent_generation_turns": generation_stats.turns,
        "agent_turns_total": all_stats.turns,
        "search_total_turn_budget": budget.total,
        "guided_self_test_mode": self_test_mode,
        "search_turns_remaining": budget.remaining,
        "guided_search_enabled": True,
        "guided_self_test_enabled": self_test_enabled,
        "self_test_planner_turns": planner_stats.turns,
        "self_test_planner_input_tokens": planner_stats.input_tokens,
        "self_test_planner_output_tokens": planner_stats.output_tokens,
        "self_test_planner_error": planner_error,
        "self_test_validation_error": self_test_validation_error,
        "self_test_generated": bool(guide.testbench_sv),
        "self_test_history": self_test_history,
        "candidate_count": tracker.candidate_id,
        "candidate_max": tracker.max_candidates,
        "candidate_stagnation_patience": tracker.patience,
        "candidate_search_history": search_history,
        "agent_errors": agent_errors,
        "sim_feedback_enabled": bool(args.sim_feedback),
        "sim_feedback_iterations": sim_feedback_iterations,
        "sim_feedback_success": sim_feedback_success,
        "sim_feedback_turn_budget": max(0, budget.total - planner_stats.turns - generation_stats.turns),
        "sim_feedback_turns_remaining": budget.remaining,
        "agent_elapsed_seconds": rounded_agent_elapsed(agent_elapsed),
        "eval_elapsed_seconds": round(eval_elapsed, 3),
        "elapsed_seconds": round(time.monotonic() - problem_t0, 3),
        "harness": args.harness,
        "model": model_alias(args.model),
        "timestamp": datetime.now().isoformat(),
    }
    if preexisting_generated_backup:
        record["preexisting_generated_backup"] = preexisting_generated_backup
    if preexisting_generated_restored:
        record["preexisting_generated_restored"] = True
    if preexisting_generated_restore_error:
        record["preexisting_generated_restore_error"] = preexisting_generated_restore_error
    try:
        record.update(search.classify_failure_record(result))
    except Exception:
        pass
    record.update(result)
    append_jsonl(run_dir / "results.jsonl", record)
    return record


def _process_problem_standard(
    prob_id: str,
    *,
    args: argparse.Namespace,
    ds: Any,
    evaluator: Any,
    run_dir: Path,
    skill: str,
    repl: Any | None,
    candidate_transaction: GeneratedCandidateTransaction | None,
) -> dict[str, Any]:
    _add_legacy_agent_path()
    import search

    problem_t0 = time.monotonic()
    info = ds.load_problem(prob_id)
    local_guardrails = bool(getattr(args, "cvdp_local_guardrails", False))
    verified_idioms = bool(getattr(args, "cvdp_verified_idioms", False))
    has_repl = repl is not None
    validate_cvdp_verified_idiom_mode(args, active_repl=has_repl)
    generated_target = (
        candidate_transaction.target
        if candidate_transaction is not None
        else PROJECT_ROOT / "Generated" / f"{prob_id}.lean"
    )
    benchmark_port_resolver = getattr(search, "_benchmark_expected_ports", None)
    benchmark_ports = (
        benchmark_port_resolver(info) if benchmark_port_resolver is not None else None
    )

    def evaluate_candidate() -> dict[str, Any]:
        source = (
            generated_target.read_text(errors="replace")
            if generated_target.exists()
            else ""
        )
        if (
            local_guardrails
            and scaffold_code is not None
            and scaffold_result is not None
            and source == scaffold_code
        ):
            return dict(scaffold_result)
        if benchmark_ports is None:
            evaluated = evaluator.evaluate(prob_id, run_dir)
        else:
            evaluated = evaluator.evaluate(
                prob_id, run_dir, benchmark_ports=benchmark_ports
            )
        if not local_guardrails:
            return evaluated
        return reject_incomplete_cvdp_scaffold(evaluated, source)

    agent_stats = AgentStats()
    repair_stats_total = AgentStats()
    agent_elapsed = 0.0
    agent_error: str | None = None
    preexisting_generated_backup = (
        candidate_transaction.backup if candidate_transaction is not None else None
    )
    preexisting_generated_restored = False
    preexisting_generated_restore_error: str | None = None
    generated_candidate_from_agent = False
    sim_feedback_history: list[dict[str, Any]] = []
    sim_feedback_turn_budget = 0
    sim_feedback_turns_remaining = 0
    sim_feedback_success = False
    sim_feedback_iterations = 0
    scaffold_code: str | None = None
    scaffold_result: dict[str, Any] | None = None
    scaffold_sha256: str | None = None
    scaffold_preflight_pass = False
    idiom_catalog_sha256: str | None = None
    idiom_initial_selected_ids: list[str] | None = None
    idiom_initial_features: dict[str, Any] | None = None
    idiom_initial_rendered_chars: int | None = None
    idiom_repair_selections: list[dict[str, Any]] = []

    if verified_idioms:
        idiom_query = search.build_cvdp_idiom_query(info)
        idiom_catalog_sha256 = search.cvdp_verified_idiom_catalog_sha256()
        idiom_initial_selected_ids = list(
            search.cvdp_verified_idiom_ids(info)
        )
        idiom_initial_features = dict(vars(idiom_query))
        idiom_initial_rendered_chars = len(
            search.format_cvdp_verified_idioms(info)
        )
        append_jsonl(run_dir / "events.jsonl", {
            "prob_id": prob_id,
            "event": "cvdp_verified_idioms_initial",
            "cvdp_verified_idiom_catalog_sha256": idiom_catalog_sha256,
            "cvdp_verified_idiom_initial_selected_ids": idiom_initial_selected_ids,
            "cvdp_verified_idiom_initial_features": idiom_initial_features,
            "cvdp_verified_idiom_initial_rendered_chars": idiom_initial_rendered_chars,
        })

    if local_guardrails and not args.eval_only:
        if repl is None:
            raise RuntimeError("--cvdp-local-guardrails requires an active Lean REPL")
        scaffold_code = search.build_cvdp_typed_scaffold(info)
        if not scaffold_code.strip():
            raise RuntimeError(
                f"Could not derive a typed CVDP scaffold for {prob_id} from the public interface"
            )
        scaffold_dir = run_dir / "scaffolds"
        scaffold_dir.mkdir(parents=True, exist_ok=True)
        scaffold_archive = scaffold_dir / f"{prob_id}.lean"
        scaffold_archive.write_text(scaffold_code, encoding="utf-8")
        scaffold_check = repl.check_file(scaffold_archive)
        scaffold_verilog = str(getattr(scaffold_check, "verilog", "") or "")
        if not (
            bool(getattr(scaffold_check, "passed", False))
            and bool(getattr(scaffold_check, "complete", False))
            and scaffold_verilog.strip()
        ):
            error_text = str(getattr(scaffold_check, "error_text", "") or "")
            raise RuntimeError(
                f"Typed CVDP scaffold preflight failed for {prob_id}: "
                + (error_text[:1000] or "no generated Verilog")
            )
        scaffold_preflight_pass = True
        scaffold_sha256 = hashlib.sha256(scaffold_code.encode("utf-8")).hexdigest()
        generated_target.parent.mkdir(parents=True, exist_ok=True)
        generated_target.write_text(scaffold_code, encoding="utf-8")
        scaffold_result = {
            "prob_id": prob_id,
            "compile_pass": True,
            "sv_extracted": True,
            "lint_pass": False,
            "sim_status": "not_run",
            "sim_mismatches": -1,
            "has_sorry": False,
            "lean_source_status": "complete",
            "scaffold_incomplete": True,
            "detail": "Compile-checked typed scaffold fallback; behavioral TODOs remain.",
        }
        append_jsonl(run_dir / "events.jsonl", {
            "prob_id": prob_id,
            "event": "cvdp_scaffold_preflight",
            "compile_pass": True,
            "sv_extracted": True,
            "scaffold_sha256": scaffold_sha256,
        })

    if not args.eval_only:
        log_base = run_dir / "logs" / prob_id / "generate"
        t0 = time.monotonic()
        try:
            user_message = search.build_user_message(
                prob_id,
                has_repl=has_repl,
                info=info,
                dataset_name=evaluator.dataset_name,
                condition_sv=None,
                include_cvdp_scaffold=local_guardrails,
                include_cvdp_verified_idioms=verified_idioms,
            )
            runner = make_runner(
                args=args,
                prob_id=prob_id,
                role="ckt-generator",
                log_base=log_base,
                skill=skill,
                info=info,
                repl=repl,
                cvdp_verified_idioms=verified_idioms,
            )
            agent_stats = runner.run(user_message, max_turns=args.max_turns)
        except Exception as exc:
            agent_error = f"{type(exc).__name__}: {exc}"
            try:
                parsed_stats = parse_agent_log(Path(str(log_base) + ".jsonl"))
                if agent_stats_observed(parsed_stats):
                    agent_stats = parsed_stats
            except Exception as parse_exc:
                agent_error += (
                    "; agent log parsing also failed: "
                    f"{type(parse_exc).__name__}: {parse_exc}"
                )
        finally:
            agent_elapsed += time.monotonic() - t0

        generated_candidate_from_agent = (
            candidate_transaction.has_new_candidate()
            if candidate_transaction is not None
            else generated_candidate_available(generated_target)
        )
        if not generated_candidate_from_agent:
            if local_guardrails and scaffold_code is not None:
                generated_target.write_text(scaffold_code, encoding="utf-8")
                generated_candidate_from_agent = True
                sim_feedback_history.append({
                    "phase": "generation",
                    "iteration": 0,
                    "note": "Initial agent emptied the target; restored the compile-checked scaffold.",
                })
        if not generated_candidate_from_agent:
            if agent_error is None:
                agent_error = (
                    "Agent returned without creating a non-empty "
                    f"Generated/{prob_id}.lean candidate"
                )
            try:
                preexisting_generated_restored = bool(
                    candidate_transaction
                    and candidate_transaction.restore_preexisting()
                )
            except Exception as exc:
                preexisting_generated_restore_error = f"{type(exc).__name__}: {exc}"
                agent_error = (
                    f"{agent_error}; failed to restore preexisting Generated candidate: "
                    f"{preexisting_generated_restore_error}"
                )

    eval_t0 = time.monotonic()
    if agent_error and generated_candidate_from_agent:
        result = evaluate_candidate()
        result["agent_error"] = agent_error
        if result.get("detail"):
            result["detail"] = f"Agent ended with {agent_error}; evaluated generated file anyway.\n{result['detail']}"
        else:
            result["detail"] = f"Agent ended with {agent_error}; evaluated generated file anyway."
    elif agent_error:
        result = {
            "prob_id": prob_id,
            "compile_pass": False,
            "sv_extracted": False,
            "lint_pass": False,
            "sim_status": "agent_error",
            "sim_mismatches": -1,
            "detail": agent_error,
            "agent_error": agent_error,
        }
    else:
        result = evaluate_candidate()
    eval_elapsed = time.monotonic() - eval_t0

    feedback_result = result
    feedback_from_rolled_back_candidate = False
    if (
        local_guardrails
        and scaffold_code is not None
        and scaffold_result is not None
        and result.get("sim_status") != "sim_pass"
        and search.eval_progress_key(scaffold_result) > search.eval_progress_key(result)
    ):
        generated_target.write_text(scaffold_code, encoding="utf-8")
        result = scaffold_result
        feedback_from_rolled_back_candidate = True
        append_jsonl(run_dir / "events.jsonl", {
            "prob_id": prob_id,
            "event": "cvdp_scaffold_rollback",
            "reason": "initial candidate ranked below compile-checked scaffold",
            "rejected_result_summary": search.summarize_eval_result(feedback_result),
        })

    if (
        args.sim_feedback
        and not args.eval_only
        and not agent_error
        and result.get("sim_status") != "sim_pass"
        and not result.get("terminal_capability_error")
    ):
        best_result = result
        best_code = generated_target.read_text(errors="replace") if generated_target.exists() else None
        if args.sim_feedback_turn_budget is None:
            sim_feedback_turn_budget = max(0, args.max_turns - agent_stats.turns)
        else:
            sim_feedback_turn_budget = max(0, args.sim_feedback_turn_budget)
        sim_feedback_turns_remaining = sim_feedback_turn_budget
        non_improving_repairs = 0
        sim_iter = 0
        while (
            result.get("sim_status") != "sim_pass"
            and not result.get("terminal_capability_error")
            and sim_feedback_turns_remaining > 0
            and sim_iter < max(0, args.sim_feedback_max_iters)
        ):
            sim_iter += 1
            sim_feedback_iterations = sim_iter
            current_code = generated_target.read_text(errors="replace") if generated_target.exists() else ""
            feedback = search.build_sim_feedback(
                prob_id=prob_id,
                result=feedback_result,
                iteration=sim_iter - 1,
                history=sim_feedback_history,
                run_dir=run_dir,
                info=info,
            )
            repair_idiom_fields: dict[str, Any] = {}
            if verified_idioms:
                repair_idiom_ids = list(
                    search.cvdp_verified_idiom_ids(
                        info, feedback=feedback, repair=True
                    )
                )
                repair_idiom_rendered_chars = len(
                    search.format_cvdp_verified_idioms(
                        info, feedback=feedback, repair=True
                    )
                )
                repair_idiom_fields = {
                    "cvdp_verified_idiom_selected_id": (
                        repair_idiom_ids[0] if repair_idiom_ids else None
                    ),
                    "cvdp_verified_idiom_selected_ids": repair_idiom_ids,
                    "cvdp_verified_idiom_rendered_chars": repair_idiom_rendered_chars,
                }
                repair_idiom_selection = {
                    "iteration": sim_iter,
                    **repair_idiom_fields,
                }
                idiom_repair_selections.append(repair_idiom_selection)
                append_jsonl(run_dir / "events.jsonl", {
                    "prob_id": prob_id,
                    "event": "cvdp_verified_idioms_repair_selection",
                    **repair_idiom_selection,
                })
            scaffold_incomplete = bool(feedback_result.get("scaffold_incomplete"))
            continuing_generation = (
                not bool(feedback_result.get("compile_pass"))
                or scaffold_incomplete
            )
            if not current_code.strip():
                repair_instruction = (
                    "No Lean candidate exists yet. Create the complete Lean source before checking it. "
                )
            elif scaffold_incomplete:
                repair_instruction = (
                    "Implement the typed scaffold TODO behavior; compilation of the marked zero scaffold is not completion. "
                )
            elif continuing_generation:
                repair_instruction = (
                    "Continue generating the Lean source and use the compile diagnostics to make it compile. "
                )
            else:
                repair_instruction = "Use the Verilog simulation diagnostics to repair the Lean source. "
            if feedback_from_rolled_back_candidate:
                repair_instruction += (
                    "The current file has been rolled back to the compile-safe best candidate. "
                    "The latest diagnostics below came from the rejected edit; reapply only "
                    "its corrected changes to the current file. "
                )
            compact_prompt = search.build_compact_repair_prompt(
                prob_id=prob_id,
                info=info,
                dataset_name=evaluator.dataset_name,
                has_repl=has_repl,
                phase=("Lean generation/compile repair" if continuing_generation else "RTL simulation feedback"),
                iteration=sim_iter,
                current_lean=current_code,
                latest_feedback=feedback,
                recent_attempts=sim_feedback_history,
                extra_constraints=repair_instruction + (
                    "Before ending, ensure the final Lean file compiles; "
                    "the outer evaluator will rerun RTL simulation."
                ),
                include_cvdp_scaffold=local_guardrails,
                include_cvdp_verified_idioms=verified_idioms,
            )
            repair_log_base = run_dir / "logs" / prob_id / f"sim_feedback_iter_{sim_iter}"
            repair_runner = make_runner(
                args=args,
                prob_id=prob_id,
                role="ckt-sim-repair",
                log_base=repair_log_base,
                skill=skill,
                cvdp_verified_idioms=verified_idioms,
                info=info,
                repl=repl,
            )
            repair_t0 = time.monotonic()
            try:
                repair_turn_limit = sim_feedback_turns_remaining
                if args.sim_feedback_turns_per_iter is not None:
                    repair_turn_limit = min(repair_turn_limit, max(0, args.sim_feedback_turns_per_iter))
                if repair_turn_limit <= 0:
                    break
                repair_stats = repair_runner.run(compact_prompt, max_turns=repair_turn_limit)
                merge_agent_stats(repair_stats_total, repair_stats)
                sim_feedback_turns_remaining = max(0, sim_feedback_turns_remaining - repair_stats.turns)
            except Exception as exc:
                sim_feedback_history.append({
                    "phase": "sim_feedback",
                    "iteration": sim_iter,
                    "note": f"Agent error during simulation repair: {type(exc).__name__}: {exc}",
                    "remaining_turns": sim_feedback_turns_remaining,
                    **repair_idiom_fields,
                })
                break
            finally:
                agent_elapsed += time.monotonic() - repair_t0

            repair_candidate_available = (
                candidate_transaction.has_new_candidate()
                if candidate_transaction is not None
                else generated_candidate_available(generated_target)
            )
            if not repair_candidate_available:
                if local_guardrails and best_code is not None:
                    generated_target.write_text(best_code, encoding="utf-8")
                    feedback_result = {
                        "prob_id": prob_id,
                        "compile_pass": False,
                        "sv_extracted": False,
                        "lint_pass": False,
                        "sim_status": "not_run",
                        "sim_mismatches": -1,
                        "detail": "Repair agent emptied the target; the compile-safe best was restored.",
                    }
                    feedback_from_rolled_back_candidate = True
                    result = best_result
                    if verified_idioms:
                        sim_feedback_history.append({
                            "phase": "sim_feedback",
                            "iteration": sim_iter,
                            "note": "Repair agent emptied the target; restored the compile-safe best.",
                            **repair_idiom_fields,
                        })
                    continue
                sim_feedback_history.append({
                    "phase": "sim_feedback",
                    "iteration": sim_iter,
                    "note": "Agent returned without a non-empty repair candidate; restored the prior best without evaluating the empty artifact.",
                    "remaining_turns": sim_feedback_turns_remaining,
                    **repair_idiom_fields,
                })
                if best_code is not None:
                    generated_target.write_text(best_code, encoding="utf-8")
                result = best_result
                break

            repair_eval_t0 = time.monotonic()
            new_result = evaluate_candidate()
            feedback_result = new_result
            eval_elapsed += time.monotonic() - repair_eval_t0
            new_key = search.eval_progress_key(new_result)
            best_key = search.eval_progress_key(best_result)
            candidate_exists = repair_candidate_available
            improved = new_key > best_key
            generation_incomplete = not bool(best_result.get("compile_pass"))
            if local_guardrails:
                accepted = candidate_exists and new_key >= best_key
            else:
                accepted = candidate_exists and (
                    generation_incomplete or new_key >= best_key
                )
            sim_feedback_history.append({
                "phase": "sim_feedback",
                "iteration": sim_iter,
                "note": "Candidate result after RTL simulation feedback repair.",
                "result_summary": search.summarize_eval_result(new_result),
                "improved_best": improved,
                "accepted_candidate": accepted,
                "rolled_back_to_best": not accepted,
                "repair_turns": repair_stats.turns,
                "repair_turn_limit": repair_turn_limit,
                "remaining_turns": sim_feedback_turns_remaining,
                "repair_input_tokens": repair_stats.input_tokens,
                "repair_output_tokens": repair_stats.output_tokens,
                "repair_compile_checks": repair_stats.compile_checks,
                **repair_idiom_fields,
            })
            append_jsonl(run_dir / "events.jsonl", {
                "prob_id": prob_id,
                "event": "sim_feedback",
                "iteration": sim_iter,
                "sim_status": new_result.get("sim_status"),
                "compile_pass": new_result.get("compile_pass"),
                "lint_pass": new_result.get("lint_pass"),
                "remaining_turns": sim_feedback_turns_remaining,
                "improved_best": improved,
                "accepted_candidate": accepted,
            })

            if accepted:
                best_result = new_result
                best_code = generated_target.read_text(errors="replace") if candidate_exists else None
                result = new_result
                feedback_from_rolled_back_candidate = False
                non_improving_repairs = 0 if improved else non_improving_repairs + 1
            else:
                if best_code is not None:
                    generated_target.write_text(best_code, encoding="utf-8")
                result = best_result
                feedback_from_rolled_back_candidate = True
                if local_guardrails and not new_result.get("compile_pass"):
                    # Existing max-iteration and turn budgets still bound compile repair.
                    pass
                else:
                    non_improving_repairs += 1

            if result.get("sim_status") == "sim_pass":
                sim_feedback_success = True
                break
            if args.sim_feedback_patience > 0 and non_improving_repairs >= args.sim_feedback_patience:
                sim_feedback_history.append({
                    "phase": "sim_feedback",
                    "iteration": sim_iter,
                    "note": f"Stopped: {args.sim_feedback_patience} consecutive simulation-feedback repairs did not improve the best candidate.",
                    "best_result_summary": search.summarize_eval_result(best_result),
                })
                break

        if best_code is not None and search.eval_progress_key(best_result) >= search.eval_progress_key(result):
            generated_target.write_text(best_code, encoding="utf-8")
            result = best_result

    all_stats = AgentStats()
    merge_agent_stats(all_stats, agent_stats)
    merge_agent_stats(all_stats, repair_stats_total)
    record = {
        "prob_id": prob_id,
        "agent_turns": agent_stats.turns,
        "agent_input_tokens": all_stats.input_tokens,
        "agent_output_tokens": all_stats.output_tokens,
        "agent_compile_checks": all_stats.compile_checks,
        "agent_tool_counts": all_stats.tool_counts,
        "agent_budget_exhausted": all_stats.budget_exhausted,
        "agent_usage_accounting_complete": all_stats.usage_accounting_complete,
        "agent_usage_accounting_notes": all_stats.usage_accounting_notes,
        "agent_turn_budget": args.max_turns,
        "prompt_profile": args.prompt_profile,
        "cvdp_local_guardrails": local_guardrails,
        "scaffold_preflight_pass": scaffold_preflight_pass,
        "scaffold_sha256": scaffold_sha256,
        "agent_generation_turns": agent_stats.turns,
        "agent_turns_total": all_stats.turns,
        "sim_feedback_enabled": bool(args.sim_feedback),
        "sim_feedback_iterations": sim_feedback_iterations,
        "sim_feedback_success": sim_feedback_success,
        "sim_feedback_turn_budget": sim_feedback_turn_budget,
        "sim_feedback_turns_remaining": sim_feedback_turns_remaining,
        "sim_feedback_history": sim_feedback_history,
        "agent_elapsed_seconds": rounded_agent_elapsed(agent_elapsed),
        "eval_elapsed_seconds": round(eval_elapsed, 3),
        "elapsed_seconds": round(time.monotonic() - problem_t0, 3),
        "harness": args.harness,
        "model": model_alias(args.model),
        "timestamp": datetime.now().isoformat(),
    }
    if verified_idioms:
        record.update({
            "cvdp_verified_idioms": True,
            "cvdp_verified_idiom_catalog_sha256": idiom_catalog_sha256,
            "cvdp_verified_idiom_initial_selected_ids": idiom_initial_selected_ids,
            "cvdp_verified_idiom_initial_features": idiom_initial_features,
            "cvdp_verified_idiom_initial_rendered_chars": idiom_initial_rendered_chars,
            "cvdp_verified_idiom_repair_selections": idiom_repair_selections,
        })
    if preexisting_generated_backup:
        record["preexisting_generated_backup"] = preexisting_generated_backup
    if preexisting_generated_restored:
        record["preexisting_generated_restored"] = True
    if preexisting_generated_restore_error:
        record["preexisting_generated_restore_error"] = preexisting_generated_restore_error
    try:
        record.update(search.classify_failure_record(result))
    except Exception:
        pass
    record.update(result)
    append_jsonl(run_dir / "results.jsonl", record)
    return record


def process_problem(
    prob_id: str,
    *,
    args: argparse.Namespace,
    ds: Any,
    evaluator: Any,
    run_dir: Path,
    skill: str,
    repl: Any | None,
) -> dict[str, Any]:
    """Process one problem under a single Generated-candidate transaction."""

    if args.eval_only:
        with GeneratedCandidateTransaction(
            PROJECT_ROOT, run_dir, prob_id, read_only=True
        ) as transaction:
            return _process_problem_standard(
                prob_id,
                args=args,
                ds=ds,
                evaluator=evaluator,
                run_dir=run_dir,
                skill=skill,
                repl=repl,
                candidate_transaction=transaction,
            )

    with GeneratedCandidateTransaction(PROJECT_ROOT, run_dir, prob_id) as transaction:
        if getattr(args, "guided_search", False):
            return process_problem_guided(
                prob_id,
                args=args,
                ds=ds,
                evaluator=evaluator,
                run_dir=run_dir,
                skill=skill,
                repl=repl,
                candidate_transaction=transaction,
            )
        return _process_problem_standard(
            prob_id,
            args=args,
            ds=ds,
            evaluator=evaluator,
            run_dir=run_dir,
            skill=skill,
            repl=repl,
            candidate_transaction=transaction,
        )


def main() -> None:
    args = parse_args()
    load_env_file(Path(args.key_env))
    # harness.py is imported before CLI env loading. Capture credentials into
    # its private client configuration, then remove them from subprocess envs.
    configure_anthropic_credentials_from_env()
    ensure_runtime_env()
    local_guardrails = bool(getattr(args, "cvdp_local_guardrails", False))
    verified_idioms = bool(getattr(args, "cvdp_verified_idioms", False))
    if verified_idioms:
        try:
            validate_cvdp_verified_idiom_mode(args)
        except ValueError as exc:
            raise SystemExit(str(exc)) from None
    if local_guardrails:
        if args.dataset != "cvdp":
            raise SystemExit("--cvdp-local-guardrails is supported only for --dataset cvdp")
        if args.harness != "anthropic-api":
            raise SystemExit(
                "--cvdp-local-guardrails currently requires --harness anthropic-api"
            )
        if args.eval_only:
            raise SystemExit("--cvdp-local-guardrails cannot be combined with --eval-only")
        if getattr(args, "guided_search", False):
            raise SystemExit(
                "--cvdp-local-guardrails currently supports the standard feedback loop, not --guided-search"
            )
        if args.no_repl:
            raise SystemExit("--cvdp-local-guardrails requires the Lean REPL; remove --no-repl")
    _add_legacy_agent_path()
    from dataset import Dataset
    from evaluator import Evaluator
    import search

    ds = Dataset(args.dataset, project_root=PROJECT_ROOT)
    problems = discover_problems(args, ds)
    if not problems:
        raise SystemExit("No problems selected")

    results_base = Path(args.results_dir).resolve()
    run_dir = results_base / f"cktarchon_run_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    run_dir.mkdir(parents=True, exist_ok=True)
    (PROJECT_ROOT / "Generated").mkdir(exist_ok=True)

    num_workers = max(1, args.workers)
    print(f"[cktarchon] problems={len(problems)} model={model_alias(args.model)} harness={args.harness} workers={num_workers} run_dir={run_dir}")

    if not args.no_repl:
        try:
            from lean_repl import LeanREPLPool
            pool = LeanREPLPool(size=num_workers, project_dir=PROJECT_ROOT)
        except Exception as exc:
            print(f"[cktarchon] Lean REPL unavailable, falling back to lake build: {exc}")
            pool = None
    else:
        pool = None

    if verified_idioms:
        try:
            validate_cvdp_verified_idiom_mode(args, active_repl=pool is not None)
        except ValueError as exc:
            raise SystemExit(str(exc)) from None
    if local_guardrails and pool is None:
        raise SystemExit(
            "--cvdp-local-guardrails failed closed because the Lean REPL is unavailable"
        )

    skill = search.load_skill()
    summary = {"total": len(problems), "skipped": 0, "compile_pass": 0, "sim_pass": 0, "sim_fail": 0, "sim_error": 0, "agent_error": 0, "sim_feedback_attempts": 0, "sim_feedback_success": 0}
    summary_lock = Lock()

    indexed_problems: list[tuple[int, str]] = []
    for idx, prob_id in enumerate(problems, 1):
        if args.resume and already_done(results_base, prob_id, args.resume_mode):
            summary["skipped"] += 1
            print(f"[{idx}/{len(problems)}] skip {prob_id}")
        else:
            indexed_problems.append((idx, prob_id))

    def _run_one(idx: int, prob_id: str) -> tuple[int, str, dict[str, Any]]:
        repl = None
        if pool is not None:
            repl = pool.acquire()
        try:
            evaluator = Evaluator(project_root=PROJECT_ROOT, dataset=args.dataset, dataset_obj=ds, lean_repl=repl)
            print(f"[{idx}/{len(problems)}] run {prob_id}")
            record = process_problem(prob_id, args=args, ds=ds, evaluator=evaluator, run_dir=run_dir, skill=skill, repl=repl)
            return idx, prob_id, record
        finally:
            if pool is not None and repl is not None:
                pool.release(repl)

    try:
        with ThreadPoolExecutor(max_workers=num_workers) as executor:
            future_map = {executor.submit(_run_one, idx, prob_id): (idx, prob_id) for idx, prob_id in indexed_problems}
            for future in as_completed(future_map):
                idx, prob_id = future_map[future]
                try:
                    _, _, record = future.result()
                except Exception as exc:
                    record = {
                        "prob_id": prob_id,
                        "compile_pass": False,
                        "lint_pass": False,
                        "sim_status": "agent_error",
                        "agent_error": f"{type(exc).__name__}: {exc}",
                    }
                    append_jsonl(run_dir / "results.jsonl", record)
                with summary_lock:
                    accumulate_summary_record(summary, record)
                print(f"[{idx}/{len(problems)}] done {prob_id}: compile={record.get('compile_pass')} lint={record.get('lint_pass')} sim={record.get('sim_status')} turns={record.get('agent_turns_total')} tok={record.get('agent_input_tokens')}+{record.get('agent_output_tokens')}")
    finally:
        if pool is not None:
            pool.close_all()

    summary.update({"dataset": args.dataset, "model": model_alias(args.model), "harness": args.harness, "run_dir": str(run_dir)})
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
