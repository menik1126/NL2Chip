"""Trusted schema handling for Lean universal-theorem evidence.

This module deliberately separates three things that are easy to conflate:

* a certificate *request* found in candidate Lean source;
* a JSON marker emitted while elaborating Lean code; and
* evidence independently revalidated against the kernel-checked environment.

Only the last item may be attached to evaluator results.  Parsing a candidate's
own log output is useful for discovery, but is never by itself a trust boundary.
"""
from __future__ import annotations

import json
import hashlib
import re
import subprocess
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path


UNIVERSAL_THEOREM_MARKER = "SPARKLE_UNIVERSAL_THEOREM_JSON:"
UNIVERSAL_THEOREM_ALLOWED_AXIOMS = frozenset({
    "propext",
    "Classical.choice",
    "Quot.sound",
})
MAX_UNIVERSAL_THEOREM_REQUESTS = 16
TRUSTED_UNIVERSAL_THEOREM_REVALIDATOR = "sparkle-certify"
_LEAN_QUALIFIED_NAME = re.compile(
    r"[A-Za-z_][A-Za-z0-9_']*(?:\.[A-Za-z_][A-Za-z0-9_']*)*"
)


def lean_source_without_comments_or_strings(source: str) -> str:
    """Blank comments and string contents before discovering requests."""
    output: list[str] = []
    index = 0
    block_depth = 0
    in_string = False
    escaped = False
    while index < len(source):
        if block_depth > 0:
            if source.startswith("/-", index):
                block_depth += 1
                output.extend("  ")
                index += 2
            elif source.startswith("-/", index):
                block_depth -= 1
                output.extend("  ")
                index += 2
            else:
                output.append("\n" if source[index] == "\n" else " ")
                index += 1
            continue
        if in_string:
            char = source[index]
            output.append("\n" if char == "\n" else " ")
            index += 1
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if source.startswith("--", index):
            while index < len(source) and source[index] != "\n":
                output.append(" ")
                index += 1
            continue
        if source.startswith("/-", index):
            block_depth = 1
            output.extend("  ")
            index += 2
            continue
        if source[index] == '"':
            in_string = True
            output.append(" ")
            index += 1
            continue
        output.append(source[index])
        index += 1
    return "".join(output)


def declared_universal_certificate_commands(
    source: str,
) -> dict[str, tuple[str, ...]]:
    """Discover a bounded set of theorem/parameter revalidation requests.

    Text discovery is intentionally *not* proof.  A syntax quotation can look
    like a command here; the independent Lean recheck must still resolve and
    certify the named theorem before any evidence is returned.
    """
    commands: dict[str, tuple[str, ...]] = {}
    pattern = re.compile(
        r"#sparkleUniversalTheorem\s+"
        r"([A-Za-z_][A-Za-z0-9_'.]*)\s+"
        r"parameters\s*\[([^\]]*)\]"
    )
    for match in pattern.finditer(lean_source_without_comments_or_strings(source)):
        theorem = match.group(1)
        parameters = tuple(
            item.strip()
            for item in match.group(2).split(",")
            if item.strip()
        )
        if not parameters or any(
            re.fullmatch(r"[A-Za-z_][A-Za-z0-9_']*", parameter) is None
            for parameter in parameters
        ):
            continue
        commands[theorem] = parameters
        if len(commands) >= MAX_UNIVERSAL_THEOREM_REQUESTS:
            break
    return commands


def _allowed_axioms(raw: object) -> bool:
    return bool(
        isinstance(raw, list)
        and all(
            isinstance(axiom, str) and axiom in UNIVERSAL_THEOREM_ALLOWED_AXIOMS
            for axiom in raw
        )
    )


def is_valid_universal_theorem_record(
    record: Mapping[str, object],
    *,
    expected_nonce: str | None = None,
) -> bool:
    """Validate a raw Lean marker, including its axiom policy and nonce."""
    parameters = record.get("parameters")
    if expected_nonce is not None and record.get("verification_nonce") != expected_nonce:
        return False
    return bool(
        record.get("schema_version") == 1
        and record.get("evidence_kind") == "universal_lean_theorem"
        and record.get("status") == "proved"
        and record.get("kernel_checked") is True
        and record.get("compiler_correctness_claimed") is False
        and isinstance(record.get("theorem"), str)
        and bool(record.get("theorem"))
        and isinstance(parameters, list)
        and bool(parameters)
        and all(isinstance(parameter, str) and parameter for parameter in parameters)
        and isinstance(record.get("proposition"), str)
        and bool(record.get("proposition"))
        and _allowed_axioms(record.get("axioms"))
    )


def is_valid_universal_theorem_evidence(evidence: Mapping[str, object]) -> bool:
    """Validate normalized evidence before search/report code trusts it."""
    parameters = evidence.get("parameters")
    return bool(
        evidence.get("kind") == "universal_lean_theorem"
        and evidence.get("status") == "proved"
        and evidence.get("scope") == "lean_source_semantics"
        and evidence.get("kernel_checked") is True
        and evidence.get("compiler_correctness_claimed") is False
        and evidence.get("revalidated_by") == TRUSTED_UNIVERSAL_THEOREM_REVALIDATOR
        and isinstance(evidence.get("theorem"), str)
        and bool(evidence.get("theorem"))
        and isinstance(parameters, list)
        and bool(parameters)
        and all(isinstance(parameter, str) and parameter for parameter in parameters)
        and isinstance(evidence.get("proposition"), str)
        and bool(evidence.get("proposition"))
        and isinstance(evidence.get("source_artifact"), str)
        and bool(evidence.get("source_artifact"))
        and _allowed_axioms(evidence.get("axioms"))
    )


def _matching_request(
    theorem: str,
    parameters: Sequence[str],
    declared: Mapping[str, tuple[str, ...]],
) -> bool:
    matches = [
        command
        for command in declared
        if theorem == command or theorem.endswith("." + command)
    ]
    return len(matches) == 1 and tuple(parameters) == declared[matches[0]]


def extract_universal_theorem_evidence(
    compiler_output: str,
    source: str,
    source_artifact: str,
    *,
    expected_nonce: str | None = None,
    revalidated_by: str | None = None,
) -> list[dict]:
    """Deserialize valid markers matching declared revalidation requests.

    Evaluator production code must pass a fresh ``expected_nonce`` and feed
    output from its independent trusted recheck.  The optional form remains
    useful for schema-level tests, but does not establish output provenance.
    """
    declared = declared_universal_certificate_commands(source)
    evidence: dict[str, dict] = {}
    for line in str(compiler_output or "").splitlines():
        marker_index = line.find(UNIVERSAL_THEOREM_MARKER)
        if marker_index < 0:
            continue
        encoded = line[marker_index + len(UNIVERSAL_THEOREM_MARKER):].strip()
        try:
            record = json.loads(encoded)
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(record, dict) or not is_valid_universal_theorem_record(
            record, expected_nonce=expected_nonce
        ):
            continue
        theorem = record["theorem"]
        parameters = record["parameters"]
        if not _matching_request(theorem, parameters, declared):
            continue
        domain = record.get("domain", [])
        domain_predicate = record.get("domain_predicate")
        if not isinstance(domain_predicate, str) or not domain_predicate:
            if isinstance(domain, list):
                premise_types = [
                    str(entry.get("type"))
                    for entry in domain
                    if isinstance(entry, dict)
                    and entry.get("role") == "premise"
                    and entry.get("type")
                ]
                domain_predicate = " ∧ ".join(premise_types) or "True"
            else:
                domain_predicate = str(domain or "True")
        normalized = {
            "kind": "universal_lean_theorem",
            "status": "proved",
            "scope": "lean_source_semantics",
            "theorem": theorem,
            "parameters": list(parameters),
            "domain_predicate": domain_predicate,
            "domain": domain,
            "proposition": record["proposition"],
            "binders": record.get("binders", []),
            "conclusion": record.get("conclusion"),
            "kernel_checked": True,
            "axioms": list(record["axioms"]),
            "source_artifact": source_artifact,
            "compiler_correctness_claimed": False,
        }
        if revalidated_by is not None:
            normalized["revalidated_by"] = revalidated_by
        evidence[theorem] = normalized
    return [evidence[name] for name in sorted(evidence)]


def independently_revalidate_universal_theorems(
    candidate_output: str,
    source: str,
    source_artifact: str,
    *,
    project_root: Path,
    module_name: str,
    certifier_path: Path | None = None,
    expected_certifier_sha256: str | None = None,
    certifier: Callable[[str, str, list[str], str], Mapping[str, object] | None]
    | None = None,
    timeout: int = 120,
) -> list[dict]:
    """Recheck every candidate request through evaluator-owned Lean code.

    Candidate output is used only to discover the fully resolved theorem name.
    Every returned evidence object comes from a second invocation carrying a
    fresh nonce.  A failed/unsupported recheck is simply no evidence; it never
    downgrades compilation or simulation of the design itself.
    """
    requests = extract_universal_theorem_evidence(
        candidate_output, source, source_artifact
    )
    if not requests:
        return []
    if _LEAN_QUALIFIED_NAME.fullmatch(module_name) is None:
        return []
    executable = (
        Path(certifier_path)
        if certifier_path is not None
        else Path(project_root) / ".lake" / "build" / "bin" / "sparkle-certify"
    )
    if certifier is None and (
        not executable.is_file() or not (executable.stat().st_mode & 0o111)
    ):
        # Never build or elaborate a checker after candidate code has run.
        # Search startup/CI prebuilds this trusted executable; absence is a
        # fail-closed lack of evidence, not a reason to trust candidate logs.
        return []
    if certifier is None and expected_certifier_sha256 is not None:
        try:
            actual_digest = hashlib.sha256(executable.read_bytes()).hexdigest()
        except OSError:
            return []
        if actual_digest != expected_certifier_sha256:
            return []

    verified: dict[str, dict] = {}
    for request in requests[:MAX_UNIVERSAL_THEOREM_REQUESTS]:
        theorem = request.get("theorem")
        parameters = request.get("parameters")
        if not isinstance(theorem, str) or not isinstance(parameters, list):
            continue
        nonce = uuid.uuid4().hex
        if _LEAN_QUALIFIED_NAME.fullmatch(theorem) is None or any(
            re.fullmatch(r"[A-Za-z_][A-Za-z0-9_']*", parameter) is None
            for parameter in parameters
        ):
            continue
        record: Mapping[str, object] | None = None
        if certifier is not None:
            try:
                record = certifier(module_name, theorem, parameters, nonce)
            except Exception:
                continue
            if not isinstance(record, Mapping):
                continue
        else:
            try:
                checker = subprocess.run(
                    [
                        str(executable.resolve()),
                        "--module", module_name,
                        "--theorem", theorem,
                        "--parameters", ",".join(parameters),
                        "--nonce", nonce,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    cwd=str(Path(project_root).resolve()),
                )
            except (OSError, subprocess.TimeoutExpired):
                continue
            output_lines = [line for line in checker.stdout.splitlines() if line.strip()]
            if checker.returncode != 0 or checker.stderr or len(output_lines) != 1:
                continue
            if expected_certifier_sha256 is not None:
                try:
                    post_digest = hashlib.sha256(executable.read_bytes()).hexdigest()
                except OSError:
                    continue
                if post_digest != expected_certifier_sha256:
                    continue
            try:
                decoded = json.loads(output_lines[0])
            except (TypeError, json.JSONDecodeError):
                continue
            if not isinstance(decoded, dict):
                continue
            record = decoded

        request_source = (
            f"#sparkleUniversalTheorem {theorem} parameters "
            f"[{', '.join(parameters)}]"
        )
        fresh = extract_universal_theorem_evidence(
            UNIVERSAL_THEOREM_MARKER + json.dumps(
                dict(record or {}), separators=(",", ":"), ensure_ascii=False
            ),
            request_source,
            source_artifact,
            expected_nonce=nonce,
            revalidated_by=TRUSTED_UNIVERSAL_THEOREM_REVALIDATOR,
        )
        for evidence in fresh:
            if (
                evidence.get("theorem") == theorem
                and evidence.get("parameters") == parameters
                and is_valid_universal_theorem_evidence(evidence)
            ):
                verified[theorem] = evidence
    return [verified[name] for name in sorted(verified)]
