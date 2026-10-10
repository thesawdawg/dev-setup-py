"""Gathering a snapshot of this machine (docs/specs/profile, FR-7, FR-10–13)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from dev_setup import registry
from dev_setup.profile import Entry
from dev_setup.snapshot import take


def fake(key, install_type="npm", *, installed=True, version="", builtin=True, apt_packages="", boom=None):
    """A duck-typed tool that records how it was asked."""
    calls: list[str] = []

    def is_installed():
        calls.append("is_installed")
        if boom == "is_installed":
            raise RuntimeError("boom")
        return installed

    def installed_version():
        calls.append("installed_version")
        return version

    return SimpleNamespace(
        key=key,
        install_type=install_type,
        apt_packages=apt_packages,
        builtin=builtin,
        is_installed=is_installed,
        installed_version=installed_version,
        calls=calls,
    )


# -- which tools ---------------------------------------------------------------------------


def test_only_installed_tools_are_included():
    snap = take([fake("a"), fake("b", installed=False), fake("c")])

    assert set(snap.profile.tools) == {"a", "c"}


def test_defaults_to_the_whole_registry(monkeypatch):
    monkeypatch.setattr(registry, "all_tools", lambda: [fake("only")])

    assert list(take().profile.tools) == ["only"]


def test_nothing_installed_is_an_empty_profile():
    snap = take([fake("a", installed=False)])

    assert snap.profile.tools == {}
    assert snap.unreadable == () and snap.custom == ()


def test_a_tool_whose_is_installed_raises_is_left_out_not_fatal():
    snap = take([fake("bad", boom="is_installed"), fake("ok")])

    assert list(snap.profile.tools) == ["ok"]


# -- versions: off by default (OQ-1) ------------------------------------------------------------


def test_default_is_keys_only_and_never_reads_a_version():
    tools = [fake("a", version="1.0.0"), fake("b", "uvx", version="2.0.0")]

    snap = take(tools)

    assert all(entry == Entry() for entry in snap.profile.tools.values())
    assert all("installed_version" not in t.calls for t in tools)


# -- versions: on (FR-10) ----------------------------------------------------------------------


def test_versions_are_recorded_for_pinnable_types_only():
    tools = [
        fake("n", "npm", version="1.0.0"),
        fake("u", "uvx", version="2.0.0"),
        fake("p", "pip", version="3.0.0"),
        fake("a", "apt", version="4.0-1", apt_packages="pkg"),
        fake("g", "git", version="abc1234"),
        fake("s", "script", version="5.0.0"),
        fake("b", "bash", version="6.0.0"),
    ]

    snap = take(tools, versions=True)

    got = {k: e.version for k, e in snap.profile.tools.items()}
    assert got == {"n": "1.0.0", "u": "2.0.0", "p": "3.0.0", "a": "4.0-1", "g": None, "s": None, "b": None}


def test_unpinnable_types_are_never_even_asked_for_a_version():
    git, bash = fake("g", "git", version="x"), fake("b", "bash", version="y")

    take([git, bash], versions=True)

    assert "installed_version" not in git.calls and "installed_version" not in bash.calls


def test_a_multi_package_apt_tool_gets_no_version_and_no_warning():
    # Resolved OQ-6: unpinnable, so there is nothing it could have recorded.
    snap = take([fake("a", "apt", version="1.0", apt_packages="one two")], versions=True)

    assert snap.profile.tools["a"] == Entry()
    assert snap.unreadable == ()


# -- unreadable versions (FR-12) ---------------------------------------------------------------


def test_a_pinnable_tool_with_an_unreadable_version_is_written_bare_and_reported():
    snap = take([fake("ok", "uvx", version="1.0"), fake("lost", "uvx", version="")], versions=True)

    assert snap.profile.tools["lost"] == Entry()
    assert snap.profile.tools["ok"] == Entry("1.0")
    assert snap.unreadable == ("lost",)


def test_a_version_the_profile_format_would_refuse_counts_as_unreadable():
    # A reader returning padding must not crash the snapshot or write a file `load` rejects.
    snap = take([fake("odd", "npm", version="  1.0  ")], versions=True)

    assert snap.profile.tools["odd"] == Entry()
    assert snap.unreadable == ("odd",)


def test_a_reader_that_raises_is_unreadable_not_fatal():
    # `installed_version()` is documented never to raise, so nothing real reaches this guard —
    # which is exactly why it needs a test: a future reader that forgets must not break snapshot.
    tool = fake("flaky", "npm")

    def explode():
        raise RuntimeError("boom")

    tool.installed_version = explode

    snap = take([tool, fake("fine", "npm", version="1.0")], versions=True)

    assert snap.profile.tools["flaky"] == Entry()
    assert snap.profile.tools["fine"] == Entry("1.0")
    assert snap.unreadable == ("flaky",)


def test_nothing_is_reported_unreadable_without_versions():
    assert take([fake("lost", "uvx", version="")]).unreadable == ()


def test_unreadable_is_sorted_and_only_covers_installed_tools():
    snap = take(
        [fake("z", "npm"), fake("m", "npm", installed=False), fake("a", "uvx")], versions=True
    )

    assert snap.unreadable == ("a", "z")


# -- custom tools (FR-13, OQ-3) ----------------------------------------------------------------


def test_tools_from_the_user_catalog_are_included_and_listed():
    snap = take([fake("bundled"), fake("mine", builtin=False), fake("also-mine", builtin=False)])

    assert {"bundled", "mine", "also-mine"} == set(snap.profile.tools)
    assert snap.custom == ("also-mine", "mine")


def test_an_uninstalled_custom_tool_is_not_mentioned():
    assert take([fake("mine", builtin=False, installed=False)]).custom == ()


# -- shape ----------------------------------------------------------------------------------


@pytest.mark.parametrize("n", [1, 5, 25])
def test_result_does_not_depend_on_completion_order(n):
    tools = [fake(f"t{i:02}", "npm", version=f"{i}.0") for i in range(n)]

    a = take(tools, versions=True)
    b = take(list(reversed(tools)), versions=True)

    assert a.profile == b.profile
    assert a.unreadable == b.unreadable
