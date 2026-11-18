"""`devstuff tui` — read-only catalog preview. Two essential checks: the
non-tty refusal happens before any catalog work, and the tty path converts
tools to sanitized plan entries without ever probing or executing."""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from dev_setup.cli import cli
from dev_setup.generic import GenericTool


def test_tui_refuses_non_tty_before_touching_the_catalog(monkeypatch):
    monkeypatch.setattr(
        "dev_setup.registry.all_tools",
        lambda: pytest.fail("catalog was read before the tty guard"),
    )
    result = CliRunner().invoke(cli, ["tui"])
    assert result.exit_code == 2
    assert "terminal" in result.output.lower()


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
    monkeypatch.setattr(
        "dev_setup.commands.configure_cmd.can_prompt", lambda: True
    )
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
    result = CliRunner().invoke(cli, ["tui"])
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
