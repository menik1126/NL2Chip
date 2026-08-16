from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

import cktarchon.harness as harness
from cktarchon.harness import AnthropicHarnessRunner, PathGuard


class _CompleteLeanResult:
    passed = True
    complete = True
    summary = "PASS COMPLETE"
    error_text = ""
    errors: list[dict[str, object]] = []
    warnings: list[dict[str, object]] = []
    verilog = "module prob_a; endmodule"


class _RecordingLeanRepl:
    def __init__(self) -> None:
        self.checked_paths: list[Path] = []

    def check_file(self, path: Path) -> _CompleteLeanResult:
        self.checked_paths.append(path)
        return _CompleteLeanResult()


def _runner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    lean_repl: object | None = None,
) -> AnthropicHarnessRunner:
    monkeypatch.setattr(harness, "ensure_runtime_env", lambda: None)
    monkeypatch.setattr(harness.anthropic, "Anthropic", lambda **_: object())
    return AnthropicHarnessRunner(
        project_root=tmp_path,
        prob_id="prob_a",
        model="test-model",
        role="test-role",
        log_base=tmp_path / "logs" / "guardrail",
        system_prompt="test",
        lean_repl=lean_repl,
        local_guardrails=True,
    )


def _text_response() -> SimpleNamespace:
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text="done")],
        stop_reason="end_turn",
        usage=SimpleNamespace(input_tokens=1, output_tokens=1),
    )


def test_marker_hint_has_priority_when_hint_list_is_truncated() -> None:
    source = """
-- CKTARCHON_IMPLEMENTATION_REQUIRED
def candidate {W : Nat} :=
  let bad := Signal.pure True : Signal dom Bool
  let a := Nat.clog2 W
  let b := Nat.log2 W
  let c := Signal.mapBV id input
  let d := List.enum values
  if W = 1 then sorry else admit
"""

    hints = harness.sparkle_candidate_hints(source)

    assert len(hints) == 6
    assert hints[0].startswith("The compile-safe scaffold is not a solution")
    assert "CKTARCHON_IMPLEMENTATION_REQUIRED" in hints[0]
    numbered_hints = [
        line
        for line in harness.format_sparkle_candidate_hints(source).splitlines()
        if line[:1].isdigit()
    ]
    assert numbered_hints[0].startswith("1. The compile-safe scaffold")


def test_local_diagnostics_are_compact_deduplicated_and_do_not_dump_verilog(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _runner(tmp_path, monkeypatch)
    errors = [
        {"pos": {"line": 1, "column": 2}, "data": "duplicate error"},
        {"pos": {"line": 1, "column": 2}, "data": "duplicate   error"},
        *[
            {"pos": {"line": line, "column": 1}, "data": f"unique error {line}"}
            for line in range(2, 8)
        ],
    ]
    warnings = [
        {"pos": {"line": line, "column": 3}, "data": f"warning {line}"}
        for line in range(10, 15)
    ]
    verilog = "module candidate;\n" + ("wire noisy;\n" * 100) + "endmodule"
    result = SimpleNamespace(
        passed=False,
        complete=False,
        elapsed=0.1,
        errors=errors,
        warnings=warnings,
        error_text="FULL DUPLICATED ERROR TEXT MUST BE OMITTED",
        verilog=verilog,
    )

    formatted = runner._format_lean_result(result)

    assert "FULL DUPLICATED ERROR TEXT MUST BE OMITTED" not in formatted
    assert formatted.count("[error ") == 4
    assert formatted.count("duplicate error") == 1
    assert formatted.count("[warning ") == 3
    assert f"generated Verilog is available ({len(verilog)} chars)" in formatted
    assert "wire noisy" not in formatted


def test_parallel_candidate_isolation_canonicalizes_absolute_and_dotdot_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generated = tmp_path / "Generated"
    generated.mkdir()
    own = generated / "prob_a.lean"
    other = generated / "prob_b.lean"
    own.write_text(
        "def prob_a := 1\n#synthesizeVerilog prob_a\n",
        encoding="utf-8",
    )
    other.write_text("secret unverified candidate\n", encoding="utf-8")
    guard = PathGuard(tmp_path, "prob_a")
    other_dotdot = Path("..") / tmp_path.name / "Generated" / "prob_b.lean"

    assert not guard.is_parallel_generated_candidate(own)
    assert guard.is_parallel_generated_candidate(other)
    assert guard.is_parallel_generated_candidate(other_dotdot)

    repl = _RecordingLeanRepl()
    runner = _runner(tmp_path, monkeypatch, lean_repl=repl)
    assert "another worker" in runner._read_file(str(other))
    assert "another worker" in runner._read_file(str(other_dotdot))
    assert "secret unverified candidate" not in runner._read_file(str(other))
    assert "def prob_a" in runner._read_file(str(own))

    assert "cannot inspect another worker" in runner._lean_check(path=str(other))
    assert "cannot inspect another worker" in runner._lean_check(path=str(other_dotdot))
    assert repl.checked_paths == []
    assert runner._lean_check(path=str(own)).startswith("PASS COMPLETE")
    assert repl.checked_paths == [own.resolve()]
    assert runner._last_complete_code is not None
    assert "#synthesizeVerilog prob_a" in runner._last_complete_code


def test_write_allowlist_uses_canonical_target_paths(tmp_path: Path) -> None:
    generated = tmp_path / "Generated"
    generated.mkdir()
    other = generated / "prob_b.lean"
    other.write_text("unverified\n", encoding="utf-8")
    symlink_alias = generated / "prob_a_alias.lean"
    symlink_alias.symlink_to(other)
    guard = PathGuard(tmp_path, "prob_a")
    own = generated / "prob_a.lean"
    own_dotdot = Path("..") / tmp_path.name / "Generated" / "prob_a.lean"
    wildcard_traversal = "Generated/prob_a_scratch/../prob_b.lean"
    workdir_traversal = "cktarchon_work/prob_a/nested/../../prob_b/stolen.txt"

    assert guard.is_write_allowed(own)
    assert guard.is_write_allowed(own_dotdot)
    assert guard.require_write_allowed(own_dotdot) == own.resolve()
    assert not guard.is_write_allowed(other)
    assert not guard.is_write_allowed(wildcard_traversal)
    assert not guard.is_write_allowed(workdir_traversal)
    assert not guard.is_write_allowed(symlink_alias)
    with pytest.raises(PermissionError, match="Write denied"):
        guard.require_write_allowed(wildcard_traversal)



def test_local_guardrails_reject_generated_prefix_collisions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _runner(tmp_path, monkeypatch)
    collision = "Generated/prob_a_retry.lean"
    work_file = "cktarchon_work/prob_a/retry.lean"

    assert runner.guard.strict_exact_generated
    assert runner.guard.is_write_allowed("Generated/prob_a.lean")
    assert not runner.guard.is_write_allowed(collision)
    assert runner.guard.is_write_allowed(work_file)
    with pytest.raises(PermissionError, match="Write denied"):
        runner._write_file(collision, "unverified scratch candidate")

    default_guard = PathGuard(tmp_path, "prob_a")
    assert default_guard.is_write_allowed(collision)

def test_extra_write_globs_match_only_canonical_project_relative_paths(
    tmp_path: Path,
) -> None:
    guard = PathGuard(
        tmp_path,
        "prob_a",
        extra_write_globs=("exports/*.sv",),
    )
    export = tmp_path / "exports" / "candidate.sv"

    assert guard.is_write_allowed(export)
    assert guard.is_write_allowed("exports/scratch/../candidate.sv")
    assert not guard.is_write_allowed("exports/scratch/../../Generated/prob_b.lean")
    assert not guard.is_write_allowed(tmp_path.parent / "outside.sv")


def test_local_run_nudges_after_prose_only_turn_without_checkpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _runner(tmp_path, monkeypatch)
    calls: list[list[dict[str, object]]] = []

    def create_message(**kwargs: object) -> SimpleNamespace:
        calls.append(list(kwargs["messages"]))  # type: ignore[index]
        return _text_response()

    monkeypatch.setattr(runner, "_create_message_with_retries", create_message)

    stats = runner.run("implement it", max_turns=2)

    assert stats.turns == 2
    assert len(calls) == 2
    assert calls[1][-1]["role"] == "user"
    assert "No tool was used" in str(calls[1][-1]["content"])
    assert "run lean_check now" in str(calls[1][-1]["content"])


def test_local_run_allows_prose_only_end_after_verified_checkpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _runner(tmp_path, monkeypatch)
    runner._last_complete_code = "def prob_a := 1\n#synthesizeVerilog prob_a\n"
    calls = 0

    def create_message(**_: object) -> SimpleNamespace:
        nonlocal calls
        calls += 1
        return _text_response()

    monkeypatch.setattr(runner, "_create_message_with_retries", create_message)

    stats = runner.run("finish", max_turns=3)

    assert stats.turns == 1
    assert calls == 1


def test_marked_scaffold_is_not_a_verified_checkpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _runner(tmp_path, monkeypatch)

    runner._remember_complete_candidate(
        "-- CKTARCHON_IMPLEMENTATION_REQUIRED\n"
        "def prob_a := 1\n"
        "#synthesizeVerilog prob_a\n",
        _CompleteLeanResult(),
    )

    assert runner._last_complete_code is None
    assert not (tmp_path / "Generated" / "prob_a.lean").exists()


def test_configure_anthropic_credentials_loads_then_scrubs_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth_token = "unit-test-auth-token"
    base_url = "https://unit-test.invalid"
    monkeypatch.setattr(harness, "_ANTHROPIC_API_KEY", None)
    monkeypatch.setattr(harness, "_ANTHROPIC_BASE_URL", None)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", auth_token)
    monkeypatch.setenv("ANTHROPIC_BASE_URL", base_url)
    monkeypatch.setenv("UNRELATED_SETTING", "preserved")

    harness.configure_anthropic_credentials_from_env()

    assert harness._ANTHROPIC_API_KEY == auth_token
    assert harness._ANTHROPIC_BASE_URL == base_url
    assert "ANTHROPIC_API_KEY" not in os.environ
    assert "ANTHROPIC_AUTH_TOKEN" not in os.environ
    assert "ANTHROPIC_BASE_URL" not in os.environ
    assert os.environ["UNRELATED_SETTING"] == "preserved"


@pytest.mark.parametrize(
    "command",
    [
        "rg CKTARCHON_IMPLEMENTATION_REQUIRED .",
        "cat Gen*/other.lean",
    ],
)
def test_local_guardrails_reject_every_raw_bash_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    command: str,
) -> None:
    def unexpected_subprocess(*_: object, **__: object) -> None:
        pytest.fail("local guardrails must not launch a bash subprocess")

    monkeypatch.setattr(harness.subprocess, "run", unexpected_subprocess)
    runner = _runner(tmp_path, monkeypatch)

    result = runner._bash(command)

    assert result.startswith("Error: bash is disabled in local guardrails mode")


@pytest.mark.parametrize(
    ("local_guardrails", "bash_expected"),
    [(True, False), (False, True)],
)
def test_anthropic_request_tool_schema_filters_bash_only_in_local_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    local_guardrails: bool,
    bash_expected: bool,
) -> None:
    runner = _runner(tmp_path, monkeypatch)
    runner.local_guardrails = local_guardrails
    requests: list[dict[str, object]] = []

    def create_message(**kwargs: object) -> SimpleNamespace:
        requests.append(kwargs)
        return _text_response()

    monkeypatch.setattr(runner, "_create_message_with_retries", create_message)

    runner.run("implement it", max_turns=1)

    tool_names = {tool["name"] for tool in requests[0]["tools"]}  # type: ignore[index]
    assert ("bash" in tool_names) is bash_expected
    assert {"read_file", "edit_file", "lean_check"} <= tool_names
