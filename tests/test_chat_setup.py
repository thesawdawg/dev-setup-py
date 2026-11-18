"""`tui --chat` provider setup — the TUI's opt-in path to a model. Covers the
Ollama pick/pull flow (confirm-gated, managed runner) and the OpenAI-compat
adapter end-to-end through a real scoped session with a fake transport."""

from __future__ import annotations

import json

import pytest

from dev_setup import catalog, functions_catalog, functions_registry, registry, ui
from dev_setup.agent import chat_setup
from dev_setup.agent import config as agent_config


@pytest.fixture()
def catalogs(tmp_path, monkeypatch):
    monkeypatch.setattr(catalog, "USER_CATALOG_PATH", tmp_path / "tools.yaml")
    monkeypatch.setattr(catalog, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(
        functions_catalog, "USER_CATALOG_PATH", tmp_path / "functions.yaml"
    )
    monkeypatch.setattr(functions_catalog, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(agent_config, "USER_CONFIG_PATH", tmp_path / "agent.yaml")
    reloads = []
    monkeypatch.setattr(registry, "reload", lambda: reloads.append("tools"))
    monkeypatch.setattr(
        functions_registry, "reload", lambda: reloads.append("functions")
    )
    return reloads


@pytest.fixture()
def fake_ollama(monkeypatch):
    """A loopback daemon that is empty until the pull 'lands'."""
    monkeypatch.setattr(chat_setup.shutil, "which", lambda name: "/usr/bin/ollama")
    state = {"installed": []}

    class FakeClient:
        def __init__(self, host, timeout=0, *, local_chat=False):
            self.host = host

        def list_models(self):
            return list(state["installed"])

        def capabilities(self, model):
            return ["tools"]

    monkeypatch.setattr(chat_setup, "OllamaClient", FakeClient)
    runs = []

    def fake_run(cmd, **kwargs):
        runs.append((list(cmd), kwargs))
        state["installed"].append(cmd[-1])

    monkeypatch.setattr(chat_setup.runner, "run", fake_run)
    launched = {}
    monkeypatch.setattr(
        chat_setup,
        "run_scoped_chat",
        lambda client, cfg, resolved, **kw: launched.update(
            client=client, resolved=resolved, num_ctx=cfg.num_ctx
        ),
    )
    state["runs"] = runs
    state["launched"] = launched
    return state


def test_ollama_missing_model_pulls_once_then_launches(catalogs, fake_ollama, monkeypatch):
    monkeypatch.setattr(ui, "confirm", lambda *a, **k: True)
    chat_setup.start_chat(
        model="qwen3:1.7b",
        host=None,
        provider="ollama",
        api_key_env=None,
        browse_catalog=None,
    )
    (argv, kw), = fake_ollama["runs"]
    assert argv == ["/usr/bin/ollama", "pull", "qwen3:1.7b"]
    assert kw["env"]["OLLAMA_NO_CLOUD"] == "1"
    assert kw["env"]["OLLAMA_HOST"] == "http://127.0.0.1:11434"
    assert kw["timeout"] == 1800
    assert fake_ollama["launched"]["resolved"] == "qwen3:1.7b"
    assert fake_ollama["launched"]["num_ctx"] == 4096


def test_ollama_declined_pull_runs_nothing(catalogs, fake_ollama, monkeypatch):
    monkeypatch.setattr(ui, "confirm", lambda *a, **k: False)
    chat_setup.start_chat(
        model="qwen3:1.7b",
        host=None,
        provider="ollama",
        api_key_env=None,
        browse_catalog=None,
    )
    assert fake_ollama["runs"] == []
    assert fake_ollama["launched"] == {}


@pytest.mark.parametrize(
    "ram,vram,expected",
    [
        # Unknown memory: only the smallest row, never a fit claim.
        (None, None, ["qwen3:1.7b"]),
        # 8 GB CPU-only: the two smallest rows fit the RAM leg.
        (8.0, None, ["qwen3:1.7b", "granite4.1:3b"]),
        # 8 GB RAM + 8 GB VRAM: the GPU leg unlocks the larger rows.
        (
            8.0,
            8.0,
            ["qwen3:1.7b", "granite4.1:3b", "qwen3:4b", "qwen3:8b"],
        ),
    ],
)
def test_suggestion_rows_follow_the_hardware_heuristic(ram, vram, expected):
    assert [s[0] for s in chat_setup._suggested(ram, vram)] == expected


def test_openai_session_echoes_tool_call_ids(catalogs, monkeypatch):
    """A real scoped AgentSession over a fake OpenAI transport: /models feeds
    the picker, the model's tool call gets its id echoed on the tool result,
    and only the scoped tool surface is reachable."""
    from dev_setup.agent.openai_compat import OpenAIClient
    from dev_setup.commands.agent_cmd import _scoped_session

    requests = []

    def transport(method, path, payload, timeout):
        requests.append((method, path, payload))
        if path == "/models":
            return {"data": [{"id": "qwen3-8b@agent"}]}
        if any(m.get("role") == "tool" for m in payload["messages"]):
            return {
                "choices": [{
                    "message": {
                        "role": "assistant",
                        "content": "ripgrep is a package",
                        "tool_calls": [],
                    }
                }]
            }
        return {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{
                        "id": "call_abc",
                        "type": "function",
                        "function": {
                            "name": "find_tools",
                            "arguments": json.dumps({"query": "ripgrep"}),
                        },
                    }],
                }
            }]
        }

    monkeypatch.setattr(
        chat_setup,
        "OpenAIClient",
        lambda host, **kw: OpenAIClient(host, transport=transport),
    )
    monkeypatch.setattr(ui, "select", lambda *a, **k: "qwen3-8b@agent")
    monkeypatch.setattr(ui, "text_input", lambda *a, **k: "")
    sessions = []
    monkeypatch.setattr(
        chat_setup,
        "run_scoped_chat",
        lambda client, cfg, resolved, **kw: sessions.append((client, cfg, resolved)),
    )
    chat_setup.start_chat(
        model=None,
        host="http://127.0.0.1:8080/v1",
        provider="openai",
        api_key_env=None,
        browse_catalog=None,
    )
    client, cfg, resolved = sessions[0]
    assert resolved == "qwen3-8b@agent"

    sess = _scoped_session(client, cfg, resolved, can_prompt=False)
    reply = sess.send("find ripgrep")
    assert reply.content == "ripgrep is a package"

    # First the model list, then two completions.
    assert requests[0][0:2] == ("GET", "/models")
    completions = [p for m, path, p in requests if path == "/chat/completions"]
    assert len(completions) == 2
    assert all(p["model"] == "qwen3-8b@agent" for p in completions)
    history = completions[1]["messages"]
    assistant = next(m for m in history if m.get("tool_calls"))
    (wire_call,) = assistant["tool_calls"]
    assert wire_call["id"] == "call_abc"
    assert wire_call["function"] == {
        "name": "find_tools",
        "arguments": json.dumps({"query": "ripgrep"}),
    }
    (tool_msg,) = [m for m in history if m["role"] == "tool"]
    assert tool_msg == {
        "role": "tool",
        "tool_call_id": "call_abc",
        "content": tool_msg["content"],
    }
    assert '"ripgrep"' in tool_msg["content"]  # the scoped catalog answer
    assert "tool_name" not in tool_msg
    # Session history kept the id for the wire serializer to find.
    tool_result = next(m for m in sess.messages if m["role"] == "tool")
    assert tool_result["tool_call_id"] == "call_abc"
    assert tool_result["tool_name"] == "find_tools"


def test_failed_second_request_rolls_back_the_whole_turn(catalogs):
    """A provider error mid-turn leaves no orphaned assistant/tool messages
    — the next request must not carry a tool call without its results."""
    from dev_setup.agent import session as session_mod
    from dev_setup.agent.ollama import OllamaError
    from dev_setup.agent.openai_compat import OpenAIClient
    from dev_setup.commands.agent_cmd import _scoped_session

    requests = []
    state = {"n": 0}

    def transport(method, path, payload, timeout):
        requests.append(payload)
        state["n"] += 1
        if state["n"] == 1:
            return {
                "choices": [{
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [{
                            "id": "call_abc",
                            "type": "function",
                            "function": {
                                "name": "find_tools",
                                "arguments": json.dumps({"query": "ripgrep"}),
                            },
                        }],
                    }
                }]
            }
        if state["n"] == 2:
            raise OllamaError("endpoint returned HTTP 500")
        return {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "done",
                    "tool_calls": [],
                }
            }]
        }

    client = OpenAIClient("http://127.0.0.1:8080/v1", transport=transport)
    sess = _scoped_session(
        client, agent_config.AgentConfig(), "m", can_prompt=False
    )
    assert [m["role"] for m in sess.messages] == ["system"]

    # Turn one: model calls find_tools, the result lands, then the second
    # request fails — the whole turn is rolled back, not left half-written.
    session_mod._turn(sess, "find ripgrep")
    assert [m["role"] for m in sess.messages] == ["system"]

    # Turn two succeeds and carries only the system prompt and its own user
    # message — nothing orphaned from the failed turn.
    session_mod._turn(sess, "again")
    assert [m.get("role") for m in requests[-1]["messages"]] == [
        "system", "user",
    ]


def test_openai_off_loopback_decline_contacts_nothing(monkeypatch):
    """Declining the send-data-off-machine confirm stops before the client —
    let alone any request — exists."""
    monkeypatch.setattr(ui, "text_input", lambda *a, **k: "")
    monkeypatch.setattr(ui, "confirm", lambda *a, **k: False)
    monkeypatch.setattr(
        chat_setup,
        "OpenAIClient",
        lambda *a, **k: pytest.fail("a client was constructed"),
    )
    chat_setup.start_chat(
        model="m",
        host="https://chat.remote.example/v1",
        provider="openai",
        api_key_env=None,
        browse_catalog=None,
    )
