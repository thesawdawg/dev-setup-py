from __future__ import annotations

import importlib
import json
import pkgutil
from pathlib import Path
from typing import Dict, List, Optional

from dev_setup.base import Tool

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
    from dev_setup import packages as pkg_ns

    for finder, name, _ in pkgutil.iter_modules(pkg_ns.__path__):  # type: ignore[attr-defined]
        module = importlib.import_module(f"dev_setup.packages.{name}")
        for attr in dir(module):
            obj = getattr(module, attr)
            if (
                isinstance(obj, type)
                and issubclass(obj, Tool)
                and obj is not Tool
                and getattr(obj, "key", "")
            ):
                instance = obj()
                instance.builtin = True
                _register(instance)


def _load_custom(config_dir: Path) -> None:
    if not config_dir.is_dir():
        return
    for f in sorted(config_dir.glob("*.json")):
        try:
            data = json.loads(f.read_text())
            from dev_setup.generic import GenericTool
            tool = GenericTool.from_dict(data, key=f.stem)
            tool.builtin = False
            _register(tool)
        except Exception:
            pass


def init() -> None:
    global _initialized
    if _initialized:
        return
    _initialized = True
    _load_builtins()
    from dev_setup.generic import CUSTOM_DIR
    _load_custom(CUSTOM_DIR)


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
