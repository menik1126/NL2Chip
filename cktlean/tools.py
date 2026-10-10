from __future__ import annotations

import argparse
import json
import re
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
    verilog_blocks = list(getattr(result, "verilog_modules", []) or [])
    if not verilog_blocks and result.verilog:
        verilog_blocks = [result.verilog]
    module_names = sorted({
        match.group(1)
        for block in verilog_blocks
        for match in re.finditer(r"\bmodule\s+([A-Za-z_]\w*)\b", str(block))
    })
    required = sorted(set(args.require_module or []))
    missing = sorted(set(required) - set(module_names))
    payload = {
        "passed": bool(result.passed and not missing),
        "complete": result.complete,
        "elapsed": result.elapsed,
        "errors": result.errors,
        "warnings": result.warnings,
        "has_verilog": bool(result.verilog),
        "verilog_module_count": len(module_names),
        "verilog_modules": module_names,
        "required_verilog_modules": required,
        "missing_verilog_modules": missing,
        "error_text": result.error_text,
    }
    print(json.dumps(payload, indent=2))
    if required and not payload["passed"]:
        raise SystemExit(1)


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
    p = argparse.ArgumentParser(description="CKTLean utility tools")
    sub = p.add_subparsers(dest="cmd", required=True)
    lean = sub.add_parser("lean-check")
    lean.add_argument("path")
    lean.add_argument(
        "--require-module",
        action="append",
        default=[],
        help="Require a generated Verilog module; repeat for a specialization family.",
    )
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
