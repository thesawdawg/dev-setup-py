from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from dev_setup.agent import catalog as agent_catalog


@dataclass
class AgentParam:
    name: str
    type: str = "string"
    description: str = ""
    required: bool = False
    default: Any = None
    enum: list[Any] | None = None

    def to_schema(self) -> dict[str, Any]:
        schema: dict[str, Any] = {"type": self.type}
        if self.description:
            schema["description"] = self.description
        if self.enum:
            schema["enum"] = self.enum
        return schema


@dataclass
class AgentTool:
    key: str
    name: str
    description: str
    impl: str
    mutating: bool = False
    target: str | None = None
    params: list[AgentParam] = field(default_factory=list)

    @classmethod
    def from_dict(cls, key: str, data: dict[str, Any]) -> AgentTool:
        return cls(
            key=key,
            name=data.get("name") or key,
            description=(data.get("description") or "").strip(),
            impl=data["impl"],
            mutating=bool(data.get("mutating", False)),
            target=data.get("target"),
            params=[
                AgentParam(
                    name=p["name"],
                    type=p.get("type", "string"),
                    description=(p.get("description") or "").strip(),
                    required=bool(p.get("required", False)),
                    default=p.get("default"),
                    enum=p.get("enum"),
                )
                for p in data.get("params", [])
            ],
        )

    def to_schema(self) -> dict[str, Any]:
        """The Ollama /api/chat `tools[]` entry for this tool."""
        return {
            "type": "function",
            "function": {
                "name": self.key,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {p.name: p.to_schema() for p in self.params},
                    "required": [p.name for p in self.params if p.required],
                },
            },
        }

    def bind(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Fill defaults and reject a call the tool cannot satisfy.

        Raises ValueError with a message aimed at the model, not the user -- it is
        returned as a tool error so the agent can correct the call itself.
        """
        bound: dict[str, Any] = {}
        for param in self.params:
            if param.name in arguments and arguments[param.name] is not None:
                bound[param.name] = arguments[param.name]
            elif param.default is not None:
                bound[param.name] = param.default
            elif param.required:
                raise ValueError(f"missing required parameter '{param.name}'")

        unexpected = set(arguments) - {p.name for p in self.params}
        if unexpected:
            known = ", ".join(p.name for p in self.params) or "none"
            raise ValueError(
                f"unexpected parameter(s): {', '.join(sorted(unexpected))}. "
                f"This tool accepts: {known}"
            )
        return bound


def _function_tools(excluded: set[str]) -> dict[str, AgentTool]:
    """Expose functions.yaml entries as agent tools.

    Only `type: script` functions qualify. A shell-eval function exists to mutate
    the calling shell -- bashrc-registered ones cannot run in a subprocess at all,
    and eval-registered ones only emit shell code for the caller to evaluate.
    Neither has any meaning inside an agent loop, which is the same reason
    `devstuff run` refuses them.
    """
    from dev_setup import functions_registry

    functions_registry.init()
    tools: dict[str, AgentTool] = {}
    for fn in functions_registry.all_functions():
        if fn.type != "script" or fn.key in excluded:
            continue
        key = f"fn_{fn.key.replace('-', '_')}"
        tools[key] = AgentTool(
            key=key,
            name=fn.name,
            description=(fn.description or fn.name).strip(),
            impl="function",
            target=fn.key,
            mutating=True,  # a script function runs arbitrary shell; treat as mutating
            params=[
                AgentParam(
                    name=p.name,
                    type="string",
                    description=(p.description or p.name).strip(),
                    required=p.required,
                    default=p.default or None,
                )
                for p in fn.params
            ],
        )
    return tools


def build() -> dict[str, AgentTool]:
    """The effective toolbox: catalog entries plus auto-exposed functions."""
    catalog = agent_catalog.load_effective_catalog()

    tools = {
        key: AgentTool.from_dict(key, entry)
        for key, entry in (catalog.get("tools") or {}).items()
    }

    if catalog.get("expose_functions", True):
        excluded = set(catalog.get("exclude_functions") or [])
        for key, tool in _function_tools(excluded).items():
            # An explicit catalog entry always wins over an auto-exposed function.
            tools.setdefault(key, tool)

    return tools


def to_schemas(tools: dict[str, AgentTool]) -> list[dict[str, Any]]:
    return [tool.to_schema() for tool in tools.values()]
