"""Carrying out an apply plan (docs/specs/profile-apply, FR-6–11, FR-19, NFR-5).

`perform` does one step; `execute` runs a whole plan under the failure policy. Together they are
the non-interactive core. `devstuff install` wraps `tool.install()` in a section header, an
"already installed" short-circuit and, at the end, an offer to run the tool's setup wizard; none
of that belongs in an unattended run, so this does only the work and reports how it went.

It deliberately does not share code with `install_cmd._install_one`: what they would share is one
`try`/`except`, and `install_cmd` is a file with no parity tests to refactor against.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from dev_setup import generic
from dev_setup.apply_plan import Action, Outcome, Plan, Step, blocked_by
from dev_setup.base import Tool


@dataclass(frozen=True)
class Result:
    """How one step ended. `ok` means the installer returned — not that the machine now matches.

    Whether it matches is decided afterwards, by re-measuring (spec SD-3); an installer that
    exits 0 having installed nothing is a known hazard of this catalog.
    """

    ok: bool
    version: str | None = None  # what the tool reports afterwards, when it reports anything
    error: str = ""


def perform(tool: Tool, step: Step) -> Result:
    """Do what `step` says to `tool`. Never raises for a failed install; always forgets probes.

    `KeyboardInterrupt` is not caught: an interrupted run must stop, not record a failed step
    and carry on (FR-19). Probes are forgotten on every path out, because an installer that
    failed or was interrupted may still have changed the machine.
    """
    try:
        if step.action is Action.INSTALL:
            return Result(True, tool.install())
        if not step.version:
            return Result(False, error=f"a '{step.action}' step has no version to pin to")
        # One call, not install-then-update: `update(version=…)` is the installer's own command
        # with a version in it, and works on a tool that is not installed (spec F-3, SD-1).
        return Result(True, tool.update(version=step.version))  # type: ignore[attr-defined]
    except Exception as exc:
        return Result(False, error=str(exc) or type(exc).__name__)
    finally:
        generic.forget_probes()


class Status(StrEnum):
    """How one planned step ended. Values are the `--json` spellings."""

    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"          # not attempted: something it needs failed or was skipped
    INTERRUPTED = "interrupted"  # Ctrl-C arrived while it was the current step
    NOT_RUN = "not run"          # never reached, because the run was interrupted first


@dataclass(frozen=True)
class StepResult:
    key: str
    status: Status
    version: str | None = None
    detail: str = ""             # the installer's error, or why the step was skipped

    def to_json(self) -> dict[str, str | None]:
        return {
            "key": self.key,
            "status": self.status.value,
            "version": self.version,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class RunResult:
    results: tuple[StepResult, ...]
    interrupted: bool = False

    @property
    def ok(self) -> bool:
        """Every planned step ran and returned. Not "the machine matches": that is re-measured."""
        return not self.interrupted and all(r.status is Status.DONE for r in self.results)

    def to_json(self) -> dict[str, object]:
        return {"interrupted": self.interrupted, "steps": [r.to_json() for r in self.results]}


def execute(
    plan: Plan,
    lookup: Callable[[str], Tool | None],
    *,
    run: Callable[[Tool, Step], Result] = perform,
    on_start: Callable[[Step], None] | None = None,
    on_done: Callable[[StepResult], None] | None = None,
) -> RunResult:
    """Run the plan's steps serially, in order, and say how each one ended.

    **A failure never stops the run** (FR-9, SD-4). Anything whose requires include a failed or
    skipped step is skipped with the reason, without being attempted; everything else carries on.

    **Ctrl-C ends the run cleanly** (FR-19): the step that was current is `interrupted`, the rest
    are `not run`, and what finished stays `done`. It returns normally so the caller can still
    print the summary — the exit status, not an exception, is what says it did not complete.

    `lookup` maps a key to its tool (`registry.get`); `run` is `perform`, injectable so every path
    here can be exercised without installing anything. `on_start`/`on_done` are for progress
    output; `on_start` is not called for a step that is skipped without being attempted.
    """
    results: list[StepResult] = []
    bad: dict[str, Outcome] = {}
    current: Step | None = None

    def record(result: StepResult) -> None:
        results.append(result)
        if result.status is Status.FAILED:
            bad[result.key] = Outcome.FAILED
        elif result.status is Status.SKIPPED:
            bad[result.key] = Outcome.SKIPPED
        if on_done:
            on_done(result)

    try:
        for step in plan.steps:
            reason = blocked_by(step, bad)
            if reason:
                record(StepResult(step.key, Status.SKIPPED, None, reason))
                continue
            current = step
            if on_start:
                on_start(step)
            tool = lookup(step.key)
            if tool is None:
                outcome = Result(False, error="not in this machine's catalog")
            else:
                outcome = run(tool, step)
            current = None  # the step is over: an interrupt from here on is "between steps"
            if outcome.ok:
                record(StepResult(step.key, Status.DONE, outcome.version))
            else:
                record(StepResult(step.key, Status.FAILED, None, outcome.error))
    except KeyboardInterrupt:
        if current is not None:
            results.append(StepResult(current.key, Status.INTERRUPTED))
        seen = {r.key for r in results}
        results += [StepResult(st.key, Status.NOT_RUN) for st in plan.steps if st.key not in seen]
        return RunResult(tuple(results), interrupted=True)
    return RunResult(tuple(results))
