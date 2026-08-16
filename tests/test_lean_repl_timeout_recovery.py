from __future__ import annotations

import os
import signal
import sys
import threading
import time
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "agent"))

import lean_repl  # noqa: E402


_FAKE_LAKE = r"""#!/usr/bin/env python3
import json
import os
import sys
import time
from pathlib import Path


mode_path = Path(os.environ["FAKE_REPL_MODE_PATH"])
pid_log = Path(os.environ["FAKE_REPL_PID_LOG"])
previous_pids = (
    pid_log.read_text(encoding="utf-8").splitlines() if pid_log.exists() else []
)
process_index = len(previous_pids) + 1
with pid_log.open("a", encoding="utf-8") as stream:
    stream.write(f"{os.getpid()}\n")
prelude_env = process_index * 100 + 1


def read_command():
    lines = []
    while True:
        line = sys.stdin.readline()
        if line == "":
            return None
        if not line.strip():
            if lines:
                return json.loads("".join(lines))
            continue
        lines.append(line)


def respond(command, env):
    payload = {
        "messages": [],
        "sorries": [],
        "tactics": [],
        "env": env,
        "requestEnv": command.get("env"),
        "command": command.get("cmd"),
    }
    if command.get("cmd") == "MULTILINE":
        encoded = json.dumps(payload, indent=2)
    else:
        encoded = json.dumps(payload)
    sys.stdout.write(encoded + "\n")
    sys.stdout.flush()


prelude = read_command()
if prelude is None:
    raise SystemExit(0)
respond(prelude, prelude_env)

if mode_path.read_text(encoding="utf-8").strip() == "block_write":
    time.sleep(60)

next_env = prelude_env + 1
while True:
    command = read_command()
    if command is None:
        raise SystemExit(0)
    if command.get("cmd") == "SLOW":
        time.sleep(60)
    respond(command, next_env)
    next_env += 1
"""


@pytest.fixture
def fake_lake(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path]:
    lake = tmp_path / "lake"
    lake.write_text(_FAKE_LAKE, encoding="utf-8")
    lake.chmod(0o755)
    mode_path = tmp_path / "mode"
    mode_path.write_text("normal", encoding="utf-8")
    pid_log = tmp_path / "pids"
    monkeypatch.setenv("LAKE_PATH", str(lake))
    monkeypatch.setenv("FAKE_REPL_MODE_PATH", str(mode_path))
    monkeypatch.setenv("FAKE_REPL_PID_LOG", str(pid_log))
    return mode_path, pid_log


def _live_io_threads(pid: int) -> list[threading.Thread]:
    name = f"LeanREPL-io-{pid}"
    return [
        thread
        for thread in threading.enumerate()
        if thread.name == name and thread.is_alive()
    ]


def _assert_stopped(proc) -> None:
    proc.wait(timeout=2)
    assert proc.poll() is not None
    assert _live_io_threads(proc.pid) == []


def test_read_timeout_restarts_cleanly_before_next_request(
    fake_lake: tuple[Path, Path],
    tmp_path: Path,
):
    _mode_path, _pid_log = fake_lake
    repl = lean_repl.LeanREPL(project_dir=tmp_path, timeout=2)
    old_proc = repl._proc
    assert old_proc is not None
    repl.timeout = 0.25

    try:
        timed_out = repl.check_code("SLOW")

        assert not timed_out.passed
        assert "Timeout after 0.25s" in timed_out.error_text
        assert repl._proc is None
        _assert_stopped(old_proc)

        recovered = repl.check_code("QUICK")
        new_proc = repl._proc

        assert recovered.complete
        assert recovered.raw["requestEnv"] == 201
        assert new_proc is not None
        assert new_proc.pid != old_proc.pid
        assert _live_io_threads(old_proc.pid) == []
    finally:
        new_proc = repl._proc
        repl.close()

    if new_proc is not None:
        _assert_stopped(new_proc)


def test_blocked_pipe_write_obeys_timeout_and_recovers(
    fake_lake: tuple[Path, Path],
    tmp_path: Path,
):
    mode_path, _pid_log = fake_lake
    mode_path.write_text("block_write", encoding="utf-8")
    repl = lean_repl.LeanREPL(project_dir=tmp_path, timeout=2)
    old_proc = repl._proc
    assert old_proc is not None
    repl.timeout = 0.25

    try:
        start = time.monotonic()
        timed_out = repl.check_code("X" * (8 * 1024 * 1024))
        elapsed = time.monotonic() - start

        assert not timed_out.passed
        assert "Timeout after 0.25s" in timed_out.error_text
        assert elapsed < 2
        assert repl._proc is None
        _assert_stopped(old_proc)

        mode_path.write_text("normal", encoding="utf-8")
        recovered = repl.check_code("QUICK")
        new_proc = repl._proc

        assert recovered.complete
        assert recovered.raw["requestEnv"] == 201
        assert new_proc is not None
        assert new_proc.pid != old_proc.pid
    finally:
        new_proc = repl._proc
        repl.close()

    if new_proc is not None:
        _assert_stopped(new_proc)


def test_normal_exchange_reuses_process_and_cached_prelude(
    fake_lake: tuple[Path, Path],
    tmp_path: Path,
):
    _mode_path, pid_log = fake_lake
    repl = lean_repl.LeanREPL(project_dir=tmp_path, timeout=2)
    proc = repl._proc
    assert proc is not None

    try:
        first = repl.check_code("QUICK")
        second = repl.check_code("MULTILINE")

        assert first.complete
        assert second.complete
        assert first.raw["requestEnv"] == 101
        assert second.raw["requestEnv"] == 101
        assert repl._proc is proc
        assert pid_log.read_text(encoding="utf-8").splitlines() == [str(proc.pid)]
        assert _live_io_threads(proc.pid) == []
    finally:
        repl.close()

    _assert_stopped(proc)



def test_process_death_rebinds_prelude_and_rejects_stale_incremental_env(
    fake_lake: tuple[Path, Path],
    tmp_path: Path,
):
    _mode_path, _pid_log = fake_lake
    repl = lean_repl.LeanREPL(project_dir=tmp_path, timeout=2)
    old_proc = repl._proc
    assert old_proc is not None

    try:
        first_step = repl.check_code_incremental("STEP")
        stale_env = first_step.env
        assert first_step.complete
        assert stale_env is not None

        os.killpg(old_proc.pid, signal.SIGKILL)
        _assert_stopped(old_proc)

        recovered = repl.check_code("QUICK")
        new_proc = repl._proc
        assert recovered.complete
        assert recovered.raw["requestEnv"] == 201
        assert new_proc is not None
        assert new_proc.pid != old_proc.pid

        stale = repl.check_code_incremental("NEXT", env=stale_env)
        assert not stale.passed
        assert "Stale REPL environment handle" in stale.error_text
        assert repl._proc is new_proc

        fresh = repl.check_code_incremental("FRESH")
        assert fresh.complete
        assert fresh.raw["requestEnv"] == 201
    finally:
        new_proc = repl._proc
        repl.close()

    if new_proc is not None:
        _assert_stopped(new_proc)


def test_interrupted_wait_retires_process_and_worker(
    fake_lake: tuple[Path, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    _mode_path, _pid_log = fake_lake
    repl = lean_repl.LeanREPL(project_dir=tmp_path, timeout=2)
    old_proc = repl._proc
    assert old_proc is not None

    def interrupt_wait(*_args, **_kwargs):
        raise KeyboardInterrupt

    try:
        with monkeypatch.context() as patch:
            patch.setattr(lean_repl.queue.Queue, "get", interrupt_wait)
            with pytest.raises(KeyboardInterrupt):
                repl.check_code("SLOW")

        assert repl._proc is None
        _assert_stopped(old_proc)

        recovered = repl.check_code("QUICK")
        new_proc = repl._proc
        assert recovered.complete
        assert recovered.raw["requestEnv"] == 201
        assert new_proc is not None
        assert new_proc.pid != old_proc.pid
    finally:
        new_proc = repl._proc
        repl.close()

    if new_proc is not None:
        _assert_stopped(new_proc)
