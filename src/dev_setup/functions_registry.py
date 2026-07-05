from __future__ import annotations

from dataclasses import dataclass, field, fields

from dev_setup import functions_catalog as catalog

# field name -> catalog YAML key (only where they differ)
_YAML_KEY: dict[str, str] = {}
_NON_CATALOG = ("key", "builtin")


@dataclass
class FunctionParam:
    name: str = ""
    description: str = ""
    required: bool = True
    default: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> FunctionParam:
        return cls(
            name=data.get("name", ""),
            description=data.get("description", ""),
            required=data.get("required", True),
            default=data.get("default", ""),
        )

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "required": self.required,
            "default": self.default,
        }


@dataclass
class FunctionDef:
    key: str = ""
    name: str = ""
    description: str = ""
    type: str = "script"
    register: str = ""
    params: list[FunctionParam] = field(default_factory=list)
    script: str = ""
    help_cmd: str = ""
    docs_url: str = ""
    builtin: bool = False

    def __post_init__(self) -> None:
        if not self.name:
            self.name = self.key

    @classmethod
    def from_dict(cls, data: dict, key: str) -> FunctionDef:
        kwargs = {
            f.name: data.get(_YAML_KEY.get(f.name, f.name), f.default)
            for f in fields(cls)
            if f.name not in _NON_CATALOG and f.name != "params"
        }
        kwargs["params"] = [FunctionParam.from_dict(p) for p in data.get("params", [])]
        return cls(key=key, **kwargs)

    def to_dict(self) -> dict:
        d: dict = {"name": self.name, "description": self.description, "type": self.type}
        if self.register:
            d["register"] = self.register
        if self.params:
            d["params"] = [p.to_dict() for p in self.params]
        for f in ("script", "help_cmd", "docs_url"):
            val = getattr(self, f)
            if val:
                d[f] = val
        return d

    def save(self) -> None:
        catalog.save_user_function(self.key, self.to_dict())


_registry: dict[str, FunctionDef] = {}
_order: list[str] = []
_initialized = False


def _register(fn: FunctionDef) -> None:
    if fn.key not in _registry:
        _registry[fn.key] = fn
        _order.append(fn.key)
    else:
        _registry[fn.key] = fn


def _load_builtins() -> None:
    effective, bundled, user = catalog.load_effective_catalog()
    for key, data in effective.items():
        fn = FunctionDef.from_dict(data, key=key)
        fn.builtin = key in bundled and key not in user
        _register(fn)


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


def get(key: str) -> FunctionDef | None:
    init()
    return _registry.get(key)


def all_functions() -> list[FunctionDef]:
    init()
    return [_registry[k] for k in _order if k in _registry]


def exists(key: str) -> bool:
    init()
    return key in _registry


def register(fn: FunctionDef) -> None:
    """Register (or replace) a function in the live registry."""
    init()
    _register(fn)


def deregister(key: str) -> None:
    """Remove a function from the live registry by key."""
    init()
    _registry.pop(key, None)
    if key in _order:
        _order.remove(key)
