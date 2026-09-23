#!/usr/bin/env python3
"""No-harness CktLean baseline on repaired Sparkle (gpt-5.6-sol ultra, jing SOCKS).

Public spec only; hidden CVDP harness hidden; no compile-only / sim / semantic
repair. Same 100-turn budget is all generation. Does not overwrite frozen trees.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
import shutil
import subprocess
import sys

PROJECT = Path("/home/sgli/work/NL2Chip_openlux_repair_state_20260914")
PRIVATE = Path("/home/sgli/work/nl2chip_noharness_cktlean_private_20260920")
NL2CHIP = Path("/home/sgli/work/NL2Chip")
PYTHON = Path("/home/sgli/work/NL2Chip/.venv/bin/python")
OUT = Path("/home/sgli/work/NL2Chip_rebuttal_artifacts/2026-09-20/sparkle_noharness_cktlean_fourds")
ARCHON_SRC = Path("/home/sgli/work/archon-official/src")
WRAPPER = PRIVATE / "codex_chatgpt_socks.py"
DUMMY_KEY = Path("/home/sgli/work/nl2chip_chatgpt_socks_private_20260918/dummy.key.env")
CVDP168 = Path("/home/sgli/work/cvdp_mainline_168_problem_ids.txt")
SOCKS_SRC = Path("/home/sgli/work/nl2chip_chatgpt_socks_private_20260918/codex_chatgpt_socks.py")

WORKERS = int(os.environ.get("FOURDS_WORKERS", "4"))

JOBS = (
    ("verilogeval", "legacy", 156, None),
    ("rtllm", "legacy", 50, None),
    ("resbench", "legacy", 56, None),
    ("cvdp", "public-spec-v2", 168, CVDP168),
)


def environment() -> dict[str, str]:
    env = os.environ.copy()
    env["NL2CHIP_ISOLATION_ROOT"] = str(PRIVATE / "agent_state")
    env["PYTHONPATH"] = str(PROJECT) + ":" + str(PROJECT / "agent")
    env["PATH"] = (
        "/home/sgli/.local/bin:/home/sgli/.elan/toolchains/"
        "leanprover--lean4---v4.28.0-rc1/bin:" + env.get("PATH", "")
    )
    env["CVDP_HARNESS_PROFILE"] = "race-safe-v1"
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


def symlink_datasets() -> None:
    for name in ("verilog-eval", "RTLLM", "ResBench"):
        dest = PROJECT / name
        src = NL2CHIP / name
        if dest.exists() or dest.is_symlink():
            continue
        dest.symlink_to(src)


def command(dataset: str, policy: str, results_dir: Path, problem_file: Path | None) -> list[str]:
    cmd = [
        str(PYTHON), "-u", "-m", "cktarchon.run",
        "--dataset", dataset,
        "--results-dir", str(results_dir),
        "--workers", str(WORKERS),
        "--resume",
        "--resume-mode", "completed",
        "--interface-prompt-policy", policy,
        "--cvdp-harness-profile", "race-safe-v1",
        "--model", "gpt-5.6-sol",
        "--max-tokens", "16384",
        "--harness", "codex-agent",
        "--max-turns", "100",
        "--prompt-profile", "compact",
        "--archon-src", str(ARCHON_SRC),
        "--codex-bin", str(WRAPPER),
        "--codex-effort", "ultra",
        "--codex-sandbox", "workspace-write",
        "--no-codex-chat-proxy",
        "--hide-cvdp-harness-from-agent",
        "--key-env", str(DUMMY_KEY),
    ]
    if problem_file is not None:
        cmd.extend(["--problem-file", str(problem_file)])
    return cmd


def main() -> int:
    subprocess.check_call(["bash", "/home/sgli/work/codex_jing_chatgpt_probe/ensure_socks.sh"])
    prepare_private()
    symlink_datasets()
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = os.environ.get("FOURDS_STAMP") or datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    env = environment()
    launched = []
    rc = 0
    for dataset, policy, expected, problem_file in JOBS:
        subprocess.check_call(["bash", "/home/sgli/work/codex_jing_chatgpt_probe/ensure_socks.sh"])
        results = OUT / f"{dataset}_{stamp}"
        results.mkdir(parents=True, exist_ok=True)
        log_path = results / "launcher.log"
        cmd = command(dataset, policy, results, problem_file)
        entry = {
            "dataset": dataset,
            "expected": expected,
            "workers": WORKERS,
            "results": str(results),
            "cmd": cmd,
        }
        print(json.dumps({"phase": "start", **{k: entry[k] for k in entry if k != "cmd"}}), flush=True)
        with log_path.open("ab") as log:
            code = subprocess.call(
                cmd,
                cwd=str(PROJECT),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
        entry["exit_code"] = code
        launched.append(entry)
        (OUT / "launch.json").write_text(json.dumps({
            "created": datetime.now(timezone.utc).isoformat(),
            "protocol": "noharness-cktlean-repaired-sparkle",
            "model": "gpt-5.6-sol",
            "effort": "ultra",
            "sim_feedback": False,
            "semantic": False,
            "generation_turn_cap": None,
            "max_turns": 100,
            "workers": WORKERS,
            "jobs": launched,
        }, indent=2) + "\n")
        if code != 0:
            rc = code
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
