"""The OpenAPI document (docs/specs/mcp, G-2, FR-21, FR-22, FR-22a–c, FR-23).

Three different claims, each needing its own test, because each can fail while the others pass:

* **valid** — an OpenAPI 3.1 validator accepts the document (FR-22a);
* **complete** — the same set of tools appears in `tools/list`, `GET /tools` and the document, in both
  directions (FR-22b);
* **truthful** — a client that reads *only* the document, and never the server's code, gets the
  statuses and bodies the document promises (FR-22c). A valid document can describe an API the
  server does not implement; validity says nothing about truth.
"""

from __future__ import annotations

import copy
import itertools
import json

import pytest
from openapi_spec_validator import validate

from dev_setup.mcp import core, openapi
from tests.mcp_http_support import TOKEN, TOOLS, Running, running, tool, toolbox

FLAGS = list(itertools.product([False, True], repeat=2))  # (install, functions)
TOOL_STATUSES = {"200", "400", "401", "403", "404", "405", "413"}
CATALOG_STATUSES = {"200", "401", "403", "405"}


def doc_for(install=False, functions=False, **kw):
    return openapi.build(toolbox(install=install, functions=functions, **kw), server_url="http://127.0.0.1:8765")


def operations(doc):
    for path, item in doc["paths"].items():
        for method, op in item.items():
            yield path, method, op


def tool_ops(doc):
    return {
        op["operationId"]: (p, m, op)
        for p, m, op in operations(doc)
        if op["operationId"] != "openai_tool_list"
    }


# -- FR-22a: valid ------------------------------------------------------------------------------


@pytest.mark.parametrize(("install", "functions"), FLAGS)
def test_the_document_is_valid_openapi_3_1(install, functions):
    doc = doc_for(install, functions)
    assert doc["openapi"].startswith("3.1")
    validate(doc)


def _info(d):
    return d["paths"]["/tools/tool_info"]["post"]


BREAKS = {
    "no info": lambda d: d.pop("info"),
    "duplicate operationId": lambda d: _info(d).update(operationId="list_tools"),
    "bogus schema type": lambda d: d["components"]["schemas"]["Error"].update(type="bogus"),
    "dangling $ref": lambda d: _info(d)["responses"]["200"]["content"]["application/json"]["schema"].update(
        {"$ref": "#/components/schemas/Nope"}
    ),
    "bad status key": lambda d: _info(d)["responses"].update({"99": {"description": "x"}}),
    "response without description": lambda d: _info(d)["responses"]["200"].pop("description"),
}


@pytest.mark.parametrize("name", sorted(BREAKS))
def test_the_validator_can_fail(name):
    """The control (F-9): a broken copy is rejected, so the passes above mean something."""
    broken = copy.deepcopy(doc_for(True, True))
    BREAKS[name](broken)
    with pytest.raises(Exception):  # noqa: B017 - which exception class is the validator's business
        validate(broken)


def test_every_security_requirement_names_a_scheme_that_exists():
    """Measured: the validator accepts a `security` entry naming no scheme, so this is checked here."""
    doc = doc_for(True, True)
    declared = set(doc["components"]["securitySchemes"])
    used = {name for req in doc["security"] for name in req}
    assert used and used <= declared


# -- FR-22b: complete in both directions ---------------------------------------------------------


@pytest.mark.parametrize(("install", "functions"), FLAGS)
def test_the_same_tools_appear_in_all_three_places(install, functions):
    box = toolbox(install=install, functions=functions)
    doc = openapi.build(box, server_url="http://x")
    listed = {t["name"]: t["inputSchema"] for t in box.describe()}
    openai = {t["function"]["name"]: t["function"]["parameters"] for t in box.openai_tools()}
    documented = {oid: op for oid, (_, _, op) in tool_ops(doc).items()}

    assert set(listed) == set(openai) == set(documented)
    for name, schema in listed.items():
        assert openai[name] == schema  # the one shared schema object feeds every framing
        body = documented[name].get("requestBody")
        assert body["content"]["application/json"]["schema"] == schema
        assert body.get("required", False) is bool(schema.get("required"))


def test_a_tool_missing_from_the_document_is_noticed():
    box = toolbox(install=True, functions=True)
    doc = openapi.build(box, server_url="http://x")
    del doc["paths"]["/tools/install_tool"]
    assert set(box.names()) != set(tool_ops(doc))


def test_the_catalog_operation_is_present_exactly_once_and_apart_from_the_tools():
    for install, functions in FLAGS:
        doc = doc_for(install, functions)
        catalog = [(p, m, op) for p, m, op in operations(doc) if op["operationId"] == "openai_tool_list"]
        assert [(p, m) for p, m, _ in catalog] == [("/tools", "get")]
        assert "openai_tool_list" not in tool_ops(doc)  # a tool operation never shares its name
        assert "requestBody" not in catalog[0][2]


def test_a_user_tool_named_like_the_catalog_operation_is_left_out_not_shadowed():
    warned: list[str] = []
    box = toolbox(tools={**TOOLS, "openai_tool_list": tool("openai_tool_list", target="search_catalog")},
                  warn=warned.append)
    doc = openapi.build(box, server_url="http://x")
    assert "openai_tool_list" not in box.names()
    assert any("openai_tool_list" in w for w in warned)
    ids = [op["operationId"] for _, _, op in operations(doc)]
    assert ids.count("openai_tool_list") == 1 and "/tools/openai_tool_list" not in doc["paths"]
    validate(doc)  # operationIds must be unique, which the validator enforces


# -- shape ---------------------------------------------------------------------------------------


def test_tool_operations_are_post_tools_key_with_a_documented_set_of_statuses():
    doc = doc_for(True, True)
    for oid, (path, method, op) in tool_ops(doc).items():
        assert (path, method) == (f"/tools/{oid}", "post")
        assert set(op["responses"]) == TOOL_STATUSES
        responses = op["responses"]
        assert responses["200"]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/ToolResult"
        }
        for status in TOOL_STATUSES - {"200"}:
            assert responses[status]["content"]["application/json"]["schema"] == {
                "$ref": "#/components/schemas/Error"
            }
    catalog = doc["paths"]["/tools"]["get"]
    assert set(catalog["responses"]) == CATALOG_STATUSES


def test_the_schemas_are_the_documented_ones():
    schemas = doc_for()["components"]["schemas"]
    assert schemas["ToolResult"]["required"] == ["is_error", "text"]
    assert set(schemas["ToolResult"]["properties"]) == {"is_error", "text", "note"}
    assert schemas["Error"]["required"] == ["error"]


def test_a_request_body_is_required_exactly_when_the_tool_has_a_required_parameter():
    doc = doc_for(True, True)
    ops = tool_ops(doc)
    assert ops["tool_info"][2]["requestBody"]["required"] is True
    assert ops["install_tool"][2]["requestBody"]["required"] is True
    for name in ("list_tools", "outdated", "search_catalog"):
        assert ops[name][2]["requestBody"].get("required", False) is False


def test_a_gated_tool_says_it_changes_this_machine_and_a_read_only_one_does_not():
    ops = tool_ops(doc_for(True, True))
    assert "changes this machine" in ops["install_tool"][2]["description"]
    assert "changes this machine" in ops["fn_x"][2]["description"]
    assert "changes this machine" not in ops["list_tools"][2]["description"]
    assert ops["list_tools"][2]["description"].startswith("the list_tools tool")


def test_security_is_a_bearer_scheme_on_every_operation():
    doc = doc_for()
    assert doc["components"]["securitySchemes"]["bearerAuth"] == {"type": "http", "scheme": "bearer"}
    assert doc["security"] == [{"bearerAuth": []}]


def test_the_server_url_is_the_one_the_client_used():
    box = toolbox()
    assert openapi.build(box, server_url="http://host.docker.internal:8765")["servers"] == [
        {"url": "http://host.docker.internal:8765"}
    ]


def test_mcp_is_not_documented_as_a_rest_operation_and_the_description_says_where_it_is():
    doc = doc_for()
    assert "/mcp" not in doc["paths"]
    assert "POST /mcp" in doc["info"]["description"]


# -- FR-22c: truthful — a client that reads only the document ---------------------------------------


def resolve(schema, doc):
    """Inline `$ref`s, so the response check never depends on the server's own types."""
    if isinstance(schema, dict):
        if "$ref" in schema:
            node = doc
            for part in schema["$ref"].removeprefix("#/").split("/"):
                node = node[part]
            return resolve(node, doc)
        return {k: resolve(v, doc) for k, v in schema.items()}
    if isinstance(schema, list):
        return [resolve(v, doc) for v in schema]
    return schema


def check(instance, schema, doc):
    import jsonschema

    jsonschema.Draft202012Validator(resolve(schema, doc)).validate(instance)


SAMPLES = {"string": "x", "boolean": True, "integer": 1, "number": 1.5, "array": ["x"], "object": {}}


def make_body(op):
    """A request body built from nothing but the operation's own schema."""
    body = op.get("requestBody")
    if not body:
        return None
    schema = body["content"]["application/json"]["schema"]
    return {name: SAMPLES[schema["properties"][name]["type"]] for name in schema.get("required", [])}


def fetch_document(srv: Running) -> dict:
    status, _, doc = srv.json("GET", "/openapi.json")
    assert status == 200
    return doc


@pytest.mark.parametrize(("install", "functions"), FLAGS)
def test_every_operation_answers_with_what_the_document_promises(install, functions):
    with running(install=install, functions=functions) as srv:
        doc = fetch_document(srv)  # the client's only source of truth
        assert doc["servers"][0]["url"] == f"http://127.0.0.1:{srv.port}"
        seen = 0
        for path, method, op in operations(doc):
            body = make_body(op)
            status, headers, data = srv.request(method.upper(), path, body=body)
            documented = op["responses"]
            assert str(status) in documented, (
                f"{method.upper()} {path} answered {status}, not in {sorted(documented)}"
            )
            assert status == 200
            payload = json.loads(data)
            schema = documented["200"]["content"]["application/json"]["schema"]
            check(payload, schema, doc)  # against THIS operation's schema, not one for all
            assert headers["content-type"].startswith("application/json")
            seen += 1
        assert seen == len(list(operations(doc)))


def test_each_operation_is_checked_against_its_own_response_schema():
    """The mutation this guards: validating every 200 against ToolResult. The catalog operation
    returns an array, which ToolResult would reject."""
    with running() as srv:
        doc = fetch_document(srv)
        status, _, data = srv.json("GET", "/tools")
        assert isinstance(data, list)
        schema = doc["paths"]["/tools"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
        check(data, schema, doc)
        with pytest.raises(Exception):  # noqa: B017
            check(data, {"$ref": "#/components/schemas/ToolResult"}, doc)


def provoke(srv: Running, path: str, method: str, op: dict) -> dict[int, tuple[int, bytes]]:
    """Bad requests aimed at one operation, keyed by the status each is *meant* to produce."""
    other = "GET" if method == "post" else "POST"
    body = make_body(op)
    out = {
        401: srv.request(method.upper(), path, token=False, body=body),
        403: srv.request(method.upper(), path, host=f"evil.example:{srv.port}", body=body),
        405: srv.request(other, path, body={}),
    }
    if method == "post":
        out[400] = srv.request("POST", path, raw=b"{not json")
        out[413] = srv.request("POST", path, raw=b" " * (core_max() + 1))
    return {status: (resp[0], resp[2]) for status, resp in out.items()}


def core_max() -> int:
    from dev_setup.mcp import http as mcp_http

    return mcp_http.MAX_BODY


def test_every_status_the_document_lists_is_one_the_server_really_sends():
    """Both directions: the client never sees an undocumented status (above and below), and a
    documented status is not fiction. The one exception is named, not hidden."""
    with running(install=True, functions=True) as srv:
        doc = fetch_document(srv)
        # 404 on `POST /tools/<key>` is for a key that is not in the toolbox. Every path *in this
        # document* is in the toolbox, so a client following it cannot reach it; it exists for a
        # client holding an older document after a restart with different flags.
        unreachable = {(f"/tools/{oid}", "post", "404") for oid in tool_ops(doc)}
        for path, method, op in operations(doc):
            produced = provoke(srv, path, method, op)
            for status, (got, _) in produced.items():
                assert got == status, f"{method.upper()} {path}: wanted {status}, got {got}"
                assert str(got) in op["responses"]
            reachable = {str(s) for s in produced} | {"200"}
            for status in op["responses"]:
                if (path, method, status) in unreachable:
                    continue
                assert status in reachable, (
                    f"{method.upper()} {path} documents {status}, which was never sent"
                )


def test_the_error_bodies_validate_against_the_documented_error_schema():
    with running() as srv:
        doc = fetch_document(srv)
        for bad in (
            srv.json("POST", "/tools/list_tools", token=False),
            srv.json("POST", "/tools/list_tools", host="evil.example"),
            srv.json("GET", "/tools/list_tools"),
            srv.json("POST", "/tools/list_tools", raw=b"[1]"),
        ):
            status, _, data = bad
            check(data, {"$ref": "#/components/schemas/Error"}, doc)


def test_a_document_fetched_with_a_container_host_names_that_host():
    with running(allow_hosts=("host.docker.internal",)) as srv:
        doc = srv.json("GET", "/openapi.json", host=f"host.docker.internal:{srv.port}")[2]
        assert doc["servers"][0]["url"] == f"http://host.docker.internal:{srv.port}"


def test_the_document_lists_what_tools_list_lists_over_the_wire():
    for install, functions in FLAGS:
        with running(install=install, functions=functions) as srv:
            doc = fetch_document(srv)
            rpc = srv.json("POST", "/mcp", body={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})[2]
            over_mcp = {t["name"] for t in rpc["result"]["tools"]}
            over_rest = {t["function"]["name"] for t in srv.json("GET", "/tools")[2]}
            assert over_mcp == over_rest == set(tool_ops(doc))
            assert TOKEN not in json.dumps(doc)  # the secret is never part of a document


def test_toolbox_helpers_used_by_the_document():
    box = toolbox(install=True)
    assert box.requires_body("tool_info") is True
    assert box.requires_body("list_tools") is False
    assert core.rest_result(core.ToolResult(False, "t")) == {"is_error": False, "text": "t"}
    assert core.rest_result(core.ToolResult(False, "t", "n")) == {"is_error": False, "text": "t", "note": "n"}
