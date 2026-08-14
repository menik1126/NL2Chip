from __future__ import annotations

import os
from pathlib import Path


def load_env_file(path: Path) -> dict[str, str]:
    """Load KEY=VALUE lines into ``os.environ`` without printing secrets."""
    loaded: dict[str, str] = {}
    if not path.exists():
        return loaded
    for raw in path.read_text(errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if not key:
            continue
        os.environ.setdefault(key, value)
        loaded[key] = value
    return loaded


def model_alias(name: str) -> str:
    """Normalize user-facing model aliases used in NL2Chip scripts."""
    aliases = {
        "claude-sonnet-4.5": "claude-sonnet-4-5-20250929",
        "claude-sonnet-4-5": "claude-sonnet-4-5-20250929",
        "sonnet-4.5": "claude-sonnet-4-5-20250929",
        "sonnet": "claude-sonnet-4-5-20250929",
        "opus-4-7": "claude-opus-4-7",
    }
    return aliases.get(name, name)


def ensure_runtime_env() -> None:
    """Expose Lean/Lake binaries used by the H20 NL2Chip experiments."""
    project_root = Path(__file__).resolve().parents[1]
    prefixes = [
        str(project_root / ".venv" / "bin"),
        "/home/sgli/.elan/bin",
        "/home/sgli/.elan/toolchains/leanprover--lean4---v4.28.0-rc1/bin",
        "/home/sgli/.local/bin",
        "/home/sgli/.local/share/mamba/bin",
    ]
    current = os.environ.get("PATH", "")
    parts = current.split(os.pathsep) if current else []
    for prefix in reversed(prefixes):
        if Path(prefix).exists() and prefix not in parts:
            parts.insert(0, prefix)
    os.environ["PATH"] = os.pathsep.join(parts)
    os.environ.setdefault("LAKE_PATH", "/home/sgli/.elan/bin/lake")
