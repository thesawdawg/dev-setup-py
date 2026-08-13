from __future__ import annotations

import pytest
import yaml
from click.testing import CliRunner

from dev_setup import catalog, registry, ui
from dev_setup.catalog import CatalogError
from dev_setup.cli import cli
from dev_setup.generic import GenericTool


@pytest.fixture()
def isolated_catalog(tmp_path, monkeypatch):
    config = tmp_path / "config"
    monkeypatch.setattr(catalog, "CONFIG_DIR", config)
    monkeypatch.setattr(catalog, "USER_CATALOG_PATH", config / "tools.yaml")
    registry._registry.clear()
    registry._order.clear()
    registry._initialized = False
    yield config
    registry._registry.clear()
    registry._order.clear()
    registry._initialized = False


def test_bundled_catalog_loads_expected_builtin_keys(isolated_catalog):
    tools = catalog.load_bundled_catalog()

    assert {
        "docker",
        "nvm",
        "uv",
        "go",
        "java",
        "ruby",
        "pi",
        "aws",
    }.issubset(tools)


def test_user_catalog_overrides_bundled_tool_in_place(isolated_catalog):
    catalog.write_user_catalog(
        {
            "docker": {
                "name": "Custom Docker",
                "description": "override",
                "type": "bash",
                "check_cmd": "docker",
            },
            "localtool": {
                "name": "Local Tool",
                "description": "appended",
                "type": "bash",
                "check_cmd": "localtool",
            },
        }
    )

    tools = registry.all_tools()
    keys = [tool.key for tool in tools]

    assert registry.get("docker").name == "Custom Docker"  # type: ignore[union-attr]
    assert keys.index("docker") < keys.index("nvm")
    assert keys[-1] == "localtool"


def test_invalid_catalog_reports_unknown_field(isolated_catalog):
    path = catalog.USER_CATALOG_PATH
    path.parent.mkdir(parents=True)
    path.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "tools": {
                    "bad": {
                        "name": "Bad",
                        "description": "",
                        "type": "bash",
                        "check_cmd": "bad",
                        "mystery": True,
                    }
                },
            }
        )
    )

    with pytest.raises(CatalogError, match="unknown field"):
        catalog.read_user_catalog()


def test_generic_tool_save_writes_user_yaml(isolated_catalog):
    tool = GenericTool(
        key="saved",
        name="Saved",
        description="from tool",
        install_type="bash",
        check_cmd="saved",
        install_script="true",
    )

    tool.save()

    data = yaml.safe_load(catalog.USER_CATALOG_PATH.read_text())
    assert data["tools"]["saved"]["name"] == "Saved"


def test_catalog_export_and_import_commands(isolated_catalog, tmp_path):
    runner = CliRunner()
    export_path = tmp_path / "effective.yaml"

    result = runner.invoke(cli, ["catalog", "export", str(export_path)])
    assert result.exit_code == 0
    exported = yaml.safe_load(export_path.read_text())
    assert "docker" in exported["tools"]

    import_path = tmp_path / "import.yaml"
    import_path.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "tools": {
                    "imported": {
                        "name": "Imported",
                        "description": "from import",
                        "type": "bash",
                        "check_cmd": "imported",
                        "install_script": "true",
                    }
                },
            },
            sort_keys=False,
        )
    )

    result = runner.invoke(cli, ["catalog", "import", str(import_path)])
    assert result.exit_code == 0
    assert catalog.read_user_catalog()["imported"]["name"] == "Imported"


def test_delete_removes_user_override_and_restores_builtin(isolated_catalog, monkeypatch):
    catalog.write_user_catalog(
        {
            "docker": {
                "name": "Custom Docker",
                "description": "override",
                "type": "bash",
                "check_cmd": "docker",
            }
        }
    )
    registry.reload()
    monkeypatch.setattr(ui, "confirm", lambda *args, **kwargs: True)

    result = CliRunner().invoke(cli, ["delete", "docker"])

    assert result.exit_code == 0
    assert not catalog.user_has_tool("docker")
    assert registry.get("docker").name == "Docker"  # type: ignore[union-attr]


# -- uv_* fields (uvx/pip only) -----------------------------------------------


def _uv_catalog(**extra):
    return {
        "version": 1,
        "tools": {"t": {"name": "T", "type": "uvx", "pip_name": "p", **extra}},
    }


def test_uv_fields_accepted_on_uvx():
    tools = catalog.validate_catalog(
        _uv_catalog(
            uv_with=["jmespath"],
            uv_executables_from=["ansible-core"],
            uv_python="3.12",
        )
    )

    assert tools["t"]["uv_with"] == ["jmespath"]
    assert tools["t"]["uv_executables_from"] == ["ansible-core"]
    assert tools["t"]["uv_python"] == "3.12"


@pytest.mark.parametrize("field", ["uv_with", "uv_executables_from", "uv_python"])
@pytest.mark.parametrize("bad_type", ["apt", "bash", "npm"])
def test_uv_fields_rejected_on_other_types(field, bad_type):
    value = "3.12" if field == "uv_python" else ["x"]
    raw = _uv_catalog(**{field: value})
    raw["tools"]["t"]["type"] = bad_type

    with pytest.raises(CatalogError, match="only valid on type"):
        catalog.validate_catalog(raw)


@pytest.mark.parametrize(
    "field,value",
    [
        ("uv_with", "not-a-list"),
        ("uv_with", [1]),
        ("uv_executables_from", "not-a-list"),
        ("uv_python", ["3.12"]),
    ],
)
def test_uv_fields_reject_wrong_shapes(field, value):
    with pytest.raises(CatalogError, match=field):
        catalog.validate_catalog(_uv_catalog(**{field: value}))


def test_uv_fields_round_trip_through_generic_tool():
    data = {
        "name": "T",
        "type": "uvx",
        "pip_name": "ansible",
        "uv_with": ["jmespath"],
        "uv_executables_from": ["ansible-core"],
        "uv_python": "3.12",
    }

    tool = GenericTool.from_dict(data, "t")

    assert tool.uv_executables_from == ["ansible-core"]
    assert tool.to_dict()["uv_with"] == ["jmespath"]
    assert tool.to_dict()["uv_executables_from"] == ["ansible-core"]
    assert tool.to_dict()["uv_python"] == "3.12"


def test_unset_uv_fields_are_not_persisted():
    tool = GenericTool.from_dict({"name": "T", "type": "uvx", "pip_name": "p"}, "t")

    d = tool.to_dict()

    assert "uv_with" not in d
    assert "uv_executables_from" not in d
    assert "uv_python" not in d


# -- CI matrix / catalog drift ---------------------------------------------------


def test_ci_matrix_covers_every_builtin_tool():
    """The weekly install canary must name exactly the builtin tools it can test.

    Drift here is silent in both directions: a tool added to the catalog is simply
    never install-tested, and a tool *removed* from the catalog leaves a matrix entry
    whose pytest node id matches nothing — pytest exits 4, the job fails every week,
    and the workflow files a GitHub issue about it. Both happened between commit
    a1a5126 (which deleted `eza` and renamed `whichllm`) and this test.
    """
    import re
    from pathlib import Path

    from tests.integration.test_tools import _SKIP

    workflow = Path(__file__).parent.parent / ".github/workflows/test-installs.yml"
    matrix = set(re.findall(r"^ +- ([a-z0-9][a-z0-9_-]*)\s*(?:#.*)?$", workflow.read_text(), re.M))

    registry.init()
    testable = {t.key for t in registry.all_tools() if t.builtin} - set(_SKIP)

    assert matrix - testable == set(), (
        f"CI matrix names tools that are not in the catalog: {sorted(matrix - testable)}"
    )
    assert testable - matrix == set(), (
        f"builtin tools missing from the CI matrix: {sorted(testable - matrix)}"
    )
