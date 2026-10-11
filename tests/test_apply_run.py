"""Running a plan (docs/specs/profile-apply, FR-8, FR-9, FR-11, FR-19; development-plan A3).

The executor decides *whether* and *in what order* steps run, and what is left behind when one
fails or the user presses Ctrl-C. Doing a step is `perform`'s job and is injected here, so every
path — including interruption — can be driven without installing anything.
"""

from __future__ import annotations

import pytest

from dev_setup.apply_exec import Result, RunResult, Status, StepResult, execute
from dev_setup.apply_plan import Action, Plan, Step


def s(key, needs=(), action=Action.INSTALL, version=None) -> Step:
    return Step(key, action, version, None, "npm", True, (), tuple(needs))


def plan_of(*steps: Step) -> Plan:
    return Plan(tuple(steps), (), ())


class Runner:
    """A fake `perform`: records every call, and fails or raises for the keys it is told to."""

    def __init__(self, *, fail=(), interrupt=(), versions=None):
        self.calls: list[str] = []
        self.fail, self.interrupt = set(fail), set(interrupt)
        self.versions = versions or {}

    def __call__(self, tool, step):
        self.calls.append(step.key)
        if step.key in self.interrupt:
            raise KeyboardInterrupt
        if step.key in self.fail:
            return Result(False, error=f"{step.key} blew up")
        return Result(True, self.versions.get(step.key))


def tools(*keys):
    table = {k: object() for k in keys}
    return table.get


def statuses(run: RunResult) -> dict[str, Status]:
    return {r.key: r.status for r in run.results}


# -- the happy path --------------------------------------------------------------------------------


def test_steps_run_once_each_in_plan_order():
    runner = Runner()

    run = execute(plan_of(s("a"), s("b"), s("c")), tools("a", "b", "c"), run=runner)

    assert runner.calls == ["a", "b", "c"]
    assert [r.key for r in run.results] == ["a", "b", "c"]
    assert run.ok and not run.interrupted


def test_a_successful_step_carries_what_the_tool_reported():
    run = execute(plan_of(s("a")), tools("a"), run=Runner(versions={"a": "1.2.3"}))

    assert run.results[0] == StepResult("a", Status.DONE, "1.2.3", "")


def test_an_empty_plan_is_an_ok_empty_run():
    run = execute(plan_of(), tools(), run=Runner())

    assert run.results == () and run.ok and not run.interrupted


def test_the_runner_is_handed_the_tool_and_the_step():
    seen = []
    table = {"a": object()}
    step = s("a", action=Action.INSTALL_PINNED, version="9")

    execute(plan_of(step), table.get, run=lambda tool, st: seen.append((tool, st)) or Result(True))

    assert seen == [(table["a"], step)]


# -- failure policy (FR-9) ------------------------------------------------------------------------


def test_a_failure_does_not_stop_the_run():
    runner = Runner(fail={"a"})

    run = execute(plan_of(s("a"), s("b"), s("c")), tools("a", "b", "c"), run=runner)

    assert runner.calls == ["a", "b", "c"]
    assert statuses(run) == {"a": Status.FAILED, "b": Status.DONE, "c": Status.DONE}
    assert not run.ok


def test_a_failure_records_the_installers_own_message():
    run = execute(plan_of(s("a")), tools("a"), run=Runner(fail={"a"}))

    assert run.results[0].detail == "a blew up"


def test_dependents_of_a_failure_are_skipped_without_being_attempted():
    runner = Runner(fail={"nvm"})

    run = execute(
        plan_of(s("nvm"), s("pkg", needs=["nvm"]), s("other")),
        tools("nvm", "pkg", "other"),
        run=runner,
    )

    assert runner.calls == ["nvm", "other"]  # `pkg` never reached the runner
    assert statuses(run) == {"nvm": Status.FAILED, "pkg": Status.SKIPPED, "other": Status.DONE}
    assert run.results[1].detail == "needs nvm, which failed"


def test_a_skip_propagates_to_what_needed_the_skipped_step():
    # c needs only b, and b was skipped because a failed: the chain is followed, not just the root.
    run = execute(
        plan_of(s("a"), s("b", needs=["a"]), s("c", needs=["b"])),
        tools("a", "b", "c"),
        run=Runner(fail={"a"}),
    )

    assert statuses(run) == {"a": Status.FAILED, "b": Status.SKIPPED, "c": Status.SKIPPED}
    assert run.results[2].detail == "needs b, which was skipped"


def test_the_root_cause_is_named_when_the_closure_lists_it():
    run = execute(
        plan_of(s("a"), s("b", needs=["a"]), s("c", needs=["a", "b"])),
        tools("a", "b", "c"),
        run=Runner(fail={"a"}),
    )

    assert run.results[2].detail == "needs a, which failed"


def test_a_step_whose_needs_all_succeeded_still_runs_after_an_unrelated_failure():
    runner = Runner(fail={"x"})

    run = execute(
        plan_of(s("base"), s("x"), s("dep", needs=["base"])),
        tools("base", "x", "dep"),
        run=runner,
    )

    assert statuses(run)["dep"] is Status.DONE and runner.calls == ["base", "x", "dep"]


def test_a_tool_missing_from_the_registry_is_a_failure_not_a_crash():
    runner = Runner()

    run = execute(
        plan_of(s("ghost"), s("after", needs=["ghost"])), tools("after"), run=runner
    )

    assert runner.calls == []  # nothing attempted: no tool to attempt it on
    assert statuses(run) == {"ghost": Status.FAILED, "after": Status.SKIPPED}
    assert "catalog" in run.results[0].detail


def test_ok_means_every_step_is_done():
    assert execute(plan_of(s("a")), tools("a"), run=Runner()).ok
    assert not execute(plan_of(s("a")), tools("a"), run=Runner(fail={"a"})).ok
    assert not execute(
        plan_of(s("a"), s("b", needs=["a"])), tools("a", "b"), run=Runner(fail={"a"})
    ).ok


@pytest.mark.parametrize(
    "status", [Status.FAILED, Status.SKIPPED, Status.INTERRUPTED, Status.NOT_RUN]
)
def test_any_step_that_did_not_finish_makes_the_run_not_ok(status):
    # Pinned on the result type itself, not only through `execute`: a skip normally comes with
    # a failure upstream, but `ok` must not depend on that being so.
    run = RunResult((StepResult("a", Status.DONE), StepResult("b", status)))

    assert not run.ok


def test_a_run_of_only_done_steps_is_ok():
    assert RunResult((StepResult("a", Status.DONE), StepResult("b", Status.DONE))).ok


# -- interruption (FR-19) -------------------------------------------------------------------------


def test_ctrl_c_mid_step_marks_that_step_and_leaves_the_rest_unrun():
    runner = Runner(interrupt={"b"})

    run = execute(plan_of(s("a"), s("b"), s("c"), s("d")), tools("a", "b", "c", "d"), run=runner)

    assert runner.calls == ["a", "b"]  # nothing after the interrupted step was attempted
    assert statuses(run) == {
        "a": Status.DONE, "b": Status.INTERRUPTED, "c": Status.NOT_RUN, "d": Status.NOT_RUN,
    }
    assert run.interrupted and not run.ok


def test_what_finished_before_the_interrupt_stays_reported_as_done():
    run = execute(plan_of(s("a"), s("b")), tools("a", "b"), run=Runner(interrupt={"b"}))

    assert run.results[0].status is Status.DONE


def test_ctrl_c_between_steps_leaves_no_interrupted_step():
    # Raised by the progress callback after `a` finished: `a` is done, nothing else started.
    def on_done(result):
        if result.key == "a":
            raise KeyboardInterrupt

    runner = Runner()

    run = execute(
        plan_of(s("a"), s("b")), tools("a", "b"), run=runner, on_done=on_done
    )

    assert runner.calls == ["a"]
    assert statuses(run) == {"a": Status.DONE, "b": Status.NOT_RUN}
    assert run.interrupted


def test_ctrl_c_after_the_last_step_still_reports_an_interrupted_run():
    # Everything finished, but the user did press Ctrl-C: say so rather than report a clean run.
    def on_done(result):
        raise KeyboardInterrupt

    run = execute(plan_of(s("a")), tools("a"), run=Runner(), on_done=on_done)

    assert statuses(run) == {"a": Status.DONE}
    assert run.interrupted and not run.ok


def test_ctrl_c_before_a_step_starts_marks_it_interrupted():
    def on_start(step):
        raise KeyboardInterrupt

    runner = Runner()

    run = execute(plan_of(s("a"), s("b")), tools("a", "b"), run=runner, on_start=on_start)

    assert runner.calls == []
    assert statuses(run) == {"a": Status.INTERRUPTED, "b": Status.NOT_RUN}


def test_an_interrupt_also_stops_a_run_whose_remaining_steps_were_to_be_skipped():
    run = execute(
        plan_of(s("a"), s("b", needs=["a"]), s("c")),
        tools("a", "b", "c"),
        run=Runner(fail={"a"}, interrupt={"c"}),
    )

    assert statuses(run) == {"a": Status.FAILED, "b": Status.SKIPPED, "c": Status.INTERRUPTED}
    assert run.interrupted


# -- progress hooks ---------------------------------------------------------------------------------


def test_start_precedes_run_and_done_follows_it_for_every_step():
    log: list[str] = []
    runner = Runner(fail={"b"})

    def run(tool, step):
        log.append(f"run:{step.key}")
        return runner(tool, step)

    execute(
        plan_of(s("a"), s("b")),
        tools("a", "b"),
        run=run,
        on_start=lambda st: log.append(f"start:{st.key}"),
        on_done=lambda r: log.append(f"done:{r.key}:{r.status.value}"),
    )

    assert log == ["start:a", "run:a", "done:a:done", "start:b", "run:b", "done:b:failed"]


def test_a_skipped_step_gets_done_but_no_start():
    log: list[str] = []

    execute(
        plan_of(s("a"), s("b", needs=["a"])),
        tools("a", "b"),
        run=Runner(fail={"a"}),
        on_start=lambda st: log.append(f"start:{st.key}"),
        on_done=lambda r: log.append(f"done:{r.key}:{r.status.value}"),
    )

    assert log == ["start:a", "done:a:failed", "done:b:skipped"]


# -- shape --------------------------------------------------------------------------------------------


def test_run_json_lists_every_step_and_whether_it_was_interrupted():
    run = execute(plan_of(s("a"), s("b")), tools("a", "b"), run=Runner(fail={"b"}))

    assert run.to_json() == {
        "interrupted": False,
        "steps": [
            {"key": "a", "status": "done", "version": None, "detail": ""},
            {"key": "b", "status": "failed", "version": None, "detail": "b blew up"},
        ],
    }


def test_default_runner_is_perform(monkeypatch):
    # Wiring, not behaviour: with no `run=`, the step is actually done through `perform`.
    from types import SimpleNamespace

    calls = []
    tool = SimpleNamespace(
        install=lambda: calls.append("install") or "1.0",
        update=lambda version=None: calls.append(("update", version)) or version,
    )

    run = execute(
        plan_of(s("a"), s("b", action=Action.INSTALL_PINNED, version="2.0")),
        {"a": tool, "b": tool}.get,
    )

    assert calls == ["install", ("update", "2.0")]
    assert [r.version for r in run.results] == ["1.0", "2.0"]


@pytest.mark.parametrize("n", [1, 10, 40])
def test_a_long_plan_with_scattered_failures_accounts_for_every_step(n):
    keys = [f"t{i:02}" for i in range(n)]
    fail = {k for i, k in enumerate(keys) if i % 4 == 1}
    steps = [s(k, needs=[keys[i - 1]] if i % 5 == 4 else []) for i, k in enumerate(keys)]

    run = execute(plan_of(*steps), tools(*keys), run=Runner(fail=fail))

    assert [r.key for r in run.results] == keys  # exactly once each, in order
    for r in run.results:
        assert r.status in (Status.DONE, Status.FAILED, Status.SKIPPED)
