"""`devstuff tui` — essential checks: the non-tty refusal happens before any
catalog or agent work, `--chat` is the only path that reaches the model, and
the default workspace drives real scoped actions through patched prompts."""

from __future__ import annotations

import sys
from types import SimpleNamespace

import click
import pytest
from click.testing import CliRunner

from dev_setup import catalog, functions_catalog, functions_registry, registry, ui
from dev_setup.agent import config as agent_config
from dev_setup.cli import cli
from dev_setup.generic import GenericTool


def _tty(monkeypatch):
    monkeypatch.setattr(
        "dev_setup.commands.configure_cmd.can_prompt", lambda: True
    )


@pytest.fixture()
def catalogs(tmp_path, monkeypatch):
    """Isolate the user catalogs in tmp_path; record instead of reload."""
    monkeypatch.setattr(catalog, "USER_CATALOG_PATH", tmp_path / "tools.yaml")
    monkeypatch.setattr(catalog, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(
        functions_catalog, "USER_CATALOG_PATH", tmp_path / "functions.yaml"
    )
    monkeypatch.setattr(functions_catalog, "CONFIG_DIR", tmp_path)
    reloads = []
    monkeypatch.setattr(registry, "reload", lambda: reloads.append("tools"))
    monkeypatch.setattr(
        functions_registry, "reload", lambda: reloads.append("functions")
    )
    return reloads


def _no_model(monkeypatch):
    monkeypatch.setattr(
        "dev_setup.agent.chat_setup.start_chat",
        lambda **kwargs: pytest.fail("the model path was touched"),
    )


def test_tui_refuses_non_tty_before_touching_the_catalog(monkeypatch):
    monkeypatch.setattr(
        "dev_setup.registry.all_tools",
        lambda: pytest.fail("catalog was read before the tty guard"),
    )
    result = CliRunner().invoke(cli, ["tui", "--browse"])
    assert result.exit_code == 2
    assert "terminal" in result.output.lower()


def test_tui_rejects_conflicting_and_orphaned_options():
    assert CliRunner().invoke(cli, ["tui", "--browse", "--chat"]).exit_code == 2
    result = CliRunner().invoke(cli, ["tui", "--model", "m:latest"])
    assert result.exit_code == 2
    assert "--chat" in result.output
    assert (
        CliRunner()
        .invoke(cli, ["tui", "--chat", "--api-key-env", "X"])
        .exit_code
        == 2
    )


def test_tui_chat_delegates_to_the_provider_setup(monkeypatch):
    """`--chat` is the opt-in: provider setup runs it, with the catalog
    browser wired as /browse in the session it launches."""
    _tty(monkeypatch)
    captured = {}
    monkeypatch.setattr(
        "dev_setup.agent.chat_setup.start_chat",
        lambda **kwargs: captured.update(kwargs),
    )
    result = CliRunner().invoke(
        cli,
        [
            "tui", "--chat", "--provider", "openai", "--model", "qwen3-8b@agent",
            "--host", "http://127.0.0.1:8080/v1", "--api-key-env", "MY_KEY",
        ],
    )
    assert result.exit_code == 0, result.output
    assert captured["provider"] == "openai"
    assert captured["model"] == "qwen3-8b@agent"
    assert captured["host"] == "http://127.0.0.1:8080/v1"
    assert captured["api_key_env"] == "MY_KEY"
    assert callable(captured["browse_catalog"])


def test_tui_browses_catalog_without_probing_or_leaking_scripts(monkeypatch):
    """One real GenericTool through the conversion: installed state and
    requirements are never consulted, the plan label survives, and the raw
    script body does not."""
    tool = GenericTool(
        key="demo",
        name="Demo",
        install_type="bash",
        install_script="echo SECRET_MARKER",
        check_cmd="demo",
        description="a demo tool",
    )
    _tty(monkeypatch)
    monkeypatch.setattr("dev_setup.registry.all_tools", lambda: [tool])
    for method in ("is_installed", "install", "update", "remove", "get_version"):
        monkeypatch.setattr(
            GenericTool,
            method,
            lambda *a, **k: pytest.fail("installed-state path was touched"),
        )
    seen = {}
    monkeypatch.setattr(
        "dev_setup.tui.browse",
        lambda entries, *, title: seen.update(
            entries=list(entries), title=title
        ),
    )
    result = CliRunner().invoke(cli, ["tui", "--browse"])
    assert result.exit_code == 0, result.output
    (entry,) = seen["entries"]
    assert entry.key == "demo"
    assert entry.source == "bash"
    assert entry.description == "a demo tool"
    assert entry.plan == ("install script: script body omitted",)
    joined = " ".join(
        (entry.key, entry.description, entry.category, entry.source, *entry.plan)
    )
    assert "SECRET_MARKER" not in joined


def test_tui_workspace_find_and_inspect_never_touches_the_model(
    catalogs, monkeypatch
):
    """Menu → Find tools → blank query → pick a bundled package → Back →
    Quit. The workspace reads the real catalog and shows its definition."""
    _tty(monkeypatch)
    _no_model(monkeypatch)
    monkeypatch.setattr(
        GenericTool, "is_installed",
        lambda *a, **k: pytest.fail("installed state was probed"),
    )
    picks = iter(["find", ("package", "docker"), ui.BACK, "quit"])
    monkeypatch.setattr(ui, "select", lambda *a, **k: next(picks))
    monkeypatch.setattr(ui, "text_input", lambda *a, **k: "")
    result = CliRunner().invoke(cli, ["tui"])
    assert result.exit_code == 0, result.output
    assert '"docker"' in result.output  # the definition panel
    assert '"kind": "package"' in result.output


def test_tui_workspace_guided_draft_denied_save_writes_nothing(
    catalogs, monkeypatch
):
    """Menu → Draft → APT package → fields → the scoped register prompt is
    declined: nothing is written, nothing runs, no model starts."""
    _tty(monkeypatch)
    _no_model(monkeypatch)
    monkeypatch.setattr(
        GenericTool, "install",
        lambda *a, **k: pytest.fail("an install ran"),
    )
    picks = iter(["draft", "apt", "quit"])
    monkeypatch.setattr(ui, "select", lambda *a, **k: next(picks))
    inputs = iter(["demo-apt", "a demo apt tool", "demo-pkg", ""])
    monkeypatch.setattr(ui, "text_input", lambda *a, **k: next(inputs))
    asked = []
    monkeypatch.setattr(
        ui, "confirm", lambda prompt, default=False: asked.append(prompt) or False
    )
    result = CliRunner().invoke(cli, ["tui"])
    assert result.exit_code == 0, result.output
    assert asked == ["Save this new catalog entry?"]
    assert not catalog.USER_CATALOG_PATH.exists()
    assert catalogs == []


def test_local_chat_rejects_a_remote_host_and_cloud_model_first(
    tmp_path, monkeypatch
):
    """local_chat validates before any HTTP client exists: a non-loopback
    host and a *-cloud/:cloud model are UsageErrors, not preflight failures."""
    from dev_setup.commands.agent_cmd import run_agent

    monkeypatch.setattr(agent_config, "USER_CONFIG_PATH", tmp_path / "agent.yaml")
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr(sys, "stdout", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr(
        "dev_setup.commands.agent_cmd.OllamaClient",
        lambda *a, **k: pytest.fail("a client was constructed"),
    )
    with pytest.raises(click.UsageError, match="loopback"):
        run_agent(local_chat=True, host="https://models.example.com")
    with pytest.raises(click.UsageError, match="cloud"):
        run_agent(local_chat=True, model="qwen3:cloud")


def test_local_chat_transport_refuses_redirects():
    """The local-chat opener declines every redirect — a loopback daemon
    never 302s, so one means something else answered."""
    from dev_setup.agent.ollama import OllamaClient, _NoRedirect

    assert (
        _NoRedirect().redirect_request(None, None, 302, "", {}, "https://x.test")
        is None
    )
    # Construction with local_chat works and keeps the host verbatim.
    client = OllamaClient("http://127.0.0.1:11434", local_chat=True)
    assert client.host == "http://127.0.0.1:11434"
