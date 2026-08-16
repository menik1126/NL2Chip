from pathlib import Path
from types import SimpleNamespace

import pytest

import cktarchon.harness as harness
from cktarchon.harness import AnthropicHarnessRunner


class _RecordingRepl:
    def __init__(self) -> None:
        self.paths: list[Path] = []

    def check_file(self, path: Path) -> SimpleNamespace:
        self.paths.append(path)
        return SimpleNamespace(
            passed=True,
            complete=True,
            summary="PASS COMPLETE",
            error_text="",
            errors=[],
            warnings=[],
            verilog="module prob_a; endmodule",
        )


def _runner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    repl: _RecordingRepl,
) -> AnthropicHarnessRunner:
    monkeypatch.setattr(harness, "ensure_runtime_env", lambda: None)
    monkeypatch.setattr(harness.anthropic, "Anthropic", lambda **_: object())
    return AnthropicHarnessRunner(
        project_root=tmp_path,
        prob_id="prob_a",
        model="test-model",
        role="test-role",
        log_base=tmp_path / "logs" / "target-guard",
        system_prompt="test",
        lean_repl=repl,
        local_guardrails=True,
        public_dev_feedback=True,
    )


def test_public_dev_exact_target_meta_io_never_reaches_repl(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "Generated" / "prob_a.lean"
    target.parent.mkdir()
    malicious = (
        "import Sparkle\n"
        "#eval IO.FS.readFile \"experiments/dataset.jsonl\"\n"
        "#synthesizeVerilog prob_a\n"
    )
    target.write_text(malicious, encoding="utf-8")
    repl = _RecordingRepl()
    runner = _runner(tmp_path, monkeypatch, repl)

    result = runner._lean_check(path="Generated/prob_a.lean")

    assert result.startswith("Error: public-dev target lean_check rejected")
    assert repl.paths == []


def test_public_dev_target_write_and_edit_reject_meta_io_before_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "Generated" / "prob_a.lean"
    target.parent.mkdir()
    safe = "import Sparkle\ndef prob_a := 1\n#synthesizeVerilog prob_a\n"
    target.write_text(safe, encoding="utf-8")
    repl = _RecordingRepl()
    runner = _runner(tmp_path, monkeypatch, repl)

    write_result = runner._write_file(
        "Generated/prob_a.lean",
        "import Sparkle\nrun_tac IO.println \"leak\"\n",
    )
    edit_result = runner._edit_file(
        "Generated/prob_a.lean",
        "def prob_a := 1",
        "#reduce IO.FS.readFile \"experiments/dataset.jsonl\"",
    )

    assert write_result.startswith("Error: public-dev target write rejected")
    assert edit_result.startswith("Error: public-dev target edit rejected")
    assert target.read_text(encoding="utf-8") == safe
    assert repl.paths == []
