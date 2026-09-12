"""Prompt-only regression checks; no model calls or circuit simulations."""

import ast
import copy
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from cktarchon import codex_runner, public_prompt as pp, run, run_verilog


ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "original_prompt_v1"


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


def syntax(path):
    return ast.parse(path.read_text())


def top_function(path, name, class_name=None):
    nodes = syntax(path).body
    if class_name:
        nodes = next(n for n in nodes if isinstance(n, ast.ClassDef) and n.name == class_name).body
    return next(n for n in nodes if isinstance(n, ast.FunctionDef) and n.name == name)


def baseline_function(module, name, class_name=None):
    node = top_function(BASELINE / Path(module.__file__).name, name, class_name)
    namespace = dict(vars(module))
    exec(compile(ast.Module(body=[node], type_ignores=[]), "frozen-v1", "exec"), namespace)
    return namespace[name]


@pytest.mark.parametrize("language", ["lean", "verilog"])
@pytest.mark.parametrize("repair", [False, True])
def test_complete_render_has_real_tools_and_unchanged_stop(language, repair, tmp_path):
    info = public_info()
    system = (run.build_system_prompt("", "sample", info, "compact") if language == "lean"
              else run_verilog.build_system_prompt("sample", info, SimpleNamespace(prompt_profile="archon")))
    prompt = (pp.repair_prompt("sample", info, language, "candidate", "compiler error") if repair
              else pp.generation_prompt("sample", info, language))
    runner = codex_runner.CodexAgentHarnessRunner(
        project_root=tmp_path, prob_id="sample", model="test", role="test",
        log_base=tmp_path / "log", system_prompt=system, direct_verilog=language == "verilog",
        public_only=True, interface_prompt_policy=pp.POLICY,
    )
    rendered = runner._codex_prompt(prompt, max_turns=10)
    for ghost in ("benchmark interface contract", "`lean_check`", "`list_directory`", "`glob`",
                  "`write_file`", "`edit_file`", "H20", "PRIVATE_"):
        assert ghost.lower() not in rendered.lower()
    assert "item | requirement | public source | evidence status" in rendered
    assert "explicit, inferred, ambiguous, or unspecified" in rendered
    assert "not a new validation gate" in rendered
    assert "not a separate file or model call" in rendered
    assert "extra compiler calls solely for the inventory" in rendered
    assert rendered.count(pp.public_packet(info)) == 1
    old = baseline_function(codex_runner, "_codex_prompt", "CodexAgentHarnessRunner")(runner, prompt, max_turns=10)
    assert rendered.split("## Final CktArchon override", 1)[1] == old.split("## Final CktArchon override", 1)[1]
    if language == "lean":
        assert ".venv/bin/python -m cktarchon.tools lean-check Generated/sample.lean" in rendered
        assert "reports success, stop immediately" in rendered
        assert "Do not perform extra Verilog review" in rendered
    else:
        assert "candidate compiler diagnostics allowed by compile-only mode" in rendered
    assert "Do not run simulation" in rendered


@pytest.mark.parametrize("language", ["lean", "verilog"])
def test_legacy_system_text_is_unchanged(language):
    info = SimpleNamespace(design_name="sample_top", metadata={"dataset": "cvdp"})
    if language == "lean":
        args = ("", "sample", info, "compact")
        module = run
    else:
        args = ("sample", info, SimpleNamespace(prompt_profile="archon"))
        module = run_verilog
    assert module.build_system_prompt(*args) == baseline_function(module, "build_system_prompt")(*args)


@pytest.mark.parametrize("direct", [False, True])
def test_legacy_runner_prompt_is_unchanged(direct, tmp_path):
    runner = codex_runner.CodexAgentHarnessRunner(
        project_root=tmp_path, prob_id="sample", model="test", role="test",
        log_base=tmp_path / "log", system_prompt="system", direct_verilog=direct,
    )
    assert runner._codex_prompt("task", max_turns=10) == baseline_function(
        codex_runner, "_codex_prompt", "CodexAgentHarnessRunner"
    )(runner, "task", max_turns=10)


def test_public_packet_is_identical_to_v1():
    spec = importlib.util.spec_from_file_location("frozen_public_v1", BASELINE / "public_prompt.py")
    v1 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(v1)
    info = public_info()
    old_info = copy.deepcopy(info)
    old_info.metadata["interface_prompt_policy"] = v1.POLICY
    assert pp.public_packet(info) == v1.public_packet(old_info)


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


class NormalizePolicyVersion(ast.NodeTransformer):
    def visit_Constant(self, node):
        if isinstance(node.value, str):
            node.value = node.value.replace("public-spec-v2", "public-spec-v1")
        return node


@pytest.mark.parametrize("filename,allowed", [
    ("run.py", {"build_system_prompt", "make_runner"}),
    ("run_verilog.py", {"build_system_prompt", "make_runner"}),
    ("public_prompt.py", set()),
])
def test_non_prompt_functions_are_unchanged(filename, allowed):
    old = {n.name: n for n in syntax(BASELINE / filename).body if isinstance(n, ast.FunctionDef)}
    new = {n.name: n for n in syntax(ROOT / "cktarchon" / filename).body if isinstance(n, ast.FunctionDef)}
    assert old.keys() == new.keys()
    for name in old.keys() - allowed:
        normalized = NormalizePolicyVersion().visit(new[name])
        assert ast.dump(old[name]) == ast.dump(normalized), name


def test_codex_execution_and_budget_watcher_unchanged():
    old_class = next(n for n in syntax(BASELINE / "codex_runner.py").body if isinstance(n, ast.ClassDef))
    new_class = next(n for n in syntax(ROOT / "cktarchon/codex_runner.py").body if isinstance(n, ast.ClassDef))
    old = {n.name: n for n in old_class.body if isinstance(n, ast.FunctionDef)}
    new = {n.name: n for n in new_class.body if isinstance(n, ast.FunctionDef)}
    assert old.keys() == new.keys()
    for name in old.keys() - {"_codex_prompt"}:
        assert ast.dump(old[name]) == ast.dump(new[name]), name


@pytest.mark.parametrize("language", ["lean", "verilog"])
def test_task_identifiers_do_not_select_special_hints(language):
    info = public_info()
    first = pp.generation_prompt("task_alpha", info, language)
    second = pp.generation_prompt("task_beta", info, language)
    assert first.replace("task_alpha", "task_beta") == second
