from __future__ import annotations

import pytest

from dev_setup import generic, registry, ui
from dev_setup.generic import GenericTool


def make_tool(**kwargs) -> GenericTool:
    kwargs.setdefault("key", "demo")
    kwargs.setdefault("name", "Demo")
    return GenericTool(**kwargs)


# -- plan_items ------------------------------------------------------------------


def test_plan_apt_install_shows_update_and_install():
    tool = make_tool(install_type="apt", apt_packages="ripgrep fd-find")
    items = tool.plan_items("install")
    details = [d for _k, _l, d in items]
    assert details == [
        "sudo apt-get update -q",
        "sudo apt-get install -y ripgrep fd-find",
    ]


def test_plan_uvx_install_includes_flags():
    tool = make_tool(
        install_type="uvx",
        pip_name="ansible",
        uv_executables_from=["ansible-core"],
        uv_python="3.12",
    )
    [(kind, _l, detail)] = tool.plan_items("install")
    assert kind == "cmd"
    assert "uv tool install" in detail
    assert "--with-executables-from ansible-core" in detail
    assert "--python 3.12" in detail
    assert detail.endswith("ansible")


def test_plan_uvx_pinned_update_shows_forced_install():
    tool = make_tool(install_type="uvx", pip_name="commitizen")
    [(_k, _l, detail)] = tool.plan_items("update", "4.16.0")
    assert "--force" in detail
    assert "commitizen==4.16.0" in detail


def test_plan_uvx_remove_prefers_remove_script():
    tool = make_tool(install_type="uvx", pip_name="ansible", remove_script="echo bye")
    [(kind, _l, detail)] = tool.plan_items("remove")
    assert kind == "script" and detail == "echo bye"


def test_plan_script_install_shows_url_not_a_body():
    tool = make_tool(install_type="script", script_url="https://x/i.sh", sha256="ab" * 32)
    items = tool.plan_items("install")
    assert any("https://x/i.sh" in d for _k, _l, d in items)


def test_plan_bash_install_shows_script_body():
    tool = make_tool(install_type="bash", install_script="echo hi")
    [(kind, _l, detail)] = tool.plan_items("install")
    assert kind == "script" and detail == "echo hi"


def test_plan_remove_without_remove_script_is_a_note():
    tool = make_tool(install_type="bash", install_script="echo hi")
    [(kind, _l, detail)] = tool.plan_items("remove")
    assert kind == "note"
    assert "devstuff delete" in detail


def test_plan_git_install_includes_clone_and_install_cmd():
    tool = make_tool(
        install_type="git",
        git_url="https://github.com/x/y.git",
        git_install_cmd="make install",
    )
    details = [d for _k, _l, d in tool.plan_items("install")]
    assert details[0].startswith("git clone --depth=1 https://github.com/x/y.git")
    assert details[1] == "make install"


def test_plan_unknown_type_is_a_note():
    tool = make_tool(install_type="nope")
    [(kind, _l, _d)] = tool.plan_items("install")
    assert kind == "note"


# -- TaskView ---------------------------------------------------------------------


def test_task_view_tracks_phases_and_tail():
    view = ui.TaskView("Installing", total=2)
    with view.phase("step one"):
        view.feed("output line")
    assert view._phases == [["step one", "done"]]
    assert "output line" in view.error_output()


def test_task_view_marks_failed_phase():
    view = ui.TaskView("Installing", total=2)
    with pytest.raises(RuntimeError), view.phase("step one"):
        raise RuntimeError("boom")
    assert view._phases[0][1] == "failed"


def test_task_view_error_tail_skips_blank_lines():
    view = ui.TaskView("x", total=1)
    view.feed("")
    view.feed("real")
    assert view.error_output() == "real"


def test_task_view_render_does_not_crash():
    view = ui.TaskView("x", total=2)
    with view.phase("one"):
        view.feed("line")
    view._render(final=True)
    view._render(final=True, failed=True)


def test_task_view_degrades_off_tty():
    # ui.console is non-interactive under pytest → the NullView path.
    with ui.task_view("x", total=1) as view:
        assert ui.current_view() is None
        with view.phase("p"):
            pass
    assert ui.current_view() is None


# -- _run tail feeding ---------------------------------------------------------------


class _FakeView:
    def __init__(self):
        self.lines: list[str] = []

    def feed(self, line: str) -> None:
        self.lines.append(line)

    def error_output(self) -> str:
        return "\n".join(self.lines)


def test_run_feeds_active_view(monkeypatch):
    view = _FakeView()
    monkeypatch.setattr(ui, "current_view", lambda: view)
    generic._run(["bash", "-c", "echo out; echo err >&2"])
    assert view.lines == ["out", "err"]


def test_run_failure_raises_with_tail(monkeypatch):
    view = _FakeView()
    monkeypatch.setattr(ui, "current_view", lambda: view)
    with pytest.raises(RuntimeError, match="oops"):
        generic._run(["bash", "-c", "echo oops >&2; exit 3"])


def test_run_without_view_uses_capture(monkeypatch):
    monkeypatch.setattr(ui, "current_view", lambda: None)
    with pytest.raises(RuntimeError, match="oops"):
        generic._run(["bash", "-c", "echo oops >&2; exit 3"])


# -- registry.dependents ---------------------------------------------------------------


def test_dependents_finds_direct_requirers(monkeypatch):
    base = make_tool(key="base")
    dep = make_tool(key="dep", requires=["base"])
    other = make_tool(key="other", requires=[])
    registry.register(base)
    registry.register(dep)
    registry.register(other)
    try:
        assert registry.dependents("base") == ["dep"]
        assert registry.dependents("dep") == []
    finally:
        registry.deregister("base")
        registry.deregister("dep")
        registry.deregister("other")


# -- Wizard -------------------------------------------------------------------------


def _text_step(key, value):
    return ui.Step(key, key, lambda a: value)


def test_wizard_runs_steps_and_returns_answers(monkeypatch):
    # Bypass the review screen: confirm immediately.
    monkeypatch.setattr(ui, "select", lambda *a, **k: "__done__")
    steps = [_text_step("a", "1"), _text_step("b", "2")]
    answers = ui.Wizard("t").run(steps)
    assert answers == {"a": "1", "b": "2"}


def test_wizard_back_rewinds_to_previous_step(monkeypatch):
    seen = []

    def ask_a(a):
        seen.append("a")
        return "x"

    def ask_b(a):
        seen.append("b")
        return ui.BACK if len(seen) == 2 else "y"

    monkeypatch.setattr(ui, "select", lambda *a, **k: "__done__")
    answers = ui.Wizard("t").run([ui.Step("a", "a", ask_a), ui.Step("b", "b", ask_b)])
    assert answers == {"a": "x", "b": "y"}
    assert seen == ["a", "b", "a", "b"]


def test_wizard_back_on_first_step_aborts():
    wiz = ui.Wizard("t")
    assert wiz.run([ui.Step("a", "a", lambda a: ui.BACK)]) is None


def test_wizard_when_filters_steps(monkeypatch):
    monkeypatch.setattr(ui, "select", lambda *a, **k: "__done__")
    steps = [
        _text_step("type", "npm"),
        ui.Step("npm_name", "npm", lambda a: "pkg", when=lambda a: a.get("type") == "npm"),
        ui.Step("apt_pkgs", "apt", lambda a: "pkg", when=lambda a: a.get("type") == "apt"),
    ]
    answers = ui.Wizard("t").run(steps)
    assert answers == {"type": "npm", "npm_name": "pkg"}
    assert "apt_pkgs" not in answers


def test_wizard_review_fills_missing_applicable_steps(monkeypatch):
    """Editing 'type' in review to 'apt' must collect the now-required apt step."""
    asks = []

    def type_ask(a):
        asks.append("type")
        return "apt" if len(asks) > 1 else "npm"

    def apt_ask(a):
        asks.append("apt")
        return "ripgrep"

    steps = [
        ui.Step("type", "type", type_ask),
        ui.Step("npm_name", "npm", lambda a: "npmpkg", when=lambda a: a.get("type") == "npm"),
        ui.Step("apt_pkgs", "apt", apt_ask, when=lambda a: a.get("type") == "apt"),
    ]
    # In review: edit type → returns "apt" → then confirm.
    picks = iter(["type", "__done__", "__done__"])
    monkeypatch.setattr(ui, "select", lambda *a, **k: next(picks))
    answers = ui.Wizard("t").run(steps)
    assert answers["type"] == "apt"
    assert answers["apt_pkgs"] == "ripgrep"
    assert asks == ["type", "type", "apt"]
