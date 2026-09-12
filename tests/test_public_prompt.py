import copy
import json
import os
from types import SimpleNamespace

import pytest

from cktarchon import public_prompt as pp
from cktarchon import run, run_verilog
from cktarchon import codex_runner


def info():
    return SimpleNamespace(
        prob_id="sample", design_name="dut", prompt_text="LOSSY SUMMARY",
        ref_code="PRIVATE_REFERENCE", testbench_path="private.xml", ref_path="answer.sv",
        metadata={
            "dataset": "cvdp",
            "cvdp_row": {
                "input": {"prompt": "Add output fee. Reset rst_n_i is active-low. Example value: 3'b001.",
                          "context": {"old.sv": "module dut(input clk); endmodule"}},
                "output": {"context": {"answer.sv": "PRIVATE_REFERENCE"}},
                "harness": {"files": {"tb.py": "PRIVATE_HARNESS"}},
            },
            "harness_files": {"tb.py": "PRIVATE_HARNESS"},
            "input_context_files": {"different.sv": "STALE_CONTEXT"},
            "native_parameter_sweep_plan": {"cases": ["PRIVATE_SWEEP"]},
            "finite_parameter_plan": {"cases": ["PRIVATE_ALIAS"]},
            "formal_parameter_contract": {"obligation_text": "PRIVATE_PROOF"},
        },
    )


def test_public_view_is_whitelisted_and_does_not_mutate_evaluator_input():
    original = info()
    before = copy.deepcopy(original.__dict__)
    public = pp.public_problem_info(original)
    assert original.__dict__ == before
    assert set(public.metadata) == {"dataset", "interface_prompt_policy", "agent_input_policy", "input_context_files"}
    assert public.testbench_path is None and public.ref_path is None
    assert "LOSSY" not in public.prompt_text
    assert "STALE" not in public.ref_code
    public.metadata["input_context_files"]["old.sv"] = "changed"
    assert original.__dict__ == before


def test_hidden_metadata_cannot_change_public_packet():
    original, changed = info(), info()
    changed.metadata["cvdp_row"]["output"] = {"context": {"secret": "OTHER_PRIVATE"}}
    changed.metadata["cvdp_row"]["harness"] = {"files": {"src/.env": "TOPLEVEL=secret"}}
    changed.metadata["harness_files"] = {"reset": "active-high"}
    changed.metadata["native_parameter_sweep_plan"] = {"cases": [987654]}
    assert pp.public_packet(pp.public_problem_info(original)) == pp.public_packet(pp.public_problem_info(changed))


def test_full_public_input_shared_in_generation_and_repair():
    original = info()
    original.metadata["cvdp_row"]["input"]["prompt"] += "x" * 35000 + " SPEC_TAIL"
    original.metadata["cvdp_row"]["input"]["context"]["large.sv"] = "y" * 8000 + " CONTEXT_TAIL"
    public = pp.public_problem_info(original)
    packet = pp.public_packet(public)
    for language in ("lean", "verilog"):
        for text in (pp.generation_prompt("sample", public, language), pp.repair_prompt("sample", public, language, "candidate", "compiler error")):
            assert text.count(packet) == 1
            assert text.count("SPEC_TAIL") == text.count("CONTEXT_TAIL") == 1
            assert "PRIVATE_" not in text and "LOSSY SUMMARY" not in text
            assert "input b001" not in text and "reset, active-high" not in text
            assert "newly requested ports" in text


def test_both_system_prompts_drop_legacy_authority():
    public = pp.public_problem_info(info())
    args = SimpleNamespace(prompt_profile="archon")
    sparkle = run.build_system_prompt("", "sample", public, "compact")
    verilog = run_verilog.build_system_prompt("sample", public, args)
    for text in (sparkle, verilog):
        assert "authoritative over guesses" not in text
        assert pp.PUBLIC_SPEC_RULES in text
        assert "PRIVATE_" not in text
    assert "#synthesizeParameterizedVerilog" in sparkle
    assert "Signal.mux" in sparkle
    assert "SystemVerilog-2012" in verilog


def test_direct_initial_bypasses_old_contract(monkeypatch):
    import search
    monkeypatch.setattr(search, "format_benchmark_interface_contract", lambda *_: pytest.fail("legacy contract called"))
    public = pp.public_problem_info(info())
    assert run_verilog.build_initial_prompt("sample", public, "cvdp") == pp.generation_prompt("sample", public, "verilog")


@pytest.mark.parametrize("diagnostics", [
    [{"message": "Cannot infer hardware type from x", "code": "lean_hardware_type_inference"}],
    {"errors": [{"data": "Cannot infer hardware type from x", "severity": "error"}]},
])
def test_preserves_structured_compiler_errors(diagnostics):
    result = {"sim_status": "not_run", "lean_diagnostics": diagnostics,
              "detail": "PRIVATE_EXPECTED_TRACE", "sim_feedback_history": ["PRIVATE_HISTORY"]}
    assert "Cannot infer hardware type" in pp.compile_feedback(result)
    assert "PRIVATE_" not in pp.compile_feedback(result)


def test_parameter_feedback_does_not_expose_private_cases():
    result = {"failure_stage": "parameter_contract", "detail": "at {'WIDTH': 987654}: contract=3 core=8"}
    feedback = pp.compile_feedback(result)
    assert "987654" not in feedback and "contract=3" not in feedback
    assert "parameter-dependent" in feedback


@pytest.mark.parametrize("status", ["sim_fail", "sim_pass"])
def test_no_simulation_feedback(status):
    with pytest.raises(ValueError, match="Simulation"):
        pp.compile_feedback({"sim_status": status, "detail": "got=1 expected=2"})


def test_unverified_detail_is_not_compiler_provenance():
    result = {"sim_status": "sim_error", "detail": "candidate.sv:10: syntax error\nassert got=1 expected=2\ntest_private.py: error: SECRET"}
    assert "SECRET" not in pp.compile_feedback(result)
    assert "candidate.sv:10" not in pp.compile_feedback(result)


def test_direct_feedback_compiles_only_the_candidate(monkeypatch):
    def compile_source(argv, **kwargs):
        assert argv[0] == "iverilog" and argv[3] == "dut"
        assert len(argv) == 7
        assert "capture_output" in kwargs
        return SimpleNamespace(returncode=1, stderr="candidate.sv:2: syntax error")
    monkeypatch.setattr(pp.subprocess, "run", compile_source)
    feedback = pp.compile_feedback({"detail": "PRIVATE_TESTBENCH"}, candidate_sv="module dut; bad endmodule", top="dut")
    assert "syntax error" in feedback and "PRIVATE_TESTBENCH" not in feedback


def test_receipt_retains_exact_prompt_and_public_hash(tmp_path):
    public = pp.public_problem_info(info())
    prompt = pp.generation_prompt("sample", public, "lean")
    pp.save_prompt_receipt(tmp_path, "sample", "generate", public, "system", prompt)
    receipt = json.loads((tmp_path / "prompt_receipts/sample/generate.json").read_text())
    assert receipt["user_prompt"] == prompt
    assert receipt["policy"] == pp.POLICY
    assert len(receipt["public_packet_sha256"]) == 64


def test_legacy_system_prompt_remains_available():
    legacy = info()
    legacy.metadata = {"dataset": "cvdp"}
    assert "authoritative over guesses" in run.build_system_prompt("", "sample", legacy, "compact")
    assert "authoritative over guesses" in run_verilog.build_system_prompt("sample", legacy, SimpleNamespace(prompt_profile="archon"))


def test_last_message_capture_preserves_private_logs(tmp_path, monkeypatch):
    monkeypatch.setattr(codex_runner.pwd, "getpwnam", lambda _: SimpleNamespace(pw_uid=os.geteuid(), pw_gid=os.getegid()))
    runner = codex_runner.CodexAgentHarnessRunner(
        project_root=tmp_path, prob_id="sample", model="test", role="test",
        log_base=tmp_path / "private/logs/generate", system_prompt="test", execution_user="test-user",
    )
    with runner._last_message_capture() as capture:
        assert capture.parent.stat().st_mode & 0o777 == 0o700
        capture.write_text("public final message")
        temporary = capture.parent
    assert not temporary.exists()
    assert (tmp_path / "private/logs/generate.last_message.txt").read_text() == "public final message"


def test_no_output_remapping_without_execution_user(tmp_path):
    runner = codex_runner.CodexAgentHarnessRunner(
        project_root=tmp_path, prob_id="sample", model="test", role="test",
        log_base=tmp_path / "logs/generate", system_prompt="test",
    )
    with runner._last_message_capture() as capture:
        assert capture is None
