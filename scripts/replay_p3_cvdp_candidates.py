#!/usr/bin/env python3
"""Replay pinned Lean candidates through the real CVDP evaluator.

The replay is deliberately model-free. It stages the requested candidates in
Generated/, runs CktArchon's eval-only path, and restores every pre-existing
Generated file even when evaluation fails or is interrupted.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


REPO_ROOT = Path(__file__).resolve().parents[1]


def read_problem_ids(problem_file: Path) -> list[str]:
    problem_ids = [
        line.strip()
        for line in problem_file.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if not problem_ids:
        raise ValueError(f"No problem IDs found in {problem_file}")
    if len(problem_ids) != len(set(problem_ids)):
        raise ValueError(f"Duplicate problem IDs found in {problem_file}")
    return problem_ids


@contextmanager
def staged_candidates(candidate_dir: Path, problem_ids: list[str]) -> Iterator[None]:
    generated_dir = REPO_ROOT / "Generated"
    generated_dir.mkdir(exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="p3-cvdp-replay-") as tmp:
        backup_dir = Path(tmp)
        preexisting: set[str] = set()

        for problem_id in problem_ids:
            source = candidate_dir / f"{problem_id}.lean"
            if not source.is_file():
                raise FileNotFoundError(f"Pinned candidate missing: {source}")

            target = generated_dir / source.name
            if target.exists():
                shutil.copy2(target, backup_dir / target.name)
                preexisting.add(problem_id)
            shutil.copy2(source, target)

        try:
            yield
        finally:
            for problem_id in problem_ids:
                target = generated_dir / f"{problem_id}.lean"
                if problem_id in preexisting:
                    shutil.copy2(backup_dir / target.name, target)
                else:
                    target.unlink(missing_ok=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--candidate-dir",
        type=Path,
        default=REPO_ROOT / "experiments" / "p3_replay_candidates" / "gpt56sol_20260816",
    )
    parser.add_argument(
        "--problem-file",
        type=Path,
        default=REPO_ROOT / "experiments" / "cvdp_parameterized_12.txt",
    )
    parser.add_argument("--results-dir", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument(
        "--native-parameter-sweep",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Enable native parameter-sweep preflight (disable for non-parameterized cases).",
    )
    parser.add_argument(
        "--key-env",
        type=Path,
        default=REPO_ROOT / "key.env",
        help="Loaded by the shared runner; no API request is made in eval-only mode.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    problem_file = args.problem_file.resolve()
    candidate_dir = args.candidate_dir.resolve()
    problem_ids = read_problem_ids(problem_file)
    if args.workers < 1:
        raise ValueError("--workers must be positive")

    command = [
        sys.executable,
        "-m",
        "cktarchon.run",
        "--dataset",
        "cvdp",
        "--problem-file",
        str(problem_file),
        "--results-dir",
        str(args.results_dir.resolve()),
        "--workers",
        str(args.workers),
        "--key-env",
        str(args.key_env.resolve()),
        "--eval-only",
        "--native-formal-policy",
        "off",
        "--native-cppsim-policy",
        "off",
        "--native-ppa-policy",
        "off",
    ]
    if args.native_parameter_sweep:
        command.append("--native-parameter-sweep")

    with staged_candidates(candidate_dir, problem_ids):
        completed = subprocess.run(command, cwd=REPO_ROOT, check=False)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
