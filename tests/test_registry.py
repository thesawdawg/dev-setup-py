from __future__ import annotations

import pytest

from dev_setup import registry
from dev_setup.generic import GenericTool


@pytest.fixture()
def fake_registry(monkeypatch):
    """Registry pre-initialized with an empty store we control."""
    monkeypatch.setattr(registry, "_registry", {})
    monkeypatch.setattr(registry, "_order", [])
    monkeypatch.setattr(registry, "_initialized", True)
    return registry


def add(reg, key, requires=None, installed=False):
    tool = GenericTool(key=key, install_type="bash", requires=requires or [])
    tool.is_installed = lambda: installed  # type: ignore[method-assign]
    reg._register(tool)
    return tool


def test_missing_requires_direct(fake_registry):
    add(fake_registry, "dep", installed=False)
    tool = add(fake_registry, "app", requires=["dep"])
    assert fake_registry.missing_requires(tool) == ["dep"]


def test_missing_requires_transitive(fake_registry):
    add(fake_registry, "c", installed=False)
    add(fake_registry, "b", requires=["c"], installed=False)
    tool = add(fake_registry, "a", requires=["b"])
    assert fake_registry.missing_requires(tool) == ["c", "b"]


def test_missing_requires_skips_installed(fake_registry):
    add(fake_registry, "c", installed=False)
    add(fake_registry, "b", requires=["c"], installed=True)
    tool = add(fake_registry, "a", requires=["b"])
    # b is installed but its own dep c is still surfaced
    assert fake_registry.missing_requires(tool) == ["c"]


def test_missing_requires_handles_cycles(fake_registry):
    add(fake_registry, "b", requires=["a"], installed=False)
    tool = add(fake_registry, "a", requires=["b"])
    assert fake_registry.missing_requires(tool) == ["b"]


def test_missing_requires_unknown_key_reported(fake_registry):
    tool = add(fake_registry, "app", requires=["ghost"])
    assert fake_registry.missing_requires(tool) == ["ghost"]
