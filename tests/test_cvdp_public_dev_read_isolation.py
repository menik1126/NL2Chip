from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import cktarchon.harness as harness
from cktarchon.harness import AnthropicHarnessRunner


class _Repl:
    def __init__(self) -> None:
        self.paths: list[Path] = []
        self.code: list[str] = []

    @staticmethod
    def _result() -> SimpleNamespace:
        return SimpleNamespace(
            passed=True,
            complete=True,
            summary="PASS COMPLETE",
            error_text="",
            errors=[],
            warnings=[],
            verilog="module prob_a; endmodule",
        )

    def check_file(self, path: Path) -> SimpleNamespace:
        self.paths.append(path)
        return self._result()

    def check_code(self, code: str) -> SimpleNamespace:
        self.code.append(code)
        return self._result()


def _runner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    public_dev_feedback: bool,
    local_guardrails: bool = True,
    repl: object | None = None,
) -> AnthropicHarnessRunner:
    monkeypatch.setattr(harness, "ensure_runtime_env", lambda: None)
    monkeypatch.setattr(harness.anthropic, "Anthropic", lambda **_: object())
    return AnthropicHarnessRunner(
        project_root=tmp_path,
        prob_id="prob_a",
        model="test-model",
        role="test-role",
        log_base=tmp_path / "logs" / "public-read",
        system_prompt="test",
        lean_repl=repl,
        local_guardrails=local_guardrails,
        public_dev_feedback=public_dev_feedback,
    )


def test_public_dev_browse_tools_use_canonical_public_allowlist(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    public_files = {
        "Generated/prob_a.lean": "OWN_TARGET\n",
        "Sparkle/Core.lean": "PUBLIC_SPARKLE\n",
        "Benchmark/RTLIdioms.lean": "PUBLIC_BENCHMARK\n",
        "Examples/Demo.lean": "PUBLIC_EXAMPLE\n",
        "Tests/Unit.lean": "PUBLIC_TEST\n",
        "cktarchon_work/prob_a/note.txt": "PUBLIC_WORK\n",
        "lakefile.lean": "PUBLIC_BUILD\n",
        "docs/Troubleshooting_Synthesis.md": "PUBLIC_TROUBLESHOOTING\n",
    }
    hidden_files = {
        "Generated/prob_b.lean": "HIDDEN_OTHER_GENERATED\n",
        "agent/secret.py": "HIDDEN_AGENT_CANARY\n",
        "tests/secret.py": "HIDDEN_LOWER_TEST_CANARY\n",
        "experiments/dataset.jsonl": "HIDDEN_EXPERIMENT_CANARY\n",
        "benchmarks/dataset.jsonl": "HIDDEN_BENCHMARK_CANARY\n",
        "results/cvdp_sim/secret.txt": "HIDDEN_RESULT_CANARY\n",
        "cktarchon_work/prob_b/note.txt": "HIDDEN_OTHER_WORK\n",
        "docs/Other.md": "HIDDEN_OTHER_DOC\n",
    }
    for rel, content in {**public_files, **hidden_files}.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    (tmp_path / "Benchmark" / "hidden_alias.jsonl").symlink_to(
        tmp_path / "experiments" / "dataset.jsonl"
    )

    runner = _runner(tmp_path, monkeypatch, public_dev_feedback=True)

    for rel, content in public_files.items():
        assert content.strip() in runner._read_file(rel)
    for rel, content in hidden_files.items():
        result = runner._read_file(rel)
        assert result.startswith("Error:")
        assert content.strip() not in result

    for attack in (
        "Benchmark/../agent/secret.py",
        "Benchmark/hidden_alias.jsonl",
        Path("..") / tmp_path.name / "experiments" / "dataset.jsonl",
    ):
        result = runner._read_file(str(attack))
        assert result.startswith("Error:")
        assert "HIDDEN_" not in result

    assert runner._list_directory("Generated") == "prob_a.lean"
    root_listing = runner._list_directory(".")
    assert "Benchmark/" in root_listing
    assert "Generated/" in root_listing
    assert "agent/" not in root_listing
    assert "experiments/" not in root_listing
    assert "results/" not in root_listing

    assert "PUBLIC_BENCHMARK" in runner._grep("PUBLIC_", ".")
    assert runner._grep("HIDDEN_", ".") == "No matches for 'HIDDEN_'"
    assert runner._grep(".", "experiments").startswith("Error:")

    broad_glob = runner._glob("**/*")
    assert "Benchmark/RTLIdioms.lean" in broad_glob
    assert "Generated/prob_a.lean" in broad_glob
    assert "Generated/prob_b.lean" not in broad_glob
    assert "experiments/" not in broad_glob
    assert "benchmarks/" not in broad_glob
    assert "results/" not in broad_glob
    assert runner._glob("experiments/**/*.jsonl").startswith("Error:")


@pytest.mark.parametrize(
    "code",
    [
        "#eval 1",
        "#reduce Nat.succ 1",
        "run_tac exact pure ()",
        "elab \"x\" : command => pure ()",
        "macro \"x\" : term => `(1)",
        "unsafe def x := 1",
        "def x : IO Unit := pure ()",
        "def x := System.FilePath.mk \"x\"",
        "def x := readFile \"experiments/secret\"",
        "def x := include_str \"experiments/secret\"",
        "import Hidden.Module",
        "extern \"secret\" x : Nat",
    ],
)
def test_public_dev_inline_lean_check_rejects_obvious_meta_and_io(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    code: str,
) -> None:
    repl = _Repl()
    runner = _runner(
        tmp_path,
        monkeypatch,
        public_dev_feedback=True,
        repl=repl,
    )

    result = runner._lean_check(code=code)

    assert result.startswith("Error: public-dev inline lean_check rejected")
    assert repl.code == []


def test_public_dev_lean_check_allows_safe_inline_and_current_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "Generated" / "prob_a.lean"
    target.parent.mkdir()
    target.write_text(
        "def prob_a := 1\n#synthesizeVerilog prob_a\n",
        encoding="utf-8",
    )
    public_example = tmp_path / "Benchmark" / "Example.lean"
    public_example.parent.mkdir()
    public_example.write_text("def example := 1\n", encoding="utf-8")
    repl = _Repl()
    runner = _runner(
        tmp_path,
        monkeypatch,
        public_dev_feedback=True,
        repl=repl,
    )
    safe_inline = (
        "-- #eval and IO in comments are inert\n"
        "def note := \"System.readFile is only text\"\n"
        "def prob_a := 1\n"
        "#synthesizeVerilog prob_a\n"
    )

    assert runner._lean_check(code=safe_inline).startswith("PASS COMPLETE")
    assert len(repl.code) == 1
    assert runner._lean_check(path="Generated/prob_a.lean").startswith(
        "PASS COMPLETE"
    )
    assert repl.paths == [target.resolve()]
    assert runner._lean_check(path="Benchmark/Example.lean").startswith(
        "Error: public-dev lean_check path is restricted"
    )


def test_public_dev_flag_requires_local_guardrails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValueError, match="requires local_guardrails"):
        _runner(
            tmp_path,
            monkeypatch,
            public_dev_feedback=True,
            local_guardrails=False,
        )


def test_public_dev_flag_off_preserves_legacy_repository_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = tmp_path / "experiments" / "dataset.jsonl"
    secret.parent.mkdir()
    secret.write_text("LEGACY_VISIBLE\n", encoding="utf-8")
    runner = _runner(
        tmp_path,
        monkeypatch,
        public_dev_feedback=False,
        local_guardrails=False,
    )

    assert runner._read_file("experiments/dataset.jsonl") == "LEGACY_VISIBLE\n"
    assert "experiments/dataset.jsonl" in runner._glob("experiments/*.jsonl")
    assert "LEGACY_VISIBLE" in runner._grep("LEGACY_VISIBLE", ".")
