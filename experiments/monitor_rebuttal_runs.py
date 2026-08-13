#!/usr/bin/env python3
"""Monitor rebuttal experiments for API failures without logging credentials."""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import re
import signal
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


EVENT_PATTERNS = [
    ("quota", re.compile(r"\binsufficient[_\s-]*(?:quota|balance|funds|credits?)\b", re.I)),
    ("quota", re.compile(r"\b(?:quota|credits?|balance)\b.{0,100}\b(?:exceeded|exhausted|depleted|insufficient|empty)\b", re.I)),
    ("quota", re.compile(r"\b(?:billing limit|billing error|payment required)\b", re.I)),
    ("auth", re.compile(r"\b(?:invalid api key|authentication (?:error|failed))\b", re.I)),
    ("auth", re.compile(r"\bHTTP(?:/[0-9.]+)?\s*(?:401|402)\b", re.I)),
    ("rate_limit", re.compile(r"\b(?:rate.?limit(?:ed)?|too many requests)\b", re.I)),
    ("rate_limit", re.compile(r"\bHTTP(?:/[0-9.]+)?\s*429\b", re.I)),
    ("invalid_model", re.compile(r"\binvalid model(?: id)?\b", re.I)),
    ("overloaded", re.compile(r"\b(?:api )?overloaded\b", re.I)),
    ("api_http", re.compile(
        r"\b(?:anthropic\s+)?HTTP(?:/[0-9.]+)?\s*"
        r"(?:400|403|404|405|408|409|422|500|501|502|503|504|505|"
        r"520|521|522|523|524|525|526|527|529)\b",
        re.I,
    )),
    ("api_http", re.compile(
        r"\b(?:error|status)\s*code\s*[:=]?\s*"
        r"(?:400|403|404|405|408|409|422|429|500|501|502|503|504|505|"
        r"520|521|522|523|524|525|526|527|529)\b",
        re.I,
    )),
    ("api_error", re.compile(
        r"\b(?:APIStatusError|APIError|BadRequestError|PermissionDeniedError|"
        r"InternalServerError|ServiceUnavailableError|APITimeoutError)\b",
        re.I,
    )),
    ("api_connection", re.compile(r"\b(?:anthropic|api).{0,40}(?:connection error|connect timeout|read timeout)\b", re.I)),
    ("api_connection", re.compile(
        r"\b(?:APIConnectionError|ConnectError|RemoteProtocolError|ReadTimeout)\b",
        re.I,
    )),
]

FATAL_CATEGORIES = {"quota", "auth"}
SECRET_RE = re.compile(r"sk-[A-Za-z0-9_-]+")
STOP = False


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def redact(text: str) -> str:
    return SECRET_RE.sub("<redacted>", text).replace("\x00", "")


def is_api_source_code_match(line: str, start: int, end: int) -> bool:
    """Recognize SDK exception names shown inside retry-helper source code."""
    context = line[max(0, start - 1200):min(len(line), end + 1200)]
    context = context.replace("\\n", "\n").replace("\\t", "\t")
    return (
        "def _is_retriable_api_error" in context
        and "isinstance(exc" in context
        and (
            "anthropic.APIConnectionError" in context
            or "anthropic.APIStatusError" in context
        )
    )


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def atomic_write(path: Path, text: str, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temp.write_text(text)
    if mode is not None:
        os.chmod(temp, mode)
    os.replace(temp, path)


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def credential(values: dict[str, str]) -> str:
    return values.get("ANTHROPIC_API_KEY") or values.get("ANTHROPIC_AUTH_TOKEN") or ""


def credential_fingerprint(values: dict[str, str]) -> str:
    value = credential(values)
    return hashlib.sha256(value.encode()).hexdigest()[:12] if value else "missing"


def update_env_text(text: str, values: dict[str, str]) -> str:
    wanted = {
        "ANTHROPIC_API_KEY": credential(values),
        "ANTHROPIC_AUTH_TOKEN": credential(values),
        "ANTHROPIC_BASE_URL": values.get("ANTHROPIC_BASE_URL", "https://api.openlux.ai/"),
    }
    seen: set[str] = set()
    output: list[str] = []
    for raw in text.splitlines():
        stripped = raw.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in wanted:
                output.append(f"{key}={wanted[key]}")
                seen.add(key)
                continue
        output.append(raw)
    for key, value in wanted.items():
        if key not in seen:
            output.append(f"{key}={value}")
    return "\n".join(output).rstrip() + "\n"


def update_mage_cfg_text(text: str, values: dict[str, str]) -> str:
    wanted = {
        "ANTHROPIC_API_KEY": credential(values),
        "ANTHROPIC_BASE_URL": values.get("ANTHROPIC_BASE_URL", "https://api.openlux.ai/"),
    }
    seen: set[str] = set()
    output: list[str] = []
    for raw in text.splitlines():
        match = re.match(r"^(\s*)([A-Za-z_][A-Za-z0-9_]*)(\s*=).*$", raw)
        if match and match.group(2) in wanted:
            key = match.group(2)
            output.append(f"{match.group(1)}{key}{match.group(3)} {wanted[key]!r}")
            seen.add(key)
        else:
            output.append(raw)
    for key, value in wanted.items():
        if key not in seen:
            output.append(f"{key}= {value!r}")
    return "\n".join(output).rstrip() + "\n"


def update_compose_env_text(text: str, values: dict[str, str]) -> str:
    wanted = {
        "ANTHROPIC_API_KEY": credential(values),
        "ANTHROPIC_AUTH_TOKEN": credential(values),
        "ANTHROPIC_BASE_URL": values.get("ANTHROPIC_BASE_URL", "https://api.openlux.ai/"),
    }
    output: list[str] = []
    for raw in text.splitlines():
        match = re.match(
            r"^(\s*)(ANTHROPIC_API_KEY|ANTHROPIC_AUTH_TOKEN|ANTHROPIC_BASE_URL)(\s*:).*$",
            raw,
        )
        if match:
            key = match.group(2)
            output.append(f"{match.group(1)}{key}{match.group(3)} {json.dumps(wanted[key])}")
        else:
            output.append(raw)
    return "\n".join(output).rstrip() + "\n"


def sync_fallback_credentials(config: dict[str, Any]) -> dict[str, Any]:
    fallback_path = Path(config["fallback_env"])
    fallback = read_env(fallback_path)
    if not credential(fallback):
        raise RuntimeError("fallback credential is missing")

    changed: list[str] = []
    active_path = Path(config["active_env"])
    active = read_env(active_path)
    already_active = (
        credential(active) == credential(fallback)
        and active.get("ANTHROPIC_BASE_URL", "").rstrip("/")
        == fallback.get("ANTHROPIC_BASE_URL", "").rstrip("/")
    )

    if not already_active:
        atomic_write(active_path, update_env_text(active_path.read_text(), fallback), 0o600)
        changed.append(str(active_path))

    agentic_env = Path(config["agentic_env"])
    updated_agentic = update_env_text(agentic_env.read_text(), fallback)
    if updated_agentic != agentic_env.read_text():
        atomic_write(agentic_env, updated_agentic, 0o600)
        changed.append(str(agentic_env))

    mage_cfg = Path(config["mage_cfg"])
    updated_mage = update_mage_cfg_text(mage_cfg.read_text(), fallback)
    if updated_mage != mage_cfg.read_text():
        atomic_write(mage_cfg, updated_mage, 0o600)
        changed.append(str(mage_cfg))

    compose_updates = 0
    for root in config.get("agentic_compose_roots", []):
        for compose_path in Path(root).rglob("docker-compose-agent.yml"):
            original = compose_path.read_text(errors="replace")
            updated = update_compose_env_text(original, fallback)
            if updated != original:
                atomic_write(compose_path, updated)
                compose_updates += 1

    return {
        "already_active": already_active,
        "changed_files": changed,
        "compose_updates": compose_updates,
        "fallback_fingerprint": credential_fingerprint(fallback),
        "restart_required": bool(changed),
    }


def pid_status(pid: int) -> dict[str, Any]:
    proc = Path(f"/proc/{pid}")
    if not proc.exists():
        return {"pid": pid, "alive": False}
    cmd = ""
    try:
        cmd = proc.joinpath("cmdline").read_bytes().replace(b"\x00", b" ").decode(errors="replace").strip()
    except OSError:
        pass
    return {"pid": pid, "alive": True, "cmd": redact(cmd)[:500]}


def run_pid_status(run: dict[str, Any]) -> list[dict[str, Any]]:
    statuses: list[dict[str, Any]] = []
    for pattern in run.get("pid_files", []):
        for raw_path in sorted(glob.glob(pattern)):
            path = Path(raw_path)
            try:
                pid = int(path.read_text().strip())
            except (OSError, ValueError):
                continue
            status = pid_status(pid)
            status["pid_file"] = str(path)
            statuses.append(status)
    return statuses


def result_progress(root: Path, kind: str) -> dict[str, Any]:
    if kind == "agentic":
        raw_results: dict[str, dict[str, Any]] = {}
        for path in sorted(root.rglob("raw_result.json")):
            data = load_json(path, {})
            if not isinstance(data, dict):
                continue
            for issue, result in data.items():
                if isinstance(result, dict):
                    raw_results[str(issue)] = result

        states: Counter[str] = Counter()
        evaluation_errors = 0
        for result in raw_results.values():
            errors = result.get("errors")
            if isinstance(errors, int):
                states["eval_pass" if errors == 0 else "eval_fail"] += 1
            else:
                states["unknown"] += 1

            tests = result.get("tests")
            if isinstance(tests, list) and any(
                isinstance(test, dict) and bool(test.get("error_msg"))
                for test in tests
            ):
                evaluation_errors += 1

        return {
            "completed": len(raw_results),
            "states": dict(sorted(states.items())),
            "agent_errors": 0,
            "evaluation_errors": evaluation_errors,
            "agent_reports": len(list(root.rglob("*_agent.txt"))),
        }

    rows: list[dict[str, Any]] = []
    for path in root.rglob("results.jsonl"):
        try:
            lines = path.read_text(errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    if kind == "mage":
        state_key = "status"
        agent_errors = sum(row.get("status") == "agent_error" for row in rows)
    else:
        state_key = "sim_status"
        agent_errors = sum(bool(row.get("agent_error")) for row in rows)
    states = Counter(str(row.get(state_key, "missing")) for row in rows)
    progress: dict[str, Any] = {
        "completed": len(rows),
        "states": dict(sorted(states.items())),
        "agent_errors": agent_errors,
    }
    return progress


def should_scan(path: Path) -> bool:
    name = path.name
    if name == "run.log" or name == "results.jsonl" or name.endswith("_agent.txt"):
        return True
    if path.suffix == ".jsonl" and "logs" in path.parts:
        return True
    return False


def scan_new_events(
    run: dict[str, Any], offsets: dict[str, int]
) -> list[dict[str, Any]]:
    root = Path(run["root"])
    events: list[dict[str, Any]] = []
    for path in root.rglob("*"):
        if not path.is_file() or not should_scan(path):
            continue
        key = str(path)
        try:
            size = path.stat().st_size
            start = offsets.get(key, 0)
            if start < 0 or start > size:
                start = 0
            with path.open("rb") as handle:
                handle.seek(start)
                data = handle.read()
            offsets[key] = size
        except OSError:
            continue
        if not data:
            continue
        text = data.decode(errors="replace")
        for line_no, line in enumerate(text.splitlines(), 1):
            for category, pattern in EVENT_PATTERNS:
                match = pattern.search(line)
                if not match:
                    continue
                reported_category = category
                if category in {"api_error", "api_connection"} and is_api_source_code_match(
                    line, match.start(), match.end()
                ):
                    reported_category = "ignored_source_text"
                left = max(0, match.start() - 100)
                right = min(len(line), match.end() + 180)
                events.append({
                    "timestamp": now_iso(),
                    "run": run["name"],
                    "category": reported_category,
                    "file": key,
                    "relative_line": line_no,
                    "excerpt": redact(line[left:right])[:500],
                })
                break
    return events


def append_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=True) + "\n")


def monitor_once(config: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    offsets = state.setdefault("offsets", {})
    all_events: list[dict[str, Any]] = []
    snapshots: list[dict[str, Any]] = []
    for run in config["runs"]:
        events = scan_new_events(run, offsets)
        all_events.extend(events)
        root = Path(run["root"])
        snapshots.append({
            "name": run["name"],
            "root": str(root),
            "pids": run_pid_status(run),
            "progress": result_progress(root, run.get("kind", "cktarchon")),
            "new_api_events": dict(Counter(event["category"] for event in events)),
        })

    fatal = [event for event in all_events if event["category"] in FATAL_CATEGORIES]
    switch_action = None
    if fatal and not state.get("fatal_event_handled"):
        switch_action = sync_fallback_credentials(config)
        switch_action["timestamp"] = now_iso()
        switch_action["trigger_count"] = len(fatal)
        state["fatal_event_handled"] = switch_action

    append_jsonl(Path(config["events_log"]), all_events)
    snapshot = {
        "checked_at": now_iso(),
        "runs": snapshots,
        "new_events": all_events,
        "switch_action": switch_action,
        "fatal_event_handled": state.get("fatal_event_handled"),
    }
    atomic_write(Path(config["latest_status"]), json.dumps(snapshot, indent=2, ensure_ascii=True) + "\n")
    state["last_checked_at"] = snapshot["checked_at"]
    return snapshot


def handle_stop(_signum: int, _frame: Any) -> None:
    global STOP
    STOP = True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--interval", type=int, default=120)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    config_path = Path(args.config).resolve()
    config = load_json(config_path, None)
    if not isinstance(config, dict):
        raise SystemExit(f"invalid monitor config: {config_path}")
    state_path = Path(config["state_file"])
    state = load_json(state_path, {})
    signal.signal(signal.SIGTERM, handle_stop)
    signal.signal(signal.SIGINT, handle_stop)

    while not STOP:
        try:
            snapshot = monitor_once(config, state)
            atomic_write(state_path, json.dumps(state, indent=2, ensure_ascii=True) + "\n", 0o600)
            counts = Counter(
                event["category"] for event in snapshot.get("new_events", [])
            )
            print(
                f"[{snapshot['checked_at']}] checked={len(snapshot['runs'])} "
                f"new_events={dict(counts)} switch={bool(snapshot.get('switch_action'))}",
                flush=True,
            )
        except Exception as exc:
            print(f"[{now_iso()}] monitor_error={type(exc).__name__}: {redact(str(exc))}", file=sys.stderr, flush=True)
        if args.once:
            break
        for _ in range(max(1, args.interval)):
            if STOP:
                break
            time.sleep(1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
