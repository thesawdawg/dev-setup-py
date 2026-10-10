"""The apply planner (docs/specs/profile-apply, FR-2, FR-3, FR-9, FR-22; development-plan A1).

Pure: it turns `diff` rows into steps, so none of this needs a registry, a subprocess or a
terminal. Rows come from the real `compare()` wherever the grid is concerned — a planner tested
only on hand-built rows could agree with rows `diff` never produces.
"""

from __future__ import annotations

import itertools

import pytest

from dev_setup import registry
from dev_setup.apply_plan import Action, Outcome, Plan, Step, blocked_by, build_plan
from dev_setup.generic import GenericTool
from dev_setup.profile import DiffState, Entry, MachineTool, Profile, compare

S = DiffState


def tool(install_type="npm", *, installed=False, pinnable=True, version="") -> MachineTool:
    return MachineTool(install_type, installed, pinnable, version)


def plan_for(entries, machine, closure=None, *, missing_only=False) -> Plan:
    """Plan from a profile + machine, through the real classifier."""
    rows = compare(Profile({k: v for k, v in entries.items()}), machine)
    return build_plan(rows, machine, closure or (lambda key: []), missing_only=missing_only)


def keys(plan: Plan) -> list[str]:
    return [s.key for s in plan.steps]


# -- the decision table (FR-2) ------------------------------------------------------------------


def _grid():
    # Every input the classifier takes, including combinations a real snapshot never produces.
    yield from itertools.product(
        [None, "1.0"], [False, True], [False, True], ["", "1.0", "2.0"], [False, True]
    )


@pytest.mark.parametrize("missing_only", [False, True])
def test_every_classifier_outcome_maps_to_exactly_one_action(missing_only):
    for pinned, installed, pinnable, version, known in _grid():
        entry = Entry(pinned)
        machine = {"t": tool(installed=installed, pinnable=pinnable, version=version)} if known else {}
        rows = compare(Profile({"t": entry}), machine)
        row = next(r for r in rows if r.key == "t")
        plan = build_plan(rows, machine, lambda key: [], missing_only=missing_only)
        where = (pinned, installed, pinnable, version, known, row.state)

        step = [s for s in plan.steps if s.key == "t"]
        skip = [s for s in plan.skipped if s.key == "t"]
        assert len(step) + len(skip) <= 1, where  # never both

        if row.state in (S.OK, S.EXTRA):
            assert not step and not skip, where
        elif row.state in (S.UNVERIFIABLE, S.UNPINNABLE, S.UNKNOWN_KEY):
            # FR-2: "could not compare" is never acted on, and never silently dropped.
            assert not step and len(skip) == 1 and skip[0].state is row.state, where
            assert skip[0].reason, where
        elif row.state is S.DRIFT:
            if missing_only:
                assert not step and len(skip) == 1 and skip[0].state is S.DRIFT, where
            else:
                assert step[0].action is Action.UPDATE_TO_PIN and step[0].version == pinned, where
        else:
            assert row.state is S.MISSING, where
            if pinned is None:
                assert step[0].action is Action.INSTALL and step[0].version is None, where
            elif pinnable:
                assert step[0].action is Action.INSTALL_PINNED and step[0].version == pinned, where
            else:
                # Never "install latest" in place of the version that was asked for.
                assert not step and len(skip) == 1 and skip[0].state is S.MISSING, where
                assert "can't be pinned" in skip[0].reason, where


def test_ok_rows_are_counted_not_listed_as_work():
    machine = {"a": tool(installed=True), "b": tool(installed=False)}

    plan = plan_for({"a": Entry(), "b": Entry()}, machine)

    assert plan.ok == ("a",)
    assert keys(plan) == ["b"]


def test_extras_are_never_touched():
    machine = {"mine": tool(installed=True), "wanted": tool(installed=False)}

    plan = plan_for({"wanted": Entry()}, machine)

    assert keys(plan) == ["wanted"]
    assert plan.skipped == () and plan.ok == ()


def test_a_pinned_drift_names_both_versions_in_the_step():
    machine = {"t": tool(installed=True, version="2.0")}

    (step,) = plan_for({"t": Entry("1.0")}, machine).steps

    assert (step.action, step.version, step.installed) == (Action.UPDATE_TO_PIN, "1.0", "2.0")


def test_missing_only_still_installs_what_is_missing():
    machine = {"drifted": tool(installed=True, version="2.0"), "gone": tool(installed=False)}

    plan = plan_for({"drifted": Entry("1.0"), "gone": Entry()}, machine, missing_only=True)

    assert keys(plan) == ["gone"]
    (skip,) = plan.skipped
    assert skip.key == "drifted" and "--missing-only" in skip.reason
    assert "2.0" in skip.reason and "1.0" in skip.reason


def test_the_unpinnable_missing_reason_says_how_to_get_the_tool_anyway():
    machine = {"s": tool("bash", installed=False, pinnable=False)}

    (skip,) = plan_for({"s": Entry("1.0")}, machine).skipped

    assert "bash" in skip.reason and "1.0" in skip.reason and "remove the version" in skip.reason


def test_non_actionable_skips_keep_diffs_own_reason():
    machine = {"s": tool("script", installed=True, pinnable=False)}

    rows = compare(Profile({"s": Entry("1.0"), "ghost": Entry()}), machine)
    plan = build_plan(rows, machine, lambda key: [])

    by_key = {s.key: s for s in plan.skipped}
    assert by_key["s"].state is S.UNPINNABLE and by_key["ghost"].state is S.UNKNOWN_KEY
    for row in rows:
        assert by_key[row.key].reason == row.note  # the classifier's wording, not a rewording


# -- dependencies (FR-3) -------------------------------------------------------------------------


def deps(**graph):
    """A `closure` over a fixed graph where nothing is installed: deepest-first, like the real one."""

    def closure(key):
        out: list[str] = []

        def visit(k):
            for d in graph.get(k, []):
                if d not in out:
                    visit(d)
                    if d not in out:
                        out.append(d)

        visit(key)
        return out

    return closure


def test_an_unnamed_dependency_is_planned_first_and_labelled():
    machine = {"claude-code": tool(), "nvm": tool("bash")}

    plan = plan_for({"claude-code": Entry()}, machine, deps(**{"claude-code": ["nvm"]}))

    assert keys(plan) == ["nvm", "claude-code"]
    nvm, cc = plan.steps
    assert (nvm.requested, nvm.needed_by) == (False, ("claude-code",))
    assert (cc.requested, cc.needed_by) == (True, ())
    assert cc.needs == ("nvm",)


def test_a_shared_dependency_appears_once_before_both_users():
    machine = {"a": tool("uvx"), "b": tool("uvx"), "uv": tool("bash")}

    plan = plan_for(
        {"a": Entry(), "b": Entry()}, machine, deps(a=["uv"], b=["uv"])
    )

    assert keys(plan) == ["uv", "a", "b"]
    assert plan.steps[0].needed_by == ("a", "b")


def test_a_chain_comes_out_deepest_first():
    machine = {k: tool("bash") for k in ("top", "mid", "base")}

    plan = plan_for({"top": Entry()}, machine, deps(top=["mid"], mid=["base"]))

    assert keys(plan) == ["base", "mid", "top"]
    assert plan.steps[2].needs == ("base", "mid")
    assert plan.steps[1].needs == ("base",)


def test_a_dependency_the_profile_also_names_keeps_its_own_pin_and_is_not_duplicated():
    machine = {"pkg": tool("npm"), "nvm": tool("npm")}

    plan = plan_for(
        {"pkg": Entry(), "nvm": Entry("0.40.0")}, machine, deps(pkg=["nvm"])
    )

    assert keys(plan) == ["nvm", "pkg"]  # nvm sorts after pkg by key but must run first
    nvm = plan.steps[0]
    assert nvm.action is Action.INSTALL_PINNED and nvm.version == "0.40.0"
    assert nvm.requested and nvm.needed_by == ("pkg",)


def test_a_dependency_that_is_already_installed_adds_no_step():
    machine = {"pkg": tool(), "nvm": tool("bash", installed=True)}

    # `closure` is what knows about install state (registry.missing_requires): installed = absent.
    plan = plan_for({"pkg": Entry()}, machine, lambda key: [])

    assert keys(plan) == ["pkg"] and plan.steps[0].needs == ()


def test_updating_a_drifted_tool_does_not_consult_its_dependencies():
    machine = {"t": tool(installed=True, version="2.0")}
    asked: list[str] = []

    plan_for({"t": Entry("1.0")}, machine, lambda key: asked.append(key) or ["x"])

    assert asked == []


def test_only_install_steps_consult_the_closure():
    machine = {"new": tool(), "old": tool(installed=True), "bad": tool("bash", pinnable=False)}
    asked: list[str] = []

    plan_for(
        {"new": Entry(), "old": Entry(), "bad": Entry("1.0")},
        machine,
        lambda key: asked.append(key) or [],
    )

    assert asked == ["new"]


def test_a_dependency_missing_from_the_catalog_skips_its_dependent():
    machine = {"pkg": tool()}  # "ghost" is not a catalog key

    plan = plan_for({"pkg": Entry()}, machine, deps(pkg=["ghost"]))

    assert plan.steps == ()
    (skip,) = plan.skipped
    assert skip.key == "pkg" and "ghost" in skip.reason and "catalog" in skip.reason


def test_a_dependency_that_is_itself_skipped_skips_its_dependents():
    machine = {"pkg": tool(), "nvm": tool("bash", pinnable=False)}

    plan = plan_for(
        {"pkg": Entry(), "nvm": Entry("0.40.0")}, machine, deps(pkg=["nvm"])
    )

    assert plan.steps == ()
    by_key = {s.key: s for s in plan.skipped}
    assert set(by_key) == {"nvm", "pkg"}
    assert "nvm" in by_key["pkg"].reason and "skipped" in by_key["pkg"].reason
    # The reason it was skipped is carried along, so the dependent's reason is not a dead end.
    assert "can't be pinned" in by_key["pkg"].reason


def test_a_skipped_dependent_does_not_drag_its_dependency_into_the_plan():
    # `nvm` would only have been installed for `pkg`; with `pkg` skipped, nothing needs it.
    machine = {"pkg": tool(), "nvm": tool("bash"), "dead": tool("bash", pinnable=False)}

    plan = plan_for(
        {"pkg": Entry()} | {"dead": Entry("1.0")},
        machine,
        deps(pkg=["nvm", "dead"]),
    )

    assert keys(plan) == []


def test_the_plan_does_not_depend_on_row_order():
    machine = {k: tool("uvx") for k in "abcd"} | {"uv": tool("bash")}
    graph = deps(a=["uv"], c=["uv"])
    entries = {k: Entry() for k in "abcd"}

    forward = compare(Profile(entries), machine)
    backward = list(reversed(forward))

    assert build_plan(forward, machine, graph) == build_plan(backward, machine, graph)
    assert keys(build_plan(forward, machine, graph)) == ["uv", "a", "b", "c", "d"]


# -- the real catalog ---------------------------------------------------------------------------


def test_every_step_follows_everything_it_needs_across_the_bundled_catalog(monkeypatch):
    # With nothing installed, ask for every tool at once: the worst case for ordering.
    monkeypatch.setattr(GenericTool, "is_installed", lambda self: False)
    all_tools = registry.all_tools()
    machine = {t.key: MachineTool(t.install_type, False, False) for t in all_tools}
    rows = compare(Profile({t.key: Entry() for t in all_tools}), machine)

    plan = build_plan(rows, machine, lambda key: registry.missing_requires(registry.get(key)))

    assert sorted(keys(plan)) == sorted(machine)  # everything once, nothing twice
    position = {s.key: i for i, s in enumerate(plan.steps)}
    for step in plan.steps:
        for need in step.needs:
            assert position[need] < position[step.key], (need, step.key)


@pytest.mark.parametrize("key", [t.key for t in registry.all_tools() if t.requires])
def test_asking_for_one_dependent_tool_pulls_in_exactly_its_closure(key, monkeypatch):
    monkeypatch.setattr(GenericTool, "is_installed", lambda self: False)
    machine = {t.key: MachineTool(t.install_type, False, False) for t in registry.all_tools()}
    rows = compare(Profile({key: Entry()}), machine)

    plan = build_plan(rows, machine, lambda k: registry.missing_requires(registry.get(k)))

    closure = registry.missing_requires(registry.get(key))
    assert keys(plan) == [*closure, key]
    assert [s.requested for s in plan.steps] == [False] * len(closure) + [True]


# -- failure propagation (FR-9) -----------------------------------------------------------------


def step(key="t", needs=()):
    return Step(key, Action.INSTALL, None, None, "npm", True, (), tuple(needs))


def test_a_step_is_blocked_by_a_failed_need():
    assert blocked_by(step(needs=["nvm"]), {"nvm": Outcome.FAILED}) == "needs nvm, which failed"


def test_a_step_is_blocked_by_a_skipped_need():
    reason = blocked_by(step(needs=["nvm"]), {"nvm": Outcome.SKIPPED})
    assert reason == "needs nvm, which was skipped"


def test_the_first_bad_need_in_plan_order_is_the_one_named():
    bad = {"base": Outcome.SKIPPED, "mid": Outcome.FAILED}

    assert blocked_by(step(needs=["base", "mid"]), bad) == "needs base, which was skipped"


def test_a_step_is_not_blocked_by_things_it_does_not_need():
    assert blocked_by(step(needs=["nvm"]), {"other": Outcome.FAILED}) is None
    assert blocked_by(step(), {"nvm": Outcome.FAILED}) is None
    assert blocked_by(step(needs=["nvm"]), {}) is None


def test_a_sibling_of_a_failure_is_not_blocked():
    bad = {"a": Outcome.FAILED}

    assert blocked_by(step("b"), bad) is None


# -- shape ----------------------------------------------------------------------------------------


def test_an_empty_plan_has_nothing_to_do():
    machine = {"a": tool(installed=True)}

    plan = plan_for({"a": Entry()}, machine)

    assert plan.nothing_to_do and plan.steps == () and plan.ok == ("a",)


def test_a_plan_with_only_skips_still_has_nothing_to_do():
    plan = plan_for({"ghost": Entry()}, {})

    assert plan.nothing_to_do and len(plan.skipped) == 1


def test_plan_json_has_the_shape_the_command_will_print():
    machine = {"pkg": tool("npm"), "nvm": tool("bash"), "x": tool("bash", pinnable=False)}

    plan = plan_for(
        {"pkg": Entry("1.2"), "x": Entry("9")}, machine, deps(pkg=["nvm"])
    )

    doc = plan.to_json()
    assert set(doc) == {"steps", "skipped", "ok"}
    assert doc["steps"][0] == {
        "key": "nvm", "action": "install", "version": None, "installed": None,
        "type": "bash", "requested": False, "needed_by": ["pkg"], "needs": [],
    }
    assert doc["steps"][1]["action"] == "install pinned" and doc["steps"][1]["version"] == "1.2"
    assert doc["skipped"][0]["key"] == "x" and doc["skipped"][0]["state"] == "missing"
