from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_p3_ppa_smoke_script_imports_from_arbitrary_cwd(tmp_path: Path):
    script = PROJECT_ROOT / "scripts" / "verify_p3_ppa_orfs.py"
    completed = subprocess.run(
        [sys.executable, str(script), "--help"],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "place and route after synthesis" in completed.stdout


def test_cvdp12_runner_supports_native_codex_without_api_key(tmp_path: Path):
    script = PROJECT_ROOT / "experiments" / "run_p3_cvdp12.sh"
    fake_python = tmp_path / "python"
    fake_codex = tmp_path / "codex"
    archon_src = tmp_path / "archon" / "src"
    archon_src.mkdir(parents=True)
    fake_python.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' \"$@\"\n"
        "printf 'profile=%s\\n' \"$CVDP_HARNESS_PROFILE\" >&2\n",
        encoding="utf-8",
    )
    fake_codex.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_python.chmod(0o755)
    fake_codex.chmod(0o755)
    env = {
        **os.environ,
        "HARNESS": "codex-agent",
        "MODEL": "gpt-5.6-sol",
        "PYTHON_BIN": str(fake_python),
        "CODEX_BIN": str(fake_codex),
        "ARCHON_SRC": str(archon_src),
        "CODEX_EFFORT": "ultra",
        "RESULTS_DIR": str(tmp_path / "results"),
        "KEY_ENV": str(tmp_path / "does-not-exist.env"),
    }

    completed = subprocess.run(
        ["bash", str(script)],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    args = completed.stdout.splitlines()
    assert args[:3] == ["-m", "cktarchon.run", "--dataset"]
    assert args[args.index("--harness") + 1] == "codex-agent"
    assert args[args.index("--model") + 1] == "gpt-5.6-sol"
    assert args[args.index("--codex-effort") + 1] == "ultra"
    assert args[args.index("--max-turns") + 1] == "10"
    assert args[args.index("--sim-feedback-max-iters") + 1] == "9"
    assert args[args.index("--sim-feedback-turn-budget") + 1] == "90"
    assert args[args.index("--search-total-turn-budget") + 1] == "100"
    assert args[args.index("--cvdp-harness-profile") + 1] == "race-safe-v1"
    assert "--native-parameter-sweep" in args
    assert "--no-codex-chat-proxy" in args
    assert "--key-env" not in args
    assert "profile=race-safe-v1" in completed.stderr
