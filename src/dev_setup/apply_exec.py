"""Carrying out one apply step (docs/specs/profile-apply, FR-6, FR-7, FR-10, NFR-5).

The non-interactive core. `devstuff install` wraps `tool.install()` in a section header, an
"already installed" short-circuit and, at the end, an offer to run the tool's setup wizard; none
of that belongs in an unattended run, so this does only the work and reports how it went.

It deliberately does not share code with `install_cmd._install_one`: what they would share is one
`try`/`except`, and `install_cmd` is a file with no parity tests to refactor against.
"""

from __future__ import annotations

from dataclasses import dataclass

from dev_setup import generic
from dev_setup.apply_plan import Action, Step
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
