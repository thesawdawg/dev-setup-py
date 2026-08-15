from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

import yaml

from dev_setup import platforms

CONFIG_DIR = Path.home() / ".config" / "devstuff"
USER_CATALOG_PATH = CONFIG_DIR / "tools.yaml"
BUNDLED_CATALOG = "tools.yaml"

VERSION = 1
VALID_KEY = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
# Fields that only mean anything to `uv tool install`, and the types that accept them.
UV_ONLY_FIELDS = ("uv_with", "uv_executables_from", "uv_python")
UV_TYPES = ("pip", "uvx")
# `system` is the canonical name for "install via the host's package manager";
# `apt` is the original spelling and stays a first-class alias, because user
# catalogs in the wild are full of it.
SYSTEM_TYPES = ("system", "apt")
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
    "requires_traits",
    "alternative",
    "platforms",
    "npm_name",
    "pip_name",
    "uv_with",
    "uv_executables_from",
    "uv_python",
    "packages",
    "apt_packages",
    "git_url",
    "git_install_cmd",
    "git_remove_cmd",
    "script_url",
    "sha256",
    "install_script",
    "remove_script",
}
# Extra keys a `platforms:` block may carry that are meaningless at the top level.
# `platforms` itself is excluded: overrides do not nest.
PLATFORM_BLOCK_FIELDS = (SUPPORTED_FIELDS | {"supported", "reason"}) - {"platforms"}
# Fields that describe *how* a tool is installed. An override touching any of them
# has replaced the mechanism, which is what `requires_traits` was describing.
MECHANISM_FIELDS = frozenset({
    "type", "install_script", "script_url", "packages", "apt_packages",
    "npm_name", "pip_name", "git_url", "git_install_cmd",
})


class CatalogError(RuntimeError):
    pass


@dataclass
class ResolvedTool:
    """One catalog entry after the current platform's overrides are folded in.

    ``data`` is a plain tool record with no ``platforms`` key left in it, ready for
    :meth:`GenericTool.from_dict`. ``unsupported_reason`` is non-empty when this host
    cannot install the tool at all — set either by an explicit ``supported: false``
    override or by a ``requires_traits`` entry the host does not satisfy.
    """

    data: dict[str, Any] = field(default_factory=dict)
    unsupported_reason: str = ""
    alternative: str = ""

    @property
    def supported(self) -> bool:
        return not self.unsupported_reason


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

        _validate_fields(item, key, source)
        _validate_platform_block(item, key, source)

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


def _validate_fields(item: dict[str, Any], key: str, source: Path | str) -> None:
    """Per-field rules that apply equally to a tool record and to a platform override.

    Shared so that a `platforms:` block can't smuggle in a field combination the top
    level rejects — `uv_python` on a bash tool is as wrong inside an override as out.
    """
    for uv_field in UV_ONLY_FIELDS:
        if uv_field not in item:
            continue
        if item.get("type") not in UV_TYPES:
            types = "/".join(UV_TYPES)
            raise CatalogError(
                f"{source}: tool {key!r} sets {uv_field!r}, which is only valid on "
                f"type {types} (got {item.get('type')!r})"
            )
        value = item[uv_field]
        if uv_field == "uv_python":
            if not isinstance(value, str):
                raise CatalogError(f"{source}: tool {key!r} {uv_field} must be a string")
        elif not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise CatalogError(f"{source}: tool {key!r} {uv_field} must be a list of strings")

    if "packages" in item:
        if "apt_packages" in item:
            raise CatalogError(
                f"{source}: tool {key!r} sets both 'packages' and 'apt_packages' — "
                "'packages' is the canonical spelling, drop the other"
            )
        if item["type"] not in SYSTEM_TYPES:
            types = "/".join(SYSTEM_TYPES)
            raise CatalogError(
                f"{source}: tool {key!r} sets 'packages', which is only valid on "
                f"type {types} (got {item['type']!r})"
            )

    for str_field in ("alternative", "reason"):
        if str_field in item and not isinstance(item[str_field], str):
            raise CatalogError(f"{source}: tool {key!r} {str_field} must be a string")

    traits = item.get("requires_traits")
    if traits is not None:
        if not isinstance(traits, list) or not all(isinstance(t, str) for t in traits):
            raise CatalogError(
                f"{source}: tool {key!r} requires_traits must be a list of strings"
            )
        # A typo here would make the tool permanently unsupported everywhere and say
        # so in words the user can't act on, so it fails at load time instead.
        unknown_traits = sorted(set(traits) - set(platforms.TRAITS))
        if unknown_traits:
            known = ", ".join(sorted(platforms.TRAITS))
            raise CatalogError(
                f"{source}: tool {key!r} requires unknown trait(s): "
                f"{', '.join(unknown_traits)} (known: {known})"
            )


def _validate_platform_block(item: dict[str, Any], key: str, source: Path | str) -> None:
    """Validate a tool's `platforms:` mapping of per-platform overrides."""
    block = item.get("platforms")
    if block is None:
        return
    if not isinstance(block, dict):
        raise CatalogError(f"{source}: tool {key!r} platforms must be a mapping")
    for scope, override in block.items():
        if not isinstance(scope, str) or not scope:
            raise CatalogError(f"{source}: tool {key!r} has an invalid platform scope {scope!r}")
        if not isinstance(override, dict):
            raise CatalogError(
                f"{source}: tool {key!r} platforms.{scope} must be a mapping"
            )
        unknown = sorted(set(override) - PLATFORM_BLOCK_FIELDS)
        if unknown:
            raise CatalogError(
                f"{source}: tool {key!r} platforms.{scope} has unknown field(s): "
                f"{', '.join(unknown)}"
            )
        supported = override.get("supported")
        if supported is not None and not isinstance(supported, bool):
            raise CatalogError(
                f"{source}: tool {key!r} platforms.{scope}.supported must be true or false"
            )
        if supported is False and not override.get("reason"):
            # An unexplained refusal is the one outcome worse than a failed install:
            # the user is told no and given nothing to do about it.
            raise CatalogError(
                f"{source}: tool {key!r} platforms.{scope} is unsupported but gives no "
                "'reason' — say why, so the user knows what to do instead"
            )
        if (
            item.get("requires_traits")
            and supported is not False
            and set(override) & MECHANISM_FIELDS
            and "requires_traits" not in override
        ):
            # Merging is literal per key, so a block that swaps the install mechanism
            # would otherwise silently inherit trait requirements describing the
            # mechanism it just replaced — and the tool would report itself
            # unavailable on the one platform someone went to the trouble of
            # supporting. Forcing the block to say so is the only way that mistake
            # can't be made quietly.
            raise CatalogError(
                f"{source}: tool {key!r} platforms.{scope} replaces the install "
                f"mechanism but does not restate 'requires_traits'. The base entry "
                f"requires {item['requires_traits']}, which describes the mechanism "
                "being replaced — set requires_traits (usually []) in the override."
            )
        # Judge the override's own fields against the type it *results in*: a block
        # that swaps a uvx tool for a system package has to satisfy the system rules.
        # Only the block's own keys are checked — fields left over from the type it
        # replaced are dead data at that point, not errors.
        scoped = {
            "type": override.get("type", item.get("type")),
            **{k: v for k, v in override.items() if k not in ("supported", "reason")},
        }
        _validate_fields(scoped, f"{key}.platforms.{scope}", source)


def resolve_for_platform(
    data: dict[str, Any],
    platform: platforms.Platform | None = None,
) -> ResolvedTool:
    """Fold the current platform's overrides into one catalog record.

    Precedence is base record → ``platforms.<family>`` → ``platforms.<id>``, so a
    Debian-wide override can be narrowed for Ubuntu specifically. Merging is
    key-wise and literal: an override that replaces the install mechanism must also
    restate ``requires_traits`` (usually as ``[]``), because the base entry's trait
    requirements describe the *base* mechanism, not the replacement.
    """
    p = platform or platforms.current()
    record = copy.deepcopy(data)
    block = record.pop("platforms", None) or {}

    supported: bool | None = None
    reason = ""
    alternative = str(record.pop("alternative", "") or "")

    # family first, then the exact id — most specific wins.
    for scope in (p.family, p.id):
        override = block.get(scope)
        if not isinstance(override, dict):
            continue
        for field_name, value in override.items():
            if field_name == "supported":
                supported = bool(value)
            elif field_name == "reason":
                reason = str(value)
            elif field_name == "alternative":
                alternative = str(value)
            else:
                record[field_name] = copy.deepcopy(value)

    traits = record.pop("requires_traits", None) or []

    if supported is False:
        return ResolvedTool(record, reason, alternative)
    if supported is True:
        # An explicit yes overrules the trait table — the override knows something
        # generic capability detection does not.
        return ResolvedTool(record, "", alternative)

    trait_reason = platforms.unsupported_reason(traits, platform=p)
    return ResolvedTool(record, trait_reason, alternative)


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
