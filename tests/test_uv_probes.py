"""The uv update probes ask each question once per run, however many uv tools exist.

Regression tests for finding F-1 in docs/specs/outdated: `devstuff update` probes every
installed tool on a thread pool, and for uv tools the old code ran `uv tool list` once per
tool plus `uv tool list --outdated` once per *thread that got there before the first one
finished* — `functools.lru_cache` memoises results, it does not make concurrent first
callers wait for each other.
"""

from __future__ import annotations

import subprocess
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import pytest

from dev_setup import generic
from dev_setup.generic import GenericTool

TOOL_LIST = """\
alpha v1.0.0
- alpha
beta v2.0.0
- beta
gamma v3.0.0
- gamma
delta v4.0.0
- delta
"""

OUTDATED = "alpha v1.0.0 [latest: 1.1.0]\n"


def _reset_uv_caches() -> None:
    # Defensive about names: this has to run (and fail for the right reason) against both
    # the old implementation and the fixed one.
    for name in ("_uv_outdated_map", "_uv_tool_versions"):
        fn = getattr(generic, name, None)
        if fn is not None and hasattr(fn, "cache_clear"):
            fn.cache_clear()


@pytest.fixture(autouse=True)
def _fresh_caches():
    _reset_uv_caches()
    yield
    _reset_uv_caches()


@pytest.fixture
def uv_calls(monkeypatch) -> Counter:
    """Replace `_probe` with a fake uv that counts calls and is slow enough to overlap."""
    calls: Counter = Counter()
    lock = threading.Lock()

    def fake_probe(cmd, **_kwargs):
        argv = tuple(str(c) for c in cmd)
        with lock:
            calls[argv[1:]] += 1
        time.sleep(0.05)  # widen the window in which racing threads would all miss the cache
        out = OUTDATED if "--outdated" in argv else TOOL_LIST
        return subprocess.CompletedProcess(argv, 0, stdout=out, stderr="")

    monkeypatch.setattr(generic, "_probe", fake_probe)
    monkeypatch.setattr(generic.shutil, "which", lambda _c: "/usr/bin/uv")
    return calls


def _uv_tool(key: str) -> GenericTool:
    return GenericTool(key=key, name=key, install_type="uvx", pip_name=key)


def _check_all(keys: list[str]) -> dict[str, generic.UpdateStatus]:
    tools = [_uv_tool(k) for k in keys]
    with ThreadPoolExecutor(max_workers=8) as pool:
        return dict(zip(keys, pool.map(lambda t: t.check_for_update(), tools), strict=True))


def test_each_uv_question_is_asked_once_across_many_tools(uv_calls):
    _check_all(["alpha", "beta", "gamma", "delta"])

    assert uv_calls[("tool", "list", "--color", "never")] == 1
    assert uv_calls[("tool", "list", "--outdated", "--color", "never")] == 1
    # Nothing else was asked of uv either.
    assert sum(uv_calls.values()) == 2


def test_probe_count_does_not_grow_with_the_number_of_uv_tools(uv_calls):
    _check_all(["alpha", "beta"])
    few = sum(uv_calls.values())

    uv_calls.clear()
    _reset_uv_caches()
    _check_all(["alpha", "beta", "gamma", "delta"])

    assert sum(uv_calls.values()) == few


def test_results_are_unchanged_by_sharing_the_probes(uv_calls):
    got = _check_all(["alpha", "beta", "gamma", "delta"])

    assert got["alpha"] == generic.UpdateStatus(current="1.0.0", latest="1.1.0", available=True)
    for key, version in (("beta", "2.0.0"), ("gamma", "3.0.0"), ("delta", "4.0.0")):
        assert got[key] == generic.UpdateStatus(current=version, available=False)


def test_tool_absent_from_uv_tool_list_is_unknown_not_current(uv_calls):
    # The `commitizen` case from the spec (F-2): on PATH, but not managed by `uv tool`.
    status = _check_all(["not-a-uv-tool"])["not-a-uv-tool"]

    assert status.available is None
    assert status.current == ""


def test_uv_missing_degrades_to_unknown_without_probing(monkeypatch):
    calls: list = []
    monkeypatch.setattr(generic, "_probe", lambda cmd, **kw: calls.append(cmd))
    monkeypatch.setattr(generic.shutil, "which", lambda _c: None)

    assert _uv_tool("alpha").check_for_update() == generic.UpdateStatus()
    assert calls == []


# -- A probe that failed is unknown, never current (spec FR-7, FR-23) ------------------
#
# Measured 2026-10-10: with no network, `uv tool list --outdated` exits 2 and prints an
# error. The old code never looked at the exit code, so empty stdout became "nothing is
# outdated" and every uv tool read as up to date. (With UV_OFFLINE set and a cold cache uv
# exits 0 and prints nothing at all, so the environment variable is the only signal.)


def _uv_fake(monkeypatch, *, list_rc=0, outdated_rc=0, outdated_out=OUTDATED, calls=None):
    def fake_probe(cmd, **_kw):
        argv = tuple(str(c) for c in cmd)
        if calls is not None:
            calls.append(argv[1:])
        if "--outdated" in argv:
            out = outdated_out if outdated_rc == 0 else ""
            return subprocess.CompletedProcess(argv, outdated_rc, stdout=out, stderr="error: Failed to fetch")
        out = TOOL_LIST if list_rc == 0 else ""
        return subprocess.CompletedProcess(argv, list_rc, stdout=out, stderr="")

    monkeypatch.setattr(generic, "_probe", fake_probe)
    monkeypatch.setattr(generic.shutil, "which", lambda _c: "/usr/bin/uv")
    monkeypatch.delenv("UV_OFFLINE", raising=False)


def test_failed_outdated_probe_is_unknown_not_current(monkeypatch):
    _uv_fake(monkeypatch, outdated_rc=2)

    status = _uv_tool("beta").check_for_update()

    assert status.available is None  # NOT False — we do not know it is current
    assert status.current == "2.0.0"  # what we do know is still reported
    assert "index" in status.note


def test_failed_outdated_probe_does_not_hide_a_known_update_elsewhere(monkeypatch):
    # Failure is for the whole listing, so no tool may claim current *or* outdated.
    _uv_fake(monkeypatch, outdated_rc=2)

    assert all(_uv_tool(k).check_for_update().available is None for k in ("alpha", "beta"))


def test_failed_tool_list_is_unknown_with_a_note(monkeypatch):
    _uv_fake(monkeypatch, list_rc=1)

    status = _uv_tool("alpha").check_for_update()

    assert status.available is None
    assert status.current == ""
    assert "uv tool list" in status.note


def test_tool_not_managed_by_uv_says_so(monkeypatch):
    # The `commitizen` case, F-2: on PATH, absent from `uv tool list`.
    _uv_fake(monkeypatch)

    status = _uv_tool("not-a-uv-tool").check_for_update()

    assert status.available is None
    assert "not installed via `uv tool`" in status.note


def test_uv_offline_env_is_unknown_and_does_not_ask_for_updates(monkeypatch):
    calls: list = []
    _uv_fake(monkeypatch, calls=calls)
    monkeypatch.setenv("UV_OFFLINE", "1")

    status = _uv_tool("alpha").check_for_update()

    assert status.available is None
    assert "offline" in status.note
    assert ("tool", "list", "--outdated", "--color", "never") not in calls


@pytest.mark.parametrize("value", ["0", "false", ""])
def test_uv_offline_env_falsy_values_do_not_count(monkeypatch, value):
    _uv_fake(monkeypatch)
    monkeypatch.setenv("UV_OFFLINE", value)

    assert _uv_tool("alpha").check_for_update().available is True


def test_healthy_probes_set_no_note(monkeypatch):
    _uv_fake(monkeypatch)

    assert _uv_tool("alpha").check_for_update().note == ""
    assert _uv_tool("beta").check_for_update().note == ""
