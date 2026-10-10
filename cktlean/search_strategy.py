from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


ProgressKey = tuple[Any, ...]
ProgressKeyFn = Callable[[dict | None], ProgressKey]


SELF_TEST_TOP = "cktlean_self_test"


@dataclass(frozen=True)
class SelfTestGuide:
    test_plan: str
    testbench_sv: str
    raw_response: str = ""


@dataclass(frozen=True)
class SelfTestResult:
    status: str
    detail: str
    compile_returncode: int | None = None
    run_returncode: int | None = None


def _runtime_tool(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    sibling = Path(sys.executable).resolve().parent / name
    if sibling.exists() and sibling.is_file():
        return str(sibling)
    raw_sibling = Path(sys.executable).parent / name
    if raw_sibling.exists() and raw_sibling.is_file():
        return str(raw_sibling)
    return None


@dataclass
class TurnBudget:
    """One turn ledger shared by planning, generation, and repair sessions."""

    total: int
    used: int = 0

    def __post_init__(self) -> None:
        self.total = max(0, int(self.total))
        self.used = max(0, int(self.used))

    @property
    def remaining(self) -> int:
        return max(0, self.total - self.used)

    def session_limit(self, requested: int | None) -> int:
        if requested is None:
            return self.remaining
        return min(self.remaining, max(0, int(requested)))

    def consume(self, turns: int) -> None:
        self.used += max(0, int(turns))


def build_self_test_planner_prompt(
    *,
    prob_id: str,
    design_name: str,
    spec: str,
    interface_contract: str,
    public_context: str,
) -> str:
    """Build a planner prompt from public task material only."""

    return f"""## Public-Spec Self-Test Planning Task

Create independent verification guidance for `{prob_id}` / module `{design_name}` using only the material in this message. You do not have, and must not infer or search for, the benchmark's hidden testbench.

The natural-language task defines the desired functional behavior and is the semantic oracle. In repair/debugging tasks, supplied RTL or tables may intentionally show the current buggy behavior: use them to diagnose the defect, but the self-test must pass only the requested corrected behavior. Never make reproduction of known buggy, erroneous, observed, or pre-fix behavior a pass condition.

The Public Interface Contract is normalized benchmark integration metadata and is authoritative only for module name, ports, widths, listed parameter values, reset polarity, and clock/reset wiring. If prose conflicts with that interface metadata, follow the Public Interface Contract for those interface facts and explicitly note the conflict. Do not invent parameter values or sweeps: when the contract names parameters but lists no observed sweep values, use only values explicitly declared in the public task/context.

### Natural-Language Specification

{spec}

### Public Interface Contract

{interface_contract or '(no additional contract available)'}

### Public Context Files

{public_context or '(none)'}

Return exactly these two XML-style sections:

<test_plan>
A concise behavioral plan covering normal cases, boundary values, reset polarity, clock-cycle latency, state transitions, and every listed parameter setting. State expected values precisely enough to guide an RTL implementation.
</test_plan>

<testbench_sv>
A complete standalone SystemVerilog self-test whose top module is `{SELF_TEST_TOP}` and which instantiates `{design_name}` exactly according to the public interface contract. For sequential checks, avoid races with nonblocking assignments: sample after the NBA update (for example, add a small delay after `@(posedge clock)` or check on the following `negedge clock`). Print the first failure with cycle/time, inputs, expected output, and actual output. Print `CKTLEAN_SELF_TEST_PASS` on success or `CKTLEAN_SELF_TEST_FAIL` on failure. Keep the test deterministic and bounded. Do not include a replacement DUT implementation.
</testbench_sv>

The generated testbench is advisory and will be validated separately. Do not wrap either section in Markdown fences.
"""


def parse_self_test_guide(response_text: str) -> SelfTestGuide:
    text = str(response_text or "")

    def section(name: str) -> str:
        match = re.search(
            rf"<{name}>\s*(.*?)\s*</{name}>",
            text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if not match:
            return ""
        body = match.group(1).strip()
        body = re.sub(r"^```(?:systemverilog|verilog|text)?\s*", "", body, flags=re.IGNORECASE)
        body = re.sub(r"\s*```$", "", body)
        return body.strip()

    test_plan = section("test_plan")
    testbench_sv = section("testbench_sv")
    if not test_plan:
        test_plan = text.strip()[:12000]
    if not re.search(rf"\bmodule\s+{re.escape(SELF_TEST_TOP)}\b", testbench_sv):
        testbench_sv = ""
    return SelfTestGuide(
        test_plan=test_plan[:16000],
        testbench_sv=testbench_sv[:40000],
        raw_response=text,
    )


def validate_self_test_guide(
    guide: SelfTestGuide,
    *,
    design_name: str,
    interface_contract: str,
) -> tuple[SelfTestGuide, str | None]:
    """Reject advisory TBs that violate the normalized public contract."""

    tb = guide.testbench_sv
    if not tb:
        return guide, None
    reasons: list[str] = []
    semantic_oracle_error = bool(
        re.search(
            r"(?:test\s+passes?\s+if|verify|expected?)"
            r"[^\n]{0,180}\b(?:buggy|erroneous|incorrect|broken)\b"
            r"[^\n]{0,100}\b(?:behavior|implementation|rtl)\b"
            r"|\b(?:buggy|erroneous|incorrect|broken)\s+(?:rtl\s+)?behavior\b"
            r"[^\n]{0,180}\b(?:as[- ]is|to\s+be\s+verified|pass\s+condition|expected)\b"
            r"|\bexpected\b[^\n]{0,100}\(bug\)",
            f"{guide.test_plan}\n{tb}",
            flags=re.IGNORECASE,
        )
    )
    if semantic_oracle_error:
        reasons.append("uses known buggy or erroneous behavior as the self-test oracle")
    parameter_override = r"(?:#\s*\((?:[^()]|\([^()]*\))*\)\s*)?"
    if not re.search(
        rf"\b{re.escape(design_name)}\s*{parameter_override}[A-Za-z_]\w*\s*\(",
        tb,
    ):
        reasons.append(f"does not instantiate `{design_name}`")
    forbidden = re.search(
        r"\$(?:fopen|fread|fscanf|readmemh|readmemb|system)\b|`include\b",
        tb,
        flags=re.IGNORECASE,
    )
    if forbidden:
        reasons.append(f"contains forbidden file/process access `{forbidden.group(0)}`")

    reset_entries = re.findall(
        r"reset=([A-Za-z_]\w*)\s*\((active-(?:high|low))\)",
        interface_contract,
        flags=re.IGNORECASE,
    )
    for name, polarity in reset_entries:
        asserted = "0" if polarity.lower() == "active-low" else "1"
        deasserted = "1" if asserted == "0" else "0"
        assignments = re.findall(
            rf"\b{re.escape(name)}\s*=\s*(?:1\s*'\s*b\s*)?([01])\b",
            tb,
            flags=re.IGNORECASE,
        )
        if assignments and (
            assignments[0] != asserted
            or not any(value == deasserted for value in assignments[1:])
        ):
            reasons.append(
                f"drives `{name}` inconsistently with contract polarity {polarity}"
            )
    if reasons:
        return (
            SelfTestGuide(
                test_plan="" if semantic_oracle_error else guide.test_plan,
                testbench_sv="",
                raw_response=guide.raw_response,
            ),
            "; ".join(reasons),
        )
    return guide, None


def format_self_test_guidance(guide: SelfTestGuide, *, include_testbench: bool = True) -> str:
    lines = [
        "### Advisory Test Plan Generated From The Public Specification",
        "",
        guide.test_plan or "No usable test plan was generated.",
    ]
    if include_testbench and guide.testbench_sv:
        lines.extend([
            "",
            "### Advisory Self-Test",
            "",
            "This self-test was independently generated from the same public specification. "
            "Use it to reason about behavior, but treat the natural-language specification and "
            "interface contract as authoritative if they disagree.",
            "",
            "```systemverilog",
            guide.testbench_sv,
            "```",
        ])
    return "\n".join(lines)


def run_generated_self_test(
    *,
    prob_id: str,
    run_dir: Path,
    verilog_sources: list[str],
    testbench_sv: str,
    candidate_id: int,
    attempt: int,
    timeout_s: int = 30,
) -> SelfTestResult:
    """Run an advisory TB against only the generated/public RTL source files."""

    if not testbench_sv.strip():
        return SelfTestResult("not_run", "No valid generated self-test was available.")
    sim_dir = Path(run_dir) / "cvdp_sim" / prob_id
    if not sim_dir.exists():
        return SelfTestResult("not_run", "Generated CVDP RTL staging directory is unavailable.")

    source_paths: list[tuple[str, Path]] = []
    sim_root = sim_dir.resolve()
    for source in verilog_sources:
        rel = source[len("/code/"):] if source.startswith("/code/") else source.lstrip("/")
        path = (sim_dir / rel).resolve()
        if path != sim_root and sim_root not in path.parents:
            continue
        if path.exists() and path.is_file():
            source_paths.append((rel, path))
    if not source_paths:
        return SelfTestResult("not_run", "No generated/public Verilog source was available for self-test.")

    work_dir = (
        Path(run_dir)
        / "self_tests"
        / prob_id
        / f"candidate_{candidate_id:02d}_attempt_{attempt:02d}"
    )
    work_dir.mkdir(parents=True, exist_ok=True)
    tb_path = work_dir / "self_test.sv"
    exe_path = work_dir / "self_test.vvp"
    tb_path.write_text(testbench_sv, encoding="utf-8")

    isolated_sources: list[Path] = []
    for rel, source_path in source_paths:
        isolated_path = work_dir / "rtl" / rel
        isolated_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, isolated_path)
        isolated_sources.append(isolated_path)

    include_dirs = sorted({str(path.parent) for path in isolated_sources})
    iverilog = _runtime_tool("iverilog")
    vvp = _runtime_tool("vvp")
    if not iverilog or not vvp:
        return SelfTestResult("unavailable", "Icarus Verilog or vvp is not installed.")
    compile_cmd = [iverilog, "-g2012", "-s", SELF_TEST_TOP, "-o", str(exe_path)]
    for include_dir in include_dirs:
        compile_cmd.extend(["-I", include_dir])
    compile_cmd.extend(str(path) for path in isolated_sources)
    compile_cmd.append(str(tb_path))
    try:
        compiled = subprocess.run(
            compile_cmd,
            cwd=work_dir,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except FileNotFoundError:
        return SelfTestResult("unavailable", "Icarus Verilog is not installed.")
    except subprocess.TimeoutExpired:
        return SelfTestResult("invalid", "Generated self-test compilation timed out.")

    compile_output = (compiled.stdout or "") + (compiled.stderr or "")
    (work_dir / "compile_output.txt").write_text(compile_output, encoding="utf-8")
    if compiled.returncode != 0:
        return SelfTestResult(
            "invalid",
            "Generated advisory testbench did not compile; ignore it for candidate scoring.\n"
            + compile_output[-4000:],
            compile_returncode=compiled.returncode,
        )

    try:
        ran = subprocess.run(
            [vvp, str(exe_path)],
            cwd=work_dir,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except FileNotFoundError:
        return SelfTestResult("unavailable", "vvp is not installed.", compile_returncode=0)
    except subprocess.TimeoutExpired:
        return SelfTestResult(
            "invalid",
            "Generated advisory testbench timed out; ignore it for candidate scoring.",
            compile_returncode=0,
        )

    output = (ran.stdout or "") + (ran.stderr or "")
    (work_dir / "run_output.txt").write_text(output, encoding="utf-8")
    detail = output[-8000:] or "(self-test produced no output)"
    if "CKTLEAN_SELF_TEST_FAIL" in output:
        status = "fail"
    elif "CKTLEAN_SELF_TEST_PASS" in output and ran.returncode == 0:
        status = "pass"
    else:
        status = "inconclusive"
        detail = "Generated advisory testbench emitted no trustworthy pass/fail marker.\n" + detail
    return SelfTestResult(
        status,
        detail,
        compile_returncode=compiled.returncode,
        run_returncode=ran.returncode,
    )


def _code_hash(code: str | None) -> str | None:
    if code is None:
        return None
    return hashlib.sha256(code.encode("utf-8", errors="replace")).hexdigest()[:16]


def _diagnostic_signature(result: dict | None) -> str:
    if not result:
        return "no-result"
    stable_signature = str(result.get("diagnostic_signature") or "").strip()
    if stable_signature:
        return "diagnostic:" + stable_signature
    detail = str(result.get("detail") or "")
    detail = re.sub(r"/[^\s:]+", "<path>", detail)
    detail = re.sub(r"\b\d+(?:\.\d+)?\s*(?:ns|us|ms|s)\b", "<time>", detail)
    detail = " ".join(detail.split())[:600]
    payload = {
        "compile_pass": result.get("compile_pass"),
        "sv_extracted": result.get("sv_extracted"),
        "lint_pass": result.get("lint_pass"),
        "sim_status": result.get("sim_status"),
        "sim_mismatches": result.get("sim_mismatches"),
        "failure_stage": result.get("failure_stage"),
        "failure_family": result.get("failure_family"),
        "detail": detail,
    }
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=True, default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()[:16]


@dataclass(frozen=True)
class CandidateObservation:
    candidate_id: int
    attempt: int
    accepted: bool
    improved_candidate: bool
    improved_global: bool
    stagnation_count: int
    stagnation_reason: str | None
    result_signature: str
    code_hash: str | None


@dataclass
class CandidateTracker:
    """Track independent candidate lineages while retaining the global best."""

    snapshot_root: Path
    prob_id: str
    progress_key: ProgressKeyFn
    max_candidates: int = 3
    patience: int = 2
    candidate_id: int = 0
    attempt: int = 0
    stagnation_count: int = 0
    active_best_result: dict | None = None
    active_best_code: str | None = None
    active_best_key: ProgressKey | None = None
    global_best_result: dict | None = None
    global_best_code: str | None = None
    global_best_key: ProgressKey | None = None
    previous_signature: str | None = None
    previous_code_hash: str | None = None
    candidates: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.snapshot_root = Path(self.snapshot_root)
        self.max_candidates = max(1, int(self.max_candidates))
        self.patience = max(0, int(self.patience))

    @property
    def can_restart(self) -> bool:
        return self.candidate_id < self.max_candidates

    @property
    def is_stagnant(self) -> bool:
        return self.patience > 0 and self.stagnation_count >= self.patience

    def start_candidate(
        self,
        result: dict,
        code: str | None,
        *,
        reason: str,
    ) -> CandidateObservation:
        if not self.can_restart:
            raise RuntimeError("maximum candidate count reached")
        self.candidate_id += 1
        self.attempt = 0
        self.stagnation_count = 0
        key = self.progress_key(result)
        self.active_best_result = dict(result)
        self.active_best_code = code
        self.active_best_key = key
        self.previous_signature = _diagnostic_signature(result)
        self.previous_code_hash = _code_hash(code)
        improved_global = self._update_global(result, code, key)
        self.candidates.append({
            "candidate_id": self.candidate_id,
            "reason": reason,
            "initial_key": list(key),
        })
        observation = CandidateObservation(
            candidate_id=self.candidate_id,
            attempt=0,
            accepted=code is not None,
            improved_candidate=True,
            improved_global=improved_global,
            stagnation_count=0,
            stagnation_reason=None,
            result_signature=self.previous_signature,
            code_hash=self.previous_code_hash,
        )
        self._snapshot(observation, result, code, reason=reason)
        return observation

    def observe(self, result: dict, code: str | None, *, reason: str) -> CandidateObservation:
        if self.candidate_id <= 0 or self.active_best_key is None:
            raise RuntimeError("start_candidate must be called before observe")
        self.attempt += 1
        key = self.progress_key(result)
        improved_candidate = key > self.active_best_key
        accepted = code is not None and key >= self.active_best_key
        signature = _diagnostic_signature(result)
        code_hash = _code_hash(code)

        if improved_candidate:
            self.stagnation_count = 0
            stagnation_reason = None
        elif code_hash is not None and code_hash == self.previous_code_hash:
            self.stagnation_count += 1
            stagnation_reason = "candidate source did not change"
        elif signature == self.previous_signature:
            self.stagnation_count += 1
            stagnation_reason = "evaluation failure signature repeated"
        else:
            # A different assertion value or failure timestamp is not measurable
            # progress.  Reset the rewrite counter only when the evaluator's
            # progress key improves (for example, more tests pass or simulation
            # advances to sim_pass).  Otherwise local edits can consume every
            # repair iteration while oscillating among equally bad signatures.
            self.stagnation_count += 1
            stagnation_reason = "evaluation signature changed without measurable progress"

        if accepted:
            self.active_best_result = dict(result)
            self.active_best_code = code
            self.active_best_key = key

        improved_global = self._update_global(result, code, key)
        self.previous_signature = signature
        self.previous_code_hash = code_hash
        observation = CandidateObservation(
            candidate_id=self.candidate_id,
            attempt=self.attempt,
            accepted=accepted,
            improved_candidate=improved_candidate,
            improved_global=improved_global,
            stagnation_count=self.stagnation_count,
            stagnation_reason=stagnation_reason,
            result_signature=signature,
            code_hash=code_hash,
        )
        self._snapshot(observation, result, code, reason=reason)
        return observation

    def restore_active(self, target: Path) -> None:
        self._restore(target, self.active_best_code)

    def restore_global(self, target: Path) -> None:
        self._restore(target, self.global_best_code)

    def _update_global(self, result: dict, code: str | None, key: ProgressKey) -> bool:
        if code is None:
            return False
        if self.global_best_key is not None and key <= self.global_best_key:
            return False
        self.global_best_result = dict(result)
        self.global_best_code = code
        self.global_best_key = key
        return True

    @staticmethod
    def _restore(target: Path, code: str | None) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        if code is None:
            target.unlink(missing_ok=True)
        else:
            target.write_text(code, encoding="utf-8")

    def _snapshot(
        self,
        observation: CandidateObservation,
        result: dict,
        code: str | None,
        *,
        reason: str,
    ) -> None:
        candidate_dir = self.snapshot_root / self.prob_id / f"candidate_{self.candidate_id:02d}"
        candidate_dir.mkdir(parents=True, exist_ok=True)
        stem = f"attempt_{observation.attempt:02d}"
        if code is not None:
            (candidate_dir / f"{stem}.lean").write_text(code, encoding="utf-8")
        metadata = {
            **observation.__dict__,
            "reason": reason,
            "progress_key": list(self.progress_key(result)),
            "result": result,
        }
        (candidate_dir / f"{stem}.json").write_text(
            json.dumps(metadata, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        if self.active_best_code is not None:
            (candidate_dir / "best.lean").write_text(self.active_best_code, encoding="utf-8")
        if self.global_best_code is not None:
            global_dir = self.snapshot_root / self.prob_id
            (global_dir / "global_best.lean").write_text(self.global_best_code, encoding="utf-8")
            source = candidate_dir / f"{stem}.json"
            if observation.improved_global and source.exists():
                shutil.copy2(source, global_dir / "global_best.json")
