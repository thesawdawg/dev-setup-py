"""OpenAI-compatible provider for `tui --chat --provider openai`.

A minimal client — `GET /models` + `POST /chat/completions` — that satisfies
the shared `ChatClient` shape so the scoped session, tool loop and approvals
are identical to the Ollama path. The wire differences (tool-call ids,
messages shape) are contained here; nothing Ollama-specific (num_ctx,
thinking, capability probes) is sent.
"""

from __future__ import annotations

import json
import urllib.error
from typing import Any
from urllib.parse import urlsplit

import click

from dev_setup.agent.ollama import (
    Message,
    OllamaError,
    OllamaTimeout,
    OllamaUnavailable,
    ToolCall,
    Transport,
    _http_transport,
)

_LOOPBACK = {"localhost", "127.0.0.1", "::1"}


def is_loopback(url: str) -> bool:
    return (urlsplit(url).hostname or "") in _LOOPBACK


def normalize_base_url(raw: str) -> str:
    """Validate an OpenAI-compatible endpoint base URL: http(s), no
    credentials, query, fragment or controls; path prefixes allowed. A bare
    host gets `/v1` appended; an explicit `/chat/completions` suffix is
    rejected — the client builds that path itself."""
    guidance = (
        "Use the endpoint's base URL, e.g. http://127.0.0.1:8080/v1 — "
        "not the /chat/completions path."
    )
    # Errors never echo raw: userinfo or a query could carry a secret.
    if any(ord(c) < 0x20 or ord(c) == 0x7F or c.isspace() for c in raw):
        raise click.UsageError("Endpoint URL contains control characters.")
    try:
        parts = urlsplit(raw)
        port = parts.port
    except ValueError as exc:
        raise click.UsageError("Invalid endpoint URL.") from exc
    if (
        parts.scheme not in {"http", "https"}
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or parts.query
        or parts.fragment
    ):
        raise click.UsageError(
            "Invalid endpoint URL: use HTTP(S) without credentials, query, "
            "or fragment."
        )
    if parts.scheme == "http" and not is_loopback(raw):
        raise click.UsageError(
            "Plain http is only allowed on a loopback endpoint; use https "
            "for anything else."
        )
    path = parts.path.rstrip("/")
    if path.endswith("/chat/completions"):
        raise click.UsageError(guidance)
    if not path:
        path = "/v1"
    host = parts.hostname
    netloc = f"[{host}]" if ":" in host else host
    if port is not None:
        netloc = f"{netloc}:{port}"
    return f"{parts.scheme}://{netloc}{path}"


def _serialize_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Internal history -> OpenAI wire shape: assistant tool calls carry an
    `id` and stringified arguments; tool results echo `tool_call_id` and drop
    `tool_name`. Ids are never inferred — every turn on this path already
    carries one, and guessing would pair a result with the wrong call."""
    out: list[dict[str, Any]] = []
    for m in messages:
        role = m.get("role")
        calls = m.get("tool_calls")
        if role == "assistant" and calls:
            wire_calls = []
            for c in calls:
                fn = c.get("function") or {}
                wire_calls.append({
                    "id": c["id"],
                    "type": "function",
                    "function": {
                        "name": fn.get("name", ""),
                        "arguments": json.dumps(fn.get("arguments") or {}),
                    },
                })
            out.append({
                "role": "assistant",
                "content": m.get("content") or "",
                "tool_calls": wire_calls,
            })
        elif role == "tool":
            out.append({
                "role": "tool",
                "tool_call_id": m["tool_call_id"],
                "content": m.get("content") or "",
            })
        else:
            out.append({"role": role, "content": m.get("content") or ""})
    return out


def _parse_response(raw: dict[str, Any]) -> Message:
    """`choices[0].message` -> Message. Tool calls need unique non-empty ids
    and names, and arguments that decode to an object — a malformed response
    raises rather than executing a single call."""
    choices = raw.get("choices")
    if not isinstance(choices, list) or not choices:
        raise OllamaError("endpoint returned no choices")
    first = choices[0]
    msg = first.get("message") if isinstance(first, dict) else None
    if not isinstance(msg, dict):
        raise OllamaError("endpoint returned no message")
    content = msg.get("content")
    if content is not None and not isinstance(content, str):
        raise OllamaError("endpoint returned a non-text message")

    calls: list[ToolCall] = []
    seen: set[str] = set()
    entries = msg.get("tool_calls")
    if entries is None:
        entries = []
    if not isinstance(entries, list):
        raise OllamaError("endpoint returned malformed tool calls")
    for entry in entries:
        fn = entry.get("function") if isinstance(entry, dict) else None
        cid = entry.get("id") if isinstance(entry, dict) else None
        etype = entry.get("type") if isinstance(entry, dict) else None
        name = fn.get("name") if isinstance(fn, dict) else None
        if (
            (etype is not None and etype != "function")
            or not isinstance(cid, str)
            or not cid.strip()
            or cid in seen
            or not isinstance(name, str)
            or not name.strip()
        ):
            raise OllamaError("endpoint returned malformed tool calls")
        arguments = fn.get("arguments")
        if not isinstance(arguments, str):
            raise OllamaError("endpoint returned malformed tool call arguments")
        try:
            args = json.loads(arguments)
        except json.JSONDecodeError as exc:
            raise OllamaError(
                "endpoint returned malformed tool call arguments"
            ) from exc
        if not isinstance(args, dict):
            raise OllamaError("endpoint returned malformed tool call arguments")
        seen.add(cid)
        calls.append(ToolCall(name=name, arguments=args, id=cid))
    return Message(
        role=msg.get("role") or "assistant",
        content=content or "",
        tool_calls=calls,
    )


class OpenAIClient:
    """Minimal OpenAI-compatible client satisfying the `ChatClient` shape.

    The API key only ever becomes an Authorization header on this transport —
    it is never stored, echoed, or put into a subprocess environment.
    """

    def __init__(
        self,
        host: str,
        timeout: int = 120,
        transport: Transport | None = None,
        *,
        api_key: str | None = None,
    ) -> None:
        self.host = host.rstrip("/")
        self.timeout = timeout
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else None
        self._transport = transport or _http_transport(
            self.host, allow_redirects=False, headers=headers
        )

    def _request(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        try:
            result = self._transport(method, path, payload, self.timeout)
        except urllib.error.HTTPError as exc:
            # No response body in the message — it can carry credentials or
            # provider internals back onto the user's screen.
            raise OllamaError(
                f"endpoint returned HTTP {exc.code}"
            ) from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                raise OllamaTimeout(
                    f"endpoint did not respond within {self.timeout}s"
                ) from exc
            raise OllamaUnavailable(
                f"cannot reach the endpoint at {self.host}: {exc.reason}"
            ) from exc
        except TimeoutError as exc:
            raise OllamaTimeout(
                f"endpoint did not respond within {self.timeout}s"
            ) from exc
        except json.JSONDecodeError as exc:
            raise OllamaError(
                f"endpoint returned a non-JSON response: {exc}"
            ) from exc
        if not isinstance(result, dict):
            raise OllamaError("endpoint returned a non-object JSON response")
        return result

    def list_models(self) -> list[str]:
        data = self._request("GET", "/models")
        entries = data.get("data")
        if not isinstance(entries, list):
            raise OllamaError("endpoint returned a malformed model list")
        return [
            entry["id"]
            for entry in entries
            if isinstance(entry, dict)
            and isinstance(entry.get("id"), str)
            and entry["id"].strip()
            and entry["id"].isprintable()
        ]

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.2,
        num_ctx: int = 16384,
        think: bool = False,
    ) -> Message:
        # num_ctx/think are Ollama concepts — accepted for the shared shape
        # and deliberately not transmitted.
        payload: dict[str, Any] = {
            "model": model,
            "messages": _serialize_messages(messages),
            "stream": False,
            "temperature": temperature,
        }
        if tools:
            payload["tools"] = tools
        return _parse_response(
            self._request("POST", "/chat/completions", payload)
        )
