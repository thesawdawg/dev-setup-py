"""Turning a diff into an install plan (docs/specs/profile-apply, FR-2, FR-3, FR-9, FR-22).

Pure: rows in, `Plan` out — no subprocess, no registry, no terminal. The one thing it needs from
the outside, which dependencies a tool is still missing, arrives as a function, so the ordering
rules can be tested exhaustively without installing anything.

The plan is derived from `profile.compare`'s rows rather than from a second comparison (SD-2): two
definitions of "missing" or "drift" would let `diff` say a machine matches while `apply` changes it.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from dev_setup.profile import DiffRow, DiffState, MachineTool


class Action(StrEnum):
    """Values are the `--json` spellings and the words the plan table shows."""

    INSTALL = "install"
    # One `update(version=…)` call, not install-then-update: measured to work on a tool that is
    # not installed, and it never installs a "latest" that was not asked for (spec F-3, SD-1).
    INSTALL_PINNED = "install pinned"
    UPDATE_TO_PIN = "update to pin"


class Outcome(StrEnum):
    """How an earlier step ended, for deciding whether a later one can run (FR-9)."""

    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class Step:
    key: str
    action: Action
    version: str | None       # the pin, for the two pinned actions
    installed: str | None     # what is installed now, for `update to pin`
    type: str
    # Asked for by the profile, as opposed to pulled in because something else needs it.
    requested: bool
    # The requested tools this one is here for. Empty when nothing else needs it.
    needed_by: tuple[str, ...]
    # Every not-yet-installed tool in this one's requires closure, deepest first. All of them are
    # planned earlier; a run uses this to skip the step when one of them failed (`blocked_by`).
    needs: tuple[str, ...]

    def to_json(self) -> dict[str, object]:
        return {
            "key": self.key,
            "action": self.action.value,
            "version": self.version,
            "installed": self.installed,
            "type": self.type,
            "requested": self.requested,
            "needed_by": list(self.needed_by),
            "needs": list(self.needs),
        }


@dataclass(frozen=True)
class Skip:
    """A profile entry `apply` will not act on, and why. Never silently dropped (FR-2)."""

    key: str
    state: DiffState
    reason: str

    def to_json(self) -> dict[str, str]:
        return {"key": self.key, "state": self.state.value, "reason": self.reason}


@dataclass(frozen=True)
class Plan:
    steps: tuple[Step, ...]
    skipped: tuple[Skip, ...]
    ok: tuple[str, ...]        # entries that already match — no work, but part of the picture

    @property
    def nothing_to_do(self) -> bool:
        return not self.steps

    def to_json(self) -> dict[str, object]:
        return {
            "steps": [s.to_json() for s in self.steps],
            "skipped": [s.to_json() for s in self.skipped],
            "ok": list(self.ok),
        }


def blocked_by(step: Step, bad: Mapping[str, Outcome]) -> str | None:
    """Why `step` must not run, given how earlier steps ended; None when nothing it needs went wrong.

    The first offender in plan order is named, so the reason points at the root of the chain.
    """
    for need in step.needs:
        outcome = bad.get(need)
        if outcome is Outcome.FAILED:
            return f"needs {need}, which failed"
        if outcome is Outcome.SKIPPED:
            return f"needs {need}, which was skipped"
    return None


def _skip_reason(row: DiffRow) -> str:
    return row.note or f"{row.state.value}: couldn't act on this entry"


def build_plan(
    rows: Sequence[DiffRow],
    machine: Mapping[str, MachineTool],
    closure: Callable[[str], Sequence[str]],
    *,
    missing_only: bool = False,
) -> Plan:
    """The steps to make the machine match, in an order where every tool follows what it needs.

    `closure(key)` is the tool's transitive requires that are *not installed*, deepest first —
    `registry.missing_requires` in production. It is asked only about tools about to be installed.
    `missing_only` leaves a drifted tool where it is.
    """
    ok: list[str] = []
    skipped: dict[str, Skip] = {}
    wanted: dict[str, tuple[Action, str | None, str | None, str]] = {}  # key -> step ingredients

    for row in rows:
        state = row.state
        if state is DiffState.OK:
            ok.append(row.key)
        elif state is DiffState.EXTRA:
            pass  # apply never removes (non-goal), and an extra is not a problem to report
        elif state is DiffState.MISSING:
            if row.pinned is None:
                wanted[row.key] = (Action.INSTALL, None, None, row.type or "")
            elif machine[row.key].pinnable:
                wanted[row.key] = (Action.INSTALL_PINNED, row.pinned, None, row.type or "")
            else:
                # The profile asked for a version this install type cannot give. Installing
                # "latest" instead is the silent substitution this command exists to avoid.
                skipped[row.key] = Skip(
                    row.key, state,
                    f"pinned to {row.pinned}, but a {row.type} install can't be pinned — "
                    "remove the version from the profile to install the latest",
                )
        elif state is DiffState.DRIFT:
            if missing_only:
                skipped[row.key] = Skip(
                    row.key, state,
                    f"installed at {row.installed}, profile pins {row.pinned} — "
                    "left alone (--missing-only)",
                )
            else:
                wanted[row.key] = (Action.UPDATE_TO_PIN, row.pinned, row.installed, row.type or "")
        else:  # unverifiable, unpinnable, unknown-key: the comparison could not be made
            skipped[row.key] = Skip(row.key, state, _skip_reason(row))

    # Dependencies. A tool that needs something this plan cannot provide is skipped, not tried.
    needs: dict[str, tuple[str, ...]] = {}
    for key in sorted(wanted):
        action = wanted[key][0]
        if action is Action.UPDATE_TO_PIN:
            continue  # already installed; its dependencies are not this command's business
        chain = tuple(closure(key))
        blocker = _blocker(chain, machine, skipped)
        if blocker is not None:
            del wanted[key]
            skipped[key] = Skip(key, DiffState.MISSING, blocker)
        else:
            needs[key] = chain

    # Emission order: each requested tool (by key, so the plan is deterministic) preceded by
    # whatever it needs that is not already out. Each closure is deepest-first, so a tool's own
    # dependencies are always out before it is — keeping first occurrences preserves that.
    order: list[str] = []
    for key in sorted(wanted):
        for dep in needs.get(key, ()):
            if dep not in order:
                order.append(dep)
        if key not in order:
            order.append(key)

    needed_by: dict[str, set[str]] = {}
    for key, chain in needs.items():
        for dep in chain:
            needed_by.setdefault(dep, set()).add(key)

    steps: list[Step] = []
    for key in order:
        if key in wanted:
            action, version, installed, type_ = wanted[key]
            requested = True
        else:  # pulled in only because something needs it: installed unpinned, as the catalog says
            action, version, installed, type_ = Action.INSTALL, None, None, machine[key].install_type
            requested = False
        steps.append(
            Step(
                key, action, version, installed, type_, requested,
                tuple(sorted(needed_by.get(key, ()))),
                needs.get(key, ()) if requested else tuple(closure(key)),
            )
        )

    return Plan(tuple(steps), tuple(skipped[k] for k in sorted(skipped)), tuple(sorted(ok)))


def _blocker(
    chain: Sequence[str], machine: Mapping[str, MachineTool], skipped: Mapping[str, Skip]
) -> str | None:
    for dep in chain:
        if dep not in machine:
            return f"needs {dep}, which is not in this machine's catalog"
        if dep in skipped:
            return f"needs {dep}, which was skipped: {skipped[dep].reason}"
    return None
