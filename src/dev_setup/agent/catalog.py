from __future__ import annotations

import copy
import re
from importlib import resources
from pathlib import Path
from typing import Any

import yaml

from dev_setup.catalog import CONFIG_DIR, CatalogError

USER_CATALOG_PATH = CONFIG_DIR / "agent_tools.yaml"
BUNDLED_CATALOG = "agent_tools.yaml"

VERSION = 1
VALID_KEY = re.compile(r"^[a-z][a-z0-9_]*$")

IMPLS = {"primitive", "catalog", "function"}
PARAM_TYPES = {"string", "integer", "number", "boolean"}

SUPPORTED_TOP_FIELDS = {"version", "expose_functions", "exclude_functions", "tools"}
SUPPORTED_FIELDS = {"name", "description", "impl", "target", "mutating", "params"}
SUPPORTED_PARAM_FIELDS = {"name", "description", "type", "required", "default", "enum"}


def load_bundled_catalog() -> dict[str, Any]:
    raw = resources.files("dev_setup").joinpath(BUNDLED_CATALOG).read_text()
    return validate_catalog(yaml.safe_load(raw), source=f"dev_setup/{BUNDLED_CATALOG}")


def load_catalog_file(path: Path, *, required: bool = False) -> dict[str, Any]:
    if not path.exists():
        if required:
            raise CatalogError(f"Catalog file not found: {path}")
        return {}
    try:
        raw = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as exc:
        raise CatalogError(f"Invalid YAML in {path}: {exc}") from exc
    return validate_catalog(raw, source=path)


def _validate_param(param: Any, *, key: str, source: Path | str) -> dict[str, Any]:
    where = f"{source}: tool '{key}'"
    if not isinstance(param, dict):
        raise CatalogError(f"{where}: each param must be a mapping")

    unknown = set(param) - SUPPORTED_PARAM_FIELDS
    if unknown:
        raise CatalogError(f"{where}: unknown param field(s): {', '.join(sorted(unknown))}")

    name = param.get("name")
    if not isinstance(name, str) or not name:
        raise CatalogError(f"{where}: every param needs a name")

    ptype = param.get("type", "string")
    if ptype not in PARAM_TYPES:
        raise CatalogError(
            f"{where}: param '{name}' has unsupported type '{ptype}' "
            f"(expected one of: {', '.join(sorted(PARAM_TYPES))})"
        )

    if "required" in param and not isinstance(param["required"], bool):
        raise CatalogError(f"{where}: param '{name}' field 'required' must be true or false")

    if "enum" in param and not isinstance(param["enum"], list):
        raise CatalogError(f"{where}: param '{name}' field 'enum' must be a list")

    return param


def validate_catalog(raw: Any, *, source: Path | str = "<catalog>") -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise CatalogError(f"{source}: catalog must be a mapping")

    unknown = set(raw) - SUPPORTED_TOP_FIELDS
    if unknown:
        raise CatalogError(f"{source}: unknown top-level field(s): {', '.join(sorted(unknown))}")

    if raw.get("version") != VERSION:
        raise CatalogError(f"{source}: version must be {VERSION}")

    if "expose_functions" in raw and not isinstance(raw["expose_functions"], bool):
        raise CatalogError(f"{source}: 'expose_functions' must be true or false")

    excluded = raw.get("exclude_functions", [])
    if not isinstance(excluded, list) or not all(isinstance(x, str) for x in excluded):
        raise CatalogError(f"{source}: 'exclude_functions' must be a list of strings")

    tools = raw.get("tools") or {}
    if not isinstance(tools, dict):
        raise CatalogError(f"{source}: 'tools' must be a mapping")

    for key, entry in tools.items():
        if not isinstance(key, str) or not VALID_KEY.match(key):
            raise CatalogError(
                f"{source}: invalid tool key '{key}' "
                "(lowercase letters, digits and underscores; must start with a letter)"
            )
        if not isinstance(entry, dict):
            raise CatalogError(f"{source}: tool '{key}' must be a mapping")

        unknown = set(entry) - SUPPORTED_FIELDS
        if unknown:
            raise CatalogError(f"{source}: tool '{key}': unknown field(s): {', '.join(sorted(unknown))}")

        if not isinstance(entry.get("description"), str) or not entry["description"].strip():
            raise CatalogError(f"{source}: tool '{key}' needs a description (the model reads it)")

        impl = entry.get("impl")
        if impl not in IMPLS:
            raise CatalogError(
                f"{source}: tool '{key}' has unsupported impl '{impl}' "
                f"(expected one of: {', '.join(sorted(IMPLS))})"
            )

        # A bridge without a target has nothing to dispatch to; primitives are
        # dispatched by key, so a target there would be silently ignored.
        if impl in ("catalog", "function"):
            if not isinstance(entry.get("target"), str) or not entry["target"]:
                raise CatalogError(f"{source}: tool '{key}' has impl '{impl}' but no 'target'")
        elif "target" in entry:
            raise CatalogError(f"{source}: tool '{key}' has impl 'primitive' and cannot take a 'target'")

        if "mutating" in entry and not isinstance(entry["mutating"], bool):
            raise CatalogError(f"{source}: tool '{key}' field 'mutating' must be true or false")

        params = entry.get("params", [])
        if not isinstance(params, list):
            raise CatalogError(f"{source}: tool '{key}' field 'params' must be a list")
        seen: set[str] = set()
        for param in params:
            validated = _validate_param(param, key=key, source=source)
            if validated["name"] in seen:
                raise CatalogError(f"{source}: tool '{key}' has duplicate param '{validated['name']}'")
            seen.add(validated["name"])

    return raw


def load_effective_catalog() -> dict[str, Any]:
    """Bundled catalog first, then the user catalog overriding matching tool keys
    in place and appending new ones -- the same precedence as tools.yaml."""
    effective = copy.deepcopy(load_bundled_catalog())
    user = load_catalog_file(USER_CATALOG_PATH)
    if not user:
        return effective

    for field in ("expose_functions", "exclude_functions"):
        if field in user:
            effective[field] = user[field]

    tools = effective.setdefault("tools", {})
    for key, entry in (user.get("tools") or {}).items():
        tools[key] = entry

    return effective
