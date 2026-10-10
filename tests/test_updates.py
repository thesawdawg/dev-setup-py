"""The shared update collector (docs/specs/outdated, FR-15–17)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from dev_setup import registry, updates
from dev_setup.generic import UpdateStatus


def fake(key, *, installed=True, status=None, install_type="npm", boom=None):
    """A duck-typed tool. `boom` names a method that should raise."""

    def _maybe(name, value):
        def call():
            if boom == name:
                raise RuntimeError(f"{name} blew up")
            return value
        return call

    return SimpleNamespace(
        key=key,
        install_type=install_type,
        is_installed=_maybe("is_installed", installed),
        check_for_update=_maybe("check_for_update", status or UpdateStatus()),
    )


def test_returns_only_installed_tools_in_input_order():
    a = fake("a", status=UpdateStatus(current="1", latest="2", available=True))
    b = fake("b", installed=False)
    c = fake("c", status=UpdateStatus(current="3", available=False))

    got = updates.collect_candidates([a, b, c])

    assert [t.key for t, _ in got] == ["a", "c"]
    assert got[0][1] == UpdateStatus(current="1", latest="2", available=True)
    assert got[1][1] == UpdateStatus(current="3", available=False)


def test_defaults_to_the_whole_registry(monkeypatch):
    monkeypatch.setattr(registry, "all_tools", lambda: [fake("only")])

    assert [t.key for t, _ in updates.collect_candidates()] == ["only"]


def test_nothing_installed_is_an_empty_list():
    assert updates.collect_candidates([fake("a", installed=False)]) == []
    assert updates.collect_candidates([]) == []


def test_a_raising_checker_is_an_unknown_status_not_a_crash():
    got = updates.collect_candidates(
        [fake("bad", boom="check_for_update"), fake("ok", status=UpdateStatus(available=False))]
    )

    assert [t.key for t, _ in got] == ["bad", "ok"]
    assert got[0][1] == UpdateStatus()  # empty = couldn't tell
    assert got[1][1].available is False


def test_a_raising_is_installed_is_treated_as_not_installed():
    got = updates.collect_candidates([fake("bad", boom="is_installed"), fake("ok")])

    assert [t.key for t, _ in got] == ["ok"]


def test_a_tool_without_check_for_update_is_unknown():
    bare = SimpleNamespace(key="bare", is_installed=lambda: True)

    got = updates.collect_candidates([bare])

    assert got == [(bare, UpdateStatus())]


def test_update_command_uses_the_shared_collector():
    # `update`'s picker and `outdated` must call one function (FR-17), not two copies.
    from dev_setup.commands import update_cmd

    assert not hasattr(update_cmd, "_collect_update_candidates")
    assert update_cmd.collect_candidates is updates.collect_candidates


@pytest.mark.parametrize("n", [1, 5, 20])
def test_many_tools_keep_their_order_under_concurrency(n):
    tools = [fake(f"t{i:02}") for i in range(n)]

    assert [t.key for t, _ in updates.collect_candidates(tools)] == [t.key for t in tools]
