from __future__ import annotations

import json
from importlib import resources

import pytest
from click.testing import CliRunner

from dev_setup import links_catalog as catalog
from dev_setup import links_registry as registry
from dev_setup.commands import links_cmd as cmd
from dev_setup.links_catalog import CatalogError
from dev_setup.links_registry import LinkDef


@pytest.fixture()
def isolated_catalog(tmp_path, monkeypatch):
    config = tmp_path / "config"
    monkeypatch.setattr(catalog, "CONFIG_DIR", config)
    monkeypatch.setattr(catalog, "USER_CATALOG_PATH", config / "links.yaml")
    monkeypatch.setattr(cmd, "USER_CATALOG_PATH", config / "links.yaml")
    registry._registry.clear()
    registry._initialized = False
    yield config
    registry._registry.clear()
    registry._initialized = False


def _link(url: str = "https://example.com", **extra) -> dict:
    return {"version": 1, "links": {"x": {"url": url, **extra}}}


# -- catalog validation ----------------------------------------------------------


def test_bundled_catalog_loads(isolated_catalog):
    links = catalog.load_bundled_catalog()
    assert "uv-docs" in links
    assert all(v["url"].startswith("https://") for v in links.values())


def test_defaults_applied(isolated_catalog):
    item = catalog.validate_catalog(_link())["x"]
    assert (item["name"], item["description"], item["category"]) == ("x", "", "custom")


@pytest.mark.parametrize(
    "url",
    ["file:///etc/passwd", "javascript:alert(1)", "ftp://example.com", "example.com", "https://", ""],
)
def test_non_web_or_malformed_urls_rejected(url):
    with pytest.raises(CatalogError):
        catalog.validate_catalog(_link(url))


def test_url_is_required():
    with pytest.raises(CatalogError, match="url"):
        catalog.validate_catalog({"version": 1, "links": {"x": {"name": "X"}}})


def test_unknown_field_and_bad_key_rejected():
    with pytest.raises(CatalogError, match="unknown field"):
        catalog.validate_catalog(_link(tags=["a"]))
    with pytest.raises(CatalogError, match="invalid link key"):
        catalog.validate_catalog({"version": 1, "links": {"Bad Key": {"url": "https://a.b"}}})


def test_wrong_version_rejected():
    with pytest.raises(CatalogError, match="version"):
        catalog.validate_catalog({"version": 2, "links": {}})


def test_user_catalog_overrides_bundled_in_place(isolated_catalog):
    isolated_catalog.mkdir(parents=True)
    (isolated_catalog / "links.yaml").write_text(
        "version: 1\nlinks:\n  uv-docs:\n    name: Mine\n    url: https://mine.example\n"
        "  extra:\n    url: https://extra.example\n"
    )
    registry.reload()
    assert registry.get("uv-docs").name == "Mine"
    assert registry.get("uv-docs").builtin is False
    assert registry.get("extra").category == "custom"
    assert registry.get("docker-docs").builtin is True


# -- search ----------------------------------------------------------------------


LINKS = [
    LinkDef(key="a", name="Docker Docs", description="Containers"),
    LinkDef(key="b", name="Hub", description="Search public docker images"),
    LinkDef(key="c", name="Starship", description="Shell prompt"),
]


def test_search_matches_title_and_description_case_insensitively():
    assert [m.key for m in registry.search("DOCKER", links=LINKS)] == ["a", "b"]


def test_search_ranks_title_matches_first():
    links = [LinkDef(key="b", name="Hub", description="docker images"), LinkDef(key="a", name="Docker")]
    assert [m.key for m in registry.search("docker", links=links)] == ["a", "b"]


def test_search_terms_are_anded_across_title_and_description():
    assert [m.key for m in registry.search("docker images", links=LINKS)] == ["b"]
    assert registry.search("docker shell", links=LINKS) == []


def test_search_ignores_key_and_blank_query():
    assert registry.search("a", links=[LinkDef(key="zzz", name="Q", description="")]) == []
    assert registry.search("   ", links=LINKS) == []


# -- commands --------------------------------------------------------------------


def test_list_groups_by_category(isolated_catalog):
    result = CliRunner().invoke(cmd.links_cmd, ["list"])
    assert result.exit_code == 0
    assert "uv-docs" in result.output and "PYTHON" in result.output


def test_list_unknown_category_is_not_an_error(isolated_catalog):
    result = CliRunner().invoke(cmd.links_cmd, ["list", "nope"])
    assert result.exit_code == 0
    assert "No links match" in result.output


def test_search_command_and_no_results_exit_zero(isolated_catalog):
    runner = CliRunner()
    hit = runner.invoke(cmd.links_cmd, ["search", "docker", "images"])
    assert hit.exit_code == 0 and "docker-hub" in hit.output
    miss = runner.invoke(cmd.links_cmd, ["search", "zzzzqqq"])
    assert miss.exit_code == 0 and "No links match" in miss.output


def test_open_launches_browser_with_url(isolated_catalog, monkeypatch):
    opened: list[str] = []
    monkeypatch.setattr(cmd.webbrowser, "open", lambda url: opened.append(url) or True)
    result = CliRunner().invoke(cmd.links_cmd, ["open", "uv-docs"])
    assert result.exit_code == 0
    assert opened == ["https://docs.astral.sh/uv/"]


def test_open_without_browser_prints_url_and_fails(isolated_catalog, monkeypatch):
    monkeypatch.setattr(cmd.webbrowser, "open", lambda url: False)
    result = CliRunner().invoke(cmd.links_cmd, ["open", "uv-docs"])
    assert result.exit_code == 1
    assert "https://docs.astral.sh/uv/" in result.output


def test_open_unknown_key_suggests_matches(isolated_catalog, monkeypatch):
    monkeypatch.setattr(cmd.webbrowser, "open", lambda url: pytest.fail("must not open"))
    result = CliRunner().invoke(cmd.links_cmd, ["open", "docker"])
    assert result.exit_code == 1
    assert "Unknown link" in result.output and "docker-docs" in result.output


def test_bare_command_lists_when_not_a_tty(isolated_catalog):
    result = CliRunner().invoke(cmd.links_cmd, [])  # CliRunner stdin is not a tty
    assert result.exit_code == 0 and "uv-docs" in result.output


# -- schema/code sync ------------------------------------------------------------


def test_schema_fields_match_supported_fields():
    raw = resources.files("dev_setup").joinpath("links.schema.json").read_text()
    documented = set(json.loads(raw)["definitions"]["link"]["properties"])
    assert documented == catalog.SUPPORTED_FIELDS
