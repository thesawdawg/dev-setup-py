from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

Transport = Callable[[str, str, dict[str, Any] | None, int], dict[str, Any]]
"""(method, path, payload, timeout) -> decoded JSON. Injected in tests."""


class OllamaError(RuntimeError):
    """Base for every failure talking to the Ollama daemon."""


class OllamaUnavailable(OllamaError):
    """The daemon could not be reached at all."""


class OllamaTimeout(OllamaError):
    """The request exceeded request_timeout."""


class ModelNotFound(OllamaError):
    """The daemon does not have the requested model pulled."""


@dataclass
class ToolCall:
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class Message:
    role: str = "assistant"
    content: str = ""
    thinking: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)


def _http_transport(base_url: str) -> Transport:
    # An explicit empty ProxyHandler: urllib otherwise honours HTTP_PROXY/ALL_PROXY
    # from the environment, which would route a localhost Ollama call through a
    # corporate proxy and fail in a way that looks like "the daemon is down".
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def transport(method: str, path: str, payload: dict[str, Any] | None, timeout: int) -> dict[str, Any]:
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(
            f"{base_url}{path}",
            data=data,
            headers={"Content-Type": "application/json"},
            method=method,
        )
        with opener.open(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
        return json.loads(body) if body else {}

    return transport


_JSON_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)

_THINK_BLOCK = re.compile(r"<(think|thinking)>(.*?)</\1>", re.DOTALL | re.IGNORECASE)
_THINK_OPEN = re.compile(r"<(think|thinking)>(.*)", re.DOTALL | re.IGNORECASE)


def _split_thinking(content: str) -> tuple[str, str]:
    """Pull inline <think> blocks out of content.

    Not every Ollama build honours `think: false`, and some emit reasoning as raw
    tags in `content` rather than in the `thinking` field -- observed with lfm2.5.
    Left in place, the reasoning renders as the answer, and a `<think>` preamble
    would also hide a JSON tool call from the content fallback below.
    """
    if "<think" not in content.lower():
        return content, ""

    thoughts = [m.group(2).strip() for m in _THINK_BLOCK.finditer(content)]
    remainder = _THINK_BLOCK.sub("", content)

    # A response truncated mid-reasoning leaves an unclosed tag; everything after
    # it is thinking, not an answer.
    unterminated = _THINK_OPEN.search(remainder)
    if unterminated:
        thoughts.append(unterminated.group(2).strip())
        remainder = remainder[: unterminated.start()]

    return remainder.strip(), "\n\n".join(t for t in thoughts if t)


def _coerce_arguments(raw: Any) -> dict[str, Any]:
    """Tool arguments arrive as an object, or as a JSON string when the model
    double-encodes them (common on smaller models)."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _tool_calls_from_content(content: str) -> list[ToolCall]:
    """Fallback for models that emit a tool call as JSON in `content` instead of
    populating `tool_calls`. Deliberately conservative: only a lone object with a
    `name` key counts, so ordinary prose mentioning JSON is never misread as a call."""
    if not content or "{" not in content:
        return []

    candidate = content.strip()
    fenced = _JSON_FENCE.search(candidate)
    if fenced:
        candidate = fenced.group(1)
    elif not (candidate.startswith("{") and candidate.endswith("}")):
        return []

    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, dict):
        return []

    # Both the bare {"name": ..., "arguments": ...} and the OpenAI-ish
    # {"function": {"name": ...}} shapes show up in the wild.
    fn = parsed.get("function") if isinstance(parsed.get("function"), dict) else parsed
    name = fn.get("name")
    if not isinstance(name, str) or not name:
        return []

    args = fn.get("arguments", fn.get("parameters", {}))
    return [ToolCall(name=name, arguments=_coerce_arguments(args))]


def parse_message(raw: dict[str, Any]) -> Message:
    """Normalise one /api/chat response into a Message.

    Every shape difference between Ollama versions and between thinking/non-thinking
    models is absorbed here, so drift is a one-place fix.
    """
    msg = raw.get("message") or {}
    if not isinstance(msg, dict):
        msg = {}

    content = msg.get("content") or ""
    thinking = msg.get("thinking") or msg.get("reasoning") or ""

    content, inline_thinking = _split_thinking(content)
    if inline_thinking and not thinking:
        thinking = inline_thinking

    calls: list[ToolCall] = []
    for entry in msg.get("tool_calls") or []:
        if not isinstance(entry, dict):
            continue
        fn = entry.get("function") if isinstance(entry.get("function"), dict) else entry
        name = fn.get("name")
        if isinstance(name, str) and name:
            calls.append(ToolCall(name=name, arguments=_coerce_arguments(fn.get("arguments"))))

    if not calls:
        calls = _tool_calls_from_content(content)
        if calls:
            # The JSON *was* the tool call, not an answer to show the user.
            content = ""

    return Message(
        role=msg.get("role") or "assistant",
        content=content,
        thinking=thinking,
        tool_calls=calls,
    )


class OllamaClient:
    def __init__(self, host: str, timeout: int = 120, transport: Transport | None = None) -> None:
        self.host = host.rstrip("/")
        self.timeout = timeout
        self._transport = transport or _http_transport(self.host)

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            return self._transport(method, path, payload, self.timeout)
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode("utf-8", "replace").strip()
            except Exception:  # noqa: BLE001 - diagnostics only, never mask the HTTPError
                pass
            if exc.code == 404:
                raise ModelNotFound(body or "not found") from exc
            raise OllamaError(f"Ollama returned HTTP {exc.code}{f': {body}' if body else ''}") from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                raise OllamaTimeout(f"Ollama did not respond within {self.timeout}s") from exc
            raise OllamaUnavailable(f"Cannot reach Ollama at {self.host}: {exc.reason}") from exc
        except TimeoutError as exc:
            raise OllamaTimeout(f"Ollama did not respond within {self.timeout}s") from exc
        except json.JSONDecodeError as exc:
            raise OllamaError(f"Ollama returned a non-JSON response: {exc}") from exc

    def list_models(self) -> list[str]:
        data = self._request("GET", "/api/tags")
        names = []
        for entry in data.get("models") or []:
            name = entry.get("model") or entry.get("name")
            if name:
                names.append(name)
        return names

    def capabilities(self, model: str) -> list[str]:
        """Capability list from /api/show, e.g. ["completion", "tools", "thinking"]."""
        data = self._request("POST", "/api/show", {"model": model})
        caps = data.get("capabilities") or []
        return [c for c in caps if isinstance(c, str)]

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
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
            "think": think,
            "options": {"temperature": temperature, "num_ctx": num_ctx},
        }
        if tools:
            payload["tools"] = tools
        return parse_message(self._request("POST", "/api/chat", payload))
