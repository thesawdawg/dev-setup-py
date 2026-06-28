from __future__ import annotations

import copy
import re
from importlib import resources
from pathlib import Path
from typing import Any

import yaml

CONFIG_DIR = Path.home() / ".config" / "dev-setup"
USER_CATALOG_PATH = CONFIG_DIR / "tools.yaml"
BUNDLED_CATALOG = "tools.yaml"

VERSION = 1
VALID_KEY = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
SUPPORTED_FIELDS = {
    "name",
    "description",
    "category",
    "type",
    "check_cmd",
    "version_cmd",
    "help_cmd",
    "docs_url",
    "requires",
    "npm_name",
    "pip_name",
    "apt_packages",
    "git_url",
    "git_install_cmd",
    "git_remove_cmd",
    "script_url",
    "install_script",
    "remove_script",
}


class CatalogError(RuntimeError):
    pass


def bundled_catalog_path() -> str:
    return f"dev_setup/{BUNDLED_CATALOG}"


def load_catalog_file(path: Path, *, required: bool = False) -> dict[str, dict[str, Any]]:
    if not path.exists():
        if required:
            raise CatalogError(f"Catalog file not found: {path}")
        return {}

    try:
        raw = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as exc:
        raise CatalogError(f"Invalid YAML in {path}: {exc}") from exc

    return validate_catalog(raw, source=path)


def validate_catalog(raw: Any, *, source: Path | str = "<catalog>") -> dict[str, dict[str, Any]]:
    if not isinstance(raw, dict):
        raise CatalogError(f"{source}: catalog must be a mapping")
    if raw.get("version") != VERSION:
        raise CatalogError(f"{source}: version must be {VERSION}")

    tools = raw.get("tools")
    if tools is None:
        return {}
    if not isinstance(tools, dict):
        raise CatalogError(f"{source}: tools must be a mapping")

    validated: dict[str, dict[str, Any]] = {}
    for key, data in tools.items():
        if not isinstance(key, str) or not VALID_KEY.match(key):
            raise CatalogError(f"{source}: invalid tool key {key!r}")
        if not isinstance(data, dict):
            raise CatalogError(f"{source}: tool {key!r} must be a mapping")

        unknown = sorted(set(data) - SUPPORTED_FIELDS)
        if unknown:
            fields = ", ".join(unknown)
            raise CatalogError(f"{source}: tool {key!r} has unknown field(s): {fields}")

        item = copy.deepcopy(data)
        item.setdefault("name", key)
        item.setdefault("description", "")
        item.setdefault("category", "custom")
        item.setdefault("type", "unknown")

        requires = item.get("requires")
        if requires is None:
            if item["type"] == "npm":
                item["requires"] = ["nvm"]
            elif item["type"] in ("pip", "uvx"):
                item["requires"] = ["uv"]
            else:
                item["requires"] = []
        elif not isinstance(requires, list) or not all(isinstance(v, str) for v in requires):
            raise CatalogError(f"{source}: tool {key!r} requires must be a list of strings")

        validated[key] = item

    return validated


def catalog_document(tools: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {"version": VERSION, "tools": tools}


def read_user_catalog() -> dict[str, dict[str, Any]]:
    return load_catalog_file(USER_CATALOG_PATH)


def write_user_catalog(tools: dict[str, dict[str, Any]]) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    USER_CATALOG_PATH.write_text(_dump(catalog_document(tools)))


def load_bundled_catalog() -> dict[str, dict[str, Any]]:
    resource = resources.files("dev_setup").joinpath(BUNDLED_CATALOG)
    try:
        raw = yaml.safe_load(resource.read_text()) or {}
    except FileNotFoundError as exc:
        raise CatalogError(f"Catalog file not found: {bundled_catalog_path()}") from exc
    except yaml.YAMLError as exc:
        raise CatalogError(f"Invalid YAML in {bundled_catalog_path()}: {exc}") from exc
    return validate_catalog(raw, source=bundled_catalog_path())


def load_effective_catalog() -> tuple[
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
]:
    bundled = load_bundled_catalog()
    user = read_user_catalog()
    effective = merge_catalogs(bundled, user)
    return effective, bundled, user


def merge_catalogs(
    bundled: dict[str, dict[str, Any]],
    user: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    merged = copy.deepcopy(bundled)
    for key, data in user.items():
        merged[key] = copy.deepcopy(data)
    return merged


def save_user_tool(key: str, data: dict[str, Any]) -> None:
    validate_catalog(catalog_document({key: data}), source=USER_CATALOG_PATH)
    user = read_user_catalog()
    user[key] = copy.deepcopy(data)
    write_user_catalog(user)


def delete_user_tool(key: str) -> bool:
    user = read_user_catalog()
    if key not in user:
        return False
    del user[key]
    write_user_catalog(user)
    return True


def user_has_tool(key: str) -> bool:
    return key in read_user_catalog()


def import_catalog(path: Path) -> list[str]:
    incoming = load_catalog_file(path, required=True)
    user = read_user_catalog()
    for key, data in incoming.items():
        user[key] = copy.deepcopy(data)
    write_user_catalog(user)
    return list(incoming)


def export_catalog(path: Path) -> None:
    effective, _bundled, _user = load_effective_catalog()
    path.write_text(_dump(catalog_document(effective)))


def _dump(data: dict[str, Any]) -> str:
    return yaml.safe_dump(data, sort_keys=False, default_flow_style=False)
