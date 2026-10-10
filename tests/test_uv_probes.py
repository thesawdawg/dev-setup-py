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
