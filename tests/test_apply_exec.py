"""Running one apply step (docs/specs/profile-apply, FR-6, FR-7, FR-10, NFR-5; development-plan A2).

`perform` is the non-interactive core: it does the one thing a step asks, reports how it went, and
leaves the machine's cached probes forgotten. What it must *not* do — offer a wizard, print a
section, short-circuit on "already installed" — is as much the contract as what it does.
"""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from dev_setup import generic
from dev_setup.apply_exec import Result, perform
from dev_setup.apply_plan import Action, Step
from dev_setup.generic import GenericTool, forget_probes


def step(action: Action, version: str | None = None) -> Step:
    return Step("t", action, version, None, "npm", True, (), ())


class Spy:
    """A tool that records which verb it was asked to do."""

    def __init__(self, *, returns: str | None = "1.0", raises: BaseException | None = None):
        self.calls: list[tuple] = []
        self.returns, self.raises = returns, raises

    def install(self):
        self.calls.append(("install",))
        if self.raises:
            raise self.raises
        return self.returns

    def update(self, version=None):
        self.calls.append(("update", version))
        if self.raises:
            raise self.raises
        return self.returns


# -- which verb (FR-6, FR-7) ---------------------------------------------------------------------


def test_an_unpinned_install_calls_install_and_nothing_else():
    tool = Spy()

    perform(tool, step(Action.INSTALL))

    assert tool.calls == [("install",)]


@pytest.mark.parametrize("action", [Action.INSTALL_PINNED, Action.UPDATE_TO_PIN])
def test_a_pinned_step_is_exactly_one_update_with_the_version(action):
    # Not install-then-update (SD-1): one call, and `install()` is never reached.
    tool = Spy()

    perform(tool, step(action, "1.2.3"))

    assert tool.calls == [("update", "1.2.3")]


@pytest.mark.parametrize("action", [Action.INSTALL_PINNED, Action.UPDATE_TO_PIN])
def test_a_pinned_step_without_a_version_is_refused_not_run_as_latest(action):
    # The planner never builds one; if it ever does, "update to latest" is the wrong silent answer.
    tool = Spy()

    result = perform(tool, step(action, None))

    assert tool.calls == [] and not result.ok and "version" in result.error


def test_there_is_no_already_installed_short_circuit():
    # `devstuff install` returns early on is_installed(); apply's plan already decided, and a
    # tool that is "installed" but wrong is exactly what UPDATE_TO_PIN is for.
    tool = Spy()
    tool.is_installed = lambda: pytest.fail("perform must not ask is_installed()")

    perform(tool, step(Action.INSTALL))


# -- the result ------------------------------------------------------------------------------------


def test_success_carries_the_version_the_tool_reports():
    assert perform(Spy(returns="4.5"), step(Action.INSTALL)) == Result(True, "4.5", "")


def test_success_with_nothing_to_report_is_still_success():
    assert perform(Spy(returns=None), step(Action.INSTALL)) == Result(True, None, "")


@pytest.mark.parametrize(
    "exc, expected",
    [
        (RuntimeError("npm exploded"), "npm exploded"),
        (subprocess.CalledProcessError(7, ["apt-get"]), "apt-get"),
        (OSError("no such file"), "no such file"),
        (ValueError(), "ValueError"),  # an exception with no message still says something
    ],
)
def test_a_failure_is_a_result_not_an_exception(exc, expected):
    result = perform(Spy(raises=exc), step(Action.INSTALL))

    assert result.ok is False and result.version is None
    assert expected in result.error


def test_ctrl_c_is_not_swallowed():
    # FR-19: an interrupted run must stop, not log a "failed" step and carry on.
    with pytest.raises(KeyboardInterrupt):
        perform(Spy(raises=KeyboardInterrupt()), step(Action.INSTALL))


# -- unattended (FR-10) --------------------------------------------------------------------------


def test_it_never_offers_a_setup_wizard(monkeypatch):
    # `devstuff install` ends with a prompt; an unattended run cannot answer one.
    from dev_setup.commands import configure_cmd

    monkeypatch.setattr(
        configure_cmd, "offer_after_install", lambda key: pytest.fail("wizard offered")
    )

    perform(Spy(), step(Action.INSTALL))
    perform(Spy(), step(Action.INSTALL_PINNED, "1.0"))


# -- forgetting cached probes (NFR-5, spec F-4) ----------------------------------------------------


def test_forget_probes_clears_every_memoised_probe():
    calls = []

    @generic._once
    def probe():
        calls.append(1)
        return len(calls)

    assert probe() == 1 and probe() == 1

    forget_probes()

    assert probe() == 2


@pytest.mark.parametrize("name", ["_uv_tool_versions", "_uv_outdated_map"])
def test_forget_probes_covers_the_real_uv_probes(name, monkeypatch):
    runs = []

    def fake_probe(cmd, **_kwargs):
        runs.append(cmd)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(generic, "_probe", fake_probe)
    monkeypatch.setattr(generic.shutil, "which", lambda n: "/usr/bin/uv" if n == "uv" else None)
    monkeypatch.delenv("UV_OFFLINE", raising=False)
    forget_probes()
    probe = getattr(generic, name)

    probe()
    probe()
    assert len(runs) == 1  # memoised

    forget_probes()
    probe()
    assert len(runs) == 2  # and forgotten
    forget_probes()


@pytest.mark.parametrize("fails", [False, True])
def test_probes_are_forgotten_after_a_step_whether_it_worked_or_not(fails):
    # A failed installer may still have changed the machine (half an install), so the next
    # read has to be a fresh one either way.
    calls = []

    @generic._once
    def probe():
        calls.append(1)
        return len(calls)

    probe()
    tool = Spy(raises=RuntimeError("boom") if fails else None)

    perform(tool, step(Action.INSTALL))

    assert probe() == 2


def test_probes_are_forgotten_even_when_the_step_is_interrupted():
    calls = []

    @generic._once
    def probe():
        calls.append(1)
        return len(calls)

    probe()

    with pytest.raises(KeyboardInterrupt):
        perform(Spy(raises=KeyboardInterrupt()), step(Action.INSTALL))

    assert probe() == 2


def test_a_uv_tool_installed_by_a_step_is_visible_to_the_next_read(monkeypatch):
    """The bug that motivated all of this (spec F-4), through the real uv probe.

    Measured: `installed_version()` was '' before an install, '' again *after* it succeeded, and
    right only once the memo was cleared. A verify-after-install that forgot would call a correct
    pin unverifiable.
    """
    state = {"installed": False}

    def fake_probe(cmd, **_kwargs):
        out = "pycowsay v0.0.0.1\n- pycowsay\n" if state["installed"] else ""
        return SimpleNamespace(returncode=0, stdout=out, stderr="")

    monkeypatch.setattr(generic, "_probe", fake_probe)
    monkeypatch.setattr(generic.shutil, "which", lambda name: "/usr/bin/uv" if name == "uv" else None)
    forget_probes()

    tool = GenericTool.from_dict(
        {"name": "pycowsay", "type": "uvx", "pip_name": "pycowsay", "category": "tools",
         "description": "x"},
        key="pycowsay",
    )

    def install_it(version=None):
        state["installed"] = True

    tool.update = install_it  # type: ignore[method-assign]

    assert tool.installed_version() == ""  # warms the memo with "not installed"

    perform(tool, step(Action.INSTALL_PINNED, "0.0.0.1"))

    assert tool.installed_version() == "0.0.0.1"
    forget_probes()
