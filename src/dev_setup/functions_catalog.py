from __future__ import annotations

import copy
import re
from importlib import resources
from pathlib import Path
from typing import Any

import yaml

from dev_setup.catalog import CONFIG_DIR, CatalogError

USER_CATALOG_PATH = CONFIG_DIR / "functions.yaml"
BUNDLED_CATALOG = "functions.yaml"

VERSION = 1
VALID_KEY = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
VALID_PARAM_NAME = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")

TYPES = {"script", "shell-eval"}
REGISTER_MODES = {"bashrc", "eval"}

SUPPORTED_FIELDS = {
    "name",
    "description",
    "category",
    "type",
    "register",
    "params",
    "script",
    "help_cmd",
    "docs_url",
}
SUPPORTED_PARAM_FIELDS = {"name", "description", "required", "default"}


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

    functions = raw.get("functions")
    if functions is None:
        return {}
    if not isinstance(functions, dict):
        raise CatalogError(f"{source}: functions must be a mapping")

    validated: dict[str, dict[str, Any]] = {}
    for key, data in functions.items():
        if not isinstance(key, str) or not VALID_KEY.match(key):
            raise CatalogError(f"{source}: invalid function key {key!r}")
        if not isinstance(data, dict):
            raise CatalogError(f"{source}: function {key!r} must be a mapping")

        unknown = sorted(set(data) - SUPPORTED_FIELDS)
        if unknown:
            fields = ", ".join(unknown)
            raise CatalogError(f"{source}: function {key!r} has unknown field(s): {fields}")

        item = copy.deepcopy(data)
        item.setdefault("name", key)
        item.setdefault("description", "")
        item.setdefault("category", "custom")

        fn_type = item.get("type")
        if fn_type not in TYPES:
            raise CatalogError(
                f"{source}: function {key!r} type must be one of {sorted(TYPES)}, got {fn_type!r}"
            )

        if not item.get("script"):
            raise CatalogError(f"{source}: function {key!r} must set 'script'")

        register = item.get("register")
        if fn_type == "shell-eval":
            if register is None:
                item["register"] = "bashrc"
            elif register not in REGISTER_MODES:
                raise CatalogError(
                    f"{source}: function {key!r} register must be one of "
                    f"{sorted(REGISTER_MODES)}, got {register!r}"
                )
        elif register is not None:
            raise CatalogError(
                f"{source}: function {key!r} sets 'register' but type is {fn_type!r} "
                "('register' only applies to type 'shell-eval')"
            )

        item["params"] = _validate_params(item.get("params"), key=key, source=source)

        validated[key] = item

    return validated


def _validate_params(params: Any, *, key: str, source: Path | str) -> list[dict[str, Any]]:
    if params is None:
        return []
    if not isinstance(params, list):
        raise CatalogError(f"{source}: function {key!r} params must be a list")

    validated: list[dict[str, Any]] = []
    seen: set[str] = set()
    for i, param in enumerate(params):
        if not isinstance(param, dict):
            raise CatalogError(f"{source}: function {key!r} params[{i}] must be a mapping")

        unknown = sorted(set(param) - SUPPORTED_PARAM_FIELDS)
        if unknown:
            fields = ", ".join(unknown)
            raise CatalogError(
                f"{source}: function {key!r} params[{i}] has unknown field(s): {fields}"
            )

        name = param.get("name")
        if not isinstance(name, str) or not VALID_PARAM_NAME.match(name):
            raise CatalogError(
                f"{source}: function {key!r} params[{i}] name must be a valid shell "
                f"identifier, got {name!r}"
            )
        if name in seen:
            raise CatalogError(f"{source}: function {key!r} has duplicate param name {name!r}")
        seen.add(name)

        item = copy.deepcopy(param)
        item.setdefault("description", "")
        item.setdefault("required", True)
        item.setdefault("default", "")
        if not isinstance(item["required"], bool):
            raise CatalogError(f"{source}: function {key!r} params[{i}] required must be a bool")
        validated.append(item)

    return validated


def catalog_document(functions: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {"version": VERSION, "functions": functions}


def read_user_catalog() -> dict[str, dict[str, Any]]:
    return load_catalog_file(USER_CATALOG_PATH)


def write_user_catalog(functions: dict[str, dict[str, Any]]) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    USER_CATALOG_PATH.write_text(_dump(catalog_document(functions)))


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


def save_user_function(key: str, data: dict[str, Any]) -> None:
    validate_catalog(catalog_document({key: data}), source=USER_CATALOG_PATH)
    user = read_user_catalog()
    user[key] = copy.deepcopy(data)
    write_user_catalog(user)


def delete_user_function(key: str) -> bool:
    user = read_user_catalog()
    if key not in user:
        return False
    del user[key]
    write_user_catalog(user)
    return True


def _dump(data: dict[str, Any]) -> str:
    return yaml.safe_dump(data, sort_keys=False, default_flow_style=False)
