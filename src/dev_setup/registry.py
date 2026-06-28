from __future__ import annotations

from typing import Dict, List, Optional

from dev_setup import catalog
from dev_setup.base import Tool
from dev_setup.generic import GenericTool

_registry: Dict[str, Tool] = {}
_order: List[str] = []
_initialized = False


def _register(tool: Tool) -> None:
    if tool.key not in _registry:
        _registry[tool.key] = tool
        _order.append(tool.key)
    else:
        _registry[tool.key] = tool


def _load_builtins() -> None:
    effective, bundled, user = catalog.load_effective_catalog()
    for key, data in effective.items():
        tool = GenericTool.from_dict(data, key=key)
        tool.builtin = key in bundled and key not in user
        _register(tool)


def init() -> None:
    global _initialized
    if _initialized:
        return
    _initialized = True
    _load_builtins()


def reload() -> None:
    global _initialized
    _registry.clear()
    _order.clear()
    _initialized = False
    init()


def get(key: str) -> Optional[Tool]:
    init()
    return _registry.get(key)


def all_tools() -> List[Tool]:
    init()
    return [_registry[k] for k in _order if k in _registry]


def exists(key: str) -> bool:
    init()
    return key in _registry


def register(tool: Tool) -> None:
    """Register (or replace) a tool in the live registry."""
    init()
    _register(tool)


def deregister(key: str) -> None:
    """Remove a tool from the live registry by key."""
    init()
    _registry.pop(key, None)
    if key in _order:
        _order.remove(key)


def missing_requires(tool: Tool) -> list:
    """Return keys in tool.requires that are not currently installed."""
    init()
    return [
        key for key in tool.requires
        if key not in _registry or not _registry[key].is_installed()
    ]
