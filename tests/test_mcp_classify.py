"""Which gate an MCP tool sits behind (docs/specs/mcp, FR-1, FR-1a, FR-3, F-10, SD-10).

The classification is by what an entry *runs* — `(impl, target)` — never by its key or by the
`mutating` field, because a user's `agent_tools.yaml` controls both of those (F-10a).
"""

from __future__ import annotations

import itertools

import pytest
import yaml

from dev_setup.agent import bridges
from dev_setup.agent import catalog as agent_catalog
from dev_setup.agent import registry as agent_registry
from dev_setup.agent.registry import AgentTool
from dev_setup.mcp.core import CATALOG_GATES, Gate, classify, is_exposed

FLAGS = list(itertools.product([False, True], repeat=2))  # (allow_install, allow_functions)


def entry(key="t", impl="catalog", target=None, mutating=False):
    return AgentTool(key=key, name=key, description="d", impl=impl, mutating=mutating, target=target)


# -- the table itself ---------------------------------------------------------------------


def test_every_bridge_has_a_gate():
    """Adding a bridge without deciding its gate fails here (the shape of
    `test_every_install_type_has_a_planner`)."""
    assert set(CATALOG_GATES) == set(bridges._BRIDGES)


def test_the_gate_of_each_bridge_is_the_documented_one():
    assert {t: g for t, g in CATALOG_GATES.items() if g is Gate.READ_ONLY} == {
        t: Gate.READ_ONLY for t in ("list_tools", "search_catalog", "tool_info", "outdated", "profile_diff")
    }
    assert CATALOG_GATES["install_tool"] is Gate.INSTALL
    assert Gate.FUNCTION not in CATALOG_GATES.values()  # functions are keyed by impl, not target


def test_exposure_per_gate_and_flag():
    for allow_install, allow_functions in FLAGS:
        kw = {"allow_install": allow_install, "allow_functions": allow_functions}
        assert is_exposed(Gate.READ_ONLY, **kw) is True
        assert is_exposed(Gate.INSTALL, **kw) is allow_install
        assert is_exposed(Gate.FUNCTION, **kw) is allow_functions
        # Never, whatever the flags: nothing on the command line can open these.
        assert is_exposed(Gate.UNCLASSIFIED, **kw) is False
        assert is_exposed(Gate.PRIMITIVE, **kw) is False


# -- classification -----------------------------------------------------------------------


@pytest.mark.parametrize("target", ["list_tools", "search_catalog", "tool_info", "outdated", "profile_diff"])
def test_read_only_bridges(target):
    assert classify(entry(target=target)) is Gate.READ_ONLY


def test_install_bridge():
    assert classify(entry(target="install_tool")) is Gate.INSTALL


def test_any_function_entry_is_the_function_class_whatever_it_says_about_itself():
    for mutating in (True, False):
        for key in ("fn_x", "runfn", "list_tools", "install_tool"):
            tool = entry(key=key, impl="function", target="aws-saml-reauth", mutating=mutating)
            assert classify(tool) is Gate.FUNCTION


def test_mutating_true_adds_a_gate_and_never_removes_one():
    # Read-only bridge flagged mutating: treated as the function class (FR-1a).
    assert classify(entry(target="list_tools", mutating=True)) is Gate.FUNCTION
    # The install class is not weakened by the flag in either direction.
    assert classify(entry(target="install_tool", mutating=True)) is Gate.INSTALL
    assert classify(entry(target="install_tool", mutating=False)) is Gate.INSTALL


def test_an_unknown_catalog_target_fails_closed():
    assert classify(entry(target="rm_everything")) is Gate.UNCLASSIFIED
    assert classify(entry(target=None)) is Gate.UNCLASSIFIED


def test_primitives_are_never_exposed():
    for key in ("read_file", "run_command", "list_tools"):  # a user may reuse a safe-looking key
        assert classify(entry(key=key, impl="primitive")) is Gate.PRIMITIVE


def test_an_unknown_impl_fails_closed():
    assert classify(entry(impl="plugin", target="x")) is Gate.UNCLASSIFIED


def test_the_key_never_decides():
    """A catalog entry named `install_tool` that runs a read-only bridge is read-only, and the
    reverse — the name is the user's to choose."""
    assert classify(entry(key="install_tool", target="list_tools")) is Gate.READ_ONLY
    assert classify(entry(key="list_tools", target="install_tool")) is Gate.INSTALL


# -- F-10a: the exact file that read as ungated ------------------------------------------

USER_FILE = {
    "version": 1,
    "tools": {
        # (1) override install_tool and declare it harmless
        "install_tool": {
            "description": "install",
            "impl": "catalog",
            "target": "install_tool",
            "mutating": False,
            "params": [{"name": "key", "type": "string", "required": True}],
        },
        # (2) the same bridge under another key
        "setup": {
            "description": "setup",
            "impl": "catalog",
            "target": "install_tool",
            "mutating": False,
            "params": [{"name": "key", "type": "string", "required": True}],
        },
        # (3) a hand-written function bridge to a credential-touching function
        "runfn": {
            "description": "run",
            "impl": "function",
            "target": "aws-saml-reauth",
            "mutating": False,
        },
    },
}


@pytest.fixture
def user_toolbox(tmp_path, monkeypatch):
    user = tmp_path / "agent_tools.yaml"
    user.write_text(yaml.safe_dump(USER_FILE))
    monkeypatch.setattr(agent_catalog, "USER_CATALOG_PATH", user)
    return agent_registry.build()


def test_the_user_file_reads_ungated_by_the_agents_own_label(user_toolbox):
    """The premise (F-10a): if this stopped being true the finding would need re-measuring."""
    assert [user_toolbox[k].mutating for k in ("install_tool", "setup", "runfn")] == [False] * 3
    assert user_toolbox["fn_aws_saml_reauth"].mutating is True  # only the auto-exposed one is labelled


@pytest.mark.parametrize(("allow_install", "allow_functions"), FLAGS)
def test_user_file_cannot_smuggle_a_tool_past_its_flag(user_toolbox, allow_install, allow_functions):
    exposed = {
        key
        for key, tool in user_toolbox.items()
        if is_exposed(classify(tool), allow_install=allow_install, allow_functions=allow_functions)
    }
    assert ("install_tool" in exposed) is allow_install
    assert ("setup" in exposed) is allow_install
    assert ("runfn" in exposed) is allow_functions
    assert ("fn_aws_saml_reauth" in exposed) is allow_functions
    # The read-only tools are there regardless.
    assert {"list_tools", "search_catalog", "tool_info", "outdated", "profile_diff"} <= exposed
    # And no primitive ever is.
    assert not exposed & {"read_file", "write_file", "list_dir", "cd", "run_command"}


def test_the_bundled_toolbox_has_no_unclassified_entry():
    """If a bundled entry fell through to UNCLASSIFIED the server would silently lack a tool."""
    gates = {key: classify(tool) for key, tool in agent_registry.build().items()}
    assert Gate.UNCLASSIFIED not in gates.values()
    assert {k for k, g in gates.items() if g is Gate.INSTALL} == {"install_tool"}
    assert all(g is Gate.FUNCTION for k, g in gates.items() if k.startswith("fn_"))
