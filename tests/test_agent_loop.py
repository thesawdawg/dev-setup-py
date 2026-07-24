from __future__ import annotations

import pytest

from dev_setup.agent import loop
from dev_setup.agent.approval import ApprovalPolicy
from dev_setup.agent.config import AgentConfig
from dev_setup.agent.ollama import Message, ToolCall
from dev_setup.agent.registry import AgentParam, AgentTool
from dev_setup.agent.sandbox import Workspace
from dev_setup.agent.session import AgentSession


class ScriptedClient:
    """Replays canned assistant messages, recording what it was sent."""

    host = "scripted"

    def __init__(self, replies: list[Message]) -> None:
        self.replies = list(replies)
        self.sent: list[list[dict]] = []

    def chat(self, messages, **kwargs):
        import copy

        self.sent.append(copy.deepcopy(messages))
        if not self.replies:
            return Message(content="(out of scripted replies)")
        return self.replies.pop(0)


class AlwaysApprove(ApprovalPolicy):
    def confirm(self, tool, args, ws):
        return True


class AlwaysDecline(ApprovalPolicy):
    def confirm(self, tool, args, ws):
        return False


@pytest.fixture()
def ws(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    return Workspace.create(root)


def make_session(ws, replies, *, policy=None, config=None, tools=None):
    client = ScriptedClient(replies)
    return AgentSession(
        client,  # type: ignore[arg-type]
        config or AgentConfig(),
        ws,
        model="test-model",
        tools=tools if tools is not None else _default_tools(),
        policy=policy or AlwaysApprove(),
    )


def _default_tools():
    from dev_setup.agent import registry

    return registry.build()


def text(content):
    return Message(content=content)


def call(name, **arguments):
    return Message(content="", tool_calls=[ToolCall(name=name, arguments=arguments)])


# -- happy path ------------------------------------------------------------------


def test_plain_answer_needs_no_tools(ws):
    session = make_session(ws, [text("hello there")])
    reply = session.send("hi")
    assert reply.content == "hello there"


def test_tool_call_then_answer(ws):
    session = make_session(
        ws,
        [call("write_file", path="a.txt", content="hi\n"), text("Created a.txt.")],
    )
    reply = session.send("make a.txt")
    assert (ws.root / "a.txt").read_text() == "hi\n"
    assert reply.content == "Created a.txt."


def test_tool_result_is_fed_back_to_the_model(ws):
    session = make_session(ws, [call("list_dir", path="."), text("done")])
    session.send("what is here")

    second_request = session.client.sent[1]  # type: ignore[attr-defined]
    assert second_request[-1]["role"] == "tool"
    assert second_request[-1]["tool_name"] == "list_dir"
    # The assistant's own tool call must be in the history too, or the model sees
    # a result with no matching request.
    assert second_request[-2]["role"] == "assistant"
    assert second_request[-2]["tool_calls"][0]["function"]["name"] == "list_dir"


def test_multi_step_sequence(ws):
    """The driving use case: mkdir, cd, then write a file inside it."""
    session = make_session(
        ws,
        [
            call("run_command", command="mkdir xyz-project"),
            call("cd", path="xyz-project"),
            call("write_file", path="main.py", content="print('hi')\n"),
            text("Project created."),
        ],
    )
    reply = session.send("create a project called xyz-project")
    assert (ws.root / "xyz-project" / "main.py").read_text() == "print('hi')\n"
    assert reply.content == "Project created."


# -- error handling --------------------------------------------------------------


def test_unknown_tool_is_reported_to_the_model_not_raised(ws):
    session = make_session(ws, [call("teleport", to="mars"), text("sorry")])
    reply = session.send("go")
    tool_msg = session.client.sent[1][-1]  # type: ignore[attr-defined]
    assert "unknown tool 'teleport'" in tool_msg["content"]
    assert reply.content == "sorry"


def test_missing_required_argument_is_reported(ws):
    session = make_session(ws, [call("read_file"), text("ok")])
    session.send("read something")
    assert "missing required parameter 'path'" in session.client.sent[1][-1]["content"]  # type: ignore[attr-defined]


def test_unexpected_argument_is_reported_with_the_real_signature(ws):
    session = make_session(ws, [call("list_dir", directory="."), text("ok")])
    session.send("list")
    content = session.client.sent[1][-1]["content"]  # type: ignore[attr-defined]
    assert "unexpected parameter" in content
    assert "path" in content  # tells the model what it should have used


def test_sandbox_refusal_is_reported_so_the_model_can_replan(ws):
    session = make_session(ws, [call("run_command", command="sudo apt install x"), text("ok")])
    session.send("install x")
    content = session.client.sent[1][-1]["content"]  # type: ignore[attr-defined]
    assert "not permitted" in content
    assert "install_tool" in content  # points at the right alternative


def test_path_escape_is_reported_not_fatal(ws):
    session = make_session(ws, [call("read_file", path="../../etc/passwd"), text("ok")])
    reply = session.send("read passwd")
    assert "escapes the workspace" in session.client.sent[1][-1]["content"]  # type: ignore[attr-defined]
    assert reply.content == "ok"


def test_tool_crash_does_not_end_the_session(ws, monkeypatch):
    from dev_setup.agent import primitives

    def boom(*_args, **_kwargs):
        raise RuntimeError("kaboom")

    monkeypatch.setitem(primitives._PRIMITIVES, "list_dir", boom)
    session = make_session(ws, [call("list_dir", path="."), text("recovered")])
    reply = session.send("list")
    assert "kaboom" in session.client.sent[1][-1]["content"]  # type: ignore[attr-defined]
    assert reply.content == "recovered"


# -- approval --------------------------------------------------------------------


def test_declined_call_reports_back_and_continues(ws):
    session = make_session(
        ws,
        [call("write_file", path="a.txt", content="x"), text("understood")],
        policy=AlwaysDecline(),
    )
    reply = session.send("write a.txt")
    assert not (ws.root / "a.txt").exists()
    assert "declined" in session.client.sent[1][-1]["content"]  # type: ignore[attr-defined]
    assert reply.content == "understood"


def test_read_only_tools_are_not_confirmed(ws):
    policy = ApprovalPolicy()
    tools = _default_tools()
    assert policy.needs_confirmation(tools["read_file"]) is False
    assert policy.needs_confirmation(tools["list_dir"]) is False
    assert policy.needs_confirmation(tools["write_file"]) is True
    assert policy.needs_confirmation(tools["run_command"]) is True
    assert policy.needs_confirmation(tools["install_tool"]) is True


def test_yolo_skips_confirmation(ws):
    policy = ApprovalPolicy(yolo=True)
    assert policy.needs_confirmation(_default_tools()["run_command"]) is False


def test_auto_approve_list_skips_confirmation(ws):
    policy = ApprovalPolicy(auto_approve=["write_file"])
    tools = _default_tools()
    assert policy.needs_confirmation(tools["write_file"]) is False
    assert policy.needs_confirmation(tools["run_command"]) is True


def test_always_choice_persists_for_the_session(ws):
    calls = []

    class CountingPolicy(ApprovalPolicy):
        def confirm(self, tool, args, workspace):
            calls.append(tool.key)
            self.always.add(tool.key)
            return True

    session = make_session(
        ws,
        [
            call("write_file", path="a.txt", content="1"),
            call("write_file", path="b.txt", content="2"),
            text("done"),
        ],
        policy=CountingPolicy(),
    )
    session.send("write two files")
    assert calls == ["write_file"]  # asked once, not twice
    assert (ws.root / "b.txt").exists()


def test_non_interactive_declines_instead_of_auto_approving(ws):
    """--print without --yolo must not become an unattended agent with write access."""
    policy = ApprovalPolicy(can_prompt=False)
    tools = _default_tools()
    assert policy.confirm(tools["write_file"], {"path": "a.txt", "content": "x"}, ws) is False


# -- limits ----------------------------------------------------------------------


def test_iteration_cap_stops_a_runaway_loop(ws):
    config = AgentConfig(max_iterations=3)
    session = make_session(ws, [call("list_dir", path=".")] * 10, config=config)
    assert session.send("loop forever") is None
    assert len(session.client.sent) == 3  # type: ignore[attr-defined]


def test_tool_output_is_truncated(ws):
    config = AgentConfig(max_tool_output_bytes=100)
    session = make_session(
        ws, [call("run_command", command="head -c 5000 /dev/zero | tr '\\0' 'a'"), text("ok")],
        config=config,
    )
    session.send("make noise")
    content = session.client.sent[1][-1]["content"]  # type: ignore[attr-defined]
    assert "truncated" in content
    assert len(content.encode()) < 400


def test_truncate_marks_dropped_bytes():
    out, was_truncated = loop.truncate("x" * 500, 100)
    assert was_truncated
    assert "400 bytes truncated" in out


def test_truncate_leaves_short_output_alone():
    out, was_truncated = loop.truncate("short", 100)
    assert out == "short"
    assert was_truncated is False


def test_truncate_does_not_split_a_multibyte_character():
    out, _ = loop.truncate("é" * 100, 51)
    assert "�" not in out


# -- system prompt ---------------------------------------------------------------


def test_system_prompt_states_the_workspace(ws):
    session = make_session(ws, [])
    assert str(ws.root) in session.messages[0]["content"]


def test_reset_clears_history_but_keeps_the_system_prompt(ws):
    session = make_session(ws, [text("hi")])
    session.send("hello")
    assert len(session.messages) > 1
    session.reset()
    assert len(session.messages) == 1
    assert session.messages[0]["role"] == "system"


def test_schemas_are_generated_for_every_tool(ws):
    tools = {"t": AgentTool("t", "T", "d", "primitive", params=[AgentParam(name="p")])}
    session = make_session(ws, [], tools=tools)
    assert session.schemas[0]["function"]["name"] == "t"


# -- transcript ------------------------------------------------------------------


def test_transcript_is_written_after_each_turn(ws, tmp_path):
    from dev_setup.agent.transcript import Transcript

    path = tmp_path / "t.json"
    transcript = Transcript(path, model="m", host="h", workspace=str(ws.root))
    client = ScriptedClient([text("hi"), text("again")])
    session = AgentSession(
        client, AgentConfig(), ws, model="m", tools={}, policy=AlwaysApprove(),
        transcript=transcript,
    )

    session.send("first")
    import json

    after_one = json.loads(path.read_text())
    assert after_one["model"] == "m"
    assert [m["content"] for m in after_one["messages"] if m["role"] == "user"] == ["first"]

    session.send("second")
    after_two = json.loads(path.read_text())
    assert len([m for m in after_two["messages"] if m["role"] == "user"]) == 2


def test_transcript_records_a_failed_turn(ws, tmp_path):
    """A session that blew up is the one most worth reading back."""
    from dev_setup.agent.ollama import OllamaError
    from dev_setup.agent.transcript import Transcript

    class ExplodingClient:
        host = "x"

        def chat(self, messages, **kwargs):
            raise OllamaError("daemon died")

    path = tmp_path / "t.json"
    session = AgentSession(
        ExplodingClient(), AgentConfig(), ws, model="m", tools={}, policy=AlwaysApprove(),
        transcript=Transcript(path, model="m", host="h", workspace=str(ws.root)),
    )
    with pytest.raises(OllamaError):
        session.send("hello")

    import json

    assert json.loads(path.read_text())["messages"][-1]["content"] == "hello"


def test_transcript_failure_does_not_break_the_session(ws, tmp_path):
    """Losing a debugging aid must never take the session down."""
    from dev_setup.agent.transcript import Transcript

    unwritable = tmp_path / "nope" / "deep"
    transcript = Transcript(unwritable / "t.json", model="m", host="h", workspace=".")
    transcript.path = tmp_path / "a-file" / "t.json"
    (tmp_path / "a-file").write_text("not a directory")

    session = AgentSession(
        ScriptedClient([text("fine")]), AgentConfig(), ws, model="m", tools={},
        policy=AlwaysApprove(), transcript=transcript,
    )
    assert session.send("hi").content == "fine"
