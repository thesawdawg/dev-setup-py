"""The profile loop for `devstuff tui` — model-free review and apply.

A profile (`@bundle` name or file path) is opened on an explicit pick only —
the workspace never reads one on its own. The review runs the same planner
(`profile_cmd.review_profile`) and the run the same executor
(`profile_cmd.execute_review`) as `devstuff profile apply`, behind the same
default-No confirmation; the only difference is that a person drives the
menu. The current profile lives in memory for the workspace session — no
config or state is written, and nothing re-runs without a fresh pick.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import questionary
from rich.text import Text

from dev_setup import bundles, generic, profile, runner, ui
from dev_setup.catalog import CatalogError
from dev_setup.commands import profile_cmd

_CANCELLED = object()

# Friendly one-liners for the shipped bundles; an unknown name shows its own.
_LABELS = {
    "essentials": "Everyday developer tools",
    "python": "Python development",
    "devops": "Containers and infrastructure",
    "languages": "Language runtimes",
    "ai-agents": "AI coding tools",
    "ai-local": "Local model tools",
}

_BACK = questionary.Choice(title="Back", value=ui.BACK)


def _one_line(value: str) -> str:
    """Collapse a reference to a safe single line for a menu row."""
    return " ".join(
        "".join(char if char.isprintable() else " " for char in value).split()
    )


def _prompt(fn: Callable, *args: Any, **kwargs: Any) -> Any:
    """One profile prompt — Ctrl+C/EOF on a menu or field means 'back'; the
    same keys inside an apply run stay interrupts and reach the executor."""
    try:
        return fn(*args, **kwargs)
    except (KeyboardInterrupt, EOFError):
        return _CANCELLED


def choose_starter() -> str | None:
    """Pick one of the shipped `@bundle` profiles; None on Back/cancel."""
    picked = _prompt(
        ui.select,
        "Starter profile",
        [
            questionary.Choice(
                title=f"@{name} — {_LABELS.get(name, name)}",
                value=f"@{name}",
            )
            for name in bundles.names()
        ]
        + [_BACK],
    )
    if picked is _CANCELLED or picked is ui.BACK or not picked:
        return None
    return picked


def open_profile() -> str | None:
    """A profile file path (or `@bundle`) typed by hand; None on blank/cancel."""
    ref = _prompt(ui.text_input, "Profile file path (or @bundle, e.g. @devops)")
    if ref is _CANCELLED or not ref:
        return None
    if not bundles.is_bundle(ref):
        ref = str(Path(ref).expanduser())
    return ref


def _review_once(reference: str) -> profile_cmd.ProfileReview | None:
    """Re-read the profile and build a fresh machine-vs-profile review.
    Probe memos are forgotten first — anything installed since the last
    review would otherwise read stale."""
    try:
        wanted = bundles.load_arg(reference)
        generic.forget_probes()
        return profile_cmd.review_profile(wanted)
    except (profile.ProfileError, CatalogError, OSError) as exc:
        ui.console.print(Text(str(exc)))
        return None


def show_profile(reference: str) -> bool:
    """Open `reference`, review it against this machine, offer the apply.

    Returns True while the reference should stay the workspace's current
    profile — a read/parse failure returns False so the caller can decide
    what to do with the established reference (the workspace keeps it: the
    user may fix the file and try again).
    """
    review = _review_once(reference)
    if review is None:
        return False
    while True:
        ui.section("Profile", _one_line(reference))
        profile_cmd.show_diff(review.rows, show_all=False)

        options: list[questionary.Choice] = []
        if review.plan.steps:
            options.append(questionary.Choice("Review changes", value="review"))
        elif review.plan.skipped:
            options.append(
                questionary.Choice("Show plan details", value="review")
            )
        options += [
            questionary.Choice("Refresh comparison", value="refresh"),
            questionary.Choice("Back to workspace", value="back"),
        ]
        picked = _prompt(ui.select, "Profile", options, default="back")
        if picked is _CANCELLED or picked in (ui.BACK, "back") or not picked:
            return True
        if picked == "refresh":
            review = _review_once(reference)
            if review is None:
                return False
            continue
        if _review_changes(reference, review):
            return True


def _review_changes(reference: str, review: profile_cmd.ProfileReview) -> bool:
    """Show the pinned plan, confirm default-No, then run it exactly once.

    Declining (or cancelling) keeps the same review for another look — the
    plan is rebuilt only on Refresh. A completed run ends the profile
    session back at the workspace: rechecking means reopening or refreshing,
    never an implicit retry.
    """
    profile_cmd.show_plan(review.plan, review.by_key, reference)
    if not review.plan.steps:
        return False  # 'Show plan details' — display only, back to the menu
    ui.console.print(
        Text(
            "This applies to this machine only. Changes can need network or "
            "elevated privileges. Nothing is removed. Extra tools are left "
            "alone."
        )
    )
    n = len(review.plan.steps)
    if not _prompt(
        ui.confirm,
        f"Apply {n} step{'s' if n != 1 else ''} to this machine?",
        default=False,
    ):
        ui.dim("Nothing changed — the same plan stays until you refresh.")
        return False
    run, final = profile_cmd.execute_review(review)
    profile_cmd.show_result(review.wanted, run, final, reference)
    ui.console.print(
        Text(f"Command logs, when available: {runner.runs_dir()}")
    )
    ui.dim("Recheck by refreshing or reopening the profile — nothing")
    ui.dim("re-runs on its own.")
    return True
