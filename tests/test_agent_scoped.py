"""Scoped assistant essentials: discovery/draft/registration are free, but
every execution or save previews and asks — and `can_prompt=False` refuses
mutations even when the model claims approval flags of its own."""

from __future__ import annotations

import json

import pytest
import yaml

from dev_setup import catalog, functions_catalog, functions_registry, registry, ui
from dev_setup.agent import session as session_mod
from dev_setup.agent.config import AgentConfig
from dev_setup.agent.ollama import Message, ToolCall
from dev_setup.agent.sandbox import Workspace
from dev_setup.agent.scoped import ScopedActions
from dev_setup.generic import GenericTool


class ScriptedClient:
    """Replays canned replies; a callable reply gets the live messages, so the
    script can answer with data the run produced (e.g. a draft id)."""

    host = "scripted"

    def __init__(self, replies):
        self.replies = list(replies)
        self.sent = []

    def chat(self, messages, **kwargs):
        self.sent.append(list(messages))
        if not self.replies:
            return Message(content="(out of scripted replies)")
        reply = self.replies.pop(0)
        return reply(messages) if callable(reply) else reply


def call(name, **arguments):
    return Message(content="", tool_calls=[ToolCall(name=name, arguments=arguments)])


def text(content):
    return Message(content=content)


def last_tool_result(session):
    return next(
        m for m in reversed(session.messages) if m["role"] == "tool"
    )["content"]


def latest_draft_call(messages):
    """The register call for the most recent draft — skips refusals and other
    non-JSON tool results while looking for the draft id."""
    for m in reversed(messages):
        if m["role"] != "tool":
            continue
        try:
            data = json.loads(m["content"])
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(data, dict) and "draft_id" in data:
            return call("register_extension", draft_id=data["draft_id"])
    raise AssertionError("no draft_id in the tool results so far")


@pytest.fixture()
def catalogs(tmp_path, monkeypatch):
    """Isolate the user catalogs in tmp_path; record instead of reload."""
    monkeypatch.setattr(catalog, "USER_CATALOG_PATH", tmp_path / "tools.yaml")
    monkeypatch.setattr(catalog, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(functions_catalog, "USER_CATALOG_PATH", tmp_path / "functions.yaml")
    monkeypatch.setattr(functions_catalog, "CONFIG_DIR", tmp_path)
    reloads = []
    monkeypatch.setattr(registry, "reload", lambda: reloads.append("tools"))
    monkeypatch.setattr(functions_registry, "reload", lambda: reloads.append("functions"))
    return reloads


@pytest.fixture()
def approvals(monkeypatch):
    asked = []
    answers = []

    def fake_confirm(prompt, default=False):
        asked.append(prompt)
        return answers.pop(0)

    fake_confirm.asked = asked
    fake_confirm.answers = answers
    monkeypatch.setattr(ui, "confirm", fake_confirm)
    return fake_confirm


@pytest.fixture()
def ws(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    return Workspace.create(root)


def make_session(ws, replies, *, can_prompt=True):
    scoped = ScopedActions(can_prompt=can_prompt)
    return session_mod.AgentSession(
        ScriptedClient(replies),  # type: ignore[arg-type]
        AgentConfig(),
        ws,
        model="test-model",
        scoped_actions=scoped,
    )


def test_scoped_flow_drafts_registers_once_and_declines_execution(
    catalogs, approvals, ws, monkeypatch
):
    """find → read → draft → register → (flags ignored) → install denied."""
    monkeypatch.setattr(
        GenericTool, "install",
        lambda *a, **k: pytest.fail("install ran after a declined approval"),
    )
    monkeypatch.setattr(
        "dev_setup.runner.run",
        lambda *a, **k: pytest.fail("a subprocess ran without approval"),
    )
    approvals.answers.extend([True, False])  # allow the save, deny the install

    session = make_session(
        ws,
        [
            call("find_tools", query=""),
            call("read_tool", kind="package", key="docker"),
            call(
                "draft_extension", kind="package", key="demo-x",
                definition_json=json.dumps({
                    "name": "Demo",
                    "type": "apt",
                    "apt_packages": "demo-x-pkg",
                    "check_cmd": "demo-x",
                }),
            ),
            latest_draft_call,
            # Draft IDs are consumed on save: a second register must not
            # double-save, and must not even reach an approval prompt.
            latest_draft_call,
            # A model-supplied "approval" flag is not a parameter — the call
            # fails on shape, before any prompt.
            call("install_tool", key="demo-x", preapproved=True),
            call("install_tool", key="demo-x"),  # preview shown, user says no
            text("all done"),
        ],
    )
    reply = session.send("add demo-x and install it")
    assert reply.content == "all done"

    # Exactly one save: the draft validated once, saved once, reloaded once.
    saved = yaml.safe_load(catalog.USER_CATALOG_PATH.read_text())
    assert list(saved["tools"]) == ["demo-x"]
    assert saved["tools"]["demo-x"]["type"] == "apt"
    assert saved["tools"]["demo-x"]["apt_packages"] == "demo-x-pkg"
    assert catalogs == ["tools"]

    # Two prompts only: save + install. The second register never asked.
    assert approvals.asked == [
        "Save this new catalog entry?",
        "Run this devstuff action?",
    ]

    contents = [m["content"] for m in session.messages if m["role"] == "tool"]
    assert any("unknown draft" in c for c in contents)          # consumed
    assert any("unexpected parameter" in c for c in contents)   # flags refused
    assert any("declined" in c for c in contents)               # install denied


def test_scoped_run_function_executes_managed_after_approval(
    catalogs, approvals, ws, monkeypatch
):
    functions_catalog.USER_CATALOG_PATH.write_text(yaml.safe_dump({
        "version": 1,
        "functions": {
            "greet": {
                "type": "script",
                "script": 'echo "hi $target"',
                "params": [{"name": "target", "required": True}],
            },
        },
    }))
    approvals.answers.append(True)
    monkeypatch.setenv("SUDO_ASKPASS", "/bin/askpass")

    captured = {}

    def fake_run(cmd, *, cwd, env, on_output, **kwargs):
        captured.update(cmd=cmd, env=env, kwargs=kwargs)
        on_output("hi world\n")

    monkeypatch.setattr("dev_setup.runner.run", fake_run)

    session = make_session(
        ws,
        [
            call("run_function", key="greet", arguments_json='{"target": "world"}'),
            text("ran it"),
        ],
    )
    reply = session.send("run greet for world")
    assert reply.content == "ran it"

    assert approvals.asked == ["Run this devstuff action?"]
    cmd = captured["cmd"]
    assert cmd[0] == "bash" and cmd[1].endswith(".sh") and cmd[2:] == ["world"]
    assert captured["kwargs"]["timeout"] == 120
    assert captured["kwargs"]["privileged"] is True
    assert "SUDO_ASKPASS" not in captured["env"]
    assert "hi world" in last_tool_result(session)


def test_no_prompt_means_no_mutation_even_with_model_flags(
    catalogs, approvals, ws, monkeypatch
):
    """--print / non-tty: the model may look and draft, never execute or save."""
    monkeypatch.setattr(
        GenericTool, "install",
        lambda *a, **k: pytest.fail("install ran without an interactive approval"),
    )

    session = make_session(
        ws,
        [
            call("find_tools", query="ripgrep"),
            call("install_tool", key="ripgrep", confirm=False, yolo=True),
            call("install_tool", key="ripgrep"),
            call(
                "draft_extension", kind="package", key="demo-y",
                definition_json=json.dumps({"type": "apt", "apt_packages": "y"}),
            ),
            call("register_extension", draft_id="anything", trusted=True),
            latest_draft_call,  # the real draft — refused at the approval gate
            text("done"),
        ],
        can_prompt=False,
    )
    reply = session.send("do things")
    assert reply.content == "done"

    contents = [m["content"] for m in session.messages if m["role"] == "tool"]
    # read-only calls still work in read-only mode
    assert any('"kind": "package"' in c for c in contents)
    # mutating calls refused: flag-smuggling fails on shape, the honest calls
    # fail on the missing approval path
    assert sum("unexpected parameter" in c for c in contents) == 2
    assert sum("interactive approval" in c for c in contents) == 2
    assert approvals.asked == []
    assert not catalog.USER_CATALOG_PATH.exists()


def test_duplicate_keys_are_refused_at_draft_time(catalogs, approvals, ws):
    session = make_session(
        ws,
        [
            call(
                "draft_extension", kind="package", key="docker",
                definition_json=json.dumps({"type": "apt", "apt_packages": "d"}),
            ),
            # apt_packages is a string the installer splits — a model-supplied
            # list is malformed even though it parses as JSON.
            call(
                "draft_extension", kind="package", key="demo-bad",
                definition_json=json.dumps({"type": "apt", "apt_packages": ["d"]}),
            ),
            text("done"),
        ],
    )
    session.send("overwrite docker")
    contents = [m["content"] for m in session.messages if m["role"] == "tool"]
    assert "already exists" in contents[0]
    assert "must be a string" in contents[1]
    assert approvals.asked == []
