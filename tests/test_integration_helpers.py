"""Unit tests for the dependency installer inside tests/integration/test_tools.py.

The helper lives in the integration suite because that is where it runs, but its
logic — walk the whole requires closure, deepest first, skip deps an earlier
install already satisfied — is testable with fakes and no installs. The bug it
fixes was measured in CI: ansible-vault requires ansible, ansible requires uv,
and a clean container has no uv — the old direct loop never installed it.
"""

from __future__ import annotations

import pytest

from dev_setup import generic, registry
from tests.integration.test_tools import _install_requires

INSTALLS: list[str] = []


class _FakeTool:
    """A registry-resident fake that records install calls."""

    def __init__(self, key, requires=(), installed=False, on_install=None):
        self.key = key
        self.requires = list(requires)
        self.installed = installed
        self._on_install = on_install

    def is_installed(self):
        return self.installed

    def install(self):
        INSTALLS.append(self.key)
        self.installed = True
        if self._on_install:
            self._on_install()


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    INSTALLS.clear()
    monkeypatch.setattr(generic, "forget_probes", lambda: INSTALLS.append("<forget>"))
    yield
    # Drop any fakes the tests registered — the real registry is process-global.
    for t in list(registry._registry.values()):
        if isinstance(t, _FakeTool):
            registry.deregister(t.key)


def _register(*tools):
    for t in tools:
        registry.register(t)


def test_requires_closure_installs_deepest_first():
    a = _FakeTool("a", requires=["b"])
    b = _FakeTool("b", requires=["c"])
    c = _FakeTool("c")
    _register(a, b, c)

    _install_requires(a)

    assert INSTALLS == ["c", "<forget>", "b", "<forget>"]


def test_a_dep_an_earlier_install_satisfied_is_not_reinstalled():
    # a requires [b, c]; installing b happens to bring c in too.
    c = _FakeTool("c")
    b = _FakeTool("b", on_install=lambda: setattr(c, "installed", True))
    a = _FakeTool("a", requires=["b", "c"])
    _register(a, b, c)

    _install_requires(a)

    assert "c" not in [k for k in INSTALLS if k != "<forget>"]
    assert INSTALLS == ["b", "<forget>"]


def test_a_requires_key_missing_from_the_catalog_fails():
    a = _FakeTool("a", requires=["ghost"])
    _register(a)

    with pytest.raises(pytest.fail.Exception, match="ghost"):
        _install_requires(a)

    assert INSTALLS == []


def test_real_catalog_ansible_vault_installs_uv_then_ansible(monkeypatch):
    """Regression for the CI failure: the closure, not just direct requires."""
    registry.init()
    calls = []
    for t in registry.all_tools():
        monkeypatch.setattr(t, "is_installed", lambda: False)
        monkeypatch.setattr(t, "install", lambda t=t: calls.append(t.key))
    # is_installed fakes return False forever, so every missing dep is installed.

    _install_requires(registry.get("ansible-vault"))

    assert calls == ["uv", "ansible"]
