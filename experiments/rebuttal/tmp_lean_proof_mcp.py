#!/usr/bin/env python3
"""Stdio MCP server exposing Lab-style lean_proof_step / lean_check."""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from lean_proof_step import format_result  # noqa: E402


def repl_url() -> str:
    url = os.environ.get("PROOF_REPL_URL", "").rstrip("/")
    if not url:
        path = os.environ.get("PROOF_REPL_URL_FILE", "")
        if path and os.path.exists(path):
            url = open(path, encoding="utf-8").read().strip().rstrip("/")
    if not url:
        raise RuntimeError("PROOF_REPL_URL is not set")
    return url


def post(path: str, payload: dict, timeout: int = 180) -> dict:
    body = json.dumps(payload).encode()
    req = urllib.request.Request(repl_url() + path, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode(errors="replace")
        try:
            return json.loads(raw)
        except Exception:
            return {"error": raw or str(exc)}


def call_tool(name: str, arguments: dict) -> str:
    if name == "lean_proof_reset":
        return json.dumps(post("/reset", {}))
    if name not in {"lean_proof_step", "lean_check"}:
        return f"Error: unknown tool {name}"
    code = arguments.get("code") or ""
    env = arguments.get("env")
    if name == "lean_check":
        env = None
    payload = {"code": code, "env": env}
    return format_result(post("/step", payload))


TOOLS = [
    {
        "name": "lean_proof_step",
        "description": (
            "Interactive tactic proof mode using the persistent Lean REPL. "
            "Send Lean incrementally; each call can reuse a previous env. "
            "Workflow: (1) send defs with lean_proof_step → env=N, "
            "(2) send theorem with `by sorry` using env=N → see === Proof Goals ===, "
            "(3) replace sorry with tactics using the definition env, "
            "(4) repeat until COMPLETE with no sorry. "
            "Do NOT include import/open lines."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Lean 4 without import/open."},
                "env": {
                    "type": "integer",
                    "description": "Environment ID from a previous lean_proof_step call.",
                },
            },
            "required": ["code"],
        },
    },
    {
        "name": "lean_check",
        "description": (
            "Check a Lean fragment against the Sparkle prelude. "
            "Does not chain env and does not replace lean_proof_step for goals."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "code": {"type": "string"},
            },
            "required": ["code"],
        },
    },
    {
        "name": "lean_proof_reset",
        "description": "Reset the persistent REPL to the Sparkle prelude.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def read_message() -> dict | None:
    header = b""
    while True:
        ch = sys.stdin.buffer.read(1)
        if not ch:
            return None
        header += ch
        if header.endswith(b"\r\n\r\n"):
            break
        if len(header) > 65536:
            raise RuntimeError("MCP header too large")
    length = 0
    for line in header.decode().split("\r\n"):
        if line.lower().startswith("content-length:"):
            length = int(line.split(":", 1)[1].strip())
    body = sys.stdin.buffer.read(length)
    if len(body) < length:
        return None
    return json.loads(body.decode())


def write_message(payload: dict) -> None:
    raw = json.dumps(payload).encode()
    sys.stdout.buffer.write(f"Content-Length: {len(raw)}\r\n\r\n".encode() + raw)
    sys.stdout.buffer.flush()


def result(msg_id, content):
    write_message({"jsonrpc": "2.0", "id": msg_id, "result": content})


def main() -> int:
    while True:
        msg = read_message()
        if msg is None:
            return 0
        method = msg.get("method")
        msg_id = msg.get("id")
        if method == "initialize":
            result(msg_id, {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "lean-repl", "version": "1.0.0"},
            })
        elif method == "notifications/initialized":
            continue
        elif method == "tools/list":
            result(msg_id, {"tools": TOOLS})
        elif method == "tools/call":
            params = msg.get("params") or {}
            name = params.get("name")
            arguments = params.get("arguments") or {}
            try:
                text = call_tool(name, arguments)
                result(msg_id, {"content": [{"type": "text", "text": text}]})
            except Exception as exc:
                result(msg_id, {
                    "content": [{"type": "text", "text": f"Error: {type(exc).__name__}: {exc}"}],
                    "isError": True,
                })
        elif method == "ping":
            result(msg_id, {})
        elif msg_id is not None:
            write_message({
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32601, "message": f"Unknown method {method}"},
            })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
