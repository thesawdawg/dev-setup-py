from __future__ import annotations

import pytest
from prompt_toolkit.document import Document

from dev_setup.agent import session as session_mod
from dev_setup.agent.completion import SlashCompleter
from dev_setup.agent.registry import AgentParam, AgentTool

COMMANDS = [("/tools", "List tools"), ("/reset", "Clear history"), ("/exit", "Quit")]


@pytest.fixture()
def completer():
    tools = {
        "write_file": AgentTool(
            "write_file", "Write", "Create or overwrite a file.", "primitive", mutating=True,
            params=[AgentParam(name="path", required=True)],
        ),
        "read_file": AgentTool("read_file", "Read", "Read a file.", "primitive"),
        "fn_validate_yaml": AgentTool(
            "fn_validate_yaml", "Validate YAML", "Validate a YAML file.", "function",
            target="validate-yaml", mutating=True,
        ),
    }
    return SlashCompleter(COMMANDS, tools)


def complete_objects(completer, text):
    return completer.get_completions(Document(text, len(text)), None)


def complete(completer, text):
    return [c.text for c in complete_objects(completer, text)]


def test_slash_offers_commands_and_tools(completer):
    results = complete(completer, "/")
    assert "/tools" in results
    assert "/write_file" in results
    assert "/fn_validate_yaml" in results


def test_commands_are_listed_before_tools(completer):
    """Commands are what a `/` press is usually reaching for."""
    results = complete(completer, "/")
    assert results.index("/exit") < results.index("/read_file")


def test_prefix_filters_candidates(completer):
    assert complete(completer, "/wr") == ["/write_file"]


def test_prefix_can_match_both_kinds(completer):
    results = complete(completer, "/re")
    assert set(results) == {"/reset", "/read_file"}


def test_no_completion_without_a_leading_slash(completer):
    assert complete(completer, "write a file") == []


def test_no_completion_for_a_slash_mid_sentence(completer):
    """A path or a date in ordinary prose must not pop a menu."""
    assert complete(completer, "check /etc/hosts") == []
    assert complete(completer, "on 12/05 do the thing") == []


def test_no_completion_once_an_argument_is_being_typed(completer):
    assert complete(completer, "/tools ") == []


def test_no_completion_on_a_later_line(completer):
    """Multi-line prose whose second line starts with / is not a command."""
    assert complete(completer, "first line\n/second") == []


def test_unknown_prefix_yields_nothing(completer):
    assert complete(completer, "/zzz") == []


def test_completion_replaces_the_typed_text(completer):
    completion = next(
        iter(completer.get_completions(Document("/wr", 3), None))
    )
    assert completion.start_position == -3


def test_mutating_tools_are_flagged_in_the_menu(completer):
    metas = {
        c.text: c.display_meta_text
        for c in completer.get_completions(Document("/", 1), None)
    }
    assert metas["/write_file"].startswith("!")
    assert not metas["/read_file"].startswith("!")


def test_completer_is_built_from_the_live_toolbox():
    """The menu must reflect this session's tools, including user-catalog ones."""
    tools = {"custom_tool": AgentTool("custom_tool", "C", "desc", "primitive")}
    assert complete(SlashCompleter(COMMANDS, tools), "/cus") == ["/custom_tool"]


# -- key bindings ----------------------------------------------------------------


def _bound_keys(kb):
    return {tuple(str(k) for k in b.keys) for b in kb.bindings}


def test_enter_submits_and_alt_enter_inserts_a_newline():
    """Inverted from prompt_toolkit's multiline default, where Enter inserts."""
    keys = _bound_keys(session_mod._key_bindings())
    assert ("Keys.Enter",) in keys or ("Keys.ControlM",) in keys
    assert any("Escape" in k[0] and len(k) == 2 for k in keys)


def test_ctrl_j_also_inserts_a_newline():
    """Many terminals send Ctrl-J for Shift+Enter."""
    keys = _bound_keys(session_mod._key_bindings())
    assert any("ControlJ" in k[0] for k in keys)


def test_enter_has_a_completion_selected_variant():
    """With the menu open, Enter takes the highlighted item instead of sending."""
    kb = session_mod._key_bindings()
    # Exactly the bare Enter bindings — Escape+Enter also contains ControlM.
    bare_enter = [b for b in kb.bindings if len(b.keys) == 1 and "ControlM" in str(b.keys[0])]
    assert len(bare_enter) == 2  # plain submit + completion-selected variant
    # The filtered one must be registered last, or it never wins while the menu is open.
    assert bare_enter[-1].handler.__name__ == "_accept_completion"


# -- driving the real prompt -----------------------------------------------------
#
# These feed keystrokes through a pipe into a real PromptSession, so they test the
# behaviour a user gets rather than merely that a binding was registered.


@pytest.fixture()
def prompt_for(tmp_path, monkeypatch, completer):
    from prompt_toolkit.history import InMemoryHistory
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    def run(keys: str) -> str:
        fake_session = type("S", (), {"tools": completer.tools})()
        with create_pipe_input() as pipe:
            pipe.send_text(keys)
            ps = session_mod.build_prompt_session(
                fake_session,
                history=InMemoryHistory(),
                input=pipe,
                output=DummyOutput(),
            )
            return ps.prompt("> ")

    return run


def test_enter_submits_a_single_line(prompt_for):
    assert prompt_for("hello world\r") == "hello world"


def test_alt_enter_inserts_a_newline_without_submitting(prompt_for):
    # \x1b\r is Escape-then-Return, i.e. Alt+Enter.
    assert prompt_for("first\x1b\rsecond\r") == "first\nsecond"


def test_ctrl_j_inserts_a_newline(prompt_for):
    assert prompt_for("first\x0asecond\r") == "first\nsecond"


def test_several_newlines_survive(prompt_for):
    assert prompt_for("a\x1b\rb\x1b\rc\r") == "a\nb\nc"


def test_empty_enter_submits_empty(prompt_for):
    assert prompt_for("\r") == ""


# Tab completion is deliberately NOT tested through the pipe above: prompt_toolkit
# resolves completions in a background task, and piped input is consumed faster than
# that task can run, so such a test would assert a harness artifact rather than the
# behaviour. The two things that can actually break are covered instead --
# the binding existing (above) and the replacement arithmetic (below) -- with the
# full keystroke path covered by tests/integration/test_agent_tui.py against a PTY.


def test_applying_a_completion_replaces_the_typed_prefix(completer):
    """start_position arithmetic: a wrong value silently yields "//write_file"."""
    from prompt_toolkit.buffer import Buffer

    buffer = Buffer(completer=completer)
    buffer.insert_text("/wr")
    buffer.apply_completion(next(iter(complete_objects(completer, "/wr"))))
    assert buffer.text == "/write_file"
