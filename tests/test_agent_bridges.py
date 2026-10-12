"""The `outdated` and `profile_diff` bridges, and what every non-primitive bridge may assume
(docs/specs/mcp, FR-2, FR-4, FR-9, FR-10, M-1).

These bridges exist for the MCP server and serve the local agent too. A bridge is called with
`ws=None, config=None` by the server, which has neither a workspace nor an agent config (F-5).
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from dev_setup import bundles, registry, verbose
from dev_setup.agent import bridges, primitives
from dev_setup.agent import catalog as agent_catalog
from dev_setup.agent import registry as agent_registry
from dev_setup.agent.sandbox import SandboxError
from dev_setup.cli import cli
from dev_setup.generic import UpdateStatus


@pytest.fixture(autouse=True)
def quiet_level():
    verbose.set_level(verbose.QUIET)
    yield
    verbose.set_level(verbose.QUIET)


def _forbidden(name):
    def call(*_a, **_k):
        raise AssertionError(f"a read-only bridge called {name}()")

    return call


def fake(key, install_type="npm", *, installed=True, status=None, version=""):
    return SimpleNamespace(
        key=key,
        name=key.title(),
        description=f"the {key} tool",
        category="tools",
        install_type=install_type,
        apt_packages="",
        builtin=True,
        requires=[],
        docs_url="",
        is_installed=lambda: installed,
        installed_version=lambda: version,
        check_for_update=lambda: status or UpdateStatus(),
        install=_forbidden("install"),
        remove=_forbidden("remove"),
        update=_forbidden("update"),
    )


OUTDATED = UpdateStatus(current="1.0.0", latest="1.1.0", available=True)
CURRENT = UpdateStatus(current="2.0.0", latest="2.0.0", available=False)

MACHINE = [
    fake("codex", "npm", status=OUTDATED),
    fake("eslint", "npm", status=CURRENT),
    fake("flaky", "npm", status=UpdateStatus()),  # a checker that could not answer
    fake("starship", "bash"),  # no checker exists for this type
    fake("absent", "npm", installed=False),
    fake("claude-code"),  # a key the @ai-agents bundle names
]


@pytest.fixture
def machine(monkeypatch):
    monkeypatch.setattr(registry, "init", lambda: None)
    monkeypatch.setattr(registry, "all_tools", lambda: list(MACHINE))
    monkeypatch.setattr(registry, "get", lambda key: next((t for t in MACHINE if t.key == key), None))
    return MACHINE


def rows(text: str) -> list[dict]:
    return json.loads(text)


# -- the catalog: the new tools are declared, and arrays are a parameter type ------------------


def test_bundled_catalog_declares_both_bridges():
    tools = agent_registry.build()
    for key in ("outdated", "profile_diff"):
        assert tools[key].impl == "catalog"
        assert tools[key].target == key
        assert tools[key].mutating is False
        assert bridges.get(key) is not None


def test_array_is_a_parameter_type_and_advertises_its_items():
    raw = {
        "version": 1,
        "tools": {
            "t": {
                "description": "d",
                "impl": "primitive",
                "params": [{"name": "keys", "type": "array"}],
            }
        },
    }
    agent_catalog.validate_catalog(raw)
    tool = agent_registry.AgentTool.from_dict("t", raw["tools"]["t"])
    prop = tool.to_schema()["function"]["parameters"]["properties"]["keys"]
    # JSON Schema (and OpenAPI 3.1) require `items` to say what is in the array; a bare
    # `{"type": "array"}` is accepted by validators but tells a model nothing.
    assert prop == {"type": "array", "items": {"type": "string"}}


def test_outdated_declares_packages_as_a_list_of_keys():
    schema = agent_registry.build()["outdated"].to_schema()["function"]["parameters"]
    assert schema["properties"]["packages"]["type"] == "array"
    assert "packages" not in schema["required"]


# -- outdated (FR-9) ------------------------------------------------------------------------


def test_outdated_matches_the_command_json_exactly(machine):
    """FR-7: one documented shape. The bridge is `outdated --json`, not a second implementation."""
    cli_out = CliRunner().invoke(cli, ["outdated", "--json"]).output
    assert rows(bridges.outdated(None, None, {})) == json.loads(cli_out)


def test_outdated_updates_only_matches_the_command(machine):
    cli_out = CliRunner().invoke(cli, ["outdated", "--json", "--updates-only"]).output
    got = rows(bridges.outdated(None, None, {"updates_only": True}))
    assert got == json.loads(cli_out)
    assert [r["key"] for r in got] == ["codex"]


def test_outdated_packages_checks_only_those_and_reports_a_missing_one(machine):
    got = rows(bridges.outdated(None, None, {"packages": ["eslint", "absent"]}))
    assert {r["key"]: r["state"] for r in got} == {"eslint": "current", "absent": "not-installed"}


def test_outdated_never_reports_a_failed_or_impossible_check_as_current(machine):
    """The honesty rule (FR-9): `unknown` and `unsupported` survive the trip through the bridge."""
    states = {r["key"]: r["state"] for r in rows(bridges.outdated(None, None, {}))}
    assert states["flaky"] == "unknown"
    assert states["starship"] == "unsupported"
    assert "current" not in (states["flaky"], states["starship"])


def test_outdated_unknown_key_is_a_tool_error_naming_it(machine):
    with pytest.raises(SandboxError, match="nope"):
        bridges.outdated(None, None, {"packages": ["eslint", "nope"]})


@pytest.mark.parametrize("bad", ["eslint", ["eslint", 3], 3, [["eslint"]]])
def test_outdated_rejects_a_packages_value_that_is_not_a_list_of_strings(machine, bad):
    # `bind()` checks names, not types, so the bridge is the one place that can refuse this.
    # A bare string must not be iterated into single characters.
    with pytest.raises(SandboxError, match="packages"):
        bridges.outdated(None, None, {"packages": bad})


def test_outdated_empty_packages_list_means_everything(machine):
    assert len(rows(bridges.outdated(None, None, {"packages": []}))) == len(
        rows(bridges.outdated(None, None, {}))
    )


@pytest.mark.parametrize("flag", ["yes", 1, [True]])
def test_outdated_rejects_a_non_boolean_updates_only(machine, flag):
    with pytest.raises(SandboxError, match="updates_only"):
        bridges.outdated(None, None, {"updates_only": flag})


# -- profile_diff (FR-10) -------------------------------------------------------------------


def test_profile_diff_matches_the_command_json_exactly(machine):
    cli_out = CliRunner().invoke(cli, ["profile", "diff", "@ai-agents", "--json"]).output
    assert rows(bridges.profile_diff(None, None, {"profile": "@ai-agents"})) == json.loads(cli_out)


def test_profile_diff_reports_the_machine_not_the_profile(machine):
    got = {r["key"]: r["state"] for r in rows(bridges.profile_diff(None, None, {"profile": "@ai-agents"}))}
    assert got["claude-code"] == "ok"
    assert got["codex"] == "ok"  # installed on the fake machine, unpinned
    assert got["devin"] == "unknown-key"  # not in this machine's catalog: never `ok`


@pytest.mark.parametrize(
    "arg",
    [
        "/etc/passwd", "../x", "./profile.yaml", "ai-agents", "ai-agents.yaml",
        "", "@", "@../x", "@/etc/passwd",
    ],
)
def test_profile_diff_refuses_anything_that_is_not_a_bundle_name(machine, arg):
    """FR-10: a path would let a client read any YAML the user can read."""
    with pytest.raises(SandboxError):
        bridges.profile_diff(None, None, {"profile": arg})


def test_profile_diff_unknown_bundle_lists_the_ones_that_exist(machine):
    with pytest.raises(SandboxError) as err:
        bridges.profile_diff(None, None, {"profile": "@nope"})
    for name in bundles.names():
        assert f"@{name}" in str(err.value)


def test_profile_diff_does_not_read_the_filesystem_for_the_name(machine, tmp_path, monkeypatch):
    """`@name` is matched against a directory listing, never joined into a path (bundles.py)."""
    (tmp_path / "x.yaml").write_text("version: 1\ntools: {}\n")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SandboxError):
        bridges.profile_diff(None, None, {"profile": "@x"})
    with pytest.raises(SandboxError):
        bridges.profile_diff(None, None, {"profile": "x.yaml"})


# -- FR-4: no workspace, no agent config ---------------------------------------------------

# One safe call per bridge. A bridge added to `_BRIDGES` without a row here fails the forcing
# test below, so "works with no workspace" cannot be skipped by forgetting to ask.
_SAFE_CALLS: dict[str, dict] = {
    "list_tools": {},
    "search_catalog": {"query": "codex"},
    "tool_info": {"key": "codex"},
    "outdated": {},
    "profile_diff": {"profile": "@ai-agents"},
    # Already installed, so nothing is installed: the point is only that it needs no workspace.
    "install_tool": {"key": "codex"},
}


def test_every_bridge_has_a_no_workspace_call():
    assert set(bridges._BRIDGES) == set(_SAFE_CALLS)


@pytest.mark.parametrize("name", sorted(_SAFE_CALLS))
def test_bridge_works_with_no_workspace_and_no_config(machine, name):
    out = bridges.get(name)(None, None, dict(_SAFE_CALLS[name]))
    assert isinstance(out, str) and out


def test_function_bridge_works_with_no_workspace_and_no_config(monkeypatch):
    from dev_setup import function_runner, functions_registry
    from dev_setup.functions_registry import FunctionDef

    fn = FunctionDef(key="probe", name="Probe", description="p", type="script", script="echo hi", params=[])
    monkeypatch.setattr(functions_registry, "init", lambda: None)
    monkeypatch.setattr(functions_registry, "get", lambda key: fn)
    monkeypatch.setattr(function_runner, "run_script_function", lambda *a, **k: "hi")
    assert bridges.run_function(None, None, "probe", {}) == "hi"


def test_bridges_never_name_a_workspace_primitive():
    """The surface MCP exposes must not be able to reach a primitive through a bridge."""
    assert not set(bridges._BRIDGES) & set(primitives._PRIMITIVES)
