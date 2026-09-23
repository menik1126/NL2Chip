#!/usr/bin/env python3
"""CVDP joint semantic+compile critiques, then one repair on the merge.

Does not patch the live precompile PD tree. Fresh isolation.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT = Path("/home/sgli/work/NL2Chip_sparkle_joint_sem_compile_20260921")
PRIVATE = Path("/home/sgli/work/nl2chip_joint_sem_compile_private_20260921")
NL2CHIP = Path("/home/sgli/work/NL2Chip")
PYTHON = Path("/home/sgli/work/NL2Chip/.venv/bin/python")
OUT = Path("/home/sgli/work/NL2Chip_rebuttal_artifacts/2026-09-21/sparkle_joint_sem_compile_cvdp")
ARCHON_SRC = Path("/home/sgli/work/archon-official/src")
WRAPPER = PRIVATE / "codex_chatgpt_socks.py"
DUMMY_KEY = Path("/home/sgli/work/nl2chip_chatgpt_socks_private_20260918/dummy.key.env")
CVDP168 = Path("/home/sgli/work/cvdp_mainline_168_problem_ids.txt")
SOCKS_SRC = Path("/home/sgli/work/nl2chip_chatgpt_socks_private_20260918/codex_chatgpt_socks.py")
WORKERS = int(os.environ.get("JOINT_WORKERS", "2"))


def environment() -> dict[str, str]:
    env = os.environ.copy()
    env["NL2CHIP_ISOLATION_ROOT"] = str(PRIVATE / "agent_state")
    env["PYTHONPATH"] = str(PROJECT) + ":" + str(PROJECT / "agent")
    env["PATH"] = (
        "/home/sgli/.local/bin:/home/sgli/.elan/toolchains/"
        "leanprover--lean4---v4.28.0-rc1/bin:" + env.get("PATH", "")
    )
    env["CVDP_HARNESS_PROFILE"] = "race-safe-v1"
    env["CVDP_DATASET_FILE"] = str(
        Path("/home/sgli/work/benchmarks/cvdp-benchmark-dataset/"
             "cvdp_v1.1.0_nonagentic_code_generation_no_commercial.jsonl")
    )
    for name in (
        "OPENLUX_API_KEY", "OPENLUX_BASE_URL", "CODEX_GATEWAY_API_KEY",
        "OPENAI_API_KEY", "OPENAI_BASE_URL",
    ):
        env.pop(name, None)
    return env


def prepare_private() -> None:
    PRIVATE.mkdir(parents=True, exist_ok=True)
    os.chmod(PRIVATE, 0o700)
    (PRIVATE / "agent_state").mkdir(exist_ok=True)
    text = SOCKS_SRC.read_text()
    text = text.replace(
        'ROOT = Path("/home/sgli/work/NL2Chip_openlux_repair_state_20260914")',
        f'ROOT = Path("{PROJECT}")',
    )
    text = text.replace(
        'ISOLATION_PARENT = Path("/home/sgli/work/nl2chip_chatgpt_socks_private_20260918/agent_state")',
        f'ISOLATION_PARENT = Path("{PRIVATE / "agent_state"}")',
    )
    WRAPPER.write_text(text)
    os.chmod(WRAPPER, 0o700)


def main() -> int:
    if "--joint-semantic-compile-feedback" not in (PROJECT / "cktarchon" / "run.py").read_text():
        raise SystemExit("run.py missing joint flag")
    subprocess.check_call(["bash", "/home/sgli/work/codex_jing_chatgpt_probe/ensure_socks.sh"])
    prepare_private()
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    results = OUT / f"cvdp_{stamp}"
    results.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(PYTHON), "-u", "-m", "cktarchon.run",
        "--dataset", "cvdp",
        "--results-dir", str(results),
        "--workers", str(WORKERS),
        "--resume", "--resume-mode", "completed",
        "--interface-prompt-policy", "public-spec-v2",
        "--cvdp-harness-profile", "race-safe-v1",
        "--model", "gpt-5.6-sol",
        "--max-tokens", "16384",
        "--harness", "codex-agent",
        "--max-turns", "40",
        "--total-turn-budget", "100",
        "--generation-turn-cap", "40",
        "--prompt-profile", "compact",
        "--archon-src", str(ARCHON_SRC),
        "--codex-bin", str(WRAPPER),
        "--codex-effort", "ultra",
        "--codex-sandbox", "workspace-write",
        "--no-codex-chat-proxy",
        "--hide-cvdp-harness-from-agent",
        "--key-env", str(DUMMY_KEY),
        "--sim-feedback",
        "--feedback-mode", "compile-only",
        "--joint-semantic-compile-feedback",
        "--sim-feedback-max-iters", "9",
        "--sim-feedback-turns-per-iter", "10",
        "--sim-feedback-patience", "0",
        "--problem-file", str(CVDP168),
    ]
    log_path = results / "launcher.log"
    print(json.dumps({"phase": "start", "results": str(results), "workers": WORKERS}), flush=True)
    with log_path.open("ab") as log:
        rc = subprocess.call(cmd, cwd=str(PROJECT), env=environment(), stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
