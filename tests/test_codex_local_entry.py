from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from cktarchon.codex_runner import CodexAgentHarnessRunner
from cktarchon.logs import AgentStats
import cktarchon.run as run_module


def _runner(tmp_path: Path, *, archon_src: Path | None = None) -> CodexAgentHarnessRunner:
    return CodexAgentHarnessRunner(
        project_root=tmp_path,
        prob_id="prob_a",
        model="gpt-5.6-sol",
        role="ckt-generator",
        log_base=tmp_path / "logs" / "generate",
        system_prompt="system",
        archon_src=archon_src,
        codex_bin="/bin/true",
        auto_chat_proxy=False,
    )


def test_codex_runner_requires_explicit_archon_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.delenv("ARCHON_SRC", raising=False)

    with pytest.raises(RuntimeError, match=r"--archon-src PATH or set ARCHON_SRC"):
        _runner(tmp_path)._ensure_archon_importable()


def test_codex_runner_rejects_nonexistent_archon_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.delenv("ARCHON_SRC", raising=False)
    missing = tmp_path / "missing-archon-src"

    with pytest.raises(RuntimeError, match=rf"does not exist: {missing}"):
        _runner(tmp_path, archon_src=missing)._ensure_archon_importable()


def test_archon_source_cli_overrides_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    environment_source = tmp_path / "environment-src"
    command_line_source = tmp_path / "command-line-src"
    monkeypatch.setenv("ARCHON_SRC", str(environment_source))

    assert run_module.configured_archon_src(None) == environment_source
    assert run_module.configured_archon_src(command_line_source) == command_line_source


def test_cvdp12_script_passes_archon_source_and_disables_chat_proxy(tmp_path: Path):
    project_root = Path(__file__).resolve().parents[1]
    script = project_root / "scripts" / "run_cvdp12_native_param.sh"
    archon_source = tmp_path / "archon-src"
    archon_source.mkdir()
    environment = os.environ.copy()
    environment.pop("SIM_FEEDBACK_PATIENCE", None)
    environment.update(
        {
            "PYTHON_BIN": "/bin/echo",
            "MODEL": "gpt-5.6-sol",
            "HARNESS": "codex-agent",
            "ARCHON_SRC": str(archon_source),
            "NO_CODEX_CHAT_PROXY": "1",
            "RESULTS_DIR": str(tmp_path / "results"),
            "WORKERS": "1",
        }
    )

    completed = subprocess.run(
        [str(script)],
        cwd=project_root,
        env=environment,
        text=True,
        capture_output=True,
        check=True,
    )

    invocation = completed.stdout
    assert f"--archon-src {archon_source}" in invocation
    assert "--no-codex-chat-proxy" in invocation
    assert "--harness codex-agent" in invocation
    assert "--sim-feedback-patience 2" in invocation


def test_cvdp12_script_preserves_explicit_zero_sim_feedback_patience(tmp_path: Path):
    project_root = Path(__file__).resolve().parents[1]
    script = project_root / "scripts" / "run_cvdp12_native_param.sh"
    environment = os.environ.copy()
    environment.update(
        {
            "PYTHON_BIN": "/bin/echo",
            "SIM_FEEDBACK_PATIENCE": "0",
            "RESULTS_DIR": str(tmp_path / "results"),
        }
    )

    completed = subprocess.run(
        [str(script)],
        cwd=project_root,
        env=environment,
        text=True,
        capture_output=True,
        check=True,
    )

    assert "--sim-feedback-patience 0" in completed.stdout


def _install_fake_search(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_search = ModuleType("search")
    fake_search.build_user_message = lambda *args, **kwargs: "problem"
    fake_search.classify_failure_record = lambda result: {}
    monkeypatch.setitem(sys.modules, "search", fake_search)


def _process_args() -> SimpleNamespace:
    return SimpleNamespace(
        guided_search=False,
        eval_only=False,
        sim_feedback=False,
        max_turns=2,
        prompt_profile="compact",
        harness="codex-agent",
        model="gpt-5.6-sol",
    )


def test_process_problem_restores_preexisting_candidate_when_runner_start_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    _install_fake_search(monkeypatch)
    monkeypatch.setattr(run_module, "PROJECT_ROOT", tmp_path)

    target = tmp_path / "Generated" / "prob_a.lean"
    target.parent.mkdir()
    target.write_text("preexisting candidate\n", encoding="utf-8")
    monkeypatch.setattr(
        run_module,
        "make_runner",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("runner startup failed")),
    )
    evaluate_calls: list[str] = []

    def evaluate(prob_id: str, run_dir: Path) -> dict:
        evaluate_calls.append(prob_id)
        raise AssertionError("a restored stale candidate must not be evaluated as a new result")

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    record = run_module.process_problem(
        "prob_a",
        args=_process_args(),
        ds=SimpleNamespace(load_problem=lambda prob_id: SimpleNamespace()),
        evaluator=SimpleNamespace(dataset_name="cvdp", evaluate=evaluate),
        run_dir=run_dir,
        skill="",
        repl=None,
    )

    assert evaluate_calls == []
    assert record["sim_status"] == "agent_error"
    assert "runner startup failed" in record["agent_error"]
    assert record["preexisting_generated_restored"] is True
    assert target.read_text(encoding="utf-8") == "preexisting candidate\n"
    assert Path(record["preexisting_generated_backup"]).read_text(encoding="utf-8") == (
        "preexisting candidate\n"
    )
    assert not list(target.parent.glob(f".{target.name}.restore-*"))


def test_process_problem_keeps_new_nonempty_candidate_when_agent_ends_with_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    _install_fake_search(monkeypatch)
    monkeypatch.setattr(run_module, "PROJECT_ROOT", tmp_path)

    target = tmp_path / "Generated" / "prob_a.lean"
    target.parent.mkdir()
    target.write_text("preexisting candidate\n", encoding="utf-8")

    class ProducingRunner:
        def run(self, prompt: str, *, max_turns: int):
            target.write_text("new candidate\n", encoding="utf-8")
            raise RuntimeError("agent ended after writing")

    monkeypatch.setattr(run_module, "make_runner", lambda **kwargs: ProducingRunner())
    evaluate_calls: list[str] = []

    def evaluate(prob_id: str, run_dir: Path) -> dict:
        evaluate_calls.append(prob_id)
        return {
            "prob_id": prob_id,
            "compile_pass": True,
            "sv_extracted": True,
            "lint_pass": True,
            "sim_status": "sim_pass",
            "sim_mismatches": 0,
            "detail": "pass",
        }

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    record = run_module.process_problem(
        "prob_a",
        args=_process_args(),
        ds=SimpleNamespace(load_problem=lambda prob_id: SimpleNamespace()),
        evaluator=SimpleNamespace(dataset_name="cvdp", evaluate=evaluate),
        run_dir=run_dir,
        skill="",
        repl=None,
    )

    assert evaluate_calls == ["prob_a"]
    assert record["sim_status"] == "sim_pass"
    assert "agent ended after writing" in record["agent_error"]
    assert "preexisting_generated_restored" not in record
    assert target.read_text(encoding="utf-8") == "new candidate\n"
    assert Path(record["preexisting_generated_backup"]).read_text(encoding="utf-8") == (
        "preexisting candidate\n"
    )


@pytest.mark.parametrize("generated_text", [None, "  \n\t"])
def test_successful_runner_without_nonempty_candidate_restores_preexisting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    generated_text: str | None,
):
    _install_fake_search(monkeypatch)
    monkeypatch.setattr(run_module, "PROJECT_ROOT", tmp_path)
    target = tmp_path / "Generated" / "prob_a.lean"
    target.parent.mkdir()
    target.write_text("preexisting candidate\n", encoding="utf-8")

    class SuccessfulRunner:
        def run(self, prompt: str, *, max_turns: int) -> AgentStats:
            if generated_text is not None:
                target.write_text(generated_text, encoding="utf-8")
            return AgentStats(turns=1)

    monkeypatch.setattr(run_module, "make_runner", lambda **kwargs: SuccessfulRunner())

    def unexpected_evaluate(prob_id: str, run_dir: Path) -> dict:
        raise AssertionError("the restored candidate must not be evaluated as new")

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    record = run_module.process_problem(
        "prob_a",
        args=_process_args(),
        ds=SimpleNamespace(load_problem=lambda prob_id: SimpleNamespace()),
        evaluator=SimpleNamespace(dataset_name="cvdp", evaluate=unexpected_evaluate),
        run_dir=run_dir,
        skill="",
        repl=None,
    )

    assert record["sim_status"] == "agent_error"
    assert "without creating a non-empty" in record["agent_error"]
    assert record["preexisting_generated_restored"] is True
    assert target.read_text(encoding="utf-8") == "preexisting candidate\n"


def test_prompt_build_exception_restores_preexisting_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    fake_search = ModuleType("search")

    def raise_prompt(*args, **kwargs):
        raise RuntimeError("prompt construction failed")

    fake_search.build_user_message = raise_prompt
    fake_search.classify_failure_record = lambda result: {}
    monkeypatch.setitem(sys.modules, "search", fake_search)
    monkeypatch.setattr(run_module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(
        run_module,
        "make_runner",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("runner construction must not follow prompt failure")
        ),
    )
    target = tmp_path / "Generated" / "prob_a.lean"
    target.parent.mkdir()
    target.write_text("preexisting candidate\n", encoding="utf-8")
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    record = run_module.process_problem(
        "prob_a",
        args=_process_args(),
        ds=SimpleNamespace(load_problem=lambda prob_id: SimpleNamespace()),
        evaluator=SimpleNamespace(dataset_name="cvdp"),
        run_dir=run_dir,
        skill="",
        repl=None,
    )

    assert record["sim_status"] == "agent_error"
    assert "prompt construction failed" in record["agent_error"]
    assert record["preexisting_generated_restored"] is True
    assert target.read_text(encoding="utf-8") == "preexisting candidate\n"


def test_log_parse_exception_does_not_escape_candidate_transaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    _install_fake_search(monkeypatch)
    monkeypatch.setattr(run_module, "PROJECT_ROOT", tmp_path)
    target = tmp_path / "Generated" / "prob_a.lean"
    target.parent.mkdir()
    target.write_text("preexisting candidate\n", encoding="utf-8")

    class FailingRunner:
        def run(self, prompt: str, *, max_turns: int):
            raise RuntimeError("runner failed")

    monkeypatch.setattr(run_module, "make_runner", lambda **kwargs: FailingRunner())
    monkeypatch.setattr(
        run_module,
        "parse_agent_log",
        lambda path: (_ for _ in ()).throw(ValueError("malformed log")),
    )
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    record = run_module.process_problem(
        "prob_a",
        args=_process_args(),
        ds=SimpleNamespace(load_problem=lambda prob_id: SimpleNamespace()),
        evaluator=SimpleNamespace(dataset_name="cvdp"),
        run_dir=run_dir,
        skill="",
        repl=None,
    )

    assert record["sim_status"] == "agent_error"
    assert "runner failed" in record["agent_error"]
    assert "malformed log" in record["agent_error"]
    assert record["preexisting_generated_restored"] is True
    assert target.read_text(encoding="utf-8") == "preexisting candidate\n"


def _guided_args() -> SimpleNamespace:
    return SimpleNamespace(
        guided_search=True,
        eval_only=False,
        search_total_turn_budget=None,
        sim_feedback_turn_budget=None,
        max_turns=1,
        disable_guided_self_test=True,
        guided_self_test_mode="guidance",
        self_test_planner_turns=0,
        harness="codex-agent",
        candidate_search_max=1,
        candidate_stagnation_patience=1,
        sim_feedback=False,
        sim_feedback_max_iters=0,
        sim_feedback_turns_per_iter=None,
        prompt_profile="compact",
        model="gpt-5.6-sol",
    )


def _install_guided_search(
    monkeypatch: pytest.MonkeyPatch,
    *,
    prompt_error: bool,
) -> None:
    fake_search = ModuleType("search")
    fake_search.format_benchmark_interface_contract = lambda info: "interface"
    fake_search.format_context_files = lambda info: "context"

    def build_user_message(*args, **kwargs):
        if prompt_error:
            raise RuntimeError("guided prompt failed")
        return "problem"

    fake_search.build_user_message = build_user_message
    fake_search.eval_progress_key = lambda result: (
        bool(result.get("compile_pass")),
        result.get("sim_status") == "sim_pass",
    )
    fake_search.summarize_eval_result = lambda result: str(result)
    fake_search.build_sim_feedback = lambda **kwargs: "feedback"
    fake_search.classify_failure_record = lambda result: {}
    monkeypatch.setitem(sys.modules, "search", fake_search)


@pytest.mark.parametrize("prompt_error", [False, True])
def test_guided_search_transaction_restores_on_empty_or_prompt_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    prompt_error: bool,
):
    _install_guided_search(monkeypatch, prompt_error=prompt_error)
    monkeypatch.setattr(run_module, "PROJECT_ROOT", tmp_path)
    target = tmp_path / "Generated" / "prob_a.lean"
    target.parent.mkdir()
    target.write_text("preexisting candidate\n", encoding="utf-8")

    class WhitespaceRunner:
        def run(self, prompt: str, *, max_turns: int) -> AgentStats:
            target.write_text(" \n", encoding="utf-8")
            return AgentStats(turns=1)

    monkeypatch.setattr(run_module, "make_runner", lambda **kwargs: WhitespaceRunner())

    def unexpected_evaluate(prob_id: str, run_dir: Path) -> dict:
        raise AssertionError("guided search must not evaluate a restored candidate")

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    record = run_module.process_problem(
        "prob_a",
        args=_guided_args(),
        ds=SimpleNamespace(
            load_problem=lambda prob_id: SimpleNamespace(
                design_name="prob_a",
                prompt_text="spec",
                metadata={},
            )
        ),
        evaluator=SimpleNamespace(dataset_name="cvdp", evaluate=unexpected_evaluate),
        run_dir=run_dir,
        skill="",
        repl=None,
    )

    assert record["sim_status"] == "agent_error"
    assert record["preexisting_generated_restored"] is True
    assert target.read_text(encoding="utf-8") == "preexisting candidate\n"
    if prompt_error:
        assert "guided prompt failed" in record["agent_error"]
    else:
        assert "non-empty Lean candidate" in record["agent_error"]


def test_problem_file_is_stably_deduplicated_before_limit(tmp_path: Path):
    problem_file = tmp_path / "problems.txt"
    problem_file.write_text("prob_a\nprob_b\nprob_a\nprob_c\n", encoding="utf-8")
    args = SimpleNamespace(problem_file=str(problem_file), filter=None, limit=2)

    problems = run_module.discover_problems(args, SimpleNamespace())

    assert problems == ["prob_a", "prob_b"]


def test_same_problem_transactions_serialize_across_threads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    _install_fake_search(monkeypatch)
    monkeypatch.setattr(run_module, "PROJECT_ROOT", tmp_path)
    target = tmp_path / "Generated" / "prob_a.lean"
    target.parent.mkdir()
    target.write_text("preexisting candidate\n", encoding="utf-8")
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    state_lock = threading.Lock()
    first_started = threading.Event()
    release_first = threading.Event()
    created_runners = 0

    class OrderedRunner:
        def __init__(self, index: int):
            self.index = index

        def run(self, prompt: str, *, max_turns: int) -> AgentStats:
            if self.index == 1:
                first_started.set()
                assert release_first.wait(2.0)
            target.write_text(f"candidate-{self.index}\n", encoding="utf-8")
            return AgentStats(turns=1)

    def make_ordered_runner(**kwargs):
        nonlocal created_runners
        with state_lock:
            created_runners += 1
            index = created_runners
        return OrderedRunner(index)

    monkeypatch.setattr(run_module, "make_runner", make_ordered_runner)

    def evaluate(prob_id: str, current_run_dir: Path) -> dict:
        return {
            "prob_id": prob_id,
            "compile_pass": True,
            "sv_extracted": True,
            "lint_pass": True,
            "sim_status": "sim_pass",
            "sim_mismatches": 0,
            "detail": target.read_text(encoding="utf-8").strip(),
        }

    call = lambda: run_module.process_problem(
        "prob_a",
        args=_process_args(),
        ds=SimpleNamespace(load_problem=lambda prob_id: SimpleNamespace()),
        evaluator=SimpleNamespace(dataset_name="cvdp", evaluate=evaluate),
        run_dir=run_dir,
        skill="",
        repl=None,
    )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(call)
        assert first_started.wait(1.0)
        second = pool.submit(call)
        try:
            time.sleep(0.05)
            assert created_runners == 1
        finally:
            release_first.set()
        first_record = first.result(timeout=3.0)
        second_record = second.result(timeout=3.0)

    assert first_record["detail"] == "candidate-1"
    assert second_record["detail"] == "candidate-2"
    assert target.read_text(encoding="utf-8") == "candidate-2\n"


def test_transaction_restore_never_overwrites_concurrent_nonempty_candidate(tmp_path: Path):
    target = tmp_path / "Generated" / "prob_a.lean"
    target.parent.mkdir()
    target.write_text("preexisting candidate\n", encoding="utf-8")
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    with run_module.GeneratedCandidateTransaction(
        tmp_path,
        run_dir,
        "prob_a",
    ) as transaction:
        writer = threading.Thread(
            target=lambda: target.write_text("concurrent candidate\n", encoding="utf-8")
        )
        writer.start()
        writer.join(timeout=1.0)
        assert not writer.is_alive()
        assert transaction.restore_preexisting() is False

    assert target.read_text(encoding="utf-8") == "concurrent candidate\n"
    assert not list(target.parent.glob(".prob_a.lean.preexisting-*"))


@pytest.mark.parametrize("original", [b"", b"  \n\t"])
def test_transaction_restores_empty_preexisting_file_byte_for_byte(
    tmp_path: Path,
    original: bytes,
):
    target = tmp_path / "Generated" / "prob_a.lean"
    target.parent.mkdir()
    target.write_bytes(original)
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    with run_module.GeneratedCandidateTransaction(
        tmp_path,
        run_dir,
        "prob_a",
    ) as transaction:
        assert not target.exists()
        assert transaction.restore_preexisting() is True
        assert target.read_bytes() == original

    assert target.exists()
    assert target.read_bytes() == original


def test_transaction_enter_restores_and_unlocks_after_keyboard_interrupt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    target = tmp_path / "Generated" / "prob_a.lean"
    target.parent.mkdir()
    target.write_text("preexisting candidate\n", encoding="utf-8")
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    monkeypatch.setattr(
        run_module.shutil,
        "copy2",
        lambda *args, **kwargs: (_ for _ in ()).throw(KeyboardInterrupt()),
    )
    with pytest.raises(KeyboardInterrupt):
        with run_module.GeneratedCandidateTransaction(tmp_path, run_dir, "prob_a"):
            raise AssertionError("transaction enter should have been interrupted")

    assert target.read_text(encoding="utf-8") == "preexisting candidate\n"
    # Reacquisition proves the file descriptor/lock was released as well.
    with run_module.GeneratedCandidateTransaction(
        tmp_path,
        run_dir,
        "prob_a",
        read_only=True,
    ):
        assert target.exists()


def test_transaction_restores_when_interrupt_follows_atomic_stage_rename(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    target = tmp_path / "Generated" / "prob_a.lean"
    target.parent.mkdir()
    target.write_text("preexisting candidate\n", encoding="utf-8")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    real_replace = run_module.os.replace
    interrupted = False

    def replace_then_interrupt(source, destination):
        nonlocal interrupted
        real_replace(source, destination)
        if Path(source) == target and not interrupted:
            interrupted = True
            raise KeyboardInterrupt()

    monkeypatch.setattr(run_module.os, "replace", replace_then_interrupt)
    with pytest.raises(KeyboardInterrupt):
        with run_module.GeneratedCandidateTransaction(tmp_path, run_dir, "prob_a"):
            raise AssertionError("transaction enter should have been interrupted")

    assert interrupted
    assert target.read_text(encoding="utf-8") == "preexisting candidate\n"
    assert not list(target.parent.glob(".prob_a.lean.preexisting-*"))


def test_transaction_closes_descriptor_when_flock_acquisition_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    target = tmp_path / "Generated" / "prob_a.lean"
    target.parent.mkdir()
    target.write_text("preexisting candidate\n", encoding="utf-8")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    transaction = run_module.GeneratedCandidateTransaction(
        tmp_path,
        run_dir,
        "prob_a",
    )

    def fail_flock(fd: int, operation: int) -> None:
        raise OSError("lock acquisition failed")

    monkeypatch.setattr(run_module.fcntl, "flock", fail_flock)
    with pytest.raises(OSError, match="lock acquisition failed"):
        transaction.__enter__()

    assert transaction._lock_file is None
    assert not transaction._lock_acquired
    assert target.read_text(encoding="utf-8") == "preexisting candidate\n"


def test_restore_archives_inode_for_writer_that_writes_after_detach(tmp_path: Path):
    target = tmp_path / "Generated" / "prob_a.lean"
    target.parent.mkdir()
    target.write_text("preexisting candidate\n", encoding="utf-8")
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    with run_module.GeneratedCandidateTransaction(
        tmp_path,
        run_dir,
        "prob_a",
    ) as transaction:
        with target.open("w", encoding="utf-8") as late_writer:
            assert transaction.restore_preexisting() is True
            late_writer.write("late concurrent candidate\n")
            late_writer.flush()
            os.fsync(late_writer.fileno())

        assert target.read_text(encoding="utf-8") == "preexisting candidate\n"
        conflicts = list(
            (target.parent / ".cktarchon_conflicts").glob("prob_a-*.lean")
        )
        assert len(conflicts) == 1
        assert conflicts[0].read_text(encoding="utf-8") == (
            "late concurrent candidate\n"
        )


def test_dataset_discovery_deduplicates_before_limit():
    observed_limits: list[int | None] = []

    class Dataset:
        def discover_problems(self, *, limit, filter_re):
            observed_limits.append(limit)
            return ["prob_a", "prob_a", "prob_b", "prob_c"]

    args = SimpleNamespace(problem_file=None, filter=None, limit=2)

    assert run_module.discover_problems(args, Dataset()) == ["prob_a", "prob_b"]
    assert observed_limits == [None]


def test_eval_only_waits_for_active_generation_transaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    _install_fake_search(monkeypatch)
    expected_ports = [
        ("output", "logic [(NUM_DICE * BIT_WIDTH)-1:0]", "dice_values")
    ]
    sys.modules["search"]._benchmark_expected_ports = (
        lambda info: expected_ports
    )
    monkeypatch.setattr(run_module, "PROJECT_ROOT", tmp_path)
    target = tmp_path / "Generated" / "prob_a.lean"
    target.parent.mkdir()
    target.write_text("preexisting candidate\n", encoding="utf-8")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    generation_entered = threading.Event()
    release_generation = threading.Event()
    evaluated = threading.Event()
    received_ports: list[list[tuple[str, str, str]] | None] = []

    def hold_generation_transaction():
        with run_module.GeneratedCandidateTransaction(tmp_path, run_dir, "prob_a"):
            generation_entered.set()
            assert release_generation.wait(2.0)

    eval_args = _process_args()
    eval_args.eval_only = True

    def evaluate(
        prob_id: str,
        current_run_dir: Path,
        *,
        benchmark_ports=None,
    ) -> dict:
        received_ports.append(benchmark_ports)
        evaluated.set()
        return {
            "prob_id": prob_id,
            "compile_pass": True,
            "sv_extracted": True,
            "lint_pass": True,
            "sim_status": "sim_pass",
            "sim_mismatches": 0,
            "detail": target.read_text(encoding="utf-8").strip(),
        }

    with ThreadPoolExecutor(max_workers=2) as pool:
        holder = pool.submit(hold_generation_transaction)
        assert generation_entered.wait(1.0)
        reader = pool.submit(
            run_module.process_problem,
            "prob_a",
            args=eval_args,
            ds=SimpleNamespace(load_problem=lambda prob_id: SimpleNamespace()),
            evaluator=SimpleNamespace(dataset_name="cvdp", evaluate=evaluate),
            run_dir=run_dir,
            skill="",
            repl=None,
        )
        try:
            assert not evaluated.wait(0.1)
        finally:
            release_generation.set()
        holder.result(timeout=3.0)
        record = reader.result(timeout=3.0)

    assert record["detail"] == "preexisting candidate"
    assert received_ports == [expected_ports]


def test_candidate_lock_serializes_a_real_subprocess(tmp_path: Path):
    target = tmp_path / "Generated" / "prob_a.lean"
    target.parent.mkdir()
    target.write_text("preexisting candidate\n", encoding="utf-8")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    ready = tmp_path / "subprocess-ready"
    marker = tmp_path / "subprocess-acquired"
    code = (
        "from pathlib import Path; import sys; "
        "from cktarchon.run import GeneratedCandidateTransaction; "
        "root, run_dir, ready, marker = map(Path, sys.argv[1:]); "
        "ready.write_text('ready'); "
        "tx = GeneratedCandidateTransaction(root, run_dir, 'prob_a', read_only=True); "
        "tx.__enter__(); marker.write_text('acquired'); tx.__exit__(None, None, None)"
    )

    with run_module.GeneratedCandidateTransaction(tmp_path, run_dir, "prob_a"):
        process = subprocess.Popen(
            [
                sys.executable,
                "-c",
                code,
                str(tmp_path),
                str(run_dir),
                str(ready),
                str(marker),
            ],
            cwd=Path(__file__).resolve().parents[1],
        )
        deadline = time.monotonic() + 2.0
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists()
        time.sleep(0.05)
        assert process.poll() is None
        assert not marker.exists()

    assert process.wait(timeout=3.0) == 0
    assert marker.read_text(encoding="utf-8") == "acquired"
