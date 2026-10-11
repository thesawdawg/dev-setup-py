"""Integration tests: `devstuff profile apply` on an empty machine (docs/specs/profile-apply, A6).

The unit tests fake every tool. This is the one place `apply` meets real installers: a `bash`
installer, a pinned `uvx` install, and `requires` ordering across them. Run it in the CI image
(see dev/Dockerfile.ci), where nothing is installed yet:

    cd dev && make run-apply-tests

It needs sudo and the network, because installing does. Each test goes through the CLI as a
subprocess, exactly as a user would, so what is asserted is what a user would see — including
that the bundles are really inside the installed package.
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from dev_setup import bundles, registry

pytestmark = pytest.mark.integration

# An older commitizen release (the latest was 4.19 when this was written): a pin that is not
# "whatever the installer would have picked anyway". `commitizen` needs `uv`, so applying it
# also exercises ordering, and `yq` is a plain `bash` installer.
PIN = "3.31.0"
PROFILE = f'version: 1\ntools:\n  commitizen:\n    version: "{PIN}"\n  yq: {{}}\n'
PROFILE_KEYS = ("commitizen", "yq")  # what the profile names ...
KEYS = ("uv", *PROFILE_KEYS)  # ... and with what it pulls in
ACTIONS = {"uv": "install", "commitizen": "install pinned", "yq": "install"}


def devstuff(*args: str, expect: int = 0) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        [sys.executable, "-m", "dev_setup", *args],
        capture_output=True, text=True, timeout=1800,
    )
    assert proc.returncode == expect, (
        f"devstuff {' '.join(args)} exited {proc.returncode}, expected {expect}\n"
        f"--- stdout\n{proc.stdout}\n--- stderr\n{proc.stderr}"
    )
    return proc


def states(rows: list[dict]) -> dict[str, str]:
    return {r["key"]: r["state"] for r in rows if r["key"] in PROFILE_KEYS}


def plan_keys(doc: dict) -> list[str]:
    return [s["key"] for s in doc["plan"]["steps"]]


@pytest.fixture
def empty_machine():
    """The ordering and idempotence checks mean nothing on a machine that already has the tools."""
    registry.init()
    present = [k for k in KEYS if registry.get(k) and registry.get(k).is_installed()]
    if present:
        pytest.skip(f"needs a machine without {present}; run it in the devstuff-ci container")


def test_apply_reproduces_a_profile_on_an_empty_machine(empty_machine, tmp_path):
    profile_file = tmp_path / "work.yaml"
    profile_file.write_text(PROFILE)
    path = str(profile_file)

    # 1. The plan. A dependency comes first, and a dry run changes nothing.
    plan = json.loads(devstuff("profile", "apply", path, "--dry-run", "--json").stdout)
    keys = plan_keys(plan)
    assert sorted(keys) == sorted(KEYS), keys
    assert keys.index("uv") < keys.index("commitizen"), f"uv must come before what needs it: {keys}"
    actions = {s["key"]: s["action"] for s in plan["plan"]["steps"]}
    assert actions == ACTIONS, actions
    assert plan["run"] is None
    # A dry run installed nothing. (Rows are filtered to the profile's own keys: a tool that is
    # installed but not listed, like `uv` later on, is an `extra` row and not what is asserted.)
    assert states(plan["rows"]) == {k: "missing" for k in PROFILE_KEYS}, plan["rows"]

    # 2. Apply it. Every step done, and the verdict (a fresh re-read) is that everything matches.
    done = json.loads(devstuff("profile", "apply", path, "--yes", "--json").stdout)
    statuses = {s["key"]: s["status"] for s in done["run"]["steps"]}
    assert statuses == {k: "done" for k in KEYS}, statuses
    assert states(done["rows"]) == {k: "ok" for k in PROFILE_KEYS}, done["rows"]

    # 3. An independent `diff` agrees: the pin was honoured, not just the install.
    rows = json.loads(devstuff("profile", "diff", path, "--json").stdout)
    assert states(rows) == {k: "ok" for k in PROFILE_KEYS}, rows
    devstuff("profile", "diff", path, "--exit-code", "--ignore-extras")

    # 4. Idempotence (NFR-4): the second plan is empty, and applying again changes nothing.
    again = json.loads(devstuff("profile", "apply", path, "--dry-run", "--json").stdout)
    assert plan_keys(again) == [], again["plan"]
    rerun = json.loads(devstuff("profile", "apply", path, "--yes", "--json").stdout)
    assert rerun["run"] is None and plan_keys(rerun) == []


@pytest.mark.parametrize("name", bundles.names())
def test_every_bundle_resolves_from_the_installed_package(name):
    """Offline: a bundle is found by name where this is really installed, and nothing it lists is
    silently dropped from the plan (spec FR-2, F-9)."""
    doc = json.loads(devstuff("profile", "apply", f"@{name}", "--dry-run", "--json").stdout)

    accounted = (
        set(plan_keys(doc))
        | set(doc["plan"]["ok"])
        | {s["key"] for s in doc["plan"]["skipped"]}
    )
    assert set(bundles.resolve(f"@{name}").tools) <= accounted
