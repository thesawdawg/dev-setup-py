"""The diff classifier (docs/specs/profile, FR-16–18, FR-20, FR-22; development-plan P4).

Pure: it compares a `Profile` to *facts* about the machine, so none of this needs a registry,
a subprocess or a terminal. The rule it exists to enforce is FR-17: `unverifiable`,
`unpinnable` and `unknown-key` are three different ways the comparison could not be made, and
none of them may ever read as `ok`.
"""

from __future__ import annotations

import itertools

import pytest

from dev_setup.profile import (
    DiffRow,
    DiffState,
    Entry,
    MachineTool,
    Profile,
    compare,
    differs,
    sort_diff_rows,
    summarize_diff,
)

S = DiffState


def machine_tool(**kw) -> MachineTool:
    kw.setdefault("install_type", "npm")
    kw.setdefault("installed", True)
    kw.setdefault("pinnable", True)
    return MachineTool(**kw)


def one(entry: Entry, tool: MachineTool | None) -> DiffRow:
    """Compare a one-key profile; `tool` None means the catalog doesn't know the key."""
    rows = compare(Profile({"k": entry}), {} if tool is None else {"k": tool})
    assert len(rows) == 1, rows
    return rows[0]


# -- each state, with the exact case that produces it (FR-16) ------------------------------------


def test_ok_unpinned_and_installed():
    assert one(Entry(), machine_tool()).state is S.OK


def test_ok_pinned_and_at_the_pinned_version():
    row = one(Entry("1.2.3"), machine_tool(version="1.2.3"))

    assert row.state is S.OK
    assert (row.pinned, row.installed) == ("1.2.3", "1.2.3")


def test_missing_in_profile_and_catalog_but_not_installed():
    assert one(Entry(), machine_tool(installed=False)).state is S.MISSING


def test_drift_pinned_installed_at_another_version():
    row = one(Entry("1.2.3"), machine_tool(version="1.2.4"))

    assert row.state is S.DRIFT
    assert (row.pinned, row.installed) == ("1.2.3", "1.2.4")


def test_unverifiable_pinned_installed_but_version_unreadable():
    row = one(Entry("1.2.3"), machine_tool(version="", why_unreadable="uv doesn't list it"))

    assert row.state is S.UNVERIFIABLE
    assert "uv doesn't list it" in row.note


def test_unpinnable_pinned_on_a_type_that_cannot_honour_a_pin():
    row = one(Entry("1.2.3"), machine_tool(install_type="bash", pinnable=False))

    assert row.state is S.UNPINNABLE
    assert "bash" in row.note and "ignored" in row.note


def test_unknown_key_when_the_catalog_does_not_have_it():
    row = one(Entry(), None)

    assert row.state is S.UNKNOWN_KEY
    assert row.type is None
    assert "catalog" in row.note


def test_extra_installed_and_in_the_catalog_but_not_in_the_profile():
    rows = compare(Profile(), {"stray": machine_tool()})

    assert [(r.key, r.state) for r in rows] == [("stray", S.EXTRA)]


# -- precedence: when several things are true at once -------------------------------------------


def test_unknown_key_beats_everything_even_with_a_pin():
    assert one(Entry("1.0"), None).state is S.UNKNOWN_KEY


def test_missing_beats_a_pin_that_could_not_be_honoured_anyway():
    # Not installed yet, so whether the pin could be honoured is not the first problem.
    tool = machine_tool(installed=False, install_type="bash", pinnable=False)

    assert one(Entry("1.0"), tool).state is S.MISSING


def test_a_missing_tool_still_reports_what_the_profile_asked_for():
    row = one(Entry("1.2.3"), machine_tool(installed=False))

    assert row.pinned == "1.2.3" and row.installed is None


def test_unpinnable_is_decided_before_the_version_is_looked_at():
    # A bash tool whose text version happens to equal the pin must not read as ok.
    assert one(Entry("1.0"), machine_tool(pinnable=False, version="1.0")).state is S.UNPINNABLE


# -- versions compare by string equality (FR-18) -------------------------------------------------


@pytest.mark.parametrize(
    ("pin", "installed"),
    [
        ("0.45", "0.45.0"), ("0.45.0", "0.45"), ("1.10", "1.1"),
        ("v1.2", "1.2"), ("1.2.3", "1.2.3 "), ("1.2.3", "1.2.3-1"),
    ],
)
def test_nearly_equal_versions_are_drift_not_ok(pin, installed):
    assert one(Entry(pin), machine_tool(version=installed)).state is S.DRIFT


def test_an_older_or_newer_installed_version_is_equally_drift():
    # No ordering: "newer than the pin" is as much a difference as "older".
    assert one(Entry("1.0.0"), machine_tool(version="2.0.0")).state is S.DRIFT
    assert one(Entry("2.0.0"), machine_tool(version="1.0.0")).state is S.DRIFT


# -- extras ----------------------------------------------------------------------------------


def test_a_tool_not_in_the_profile_and_not_installed_is_not_a_row_at_all():
    assert compare(Profile(), {"a": machine_tool(installed=False)}) == []


def test_extras_and_profile_rows_are_both_reported():
    rows = compare(Profile({"want": Entry()}), {"want": machine_tool(), "stray": machine_tool()})

    assert {(r.key, r.state) for r in rows} == {("want", S.OK), ("stray", S.EXTRA)}


def test_a_key_in_the_profile_is_never_also_reported_as_extra():
    rows = compare(Profile({"a": Entry()}), {"a": machine_tool()})

    assert [r.key for r in rows] == ["a"]


# -- the decision table, exhaustively: the spec's rules hold for every combination -----------------


def _grid():
    for in_profile, pin, known, installed, pinnable, version in itertools.product(
        (True, False),            # key is in the profile
        (None, "1.0"),            # the profile's pin
        (True, False),            # the catalog knows the key
        (True, False),            # installed
        (True, False),            # the type can honour a pin
        ("", "1.0", "2.0"),       # the version read from the machine
    ):
        if not in_profile and pin is not None:
            continue  # a pin only exists on a profile entry
        yield in_profile, pin, known, installed, pinnable, version


@pytest.mark.parametrize(("in_profile", "pin", "known", "installed", "pinnable", "version"), list(_grid()))
def test_every_combination_obeys_the_spec(in_profile, pin, known, installed, pinnable, version):
    profile = Profile({"k": Entry(pin)}) if in_profile else Profile()
    machine = {"k": machine_tool(installed=installed, pinnable=pinnable, version=version)} if known else {}

    rows = compare(profile, machine)
    states = [r.state for r in rows]

    if not in_profile:
        # FR-16 `extra`: installed, in the catalog, not in the profile — otherwise no row.
        assert states == ([S.EXTRA] if known and installed else [])
        return
    assert len(rows) == 1
    (state,) = states
    if not known:
        assert state is S.UNKNOWN_KEY
    elif not installed:
        assert state is S.MISSING
    elif pin is None:
        assert state is S.OK
    elif not pinnable:
        assert state is S.UNPINNABLE
    elif version == "":
        assert state is S.UNVERIFIABLE
    elif version == pin:
        assert state is S.OK
    else:
        assert state is S.DRIFT

    # FR-17, stated as the property itself rather than as a case: `ok` is only ever the answer
    # when the comparison genuinely succeeded.
    if state is S.OK:
        assert known and installed
        assert pin is None or (pinnable and version == pin)


def test_the_grid_exercises_every_state():
    seen = set()
    for in_profile, pin, known, installed, pinnable, version in _grid():
        profile = Profile({"k": Entry(pin)}) if in_profile else Profile()
        tool = machine_tool(installed=installed, pinnable=pinnable, version=version)
        machine = {"k": tool} if known else {}
        seen |= {r.state for r in compare(profile, machine)}

    assert seen == set(DiffState), "a state the exhaustive test never reaches proves nothing"


# -- FR-17: the three "could not compare" states never fold into ok -----------------------------


def test_the_three_could_not_compare_states_are_distinct_from_each_other_and_from_ok():
    rows = compare(
        Profile({"a": Entry("1.0"), "b": Entry("1.0"), "c": Entry("1.0"), "d": Entry()}),
        {
            "a": machine_tool(version=""),                    # unverifiable
            "b": machine_tool(pinnable=False, version="1.0"),  # unpinnable
            # "c" is not in the catalog                          unknown-key
            "d": machine_tool(),                              # ok
        },
    )

    assert {r.key: r.state for r in rows} == {
        "a": S.UNVERIFIABLE, "b": S.UNPINNABLE, "c": S.UNKNOWN_KEY, "d": S.OK,
    }
    counts = summarize_diff(rows)
    assert counts[S.OK] == 1, "ok must not absorb the states where the comparison failed"


def test_counts_sum_to_the_rows_and_every_state_is_present():
    profile = Profile({"a": Entry(), "b": Entry()})
    rows = compare(profile, {"a": machine_tool(), "b": machine_tool(installed=False)})

    counts = summarize_diff(rows)

    assert set(counts) == set(DiffState)
    assert sum(counts.values()) == len(rows) == 2
    assert counts[S.OK] == 1 and counts[S.MISSING] == 1 and counts[S.DRIFT] == 0


# -- ordering (FR-19 display order) ------------------------------------------------------------


def test_problems_sort_before_ok_and_alphabetically_within_a_state():
    rows = [
        DiffRow("z", S.OK, "npm", None, None),
        DiffRow("b", S.EXTRA, "npm", None, None),
        DiffRow("m", S.MISSING, "npm", None, None),
        DiffRow("a", S.MISSING, "npm", None, None),
        DiffRow("q", S.UNKNOWN_KEY, None, None, None),
        DiffRow("d", S.DRIFT, "npm", "1", "2"),
        DiffRow("u", S.UNVERIFIABLE, "npm", "1", None),
        DiffRow("p", S.UNPINNABLE, "bash", "1", None),
    ]

    assert [(r.key, r.state.value) for r in sort_diff_rows(rows)] == [
        ("a", "missing"), ("m", "missing"), ("d", "drift"), ("u", "unverifiable"),
        ("p", "unpinnable"), ("q", "unknown-key"), ("b", "extra"), ("z", "ok"),
    ]


def test_compare_is_deterministic_whatever_order_the_inputs_arrive_in():
    p1 = Profile({"b": Entry(), "a": Entry("1")})
    p2 = Profile({"a": Entry("1"), "b": Entry()})
    machine = {"a": machine_tool(version="1"), "b": machine_tool(), "x": machine_tool()}

    shuffled = dict(reversed(machine.items()))

    assert sort_diff_rows(compare(p1, machine)) == sort_diff_rows(compare(p2, shuffled))


# -- exit status (FR-22, OQ-2) ---------------------------------------------------------------


def _row(state: DiffState) -> DiffRow:
    return DiffRow("k", state, "npm", None, None)


def test_a_clean_diff_does_not_differ():
    assert differs([_row(S.OK), _row(S.OK)], ignore_extras=False) is False
    assert differs([], ignore_extras=False) is False


@pytest.mark.parametrize("state", [S.MISSING, S.DRIFT, S.UNVERIFIABLE, S.UNPINNABLE, S.UNKNOWN_KEY])
def test_each_problem_state_is_a_difference_even_alongside_ok_rows(state):
    assert differs([_row(S.OK), _row(state)], ignore_extras=False) is True
    assert differs([_row(state)], ignore_extras=True) is True


def test_extras_count_unless_ignored():
    # Resolved OQ-2: "exactly what the profile says" is the question a gate asks.
    assert differs([_row(S.OK), _row(S.EXTRA)], ignore_extras=False) is True
    assert differs([_row(S.OK), _row(S.EXTRA)], ignore_extras=True) is False


def test_ignoring_extras_never_hides_a_real_difference():
    assert differs([_row(S.EXTRA), _row(S.DRIFT)], ignore_extras=True) is True


# -- JSON shape (FR-21) -----------------------------------------------------------------------


def test_row_json_has_exactly_the_six_documented_fields():
    row = DiffRow("lazygit", S.DRIFT, "uvx", "0.45.0", "0.44.1", "a note")

    assert row.to_json() == {
        "key": "lazygit", "state": "drift", "type": "uvx",
        "pinned": "0.45.0", "installed": "0.44.1", "note": "a note",
    }


def test_row_json_uses_null_for_absent_values_and_the_state_spelling_with_a_hyphen():
    row = one(Entry(), None)

    data = row.to_json()
    assert data["state"] == "unknown-key"
    assert data["type"] is None and data["pinned"] is None and data["installed"] is None
