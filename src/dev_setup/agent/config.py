from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from dev_setup.catalog import CONFIG_DIR, CatalogError

USER_CONFIG_PATH = CONFIG_DIR / "agent.yaml"

VERSION = 1

# lfm2.5 is the default because it advertises the `tools` capability, carries a
# 128k context, and is small enough for an edge device. Nothing in the agent
# depends on it — preflight verifies capabilities at run time instead.
DEFAULT_MODEL = "lfm2.5:latest"
DEFAULT_HOST = "http://localhost:11434"

SUPPORTED_FIELDS = {
    "version",
    "model",
    "host",
    "temperature",
    "num_ctx",
    "think",
    "max_iterations",
    "request_timeout",
    "auto_approve",
    "deny_patterns",
    "max_tool_output_bytes",
}


@dataclass
class AgentConfig:
    model: str = DEFAULT_MODEL
    host: str = DEFAULT_HOST
    temperature: float = 0.2
    # The model allows 128k, but Ollama sizes the KV cache to num_ctx and this is
    # aimed at edge devices. 16k holds a working session with tool results; raise
    # it in agent.yaml if the machine has headroom.
    num_ctx: int = 16384
    # Reasoning tokens are latency the tool loop rarely needs. Opt in per-config.
    think: bool = False
    max_iterations: int = 12
    request_timeout: int = 120
    auto_approve: list[str] = field(default_factory=list)
    deny_patterns: list[str] = field(default_factory=list)
    max_tool_output_bytes: int = 8000


_INT_FIELDS = ("num_ctx", "max_iterations", "request_timeout", "max_tool_output_bytes")
_STR_LIST_FIELDS = ("auto_approve", "deny_patterns")


def _validate(raw: Any, *, source: Path | str) -> dict[str, Any]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise CatalogError(f"{source}: config must be a mapping")

    unknown = set(raw) - SUPPORTED_FIELDS
    if unknown:
        raise CatalogError(f"{source}: unknown field(s): {', '.join(sorted(unknown))}")

    # `version` may be omitted -- this is a config file the user hand-writes, not a
    # catalog we ship, so absence means "current". A *wrong* version is still fatal,
    # since it signals a file written against a schema we no longer understand.
    version = raw.get("version", VERSION)
    if version != VERSION:
        raise CatalogError(f"{source}: version must be {VERSION}")

    data = {k: v for k, v in raw.items() if k != "version"}

    if "model" in data and not (isinstance(data["model"], str) and data["model"].strip()):
        raise CatalogError(f"{source}: 'model' must be a non-empty string")

    if "host" in data:
        host = data["host"]
        if not (isinstance(host, str) and host.strip()):
            raise CatalogError(f"{source}: 'host' must be a non-empty string")
        data["host"] = host.strip().rstrip("/")

    if "temperature" in data:
        temp = data["temperature"]
        if isinstance(temp, bool) or not isinstance(temp, int | float):
            raise CatalogError(f"{source}: 'temperature' must be a number")
        data["temperature"] = float(temp)

    if "think" in data and not isinstance(data["think"], bool):
        raise CatalogError(f"{source}: 'think' must be true or false")

    for key in _INT_FIELDS:
        if key in data:
            val = data[key]
            if isinstance(val, bool) or not isinstance(val, int) or val <= 0:
                raise CatalogError(f"{source}: '{key}' must be a positive integer")

    for key in _STR_LIST_FIELDS:
        if key in data:
            val = data[key]
            if not isinstance(val, list) or not all(isinstance(x, str) for x in val):
                raise CatalogError(f"{source}: '{key}' must be a list of strings")

    return data


def load(path: Path | None = None) -> AgentConfig:
    """Load agent config, falling back to defaults when the file is absent."""
    path = path or USER_CONFIG_PATH
    if not path.exists():
        return AgentConfig()

    try:
        raw = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        raise CatalogError(f"Invalid YAML in {path}: {exc}") from exc

    return AgentConfig(**_validate(raw, source=path))


def apply_overrides(config: AgentConfig, *, model: str | None = None, host: str | None = None) -> AgentConfig:
    """Apply per-invocation CLI overrides on top of a loaded config."""
    if model:
        config.model = model
    if host:
        config.host = host.strip().rstrip("/")
    return config
