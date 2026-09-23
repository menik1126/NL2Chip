#!/usr/bin/env python3
"""Codex-callable client for the stepwise Lean proof REPL gateway."""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request


def format_result(payload: dict) -> str:
    if payload.get("error") and "passed" not in payload:
        return f"Error: {payload['error']}"
    lines = []
    if payload.get("passed"):
        tag = "COMPLETE" if payload.get("complete") else "OK (has sorry)"
        lines.append(f"✓ {tag} ({payload.get('elapsed', 0):.2f}s)")
    else:
        errors = payload.get("errors") or []
        lines.append(f"✗ FAILED ({payload.get('elapsed', 0):.2f}s, {len(errors)} errors)")
    for err in payload.get("errors") or []:
        pos = err.get("pos") or {}
        lines.append(
            f"  [error line {pos.get('line', '?')}:{pos.get('column', '?')}] "
            f"{err.get('data', '')}"
        )
    for warn in payload.get("warnings") or []:
        pos = warn.get("pos") or {}
        data = warn.get("data", "")
        if "sorry" in data or "failed" in data:
            lines.append(
                f"  [warning line {pos.get('line', '?')}:{pos.get('column', '?')}] {data}"
            )
    sorries = payload.get("sorries") or []
    if sorries:
        lines.append("")
        lines.append("=== Proof Goals ===")
        for i, item in enumerate(sorries):
            pos = item.get("pos") or {}
            goal = item.get("goal") or "(no goal)"
            lines.append(f"Goal {i} (line {pos.get('line', '?')}:{pos.get('column', '?')}):")
            for gl in str(goal).splitlines():
                lines.append(f"  {gl}")
    if payload.get("verilog"):
        lines.append("")
        lines.append("=== Generated Verilog ===")
        lines.append(str(payload["verilog"])[:2000])
    lines.append("")
    lines.append(f"env: {payload.get('env')}")
    return "\n".join(lines)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--env", type=int, default=None)
    p.add_argument("--code-file", default=None)
    p.add_argument("--reset", action="store_true")
    args = p.parse_args()
    url = os.environ.get("PROOF_REPL_URL", "").rstrip("/")
    if not url:
        print("Error: PROOF_REPL_URL is not set", file=sys.stderr)
        return 2
    if args.reset:
        req = urllib.request.Request(url + "/reset", data=b"{}", method="POST")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=180) as resp:
            print(resp.read().decode())
        return 0
    if args.code_file:
        code = open(args.code_file, encoding="utf-8").read()
    else:
        code = sys.stdin.read()
    body = json.dumps({"code": code, "env": args.env}).encode()
    req = urllib.request.Request(url + "/step", data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=180) as resp:
        payload = json.loads(resp.read().decode())
    print(format_result(payload))
    return 0 if payload.get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
