"""The 'Enable chat' flow for `devstuff tui`.

TUI-only: pick a provider, then launch a scoped session through
`run_scoped_chat`. Configuration is per-session — nothing here writes
`agent.yaml` or stores an API key. Ollama stays loopback-only and local; an
OpenAI-compatible endpoint is warned-and-confirmed before any request goes
out. There is no provider fallback: a failure reports and returns to the
workspace menu.
"""

from __future__ import annotations

import collections
import os
import re
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import click
import questionary
from rich.text import Text

from dev_setup import runner, ui
from dev_setup.agent import config as agent_config
from dev_setup.agent import preflight
from dev_setup.agent.ollama import (
    OllamaClient,
    OllamaError,
    loopback_host,
)
from dev_setup.agent.openai_compat import (
    OpenAIClient,
    is_loopback,
    normalize_base_url,
)
from dev_setup.agent.preflight import PreflightError, normalize_model
from dev_setup.catalog import CatalogError
from dev_setup.commands.agent_cmd import run_scoped_chat

_CANCELLED = object()

_PROVIDER_MENU = [
    questionary.Choice("Ollama (local)", value="ollama"),
    questionary.Choice("OpenAI-compatible endpoint", value="openai"),
    questionary.Choice(title="Back", value=ui.BACK),
]

# Rough hardware-fit heuristics for models known to call tools — authored
# values, not measurements. (model, download GB, min RAM GiB, min VRAM GiB).
_SUGGESTIONS: list[tuple[str, float, int, int]] = [
    ("qwen3:1.7b", 1.4, 4, 3),
    ("granite4.1:3b", 2.1, 7, 4),
    ("qwen3:4b", 2.5, 11, 6),
    ("qwen3:8b", 5.2, 15, 8),
]
_SUGGESTION_SIZES = {normalize_model(name): size for name, size, _, _ in _SUGGESTIONS}

_OLLAMA_PULL_TIMEOUT = 1800
_LOCAL_CHAT_NUM_CTX = 4096

# A model tag is the argument to `ollama pull` — it becomes subprocess argv,
# so it is validated before the command is ever built.
_TAG = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9_.-]*(?:/[A-Za-z0-9][A-Za-z0-9_.-]*)*"
    r"(?::[A-Za-z0-9][A-Za-z0-9_.-]*)?"
)
_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# Picker sentinel for "type it yourself" — an object, so a real server-side
# model ID can never collide with it.
_TYPED = object()


def _prompt(fn: Callable, *args: Any, **kwargs: Any) -> Any:
    """One setup prompt — Ctrl+C/EOF on a picker or field means 'don't start
    chat'; the same keys inside a pull or a running session stay interrupts."""
    try:
        return fn(*args, **kwargs)
    except (KeyboardInterrupt, EOFError):
        return _CANCELLED


def _is_cloud_alias(model: str) -> bool:
    """Cloud models can only live on ollama.com — a loopback-only chat has no
    use for the alias. Checked on the last name/tag path segment."""
    tail = model.rsplit("/", 1)[-1].casefold()
    name, _, tag = tail.partition(":")
    return tag == "cloud" or tag.endswith("-cloud") or name.endswith("-cloud")


def _system_ram_gib() -> float | None:
    """MemTotal from /proc/meminfo in GiB; None when it can't be read —
    never an invented figure."""
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) / (1024 * 1024)
    except (OSError, ValueError, IndexError):
        pass
    return None


def _gpu_vram_gib() -> float | None:
    """Largest single nvidia GPU's memory in GiB (no summing — a model lands
    on one device); None when absent or the query fails. Other vendors are
    simply 'unknown', not assumed missing."""
    try:
        out = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=2,
            check=True,
        )
        mib = max(
            int(line.strip()) for line in out.stdout.splitlines() if line.strip()
        )
        return mib / 1024
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def _suggested(ram: float | None, vram: float | None) -> list[tuple[str, float, int, int]]:
    """Roughly-plausible rows only. Unknown RAM offers just the smallest and
    never claims it fits; the VRAM leg requires a known figure (no 'no GPU
    detected means pass')."""
    if ram is None:
        return _SUGGESTIONS[:1]
    return [
        s
        for s in _SUGGESTIONS
        if ram >= s[2] or (ram >= 4 and vram is not None and vram >= s[3])
    ]


def _default_port(url: str) -> str:
    """Pin 11434 when the URL carries no port, so the `ollama` CLI and the
    HTTP client agree on one target."""
    from urllib.parse import urlsplit

    parts = urlsplit(url)
    if parts.port is not None:
        return url
    host = parts.hostname or ""
    netloc = f"[{host}]" if ":" in host else host
    return f"{parts.scheme}://{netloc}:11434"


def _pick_model(
    installed: list[str], ram: float | None, vram: float | None
) -> str | None:
    """Choose or type a model tag; None when the user backs out."""
    rows = [
        questionary.Choice(title=f"{m}  (installed)", value=m) for m in installed
    ]
    for name, size, _, _ in _suggested(ram, vram):
        tag = normalize_model(name)
        if tag not in installed:
            rows.append(
                questionary.Choice(title=f"{tag}  (~{size} GB download)", value=tag)
            )
    rows += [
        questionary.Choice(title="Enter model tag", value=_TYPED),
        questionary.Choice(title="Back", value=ui.BACK),
    ]
    picked = _prompt(ui.select, "Model", rows, searchable=True)
    if picked is _CANCELLED or picked is ui.BACK or not picked:
        return None
    if picked is _TYPED:
        picked = _prompt(ui.text_input, "Model tag", required=True)
        if picked is _CANCELLED or not picked:
            return None
    picked = normalize_model(picked)
    if not _TAG.fullmatch(picked) or _is_cloud_alias(picked):
        ui.console.print(Text(f"'{picked}' is not a usable local model tag."))
        return None
    return picked


def _pull(binary: str, model: str, host: str) -> bool:
    """`ollama pull` through the managed runner — no terminal, bounded tail
    for the error. The env is set for the CLI; server-side cloud mode is
    configured separately."""
    tail: collections.deque[str] = collections.deque(maxlen=8)
    try:
        with ui.spinner(f"pulling {model}…"):
            runner.run(
                [binary, "pull", model],
                cwd=None,
                env={
                    **os.environ,
                    "OLLAMA_HOST": host,
                    "OLLAMA_NO_CLOUD": "1",
                },
                timeout=_OLLAMA_PULL_TIMEOUT,
                on_output=tail.append,
            )
    except RuntimeError as exc:
        ui.console.print(Text(f"pull failed: {exc}"))
        for chunk in tail:
            ui.console.print(Text(chunk.rstrip()))
        return False
    return True


def _ollama_chat(
    *, model: str | None, host: str | None, browse_catalog: Callable[[], None]
) -> None:
    binary = shutil.which("ollama")
    if binary is None:
        ui.console.print(
            Text(
                "Ollama is not installed. To use it:\n\n"
                "    devstuff install ollama\n"
                "    OLLAMA_NO_CLOUD=1 ollama serve"
            )
        )
        return
    ui.dim("chat talks to a loopback Ollama only. For fully local operation")
    ui.dim("start the daemon with OLLAMA_NO_CLOUD=1 — the client cannot")
    ui.dim("verify the server's own setting.")

    try:
        cfg = agent_config.load()
    except CatalogError as exc:
        raise click.ClickException(str(exc)) from exc

    api_host = _default_port(loopback_host(host or cfg.host))
    client = OllamaClient(
        api_host, timeout=cfg.request_timeout, local_chat=True
    )

    with ui.spinner("listing models…"):
        try:
            installed = client.list_models()
        except OllamaError as exc:
            ui.console.print(Text(f"cannot list models at {api_host}: {exc}"))
            return  # no pull attempt when the daemon isn't answering
    installed = sorted(
        {
            normalize_model(m)
            for m in installed
            if _TAG.fullmatch(m) and not _is_cloud_alias(m)
        }
    )

    if model is None:
        ram, vram = _system_ram_gib(), _gpu_vram_gib()
        note = (
            f"{ram:.1f} GiB RAM" if ram is not None else "memory unknown"
        ) + (f" + {vram:.1f} GiB VRAM" if vram is not None else "")
        ui.dim(f"hardware: {note}. Suggestions are rough size heuristics,")
        ui.dim("not measurements — tool-calling quality varies per model.")
        model = _pick_model(installed, ram, vram)
        if model is None:
            return
    else:
        model = normalize_model(model)
        if not _TAG.fullmatch(model) or _is_cloud_alias(model):
            raise click.UsageError(
                f"'{model}' is not a usable local model tag for this mode."
            )

    if model not in installed:
        size = _SUGGESTION_SIZES.get(model)
        hint = f" — about {size} GB" if size is not None else " (size unknown)"
        if not ui.confirm(
            f"Download '{model}' via {api_host}?{hint}", default=False
        ):
            ui.dim("No model downloaded.")
            return
        if not _pull(binary, model, api_host):
            return

    cfg.model = model
    cfg.num_ctx = min(cfg.num_ctx, _LOCAL_CHAT_NUM_CTX)
    with ui.spinner("checking model…"):
        try:
            resolved = preflight.check(cfg, client)
        except PreflightError as exc:
            ui.console.print(Text(str(exc)))
            for remedy in exc.remedies:
                ui.dim(f"  {remedy}")
            return
    run_scoped_chat(client, cfg, resolved, browse_catalog=browse_catalog)


def _api_key(name: str | None) -> str | None:
    """The *name* of an env var holding the key — the secret itself is never
    prompted for, echoed, or stored."""
    if name is None:
        name = _prompt(
            ui.text_input, "API key env var name (blank = none required)"
        )
        if name is _CANCELLED:
            raise _CancelledStartup
        if not name:
            return None
    if not _ENV_NAME.fullmatch(name):
        raise click.UsageError(f"{name!r} is not a valid environment variable name")
    value = os.environ.get(name)
    if not value:
        raise click.UsageError(f"${name} is not set or is empty")
    # The value becomes an HTTP header — anything outside a plain token
    # would corrupt the request (or, via a reflected error, echo the key).
    if not value.isascii() or any(not 0x21 <= ord(c) <= 0x7E for c in value):
        raise click.UsageError(
            f"${name} must contain an ASCII token without whitespace"
        )
    return value


class _CancelledStartup(Exception):
    """Backing out of an interactive field during provider setup."""


def _openai_chat(
    *,
    model: str | None,
    host: str | None,
    api_key_env: str | None,
    browse_catalog: Callable[[], None],
) -> None:
    if host is None:
        host = _prompt(
            ui.text_input,
            "Endpoint base URL (e.g. http://127.0.0.1:8080/v1)",
            required=True,
        )
        if host is _CANCELLED:
            return
    base = normalize_base_url(host)
    try:
        key = _api_key(api_key_env)
    except _CancelledStartup:
        return

    # Nothing is contacted until the user sees where the data goes; an
    # explicit --provider does not waive this.
    if not is_loopback(base) and not ui.confirm(
        f"Prompts, catalog code and tool output will be sent to {base}. "
        "Continue?",
        default=False,
    ):
        ui.dim("No endpoint contacted.")
        return

    client = OpenAIClient(base, api_key=key)

    if model is None:
        model = _pick_openai_model(client, base)
        if model is None:
            return

    cfg = agent_config.AgentConfig()
    run_scoped_chat(client, cfg, model, browse_catalog=browse_catalog)


def _pick_openai_model(client: OpenAIClient, base: str) -> str | None:
    try:
        with ui.spinner("listing models…"):
            ids = client.list_models()
    except OllamaError as exc:
        ui.console.print(Text(f"cannot list models at {base}: {exc}"))
        ids = []
    rows = [questionary.Choice(title=i, value=i) for i in ids]
    rows += [
        questionary.Choice(title="Enter model ID", value=_TYPED),
        questionary.Choice(title="Back", value=ui.BACK),
    ]
    picked = _prompt(ui.select, "Model", rows, searchable=True)
    if picked is _CANCELLED or picked is ui.BACK or not picked:
        return None
    if picked is _TYPED:
        picked = _prompt(ui.text_input, "Model ID", required=True)
        if picked is _CANCELLED or not picked:
            return None
    return picked


def start_chat(
    *,
    model: str | None,
    host: str | None,
    provider: str | None,
    api_key_env: str | None,
    browse_catalog: Callable[[], None],
) -> None:
    """Entry for `tui --chat` and the workspace's 'Enable chat'. Provider
    choice is per-invocation: explicit flags carry their Ollama meaning, an
    explicit --provider skips the menu, and nothing contacts a provider
    before the user picks one."""
    if provider is None:
        if model is not None or host is not None:
            provider = "ollama"
        else:
            provider = _prompt(ui.select, "Chat provider", _PROVIDER_MENU)
            if provider is _CANCELLED or provider is ui.BACK or not provider:
                return
    if provider == "ollama":
        _ollama_chat(model=model, host=host, browse_catalog=browse_catalog)
    elif provider == "openai":
        _openai_chat(
            model=model,
            host=host,
            api_key_env=api_key_env,
            browse_catalog=browse_catalog,
        )
    else:
        raise click.UsageError(f"unknown provider {provider!r}")
