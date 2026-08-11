from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .env import ensure_runtime_env, load_env_file

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _agent_path() -> None:
    path = PROJECT_ROOT / "agent"
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


def cmd_lean_check(args: argparse.Namespace) -> None:
    ensure_runtime_env()
    _agent_path()
    from lean_repl import LeanREPL

    path = PROJECT_ROOT / args.path
    with LeanREPL(project_dir=PROJECT_ROOT) as repl:
        result = repl.check_file(path)
    payload = {
        "passed": result.passed,
        "complete": result.complete,
        "elapsed": result.elapsed,
        "errors": result.errors,
        "warnings": result.warnings,
        "has_verilog": bool(result.verilog),
        "error_text": result.error_text,
    }
    print(json.dumps(payload, indent=2))


def cmd_eval(args: argparse.Namespace) -> None:
    ensure_runtime_env()
    _agent_path()
    from dataset import Dataset
    from evaluator import Evaluator

    load_env_file(PROJECT_ROOT / "key.env")
    ds = Dataset(args.dataset, project_root=PROJECT_ROOT)
    evaluator = Evaluator(project_root=PROJECT_ROOT, dataset=args.dataset, dataset_obj=ds)
    run_dir = Path(args.run_dir).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    print(json.dumps(evaluator.evaluate(args.prob_id, run_dir), indent=2, default=str))


def main() -> None:
    p = argparse.ArgumentParser(description="CktArchon utility tools")
    sub = p.add_subparsers(dest="cmd", required=True)
    lean = sub.add_parser("lean-check")
    lean.add_argument("path")
    lean.set_defaults(func=cmd_lean_check)
    ev = sub.add_parser("eval")
    ev.add_argument("prob_id")
    ev.add_argument("--dataset", default="cvdp")
    ev.add_argument("--run-dir", required=True)
    ev.set_defaults(func=cmd_eval)
    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
