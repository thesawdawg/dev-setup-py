"""`devstuff install` / `remove` / `update`: plan, confirm, `--yes`, requires, failure, summary.

These commands were rewritten around a plan preview and a per-run summary. What they must do is
mostly decisions — what is asked, what is skipped, what is attempted after something fails, what the
exit status says — so the tools here are stateful fakes that record every verb, and the real command
layer runs over them. Nothing is installed.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from dev_setup import registry, ui
from dev_setup.cli import cli
from dev_setup.commands import configure_cmd, install_cmd, remove_cmd, update_cmd

EVENTS: list[tuple] = []
PROMPTS: list[tuple[str, bool]] = []
OFFERS: list[str] = []


class Fake:
    """A tool that records which verb it was asked to do."""

    def __init__(
        self, key, install_type="npm", *, installed=False, version="", requires=(),
        fail=None, after=None,
    ):
        self.key, self.name, self.install_type = key, key.title(), install_type
        self.description, self.category = f"the {key} tool", "tools"
        self.installed, self.version, self.requires = installed, version, tuple(requires)
        self.fail, self.after = fail, after

    def is_installed(self):
        return self.installed

    def get_version(self):
        return self.version

    def plan_items(self, action, version=None):
        suffix = f" @{version}" if version else ""
        return [("cmd", action, f"PLAN[{action} {self.key}{suffix}]")]

    def _act(self, verb, **kw):
        EVENTS.append((verb, self.key, *kw.values()))
        if self.fail:
            raise RuntimeError(self.fail)

    def install(self):
        self._act("install")
        self.installed, self.version = True, self.version or "1.0"
        return self.version

    def remove(self):
        self._act("remove")
        self.installed = False

    def update(self, version=None):
        self._act("update", version=version)
        return self.after


@pytest.fixture(autouse=True)
def _clean():
    EVENTS.clear()
    PROMPTS.clear()
    OFFERS.clear()


@pytest.fixture
def world(monkeypatch):
    """Make these fakes the catalog. Returns a function to call with the tools."""

    def install(*tools, answers=()):
        by_key = {t.key: t for t in tools}
        monkeypatch.setattr(registry, "get", by_key.get)
        monkeypatch.setattr(registry, "exists", lambda k: k in by_key)
        monkeypatch.setattr(registry, "all_tools", lambda: list(tools))
        monkeypatch.setattr(
            registry, "missing_requires",
            lambda t: [k for k in t.requires if not (k in by_key and by_key[k].installed)],
        )
        monkeypatch.setattr(
            registry, "dependents", lambda k: [t.key for t in tools if k in t.requires]
        )
        scripted = list(answers)

        def confirm(prompt, default=False):
            PROMPTS.append((prompt, default))
            return scripted.pop(0) if scripted else default

        monkeypatch.setattr(ui, "confirm", confirm)
        monkeypatch.setattr(configure_cmd, "offer_after_install", OFFERS.append)
        return tools

    return install


def run(*args):
    return CliRunner().invoke(cli, list(args))


def flat(out: str) -> str:
    return " ".join("".join(ch for ch in out if ch not in "│╭╮╰╯─├┤┬┴┼╼╾").split())


def verbs() -> list[tuple]:
    return [e[:2] for e in EVENTS]


# == install ===========================================================================================


def test_install_an_unknown_package_is_exit_1_and_installs_nothing(world):
    world(Fake("a"))

    result = run("install", "zzz")

    assert result.exit_code == 1 and EVENTS == []
    assert "Unknown package: 'zzz'" in flat(result.output)


def test_install_skips_what_is_already_installed_without_a_plan_or_a_question(world):
    world(Fake("a", installed=True, version="2.0"))

    result = run("install", "a")

    assert result.exit_code == 0 and EVENTS == [] and PROMPTS == []
    assert "already installed: 2.0" in flat(result.output)
    assert "PLAN[" not in result.output


def test_install_shows_the_plan_then_asks_before_doing_anything(world):
    world(Fake("a"), answers=[True])

    result = run("install", "a")

    assert result.exit_code == 0 and verbs() == [("install", "a")]
    assert "PLAN[install a]" in flat(result.output)
    assert PROMPTS == [("Install A?", True)]  # installing defaults to yes


def test_install_yes_skips_the_plan_and_the_question(world):
    world(Fake("a"))

    result = run("install", "a", "--yes")

    assert result.exit_code == 0 and verbs() == [("install", "a")]
    assert PROMPTS == [] and "PLAN[" not in result.output


def test_declining_the_plan_installs_nothing_and_says_aborted(world):
    world(Fake("a"), answers=[False])

    result = run("install", "a")

    assert result.exit_code == 0 and EVENTS == []
    assert "Aborted" in flat(result.output)


def test_a_failed_install_is_exit_1_and_shows_why(world):
    world(Fake("a", fail="npm ERR! no network"))

    result = run("install", "a", "-y")

    assert result.exit_code == 1
    assert "Failed to install A" in flat(result.output) and "no network" in flat(result.output)


def test_several_packages_get_one_batch_plan_one_question_and_a_summary(world):
    world(Fake("a"), Fake("b"), answers=[True])

    result = run("install", "a", "b")

    text = flat(result.output)
    assert verbs() == [("install", "a"), ("install", "b")]
    assert PROMPTS == [("Install 2 packages?", True)]
    assert "Install plan" in text and "PLAN[install a]" in text and "PLAN[install b]" in text
    assert "Install 2 ok · 0 failed" in text


def test_a_single_package_gets_no_summary_table(world):
    # The summary is for comparing several results; for one, the line above it already said it.
    world(Fake("a"))

    result = run("install", "a", "-y")

    assert result.exit_code == 0 and "ok · 0 failed" not in flat(result.output)


def test_the_plan_covers_only_what_will_be_installed(world):
    world(Fake("a", installed=True), Fake("b"), Fake("c"), answers=[True])

    result = run("install", "a", "b", "c")

    text = flat(result.output)
    assert "PLAN[install b]" in text and "PLAN[install c]" in text
    assert "PLAN[install a]" not in text
    assert PROMPTS == [("Install 2 packages?", True)]


def test_one_failure_does_not_stop_the_rest_and_the_exit_is_1(world):
    world(Fake("a", fail="boom"), Fake("b"))

    result = run("install", "a", "b", "-y")

    assert verbs() == [("install", "a"), ("install", "b")]
    assert result.exit_code == 1
    assert "Install 1 ok · 1 failed" in flat(result.output)


def test_a_missing_requirement_is_installed_first_under_yes(world):
    world(Fake("dep"), Fake("pkg", requires=["dep"]))

    result = run("install", "pkg", "-y")

    assert result.exit_code == 0
    assert verbs() == [("install", "dep"), ("install", "pkg")]
    assert PROMPTS == []


def test_without_yes_a_missing_requirement_is_offered_not_assumed(world):
    world(Fake("dep"), Fake("pkg", requires=["dep"]), answers=[True, True, True])

    run("install", "pkg")

    asked = [p for p, _ in PROMPTS]
    assert "Pkg requires dep — install them first?" in asked
    assert verbs() == [("install", "dep"), ("install", "pkg")]


def test_declining_the_requirements_means_the_package_is_not_installed(world):
    world(Fake("dep"), Fake("pkg", requires=["dep"]), answers=[True, False])

    result = run("install", "pkg")

    assert EVENTS == [] and result.exit_code == 1
    assert "missing: dep" in flat(result.output)


def test_an_unknown_requirement_fails_the_package_without_trying(world):
    world(Fake("pkg", requires=["ghost"]))
    # `ghost` is not in the catalog at all, so it cannot be offered.

    result = run("install", "pkg", "-y")

    assert EVENTS == [] and result.exit_code == 1
    assert "unknown required tools: ghost" in flat(result.output)


def test_a_requirement_that_fails_stops_its_dependent_from_being_attempted(world):
    world(Fake("dep", fail="dep exploded"), Fake("pkg", requires=["dep"]))

    result = run("install", "pkg", "-y")

    assert verbs() == [("install", "dep")]  # `pkg` never reached its installer
    assert result.exit_code == 1


def test_the_setup_wizard_is_offered_after_a_successful_install_only(world):
    world(Fake("good"), Fake("bad", fail="nope"))

    run("install", "good", "bad", "-y")

    assert OFFERS == ["good"]


def test_install_by_key_reports_whether_the_tool_ends_up_installed(world):
    world(Fake("a"), Fake("b", fail="nope"), Fake("c", installed=True), answers=[False])

    assert install_cmd.install_by_key("a", yes=True) is True
    assert install_cmd.install_by_key("b", yes=True) is False
    assert install_cmd.install_by_key("c", yes=True) is True  # already there is success
    assert install_cmd.install_by_key("zzz", yes=True) is False  # unknown: False, not a crash


def test_install_by_key_does_not_install_when_the_plan_is_declined(world):
    world(Fake("a"), answers=[False])

    assert install_cmd.install_by_key("a") is False
    assert EVENTS == []


def test_the_install_picker_with_nothing_selected_installs_nothing(world, monkeypatch):
    world(Fake("a"))
    monkeypatch.setattr(ui, "checkbox", lambda *a, **k: [])

    result = run("install")

    assert result.exit_code == 0 and EVENTS == []
    assert "No packages selected" in flat(result.output)


def test_the_install_picker_installs_what_was_selected_after_one_confirmation(world, monkeypatch):
    world(Fake("a"), Fake("b"), answers=[True])
    monkeypatch.setattr(ui, "checkbox", lambda *a, **k: ["b"])

    result = run("install")

    assert result.exit_code == 0 and verbs() == [("install", "b")]
    assert len(PROMPTS) == 1


def test_the_install_picker_marks_installed_and_blocked_rows_as_disabled(world, monkeypatch):
    world(
        Fake("have", installed=True), Fake("want"), Fake("blocked", requires=["dep"]),
        Fake("dep"),
    )
    seen = {}

    def checkbox(prompt, choices, **kw):
        seen.update({c.value: c.disabled for c in choices if hasattr(c, "value")})
        return []

    monkeypatch.setattr(ui, "checkbox", checkbox)

    run("install")

    assert seen["have"] == "installed"
    assert seen["want"] is None
    assert seen["blocked"] == "requires dep"


# == remove ============================================================================================


def test_remove_an_unknown_package_is_exit_1(world):
    world(Fake("a", installed=True))

    assert run("remove", "zzz").exit_code == 1


def test_remove_says_so_and_asks_nothing_when_it_is_not_installed(world):
    world(Fake("a"))

    result = run("remove", "a")

    assert result.exit_code == 0 and EVENTS == [] and PROMPTS == []
    assert "not installed" in flat(result.output)


def test_remove_defaults_to_no_and_shows_the_plan_first(world):
    world(Fake("a", installed=True), answers=[True])

    result = run("remove", "a")

    assert verbs() == [("remove", "a")]
    assert "PLAN[remove a]" in flat(result.output)
    assert PROMPTS == [("Remove A?", False)]  # the destructive verb defaults to No


def test_declining_a_removal_removes_nothing(world):
    world(Fake("a", installed=True), answers=[False])

    result = run("remove", "a")

    assert result.exit_code == 0 and EVENTS == []
    assert "Aborted" in flat(result.output)


def test_remove_yes_skips_the_plan_and_the_question(world):
    world(Fake("a", installed=True))

    result = run("remove", "a", "-y")

    assert verbs() == [("remove", "a")] and PROMPTS == []
    assert "PLAN[" not in result.output


def test_remove_warns_which_tools_depend_on_it_even_under_yes(world):
    world(Fake("dep", installed=True), Fake("pkg", installed=True, requires=["dep"]))

    result = run("remove", "dep", "-y")

    assert verbs() == [("remove", "dep")]  # a warning, not a veto
    assert "required by 1 catalog tool(s): pkg" in flat(result.output)


def test_a_failed_removal_is_exit_1(world):
    world(Fake("a", installed=True, fail="permission denied"))

    result = run("remove", "a", "-y")

    assert result.exit_code == 1
    assert "Failed to remove A" in flat(result.output)


def test_removing_several_asks_once_with_default_no_and_ends_in_a_summary(world):
    world(Fake("a", installed=True), Fake("b", installed=True), answers=[True])

    result = run("remove", "a", "b")

    assert verbs() == [("remove", "a"), ("remove", "b")]
    assert PROMPTS == [("Remove 2 packages?", False)]
    assert "Remove 2 ok · 0 failed" in flat(result.output)


def test_the_remove_picker_lists_only_installed_tools_and_annotates_dependents(world, monkeypatch):
    world(
        Fake("dep", installed=True), Fake("pkg", installed=True, requires=["dep"]),
        Fake("absent"),
    )
    seen = {}

    def checkbox(prompt, choices, **kw):
        seen.update({c.value: c.title for c in choices})
        return []

    monkeypatch.setattr(ui, "checkbox", checkbox)

    run("remove")

    assert set(seen) == {"dep", "pkg"}
    assert "required by: pkg" in seen["dep"] and "required by" not in seen["pkg"]


def test_the_remove_picker_with_nothing_installed_says_so(world):
    world(Fake("a"))

    result = run("remove")

    assert result.exit_code == 0 and "No installed packages to remove" in flat(result.output)


def test_the_remove_picker_removes_the_selection_after_one_confirmation(world, monkeypatch):
    world(Fake("a", installed=True), Fake("b", installed=True), answers=[True])
    monkeypatch.setattr(ui, "checkbox", lambda *a, **k: ["a"])

    run("remove")

    assert verbs() == [("remove", "a")] and len(PROMPTS) == 1


# == update ============================================================================================


def test_update_an_unknown_package_is_exit_1(world):
    world(Fake("a", installed=True))

    assert run("update", "zzz").exit_code == 1


def test_update_of_something_not_installed_points_at_install(world):
    world(Fake("a"))

    result = run("update", "a")

    assert result.exit_code == 0 and EVENTS == [] and PROMPTS == []
    assert "devstuff install a" in flat(result.output)


def test_version_needs_a_package(world):
    world(Fake("a", installed=True))

    result = run("update", "--version", "1.0")

    assert result.exit_code == 1 and "requires a package key" in flat(result.output)


def test_version_cannot_be_combined_with_several_packages(world):
    world(Fake("a", installed=True), Fake("b", installed=True))

    result = run("update", "a", "b", "--version", "1.0")

    assert result.exit_code == 1 and EVENTS == []


def test_update_to_latest_passes_no_version(world):
    world(Fake("a", installed=True, version="1.0", after="2.0"))

    result = run("update", "a", "-y")

    assert result.exit_code == 0 and EVENTS == [("update", "a", None)]
    assert PROMPTS == []


def test_update_passes_the_requested_version_through(world):
    world(Fake("a", installed=True, version="1.0", after="1.5"), answers=[True])

    run("update", "a", "--version", "1.5")

    assert EVENTS == [("update", "a", "1.5")]
    assert PROMPTS == [("Update A?", True)]


def test_the_update_plan_is_shown_with_the_version(world):
    world(Fake("a", installed=True, version="1.0", after="1.5"), answers=[False])

    result = run("update", "a", "--version", "1.5")

    assert "PLAN[update a @1.5]" in flat(result.output) and EVENTS == []


@pytest.mark.parametrize("install_type, warned", [
    ("bash", True), ("script", True), ("npm", False), ("uvx", False), ("apt", False),
])
def test_only_installer_script_types_warn_that_update_reruns_the_installer(
    world, install_type, warned
):
    world(Fake("a", install_type, installed=True, version="1.0"), answers=[False])

    result = run("update", "a")

    assert ("re-runs this tool's full installer" in flat(result.output)) is warned


@pytest.mark.parametrize("before, after, expected", [
    ("1.0", "1.0", "already up to date: 1.0"),
    ("1.0", "2.0", "updated: 1.0 → 2.0"),
    ("", "2.0", "updated: 2.0"),
    ("1.0", None, "A updated"),  # nothing reported back: say it plainly rather than invent a version
])
def test_update_reports_what_changed(world, before, after, expected):
    tool = Fake("a", installed=True, version=before, after=after)
    if after is None:
        tool.version = ""  # and nothing readable afterwards either
    world(tool)

    result = run("update", "a", "-y")

    assert result.exit_code == 0 and expected in flat(result.output)


def test_a_failed_update_is_exit_1(world):
    world(Fake("a", installed=True, fail="registry unreachable"))

    result = run("update", "a", "-y")

    assert result.exit_code == 1 and "Failed to update A" in flat(result.output)


def test_updating_several_asks_once_and_ends_in_a_summary(world):
    world(
        Fake("a", installed=True, version="1", after="2"),
        Fake("b", installed=True, version="1", after="2"),
        answers=[True],
    )

    result = run("update", "a", "b")

    assert PROMPTS == [("Update 2 packages?", True)]
    assert "Update 2 ok · 0 failed" in flat(result.output)


def test_the_update_picker_prechecks_only_known_updates(world, monkeypatch):
    a = Fake("a", installed=True, version="1")
    b = Fake("b", installed=True, version="1")
    c = Fake("c", installed=True, version="1")
    world(a, b, c)
    status = SimpleNamespace
    monkeypatch.setattr(update_cmd, "collect_candidates", lambda: [
        (a, status(available=True, current="1", latest="2")),
        (b, status(available=False, current="1", latest="1")),
        (c, status(available=None, current="1", latest=None)),
    ])
    seen = {}

    def checkbox(prompt, choices, **kw):
        seen.update({ch.value: (ch.checked, ch.title) for ch in choices})
        return []

    monkeypatch.setattr(ui, "checkbox", checkbox)

    run("update")

    assert [seen[k][0] for k in "abc"] == [True, False, False]
    assert "update available: 1 → 2" in seen["a"][1]
    assert "up to date" in seen["b"][1] and "unknown" in seen["c"][1]


def test_the_update_picker_with_no_installed_tools_says_so(world, monkeypatch):
    world(Fake("a"))
    monkeypatch.setattr(update_cmd, "collect_candidates", lambda: [])

    result = run("update")

    assert result.exit_code == 0 and "No installed packages to update" in flat(result.output)


def test_the_update_picker_updates_the_selection_after_one_confirmation(world, monkeypatch):
    a = Fake("a", installed=True, version="1", after="2")
    world(a, answers=[True])
    monkeypatch.setattr(
        update_cmd, "collect_candidates",
        lambda: [(a, SimpleNamespace(available=True, current="1", latest="2"))],
    )
    monkeypatch.setattr(ui, "checkbox", lambda *a_, **k: ["a"])

    run("update")

    assert EVENTS == [("update", "a", None)] and len(PROMPTS) == 1


# == text that looks like Rich markup =================================================================
#
# Installer errors are arbitrary text. `[/usr/bin/x]` is a closing tag to Rich, which raises
# MarkupError; `[lock]` is an unknown style, which Rich silently deletes. A crash while *reporting* a
# failure, in a multi-package run, would also abandon every package after it.

NASTY = "cannot open [/usr/bin/x] while reading ansible-lint[lock]"


@pytest.mark.parametrize("command, fake", [
    ("install", lambda: Fake("a", fail=NASTY)),
    ("remove", lambda: Fake("a", installed=True, fail=NASTY)),
    ("update", lambda: Fake("a", installed=True, version="1", fail=NASTY)),
])
def test_a_failure_message_containing_brackets_is_reported_not_a_crash(world, command, fake):
    world(fake())

    result = run(command, "a", "-y")

    # A crash also exits 1, so say what it was: a deliberate `sys.exit(1)`, not a traceback.
    assert result.exit_code == 1 and isinstance(result.exception, SystemExit), result.exception
    assert "[/usr/bin/x]" in flat(result.output) and "ansible-lint[lock]" in flat(result.output)


def test_a_failure_with_brackets_does_not_abandon_the_packages_after_it(world):
    world(Fake("a", fail=NASTY), Fake("b"))

    result = run("install", "a", "b", "-y")

    assert verbs() == [("install", "a"), ("install", "b")] and result.exit_code == 1
    assert "[/usr/bin/x]" in flat(result.output)  # in the summary table too


def test_the_summary_shows_bracketed_text_instead_of_crashing_or_deleting_it():
    with ui.console.capture() as cap:
        ui.summary([("a", "fail", NASTY), ("ansible-lint[lock]", "ok", "1.0")])

    text = flat(cap.get())
    assert "[/usr/bin/x]" in text and "ansible-lint[lock]" in text


def test_the_batch_plan_shows_a_command_exactly_as_it_will_run():
    with ui.console.capture() as cap:
        ui.batch_plan_panel([("ansible-lint", "uv tool install ansible-lint[lock]")], "Install")

    assert "uv tool install ansible-lint[lock]" in flat(cap.get())


# == the three agree ====================================================================================


@pytest.mark.parametrize("module", [install_cmd, remove_cmd, update_cmd])
def test_every_lifecycle_command_takes_yes(module):
    names = {name for p in module.__dict__.values() if hasattr(p, "params") for name in
             [o for param in p.params for o in param.opts]}

    assert "-y" in names and "--yes" in names
