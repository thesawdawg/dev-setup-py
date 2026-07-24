from __future__ import annotations

import pytest
import yaml

from dev_setup.agent import catalog as agent_catalog
from dev_setup.agent import primitives, registry
from dev_setup.agent.config import AgentConfig
from dev_setup.agent.registry import AgentParam, AgentTool
from dev_setup.agent.sandbox import SandboxError, Workspace
from dev_setup.catalog import CatalogError


@pytest.fixture()
def ws(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    return Workspace.create(root)


@pytest.fixture()
def cfg():
    return AgentConfig()


def catalog_yaml(**tools):
    return {"version": 1, "tools": tools}


# -- catalog validation ----------------------------------------------------------


def test_bundled_catalog_is_valid():
    catalog = agent_catalog.load_bundled_catalog()
    assert "run_command" in catalog["tools"]
    assert catalog["tools"]["run_command"]["mutating"] is True
    assert catalog["tools"]["read_file"]["mutating"] is False


def test_rejects_wrong_version():
    with pytest.raises(CatalogError, match="version must be 1"):
        agent_catalog.validate_catalog({"version": 2, "tools": {}})


def test_rejects_unknown_top_level_field():
    with pytest.raises(CatalogError, match="unknown top-level field"):
        agent_catalog.validate_catalog({"version": 1, "toolz": {}})


def test_rejects_unknown_tool_field():
    raw = catalog_yaml(t={"description": "d", "impl": "primitive", "colour": "red"})
    with pytest.raises(CatalogError, match="unknown field"):
        agent_catalog.validate_catalog(raw)


def test_rejects_missing_description():
    """The description is what the model reads to choose the tool."""
    raw = catalog_yaml(t={"impl": "primitive"})
    with pytest.raises(CatalogError, match="needs a description"):
        agent_catalog.validate_catalog(raw)


def test_rejects_unknown_impl():
    raw = catalog_yaml(t={"description": "d", "impl": "magic"})
    with pytest.raises(CatalogError, match="unsupported impl"):
        agent_catalog.validate_catalog(raw)


def test_rejects_bridge_without_target():
    raw = catalog_yaml(t={"description": "d", "impl": "catalog"})
    with pytest.raises(CatalogError, match="no 'target'"):
        agent_catalog.validate_catalog(raw)


def test_rejects_primitive_with_target():
    """A target on a primitive would be silently ignored, so it is an error."""
    raw = catalog_yaml(t={"description": "d", "impl": "primitive", "target": "x"})
    with pytest.raises(CatalogError, match="cannot take a 'target'"):
        agent_catalog.validate_catalog(raw)


def test_rejects_bad_param_type():
    raw = catalog_yaml(
        t={"description": "d", "impl": "primitive", "params": [{"name": "p", "type": "date"}]}
    )
    with pytest.raises(CatalogError, match="unsupported type"):
        agent_catalog.validate_catalog(raw)


def test_rejects_duplicate_param():
    raw = catalog_yaml(
        t={"description": "d", "impl": "primitive", "params": [{"name": "p"}, {"name": "p"}]}
    )
    with pytest.raises(CatalogError, match="duplicate param"):
        agent_catalog.validate_catalog(raw)


def test_rejects_invalid_key():
    raw = catalog_yaml(**{"Bad-Key": {"description": "d", "impl": "primitive"}})
    with pytest.raises(CatalogError, match="invalid tool key"):
        agent_catalog.validate_catalog(raw)


def test_user_catalog_overrides_bundled_tool_in_place(tmp_path, monkeypatch):
    user = tmp_path / "agent_tools.yaml"
    user.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "tools": {
                    "read_file": {"description": "my override", "impl": "primitive"},
                    "my_tool": {"description": "new one", "impl": "primitive"},
                },
            }
        )
    )
    monkeypatch.setattr(agent_catalog, "USER_CATALOG_PATH", user)

    effective = agent_catalog.load_effective_catalog()
    assert effective["tools"]["read_file"]["description"] == "my override"
    assert "my_tool" in effective["tools"]
    assert "run_command" in effective["tools"]  # bundled entries survive


def test_invalid_user_catalog_fails_loudly(tmp_path, monkeypatch):
    user = tmp_path / "agent_tools.yaml"
    user.write_text("version: 1\ntools:\n  bad:\n    impl: primitive\n")
    monkeypatch.setattr(agent_catalog, "USER_CATALOG_PATH", user)
    with pytest.raises(CatalogError):
        agent_catalog.load_effective_catalog()


# -- schema generation -----------------------------------------------------------


def test_to_schema_matches_ollama_tool_format():
    tool = AgentTool(
        key="write_file",
        name="Write",
        description="Write a file.",
        impl="primitive",
        mutating=True,
        params=[
            AgentParam(name="path", description="Where", required=True),
            AgentParam(name="mode", type="string", enum=["a", "w"], required=False),
        ],
    )
    schema = tool.to_schema()
    assert schema["type"] == "function"
    assert schema["function"]["name"] == "write_file"
    assert schema["function"]["parameters"]["required"] == ["path"]
    assert schema["function"]["parameters"]["properties"]["mode"]["enum"] == ["a", "w"]


def test_bind_fills_defaults():
    tool = AgentTool("t", "T", "d", "primitive", params=[AgentParam(name="path", default=".")])
    assert tool.bind({}) == {"path": "."}


def test_bind_rejects_missing_required():
    tool = AgentTool("t", "T", "d", "primitive", params=[AgentParam(name="path", required=True)])
    with pytest.raises(ValueError, match="missing required parameter"):
        tool.bind({})


def test_bind_rejects_unexpected_parameter():
    tool = AgentTool("t", "T", "d", "primitive", params=[AgentParam(name="path")])
    with pytest.raises(ValueError, match="unexpected parameter"):
        tool.bind({"path": ".", "recursive": True})


def test_bind_treats_explicit_null_as_absent():
    """Models routinely send null for optional arguments."""
    tool = AgentTool("t", "T", "d", "primitive", params=[AgentParam(name="path", default=".")])
    assert tool.bind({"path": None}) == {"path": "."}


# -- registry assembly -----------------------------------------------------------


def test_build_includes_primitives_and_bridges():
    tools = registry.build()
    assert {"read_file", "write_file", "list_dir", "cd", "run_command"} <= set(tools)
    assert tools["install_tool"].impl == "catalog"
    assert tools["install_tool"].target == "install_tool"


def test_build_exposes_script_functions_but_not_shell_eval():
    """shell-eval functions exist to mutate the calling shell, which a subprocess
    tool call cannot do -- the same reason `devstuff run` refuses them."""
    tools = registry.build()
    assert "fn_validate_yaml" in tools  # type: script
    assert "fn_ssh_agent_key" not in tools  # type: shell-eval


def test_exposed_functions_are_marked_mutating():
    assert registry.build()["fn_validate_yaml"].mutating is True


# -- primitives ------------------------------------------------------------------


def test_write_then_read_round_trip(ws, cfg):
    primitives.write_file(ws, cfg, {"path": "a.txt", "content": "hello\n"})
    assert primitives.read_file(ws, cfg, {"path": "a.txt"}) == "hello\n"


def test_write_file_creates_parent_directories(ws, cfg):
    primitives.write_file(ws, cfg, {"path": "src/pkg/main.py", "content": "x = 1\n"})
    assert (ws.root / "src" / "pkg" / "main.py").exists()


def test_write_file_cannot_escape(ws, cfg):
    with pytest.raises(SandboxError, match="escapes the workspace"):
        primitives.write_file(ws, cfg, {"path": "../evil.txt", "content": "x"})


def test_read_file_reports_missing_file(ws, cfg):
    with pytest.raises(SandboxError, match="no such file"):
        primitives.read_file(ws, cfg, {"path": "ghost.txt"})


def test_read_file_rejects_a_directory(ws, cfg):
    (ws.root / "sub").mkdir()
    with pytest.raises(SandboxError, match="use list_dir"):
        primitives.read_file(ws, cfg, {"path": "sub"})


def test_read_file_rejects_binary(ws, cfg):
    (ws.root / "a.bin").write_bytes(b"\xff\xfe\x00\x01")
    with pytest.raises(SandboxError, match="not a UTF-8"):
        primitives.read_file(ws, cfg, {"path": "a.bin"})


def test_list_dir_marks_directories(ws, cfg):
    (ws.root / "sub").mkdir()
    (ws.root / "a.txt").write_text("x")
    out = primitives.list_dir(ws, cfg, {"path": "."})
    assert "sub/" in out
    assert "a.txt" in out


def test_list_dir_defaults_to_current_directory(ws, cfg):
    (ws.root / "a.txt").write_text("x")
    assert "a.txt" in primitives.list_dir(ws, cfg, {})


def test_cd_moves_and_affects_later_calls(ws, cfg):
    (ws.root / "sub").mkdir()
    primitives.cd(ws, cfg, {"path": "sub"})
    primitives.write_file(ws, cfg, {"path": "inner.txt", "content": "x"})
    assert (ws.root / "sub" / "inner.txt").exists()


def test_run_command_returns_output(ws, cfg):
    assert "hello" in primitives.run_command(ws, cfg, {"command": "echo hello"})


def test_run_command_runs_in_the_workspace(ws, cfg):
    out = primitives.run_command(ws, cfg, {"command": "pwd"})
    assert str(ws.root) in out


def test_run_command_reports_exit_code_without_raising(ws, cfg):
    """A failing command is information the model needs, not a sandbox refusal."""
    out = primitives.run_command(ws, cfg, {"command": "exit 3"})
    assert "exit code 3" in out


def test_run_command_enforces_the_denylist(ws, cfg):
    with pytest.raises(SandboxError, match="not permitted"):
        primitives.run_command(ws, cfg, {"command": "sudo whoami"})


def test_run_command_times_out(ws):
    config = AgentConfig(command_timeout=1)
    with pytest.raises(SandboxError, match="timed out"):
        primitives.run_command(ws, config, {"command": "sleep 5"})


# -- function bridge -------------------------------------------------------------


def _fake_function(monkeypatch, params):
    """Register a throwaway script function and capture the argv it receives."""
    from dev_setup import function_runner, functions_registry
    from dev_setup.functions_registry import FunctionDef, FunctionParam

    fn = FunctionDef(
        key="probe",
        name="Probe",
        description="probe",
        type="script",
        script="echo hi",
        params=[FunctionParam(**p) for p in params],
    )
    monkeypatch.setattr(functions_registry, "get", lambda key: fn if key == "probe" else None)
    monkeypatch.setattr(functions_registry, "init", lambda: None)

    captured = {}

    def fake_run(f, args, prompt=None, capture=False):
        captured["args"] = args
        return ""

    monkeypatch.setattr(function_runner, "run_script_function", fake_run)
    return captured


def test_function_bridge_passes_arguments_in_declared_order(ws, cfg, monkeypatch):
    from dev_setup.agent import bridges

    captured = _fake_function(
        monkeypatch, [{"name": "url"}, {"name": "instruction"}]
    )
    bridges.run_function(ws, cfg, "probe", {"url": "http://x", "instruction": "go"})
    assert captured["args"] == ("http://x", "go")


def test_function_bridge_keeps_positions_when_an_argument_is_omitted(ws, cfg, monkeypatch):
    """function_runner maps values to params by position, so a missing early
    argument must hold its slot rather than shifting later ones left."""
    from dev_setup.agent import bridges

    captured = _fake_function(
        monkeypatch, [{"name": "url"}, {"name": "instruction"}]
    )
    bridges.run_function(ws, cfg, "probe", {"instruction": "go"})
    assert captured["args"] == ("", "go")


def test_function_bridge_reports_unknown_function(ws, cfg, monkeypatch):
    from dev_setup.agent import bridges

    _fake_function(monkeypatch, [])
    with pytest.raises(SandboxError, match="unknown function"):
        bridges.run_function(ws, cfg, "nope", {})


def test_function_bridge_surfaces_missing_required_param(ws, cfg, monkeypatch):
    """Raised as a tool error the model can correct, not a crash."""
    from dev_setup import function_runner, functions_registry
    from dev_setup.agent import bridges
    from dev_setup.functions_registry import FunctionDef, FunctionParam

    fn = FunctionDef(
        key="probe", name="Probe", description="probe", type="script", script="echo hi",
        params=[FunctionParam(name="file", required=True)],
    )
    monkeypatch.setattr(functions_registry, "get", lambda key: fn)
    monkeypatch.setattr(functions_registry, "init", lambda: None)

    def fake_run(f, args, prompt=None, capture=False):
        function_runner.resolve_params(f.params, args, prompt=prompt)
        return ""

    monkeypatch.setattr(function_runner, "run_script_function", fake_run)

    with pytest.raises(SandboxError, match="Missing required parameter"):
        bridges.run_function(ws, cfg, "probe", {})


def test_exposed_function_schema_carries_its_params():
    tools = registry.build()
    acc = tools["fn_acc_check"]
    props = acc.to_schema()["function"]["parameters"]["properties"]
    assert set(props) == {"url", "instruction"}


def test_excluded_functions_are_withheld(tmp_path, monkeypatch):
    user = tmp_path / "agent_tools.yaml"
    user.write_text("version: 1\nexclude_functions: [validate-yaml]\ntools: {}\n")
    monkeypatch.setattr(agent_catalog, "USER_CATALOG_PATH", user)
    tools = registry.build()
    assert "fn_validate_yaml" not in tools
    assert "fn_acc_check" in tools


def test_expose_functions_can_be_turned_off(tmp_path, monkeypatch):
    user = tmp_path / "agent_tools.yaml"
    user.write_text("version: 1\nexpose_functions: false\ntools: {}\n")
    monkeypatch.setattr(agent_catalog, "USER_CATALOG_PATH", user)
    tools = registry.build()
    assert not [k for k in tools if k.startswith("fn_")]
    assert "read_file" in tools


def test_function_bridge_returns_script_output_to_the_model(ws, cfg, monkeypatch):
    from dev_setup import functions_registry
    from dev_setup.agent import bridges
    from dev_setup.functions_registry import FunctionDef

    fn = FunctionDef(
        key="probe", name="Probe", description="d", type="script",
        script="echo 'the actual output'", params=[],
    )
    monkeypatch.setattr(functions_registry, "get", lambda key: fn)
    monkeypatch.setattr(functions_registry, "init", lambda: None)
    assert "the actual output" in bridges.run_function(ws, cfg, "probe", {})


def test_function_bridge_includes_diagnostics_on_failure(ws, cfg, monkeypatch):
    """A guard message like "yq is required" must survive into the tool error,
    or the model invents a cause for the bare exit code."""
    from dev_setup import functions_registry
    from dev_setup.agent import bridges
    from dev_setup.functions_registry import FunctionDef

    fn = FunctionDef(
        key="probe", name="Probe", description="d", type="script",
        script="echo 'yq is required. Install it first.'; exit 1", params=[],
    )
    monkeypatch.setattr(functions_registry, "get", lambda key: fn)
    monkeypatch.setattr(functions_registry, "init", lambda: None)

    with pytest.raises(SandboxError, match="yq is required"):
        bridges.run_function(ws, cfg, "probe", {})
