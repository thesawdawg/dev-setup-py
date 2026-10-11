"""The plan-preview helpers (`commands/_plan.py`) and the choice-list padding in `ui.select`.

`select` was changed twice at once: full-width padding so the highlighted row reads as a bar, and a
`searchable` mode added for `devstuff links`. Both change what questionary is handed, so these tests
fix what that is.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import questionary

from dev_setup import generic, ui
from dev_setup.commands._plan import plan_for, plan_summary

# -- _plan.py ---------------------------------------------------------------------------------------


class Planner:
    def __init__(self, items):
        self.items, self.asked = items, []

    def plan_items(self, action, version=None):
        self.asked.append((action, version))
        return self.items


def test_plan_for_asks_the_tool_for_its_own_plan():
    tool = Planner([("cmd", "x", "npm install -g x")])

    assert plan_for(tool, "update", "1.2") == [("cmd", "x", "npm install -g x")]
    assert tool.asked == [("update", "1.2")]


def test_a_tool_without_a_planner_gets_a_generic_note_not_an_error():
    tool = SimpleNamespace(key="thing")

    assert plan_for(tool, "install") == [("note", "", "install thing")]


def test_a_summary_joins_commands_and_says_a_script_is_a_script():
    tool = Planner([
        ("cmd", "update", "sudo apt-get update -q"),
        ("cmd", "install", "sudo apt-get install -y jq"),
        ("script", "install script", "set -e\ncurl ... | sh"),
        ("note", "", "then restart your shell"),
    ])

    assert plan_summary(tool, "install") == (
        "sudo apt-get update -q;  sudo apt-get install -y jq;  runs a script;  then restart your shell"
    )


def test_a_summary_never_inlines_a_script_body():
    tool = Planner([("script", "install script", "rm -rf /tmp/x\necho hi")])

    assert plan_summary(tool, "install") == "runs a script"


# -- ui._pad_choices / select / checkbox ---------------------------------------------------------


@pytest.fixture
def narrow(monkeypatch):
    monkeypatch.setattr(ui.console, "width", 30)  # rows are padded to width - 6 == 24
    return 24


def test_a_plain_string_becomes_a_padded_choice_whose_value_is_the_original(narrow):
    (choice,) = ui._pad_choices(["abc"])

    assert isinstance(choice, questionary.Choice)
    assert choice.title == "abc".ljust(narrow) and choice.value == "abc"


def test_a_choice_title_is_padded_but_its_value_is_not(narrow):
    original = questionary.Choice(title="row", value="the-key")

    (choice,) = ui._pad_choices([original])

    assert choice.title == "row".ljust(narrow) and choice.value == "the-key"


def test_a_title_already_as_wide_as_the_row_is_left_alone(narrow):
    wide = "x" * narrow

    (choice,) = ui._pad_choices([questionary.Choice(title=wide, value="k")])

    assert choice.title == wide


def test_styled_token_titles_and_separators_pass_through_unchanged(narrow):
    tokens = [("class:text", "styled row")]
    styled = questionary.Choice(title=tokens, value="k", disabled="installed")
    separator = questionary.Separator("--- group ---")

    out = ui._pad_choices([styled, separator])

    assert out[0].title == tokens and out[1] is separator


class FakePrompt:
    def __init__(self, answer):
        self.answer = answer

    def unsafe_ask(self):
        return self.answer


@pytest.fixture
def captured(monkeypatch):
    seen: dict = {}

    def fake(kind, answer):
        def build(prompt, **kwargs):
            seen.update(kind=kind, prompt=prompt, **kwargs)
            return FakePrompt(answer)

        return build

    monkeypatch.setattr(questionary, "select", fake("select", "picked"))
    monkeypatch.setattr(questionary, "checkbox", fake("checkbox", ["a"]))
    monkeypatch.setattr(ui.console, "width", 30)
    return seen


def test_select_hands_questionary_padded_choices_and_the_default(captured):
    answer = ui.select("Pick", ["one", "two"], default="two")

    assert answer == "picked" and captured["default"] == "two"
    assert [c.value for c in captured["choices"]] == ["one", "two"]
    assert all(len(c.title) == 24 for c in captured["choices"])


def test_a_plain_select_keeps_the_j_and_k_keys_and_has_no_search(captured):
    ui.select("Pick", ["one"])

    assert captured["use_search_filter"] is False and captured["use_jk_keys"] is True


def test_a_searchable_select_turns_search_on_and_j_k_off_and_is_still_padded(captured):
    # j/k would otherwise be swallowed as search text. Both features must hold together: this is
    # the combination `devstuff links` uses.
    ui.select("Open which link?", ["one"], searchable=True)

    assert captured["use_search_filter"] is True and captured["use_jk_keys"] is False
    assert captured["choices"][0].value == "one" and len(captured["choices"][0].title) == 24


def test_select_returns_an_empty_string_when_nothing_is_chosen(monkeypatch):
    monkeypatch.setattr(questionary, "select", lambda *a, **k: FakePrompt(None))

    assert ui.select("Pick", ["one"]) == ""


def test_checkbox_pads_its_choices_and_forwards_its_options(captured):
    answer = ui.checkbox("Select", ["a", "b"], instruction="(space)")

    assert answer == ["a"] and captured["instruction"] == "(space)"
    assert [c.value for c in captured["choices"]] == ["a", "b"]
    assert all(len(c.title) == 24 for c in captured["choices"])


@pytest.mark.parametrize("default", ["npm", "apt", "bash"])
def test_a_default_still_lands_on_its_row_after_padding(default):
    # The wizard passes `default=` as the *value*; padding changes only the title. Checked against
    # questionary's real selection logic, since a fake would only repeat what the code assumes.
    from questionary.prompts.common import InquirerControl

    names = ["npm", "uvx", "apt", "git", "script", "bash"]
    prompt = questionary.select(
        "t", choices=ui._pad_choices(names), default=default, style=ui._STYLE
    )
    control = next(c for c in prompt.application.layout.find_all_controls()
                   if isinstance(c, InquirerControl))

    assert control.choices[control.pointed_at].value == default


def test_every_install_type_has_a_planner():
    # Without one, the plan preview is a generic note and the progress bar shows no phases. CLAUDE.md
    # tells whoever adds a type to add a planner; this is what makes that more than a sentence.
    assert set(generic._PLANNERS) == set(generic._INSTALLERS)
