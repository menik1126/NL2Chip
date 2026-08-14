#!/usr/bin/env python3
"""Minimal live Anthropic-compatible API smoke test for NL2Chip.

The test intentionally prints only model, endpoint, latency, and token usage.
It never prints credentials or request headers.
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

import anthropic

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from cktarchon.env import load_env_file, model_alias


SECRET_PATTERN = re.compile(r"(?:sk|api)[-_][A-Za-z0-9_-]+", re.IGNORECASE)


def redact(text: object) -> str:
    return SECRET_PATTERN.sub("[REDACTED]", str(text)).replace("\\n", " ")[:500]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--key-env", type=Path, default=PROJECT_ROOT / "key.env")
    parser.add_argument("--model", default="claude-sonnet-4.5")
    parser.add_argument("--timeout", type=float, default=60.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    env = load_env_file(args.key_env)
    api_key = env.get("ANTHROPIC_API_KEY") or env.get("ANTHROPIC_AUTH_TOKEN")
    base_url = env.get("ANTHROPIC_BASE_URL")
    if not api_key:
        print("API_SMOKE_CONFIG_ERROR missing ANTHROPIC_API_KEY/ANTHROPIC_AUTH_TOKEN", file=sys.stderr)
        return 2

    client_kwargs: dict[str, object] = {"api_key": api_key, "timeout": args.timeout}
    if base_url:
        client_kwargs["base_url"] = base_url
    model = model_alias(args.model)
    started = time.monotonic()
    try:
        response = anthropic.Anthropic(**client_kwargs).messages.create(
            model=model,
            max_tokens=1,
            messages=[{"role": "user", "content": "Reply with OK."}],
        )
    except anthropic.APIStatusError as exc:
        print(
            f"API_SMOKE_STATUS_ERROR status={exc.status_code} error={redact(exc.message)}",
            file=sys.stderr,
        )
        return 3
    except Exception as exc:
        print(f"API_SMOKE_REQUEST_ERROR type={type(exc).__name__} error={redact(exc)}", file=sys.stderr)
        return 4

    usage = response.usage
    elapsed = time.monotonic() - started
    endpoint = base_url or "https://api.anthropic.com"
    print(
        "API_SMOKE_PASS"
        f" model={model} endpoint={endpoint} elapsed_s={elapsed:.2f}"
        f" input_tokens={getattr(usage, 'input_tokens', 0)}"
        f" output_tokens={getattr(usage, 'output_tokens', 0)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
