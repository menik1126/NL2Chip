from __future__ import annotations

import contextlib
import json
import os
import threading
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Iterator
from urllib.parse import urlparse


DEFAULT_BASE_URL_ENV = "CKTARCHON_CODEX_BASE_URL"
DEFAULT_KEY_ENV = "CKTARCHON_CODEX_API_KEY"


def _content_to_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
                continue
            if not isinstance(item, dict):
                parts.append(json.dumps(item, ensure_ascii=False))
                continue
            kind = item.get("type")
            if kind in {"input_text", "output_text", "text"}:
                parts.append(str(item.get("text", "")))
            elif kind == "input_image":
                parts.append(f"[input_image: {item.get('image_url', '')}]")
            elif kind == "input_audio":
                parts.append(f"[input_audio: {item.get('audio_url', '')}]")
            elif "text" in item:
                parts.append(str(item.get("text", "")))
            else:
                parts.append(json.dumps(item, ensure_ascii=False))
        return "\n".join(part for part in parts if part)
    if isinstance(content, dict):
        if "text" in content:
            return str(content.get("text") or "")
        if "content" in content:
            return _content_to_text(content.get("content"))
    return json.dumps(content, ensure_ascii=False)


def function_output_to_text(output: Any) -> str:
    """Convert Codex Responses function_call_output payloads to chat text."""
    return _content_to_text(output)


def _truthy_env(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def responses_input_to_messages(
    input_items: Any,
    instructions: str | None = None,
    *,
    user_assistant_only: bool = False,
) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    pending_system = f"System instructions:\n{instructions}" if instructions and user_assistant_only else ""

    def append_user(content: Any) -> None:
        nonlocal pending_system
        text = _content_to_text(content)
        if pending_system:
            text = f"{pending_system}\n\nUser message:\n{text}" if text else pending_system
            pending_system = ""
        messages.append({"role": "user", "content": text})

    if instructions and not user_assistant_only:
        messages.append({"role": "system", "content": instructions})

    if isinstance(input_items, str):
        input_items = [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": input_items}]}]
    elif isinstance(input_items, dict):
        input_items = [input_items]
    elif input_items is None:
        input_items = []

    for item in input_items:
        if not isinstance(item, dict):
            append_user(item)
            continue
        kind = item.get("type")
        if kind == "message":
            role = str(item.get("role") or "user")
            content = _content_to_text(item.get("content"))
            if user_assistant_only:
                if role == "assistant":
                    messages.append({"role": "assistant", "content": content})
                elif role == "user":
                    append_user(content)
                else:
                    append_user(f"[{role} message]\n{content}" if content else f"[{role} message]")
            else:
                messages.append({"role": role, "content": content})
        elif kind == "function_call":
            call_id = str(item.get("call_id") or item.get("id") or f"call_{uuid.uuid4().hex[:16]}")
            name = str(item.get("name") or "")
            arguments = str(item.get("arguments") or "{}")
            if user_assistant_only:
                content = item.get("content") or f"[tool call {call_id}: {name}]\n{arguments}"
                messages.append({"role": "assistant", "content": content})
            else:
                messages.append(
                    {
                        "role": "assistant",
                        "content": item.get("content") or None,
                        "tool_calls": [
                            {
                                "id": call_id,
                                "type": "function",
                                "function": {
                                    "name": name,
                                    "arguments": arguments,
                                },
                            }
                        ],
                    }
                )
        elif kind in {"function_call_output", "custom_tool_call_output", "mcp_tool_call_output"}:
            call_id = str(item.get("call_id") or "")
            content = function_output_to_text(item.get("output"))
            if user_assistant_only:
                append_user(f"[tool output {call_id}]\n{content}")
            else:
                messages.append({"role": "tool", "tool_call_id": call_id, "content": content})
        elif kind in {"reasoning", "tool_search_output"}:
            continue
        else:
            append_user(item)

    if not messages:
        append_user("")
    elif pending_system:
        messages.insert(0, {"role": "user", "content": pending_system})
    return messages


def responses_tools_to_chat_tools(tools: Any) -> list[dict[str, Any]]:
    if not isinstance(tools, list):
        return []
    out: list[dict[str, Any]] = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        if tool.get("type") == "function" and isinstance(tool.get("function"), dict):
            out.append(tool)
            continue
        if tool.get("type") == "function" and tool.get("name"):
            out.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool.get("name"),
                        "description": tool.get("description", ""),
                        "parameters": tool.get("parameters") or tool.get("input_schema") or {"type": "object"},
                    },
                }
            )
    return out


def responses_request_to_chat_request(body: dict[str, Any]) -> dict[str, Any]:
    request: dict[str, Any] = {
        "model": body.get("model"),
        "messages": responses_input_to_messages(
            body.get("input"),
            body.get("instructions"),
            user_assistant_only=_truthy_env("CKTARCHON_CHAT_USER_ASSISTANT_ONLY"),
        ),
        "stream": False,
    }
    tools = responses_tools_to_chat_tools(body.get("tools"))
    if tools:
        request["tools"] = tools
        tool_choice = body.get("tool_choice")
        if isinstance(tool_choice, str):
            request["tool_choice"] = tool_choice
        elif isinstance(tool_choice, dict):
            request["tool_choice"] = tool_choice
    if body.get("parallel_tool_calls") is not None:
        request["parallel_tool_calls"] = bool(body.get("parallel_tool_calls"))
    if body.get("temperature") is not None:
        request["temperature"] = body.get("temperature")
    if body.get("max_output_tokens") is not None:
        request["max_tokens"] = body.get("max_output_tokens")
    return {k: v for k, v in request.items() if v is not None}


def chat_response_to_responses_events(chat: dict[str, Any], response_id: str | None = None) -> list[dict[str, Any]]:
    response_id = response_id or str(chat.get("id") or f"resp_{uuid.uuid4().hex}")
    events: list[dict[str, Any]] = [{"type": "response.created", "response": {"id": response_id}}]
    choice = (chat.get("choices") or [{}])[0] or {}
    message = choice.get("message") or {}
    content = message.get("content") or ""
    if content:
        events.append(
            {
                "type": "response.output_item.done",
                "item": {
                    "type": "message",
                    "role": "assistant",
                    "id": f"msg_{uuid.uuid4().hex[:16]}",
                    "content": [{"type": "output_text", "text": content}],
                },
            }
        )
    for idx, tool_call in enumerate(message.get("tool_calls") or []):
        if not isinstance(tool_call, dict):
            continue
        fn = tool_call.get("function") or {}
        events.append(
            {
                "type": "response.output_item.done",
                "item": {
                    "type": "function_call",
                    "id": f"fc_{uuid.uuid4().hex[:16]}",
                    "call_id": str(tool_call.get("id") or f"call_{idx}_{uuid.uuid4().hex[:10]}"),
                    "name": str(fn.get("name") or ""),
                    "arguments": str(fn.get("arguments") or "{}"),
                },
            }
        )

    usage = chat.get("usage") or {}
    input_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
    output_tokens = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
    total_tokens = int(usage.get("total_tokens") or input_tokens + output_tokens)
    events.append(
        {
            "type": "response.completed",
            "response": {
                "id": response_id,
                "usage": {
                    "input_tokens": input_tokens,
                    "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
                    "output_tokens": output_tokens,
                    "output_tokens_details": {"reasoning_tokens": 0},
                    "total_tokens": total_tokens,
                },
            },
        }
    )
    return events


def events_to_sse(events: list[dict[str, Any]]) -> bytes:
    chunks: list[str] = []
    for event in events:
        kind = event.get("type", "message")
        chunks.append(f"event: {kind}\n")
        chunks.append(f"data: {json.dumps(event, ensure_ascii=False)}\n\n")
    return "".join(chunks).encode("utf-8")


def upstream_chat_url_from_env() -> str:
    explicit = os.environ.get("CKTARCHON_UPSTREAM_CHAT_URL")
    if explicit:
        return explicit
    base = os.environ.get("OPENAI_BASE_URL") or os.environ.get("ANTHROPIC_BASE_URL") or "https://api.openai.com/v1"
    base = base.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    if base.endswith("/v1"):
        return f"{base}/chat/completions"
    return f"{base}/v1/chat/completions"


def upstream_api_key_from_env() -> str:
    for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        value = os.environ.get(name)
        if value:
            return value
    raise RuntimeError("No upstream API key found in OPENAI_API_KEY, ANTHROPIC_API_KEY, or ANTHROPIC_AUTH_TOKEN")


def inherit_proxy_env_from_processes() -> int:
    """Best-effort H20 helper: copy proxy vars from another same-user process.

    Batch runs are often launched by a non-login service that already has the
    proxy exported, while ad-hoc ssh commands do not. Values are never logged.
    """
    proxy_keys = {"http_proxy", "https_proxy", "all_proxy", "no_proxy"}
    if any(key.lower() in proxy_keys for key in os.environ):
        return 0
    proc = os.scandir("/proc") if os.path.isdir("/proc") else None
    if proc is None:
        return 0
    copied: dict[str, str] = {}
    with proc:
        for entry in proc:
            if not entry.name.isdigit():
                continue
            env_path = f"/proc/{entry.name}/environ"
            try:
                raw = open(env_path, "rb").read()
            except OSError:
                continue
            found: dict[str, str] = {}
            for item in raw.split(b"\0"):
                if b"=" not in item:
                    continue
                key_b, value_b = item.split(b"=", 1)
                key = key_b.decode(errors="ignore")
                if key.lower() in proxy_keys and value_b:
                    found[key] = value_b.decode(errors="ignore")
            if found:
                copied = found
                break
    for key, value in copied.items():
        os.environ.setdefault(key, value)
    return len(copied)


@dataclass
class ResponsesChatProxy:
    upstream_chat_url: str
    upstream_api_key: str
    timeout_s: float = 300.0

    def handle_responses_request(self, body: dict[str, Any]) -> bytes:
        response_id = f"resp_{uuid.uuid4().hex}"
        try:
            chat_request = responses_request_to_chat_request(body)
            chat_response = self._post_chat(chat_request)
            return events_to_sse(chat_response_to_responses_events(chat_response, response_id=response_id))
        except Exception as exc:
            event = {
                "type": "response.failed",
                "response": {
                    "id": response_id,
                    "error": {"code": "proxy_error", "message": f"{type(exc).__name__}: {exc}"},
                },
            }
            return events_to_sse([event])

    def _post_chat(self, body: dict[str, Any]) -> dict[str, Any]:
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            self.upstream_chat_url,
            data=data,
            headers={
                "Authorization": f"Bearer {self.upstream_api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"upstream chat HTTP {exc.code}: {detail[:1000]}") from exc


class _ProxyHTTPServer(ThreadingHTTPServer):
    proxy: ResponsesChatProxy


class _ProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path in {"/health", "/v1/health"}:
            self._send_json({"ok": True})
            return
        if path in {"/models", "/v1/models"}:
            self._send_json({"object": "list", "data": []})
            return
        self.send_error(404, "not found")

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path not in {"/responses", "/v1/responses"}:
            self.send_error(404, "not found")
            return
        length = int(self.headers.get("content-length") or "0")
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw.decode("utf-8"))
            payload = self.server.proxy.handle_responses_request(body)  # type: ignore[attr-defined]
        except Exception as exc:
            payload = events_to_sse(
                [
                    {
                        "type": "response.failed",
                        "response": {
                            "id": f"resp_{uuid.uuid4().hex}",
                            "error": {"code": "proxy_request_error", "message": f"{type(exc).__name__}: {exc}"},
                        },
                    }
                ]
            )
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        try:
            self.wfile.write(payload)
        except BrokenPipeError:
            return

    def _send_json(self, obj: dict[str, Any]) -> None:
        payload = json.dumps(obj).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        try:
            self.wfile.write(payload)
        except BrokenPipeError:
            return


@dataclass
class ProxyHandle:
    server: _ProxyHTTPServer
    thread: threading.Thread
    base_url: str

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def start_proxy(host: str = "127.0.0.1", port: int = 0, timeout_s: float = 300.0) -> ProxyHandle:
    inherit_proxy_env_from_processes()
    proxy = ResponsesChatProxy(
        upstream_chat_url=upstream_chat_url_from_env(),
        upstream_api_key=upstream_api_key_from_env(),
        timeout_s=timeout_s,
    )
    server = _ProxyHTTPServer((host, port), _ProxyHandler)
    server.proxy = proxy
    thread = threading.Thread(target=server.serve_forever, name="cktarchon-responses-chat-proxy", daemon=True)
    thread.start()
    actual_port = server.server_address[1]
    return ProxyHandle(server=server, thread=thread, base_url=f"http://{host}:{actual_port}/v1")


@contextlib.contextmanager
def auto_proxy_env(enabled: bool = True, timeout_s: float = 300.0) -> Iterator[dict[str, str]]:
    if not enabled:
        yield {}
        return
    handle = start_proxy(timeout_s=timeout_s)
    try:
        yield {DEFAULT_BASE_URL_ENV: handle.base_url, DEFAULT_KEY_ENV: "local-proxy"}
    finally:
        handle.close()


def main() -> None:
    handle = start_proxy()
    print(json.dumps({"base_url": handle.base_url}))
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        handle.close()


if __name__ == "__main__":
    main()
