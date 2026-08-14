from __future__ import annotations

import sys
import threading
from collections import Counter
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "agent"))

from parameterized_ppa import (  # noqa: E402
    EvidenceKind,
    PPARequest,
    ParameterizedPPARunner,
    VerificationEvidence,
)


RTL = """
module dut #(parameter W = 8) (
    input logic [W-1:0] x,
    output logic [W-1:0] y
);
    assign y = x;
endmodule
"""


def _request(
    width: int,
    *,
    sv_code: str = RTL,
    top_module: str = "dut",
    flow_fingerprint: str = "orfs-sky130hd-v1",
) -> PPARequest:
    return PPARequest(
        config=(("W", width),),
        sv_code=sv_code,
        top_module=top_module,
        flow_fingerprint=flow_fingerprint,
    )


def _width(config: tuple[tuple[str, int], ...]) -> int:
    return dict(config)["W"]


def _successful_metrics(request: PPARequest) -> dict:
    width = _width(request.config)
    return {
        "synth_pass": True,
        "area_um2": float(width),
        "cell_count": width * 2,
    }


def test_duplicate_request_is_single_flight_and_persistently_cached(
    tmp_path: Path,
):
    calls = 0
    calls_lock = threading.Lock()

    def synthesize(request: PPARequest, workspace: Path) -> dict:
        nonlocal calls
        assert workspace.is_dir()
        with calls_lock:
            calls += 1
        return _successful_metrics(request)

    cache_dir = tmp_path / "ppa-cache"
    runner = ParameterizedPPARunner(
        cache_dir=cache_dir,
        synthesize=synthesize,
        max_workers=4,
    )
    request = PPARequest(
        # Equivalent requests with a different parameter insertion order must
        # share one canonical key.
        config=(("Z", 2), ("W", 8)),
        sv_code=RTL,
        top_module="dut",
        flow_fingerprint="orfs-sky130hd-v1",
    )
    reordered = PPARequest(
        config=(("W", 8), ("Z", 2)),
        sv_code=RTL,
        top_module="dut",
        flow_fingerprint="orfs-sky130hd-v1",
    )

    first = runner.run([request, request, reordered])

    assert calls == 1
    assert len(first) == 1
    assert first[0].config == (("W", 8), ("Z", 2))
    assert first[0].success is True
    assert first[0].cache_hit is False
    assert first[0].metrics["area_um2"] == 8.0

    def must_not_run(_request: PPARequest, _workspace: Path) -> dict:
        raise AssertionError("a new runner did not reuse the persistent cache")

    # The cache is an on-disk contract, not merely an instance-local memo.
    restarted = ParameterizedPPARunner(
        cache_dir=cache_dir,
        synthesize=must_not_run,
        max_workers=2,
    )
    second = restarted.run([reordered])

    assert len(second) == 1
    assert second[0].success is True
    assert second[0].cache_hit is True
    assert second[0].metrics == first[0].metrics


def test_different_configs_run_in_parallel_in_isolated_workspaces(
    tmp_path: Path,
):
    both_started = threading.Barrier(2, timeout=5)
    workspaces: dict[int, Path] = {}
    workspaces_lock = threading.Lock()

    def synthesize(request: PPARequest, workspace: Path) -> dict:
        width = _width(request.config)
        with workspaces_lock:
            workspaces[width] = workspace
        # This barrier deterministically fails if the runner serializes
        # distinct configurations; no timing sleep is involved.
        both_started.wait()
        return _successful_metrics(request)

    runner = ParameterizedPPARunner(
        cache_dir=tmp_path / "cache",
        synthesize=synthesize,
        max_workers=2,
    )
    results = runner.run([_request(8), _request(16)])

    assert [result.config for result in results] == [
        (("W", 8),),
        (("W", 16),),
    ]
    assert all(result.success for result in results)
    assert set(workspaces) == {8, 16}
    assert workspaces[8] != workspaces[16]
    assert all(path.is_dir() for path in workspaces.values())


def test_cache_key_invalidates_on_every_synthesis_input(tmp_path: Path):
    calls: Counter[tuple] = Counter()

    def synthesize(request: PPARequest, workspace: Path) -> dict:
        key = (
            request.config,
            request.sv_code,
            request.top_module,
            request.flow_fingerprint,
        )
        calls[key] += 1
        return _successful_metrics(request)

    runner = ParameterizedPPARunner(
        cache_dir=tmp_path / "cache",
        synthesize=synthesize,
        max_workers=2,
    )
    baseline = _request(8)

    assert runner.run([baseline])[0].cache_hit is False
    assert runner.run([baseline])[0].cache_hit is True

    changed_rtl = _request(8, sv_code=RTL.replace("assign y = x", "assign y = x & '1"))
    changed_config = _request(9)
    changed_top = _request(8, top_module="dut_other")
    changed_flow = _request(8, flow_fingerprint="orfs-sky130hd-v2")
    for request in [changed_rtl, changed_config, changed_top, changed_flow]:
        result = runner.run([request])[0]
        assert result.success is True
        assert result.cache_hit is False

    # Exact repetition is cached, while RTL, canonical configuration, top,
    # and tool/flow identity each independently invalidate the key.
    assert sum(calls.values()) == 5
    assert all(count == 1 for count in calls.values())


def test_one_configuration_failure_is_isolated_and_not_cached(tmp_path: Path):
    calls: Counter[int] = Counter()
    calls_lock = threading.Lock()

    def synthesize(request: PPARequest, workspace: Path) -> dict:
        width = _width(request.config)
        with calls_lock:
            calls[width] += 1
        if width == 16:
            raise RuntimeError("mock OpenROAD failure for W=16")
        return _successful_metrics(request)

    runner = ParameterizedPPARunner(
        cache_dir=tmp_path / "cache",
        synthesize=synthesize,
        max_workers=3,
    )
    requests = [_request(8), _request(16), _request(32)]

    first = runner.run(requests)

    assert [result.config for result in first] == [
        (("W", 8),),
        (("W", 16),),
        (("W", 32),),
    ]
    assert [result.success for result in first] == [True, False, True]
    assert "mock OpenROAD failure for W=16" in (first[1].error or "")
    assert first[0].metrics["area_um2"] == 8.0
    assert first[2].metrics["area_um2"] == 32.0

    second = runner.run(requests)

    # Successful points are cache hits. Exceptions remain retryable and do
    # not poison either their neighbors or the persistent cache.
    assert [result.cache_hit for result in second] == [True, False, True]
    assert [result.success for result in second] == [True, False, True]
    assert calls == Counter({16: 2, 8: 1, 32: 1})


def test_result_order_is_canonical_not_completion_order(tmp_path: Path):
    width_17_done = threading.Event()
    width_8_done = threading.Event()
    completion_order: list[int] = []
    completion_lock = threading.Lock()

    def synthesize(request: PPARequest, workspace: Path) -> dict:
        width = _width(request.config)
        if width == 8:
            assert width_17_done.wait(timeout=5)
        elif width == 3:
            assert width_8_done.wait(timeout=5)

        with completion_lock:
            completion_order.append(width)
        if width == 17:
            width_17_done.set()
        elif width == 8:
            width_8_done.set()
        return _successful_metrics(request)

    runner = ParameterizedPPARunner(
        cache_dir=tmp_path / "cache",
        synthesize=synthesize,
        max_workers=3,
    )
    results = runner.run([_request(17), _request(3), _request(8)])

    assert completion_order == [17, 8, 3]
    assert [_width(result.config) for result in results] == [3, 8, 17]


def test_finite_sweep_success_never_claims_universal_evidence(tmp_path: Path):
    runner = ParameterizedPPARunner(
        cache_dir=tmp_path / "cache",
        synthesize=lambda request, _workspace: _successful_metrics(request),
        max_workers=2,
    )
    results = runner.run([_request(4), _request(8)])

    assert all(result.success for result in results)
    assert all(
        result.evidence.kind is EvidenceKind.FINITE_SWEEP
        for result in results
    )
    assert all(
        result.evidence.kind is not EvidenceKind.UNIVERSAL_LEAN_THEOREM
        for result in results
    )
    assert all(result.evidence.theorem is None for result in results)

    finite = VerificationEvidence(
        kind=EvidenceKind.FINITE_SWEEP,
        configurations=tuple(result.config for result in results),
        theorem=None,
    )
    assert finite.kind is EvidenceKind.FINITE_SWEEP

    # Universal coverage is a distinct evidence type and requires an explicit
    # Lean theorem artifact; passing every member of a finite sweep is not a
    # proof over all parameter values.
    with pytest.raises(ValueError, match="theorem"):
        VerificationEvidence(
            kind=EvidenceKind.UNIVERSAL_LEAN_THEOREM,
            configurations=finite.configurations,
            theorem=None,
        )

    universal = VerificationEvidence(
        kind=EvidenceKind.UNIVERSAL_LEAN_THEOREM,
        configurations=(),
        theorem="Tests.parameterizedDut_correct_for_all_W",
    )
    assert universal.kind is EvidenceKind.UNIVERSAL_LEAN_THEOREM
    assert universal.theorem == "Tests.parameterizedDut_correct_for_all_W"
