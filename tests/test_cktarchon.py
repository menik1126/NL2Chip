from __future__ import annotations

import json
from types import SimpleNamespace
from pathlib import Path

import pytest

from cktarchon.codex_runner import CodexAgentHarnessRunner
from cktarchon.env import model_alias
from cktarchon.harness import AnthropicHarnessRunner, PathGuard
from cktarchon.logs import append_jsonl, parse_agent_log
from cktarchon.run import already_done, build_system_prompt, clear_generated_target
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
    assert "cktarchon_budget_exceeded" in runner.log_path.read_text(encoding="utf-8")


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
