from __future__ import annotations

import json
import urllib.error
from typing import Any

import pytest

from dev_setup.agent import config as agent_config
from dev_setup.agent import preflight
from dev_setup.agent.config import AgentConfig
from dev_setup.agent.ollama import (
    ModelNotFound,
    OllamaClient,
    OllamaError,
    OllamaTimeout,
    OllamaUnavailable,
    parse_message,
)
from dev_setup.catalog import CatalogError


def fake_transport(responses: dict[str, Any]):
    """Map a path to a canned response, an Exception to raise, or a callable."""
    calls: list[tuple[str, str, dict | None]] = []

    def transport(method: str, path: str, payload: dict | None, timeout: int) -> dict:
        calls.append((method, path, payload))
        result = responses[path]
        if isinstance(result, Exception):
            raise result
        if callable(result):
            return result(payload)
        return result

    transport.calls = calls  # type: ignore[attr-defined]
    return transport


def client_for(responses: dict[str, Any], **kwargs) -> OllamaClient:
    return OllamaClient("http://localhost:11434", transport=fake_transport(responses), **kwargs)


# -- config ----------------------------------------------------------------------


def test_config_defaults_when_file_absent(tmp_path):
    cfg = agent_config.load(tmp_path / "nope.yaml")
    assert cfg.model == "gemma4:latest"
    assert cfg.num_ctx == 16384
    assert cfg.think is False


def test_config_file_overrides_defaults(tmp_path):
    path = tmp_path / "agent.yaml"
    path.write_text("version: 1\nmodel: granite4.1:8b\nnum_ctx: 4096\nthink: true\n")
    cfg = agent_config.load(path)
    assert cfg.model == "granite4.1:8b"
    assert cfg.num_ctx == 4096
    assert cfg.think is True
    assert cfg.temperature == 0.2  # untouched fields keep their default


def test_config_version_may_be_omitted(tmp_path):
    path = tmp_path / "agent.yaml"
    path.write_text("model: ornith:latest\n")
    assert agent_config.load(path).model == "ornith:latest"


def test_config_rejects_wrong_version(tmp_path):
    path = tmp_path / "agent.yaml"
    path.write_text("version: 2\nmodel: x\n")
    with pytest.raises(CatalogError, match="version must be 1"):
        agent_config.load(path)


def test_config_rejects_unknown_field(tmp_path):
    path = tmp_path / "agent.yaml"
    path.write_text("version: 1\nmodle: typo\n")
    with pytest.raises(CatalogError, match="unknown field"):
        agent_config.load(path)


@pytest.mark.parametrize(
    "body, match",
    [
        ("num_ctx: 0\n", "positive integer"),
        ("num_ctx: -5\n", "positive integer"),
        ("temperature: hot\n", "must be a number"),
        ("think: maybe\n", "must be true or false"),
        ("model: ''\n", "non-empty string"),
        ("auto_approve: write_file\n", "list of strings"),
    ],
)
def test_config_rejects_bad_values(tmp_path, body, match):
    path = tmp_path / "agent.yaml"
    path.write_text(body)
    with pytest.raises(CatalogError, match=match):
        agent_config.load(path)


def test_config_strips_trailing_slash_from_host(tmp_path):
    path = tmp_path / "agent.yaml"
    path.write_text("host: http://box:11434/\n")
    assert agent_config.load(path).host == "http://box:11434"


def test_cli_overrides_win_over_file():
    cfg = AgentConfig(model="from-file", host="http://a:1")
    agent_config.apply_overrides(cfg, model="from-flag", host="http://b:2/")
    assert cfg.model == "from-flag"
    assert cfg.host == "http://b:2"


def test_cli_overrides_ignore_none():
    cfg = AgentConfig(model="keep")
    agent_config.apply_overrides(cfg, model=None, host=None)
    assert cfg.model == "keep"


# -- parse_message ---------------------------------------------------------------


def test_parse_plain_content():
    msg = parse_message({"message": {"role": "assistant", "content": "hello"}})
    assert msg.content == "hello"
    assert msg.thinking == ""
    assert msg.tool_calls == []


def test_parse_separates_thinking_from_content():
    msg = parse_message(
        {"message": {"role": "assistant", "thinking": "let me see", "content": "the answer"}}
    )
    assert msg.thinking == "let me see"
    assert msg.content == "the answer"


def test_parse_extracts_inline_think_tags():
    """Some Ollama builds ignore think:false and emit <think> tags in content
    instead of populating the thinking field -- observed with lfm2.5."""
    msg = parse_message(
        {"message": {"content": "<think>\nlet me count words\n</think>\nHello there friend."}}
    )
    assert msg.content == "Hello there friend."
    assert msg.thinking == "let me count words"


def test_parse_handles_unterminated_think_tag():
    msg = parse_message({"message": {"content": "<think>reasoning that got cut off"}})
    assert msg.content == ""
    assert msg.thinking == "reasoning that got cut off"


def test_parse_prefers_native_thinking_field_over_inline():
    msg = parse_message(
        {"message": {"thinking": "native", "content": "<think>inline</think>answer"}}
    )
    assert msg.thinking == "native"
    assert msg.content == "answer"


def test_parse_finds_tool_call_after_a_think_block():
    """A <think> preamble must not hide a JSON tool call from the fallback."""
    msg = parse_message(
        {"message": {"content": '<think>I should list it</think>\n{"name": "list_dir", "arguments": {}}'}}
    )
    assert [c.name for c in msg.tool_calls] == ["list_dir"]
    assert msg.thinking == "I should list it"


def test_parse_content_without_think_tags_is_untouched():
    msg = parse_message({"message": {"content": "plain answer"}})
    assert msg.content == "plain answer"
    assert msg.thinking == ""


def test_parse_native_tool_calls():
    msg = parse_message(
        {
            "message": {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"function": {"name": "read_file", "arguments": {"path": "a.py"}}}
                ],
            }
        }
    )
    assert [c.name for c in msg.tool_calls] == ["read_file"]
    assert msg.tool_calls[0].arguments == {"path": "a.py"}


def test_parse_double_encoded_arguments():
    """Smaller models often hand back arguments as a JSON string."""
    msg = parse_message(
        {
            "message": {
                "tool_calls": [
                    {"function": {"name": "write_file", "arguments": '{"path": "x", "content": "y"}'}}
                ]
            }
        }
    )
    assert msg.tool_calls[0].arguments == {"path": "x", "content": "y"}


def test_parse_falls_back_to_tool_call_in_content():
    msg = parse_message(
        {"message": {"content": '{"name": "list_dir", "arguments": {"path": "."}}'}}
    )
    assert [c.name for c in msg.tool_calls] == ["list_dir"]
    assert msg.tool_calls[0].arguments == {"path": "."}
    # The JSON was the call, not an answer to show the user.
    assert msg.content == ""


def test_parse_falls_back_to_fenced_tool_call():
    msg = parse_message(
        {"message": {"content": '```json\n{"name": "list_dir", "arguments": {}}\n```'}}
    )
    assert [c.name for c in msg.tool_calls] == ["list_dir"]


def test_parse_does_not_mistake_prose_for_a_tool_call():
    content = 'You can pass {"name": "x"} to that function.'
    msg = parse_message({"message": {"content": content}})
    assert msg.tool_calls == []
    assert msg.content == content


def test_parse_ignores_json_object_without_a_name():
    msg = parse_message({"message": {"content": '{"path": "a.py"}'}})
    assert msg.tool_calls == []
    assert msg.content == '{"path": "a.py"}'


def test_parse_tolerates_missing_message():
    msg = parse_message({})
    assert msg.content == ""
    assert msg.tool_calls == []


# -- client / error mapping ------------------------------------------------------


def test_list_models_reads_tags():
    client = client_for({"/api/tags": {"models": [{"model": "lfm2.5:latest"}, {"model": "ornith:latest"}]}})
    assert client.list_models() == ["lfm2.5:latest", "ornith:latest"]


def test_capabilities_reads_show():
    client = client_for({"/api/show": {"capabilities": ["completion", "tools", "thinking"]}})
    assert client.capabilities("lfm2.5:latest") == ["completion", "tools", "thinking"]


def test_chat_sends_expected_payload():
    transport = fake_transport({"/api/chat": {"message": {"content": "hi"}}})
    client = OllamaClient("http://localhost:11434", transport=transport)
    client.chat([{"role": "user", "content": "yo"}], model="m", temperature=0.5, num_ctx=2048)

    _, path, payload = transport.calls[0]  # type: ignore[attr-defined]
    assert path == "/api/chat"
    assert payload["stream"] is False
    assert payload["options"] == {"temperature": 0.5, "num_ctx": 2048}
    assert "tools" not in payload  # no tools in milestone 1


def test_connection_refused_maps_to_unavailable():
    err = urllib.error.URLError(ConnectionRefusedError("refused"))
    client = client_for({"/api/tags": err})
    with pytest.raises(OllamaUnavailable, match="Cannot reach Ollama"):
        client.list_models()


def test_timeout_maps_to_timeout_error():
    client = client_for({"/api/tags": urllib.error.URLError(TimeoutError())})
    with pytest.raises(OllamaTimeout):
        client.list_models()


def test_bare_timeout_maps_to_timeout_error():
    client = client_for({"/api/tags": TimeoutError()})
    with pytest.raises(OllamaTimeout):
        client.list_models()


def test_http_404_maps_to_model_not_found():
    err = urllib.error.HTTPError("http://x", 404, "Not Found", {}, None)
    client = client_for({"/api/show": err})
    with pytest.raises(ModelNotFound):
        client.capabilities("ghost")


def test_http_500_maps_to_generic_error():
    err = urllib.error.HTTPError("http://x", 500, "Boom", {}, None)
    client = client_for({"/api/tags": err})
    with pytest.raises(OllamaError, match="HTTP 500"):
        client.list_models()


def test_non_json_response_maps_to_generic_error():
    client = client_for({"/api/tags": json.JSONDecodeError("bad", "", 0)})
    with pytest.raises(OllamaError, match="non-JSON"):
        client.list_models()


# -- preflight -------------------------------------------------------------------


@pytest.fixture()
def ollama_on_path(monkeypatch):
    monkeypatch.setattr(preflight.shutil, "which", lambda _: "/usr/local/bin/ollama")


def test_preflight_passes_and_resolves_tag(ollama_on_path):
    client = client_for(
        {
            "/api/tags": {"models": [{"model": "lfm2.5:latest"}]},
            "/api/show": {"capabilities": ["completion", "tools"]},
        }
    )
    # User typed the bare name; preflight resolves it to the full tag.
    assert preflight.check(AgentConfig(model="lfm2.5"), client) == "lfm2.5:latest"


def test_preflight_reports_missing_binary(monkeypatch):
    monkeypatch.setattr(preflight.shutil, "which", lambda _: None)
    client = client_for({})
    with pytest.raises(preflight.PreflightError) as exc:
        preflight.check(AgentConfig(), client)
    assert "not installed" in str(exc.value)
    assert "devstuff install ollama" in exc.value.remedies


def test_preflight_skips_binary_check_for_remote_host(monkeypatch):
    monkeypatch.setattr(preflight.shutil, "which", lambda _: None)
    client = client_for(
        {
            "/api/tags": {"models": [{"model": agent_config.DEFAULT_MODEL}]},
            "/api/show": {"capabilities": ["tools"]},
        }
    )
    cfg = AgentConfig(host="http://gpubox:11434")
    assert preflight.check(cfg, client) == agent_config.DEFAULT_MODEL


def test_preflight_reports_unreachable_daemon(ollama_on_path):
    client = client_for({"/api/tags": urllib.error.URLError(ConnectionRefusedError("refused"))})
    with pytest.raises(preflight.PreflightError) as exc:
        preflight.check(AgentConfig(), client)
    assert any("systemctl start ollama" in r for r in exc.value.remedies)


def test_preflight_reports_unpulled_model_and_lists_alternatives(ollama_on_path):
    client = client_for({"/api/tags": {"models": [{"model": "granite4.1:8b"}]}})
    with pytest.raises(preflight.PreflightError) as exc:
        preflight.check(AgentConfig(model="lfm2.5:latest"), client)
    assert "not available locally" in str(exc.value)
    assert any("ollama pull lfm2.5:latest" in r for r in exc.value.remedies)
    assert any("granite4.1:8b" in r for r in exc.value.remedies)


def test_preflight_rejects_model_without_tool_support(ollama_on_path):
    def show(payload):
        caps = {"nomic-embed:latest": ["completion"], "granite4.1:8b": ["completion", "tools"]}
        return {"capabilities": caps[payload["model"]]}

    client = client_for(
        {
            "/api/tags": {"models": [{"model": "nomic-embed:latest"}, {"model": "granite4.1:8b"}]},
            "/api/show": show,
        }
    )
    with pytest.raises(preflight.PreflightError) as exc:
        preflight.check(AgentConfig(model="nomic-embed:latest"), client)
    assert "does not support tool-calling" in str(exc.value)
    # The error names a model the user can actually switch to.
    assert any("granite4.1:8b" in r for r in exc.value.remedies)
