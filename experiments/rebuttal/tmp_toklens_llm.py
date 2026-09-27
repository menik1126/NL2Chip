#!/usr/bin/env python3
"""Toklens OpenAI-compatible chat client for contract gen/judge.

Credentials live in a chmod-600 env file on H20, never in git.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_ENV = Path(
    os.environ.get(
        "TOKLENS_ENV",
        "/home/sgli/work/nl2chip_contract_gen_private_20260923/toklens.env",
    )
)
DEFAULT_BASE = "https://api-fdm.toklens.ai:8443/v1"
DEFAULT_MODEL = os.environ.get("TOKLENS_MODEL", "gpt-5.6-sol")


def _parse_env_file(path: Path) -> dict[str, str]:
    out = {}
    if not path.exists():
        return out
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip().strip("'\"")
    return out


def credentials() -> tuple[str, str]:
    file_env = _parse_env_file(DEFAULT_ENV)
    key = (
        os.environ.get("TOKLENS_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or file_env.get("TOKLENS_API_KEY")
        or file_env.get("OPENAI_API_KEY")
        or ""
    )
    base = (
        os.environ.get("TOKLENS_BASE_URL")
        or os.environ.get("OPENAI_BASE_URL")
        or file_env.get("TOKLENS_BASE_URL")
        or file_env.get("OPENAI_BASE_URL")
        or DEFAULT_BASE
    ).rstrip("/")
    if not key:
        raise RuntimeError(f"missing TOKLENS_API_KEY (looked at {DEFAULT_ENV})")
    return base, key


def chat(
    messages: list[dict[str, str]],
    *,
    model: str | None = None,
    max_tokens: int = 8192,
    timeout: int = 180,
) -> dict:
    base, key = credentials()
    body = {
        "model": model or DEFAULT_MODEL,
        "messages": messages,
        "max_tokens": max_tokens,
    }
    req = urllib.request.Request(
        base + "/chat/completions",
        data=json.dumps(body).encode(),
        headers={
            "Authorization": "Bearer " + key,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        err = exc.read().decode("utf-8", "replace")[:1500]
        raise RuntimeError(f"toklens HTTP {exc.code}: {err}") from exc
    text = (
        (((payload.get("choices") or [{}])[0].get("message") or {}).get("content"))
        or ""
    )
    return {"text": text, "raw": payload, "model": body["model"]}
