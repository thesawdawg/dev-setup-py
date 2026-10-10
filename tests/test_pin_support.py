"""`supports_pin` must agree with what `update(version=…)` actually does (spec FR-26, SD-10).

The predicate is an explicit set, not something derived by calling `update` and catching the
error: a read (`profile diff`) must not attempt a mutation to find out what it can do. The price
of being explicit is that it can drift from the updaters, so this test is what ties them
together — for every install type there is an updater for.
"""

from __future__ import annotations

import re

import pytest

from dev_setup import generic
from dev_setup.generic import GenericTool, supports_pin

VERSION = "1.2.3"


def _tool(install_type: str, **fields) -> GenericTool:
    return GenericTool(key="demo", name="Demo", install_type=install_type, **fields)


# One tool per way an install type can be configured. A new entry in `_UPDATERS` with no case
# here fails `test_every_updater_type_is_covered`, which is how a new type is forced to decide.
CASES = {
    "npm": _tool("npm", npm_name="pkg"),
    "pip": _tool("pip", pip_name="pkg"),
    "uvx": _tool("uvx", pip_name="pkg"),
    "apt, one package": _tool("apt", apt_packages="pkg"),
    "apt, several packages": _tool("apt", apt_packages="pkg other"),
    "git": _tool("git", git_url="https://example.com/x.git"),
    "script": _tool("script", script_url="https://example.com/install.sh"),
    "bash": _tool("bash", install_script="echo hi"),
    "an install type nothing knows": _tool("composer"),
}


@pytest.fixture
def commands(monkeypatch) -> list[list[str]]:
    """Stub everything `update` could shell out to; record the argv it would have run."""
    ran: list[list[str]] = []
    monkeypatch.setattr(generic, "_run", lambda cmd, **_kw: ran.append([str(c) for c in cmd]))
    monkeypatch.setattr(generic, "_run_apt_update", lambda: None)
    monkeypatch.setattr(generic.shutil, "which", lambda _c: "/usr/bin/uv")
    # A "reinstall" is how script/bash update — record it as a command so a swallowed pin shows.
    monkeypatch.setattr(generic, "_install_script_url", lambda t: ran.append(["reinstall-script", t.key]))
    monkeypatch.setattr(generic, "_install_bash", lambda t: ran.append(["reinstall-bash", t.key]))
    monkeypatch.setattr(GenericTool, "get_version", lambda self: VERSION)
    return ran


def honours_a_pin(tool: GenericTool, ran: list[list[str]]) -> bool:
    """Does `tool.update(version=…)` really install that version?

    True only if it succeeds *and* the version reaches a command. Raising is False. Succeeding
    without the version appearing anywhere would mean a pin was silently swallowed — a third
    outcome that must fail the test loudly, not be rounded to either answer.
    """
    try:
        tool.update(version=VERSION)
    except RuntimeError as exc:
        # Only a *refusal of the pin* counts as "does not honour it". Failing for any other
        # reason (a missing clone, an unset field) would be indistinguishable here, and a
        # test that rounds those to "can't pin" passes while an updater quietly accepts pins.
        if not re.search(r"pinning|unsupported update type", str(exc), re.IGNORECASE):
            raise AssertionError(
                f"{tool.install_type}: update(version=...) failed, but not by refusing the pin "
                f"({exc}). Fix the fixture, or the updater no longer refuses pins."
            ) from exc
        return False
    assert any(VERSION in part for cmd in ran for part in cmd), (
        f"{tool.install_type}: update(version=...) succeeded but the version appears in no "
        f"command — a pin was silently ignored: {ran}"
    )
    return True


@pytest.mark.parametrize("label", list(CASES))
def test_supports_pin_agrees_with_what_update_really_does(label, commands):
    tool = CASES[label]

    assert supports_pin(tool) is honours_a_pin(tool, commands), label


def test_every_updater_type_is_covered():
    covered = {tool.install_type for tool in CASES.values()}

    assert set(generic._UPDATERS) <= covered, (
        "a new install type has an updater but no case in CASES — decide whether it can pin "
        f"and add it: {sorted(set(generic._UPDATERS) - covered)}"
    )


def test_exactly_these_types_are_pinnable_today():
    pinnable = sorted(label for label, tool in CASES.items() if supports_pin(tool))

    assert pinnable == ["apt, one package", "npm", "pip", "uvx"]


def test_pins_reach_the_right_command_per_type(commands):
    # What "honours a pin" means in each ecosystem — so the agreement above is not vacuous.
    for label in ("npm", "uvx", "apt, one package"):
        CASES[label].update(version=VERSION)

    flat = [" ".join(cmd) for cmd in commands]
    assert any("pkg@1.2.3" in c for c in flat)
    assert any("pkg==1.2.3" in c for c in flat)
    assert any("pkg=1.2.3" in c for c in flat)


def test_a_multi_package_apt_tool_is_unpinnable_even_though_apt_can_pin():
    # Resolved OQ-6: a profile entry has one version, and `update` pins a single package only.
    assert supports_pin(CASES["apt, one package"]) is True
    assert supports_pin(CASES["apt, several packages"]) is False


def test_an_apt_tool_with_no_packages_is_unpinnable():
    assert supports_pin(_tool("apt", apt_packages="")) is False
