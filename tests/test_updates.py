"""The shared update collector (docs/specs/outdated, FR-15–17)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from dev_setup import registry, updates
from dev_setup.generic import UpdateStatus
from dev_setup.updates import Row, State, classify, make_row, sort_rows, summarize


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


# -- The five states (FR-5–8, SD-2/3) --------------------------------------------

OUTDATED = UpdateStatus(current="1.0", latest="1.1", available=True)
CURRENT = UpdateStatus(current="1.1", available=False)
EMPTY = UpdateStatus()


@pytest.mark.parametrize(
    ("install_type", "status", "installed", "expected"),
    [
        ("npm", OUTDATED, True, State.OUTDATED),
        ("npm", CURRENT, True, State.CURRENT),
        ("npm", EMPTY, True, State.UNKNOWN),
        # The distinction SD-2 exists for: an empty status means two different things.
        ("bash", EMPTY, True, State.UNSUPPORTED),
        ("script", EMPTY, True, State.UNSUPPORTED),
        ("uvx", EMPTY, True, State.UNKNOWN),
        ("apt", EMPTY, True, State.UNKNOWN),
        ("git", EMPTY, True, State.UNKNOWN),
        ("pip", EMPTY, True, State.UNKNOWN),
        # A type nothing knows about can't be checked either.
        ("composer", EMPTY, True, State.UNSUPPORTED),
        # Not installed wins over everything else.
        ("npm", OUTDATED, False, State.NOT_INSTALLED),
        ("bash", EMPTY, False, State.NOT_INSTALLED),
    ],
)
def test_classify(install_type, status, installed, expected):
    assert classify(install_type, status, installed=installed) is expected


def test_unsupported_is_decided_by_type_not_by_an_empty_status():
    """A bash tool and a failed npm probe return the *same* status and must still differ."""
    assert classify("bash", EMPTY) is not classify("npm", EMPTY)


def test_every_checkable_install_type_can_be_unknown_and_none_can_be_unsupported():
    from dev_setup import generic

    for install_type in generic._UPDATE_CHECKERS:
        assert generic.supports_update_check(install_type)
        assert classify(install_type, EMPTY) is State.UNKNOWN
    for install_type in ("bash", "script"):
        assert not generic.supports_update_check(install_type)


def test_make_row_fills_versions_from_the_status():
    tool = fake("codex", install_type="npm")

    row = make_row(tool, OUTDATED)

    assert row == Row(
        key="codex", type="npm", state=State.OUTDATED, installed="1.0", latest="1.1", note=""
    )


def test_make_row_uses_none_not_empty_string_for_absent_versions():
    row = make_row(fake("x", install_type="bash"), EMPTY)

    assert row.installed is None
    assert row.latest is None


def test_not_installed_row_has_no_versions():
    row = make_row(fake("x"), None, installed=False)

    assert row.state is State.NOT_INSTALLED
    assert (row.installed, row.latest) == (None, None)


def test_to_json_has_exactly_the_six_documented_fields():
    row = make_row(fake("codex", install_type="npm"), OUTDATED)

    assert row.to_json() == {
        "key": "codex",
        "type": "npm",
        "state": "outdated",
        "installed": "1.0",
        "latest": "1.1",
        "note": "",
    }


def _row(key, state):
    return Row(key=key, type="npm", state=state, installed=None, latest=None)


def test_sort_puts_outdated_first_and_unsupported_last_alphabetical_within_state():
    rows = [
        _row("z", State.CURRENT),
        _row("b", State.UNSUPPORTED),
        _row("a", State.UNKNOWN),
        _row("m", State.OUTDATED),
        _row("c", State.OUTDATED),
        _row("n", State.NOT_INSTALLED),
    ]

    assert [(r.key, r.state.value) for r in sort_rows(rows)] == [
        ("c", "outdated"),
        ("m", "outdated"),
        ("a", "unknown"),
        ("z", "current"),
        ("n", "not-installed"),
        ("b", "unsupported"),
    ]


def test_summary_counts_sum_to_rows_and_unknown_is_never_folded_into_current():
    rows = [
        _row("a", State.OUTDATED),
        _row("b", State.CURRENT),
        _row("c", State.CURRENT),
        _row("d", State.UNKNOWN),
        _row("e", State.UNSUPPORTED),
        _row("f", State.UNSUPPORTED),
    ]

    counts = summarize(rows)

    assert sum(counts.values()) == len(rows)
    assert counts[State.CURRENT] == 2  # not 2 + 1 unknown + 2 unsupported
    assert counts[State.UNKNOWN] == 1
    assert counts[State.UNSUPPORTED] == 2
    assert counts[State.NOT_INSTALLED] == 0
