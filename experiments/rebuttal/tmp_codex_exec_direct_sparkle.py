#!/usr/bin/python3
"""Direct Sparkle Codex via jing SOCKS. Same bwrap/auth as working CktArchon wrapper."""
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path("/home/sgli/work/NL2Chip_openlux_repair_state_20260914")
BINARY = "/home/sgli/work/codex-runtime/home/packages/standalone/releases/0.146.0-x86_64-unknown-linux-musl/bin/codex"
AUTH_SRC = Path("/home/sgli/work/codex_jing_chatgpt_probe/codex_home")
ISOLATION_PARENT = Path("/home/sgli/work/nl2chip_direct_sparkle_private_20260920/agent_state")
PRIVATE = Path("/home/sgli/work/nl2chip_direct_sparkle_private_20260920")
SOCKS = "socks5h://127.0.0.1:11080"

task = os.environ.get("NL2CHIP_TASK_ID", "transport_probe")
if not re.fullmatch(r"[A-Za-z0-9_-]+", task):
    raise SystemExit("Invalid task id")
state = Path(os.environ["NL2CHIP_ISOLATION_ROOT"]) / task
assert state.parent == ISOLATION_PARENT
for name in ("Generated", "work", "codex"):
    (state / name).mkdir(parents=True, exist_ok=True)
(ROOT / "Generated").mkdir(exist_ok=True)

codex_home = state / "codex"
auth_src = AUTH_SRC / "auth.json"
auth_dst = codex_home / "auth.json"
if auth_src.exists() and not auth_dst.exists():
    shutil.copy2(auth_src, auth_dst)
    os.chmod(auth_dst, 0o600)
for name in ("cloud-config-bundle-cache.json", "config.toml"):
    src = AUTH_SRC / name
    if src.exists():
        shutil.copy2(src, codex_home / name)

argv = sys.argv[1:]
child_env = os.environ.copy()
for name in (
    "OPENLUX_API_KEY", "OPENLUX_BASE_URL", "CODEX_GATEWAY_API_KEY",
    "OPENAI_API_KEY", "OPENAI_BASE_URL", "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
):
    child_env.pop(name, None)
child_env["ALL_PROXY"] = SOCKS
child_env["HTTPS_PROXY"] = SOCKS
child_env["HTTP_PROXY"] = SOCKS
child_env["https_proxy"] = SOCKS
child_env["http_proxy"] = SOCKS
child_env["NO_PROXY"] = "localhost,127.0.0.1"
child_env["no_proxy"] = "localhost,127.0.0.1"
child_env["CODEX_HOME"] = str(codex_home)

last_message = None
if "-o" in argv:
    i = argv.index("-o") + 1
    last_message = Path(argv[i])
    argv[i] = str(ROOT / "cktarchon_work" / "last_message.txt")

cmd = [
    "/usr/bin/bwrap", "--die-with-parent", "--new-session",
    "--unshare-all", "--share-net", "--ro-bind", "/usr", "/usr",
    "--symlink", "usr/bin", "/bin", "--symlink", "usr/sbin", "/sbin",
    "--symlink", "usr/lib", "/lib", "--symlink", "usr/lib64", "/lib64",
    "--ro-bind", "/etc", "/etc", "--proc", "/proc", "--dev", "/dev",
    "--tmpfs", "/tmp", "--dir", "/home/sgli",
]
resolver = str(Path("/etc/resolv.conf").resolve())
if not resolver.startswith("/etc/"):
    cmd += ["--ro-bind", resolver, resolver]
for name in (
    "Sparkle", "Sparkle.lean", "Benchmark", ".lake", "lakefile.lean",
    "lake-manifest.json", "lean-toolchain", "agent", "cktarchon",
    "structural_checker.py",
):
    path = str(ROOT / name)
    if Path(path).exists():
        cmd += ["--ro-bind", path, path]
for path in (
    "/home/sgli/.elan/toolchains/leanprover--lean4---v4.28.0-rc1",
    "/home/sgli/.local/share/uv/python/cpython-3.13.3-linux-x86_64-gnu",
    "/home/sgli/work/NL2Chip/.venv",
    "/home/sgli/work/toolcache/iverilog_deb",
    "/home/sgli/.local/bin/iverilog",
    "/home/sgli/.local/bin/vvp",
    BINARY,
    str(PRIVATE),
):
    if Path(path).exists():
        cmd += ["--ro-bind", path, path]
cmd += [
    "--symlink", "/home/sgli/work/NL2Chip/.venv", str(ROOT / ".venv"),
    "--bind", str(state / "Generated"), str(ROOT / "Generated"),
    "--bind", str(state / "work"), str(ROOT / "cktarchon_work"),
    "--bind", str(codex_home), str(codex_home),
    "--setenv", "HOME", "/home/sgli",
    "--setenv", "CODEX_HOME", str(codex_home),
    "--setenv", "ALL_PROXY", SOCKS,
    "--setenv", "HTTPS_PROXY", SOCKS,
    "--setenv", "HTTP_PROXY", SOCKS,
    "--setenv", "https_proxy", SOCKS,
    "--setenv", "http_proxy", SOCKS,
    "--setenv", "NO_PROXY", "localhost,127.0.0.1",
    "--setenv", "no_proxy", "localhost,127.0.0.1",
    "--chdir", str(ROOT),
]
cmd += argv[1:] if argv[:1] == ["--isolation-test-command"] else [BINARY, *argv]
result = subprocess.run(cmd, env=child_env)
auth_dst = AUTH_SRC / "auth.json"
auth_src = codex_home / "auth.json"
if auth_src.exists() and auth_src.stat().st_size > 100:
    shutil.copy2(auth_src, auth_dst)
    os.chmod(auth_dst, 0o600)
capture = state / "work" / "last_message.txt"
if last_message is not None and capture.exists():
    last_message.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(capture, last_message)
raise SystemExit(result.returncode)
