"""Prompt-only regression checks; no model calls or circuit simulations."""

import sys
from types import SimpleNamespace

import pytest

from cktarchon import codex_runner, public_prompt as pp, run, run_verilog


def public_info():
    return pp.public_problem_info(SimpleNamespace(
        design_name="sample_top", metadata={
            "dataset": "cvdp",
            "cvdp_row": {
                "input": {
                    "prompt": "Add output ready. Update it one cycle after enable. Retain all other behavior.",
                    "context": {"old.sv": "module sample_top #(parameter W=5)(input [W-1:0] data); endmodule"},
                },
                "output": {"answer": "PRIVATE_ANSWER"},
                "harness": {"expected": "PRIVATE_EXPECTED"},
            },
        },
    ))


@pytest.mark.parametrize("language", ["lean", "verilog"])
@pytest.mark.parametrize("repair", [False, True])
def test_complete_render_has_real_tools_and_stop_rules(language, repair, tmp_path):
    info = public_info()
    system = (run.build_system_prompt("", "sample", info, "compact") if language == "lean"
              else run_verilog.build_system_prompt("sample", info, SimpleNamespace(prompt_profile="archon")))
    prompt = (pp.repair_prompt("sample", info, language, "candidate", "compiler error",
                               extra_instruction="Create the candidate first.") if repair
              else pp.generation_prompt("sample", info, language))
    runner = codex_runner.CodexAgentHarnessRunner(
        project_root=tmp_path, prob_id="sample", model="test", role="test",
        log_base=tmp_path / "log", system_prompt=system, direct_verilog=language == "verilog",
        public_only=True, interface_prompt_policy=pp.POLICY,
    )
    rendered = runner._codex_prompt(prompt, max_turns=10)
    for ghost in ("benchmark interface contract", "`lean_check`", "`list_directory`", "`glob`",
                  "`write_file`", "`edit_file`", "PRIVATE_"):
        assert ghost.lower() not in rendered.lower()
    assert "item | requirement | public source | evidence status" in rendered
    assert "explicit, inferred, ambiguous, or unspecified" in rendered
    assert "not a new validation gate" in rendered
    assert "not a separate file or model call" in rendered
    assert "extra compiler calls solely for the inventory" in rendered
    assert rendered.count(pp.public_packet(info)) == 1
    if language == "lean":
        assert ".venv/bin/python -m cktarchon.tools lean-check Generated/sample.lean" in rendered
        assert "reports success, stop immediately" in rendered
        assert "Do not perform extra Verilog review" in rendered
    else:
        assert "candidate compiler diagnostics allowed by compile-only mode" in rendered
    assert "Do not run simulation" in rendered


@pytest.mark.parametrize("language", ["lean", "verilog"])
def test_v2_policy_reaches_native_runner_without_changing_budget(language, monkeypatch, tmp_path):
    module = run if language == "lean" else run_verilog
    argv = ["run", "--results-dir", str(tmp_path), "--interface-prompt-policy", pp.POLICY,
            "--feedback-mode", "compile-only", "--sim-feedback", "--sim-feedback-max-iters", "9",
            "--sim-feedback-turns-per-iter", "10", "--sim-feedback-patience", "0"]
    if language == "lean":
        argv += ["--harness", "codex-agent", "--hide-cvdp-harness-from-agent", "--max-turns", "40",
                 "--total-turn-budget", "100", "--generation-turn-cap", "40"]
    else:
        argv += ["--max-turns", "10", "--sim-feedback-turn-budget", "90"]
    monkeypatch.setattr(sys, "argv", argv)
    args = module.parse_args()
    info = public_info()
    if language == "lean":
        runner = run.make_runner(args=args, prob_id="sample", role="test", log_base=tmp_path / "log", skill="", info=info, repl=None)
        assert (args.total_turn_budget, args.generation_turn_cap) == (100, 40)
        assert not args.guided_search
    else:
        runner = run_verilog.make_runner(args, "sample", "test", tmp_path / "log", info)
        assert (args.max_turns, args.sim_feedback_turn_budget) == (10, 90)
    assert runner.interface_prompt_policy == pp.POLICY
    assert args.sim_feedback_max_iters == 9 and args.sim_feedback_turns_per_iter == 10
    assert args.feedback_mode == "compile-only"


@pytest.mark.parametrize("language", ["lean", "verilog"])
def test_task_identifiers_do_not_select_special_hints(language):
    info = public_info()
    first = pp.generation_prompt("task_alpha", info, language)
    second = pp.generation_prompt("task_beta", info, language)
    assert first.replace("task_alpha", "task_beta") == second
