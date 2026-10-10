"""`GenericTool.installed_version()`: the installed version, read locally (spec FR-11, FR-25).

It exists so `profile snapshot` can record a version without the network and without parsing
`get_version()`'s free text (`'bat 0.26.1 (979ba22)'`, spec F-1). Everything here is about what
it must *not* do: ask the network, raise, or guess.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from dev_setup import generic
from dev_setup.generic import GenericTool

TOOL_LIST = "alpha v1.0.0\n- alpha\nbeta v2.0.0\n- beta\n"


@pytest.fixture(autouse=True)
def _fresh_uv_cache():
    generic._uv_tool_versions.cache_clear()
    generic._uv_outdated_map.cache_clear()
    yield
    generic._uv_tool_versions.cache_clear()
    generic._uv_outdated_map.cache_clear()


@pytest.fixture
def probes(monkeypatch) -> list[str]:
    """A fake machine: records every command as one string, answers like the real tools."""
    seen: list[str] = []

    def fake(cmd, **_kw):
        line = " ".join(str(c) for c in cmd)
        seen.append(line)
        if "npm list" in line:
            out = json.dumps({"dependencies": {"pkg": {"version": "3.4.5"}}})
        elif "tool list" in line:
            out = TOOL_LIST
        elif cmd[0] == "dpkg-query":
            out = "7.8-9"
        else:
            out = ""
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")

    monkeypatch.setattr(generic, "_probe", fake)
    monkeypatch.setattr(generic.shutil, "which", lambda _c: "/usr/bin/uv")
    return seen


def tool(install_type: str, **fields) -> GenericTool:
    return GenericTool(key="demo", name="Demo", install_type=install_type, **fields)


# -- what it reads --------------------------------------------------------------------------


def test_npm_reads_the_global_package_version(probes):
    assert tool("npm", npm_name="pkg").installed_version() == "3.4.5"


@pytest.mark.parametrize("install_type", ["uvx", "pip"])
def test_uv_types_read_uv_tool_list(probes, install_type):
    assert tool(install_type, pip_name="alpha").installed_version() == "1.0.0"
    assert tool(install_type, pip_name="beta").installed_version() == "2.0.0"


def test_single_package_apt_reads_dpkg(probes):
    assert tool("apt", apt_packages="pkg").installed_version() == "7.8-9"


# -- what it refuses to guess ---------------------------------------------------------------


def test_a_tool_uv_does_not_manage_has_no_installed_version(probes):
    # The `commitizen` case: on PATH, but not a `uv tool`.
    assert tool("uvx", pip_name="not-a-uv-tool").installed_version() == ""


def test_multi_package_apt_has_no_installed_version_and_asks_nothing(probes):
    # Which package's version would it be? (Resolved OQ-6: such an entry is unpinnable anyway.)
    assert tool("apt", apt_packages="a b").installed_version() == ""
    assert probes == []


@pytest.mark.parametrize(
    ("install_type", "fields"),
    [
        ("git", {"git_url": "https://example.com/x.git"}),
        ("script", {"script_url": "https://example.com/i.sh"}),
        ("bash", {"install_script": "echo hi", "check_cmd": "demo"}),
        ("composer", {}),
    ],
)
def test_types_with_no_clean_version_return_empty_and_run_nothing(probes, install_type, fields):
    assert tool(install_type, **fields).installed_version() == ""
    assert probes == [], "a type with no reader must not shell out (and must not parse get_version)"


@pytest.mark.parametrize(
    "fields",
    [
        {"install_type": "npm", "npm_name": ""},
        {"install_type": "uvx", "pip_name": ""},
        {"install_type": "apt", "apt_packages": ""},
    ],
)
def test_missing_package_name_returns_empty(probes, fields):
    assert GenericTool(key="demo", name="Demo", **fields).installed_version() == ""


# -- it never touches the network -------------------------------------------------------------

NETWORK_FRAGMENTS = ("npm view", "--outdated", "apt-cache", "ls-remote", "apt-get update", "pip index")


def test_no_type_ever_runs_a_networked_command(probes):
    for t in (
        tool("npm", npm_name="pkg"),
        tool("uvx", pip_name="alpha"),
        tool("pip", pip_name="beta"),
        tool("apt", apt_packages="pkg"),
        tool("git", git_url="https://example.com/x.git"),
    ):
        t.installed_version()

    assert probes, "the fake machine recorded nothing, so this test would pass vacuously"
    for line in probes:
        assert not any(f in line for f in NETWORK_FRAGMENTS), line


@pytest.mark.parametrize(
    "t",
    [
        tool("npm", npm_name="pkg"),
        tool("uvx", pip_name="alpha"),
        tool("pip", pip_name="beta"),
        tool("apt", apt_packages="pkg"),
        tool("git", git_url="https://example.com/x.git"),
        tool("script", script_url="https://example.com/i.sh"),
        tool("bash", install_script="x", check_cmd="demo"),
        tool("composer"),
    ],
    ids=lambda t: t.install_type,
)
def test_never_goes_through_get_version(probes, monkeypatch, t):
    # Record rather than raise: installed_version() swallows exceptions, so a raising trap
    # would be swallowed with it and the test would pass whatever the code did.
    called: list[str] = []

    def spy(self):
        called.append(self.install_type)
        return "bat 1.0 (abc)"

    monkeypatch.setattr(GenericTool, "get_version", spy)

    t.installed_version()

    assert called == [], "get_version() is free text; installed_version() must not parse it"


# -- it never raises ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "exc",
    [RuntimeError("boom"), subprocess.TimeoutExpired("cmd", 1), OSError("gone"), ValueError("bad")],
)
@pytest.mark.parametrize(
    "t",
    [tool("npm", npm_name="pkg"), tool("uvx", pip_name="alpha"), tool("apt", apt_packages="pkg")],
    ids=["npm", "uvx", "apt"],
)
def test_a_failing_probe_is_an_empty_string_not_an_exception(monkeypatch, t, exc):
    def explode(*_a, **_k):
        raise exc

    monkeypatch.setattr(generic, "_probe", explode)
    monkeypatch.setattr(generic.shutil, "which", lambda _c: "/usr/bin/uv")

    assert t.installed_version() == ""


@pytest.mark.parametrize("exc", [RuntimeError("boom"), KeyError("x"), ValueError("bad")])
def test_installed_version_guards_against_a_reader_that_raises(monkeypatch, exc):
    # The readers swallow their own errors today, so nothing above reaches the outer guard.
    # A future reader that forgets to must not be able to break `profile snapshot`.
    def explode(_tool):
        raise exc

    monkeypatch.setitem(generic._VERSION_READERS, "npm", explode)

    assert tool("npm", npm_name="pkg").installed_version() == ""


def test_a_probe_that_exits_non_zero_is_an_empty_string(monkeypatch):
    monkeypatch.setattr(
        generic, "_probe", lambda cmd, **_kw: subprocess.CompletedProcess(cmd, 1, stdout="", stderr="no")
    )
    monkeypatch.setattr(generic.shutil, "which", lambda _c: "/usr/bin/uv")

    for t in (tool("npm", npm_name="pkg"), tool("uvx", pip_name="alpha"), tool("apt", apt_packages="pkg")):
        assert t.installed_version() == ""


def test_the_uv_reader_shares_the_per_run_probe(probes):
    # It must reuse `_uv_tool_versions`' once-per-process memo, not run `uv tool list` per tool.
    for key in ("alpha", "beta", "alpha", "beta"):
        tool("uvx", pip_name=key).installed_version()

    assert sum("tool list" in line for line in probes) == 1
