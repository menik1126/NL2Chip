"""Concurrent, content-addressed execution for finite parameter PPA sweeps.

This module deliberately knows nothing about Yosys or OpenROAD.  Callers
provide a ``synthesize`` callback which receives one canonical request and an
isolated workspace.  The runner supplies the pieces that are easy to get
wrong around that callback:

* equivalent parameter dictionaries have one stable cache key;
* duplicate work is single-flight, including across runner instances in this
  Python process;
* distinct configurations may execute in parallel;
* only successful results are published to the persistent cache; and
* finite sweep evidence can never be confused with a universal Lean theorem.

Cache entries contain metrics only.  Backend integrations which need to reuse
large netlists or physical-design databases should build a stage-specific
artifact cache on top of the same request key.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Iterable, Mapping, TypeAlias


ParameterConfig: TypeAlias = tuple[tuple[str, int], ...]
SynthesisMetrics: TypeAlias = dict[str, object]
SynthesisCallback: TypeAlias = Callable[["PPARequest", Path], Mapping[str, object]]

_CACHE_SCHEMA_VERSION = 1
_SV_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")


def _canonical_config(
    config: Mapping[str, int] | Iterable[tuple[str, int]],
) -> ParameterConfig:
    """Return a validated, name-sorted native-Nat parameter environment."""
    items = config.items() if isinstance(config, Mapping) else config
    canonical: dict[str, int] = {}
    for raw_name, raw_value in items:
        if not isinstance(raw_name, str) or not _SV_IDENTIFIER.fullmatch(raw_name):
            raise ValueError(f"invalid SystemVerilog parameter name: {raw_name!r}")
        if raw_name in canonical:
            raise ValueError(f"duplicate parameter in PPA configuration: {raw_name}")
        if isinstance(raw_value, bool) or not isinstance(raw_value, int):
            raise TypeError(
                f"PPA parameter {raw_name} must be an integer, got {type(raw_value).__name__}"
            )
        if raw_value < 0 or raw_value > 0xFFFF_FFFF:
            raise ValueError(
                f"PPA parameter {raw_name}={raw_value} is outside the 32-bit unsigned Nat contract"
            )
        canonical[raw_name] = raw_value
    return tuple(sorted(canonical.items()))


class EvidenceKind(str, Enum):
    """The logical scope of evidence attached to an evaluation result."""

    FINITE_SWEEP = "finite_parameter_sweep"
    UNIVERSAL_LEAN_THEOREM = "universal_lean_theorem"


@dataclass(frozen=True)
class VerificationEvidence:
    """Evidence with an explicit, non-upgradable verification scope.

    A finite set of successful tool runs is useful validation evidence, but it
    is never a theorem about every legal parameter value.  Conversely,
    universal evidence must name the Lean theorem which establishes it.
    """

    kind: EvidenceKind
    configurations: tuple[ParameterConfig, ...] = ()
    theorem: str | None = None

    def __post_init__(self) -> None:
        try:
            kind = self.kind if isinstance(self.kind, EvidenceKind) else EvidenceKind(self.kind)
        except ValueError as error:
            raise ValueError(f"unknown verification evidence kind: {self.kind!r}") from error

        normalized = tuple(
            sorted({_canonical_config(config) for config in self.configurations})
        )
        theorem = self.theorem.strip() if isinstance(self.theorem, str) else self.theorem

        if kind is EvidenceKind.UNIVERSAL_LEAN_THEOREM and (
            not isinstance(theorem, str) or not theorem
        ):
            raise ValueError("universal Lean theorem evidence requires a theorem name")
        if kind is EvidenceKind.FINITE_SWEEP and theorem is not None:
            raise ValueError("finite sweep evidence cannot name a universal theorem")

        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "configurations", normalized)
        object.__setattr__(self, "theorem", theorem)


@dataclass(frozen=True)
class PPARequest:
    """Every input which can affect one concrete synthesis/PPA result.

    ``flow_fingerprint`` is supplied by the integration layer.  It should be a
    digest of the effective flow configuration and toolchain identity (for
    example config/SDC contents, ORFS revision, Docker image digest, platform,
    and PDK revision), rather than a mutable image tag.
    """

    config: ParameterConfig
    sv_code: str
    top_module: str
    flow_fingerprint: str

    def __post_init__(self) -> None:
        canonical = _canonical_config(self.config)
        if not isinstance(self.sv_code, str):
            raise TypeError("sv_code must be a string")
        if not isinstance(self.top_module, str) or not self.top_module:
            raise ValueError("top_module must be a non-empty string")
        if not isinstance(self.flow_fingerprint, str) or not self.flow_fingerprint:
            raise ValueError("flow_fingerprint must be a non-empty string")
        object.__setattr__(self, "config", canonical)

    @property
    def rtl_sha256(self) -> str:
        return hashlib.sha256(self.sv_code.encode("utf-8")).hexdigest()

    def cache_payload(self) -> dict[str, object]:
        """Canonical, path-independent input record used by the disk cache."""
        return {
            "schema_version": _CACHE_SCHEMA_VERSION,
            "rtl_sha256": self.rtl_sha256,
            "top_module": self.top_module,
            "parameters": [[name, value] for name, value in self.config],
            "flow_fingerprint": self.flow_fingerprint,
        }

    @property
    def cache_key(self) -> str:
        encoded = json.dumps(
            self.cache_payload(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @property
    def key(self) -> str:
        """Short compatibility alias for callers which call requests keys."""
        return self.cache_key


@dataclass(frozen=True)
class PPAResult:
    """Result for one canonical configuration."""

    config: ParameterConfig
    cache_key: str
    success: bool
    cache_hit: bool
    metrics: SynthesisMetrics = field(default_factory=dict)
    error: str | None = None
    evidence: VerificationEvidence = field(
        default_factory=lambda: VerificationEvidence(EvidenceKind.FINITE_SWEEP)
    )
    workspace: Path | None = None
    cache_error: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "config": dict(self.config),
            "cache_key": self.cache_key,
            "success": self.success,
            "cache_hit": self.cache_hit,
            "metrics": dict(self.metrics),
            "error": self.error,
            "evidence": {
                "kind": self.evidence.kind.value,
                "configurations": [dict(config) for config in self.evidence.configurations],
                "theorem": self.evidence.theorem,
            },
            "workspace": str(self.workspace) if self.workspace is not None else None,
            "cache_error": self.cache_error,
        }


# A module-wide registry prevents duplicate work when independent evaluator
# objects point at the same persistent cache in one Python process.  The
# initiating runner owns the Future's executor; other runners only wait on it.
_inflight_lock = threading.RLock()
_inflight: dict[tuple[str, str], Future[PPAResult]] = {}


class ParameterizedPPARunner:
    """Run and cache a finite collection of concrete parameter configurations."""

    def __init__(
        self,
        *,
        cache_dir: Path | str,
        synthesize: SynthesisCallback,
        max_workers: int = 1,
    ) -> None:
        if isinstance(max_workers, bool) or not isinstance(max_workers, int) or max_workers < 1:
            raise ValueError("max_workers must be a positive integer")
        if not callable(synthesize):
            raise TypeError("synthesize must be callable")

        requested_cache_dir = Path(cache_dir).expanduser()
        requested_cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir = requested_cache_dir.resolve()
        self.synthesize = synthesize
        self.max_workers = max_workers
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="sparkle-ppa",
        )
        self._closed = False
        self._state_lock = threading.Lock()

    def _entry_dir(self, request: PPARequest) -> Path:
        key = request.cache_key
        return self.cache_dir / "objects" / key[:2] / key

    def _manifest_path(self, request: PPARequest) -> Path:
        return self._entry_dir(request) / "manifest.json"

    @staticmethod
    def _evidence(config: ParameterConfig) -> VerificationEvidence:
        return VerificationEvidence(
            kind=EvidenceKind.FINITE_SWEEP,
            configurations=(config,),
            theorem=None,
        )

    def _load_cached(self, request: PPARequest) -> PPAResult | None:
        manifest_path = self._manifest_path(request)
        try:
            record = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None

        if not isinstance(record, dict):
            return None
        if record.get("schema_version") != _CACHE_SCHEMA_VERSION:
            return None
        if record.get("complete") is not True or record.get("success") is not True:
            return None
        if record.get("cache_key") != request.cache_key:
            return None
        if record.get("request") != request.cache_payload():
            return None
        metrics = record.get("metrics")
        if not isinstance(metrics, dict):
            return None

        return PPAResult(
            config=request.config,
            cache_key=request.cache_key,
            success=True,
            cache_hit=True,
            metrics=dict(metrics),
            error=None,
            evidence=self._evidence(request.config),
            workspace=None,
        )

    def _publish_cached(self, request: PPARequest, metrics: SynthesisMetrics) -> None:
        """Publish a complete manifest with one atomic rename.

        Readers never inspect temporary files, so a crash before ``replace``
        leaves a cache miss rather than a partially valid hit.  The temporary
        file is created in the destination directory to keep the rename on one
        filesystem.
        """
        entry_dir = self._entry_dir(request)
        entry_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = entry_dir / "manifest.json"
        record = {
            "schema_version": _CACHE_SCHEMA_VERSION,
            "complete": True,
            "success": True,
            "cache_key": request.cache_key,
            "request": request.cache_payload(),
            "metrics": metrics,
        }
        encoded = json.dumps(
            record,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )

        fd, temporary_name = tempfile.mkstemp(
            prefix=".manifest-",
            suffix=".tmp",
            dir=entry_dir,
            text=True,
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as output:
                output.write(encoded)
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary_path, manifest_path)
            # Persist the directory entry where supported.  Some filesystems
            # reject directory fsync; the manifest is still atomically visible.
            try:
                directory_fd = os.open(entry_dir, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            except OSError:
                pass
        except Exception:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass
            raise

    def _new_workspace(self, request: PPARequest) -> Path:
        root = self.cache_dir / "workspaces" / request.cache_key[:2] / request.cache_key
        root.mkdir(parents=True, exist_ok=True)
        # Retain attempts for diagnostics and to honor the callback contract
        # that its isolated workspace remains available after completion.
        workspace = root / f"attempt-{uuid.uuid4().hex}"
        workspace.mkdir()
        return workspace

    @staticmethod
    def _reported_failure(metrics: SynthesisMetrics) -> str | None:
        explicitly_failed = (
            ("success" in metrics and not bool(metrics["success"]))
            or ("synth_pass" in metrics and not bool(metrics["synth_pass"]))
        )
        if explicitly_failed:
            for key in ("error", "synth_error", "ppa_error", "pnr_error"):
                detail = metrics.get(key)
                if detail:
                    return str(detail)
            return "synthesis callback reported failure"
        return None

    def _execute(self, request: PPARequest) -> PPAResult:
        workspace: Path | None = None
        try:
            workspace = self._new_workspace(request)
            raw_metrics = self.synthesize(request, workspace)
            if not isinstance(raw_metrics, Mapping):
                raise TypeError(
                    "synthesis callback must return a mapping of result metrics"
                )
            metrics: SynthesisMetrics = dict(raw_metrics)
        except Exception as error:
            return PPAResult(
                config=request.config,
                cache_key=request.cache_key,
                success=False,
                cache_hit=False,
                metrics={},
                error=f"{type(error).__name__}: {error}",
                evidence=self._evidence(request.config),
                workspace=workspace,
            )

        reported_failure = self._reported_failure(metrics)
        if reported_failure is not None:
            return PPAResult(
                config=request.config,
                cache_key=request.cache_key,
                success=False,
                cache_hit=False,
                metrics=metrics,
                error=reported_failure,
                evidence=self._evidence(request.config),
                workspace=workspace,
            )

        cache_error = None
        try:
            self._publish_cached(request, metrics)
        except Exception as error:
            # The tool result remains valid even if persistence is unavailable.
            # Since no complete manifest was published, a later call will retry.
            cache_error = f"{type(error).__name__}: {error}"

        return PPAResult(
            config=request.config,
            cache_key=request.cache_key,
            success=True,
            cache_hit=False,
            metrics=metrics,
            error=None,
            evidence=self._evidence(request.config),
            workspace=workspace,
            cache_error=cache_error,
        )

    @staticmethod
    def _completed_future(result: PPAResult) -> Future[PPAResult]:
        future: Future[PPAResult] = Future()
        future.set_result(result)
        return future

    def _future_for(self, request: PPARequest) -> Future[PPAResult]:
        identity = (str(self.cache_dir), request.cache_key)
        with _inflight_lock:
            # Check disk first.  This makes a sequential call after a completed
            # miss observably a cache hit even if its done callback has not yet
            # removed the Future from the registry.
            cached = self._load_cached(request)
            if cached is not None:
                return self._completed_future(cached)

            existing = _inflight.get(identity)
            if existing is not None:
                return existing

            with self._state_lock:
                if self._closed:
                    raise RuntimeError("ParameterizedPPARunner is closed")
                future = self._executor.submit(self._execute, request)
            _inflight[identity] = future

            def forget(completed: Future[PPAResult]) -> None:
                with _inflight_lock:
                    if _inflight.get(identity) is completed:
                        _inflight.pop(identity, None)

            future.add_done_callback(forget)
            return future

    def run(self, requests: Iterable[PPARequest]) -> list[PPAResult]:
        """Execute unique requests and return results in canonical order."""
        unique: dict[str, PPARequest] = {}
        for request in requests:
            if not isinstance(request, PPARequest):
                raise TypeError("requests must contain PPARequest values")
            unique.setdefault(request.cache_key, request)

        ordered_requests = sorted(
            unique.values(),
            key=lambda request: (
                request.config,
                request.top_module,
                request.flow_fingerprint,
                request.cache_key,
            ),
        )
        futures = [self._future_for(request) for request in ordered_requests]
        results = [future.result() for future in futures]
        return sorted(results, key=lambda result: (result.config, result.cache_key))

    def close(self, *, wait: bool = True) -> None:
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
        self._executor.shutdown(wait=wait)

    shutdown = close

    def __enter__(self) -> "ParameterizedPPARunner":
        return self

    def __exit__(self, _exc_type, _exc_value, _traceback) -> None:
        self.close()


# Descriptive aliases make the result type convenient for integrations while
# preserving the small API used by the standalone runner tests.
PPAConfigResult = PPAResult


__all__ = [
    "EvidenceKind",
    "ParameterConfig",
    "PPAConfigResult",
    "PPARequest",
    "PPAResult",
    "ParameterizedPPARunner",
    "VerificationEvidence",
]
