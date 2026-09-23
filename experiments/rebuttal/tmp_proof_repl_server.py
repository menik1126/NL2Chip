#!/usr/bin/env python3
"""Persistent Lean REPL HTTP gateway for stepwise proof env chaining."""
from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys

PROJECT = Path("/home/sgli/work/NL2Chip_sparkle_proof_ablation_20260919")
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "agent"))

from lean_repl import LeanREPL  # noqa: E402


def serialize(result) -> dict:
    return {
        "passed": result.passed,
        "complete": result.complete,
        "errors": result.errors,
        "warnings": result.warnings,
        "sorries": result.sorries,
        "env": result.env,
        "verilog": (result.verilog or "")[:4000],
        "elapsed": result.elapsed,
        "error_text": result.error_text,
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, required=True)
    p.add_argument("--project", default=str(PROJECT))
    args = p.parse_args()
    project = Path(args.project)
    repl = LeanREPL(project_dir=project)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *rest) -> None:
            sys.stderr.write("[repl %s] " % args.port + (fmt % rest) + "\n")

        def _read_json(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            if n <= 0:
                return {}
            return json.loads(self.rfile.read(n).decode())

        def _write(self, code: int, payload: dict) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:  # noqa: N802
            try:
                if self.path == "/reset":
                    repl.restart()
                    self._write(200, {"ok": True, "prelude_env": repl.prelude_env})
                    return
                if self.path == "/step":
                    body = self._read_json()
                    result = repl.check_code_incremental(
                        body.get("code") or "",
                        env=body.get("env"),
                    )
                    self._write(200, serialize(result))
                    return
                self._write(404, {"error": "unknown path"})
            except Exception as exc:
                self._write(500, {"error": f"{type(exc).__name__}: {exc}"})

    httpd = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"proof repl on 127.0.0.1:{args.port}", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
