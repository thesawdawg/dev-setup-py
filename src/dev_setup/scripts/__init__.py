from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field


@dataclass(frozen=True)
class BuiltinScript:
    key: str
    name: str
    description: str
    handler: Callable[..., int | None]
    category: str = "scripts"
    params: tuple[dict, ...] = field(default_factory=tuple)
    help_cmd: str = ""
    docs_url: str = ""


_scripts: dict[str, BuiltinScript] = {}
_loaded = False


def param(
    name: str,
    description: str = "",
    *,
    required: bool = True,
    default: str = "",
) -> dict:
    """Build a parameter definition for a registered Python script."""
    return {
        "name": name,
        "description": description,
        "required": required,
        "default": default,
    }


def register(
    *,
    key: str,
    name: str = "",
    description: str = "",
    category: str = "scripts",
    params: Iterable[dict] = (),
    help_cmd: str = "",
    docs_url: str = "",
) -> Callable[[Callable[..., int | None]], Callable[..., int | None]]:
    """Register a built-in Python script function.

    Modules in this package can decorate a callable with this helper. The callable
    receives resolved function parameters as keyword arguments and may return an
    integer process-style status code.
    """

    def decorator(handler: Callable[..., int | None]) -> Callable[..., int | None]:
        _scripts[key] = BuiltinScript(
            key=key,
            name=name or key,
            description=description,
            category=category,
            params=tuple(params),
            help_cmd=help_cmd,
            docs_url=docs_url,
            handler=handler,
        )
        return handler

    return decorator


def all_scripts() -> list[BuiltinScript]:
    _load_modules()
    return list(_scripts.values())


def _load_modules() -> None:
    global _loaded
    if _loaded:
        return
    _loaded = True

    prefix = f"{__name__}."
    for module in pkgutil.iter_modules(__path__, prefix):
        name = module.name.rsplit(".", 1)[-1]
        if name.startswith("_") or module.ispkg:
            continue
        importlib.import_module(module.name)
