#!/usr/bin/python3
"""Jing SOCKS Codex exec for Direct Sparkle: no CktArchon, read-only sandbox."""
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path("/tmp/direct_sparkle_empty")
BINARY = "/home/sgli/work/codex-runtime/home/packages/standalone/releases/0.146.0-x86_64-unknown-linux-musl/bin/codex"
AUTH_SRC = Path("/home/sgli/work/codex_jing_chatgpt_probe/codex_home")
ISOLATION_PARENT = Path("/home/sgli/work/nl2chip_direct_sparkle_private_20260920/agent_state")
PRIVATE = Path("/home/sgli/work/nl2chip_direct_sparkle_private_20260920")
SOCKS = "socks5h://127.0.0.1:11080"

task = os.environ.get("NL2CHIP_TASK_ID", "transport_probe")
if not re.fullmatch(r"[A-Za-z0-9_.-]+", task):
    raise SystemExit("Invalid task id")
state = Path(os.environ["NL2CHIP_ISOLATION_ROOT"]) / task
assert state.parent == ISOLATION_PARENT
for name in ("work", "codex"):
    (state / name).mkdir(parents=True, exist_ok=True)
ROOT.mkdir(exist_ok=True)
(ROOT / "README").write_text("empty workspace for direct sparkle\n")

codex_home = state / "codex"
auth_src = AUTH_SRC / "auth.json"
auth_dst = codex_home / "auth.json"
if auth_src.exists() and not auth_dst.exists():
    shutil.copy2(auth_src, auth_dst)
    os.chmod(auth_dst, 0o600)
cache = AUTH_SRC / "cloud-config-bundle-cache.json"
if cache.exists():
    shutil.copy2(cache, codex_home / "cloud-config-bundle-cache.json")
(codex_home / "config.toml").write_text(
    'model = "gpt-5.6-sol"\n'
    'model_reasoning_effort = "ultra"\n'
    'model_provider = "openai"\n'
)

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
    argv[i] = str(state / "work" / "last_message.txt")

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
cmd += [
    "--bind", str(ROOT), str(ROOT),
    "--bind", str(state / "work"), str(state / "work"),
    "--bind", str(codex_home), str(codex_home),
    "--ro-bind", BINARY, BINARY,
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
    BINARY, *argv,
]
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
