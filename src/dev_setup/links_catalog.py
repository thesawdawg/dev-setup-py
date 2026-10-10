from __future__ import annotations

import copy
import re
from importlib import resources
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

from dev_setup.catalog import CONFIG_DIR, CatalogError

USER_CATALOG_PATH = CONFIG_DIR / "links.yaml"
BUNDLED_CATALOG = "links.yaml"

VERSION = 1
VALID_KEY = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

# Links are opened with webbrowser.open() from a user-editable file. Anything but web
# schemes (file://, javascript:, custom protocol handlers) would let a catalog launch
# local programs, so the allowlist is the control, not a courtesy.
ALLOWED_SCHEMES = {"http", "https"}

SUPPORTED_FIELDS = {"name", "description", "url", "category"}


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

    links = raw.get("links")
    if links is None:
        return {}
    if not isinstance(links, dict):
        raise CatalogError(f"{source}: links must be a mapping")

    validated: dict[str, dict[str, Any]] = {}
    for key, data in links.items():
        if not isinstance(key, str) or not VALID_KEY.match(key):
            raise CatalogError(f"{source}: invalid link key {key!r}")
        if not isinstance(data, dict):
            raise CatalogError(f"{source}: link {key!r} must be a mapping")

        unknown = sorted(set(data) - SUPPORTED_FIELDS)
        if unknown:
            fields = ", ".join(unknown)
            raise CatalogError(f"{source}: link {key!r} has unknown field(s): {fields}")

        item = copy.deepcopy(data)
        item.setdefault("name", key)
        item.setdefault("description", "")
        item.setdefault("category", "custom")

        for field in ("name", "description", "category"):
            if not isinstance(item[field], str):
                raise CatalogError(f"{source}: link {key!r} {field} must be a string")

        url = item.get("url")
        if not isinstance(url, str) or not url:
            raise CatalogError(f"{source}: link {key!r} must set 'url'")
        parsed = urlparse(url)
        if parsed.scheme not in ALLOWED_SCHEMES or not parsed.netloc:
            raise CatalogError(
                f"{source}: link {key!r} url must be an absolute http(s) URL, got {url!r}"
            )

        validated[key] = item

    return validated


def catalog_document(links: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {"version": VERSION, "links": links}


def read_user_catalog() -> dict[str, dict[str, Any]]:
    return load_catalog_file(USER_CATALOG_PATH)


def write_user_catalog(links: dict[str, dict[str, Any]]) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    USER_CATALOG_PATH.write_text(_dump(catalog_document(links)))


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
    return merge_catalogs(bundled, user), bundled, user


def merge_catalogs(
    bundled: dict[str, dict[str, Any]],
    user: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    merged = copy.deepcopy(bundled)
    for key, data in user.items():
        merged[key] = copy.deepcopy(data)
    return merged


def _dump(data: dict[str, Any]) -> str:
    return yaml.safe_dump(data, sort_keys=False, default_flow_style=False)
