from __future__ import annotations

import json
import os
import shutil
import sys
from types import SimpleNamespace
from pathlib import Path

import pytest

from cktlean.codex_runner import CodexAgentHarnessRunner
from cktlean.env import ensure_runtime_env, model_alias
from cktlean.harness import AnthropicHarnessRunner, PathGuard
from cktlean.logs import append_jsonl, normalize_token_usage, parse_agent_log
from cktlean.run import (
    agent_visible_problem_info,
    already_done,
    build_fresh_candidate_prompt,
    build_system_prompt,
    candidate_delivery_guard,
    clear_generated_target,
    evaluate_with_infrastructure_retries,
    feedback_turn_budget_after_generation,
    finalize_guided_candidate,
    generation_turn_limit,
    is_feedback_repairable as is_main_feedback_repairable,
    parse_args,
    runner_turn_limit,
)
from cktlean.run_verilog import (
    is_feedback_repairable,
    make_runner as make_verilog_runner,
    parse_args as parse_verilog_args,
)
from cktlean.search_strategy import (
    CandidateTracker,
    TurnBudget,
    build_self_test_planner_prompt,
    parse_self_test_guide,
    run_generated_self_test,
    validate_self_test_guide,
)
from cktlean.responses_chat_proxy import (
    chat_response_to_responses_events,
    events_to_sse,
    responses_request_to_chat_request,
)


def test_public_only_problem_info_removes_private_cvdp_context():
    info = SimpleNamespace(
        metadata={
            "dataset": "cvdp",
            "input_context_files": {"public.sv": "module public_input; endmodule"},
            "harness_files": {"test.py": "golden output implementation"},
            "cvdp_row": {"private": True},
            "verilog_sources": ["hidden.sv"],
            "categories": ["hard"],
        },
        ref_code="golden output implementation",
    )

    public_info = agent_visible_problem_info(info, hide_cvdp_harness=True)

    assert public_info is not info
    assert "public_input" in public_info.ref_code
    assert "golden output implementation" not in public_info.ref_code
    assert "harness_files" not in public_info.metadata
    assert "cvdp_row" not in public_info.metadata
    assert "verilog_sources" not in public_info.metadata
    assert public_info.metadata["categories"] == ["hard"]
    assert public_info.metadata["agent_input_policy"] == "cvdp-public-only"


def test_public_only_anthropic_runner_omits_bash_tool(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    runner = _anthropic_runner(tmp_path, monkeypatch)
    runner.allow_bash_tool = False

    assert "bash" not in {tool["name"] for tool in runner._model_tools()}
    assert "lean_check" in {tool["name"] for tool in runner._model_tools()}


def test_skill_requires_outputs_outside_signal_loop():
    skill = (Path(__file__).resolve().parents[1] / "agent" / "skill.txt").read_text(
        encoding="utf-8"
    )
    prompt = build_system_prompt(skill, "prob_a", prompt_profile="cvdp-skill-fewshot")

    assert "`Signal.loop` returns feedback state, not final interface outputs" in prompt
    assert "Never return\n  a packed output bundle from the same loop body" in prompt
    assert "A derived Nat width alias is\n  supported" in prompt
    assert "exampleNativePointer" in prompt
    assert "Use `Signal.pure` only with an explicit" in prompt
    assert "Signal.mux cond (Signal.pure false) (Signal.pure true)" in prompt
    assert "Do not write `let v := zext x : Signal dom (BitVec 8)`" in prompt
    assert "Signal.loop fun (q : Signal dom (BitVec W)) =>" in prompt
    assert "`Signal.const` and `Signal.not` are not Sparkle APIs" in prompt
    assert "Do not use Lean `if`/`match` to select hardware behavior" in prompt
    assert "There are two legitimate full conventions" in prompt
    assert "Capture each channel separately" in prompt
    assert "a timer\n  that counts after a running countdown" in prompt


def test_fresh_candidate_prompt_requires_source_before_exploration():
    guard = candidate_delivery_guard("prob_a", 10)

    assert "at most 10 turns" in guard
    assert "first tool action MUST create" in guard
    assert "Generated/prob_a.lean" in guard
    assert "`write_file`, `apply_patch`, or the equivalent" in guard
    assert "do not call `glob`, `grep`" in guard

    fake_search = SimpleNamespace(
        build_user_message=lambda *args, **kwargs: "## Base Problem",
        summarize_eval_result=lambda result: "prior result",
        compact_repair_feedback=lambda feedback: f"compact: {feedback}",
        summarize_recent_attempts=lambda attempts: "prior attempts",
    )
    prompt = build_fresh_candidate_prompt(
        search=fake_search,
        prob_id="prob_a",
        info=None,
        dataset_name="cvdp",
        has_repl=True,
        candidate_id=2,
        guide_text="guide",
        prior_result={},
        prior_feedback="Expected 9, got 0",
        recent_attempts=[],
        latest_self_test=None,
        turn_limit=10,
    )

    assert prompt.startswith("## Mandatory Candidate Delivery")
    assert prompt.index("first tool action MUST") < prompt.index("## Base Problem")
    assert "### Failure Evidence To Avoid" in prompt
    assert "compact: Expected 9, got 0" in prompt


def test_model_alias_sonnet_45():
    assert model_alias("claude-sonnet-4.5") == "claude-sonnet-4-5-20250929"


def test_sim_feedback_rewrite_routes_to_fresh_candidate_search(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(sys, "argv", [
        "run.py",
        "--results-dir",
        "out",
        "--sim-feedback",
        "--max-turns",
        "10",
        "--sim-feedback-turn-budget",
        "90",
        "--sim-feedback-rewrite-patience",
        "2",
        "--sim-feedback-max-candidates",
        "3",
    ])
    args = parse_args()

    assert args.guided_search
    assert args.disable_guided_self_test
    assert args.candidate_stagnation_patience == 2
    assert args.candidate_search_max == 3
    assert args.max_turns + args.sim_feedback_turn_budget == 100

def test_sim_feedback_rewrite_is_opt_in(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(sys, "argv", ["run.py", "--results-dir", "out", "--sim-feedback"])
    args = parse_args()

    assert not args.guided_search
    assert not args.disable_guided_self_test


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (
            {
                "compile_pass": False,
                "sim_status": "compile_fail",
                "detail": "iverilog compile failed: syntax error",
            },
            True,
        ),
        (
            {
                "compile_pass": True,
                "sim_status": "sim_error",
                "detail": "CVDP local Verilog compile error",
            },
            True,
        ),
        (
            {
                "compile_pass": True,
                "sim_status": "sim_fail",
                "detail": "got=0 expected=1",
            },
            False,
        ),
        (
            {
                "compile_pass": True,
                "sim_status": "sim_error",
                "detail": "simulation timeout",
            },
            False,
        ),
        (
            {
                "compile_pass": False,
                "sim_status": "sim_error",
                "detail": "CVDP local simulator/tool error: iverilog executable not found",
            },
            False,
        ),
    ],
)
def test_direct_verilog_compile_only_feedback_filter(result: dict, expected: bool):
    assert is_feedback_repairable(result, "compile-only") is expected


def test_direct_verilog_compile_only_cli(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(sys, "argv", [
        "run_verilog.py",
        "--results-dir",
        "out",
        "--sim-feedback",
        "--feedback-mode",
        "compile-only",
    ])

    args = parse_verilog_args()

    assert args.sim_feedback
    assert args.feedback_mode == "compile-only"
    assert args.model == "gpt-5.6-sol"
    assert args.codex_effort == "ultra"
    assert args.key_env is None


def test_direct_verilog_runner_uses_native_codex(tmp_path: Path):
    args = SimpleNamespace(
        model="gpt-5.6-sol",
        prompt_profile="archon",
        archon_src=str(tmp_path / "archon"),
        codex_bin=None,
        codex_effort="ultra",
        codex_sandbox="danger-full-access",
        codex_idle_timeout=900.0,
        codex_max_attempts=3,
        api_timeout=300.0,
        dataset="cvdp",
    )
    info = SimpleNamespace(design_name="dut")

    runner = make_verilog_runner(
        args,
        "prob_a",
        "verilog-generator",
        tmp_path / "generate",
        info,
    )

    assert isinstance(runner, CodexAgentHarnessRunner)
    assert runner.model == "gpt-5.6-sol"
    assert runner.effort == "ultra"
    assert runner.direct_verilog
    assert runner.native_login_only
    assert runner.public_only
    assert not runner.auto_chat_proxy


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (
            {"failure_stage": "lean_elaboration", "compile_pass": False, "sim_status": "not_run"},
            True,
        ),
        (
            {"failure_stage": "parameter_contract", "compile_pass": True, "sim_status": "not_run"},
            True,
        ),
        (
            {"failure_stage": "verilog_elaboration", "compile_pass": True, "sim_status": "sim_error"},
            True,
        ),
        (
            {"failure_stage": "simulation_mismatch", "compile_pass": True, "sim_status": "sim_fail"},
            False,
        ),
        (
            {"failure_stage": "simulation_error", "compile_pass": True, "sim_status": "sim_error"},
            False,
        ),
        (
            {"failure_stage": "infrastructure", "compile_pass": False, "sim_status": "sim_error"},
            False,
        ),
    ],
)
def test_main_compile_only_feedback_filter(result: dict, expected: bool):
    assert is_main_feedback_repairable(result, "compile-only") is expected


def test_main_compile_only_cli(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(sys, "argv", [
        "run.py",
        "--results-dir",
        "out",
        "--sim-feedback",
        "--feedback-mode",
        "compile-only",
        "--total-turn-budget",
        "100",
        "--generation-turn-cap",
        "40",
    ])

    args = parse_args()

    assert args.sim_feedback
    assert args.feedback_mode == "compile-only"


def test_shared_total_turn_budget_reserves_repair_capacity(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(sys, "argv", [
        "run.py",
        "--results-dir",
        "out",
        "--harness",
        "codex-agent",
        "--sim-feedback",
        "--total-turn-budget",
        "100",
        "--generation-turn-cap",
        "40",
        "--sim-feedback-turns-per-iter",
        "10",
    ])

    args = parse_args()

    assert generation_turn_limit(args) == 40
    assert runner_turn_limit(args, generation_turn_limit(args)) == 39
    assert runner_turn_limit(args, 10) == 9
    assert runner_turn_limit(args, 1) == 0
    assert feedback_turn_budget_after_generation(args, generation_turns=18) == 82
    assert feedback_turn_budget_after_generation(args, generation_turns=40) == 60


def test_shared_total_turn_budget_rejects_conflicting_legacy_budget(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(sys, "argv", [
        "run.py",
        "--results-dir",
        "out",
        "--sim-feedback",
        "--total-turn-budget",
        "100",
        "--generation-turn-cap",
        "40",
        "--sim-feedback-turn-budget",
        "90",
    ])

    with pytest.raises(SystemExit):
        parse_args()


def test_cvdp_harness_profile_defaults_and_official_ablation(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(sys, "argv", ["run.py", "--results-dir", "out"])
    assert parse_args().cvdp_harness_profile == "race-safe-v1"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run.py",
            "--results-dir",
            "out",
            "--cvdp-harness-profile",
            "official",
        ],
    )
    assert parse_args().cvdp_harness_profile == "official"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_verilog.py",
            "--results-dir",
            "out",
            "--cvdp-harness-profile",
            "official",
        ],
    )
    assert parse_verilog_args().cvdp_harness_profile == "official"


def test_runtime_env_prioritizes_guarded_iverilog_wrapper(monkeypatch: pytest.MonkeyPatch):
    raw_iverilog_bin = "/opt/toolcache/iverilog/usr/bin"
    guarded_bin = str(Path.home() / ".local" / "bin")
    original_exists = Path.exists
    monkeypatch.setattr(
        "cktlean.env.Path.exists",
        lambda path: str(path) in {raw_iverilog_bin, guarded_bin} or original_exists(path),
    )
    monkeypatch.setenv("PATH", f"{raw_iverilog_bin}:/usr/bin")

    ensure_runtime_env()

    parts = os.environ["PATH"].split(":")
    assert parts.index(guarded_bin) < parts.index(raw_iverilog_bin)


def test_runtime_env_replaces_stale_host_specific_lake_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    local_lake = tmp_path / "lake"
    local_lake.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    local_lake.chmod(0o755)
    monkeypatch.setenv("LAKE_PATH", "/home/other-host/.elan/bin/lake")
    monkeypatch.setattr(
        "cktlean.env.shutil.which",
        lambda name, path=None: str(local_lake) if name == "lake" else None,
    )

    ensure_runtime_env()

    assert os.environ["LAKE_PATH"] == str(local_lake)


def test_evaluator_retries_infrastructure_without_model_turns(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    class FakeEvaluator:
        def __init__(self):
            self.calls = 0

        def evaluate(self, prob_id, run_dir, problem_info=None, skip_sim=False):
            self.calls += 1
            if self.calls < 3:
                return {
                    "prob_id": prob_id,
                    "failure_stage": "infrastructure",
                    "detail": "temporary simulator unavailable",
                }
            return {
                "prob_id": prob_id,
                "failure_stage": None,
                "compile_pass": True,
                "sim_status": "sim_pass",
            }

    evaluator = FakeEvaluator()
    monkeypatch.setattr("cktlean.run.time.sleep", lambda _: None)

    result = evaluate_with_infrastructure_retries(
        evaluator,
        "prob_a",
        tmp_path,
        SimpleNamespace(),
    )

    assert evaluator.calls == 3
    assert result["sim_status"] == "sim_pass"
    assert result["infrastructure_eval_attempts"] == 3
    events = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text().splitlines()]
    assert [event["attempt"] for event in events] == [1, 2]


def test_path_guard_allows_only_problem_outputs(tmp_path: Path):
    guard = PathGuard(tmp_path, "prob_a")
    assert guard.is_write_allowed("Generated/prob_a.lean")
    assert guard.is_write_allowed("cktlean_work/prob_a/notes.json")
    assert not guard.is_write_allowed("Generated/prob_b.lean")
    assert not guard.is_write_allowed("agent/search.py")


def test_path_guard_hides_peer_artifacts_and_credentials(tmp_path: Path):
    guard = PathGuard(tmp_path, "prob_a")

    assert guard.is_read_allowed("Generated")
    assert guard.is_read_allowed("Generated/prob_a.lean")
    assert guard.is_read_allowed("Generated/prob_a_helper.lean")
    assert not guard.is_read_allowed("Generated/prob_b.lean")
    assert guard.is_read_allowed("cktlean_work")
    assert guard.is_read_allowed("cktlean_work/prob_a/notes.txt")
    assert not guard.is_read_allowed("cktlean_work/prob_b/notes.txt")
    assert not guard.is_read_allowed("key.env")
    assert not guard.is_read_allowed(".git/config")
    assert guard.is_read_allowed("docs/SignalDSL_Syntax.md")


def test_path_guard_blocks_shell_file_discovery_and_peer_modules(tmp_path: Path):
    guard = PathGuard(tmp_path, "prob_a")

    assert guard.bash_access_error("find Generated -name '*.lean'")
    assert guard.bash_access_error("cat Generated/prob_a.lean")
    assert guard.bash_access_error("lake build Generated.prob_b")
    assert guard.bash_access_error("lake build Generated.prob_a") is None
    assert guard.bash_access_error("lake build Sparkle") is None


class _CompleteLeanResult:
    passed = True
    complete = True
    summary = "PASS COMPLETE"
    error_text = ""
    errors = []
    warnings = []
    verilog = "module prob_a; endmodule"


class _CompleteLeanRepl:
    last_code = ""

    def check_code(self, code: str):
        self.last_code = code
        assert "def prob_a" in code
        return _CompleteLeanResult()

    def check_file(self, path: Path):
        assert "def prob_a" in path.read_text(encoding="utf-8")
        return _CompleteLeanResult()


class _LeanOnlyCompleteResult(_CompleteLeanResult):
    verilog = ""


class _LeanOnlyCompleteRepl:
    def check_code(self, code: str):
        return _LeanOnlyCompleteResult()


def _anthropic_runner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AnthropicHarnessRunner:
    monkeypatch.setattr("cktlean.harness.ensure_runtime_env", lambda: None)
    monkeypatch.setattr("cktlean.harness.anthropic.Anthropic", lambda **_: object())
    return AnthropicHarnessRunner(
        project_root=tmp_path,
        prob_id="prob_a",
        model="test-model",
        role="test-role",
        log_base=tmp_path / "logs" / "test",
        system_prompt="test",
        lean_repl=_CompleteLeanRepl(),
    )


def test_harness_logs_anthropic_cache_usage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    runner = _anthropic_runner(tmp_path, monkeypatch)
    response = SimpleNamespace(
        usage=SimpleNamespace(
            input_tokens=3,
            cache_creation_input_tokens=1634,
            cache_read_input_tokens=0,
            output_tokens=16,
        ),
        content=[],
        stop_reason="end_turn",
    )
    monkeypatch.setattr(runner, "_create_message_with_retries", lambda **_: response)

    stats = runner.run("test prompt", max_turns=1)

    assert stats.input_tokens == 1637
    assert stats.uncached_input_tokens == 3
    assert stats.cache_creation_input_tokens == 1634
    assert stats.cache_read_input_tokens == 0
    rows = [json.loads(line) for line in runner.log_path.read_text().splitlines()]
    assistant = next(row for row in rows if row["event"] == "assistant")
    session_end = next(row for row in rows if row["event"] == "session_end")
    assert assistant["usage"]["input_tokens_total"] == 1637
    assert session_end["input_tokens_total"] == 1637
    assert session_end["cache_creation_input_tokens"] == 1634


def test_harness_read_tools_expose_only_current_task(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    runner = _anthropic_runner(tmp_path, monkeypatch)
    generated = tmp_path / "Generated"
    generated.mkdir()
    (generated / "prob_a.lean").write_text("own marker\n", encoding="utf-8")
    (generated / "prob_b.lean").write_text("peer secret marker\n", encoding="utf-8")

    assert runner._read_file("Generated/prob_a.lean") == "own marker\n"
    denied = runner._execute_tool("read_file", {"path": "Generated/prob_b.lean"})
    assert "Read denied" in denied
    assert "peer secret marker" not in runner._grep("marker", ".")
    assert runner._grep("own marker", ".").startswith("Generated/prob_a.lean:1:")

    visible_glob = runner._glob("Generated/*.lean")
    visible_listing = runner._list_directory("Generated")
    assert "prob_a.lean" in visible_glob
    assert "prob_b.lean" not in visible_glob
    assert visible_listing == "prob_a.lean"


def test_harness_bash_subprocess_does_not_receive_secret_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    runner = _anthropic_runner(tmp_path, monkeypatch)
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "must-not-reach-subprocess")
    captured = {}

    def fake_run(*args, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(stdout="ok\n", stderr="", returncode=0)

    monkeypatch.setattr("cktlean.harness.subprocess.run", fake_run)
    assert runner._bash("lake build Sparkle") == "ok\n"
    assert "ANTHROPIC_AUTH_TOKEN" not in captured["env"]


def test_harness_autosaves_complete_inline_lean_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    runner = _anthropic_runner(tmp_path, monkeypatch)
    runner._tool_sequence = 3

    runner._lean_check_code("def prob_a := 1\n#synthesizeVerilog prob_a")

    target = tmp_path / "Generated" / "prob_a.lean"
    assert target.exists()
    assert target.read_text(encoding="utf-8").startswith("import Sparkle\n")
    assert "#synthesizeVerilog prob_a" in target.read_text(encoding="utf-8")
    assert "auto_saved_candidate" in runner.log_path.read_text(encoding="utf-8")


def test_inline_lean_check_strips_file_imports_only_for_cached_repl(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    runner = _anthropic_runner(tmp_path, monkeypatch)
    source = (
        "import Sparkle\n"
        "import Sparkle.Compiler.Elab\n\n"
        "open Sparkle.Core.Signal\n\n"
        "def prob_a := 1\n"
        "#synthesizeVerilog prob_a\n"
    )

    runner._lean_check_code(source)

    assert "import Sparkle" not in runner.lean_repl.last_code
    assert "open Sparkle.Core.Signal" in runner.lean_repl.last_code
    assert (tmp_path / "Generated" / "prob_a.lean").read_text(encoding="utf-8") == source


def test_harness_does_not_autosave_lean_only_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    runner = _anthropic_runner(tmp_path, monkeypatch)
    runner.lean_repl = _LeanOnlyCompleteRepl()
    runner._tool_sequence = 3

    runner._lean_check_code("def prob_a := 1\n#synthesizeVerilog prob_a")

    assert not (tmp_path / "Generated" / "prob_a.lean").exists()


def test_harness_restores_last_complete_candidate_after_unchecked_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    runner = _anthropic_runner(tmp_path, monkeypatch)
    runner._tool_sequence = 3
    runner._lean_check_code("def prob_a := 1\n#synthesizeVerilog prob_a")
    runner._tool_sequence = 4
    runner._write_file("Generated/prob_a.lean", "newer explicit candidate\n")

    runner._autosave_last_complete_candidate()

    target = tmp_path / "Generated" / "prob_a.lean"
    text = target.read_text(encoding="utf-8")
    assert "#synthesizeVerilog prob_a" in text
    assert "newer explicit candidate" not in text


def test_harness_path_check_updates_compile_safe_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    runner = _anthropic_runner(tmp_path, monkeypatch)
    runner._tool_sequence = 3
    runner._lean_check_code("def prob_a := 1\n#synthesizeVerilog prob_a")
    runner._tool_sequence = 4
    runner._write_file(
        "Generated/prob_a.lean",
        "import Sparkle\n\ndef prob_a := 2\n#synthesizeVerilog prob_a\n",
    )
    runner._tool_sequence = 5
    runner._lean_check(path="Generated/prob_a.lean")
    runner._tool_sequence = 6
    runner._write_file("Generated/prob_a.lean", "unchecked broken candidate\n")

    runner._autosave_last_complete_candidate()

    text = (tmp_path / "Generated" / "prob_a.lean").read_text(encoding="utf-8")
    assert "def prob_a := 2" in text
    assert "unchecked broken candidate" not in text


def test_harness_seeds_existing_compile_safe_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    runner = _anthropic_runner(tmp_path, monkeypatch)
    target = tmp_path / "Generated" / "prob_a.lean"
    target.parent.mkdir(parents=True)
    target.write_text(
        "import Sparkle\n\ndef prob_a := 1\n#synthesizeVerilog prob_a\n",
        encoding="utf-8",
    )

    runner._seed_compile_safe_candidate()
    runner._tool_sequence = 1
    runner._write_file("Generated/prob_a.lean", "unchecked broken candidate\n")
    runner._autosave_last_complete_candidate()

    text = target.read_text(encoding="utf-8")
    assert "#synthesizeVerilog prob_a" in text
    assert "unchecked broken candidate" not in text
    assert "seeded_compile_safe_candidate" in runner.log_path.read_text(encoding="utf-8")


def test_harness_unwraps_valid_raw_tool_arguments(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    runner = _anthropic_runner(tmp_path, monkeypatch)

    result = runner._execute_tool(
        "write_file",
        {
            "raw": json.dumps(
                {
                    "path": "Generated/prob_a.lean",
                    "content": "def prob_a := 1\n",
                }
            )
        },
    )

    assert result.startswith("Wrote ")
    assert (tmp_path / "Generated" / "prob_a.lean").read_text() == "def prob_a := 1\n"


def test_harness_reports_truncated_raw_tool_arguments(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    runner = _anthropic_runner(tmp_path, monkeypatch)

    result = runner._execute_tool(
        "write_file",
        {"raw": '{"path": "Generated/prob_a.lean"'},
    )

    assert "malformed write_file tool arguments" in result
    assert "truncated or invalid" in result
    assert "missing required field(s): path, content" in result
    assert "smaller edit_file operations" in result
    assert not (tmp_path / "Generated" / "prob_a.lean").exists()


def test_parse_agent_log(tmp_path: Path):
    log = tmp_path / "agent.jsonl"
    append_jsonl(log, {"event": "assistant", "turn": 0, "usage": {"input_tokens": 10, "output_tokens": 2}})
    append_jsonl(log, {"event": "tool_call", "name": "lean_check", "input": {}})
    append_jsonl(log, {"event": "session_end", "turns": 1})
    stats = parse_agent_log(log)
    assert stats.turns == 1
    assert stats.input_tokens == 10
    assert stats.uncached_input_tokens == 10
    assert stats.cache_creation_input_tokens == 0
    assert stats.cache_read_input_tokens == 0
    assert stats.output_tokens == 2
    assert stats.compile_checks == 1
    assert stats.tool_counts == {"lean_check": 1}


def test_parse_codex_archon_log(tmp_path: Path):
    log = tmp_path / "codex.jsonl"
    append_jsonl(log, {"event": "session_meta", "session_id": "thread-1"})
    append_jsonl(log, {"event": "tool_call", "tool": "Bash", "input": {"command": "python -m cktlean.tools lean-check Generated/prob.lean"}})
    append_jsonl(log, {"event": "turn_usage", "input_tokens": 7, "cache_read_input_tokens": 3, "output_tokens": 2})
    append_jsonl(log, {"event": "session_end", "num_turns": 4, "input_tokens_total": 10, "output_tokens": 2})
    stats = parse_agent_log(log)
    assert stats.session_id == "thread-1"
    assert stats.turns == 4
    assert stats.input_tokens == 10
    assert stats.uncached_input_tokens == 7
    assert stats.cache_creation_input_tokens == 0
    assert stats.cache_read_input_tokens == 3
    assert stats.output_tokens == 2
    assert stats.compile_checks == 1
    assert stats.tool_counts == {"Bash": 1}


def test_parse_codex_log_uses_the_enforced_action_unit(tmp_path: Path):
    log = tmp_path / "codex-actions.jsonl"
    append_jsonl(log, {"event": "session_start", "runner": "codex"})
    append_jsonl(log, {"event": "thinking", "content": "private reasoning"})
    append_jsonl(log, {"event": "text", "content": "I will check it."})
    append_jsonl(log, {"event": "tool_call", "tool": "Bash", "input": {}})
    append_jsonl(log, {"event": "tool_result", "content": "ok"})
    append_jsonl(
        log,
        {
            "event": "session_end",
            "runner": "codex",
            "num_items": 9,
            "input_tokens_total": 100,
            "output_tokens": 10,
        },
    )

    stats = parse_agent_log(log)

    assert stats.turns == 2
    assert stats.token_accounting_complete


def test_codex_budget_stop_without_recovered_usage_is_marked_incomplete(
    tmp_path: Path,
):
    log = tmp_path / "codex-incomplete.jsonl"
    append_jsonl(log, {"event": "session_start", "runner": "codex"})
    append_jsonl(log, {"event": "tool_call", "tool": "Edit", "input": {}})
    append_jsonl(log, {"event": "cktlean_budget_exceeded"})
    append_jsonl(
        log,
        {
            "event": "session_end",
            "runner": "codex",
            "num_items": 1,
            "input_tokens_total": 0,
            "output_tokens": 0,
        },
    )

    stats = parse_agent_log(log)

    assert stats.turns == 1
    assert not stats.token_accounting_complete


def test_anthropic_cache_usage_is_counted_once(tmp_path: Path):
    first = normalize_token_usage(
        SimpleNamespace(
            input_tokens=3,
            cache_creation_input_tokens=1634,
            cache_read_input_tokens=0,
            output_tokens=16,
        )
    )
    assert first == {
        "input_tokens": 3,
        "uncached_input_tokens": 3,
        "cache_creation_input_tokens": 1634,
        "cache_read_input_tokens": 0,
        "input_tokens_total": 1637,
        "output_tokens": 16,
    }

    log = tmp_path / "anthropic-cache.jsonl"
    append_jsonl(log, {"event": "assistant", "turn": 0, "usage": first})
    append_jsonl(
        log,
        {
            "event": "assistant",
            "turn": 1,
            "usage": {
                "input_tokens": 3,
                "uncached_input_tokens": 3,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 2000,
                "input_tokens_total": 2003,
                "output_tokens": 8,
            },
        },
    )
    aggregate = {
        "input_tokens": 6,
        "uncached_input_tokens": 6,
        "cache_creation_input_tokens": 1634,
        "cache_read_input_tokens": 2000,
        "input_tokens_total": 3640,
        "output_tokens": 24,
    }
    append_jsonl(
        log,
        {
            "event": "session_end",
            "turns": 2,
            "usage": aggregate,
            **aggregate,
        },
    )

    stats = parse_agent_log(log)
    assert stats.turns == 2
    assert stats.input_tokens == 3640
    assert stats.uncached_input_tokens == 6
    assert stats.cache_creation_input_tokens == 1634
    assert stats.cache_read_input_tokens == 2000
    assert stats.output_tokens == 24
    assert stats.input_tokens == (
        stats.uncached_input_tokens
        + stats.cache_creation_input_tokens
        + stats.cache_read_input_tokens
    )


def test_codex_runner_fails_loud_without_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("ARCHON_CODEX_BIN", raising=False)
    monkeypatch.setattr("cktlean.codex_runner.shutil.which", lambda _: None)
    runner = CodexAgentHarnessRunner(
        project_root=tmp_path,
        prob_id="prob_a",
        model="gpt-5.1-codex",
        role="ckt-generator",
        log_base=tmp_path / "logs" / "generate",
        system_prompt="system",
    )
    with pytest.raises(RuntimeError, match="codex-agent harness requested"):
        runner._resolve_codex_bin()


def test_codex_prompt_points_to_lean_check(tmp_path: Path):
    runner = CodexAgentHarnessRunner(
        project_root=tmp_path,
        prob_id="prob_a",
        model="gpt-5.1-codex",
        role="ckt-generator",
        log_base=tmp_path / "logs" / "generate",
        system_prompt="system",
    )
    prompt = runner._codex_prompt("problem", max_turns=80)
    assert ".venv/bin/python -m cktlean.tools lean-check Generated/prob_a.lean" in prompt
    assert "Only edit `Generated/prob_a.lean`" in prompt
    assert "Never read prior benchmark candidates or run artifacts" in prompt
    assert "experiments/p3_replay_candidates" in prompt
    assert "Final CKTLean override" in prompt
    assert "Do not run simulation, pytest, cocotb, or a final `lake build` after lean-check succeeds" in prompt


def test_codex_runner_wraps_cli_for_execution_user(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("cktlean.codex_runner.shutil.which", lambda _: "/usr/sbin/runuser")
    runner = CodexAgentHarnessRunner(
        project_root=tmp_path,
        prob_id="prob_a",
        model="gpt-5.6-sol",
        role="ckt-generator",
        log_base=tmp_path / "logs" / "generate",
        system_prompt="system",
        execution_user="root",
    )

    wrapper = Path(runner._wrap_codex_bin_for_execution_user("/usr/local/bin/codex"))
    script = wrapper.read_text(encoding="utf-8")

    assert wrapper.is_file()
    assert "--user root -- /usr/local/bin/codex" in script


def test_codex_public_only_mode_scrubs_cvdp_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CVDP_DATASET_FILE", "/private/cvdp.jsonl")
    monkeypatch.setenv("CVDP_HARNESS_PROFILE", "race-safe-v1")
    runner = CodexAgentHarnessRunner(
        project_root=tmp_path,
        prob_id="prob_a",
        model="gpt-5.6-sol",
        role="ckt-generator",
        log_base=tmp_path / "logs" / "generate",
        system_prompt="system",
        public_only=True,
    )

    env = runner._build_agent_env({"PROXY_ONLY": "1"})

    assert "CVDP_DATASET_FILE" not in env
    assert "CVDP_HARNESS_PROFILE" not in env
    assert env["PROXY_ONLY"] == "1"
    assert "Hidden CVDP harnesses" in runner._codex_prompt("problem", max_turns=1)


def test_codex_native_direct_verilog_prompt_and_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    for name in (
        "CKTLEAN_CODEX_BASE_URL",
        "CKTLEAN_CODEX_API_KEY",
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_BASE_URL",
    ):
        monkeypatch.setenv(name, "must-not-reach-codex")
    runner = CodexAgentHarnessRunner(
        project_root=tmp_path,
        prob_id="prob_a",
        model="gpt-5.6-sol",
        role="verilog-generator",
        log_base=tmp_path / "logs" / "generate",
        system_prompt="direct system prompt",
        public_only=True,
        direct_verilog=True,
        native_login_only=True,
    )

    prompt = runner._codex_prompt("problem", max_turns=10)
    env = runner._build_agent_env({})

    assert "cktlean_work/prob_a/candidate.sv" in prompt
    assert "Generated/prob_a.lean" not in prompt
    assert "lean-check" not in prompt
    assert "Do not run simulation, pytest, cocotb" in prompt
    assert "only feedback allowed by the selected feedback mode" in prompt
    assert not runner._should_auto_proxy()
    for name in (
        "CKTLEAN_CODEX_BASE_URL",
        "CKTLEAN_CODEX_API_KEY",
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_BASE_URL",
    ):
        assert name not in env


def test_codex_budget_watcher_sets_cancel(tmp_path: Path):
    runner = CodexAgentHarnessRunner(
        project_root=tmp_path,
        prob_id="prob_a",
        model="claude-sonnet-4-5-20250929",
        role="ckt-generator",
        log_base=tmp_path / "logs" / "generate",
        system_prompt="system",
    )
    runner.log_path.parent.mkdir(parents=True)
    append_jsonl(runner.log_path, {"event": "text", "content": "thinking"})
    append_jsonl(runner.log_path, {"event": "tool_call", "tool": "Bash", "input": {}})

    import threading

    cancel = threading.Event()
    runner._watch_turn_budget(2, cancel)

    assert cancel.is_set()
    assert "cktlean_budget_exceeded" in runner.log_path.read_text(encoding="utf-8")


def test_codex_runner_recovers_usage_from_persistent_rollout(tmp_path: Path):
    codex_home = tmp_path / "codex-home"
    rollout = (
        codex_home
        / "sessions"
        / "2026"
        / "08"
        / "18"
        / "rollout-thread-usage.jsonl"
    )
    rollout.parent.mkdir(parents=True)
    rollout.write_text(
        json.dumps(
            {
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "total_token_usage": {
                            "input_tokens": 120,
                            "cached_input_tokens": 80,
                            "cache_write_input_tokens": 0,
                            "output_tokens": 9,
                        }
                    },
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    runner = CodexAgentHarnessRunner(
        project_root=tmp_path,
        prob_id="prob_a",
        model="gpt-5.6-sol",
        role="ckt-generator",
        log_base=tmp_path / "logs" / "generate",
        system_prompt="system",
    )
    append_jsonl(runner.log_path, {"event": "session_start", "runner": "codex"})
    append_jsonl(
        runner.log_path,
        {"event": "session_meta", "session_id": "thread-usage"},
    )
    append_jsonl(runner.log_path, {"event": "cktlean_budget_exceeded"})
    append_jsonl(
        runner.log_path,
        {
            "event": "session_end",
            "runner": "codex",
            "num_items": 1,
            "input_tokens_total": 0,
            "output_tokens": 0,
        },
    )

    runner._recover_rollout_usage({"CODEX_HOME": str(codex_home)})
    stats = parse_agent_log(runner.log_path)

    assert stats.input_tokens == 120
    assert stats.uncached_input_tokens == 40
    assert stats.cache_read_input_tokens == 80
    assert stats.output_tokens == 9
    assert stats.recovered_usage_sessions == 1
    assert stats.token_accounting_complete
    assert not rollout.exists()


def test_system_prompt_separates_file_name_from_target_module():
    prompt = build_system_prompt(
        "base skill",
        "cvdp_problem_id",
        SimpleNamespace(design_name="target_module_name"),
    )
    assert "Generated/cvdp_problem_id.lean" in prompt
    assert "Lean function/top module must be `target_module_name`" in prompt
    assert "#synthesizeVerilog target_module_name" in prompt
    assert "Do not name the synthesized function `cvdp_problem_id`" in prompt


def test_system_prompt_contains_sparkle_hazard_rules():
    prompt = build_system_prompt("base skill\n## Architecture Exploration\nlarge irrelevant section", "prob_a")
    assert "Architecture Exploration" not in prompt
    assert "docs/Troubleshooting_Synthesis.md" in prompt
    assert "Do not `cat` all of `docs/Troubleshooting_Synthesis.md`" in prompt
    assert "x >>> 1#8" in prompt
    assert "Never write `x >>> 1`" in prompt
    assert "Signal.ult" in prompt
    assert "avoid Lean `||` and `&&` over signals" in prompt
    assert "do not destructure with `let (a, b) := ...`" in prompt
    assert "use clog2 DEPTH" in prompt

    assert "`dff init next` takes a plain initial payload" in prompt
    assert "Lean product types associate to the right" in prompt
    assert "Do not use `bundleAll!` to build a packed bit-vector result" in prompt
    assert "Every inline `code` check must include" in prompt
    assert "The harness automatically saves" in prompt


def test_clear_generated_target_backs_up_stale_file(tmp_path: Path):
    generated = tmp_path / "Generated"
    generated.mkdir()
    target = generated / "prob_a.lean"
    target.write_text("old generated code", encoding="utf-8")
    run_dir = tmp_path / "run"

    backup = clear_generated_target(tmp_path, run_dir, "prob_a")

    assert backup is not None
    assert not target.exists()
    assert Path(backup).read_text(encoding="utf-8") == "old generated code"
    assert str(run_dir / "preexisting_generated") in backup


def test_turn_budget_is_shared_across_sessions():
    budget = TurnBudget(100)
    assert budget.session_limit(10) == 10
    budget.consume(1)
    budget.consume(10)
    assert budget.remaining == 89
    assert budget.session_limit(100) == 89


def test_self_test_planner_prompt_contains_no_hidden_harness():
    prompt = build_self_test_planner_prompt(
        prob_id="prob_a",
        design_name="dut",
        spec="Increment input by one.",
        interface_contract="input a; output y",
        public_context="",
    )
    assert "Increment input by one" in prompt
    assert "input a; output y" in prompt
    assert "hidden testbench" in prompt
    assert "authoritative" in prompt
    assert "desired functional behavior" in prompt
    assert "Never make reproduction of known buggy" in prompt
    assert "Do not invent parameter values or sweeps" in prompt
    assert "after the NBA update" in prompt
    assert "harness_files" not in prompt


def test_parse_self_test_guide_requires_named_top():
    guide = parse_self_test_guide(
        """
<test_plan>Check zero and maximum input.</test_plan>
<testbench_sv>
module cktlean_self_test; initial $display("CKTLEAN_SELF_TEST_PASS"); endmodule
</testbench_sv>
"""
    )
    assert guide.test_plan == "Check zero and maximum input."
    assert "module cktlean_self_test" in guide.testbench_sv

    invalid = parse_self_test_guide(
        "<test_plan>plan</test_plan><testbench_sv>module wrong; endmodule</testbench_sv>"
    )
    assert invalid.testbench_sv == ""


def test_self_test_validation_enforces_contract_reset_polarity():
    guide = parse_self_test_guide(
        """
<test_plan>Check reset.</test_plan>
<testbench_sv>
module cktlean_self_test;
logic rst;
dut top(.rst(rst));
initial begin rst = 1; #1; rst = 0; end
endmodule
</testbench_sv>
"""
    )
    validated, error = validate_self_test_guide(
        guide,
        design_name="dut",
        interface_contract="- Clock/reset names: reset=rst (active-low)",
    )
    assert validated.testbench_sv == ""
    assert "active-low" in (error or "")


def test_self_test_validation_blocks_file_or_process_access():
    guide = parse_self_test_guide(
        """
<test_plan>Check input.</test_plan>
<testbench_sv>
module cktlean_self_test;
logic a, y;
dut top(.a(a), .y(y));
initial begin $readmemh("secret.hex", mem); end
endmodule
</testbench_sv>
"""
    )
    validated, error = validate_self_test_guide(
        guide,
        design_name="dut",
        interface_contract="",
    )
    assert validated.testbench_sv == ""
    assert "forbidden" in (error or "")


def test_self_test_validation_accepts_parameterized_dut_instance():
    guide = parse_self_test_guide(
        """
<test_plan>Check a parameterized DUT.</test_plan>
<testbench_sv>
module cktlean_self_test;
logic clock;
fifo_policy #(
  .NWAYS(4),
  .NINDEXES(32)
) dut (
  .clock(clock)
);
endmodule
</testbench_sv>
"""
    )
    validated, error = validate_self_test_guide(
        guide,
        design_name="fifo_policy",
        interface_contract="- Expected top module: `fifo_policy`",
    )
    assert error is None
    assert "fifo_policy #(" in validated.testbench_sv


def test_self_test_validation_rejects_known_bug_as_oracle():
    guide = parse_self_test_guide(
        """
<test_plan>
The actual buggy RTL behavior is to be verified. Test passes if RTL behavior
matches the actual buggy implementation.
</test_plan>
<testbench_sv>
module cktlean_self_test;
logic access;
dut top(.access(access));
initial begin
  $display("expected increment on a hit (bug)");
  $display("CKTLEAN_SELF_TEST_PASS");
end
endmodule
</testbench_sv>
"""
    )
    validated, error = validate_self_test_guide(
        guide,
        design_name="dut",
        interface_contract="- Expected top module: `dut`",
    )
    assert validated.test_plan == ""
    assert validated.testbench_sv == ""
    assert "buggy or erroneous behavior" in (error or "")


def _progress(result):
    return (result.get("rank", 0),)


def test_candidate_tracker_keeps_local_and_global_best(tmp_path: Path):
    target = tmp_path / "Generated" / "prob_a.lean"
    tracker = CandidateTracker(
        snapshot_root=tmp_path / "candidates",
        prob_id="prob_a",
        progress_key=_progress,
        max_candidates=3,
        patience=2,
    )
    tracker.start_candidate({"rank": 5}, "candidate A", reason="initial")
    tracker.observe({"rank": 4}, "candidate A worse", reason="repair")
    tracker.restore_active(target)
    assert target.read_text() == "candidate A"

    equivalent = tracker.observe({"rank": 5}, "candidate A equivalent", reason="repair")
    assert not equivalent.improved_global
    assert tracker.is_stagnant
    tracker.start_candidate({"rank": 1}, "candidate B", reason="fresh restart")
    assert tracker.active_best_code == "candidate B"
    assert tracker.global_best_code == "candidate A"

    tracker.observe({"rank": 6}, "candidate B improved", reason="repair")
    tracker.restore_global(target)
    assert target.read_text() == "candidate B improved"
    assert (tmp_path / "candidates" / "prob_a" / "candidate_02" / "best.lean").exists()


def test_guided_finalization_replays_global_best_when_last_artifacts_are_stale(
    tmp_path: Path,
):
    target = tmp_path / "Generated" / "prob_a.lean"
    tracker = CandidateTracker(
        snapshot_root=tmp_path / "candidates",
        prob_id="prob_a",
        progress_key=_progress,
        max_candidates=2,
        patience=1,
    )
    tracker.start_candidate({"rank": 5, "sim_status": "sim_fail"}, "candidate A", reason="initial")
    tracker.observe({"rank": 5, "sim_status": "sim_fail"}, "candidate A2", reason="repair")
    calls = []

    def evaluate_selected():
        calls.append(target.read_text())
        return {"rank": 5, "sim_status": "sim_pass"}

    result, replayed = finalize_guided_candidate(
        tracker,
        target,
        last_evaluated_code="candidate A2",
        evaluate_selected=evaluate_selected,
    )

    assert replayed
    assert calls == ["candidate A"]
    assert result == {"rank": 5, "sim_status": "sim_pass"}
    assert target.read_text() == "candidate A"


def test_guided_finalization_skips_replay_when_selected_source_was_evaluated_last(
    tmp_path: Path,
):
    target = tmp_path / "Generated" / "prob_a.lean"
    tracker = CandidateTracker(
        snapshot_root=tmp_path / "candidates",
        prob_id="prob_a",
        progress_key=_progress,
    )
    expected = {"rank": 5, "sim_status": "sim_fail"}
    tracker.start_candidate(expected, "candidate A", reason="initial")

    result, replayed = finalize_guided_candidate(
        tracker,
        target,
        last_evaluated_code="candidate A",
        evaluate_selected=lambda: pytest.fail("unexpected replay"),
    )

    assert not replayed
    assert result == expected
    assert target.read_text() == "candidate A"


def test_generated_self_test_runs_in_isolated_directory(tmp_path: Path):
    if not (shutil.which("iverilog") and shutil.which("vvp")):
        pytest.skip("Icarus Verilog is not installed")
    sim_dir = tmp_path / "cvdp_sim" / "prob_a"
    rtl_path = sim_dir / "src" / "dut.sv"
    rtl_path.parent.mkdir(parents=True)
    rtl_path.write_text(
        "module dut(input logic a, output logic y); assign y = a; endmodule\n",
        encoding="utf-8",
    )
    (sim_dir / "src" / "hidden_test.py").write_text("secret oracle", encoding="utf-8")
    tb = """
module cktlean_self_test;
  logic a;
  logic y;
  dut d(.a(a), .y(y));
  initial begin
    a = 1'b1; #1;
    if (y !== 1'b1) $display("CKTLEAN_SELF_TEST_FAIL");
    else $display("CKTLEAN_SELF_TEST_PASS");
    $finish;
  end
endmodule
"""
    result = run_generated_self_test(
        prob_id="prob_a",
        run_dir=tmp_path,
        verilog_sources=["/code/src/dut.sv"],
        testbench_sv=tb,
        candidate_id=1,
        attempt=0,
    )
    assert result.status == "pass"
    isolated = tmp_path / "self_tests" / "prob_a" / "candidate_01_attempt_00"
    assert (isolated / "rtl" / "src" / "dut.sv").exists()
    assert not (isolated / "rtl" / "src" / "hidden_test.py").exists()


def test_resume_completed_treats_budget_exceeded_as_done(tmp_path: Path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    append_jsonl(
        run_dir / "results.jsonl",
        {
            "prob_id": "prob_a",
            "agent_error": "RuntimeError: official Archon CodexAgent exceeded max-turns budget 80",
        },
    )
    assert already_done(tmp_path, "prob_a", "completed")
    assert not already_done(tmp_path, "prob_a", "passed")


def test_codex_runner_auto_proxy_default(tmp_path: Path):
    runner = CodexAgentHarnessRunner(
        project_root=tmp_path,
        prob_id="prob_a",
        model="claude-sonnet-4-5-20250929",
        role="ckt-generator",
        log_base=tmp_path / "logs" / "generate",
        system_prompt="system",
    )
    assert runner._should_auto_proxy()
    runner.base_url_env = "EXTERNAL_RESPONSES_BASE_URL"
    assert not runner._should_auto_proxy()


def test_responses_request_to_chat_request():
    body = {
        "model": "claude-sonnet-4-5-20250929",
        "instructions": "system prompt",
        "input": [
            {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "make a circuit"}]},
            {"type": "function_call", "call_id": "call_1", "name": "exec_command", "arguments": "{\"cmd\":\"pwd\"}"},
            {"type": "function_call_output", "call_id": "call_1", "output": "done"},
        ],
        "tools": [
            {
                "type": "function",
                "name": "exec_command",
                "description": "run command",
                "parameters": {"type": "object", "properties": {"cmd": {"type": "string"}}},
            }
        ],
        "tool_choice": "auto",
    }
    chat = responses_request_to_chat_request(body)
    assert chat["model"] == "claude-sonnet-4-5-20250929"
    assert chat["messages"][0] == {"role": "system", "content": "system prompt"}
    assert chat["messages"][1] == {"role": "user", "content": "make a circuit"}
    assert chat["messages"][2]["tool_calls"][0]["function"]["name"] == "exec_command"
    assert chat["messages"][3] == {"role": "tool", "tool_call_id": "call_1", "content": "done"}
    assert chat["tools"][0]["function"]["parameters"]["type"] == "object"


def test_responses_request_omits_parallel_tools_without_tools():
    chat = responses_request_to_chat_request(
        {
            "model": "gpt-5.6-sol",
            "input": "continue",
            "parallel_tool_calls": True,
        }
    )

    assert "tools" not in chat
    assert "parallel_tool_calls" not in chat


def test_responses_request_injects_workspace_tool_for_codex_gateway(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CKTLEAN_INJECT_CODEX_CHAT_TOOLS", "1")
    monkeypatch.setenv("CKTLEAN_CHAT_TOOL_REASONING_EFFORT", "none")

    chat = responses_request_to_chat_request(
        {"model": "gpt-5.6-sol", "input": "write the candidate"}
    )

    assert chat["tool_choice"] == "required"
    assert chat["tools"][0]["function"]["name"] == "exec_command"
    assert chat["tools"][0]["function"]["parameters"]["required"] == ["cmd"]
    assert chat["reasoning_effort"] == "none"


def test_responses_request_requires_an_initial_workspace_tool_call():
    body = {
        "model": "gpt-5.6-sol",
        "input": "write the candidate",
        "tools": [
            {
                "type": "function",
                "name": "exec_command",
                "description": "run command",
                "parameters": {"type": "object"},
            }
        ],
        "tool_choice": "auto",
    }

    initial = responses_request_to_chat_request(body)
    assert initial["tool_choice"] == "required"

    body["input"] = [
        {
            "type": "function_call_output",
            "call_id": "call_1",
            "output": "candidate written",
        }
    ]
    continued = responses_request_to_chat_request(body)
    assert continued["tool_choice"] == "auto"


def test_responses_request_user_assistant_only_mode(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CKTLEAN_CHAT_USER_ASSISTANT_ONLY", "1")
    body = {
        "model": "claude-sonnet-4-5-20250929",
        "instructions": "system prompt",
        "input": [
            {"type": "message", "role": "developer", "content": [{"type": "input_text", "text": "developer hint"}]},
            {"type": "function_call", "call_id": "call_1", "name": "exec_command", "arguments": "{\"cmd\":\"pwd\"}"},
            {"type": "function_call_output", "call_id": "call_1", "output": "done"},
        ],
        "tools": [
            {
                "type": "function",
                "name": "exec_command",
                "description": "run command",
                "parameters": {"type": "object"},
            }
        ],
    }

    chat = responses_request_to_chat_request(body)

    assert [message["role"] for message in chat["messages"]] == ["user", "assistant", "user"]
    assert "System instructions:" in chat["messages"][0]["content"]
    assert "[developer message]" in chat["messages"][0]["content"]
    assert "[tool call call_1: exec_command]" in chat["messages"][1]["content"]
    assert "[tool output call_1]" in chat["messages"][2]["content"]
    assert "tools" in chat


def test_chat_response_to_responses_sse():
    chat = {
        "id": "chatcmpl_1",
        "choices": [
            {
                "message": {
                    "content": "I will inspect the file.",
                    "tool_calls": [
                        {
                            "id": "call_abc",
                            "type": "function",
                            "function": {"name": "exec_command", "arguments": "{\"cmd\":\"ls\"}"},
                        }
                    ],
                }
            }
        ],
        "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
    }
    events = chat_response_to_responses_events(chat, response_id="resp_test")
    assert events[0]["type"] == "response.created"
    assert events[1]["item"]["content"][0]["text"] == "I will inspect the file."
    assert events[2]["item"]["type"] == "function_call"
    assert events[2]["item"]["call_id"] == "call_abc"
    assert events[-1]["response"]["usage"]["total_tokens"] == 18
    payload = events_to_sse(events).decode()
    assert "event: response.output_item.done" in payload
    assert "\"type\": \"response.completed\"" in payload


def test_candidate_tracker_rewrites_after_two_non_improving_repairs(tmp_path: Path):
    tracker = CandidateTracker(
        snapshot_root=tmp_path / "candidates",
        prob_id="prob_b",
        progress_key=_progress,
        max_candidates=3,
        patience=2,
    )
    tracker.start_candidate({"rank": 0, "detail": "first error"}, "candidate A", reason="initial")

    repeated = tracker.observe({"rank": 0, "detail": "first error"}, "candidate A1", reason="repair")
    assert repeated.stagnation_count == 1
    assert repeated.stagnation_reason == "evaluation failure signature repeated"

    changed = tracker.observe({"rank": 0, "detail": "second error"}, "candidate A2", reason="repair")
    assert changed.stagnation_count == 2
    assert changed.stagnation_reason == "evaluation signature changed without measurable progress"
    assert tracker.is_stagnant


def test_candidate_tracker_resets_rewrite_counter_only_on_measurable_progress(tmp_path: Path):
    tracker = CandidateTracker(
        snapshot_root=tmp_path / "candidates",
        prob_id="prob_progress",
        progress_key=_progress,
        max_candidates=3,
        patience=2,
    )
    tracker.start_candidate({"rank": 0, "detail": "first error"}, "candidate A", reason="initial")
    tracker.observe({"rank": 0, "detail": "different error"}, "candidate A1", reason="repair")
    improved = tracker.observe({"rank": 1, "detail": "later stage"}, "candidate A2", reason="repair")

    assert improved.improved_candidate
    assert improved.stagnation_count == 0
    assert not tracker.is_stagnant


def test_candidate_tracker_prefers_stable_evaluator_diagnostic_signature(tmp_path: Path):
    tracker = CandidateTracker(
        snapshot_root=tmp_path / "candidates",
        prob_id="prob_c",
        progress_key=_progress,
        patience=2,
    )
    tracker.start_candidate(
        {"rank": 0, "detail": "first rendered error", "diagnostic_signature": "stable-loop"},
        "candidate A",
        reason="initial",
    )
    observation = tracker.observe(
        {"rank": 0, "detail": "different rendered context", "diagnostic_signature": "stable-loop"},
        "candidate A1",
        reason="repair",
    )

    assert observation.stagnation_count == 1
    assert observation.stagnation_reason == "evaluation failure signature repeated"


def test_harness_accepts_parameterized_synthesis_as_complete_candidate(tmp_path: Path):
    class Result:
        passed = True
        complete = True
        verilog = "module demo; endmodule"
        verilog_modules = [verilog]

    runner = object.__new__(AnthropicHarnessRunner)
    runner.required_verilog_modules = ()
    runner._last_complete_code = None
    runner._last_complete_sequence = None
    runner._tool_sequence = 3
    runner._autosave_last_complete_candidate = lambda: None

    code = """def demo {W : Nat} := by sorry
#synthesizeParameterizedVerilog demo [W := 8]
"""
    runner._remember_complete_candidate(code, Result())

    assert runner._last_complete_code == code
    assert runner._last_complete_sequence == 3
