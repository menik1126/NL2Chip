import copy
import json
import sys
from types import SimpleNamespace

import pytest

from cktlean import public_prompt, run
from cktlean.logs import AgentStats
from structural_checker import check_public_structure, public_progress_key


SPEC = "## Inputs\n| Name | Width |\n|---|---|\n| din | 8 |\n\n## Outputs\n| Name | Width |\n|---|---|\n| dout | 8 |\n"


def problem():
    return SimpleNamespace(prob_id="sample", design_name="dut", prompt_text=SPEC,
        ref_code="PRIVATE_REFERENCE", testbench_path=None, ref_path=None,
        metadata={"dataset": "cvdp", "cvdp_row": {"input": {"prompt": SPEC, "context": {}}},
                  "harness_files": {"tb": "PRIVATE_TEST"}})


def test_checker_uses_public_only_and_tolerates_packed_outputs():
    import search
    p = problem()
    with pytest.raises(ValueError):
        check_public_structure(sv="", info=p, search_module=search)
    p = public_prompt.public_problem_info(p)
    good = "module dut(input logic [7:0] din, output logic [7:0] dout); endmodule"
    assert check_public_structure(sv=good, info=p, search_module=search) == []
    assert check_public_structure(sv=good.replace("din", "_gen_din"), info=p, search_module=search) == []
    prefixed_scalar = good.replace("[7:0] din", "_gen_din")
    assert [x["kind"] for x in check_public_structure(sv=prefixed_scalar, info=p, search_module=search)] == ["port_width"]
    issues = check_public_structure(sv=good.replace("[7:0] din", "din"), info=p, search_module=search)
    assert [x["kind"] for x in issues] == ["port_width"]
    assert check_public_structure(sv=good.replace("dout", "out"), info=p, search_module=search) == []


@pytest.mark.parametrize("initial_status", ["sim_fail", "sim_pass"])
def test_real_process_trigger_budget_and_hidden_outcome_independence(tmp_path, monkeypatch, initial_status):
    import search
    monkeypatch.setattr(sys, "argv", ["run", "--results-dir", str(tmp_path), "--sim-feedback",
        "--feedback-mode", "compile-only", "--interface-prompt-policy", "public-spec-v2",
        "--hide-cvdp-harness-from-agent", "--total-turn-budget", "100", "--generation-turn-cap", "40",
        "--max-turns", "40", "--sim-feedback-turns-per-iter", "10", "--sim-feedback-max-iters", "9"])
    args = run.parse_args()
    monkeypatch.setattr(run, "PROJECT_ROOT", tmp_path)
    (tmp_path / "Generated").mkdir()
    target = tmp_path / "Generated/sample.lean"
    monkeypatch.setattr(run, "configure_parameter_mode", lambda info, **kw: info)
    monkeypatch.setattr(run, "agent_visible_problem_info", lambda info, **kw: info)
    monkeypatch.setattr(search, "_benchmark_expected_ports", lambda *_: pytest.fail("Hidden-capable parser called"))
    prompts, limits = [], []

    class Runner:
        def run(self, prompt, max_turns):
            prompts.append(prompt)
            limits.append(max_turns)
            target.write_text("candidate " + str(len(prompts)))
            return AgentStats(turns=18 if len(prompts) == 1 else 10)

    monkeypatch.setattr(run, "make_runner", lambda **kw: Runner())
    evaluations = []

    def evaluate(*unused, **unused_kwargs):
        evaluations.append(True)
        sv_dir = tmp_path / "sv"
        sv_dir.mkdir(exist_ok=True)
        typ = "" if len(evaluations) == 1 else "[7:0] "
        (sv_dir / "sample.sv").write_text(f"module dut(input logic {typ}din, output logic [7:0] dout); endmodule")
        return {"compile_pass": True, "sv_extracted": True, "lint_pass": True,
                "sim_status": initial_status if len(evaluations) == 1 else "sim_fail",
                "detail": "PRIVATE_TEST expected=8675309", "failure_stage": "simulation_mismatch"}

    monkeypatch.setattr(run, "evaluate_with_infrastructure_retries", evaluate)
    row = run.process_problem("sample", args=args, ds=SimpleNamespace(load_problem=lambda _: problem()),
                              evaluator=SimpleNamespace(dataset_name="cvdp"), run_dir=tmp_path, skill="", repl=None)
    assert len(prompts) == 2
    assert "Public Structural Contract Check" in prompts[1]
    assert "port_width" in prompts[1]
    assert "PRIVATE_TEST" not in prompts[1] and "8675309" not in prompts[1]
    assert row["agent_turns_total"] == 28
    assert row["sim_feedback_turns_remaining"] == 72
    assert row["public_structural_repair_used"] is True
    assert row["public_structural_check_count"] == 0
    assert target.read_text() == "candidate 2"
    assert limits[1] <= 10
    events = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text().splitlines()]
    assert sum(e["event"] == "public_structural_repair" for e in events) == 1


def test_candidate_selection_does_not_use_hidden_score():
    first = {"compile_pass": True, "sv_extracted": True, "lint_pass": True, "sim_status": "sim_pass"}
    second = dict(first, sim_status="sim_fail", sim_mismatches=999)
    assert public_progress_key(first, []) == public_progress_key(second, [])
