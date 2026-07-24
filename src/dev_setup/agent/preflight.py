from __future__ import annotations

import shutil
from urllib.parse import urlparse

from dev_setup.agent.config import AgentConfig
from dev_setup.agent.ollama import ModelNotFound, OllamaClient, OllamaError

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", ""}


class PreflightError(RuntimeError):
    """A precondition failed, with concrete steps to fix it."""

    def __init__(self, message: str, remedies: list[str] | None = None) -> None:
        super().__init__(message)
        self.remedies = remedies or []


def _is_local(host: str) -> bool:
    return (urlparse(host).hostname or "") in _LOCAL_HOSTS


def normalize_model(name: str) -> str:
    """`ollama list` reports tags in full; users type the bare name."""
    return name if ":" in name else f"{name}:latest"


def check(config: AgentConfig, client: OllamaClient) -> str:
    """Verify Ollama is usable with the configured model. Returns the resolved
    model tag, or raises PreflightError with actionable remedies."""

    # A remote host needs no local binary, so only gate on `which` when we'd be
    # talking to a daemon this machine is responsible for running.
    if _is_local(config.host) and shutil.which("ollama") is None:
        raise PreflightError(
            "Ollama is not installed.",
            ["devstuff install ollama"],
        )

    try:
        available = client.list_models()
    except OllamaError as exc:
        raise PreflightError(
            str(exc),
            [
                "sudo systemctl start ollama",
                "ollama serve   # if not running under systemd",
                f"Check the host setting: {config.host}",
            ]
            if _is_local(config.host)
            else [f"Check that {config.host} is reachable and running Ollama."],
        ) from exc

    model = normalize_model(config.model)
    if model not in available:
        remedies = [f"ollama pull {config.model}"]
        if available:
            remedies.append(f"Or use one you have: {', '.join(sorted(available))}")
        raise PreflightError(f"Model '{config.model}' is not available locally.", remedies)

    try:
        caps = client.capabilities(model)
    except ModelNotFound as exc:
        raise PreflightError(
            f"Model '{model}' disappeared between listing and inspection.",
            [f"ollama pull {config.model}"],
        ) from exc
    except OllamaError as exc:
        raise PreflightError(str(exc)) from exc

    if "tools" not in caps:
        remedies = ["Pick a model that supports tool-calling."]
        alternatives = _tool_capable(client, available, exclude=model)
        if alternatives:
            remedies.append(f"Locally available with tool support: {', '.join(alternatives)}")
        raise PreflightError(
            f"Model '{model}' does not support tool-calling "
            f"(capabilities: {', '.join(caps) or 'none reported'}).",
            remedies,
        )

    return model


def _tool_capable(client: OllamaClient, models: list[str], *, exclude: str) -> list[str]:
    """Best-effort scan for usable alternatives -- purely to improve an error
    message, so a model that fails inspection is skipped rather than raising."""
    found = []
    for name in sorted(models):
        if name == exclude:
            continue
        try:
            if "tools" in client.capabilities(name):
                found.append(name)
        except OllamaError:
            continue
    return found
