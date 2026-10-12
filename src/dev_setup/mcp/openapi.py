"""The OpenAPI 3.1 document for the REST front end (docs/specs/mcp, G-2, FR-22).

Pure: a toolbox goes in, a dict comes out. It is generated from the **same** `Toolbox` object that
answers `tools/list`, so the three framings cannot disagree about which tools exist or what their
arguments are (FR-22b); `tests/test_mcp_openapi.py` checks that, and checks the *server* against the
document rather than trusting the two to agree (FR-22c).
"""

from __future__ import annotations

from typing import Any

from dev_setup import __version__
from dev_setup.mcp.core import Gate, Toolbox

# The one operation that is not a tool. Its id is a legal tool name on purpose — a client that turns
# every operationId into a function name can — and a tool *key* equal to it is left out of the
# toolbox, so the two kinds of operation can never collide (core.RESERVED_NAMES).
CATALOG_OPERATION_ID = "openai_tool_list"

_DESCRIPTION = (
    "devstuff's tools as plain HTTP. `GET /tools` lists them in the OpenAI function-calling shape and "
    "`POST /tools/{name}` runs one. Every request needs `Authorization: Bearer <token>`.\n\n"
    "`POST /mcp` is the same tool set spoken as MCP (Streamable HTTP, JSON responses only). It is the "
    "MCP protocol's own endpoint rather than a REST operation, so it is not listed below."
)

_CHANGES_MACHINE = " This tool changes this machine."


def _error(description: str) -> dict[str, Any]:
    return {
        "description": description,
        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Error"}}},
    }


def _tool_operation(box: Toolbox, key: str) -> dict[str, Any]:
    tool = box.get(key)
    assert tool is not None
    description = tool.description
    if box.gate(key) is not Gate.READ_ONLY:
        description += _CHANGES_MACHINE
    schema = box.input_schema(tool)
    request: dict[str, Any] = {"content": {"application/json": {"schema": schema}}}
    if schema.get("required"):
        request["required"] = True
    return {
        "operationId": key,
        "summary": tool.name,
        "description": description,
        "requestBody": request,
        "responses": {
            "200": {
                "description": "The tool ran. `is_error` is true when the tool itself failed.",
                "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ToolResult"}}},
            },
            "400": _error("The body is not valid JSON, or not a JSON object, or is missing and required."),
            "401": _error("Missing or wrong bearer token."),
            "403": _error("Forbidden `Host` or `Origin`."),
            "404": _error("No such tool, or it is not enabled on this server."),
            "405": _error("Wrong method for this path."),
            "413": _error("The request body is over 1 MiB."),
        },
    }


def build(box: Toolbox, *, server_url: str) -> dict[str, Any]:
    paths: dict[str, Any] = {
        "/tools": {
            "get": {
                "operationId": CATALOG_OPERATION_ID,
                "summary": "List the tools",
                "description": "The enabled tools as an array of OpenAI function-calling objects.",
                "responses": {
                    "200": {
                        "description": "The tools.",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "array",
                                    "items": {"$ref": "#/components/schemas/OpenAIFunction"},
                                }
                            }
                        },
                    },
                    "401": _error("Missing or wrong bearer token."),
                    "403": _error("Forbidden `Host` or `Origin`."),
                    "405": _error("Wrong method for this path."),
                },
            }
        }
    }
    for key in box.names():
        paths[f"/tools/{key}"] = {"post": _tool_operation(box, key)}
    return {
        "openapi": "3.1.0",
        "info": {"title": "devstuff", "version": __version__, "description": _DESCRIPTION},
        "servers": [{"url": server_url}],
        "security": [{"bearerAuth": []}],
        "paths": paths,
        "components": {
            "securitySchemes": {"bearerAuth": {"type": "http", "scheme": "bearer"}},
            "schemas": {
                "ToolResult": {
                    "type": "object",
                    "properties": {
                        "is_error": {"type": "boolean"},
                        "text": {"type": "string"},
                        "note": {"type": "string", "description": "Advisory; kept apart from `text`."},
                    },
                    "required": ["is_error", "text"],
                    "additionalProperties": False,
                },
                "Error": {
                    "type": "object",
                    "properties": {"error": {"type": "string"}},
                    "required": ["error"],
                    "additionalProperties": False,
                },
                "OpenAIFunction": {
                    "type": "object",
                    "properties": {
                        "type": {"const": "function"},
                        "function": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "description": {"type": "string"},
                                "parameters": {"type": "object"},
                            },
                            "required": ["name", "description", "parameters"],
                        },
                    },
                    "required": ["type", "function"],
                },
            },
        },
    }
