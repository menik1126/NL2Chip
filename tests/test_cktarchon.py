from __future__ import annotations

import json
import sys
import threading
from types import ModuleType
from types import SimpleNamespace
from pathlib import Path

import pytest

from cktarchon.codex_runner import CodexAgentHarnessRunner
from cktarchon.env import model_alias
from cktarchon.harness import AnthropicHarnessRunner, PathGuard
from cktarchon.logs import AgentStats, append_jsonl, parse_agent_log
from cktarchon.run import (
    accumulate_summary_record,
    already_done,
    build_system_prompt,
    clear_generated_target,
)
from cktarchon.search_strategy import (
    CandidateTracker,
    TurnBudget,
    build_self_test_planner_prompt,
    parse_self_test_guide,
    run_generated_self_test,
    validate_self_test_guide,
)
from cktarchon.responses_chat_proxy import (
    chat_response_to_responses_events,
    events_to_sse,
    responses_request_to_chat_request,
)


def test_model_alias_sonnet_45():
    assert model_alias("claude-sonnet-4.5") == "claude-sonnet-4-5-20250929"


def test_summary_counts_classified_agent_errors_before_sim_not_run():
    summary = {
        "total": 5,
        "skipped": 0,
        "compile_pass": 0,
        "sim_pass": 0,
        "sim_fail": 0,
        "sim_error": 0,
        "agent_error": 0,
        "sim_feedback_attempts": 0,
        "sim_feedback_success": 0,
    }
    records = [
        {
            "prob_id": "agent_a",
            "compile_pass": False,
            "sim_status": "not_run",
            "failure_category": "agent_error",
        },
        {
            "prob_id": "agent_b",
            "compile_pass": False,
            "sim_status": "not_run",
            "failure_category": "agent_error",
        },
        {
            "prob_id": "compile_a",
            "compile_pass": False,
            "sim_status": "not_run",
            "failure_category": "source_compile_error",
        },
        {
            "prob_id": "compile_b",
            "compile_pass": False,
            "sim_status": "not_run",
            "failure_category": "source_compile_error",
        },
        {
            "prob_id": "other",
            "compile_pass": False,
            "sim_status": "not_run",
            "failure_category": "unknown_failure",
        },
    ]

    for record in records:
        accumulate_summary_record(summary, record)

    assert summary["agent_error"] == 2
    assert summary["sim_error"] == 3
    assert summary["sim_pass"] == 0
    assert summary["sim_fail"] == 0


@pytest.mark.parametrize(
    "record",
    [
        {"agent_error": "RuntimeError: failed", "sim_status": "not_run"},
        {"sim_status": "agent_error"},
    ],
)
def test_summary_agent_error_fallbacks(record):
    summary = {
        key: 0
        for key in (
            "compile_pass",
            "sim_pass",
            "sim_fail",
            "sim_error",
            "agent_error",
            "sim_feedback_attempts",
            "sim_feedback_success",
        )
    }

    accumulate_summary_record(summary, record)

    assert summary["agent_error"] == 1
    assert summary["sim_error"] == 0


def test_path_guard_allows_only_problem_outputs(tmp_path: Path):
    guard = PathGuard(tmp_path, "prob_a")
    assert guard.is_write_allowed("Generated/prob_a.lean")
    assert guard.is_write_allowed("cktarchon_work/prob_a/notes.json")
    assert not guard.is_write_allowed("Generated/prob_b.lean")
    assert not guard.is_write_allowed("agent/search.py")


class _CompleteLeanResult:
    passed = True
    complete = True
    summary = "PASS COMPLETE"
    error_text = ""
    errors = []
    warnings = []
    verilog = "module prob_a; endmodule"


class _CompleteLeanRepl:
    def check_code(self, code: str):
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
    monkeypatch.setattr("cktarchon.harness.ensure_runtime_env", lambda: None)
    monkeypatch.setattr("cktarchon.harness.anthropic.Anthropic", lambda **_: object())
    return AnthropicHarnessRunner(
        project_root=tmp_path,
        prob_id="prob_a",
        model="test-model",
        role="test-role",
        log_base=tmp_path / "logs" / "test",
        system_prompt="test",
        lean_repl=_CompleteLeanRepl(),
    )


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


def test_parse_agent_log(tmp_path: Path):
    log = tmp_path / "agent.jsonl"
    append_jsonl(log, {"event": "assistant", "turn": 0, "usage": {"input_tokens": 10, "output_tokens": 2}})
    append_jsonl(log, {"event": "tool_call", "name": "lean_check", "input": {}})
    append_jsonl(log, {"event": "session_end", "turns": 1})
    stats = parse_agent_log(log)
    assert stats.turns == 1
    assert stats.input_tokens == 10
    assert stats.output_tokens == 2
    assert stats.compile_checks == 1
    assert stats.tool_counts == {"lean_check": 1}


def test_parse_codex_archon_log(tmp_path: Path):
    log = tmp_path / "codex.jsonl"
    append_jsonl(log, {"event": "session_meta", "session_id": "thread-1"})
    append_jsonl(log, {"event": "tool_call", "tool": "Bash", "input": {"command": "python -m cktarchon.tools lean-check Generated/prob.lean"}})
    append_jsonl(log, {"event": "turn_usage", "input_tokens": 7, "cache_read_input_tokens": 3, "output_tokens": 2})
    append_jsonl(log, {"event": "session_end", "num_turns": 4, "input_tokens_total": 10, "output_tokens": 2})
    stats = parse_agent_log(log)
    assert stats.session_id == "thread-1"
    assert stats.turns == 4
    assert stats.input_tokens == 10
    assert stats.output_tokens == 2
    assert stats.compile_checks == 1
    assert stats.tool_counts == {"Bash": 1}


def test_parse_agent_log_keeps_nonzero_usage_when_session_end_reports_zero(tmp_path: Path):
    log = tmp_path / "cancelled.jsonl"
    append_jsonl(
        log,
        {
            "event": "turn_usage",
            "input_tokens": 7,
            "cache_read_input_tokens": 3,
            "output_tokens": 2,
        },
    )
    append_jsonl(log, {"event": "cktarchon_budget_exceeded", "max_turns": 2})
    append_jsonl(
        log,
        {
            "event": "session_end",
            "num_turns": 2,
            "input_tokens_total": 0,
            "output_tokens": 0,
        },
    )

    stats = parse_agent_log(log)

    assert stats.input_tokens == 10
    assert stats.output_tokens == 2
    assert stats.budget_exhausted
    assert not stats.usage_accounting_complete
    assert stats.usage_accounting_notes


def test_parse_agent_log_counts_only_actual_compile_commands(tmp_path: Path):
    log = tmp_path / "commands.jsonl"
    commands = [
        "grep -R parameter .lake/build Generated",
        "echo lake build",
        "/bin/bash -lc '.venv/bin/python -m cktarchon.tools lean-check Generated/prob.lean'",
        "/root/.elan/bin/lake env lean Generated/prob.lean",
        "lake build Sparkle",
    ]
    for command in commands:
        append_jsonl(log, {"event": "tool_call", "tool": "Bash", "input": {"command": command}})

    stats = parse_agent_log(log)

    assert stats.compile_checks == 3
    assert stats.tool_counts == {"Bash": 5}


def test_codex_runner_fails_loud_without_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("ARCHON_CODEX_BIN", raising=False)
    monkeypatch.setattr("cktarchon.codex_runner.shutil.which", lambda _: None)
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
    assert ".venv/bin/python -m cktarchon.tools lean-check Generated/prob_a.lean" in prompt
    assert "Only edit `Generated/prob_a.lean`" in prompt
    assert "Final CktArchon override" in prompt
    assert "Do not run simulation, pytest, cocotb, or a final `lake build` after lean-check succeeds" in prompt


def test_codex_budget_watcher_waits_for_tool_result_before_cancel(tmp_path: Path):
    runner = CodexAgentHarnessRunner(
        project_root=tmp_path,
        prob_id="prob_a",
        model="claude-sonnet-4-5-20250929",
        role="ckt-generator",
        log_base=tmp_path / "logs" / "generate",
        system_prompt="system",
    )
    runner.budget_poll_interval_s = 0.005
    runner.log_path.parent.mkdir(parents=True)
    append_jsonl(runner.log_path, {"event": "text", "content": "thinking"})
    append_jsonl(runner.log_path, {"event": "tool_call", "tool": "Bash", "input": {}})

    cancel = threading.Event()
    monitor = threading.Thread(target=runner._watch_turn_budget, args=(2, cancel), daemon=True)
    monitor.start()

    assert not cancel.wait(0.05)
    state = runner._budget_log_state()
    assert state.counted_events == 2
    assert state.in_flight_tool_calls == 1
    assert "cktarchon_budget_exceeded" not in runner.log_path.read_text(encoding="utf-8")

    append_jsonl(runner.log_path, {"event": "tool_result", "content": "done"})
    assert cancel.wait(1.0)
    monitor.join(timeout=1.0)

    assert cancel.is_set()
    log_text = runner.log_path.read_text(encoding="utf-8")
    assert "cktarchon_budget_exceeded" in log_text
    assert '"safe_boundary": true' in log_text


def test_codex_budget_watcher_treats_todowrite_as_completed(tmp_path: Path):
    runner = CodexAgentHarnessRunner(
        project_root=tmp_path,
        prob_id="prob_a",
        model="gpt-5.6-sol",
        role="ckt-generator",
        log_base=tmp_path / "logs" / "generate",
        system_prompt="system",
        budget_poll_interval_s=0.005,
    )
    append_jsonl(runner.log_path, {"event": "text", "content": "planning"})
    append_jsonl(
        runner.log_path,
        {"event": "tool_call", "tool": "TodoWrite", "input": {"items": []}},
    )
    cancel = threading.Event()

    runner._watch_turn_budget(2, cancel)

    assert cancel.is_set()
    assert runner._budget_log_state().in_flight_tool_calls == 0


def _install_fake_archon(
    monkeypatch: pytest.MonkeyPatch,
    *,
    create_candidate: bool,
) -> None:
    class FakeCodexAgent:
        def __init__(self, *, descriptor, role):
            self.descriptor = descriptor
            self.role = role

        def run(self, prompt, *, cwd, log_base, **kwargs):
            if create_candidate:
                target = Path(cwd) / "Generated" / "prob_a.lean"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("def prob_a := 1\n", encoding="utf-8")
            log_path = Path(str(log_base) + ".jsonl")
            append_jsonl(log_path, {"event": "text", "content": "candidate attempted"})
            append_jsonl(
                log_path,
                {
                    "event": "cktarchon_budget_exceeded",
                    "max_turns": 1,
                    "counted_events": 1,
                    "safe_boundary": True,
                },
            )
            append_jsonl(
                log_path,
                {
                    "event": "session_end",
                    "num_turns": 1,
                    "input_tokens_total": 0,
                    "output_tokens": 0,
                },
            )
            raise RuntimeError("Codex subprocess cancelled")

    archon = ModuleType("archon")
    archon.__path__ = []
    agents = ModuleType("archon.agents")
    agents.__path__ = []
    codex = ModuleType("archon.agents.codex")
    codex.CodexAgent = FakeCodexAgent
    commands = ModuleType("archon.commands")
    commands.__path__ = []
    tooling = ModuleType("archon.commands.tooling")
    tooling.__path__ = []
    project_config = ModuleType("archon.commands.tooling.project_config")
    project_config.HarnessDescriptor = lambda **kwargs: SimpleNamespace(**kwargs)
    for name, module in {
        "archon": archon,
        "archon.agents": agents,
        "archon.agents.codex": codex,
        "archon.commands": commands,
        "archon.commands.tooling": tooling,
        "archon.commands.tooling.project_config": project_config,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)


def _fake_codex_runner(tmp_path: Path) -> CodexAgentHarnessRunner:
    return CodexAgentHarnessRunner(
        project_root=tmp_path,
        prob_id="prob_a",
        model="gpt-5.6-sol",
        role="ckt-generator",
        log_base=tmp_path / "logs" / "generate",
        system_prompt="system",
        archon_src=tmp_path,
        codex_bin="/bin/true",
        auto_chat_proxy=False,
        budget_poll_interval_s=0.005,
    )


def test_codex_budget_exhaustion_returns_retained_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    _install_fake_archon(monkeypatch, create_candidate=True)
    monkeypatch.setattr("cktarchon.codex_runner.ensure_runtime_env", lambda: None)
    runner = _fake_codex_runner(tmp_path)

    stats = runner.run("problem", max_turns=1)

    assert stats.turns == 1
    assert stats.budget_exhausted
    assert not stats.usage_accounting_complete
    assert (tmp_path / "Generated" / "prob_a.lean").exists()
    assert "cktarchon_bounded_completion" in runner.log_path.read_text(encoding="utf-8")


def test_codex_budget_exhaustion_without_candidate_fails_clearly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    _install_fake_archon(monkeypatch, create_candidate=False)
    monkeypatch.setattr("cktarchon.codex_runner.ensure_runtime_env", lambda: None)
    runner = _fake_codex_runner(tmp_path)

    with pytest.raises(RuntimeError, match="without creating Generated/prob_a.lean"):
        runner.run("problem", max_turns=1)


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
    assert "Do not use `bundleAll!` to build a packed bit-vector result" in prompt
    assert "#synthesizeVerilog <design> parameters [PARAM := <nonnegative-default>]" in prompt
    assert "exact benchmark parameter name" in prompt
    assert "evaluator rejects declaration-only parameters" in prompt
    assert "Every inline `code` check must include" in prompt
    assert "The harness automatically saves" in prompt


def test_system_prompt_distinguishes_implicit_clock_from_public_reset():
    prompt = build_system_prompt("base skill", "clock_reset_contract")

    assert "Never declare a clock `Signal` binder" in prompt
    assert "even when the Benchmark Interface Contract lists a public clock" in prompt
    assert "exact benchmark reset name as a `Signal dom Bool` binder" in prompt
    assert "`resetHigh` for active-high or `resetLow` for active-low" in prompt
    assert "implicit ABI `rst` deasserted" in prompt
    assert "preserving D-path reset semantics" in prompt


def test_cvdp_user_message_keeps_public_clock_without_requesting_clock_binder():
    import cktarchon.run as run_module

    run_module._add_legacy_agent_path()
    from search import build_user_message

    info = SimpleNamespace(
        prob_id="clock_reset_contract",
        design_name="clocked_counter",
        prompt_text="""
        ### Interface
        #### Inputs
        - `sys_clk`: Clock.
        - `reset_n`: Active-low reset.
        - `d`: Data input.
        #### Outputs
        - `q`: Registered output.
        """,
        ref_code=(
            "module clocked_counter("
            "input logic sys_clk, input logic reset_n, "
            "input logic d, output logic q); endmodule"
        ),
        testbench_path=Path("dummy.jsonl"),
        ref_path=None,
        metadata={
            "dataset": "cvdp",
            "harness_files": {
                "src/.env": "TOPLEVEL=clocked_counter\n",
                "src/test.py": (
                    "dut.sys_clk.value = 0\n"
                    "dut.reset_n.value = 0\n"
                    "dut.d.value = 0\n"
                    "int(dut.q.value)\n"
                ),
            },
        },
    )

    user_message = build_user_message(
        "clock_reset_contract",
        info=info,
        dataset_name="cvdp",
    )
    full_generation_prompt = (
        build_system_prompt("base skill", "clock_reset_contract", info)
        + "\n\n"
        + user_message
    )

    assert "input sys_clk: logic (clock)" in user_message
    assert "input reset_n: logic (reset, active-low)" in user_message
    assert "clock=sys_clk, reset=reset_n (active-low)" in user_message
    assert "Never declare a clock `Signal` binder" in full_generation_prompt
    assert "exact benchmark reset name as a `Signal dom Bool` binder" in full_generation_prompt


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


def test_process_problem_records_agent_elapsed_on_exception(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    import cktarchon.run as run_module

    class RaisingRunner:
        def run(self, prompt: str, *, max_turns: int):
            raise RuntimeError("generation failed")

    fake_search = ModuleType("search")
    fake_search.build_user_message = lambda *args, **kwargs: "problem"
    fake_search.classify_failure_record = lambda result: {}
    monkeypatch.setitem(sys.modules, "search", fake_search)
    monkeypatch.setattr(run_module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(run_module, "make_runner", lambda **kwargs: RaisingRunner())

    args = SimpleNamespace(
        guided_search=False,
        eval_only=False,
        sim_feedback=False,
        max_turns=2,
        prompt_profile="compact",
        harness="codex-agent",
        model="gpt-5.6-sol",
    )
    ds = SimpleNamespace(load_problem=lambda prob_id: SimpleNamespace())
    evaluator = SimpleNamespace(dataset_name="cvdp")
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    record = run_module.process_problem(
        "prob_a",
        args=args,
        ds=ds,
        evaluator=evaluator,
        run_dir=run_dir,
        skill="",
        repl=None,
    )

    assert record["sim_status"] == "agent_error"
    assert "generation failed" in record["agent_error"]
    assert record["agent_elapsed_seconds"] > 0


def test_bounded_generation_candidate_can_enter_configured_sim_feedback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    import cktarchon.run as run_module

    target = tmp_path / "Generated" / "prob_a.lean"

    class InitialRunner:
        def run(self, prompt: str, *, max_turns: int):
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("initial candidate\n", encoding="utf-8")
            return AgentStats(
                turns=2,
                budget_exhausted=True,
                usage_accounting_complete=False,
                usage_accounting_notes=["cancelled usage"],
            )

    class RepairRunner:
        def run(self, prompt: str, *, max_turns: int):
            target.write_text("repaired candidate\n", encoding="utf-8")
            return AgentStats(turns=1)

    runners = iter([InitialRunner(), RepairRunner()])
    fake_search = ModuleType("search")
    fake_search.build_user_message = lambda *args, **kwargs: "problem"
    fake_search.build_sim_feedback = lambda **kwargs: "compile feedback"
    fake_search.build_compact_repair_prompt = lambda **kwargs: "repair"
    fake_search.eval_progress_key = lambda result: (
        bool(result.get("compile_pass")),
        result.get("sim_status") == "sim_pass",
    )
    fake_search.summarize_eval_result = lambda result: str(result)
    fake_search.classify_failure_record = lambda result: {}
    monkeypatch.setitem(sys.modules, "search", fake_search)
    monkeypatch.setattr(run_module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(run_module, "make_runner", lambda **kwargs: next(runners))

    evaluations = [
        {
            "prob_id": "prob_a",
            "compile_pass": False,
            "sv_extracted": False,
            "lint_pass": False,
            "sim_status": "not_run",
            "sim_mismatches": -1,
            "detail": "Lean compile failed",
        },
        {
            "prob_id": "prob_a",
            "compile_pass": True,
            "sv_extracted": True,
            "lint_pass": True,
            "sim_status": "sim_pass",
            "sim_mismatches": 0,
            "detail": "pass",
        },
    ]
    evaluator = SimpleNamespace(
        dataset_name="cvdp",
        evaluate=lambda prob_id, run_dir: evaluations.pop(0),
    )
    args = SimpleNamespace(
        guided_search=False,
        eval_only=False,
        sim_feedback=True,
        sim_feedback_turn_budget=1,
        sim_feedback_turns_per_iter=1,
        sim_feedback_max_iters=1,
        sim_feedback_patience=2,
        max_turns=2,
        prompt_profile="compact",
        harness="codex-agent",
        model="gpt-5.6-sol",
    )
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    record = run_module.process_problem(
        "prob_a",
        args=args,
        ds=SimpleNamespace(load_problem=lambda prob_id: SimpleNamespace()),
        evaluator=evaluator,
        run_dir=run_dir,
        skill="",
        repl=None,
    )

    assert record["sim_status"] == "sim_pass"
    assert record["sim_feedback_iterations"] == 1
    assert record["sim_feedback_success"]
    assert record["agent_budget_exhausted"]
    assert not record["agent_usage_accounting_complete"]
    assert target.read_text(encoding="utf-8") == "repaired candidate\n"


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
module cktarchon_self_test; initial $display("CKTARCHON_SELF_TEST_PASS"); endmodule
</testbench_sv>
"""
    )
    assert guide.test_plan == "Check zero and maximum input."
    assert "module cktarchon_self_test" in guide.testbench_sv

    invalid = parse_self_test_guide(
        "<test_plan>plan</test_plan><testbench_sv>module wrong; endmodule</testbench_sv>"
    )
    assert invalid.testbench_sv == ""


def test_self_test_validation_enforces_contract_reset_polarity():
    guide = parse_self_test_guide(
        """
<test_plan>Check reset.</test_plan>
<testbench_sv>
module cktarchon_self_test;
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
module cktarchon_self_test;
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
module cktarchon_self_test;
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
module cktarchon_self_test;
logic access;
dut top(.access(access));
initial begin
  $display("expected increment on a hit (bug)");
  $display("CKTARCHON_SELF_TEST_PASS");
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


def test_generated_self_test_runs_in_isolated_directory(tmp_path: Path):
    sim_dir = tmp_path / "cvdp_sim" / "prob_a"
    rtl_path = sim_dir / "src" / "dut.sv"
    rtl_path.parent.mkdir(parents=True)
    rtl_path.write_text(
        "module dut(input logic a, output logic y); assign y = a; endmodule\n",
        encoding="utf-8",
    )
    (sim_dir / "src" / "hidden_test.py").write_text("secret oracle", encoding="utf-8")
    tb = """
module cktarchon_self_test;
  logic a;
  logic y;
  dut d(.a(a), .y(y));
  initial begin
    a = 1'b1; #1;
    if (y !== 1'b1) $display("CKTARCHON_SELF_TEST_FAIL");
    else $display("CKTARCHON_SELF_TEST_PASS");
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


def test_responses_request_user_assistant_only_mode(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CKTARCHON_CHAT_USER_ASSISTANT_ONLY", "1")
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
