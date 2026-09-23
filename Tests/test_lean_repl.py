from __future__ import annotations

import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))
import lean_repl as repl_module


FAKE_REPL = r'''
import json
import os
import sys
import time

prelude_env = 0 if os.environ.get("FAKE_REPL_REUSED_ENV") else os.getpid()
next_env = prelude_env
valid_envs = set()
while True:
    lines = []
    for line in sys.stdin:
        if line.strip():
            lines.append(line)
        elif lines:
            break
    if not lines:
        break
    request = json.loads("".join(lines))
    code = request["cmd"]
    if os.environ.get("FAKE_REPL_PRELUDE_EXIT") and code == "prelude":
        sys.exit(1)
    if code == "late":
        sys.stdout.write('{"messages": [\n')
        sys.stdout.flush()
        time.sleep(0.7)
        for line in ['{"severity": "info", "data": "OLD"}\n', '], "env": 999}\n\n']:
            sys.stdout.write(line)
            sys.stdout.flush()
            time.sleep(0.05)
        continue
    if code == "silent":
        time.sleep(30)
    if code == "eof":
        sys.stdout.write('{"messages": [\n')
        sys.stdout.flush()
        sys.exit(0)
    if code == "malformed":
        sys.stdout.write('{"messages": INVALID}\n\n')
        sys.stdout.flush()
        continue
    if code == "non_object":
        sys.stdout.write('[]\n\n')
        sys.stdout.flush()
        continue
    if code == "stderr":
        sys.stderr.write("diagnostic" * 100000)
        sys.stderr.flush()
    if os.environ.get("FAKE_REPL_PRELUDE_FAIL") and code == "prelude":
        response = {"messages": [{"severity": "error", "data": "bad prelude"}]}
    elif code != "prelude" and request.get("env") not in valid_envs:
        response = {"messages": [{"severity": "error", "data": "WRONG ENV"}]}
    else:
        next_env += 1
        valid_envs.add(next_env)
        data = ("module big;\n" + "// data\n" * 40000 + "endmodule") if code == "large" else code
        response = {
            "env": next_env,
            "messages": [{"severity": "error" if code == "lean_error" else "info", "data": data}],
            "request": request,
        }
    wire = json.dumps(response, indent=2) + "\n\n"
    if code == "fragmented":
        for ch in wire:
            sys.stdout.write(ch)
            sys.stdout.flush()
            time.sleep(0.0001)
    else:
        sys.stdout.write(wire)
        sys.stdout.flush()
    if code == "pause_reader":
        time.sleep(30)
'''


@pytest.fixture
def fake_repl(tmp_path, monkeypatch):
    script = tmp_path / "fake_repl.py"
    script.write_text(FAKE_REPL)
    real_popen = subprocess.Popen
    processes = []
    clients = []

    def popen(_command, **kwargs):
        proc = real_popen([sys.executable, "-u", str(script)], **kwargs)
        processes.append(proc)
        return proc

    monkeypatch.setattr(repl_module.subprocess, "Popen", popen)

    def create():
        client = repl_module.LeanREPL(tmp_path, timeout=3, prelude="prelude")
        clients.append(client)
        return client

    create.processes = processes
    yield create
    for client in clients:
        client.close()
    for proc in processes:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=3)


@pytest.mark.parametrize("code", ["silent", "late"])
def test_timeout_retires_process_and_next_request_is_isolated(fake_repl, code):
    client = fake_repl()
    old_proc = client._proc
    client.timeout = 0.1
    result = client.check_code(code)
    assert not result.passed
    assert old_proc.poll() is not None, "timed-out REPL must not remain reusable"
    assert client.prelude_env is None
    client.timeout = 3
    result = client.check_code("NEW")
    assert result.passed, result.error_text
    assert result.infos[0]["data"] == "NEW"
    assert client._proc.pid != old_proc.pid


def test_late_response_cannot_satisfy_following_request(fake_repl):
    client = fake_repl()
    client.timeout = 0.1
    assert not client.check_code("late").passed
    client.timeout = 2
    result = client.check_code("NEW")
    assert result.passed, result.error_text
    assert result.infos[0]["data"] == "NEW"


@pytest.mark.parametrize("code", ["eof", "malformed", "non_object"])
def test_bad_response_retires_process(fake_repl, code):
    client = fake_repl()
    old_proc = client._proc
    client.timeout = 0.2
    result = client.check_code(code)
    assert not result.passed
    assert old_proc.poll() is not None
    client.timeout = 3
    assert client.check_code("after_failure").passed


@pytest.mark.parametrize("code", ["fragmented", "stderr", "large"])
def test_complete_response_without_truncation_or_pipe_deadlock(fake_repl, code):
    client = fake_repl()
    proc = client._proc
    result = client.check_code(code)
    assert result.passed, result.error_text
    if code == "large":
        assert result.verilog == "module big;\n" + "// data\n" * 40000 + "endmodule"
    else:
        assert result.infos[0]["data"] == code
    assert client.check_code("following").infos[0]["data"] == "following"
    assert client._proc is proc


def test_lean_errors_keep_healthy_session(fake_repl):
    client = fake_repl()
    proc = client._proc
    assert not client.check_code("lean_error").passed
    assert client.check_code("fixed").passed
    assert client._proc is proc


@pytest.mark.parametrize("reuse_wire_ids", [False, True])
def test_incremental_environment_is_preserved_but_not_reused_after_restart(fake_repl, monkeypatch, reuse_wire_ids):
    if reuse_wire_ids:
        monkeypatch.setenv("FAKE_REPL_REUSED_ENV", "1")
    client = fake_repl()
    first = client.check_code("first")
    second = client.check_code_incremental("second", env=first.env)
    assert second.passed
    assert second.raw["request"]["env"] == first.raw["env"]
    assert second.raw["request"]["allTactics"] is True
    client.restart()
    assert not client.check_code_incremental("stale", env=first.env).passed
    assert client.check_code_incremental("fresh").passed


def test_blocked_request_write_obeys_timeout(fake_repl):
    client = fake_repl()
    assert client.check_code("pause_reader").passed
    proc = client._proc
    client.timeout = 0.1
    assert not client.check_code("X" * 1000000).passed
    assert proc.poll() is not None
    client.timeout = 3
    assert client.check_code("recovered").passed


def test_cancelled_request_discards_session(fake_repl, monkeypatch):
    client = fake_repl()
    proc = client._proc

    def cancel(*_args, **_kwargs):
        raise KeyboardInterrupt

    with monkeypatch.context() as patch:
        patch.setattr(repl_module.selectors.DefaultSelector, "select", cancel)
        with pytest.raises(KeyboardInterrupt):
            client.check_code("cancelled")
    assert proc.poll() is not None
    assert client.check_code("recovered").passed


def test_dead_process_reloads_prelude_before_selecting_env(fake_repl):
    client = fake_repl()
    proc = client._proc
    proc.kill()
    proc.wait(timeout=3)
    result = client.check_code("after_crash")
    assert result.passed, result.error_text
    assert client._proc is not proc


@pytest.mark.parametrize("failure", ["FAKE_REPL_PRELUDE_FAIL", "FAKE_REPL_PRELUDE_EXIT"])
def test_failed_prelude_cleans_up_without_recursive_restart(fake_repl, monkeypatch, failure):
    monkeypatch.setenv(failure, "1")
    with pytest.raises(RuntimeError, match="prelude"):
        fake_repl()
    assert len(fake_repl.processes) == 1
    assert fake_repl.processes[0].poll() is not None


def test_broken_stdin_retires_session(fake_repl):
    client = fake_repl()
    old_proc = client._proc
    old_proc.stdin.close()
    assert not client.check_code("broken_pipe").passed
    assert old_proc.poll() is not None
    assert client.check_code("recovered").passed


def test_large_unicode_request_and_serialized_concurrent_calls(fake_repl):
    client = fake_repl()
    codes = ["\u6d4b\u8bd5" * 50000, "second", "third"]
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(client.check_code, codes))
    assert all(result.passed for result in results)
    assert [result.infos[0]["data"] for result in results] == codes


def test_close_reaps_process_and_closes_pipes(fake_repl):
    client = fake_repl()
    proc = client._proc
    client.close()
    client.close()
    assert proc.poll() is not None
    assert all(stream.closed for stream in (proc.stdin, proc.stdout, proc.stderr))
    assert client.prelude_env is None
