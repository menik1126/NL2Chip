from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from cktarchon import env as runtime_env


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "agent"))

import lean_repl  # noqa: E402


def _make_executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def test_resolve_lake_prefers_executable_environment_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    configured = _make_executable(tmp_path / "configured" / "lake")
    on_path = _make_executable(tmp_path / "path" / "lake")
    monkeypatch.setenv("LAKE_PATH", str(configured))
    monkeypatch.setenv("PATH", str(on_path.parent))

    assert lean_repl.resolve_lake_path() == os.path.abspath(configured)


def test_resolve_lake_ignores_stale_environment_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    on_path = _make_executable(tmp_path / "bin" / "lake")
    monkeypatch.setenv("LAKE_PATH", "/missing/legacy/home/.elan/bin/lake")
    monkeypatch.setenv("PATH", str(on_path.parent))

    assert lean_repl.resolve_lake_path() == os.path.abspath(on_path)


def test_resolve_lake_uses_compatible_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    fallback = _make_executable(tmp_path / "home" / ".elan" / "bin" / "lake")
    monkeypatch.delenv("LAKE_PATH", raising=False)
    monkeypatch.setenv("PATH", "")
    monkeypatch.setattr(lean_repl, "_LAKE_FALLBACK_PATHS", (fallback,))

    assert lean_repl.resolve_lake_path() == os.path.abspath(fallback)


def test_resolve_lake_fails_loud_when_no_executable_exists(
    monkeypatch: pytest.MonkeyPatch,
):
    stale = "/missing/legacy/home/.elan/bin/lake"
    monkeypatch.setenv("LAKE_PATH", stale)
    monkeypatch.setenv("PATH", "")
    monkeypatch.setattr(lean_repl, "_LAKE_FALLBACK_PATHS", ())

    with pytest.raises(RuntimeError, match="Unable to locate an executable Lake") as exc:
        lean_repl.resolve_lake_path()

    assert stale in str(exc.value)
    assert "add `lake` to PATH" in str(exc.value)


def test_runtime_env_replaces_stale_lake_path_with_current_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    on_path = _make_executable(tmp_path / "bin" / "lake")
    monkeypatch.setenv("LAKE_PATH", "/home/sgli/.elan/bin/lake")
    monkeypatch.setenv("PATH", str(on_path.parent))

    runtime_env.ensure_runtime_env()

    assert os.environ["LAKE_PATH"] == os.path.abspath(on_path)


def test_runtime_env_drops_stale_path_when_lake_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("LAKE_PATH", "/home/sgli/.elan/bin/lake")
    monkeypatch.setattr(runtime_env.shutil, "which", lambda _candidate: None)

    runtime_env.ensure_runtime_env()

    assert "LAKE_PATH" not in os.environ
