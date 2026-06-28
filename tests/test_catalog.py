from __future__ import annotations

from pathlib import Path

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
