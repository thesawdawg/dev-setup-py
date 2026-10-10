"""Pin what the npm and apt update checkers return, before and after P2's refactor.

P2 factors the local version reads out of `_check_update_apt` so `installed_version()` can share
them. These tests are the baseline that refactor must not move: they were written and run against
the unrefactored code first (docs/specs/profile, development-plan P2).
"""

from __future__ import annotations

import subprocess

import pytest

from dev_setup import generic
from dev_setup.generic import GenericTool, UpdateStatus


def _apt_tool(packages: str = "pkg") -> GenericTool:
    return GenericTool(key="x", name="X", install_type="apt", apt_packages=packages)


def _apt_probe(monkeypatch, *, dpkg="1.0-1", dpkg_rc=0, candidate="1.1-1", policy_exc=None, dpkg_exc=None):
    seen: list[list] = []

    def fake(cmd, **_kw):
        seen.append(list(cmd))
        if cmd[0] == "dpkg-query":
            if dpkg_exc:
                raise dpkg_exc
            return subprocess.CompletedProcess(cmd, dpkg_rc, stdout=dpkg if dpkg_rc == 0 else "", stderr="")
        if policy_exc:
            raise policy_exc
        out = f"pkg:\n  Installed: {dpkg}\n  Candidate: {candidate}\n"
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")

    monkeypatch.setattr(generic, "_probe", fake)
    return seen


# -- apt ------------------------------------------------------------------------------------


def test_apt_newer_candidate_is_an_update(monkeypatch):
    _apt_probe(monkeypatch, dpkg="1.0-1", candidate="1.1-1")

    assert _apt_tool().check_for_update() == UpdateStatus(current="1.0-1", latest="1.1-1", available=True)


def test_apt_same_candidate_is_current(monkeypatch):
    _apt_probe(monkeypatch, dpkg="1.1-1", candidate="1.1-1")

    assert _apt_tool().check_for_update() == UpdateStatus(current="1.1-1", latest="1.1-1", available=False)


def test_apt_package_not_installed_has_no_current_and_no_update(monkeypatch):
    _apt_probe(monkeypatch, dpkg_rc=1, candidate="1.1-1")

    status = _apt_tool().check_for_update()

    assert status.current == "" and status.latest == "1.1-1" and status.available is False


def test_apt_dpkg_query_raising_means_no_current_version(monkeypatch):
    _apt_probe(monkeypatch, dpkg_exc=RuntimeError("boom"), candidate="1.1-1")

    status = _apt_tool().check_for_update()

    assert status.current == "" and status.latest == "1.1-1"


def test_apt_uses_the_first_package_of_several(monkeypatch):
    seen = _apt_probe(monkeypatch)

    _apt_tool("first second third").check_for_update()

    assert ["dpkg-query", "-W", "-f=${Version}", "first"] in seen
    assert not any("second" in c for cmd in seen for c in cmd)


def test_apt_without_a_package_name_is_unknown(monkeypatch):
    seen = _apt_probe(monkeypatch)

    assert _apt_tool("").check_for_update() == UpdateStatus()
    assert seen == []


def test_apt_policy_failure_keeps_the_installed_version_and_explains(monkeypatch):
    _apt_probe(monkeypatch, policy_exc=RuntimeError("boom"))

    status = _apt_tool().check_for_update()

    assert status.current == "1.0-1" and status.available is None and status.note


# -- npm ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("installed", "latest", "expected"),
    [
        ("1.0.0", "1.1.0", UpdateStatus(current="1.0.0", latest="1.1.0", available=True)),
        ("1.1.0", "1.1.0", UpdateStatus(current="1.1.0", latest="1.1.0", available=False)),
        # Inequality, not ordering: a locally newer version still reads as an update (spec F-4).
        ("2.0.0", "1.1.0", UpdateStatus(current="2.0.0", latest="1.1.0", available=True)),
        # Not installed globally: nothing to compare, so never an update.
        ("", "1.1.0", UpdateStatus(current="", latest="1.1.0", available=False)),
    ],
)
def test_npm_checker(monkeypatch, installed, latest, expected):
    monkeypatch.setattr(generic, "_npm_installed_version", lambda _p: installed)
    monkeypatch.setattr(generic, "_npm_latest_version", lambda _p: latest)
    tool = GenericTool(key="x", name="X", install_type="npm", npm_name="x")

    assert tool.check_for_update() == expected


def test_npm_without_a_package_name_is_unknown():
    tool = GenericTool(key="x", name="X", install_type="npm", npm_name="")

    assert tool.check_for_update() == UpdateStatus()
