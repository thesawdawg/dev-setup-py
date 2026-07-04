from __future__ import annotations

from dev_setup import catalog
from dev_setup.base import Tool
from dev_setup.generic import GenericTool

_registry: dict[str, Tool] = {}
_order: list[str] = []
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


def get(key: str) -> Tool | None:
    init()
    return _registry.get(key)


def all_tools() -> list[Tool]:
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
    """Return keys in the transitive requires closure that are not installed.

    Walks requires recursively (depth-first) with cycle protection, so a
    dependency's own missing dependencies are surfaced too. Order is
    deterministic: deepest dependencies first.
    """
    init()
    missing: list[str] = []
    seen: set = {tool.key}

    def visit(key: str) -> None:
        if key in seen:
            return
        seen.add(key)
        dep = _registry.get(key)
        if dep is not None:
            for sub in dep.requires:
                visit(sub)
        if dep is None or not dep.is_installed():
            missing.append(key)

    for key in tool.requires:
        visit(key)
    return missing
