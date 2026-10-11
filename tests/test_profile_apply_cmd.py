"""`devstuff profile apply` (docs/specs/profile-apply, FR-1–19, NFR-1–5; development-plan A4).

The tools here are stateful fakes: an install changes what `is_installed()` and
`installed_version()` answer, because the command's verdict comes from *re-measuring the
machine*, not from whether an installer returned. One fake installs nothing and says it did.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from dev_setup import bundles, profile, registry, ui, verbose
from dev_setup.cli import cli
from dev_setup.commands import profile_cmd
from dev_setup.profile import Entry, Profile

EVENTS: list[tuple] = []


class Fake:
    def __init__(
        self, key, install_type="npm", *, installed=False, version="", requires=(),
        install_script="", fail=None, lies=False, interrupt=False, unreadable_after=False,
        apt_packages="", chatty=False,
    ):
        self.key, self.name, self.install_type = key, key.title(), install_type
        self.installed, self.version, self.requires = installed, version, tuple(requires)
        self.install_script, self.apt_packages, self.builtin = install_script, apt_packages, True
        self.fail, self.lies, self.interrupt = fail, lies, interrupt
        self.unreadable_after, self.chatty = unreadable_after, chatty
        self.unreadable = False

    def is_installed(self):
        return self.installed

    def installed_version(self):
        return "" if self.unreadable else self.version

    def get_version(self):
        return self.version

    def _act(self, verb, version):
        EVENTS.append((verb, self.key, version))
        EVENTS_LEVEL.append(verbose.level())
        if self.chatty:
            print(f"installer output for {self.key}")  # what a streaming installer would do
            ui.info(f"noise from {self.key}")
        if self.interrupt:
            raise KeyboardInterrupt
        if self.fail:
            raise RuntimeError(self.fail)
        if self.lies:
            return None  # "succeeded", changed nothing
        self.installed = True
        self.version = version or "9.9"
        self.unreadable = self.unreadable_after
        return self.version

    def install(self):
        return self._act("install", None)

    def update(self, version=None):
        return self._act("update", version)


EVENTS_LEVEL: list[int] = []
PROMPTS: list[str] = []


@pytest.fixture(autouse=True)
def _clean():
    EVENTS.clear()
    EVENTS_LEVEL.clear()
    PROMPTS.clear()
    verbose.set_level(verbose.QUIET)
    yield
    verbose.set_level(verbose.QUIET)


@pytest.fixture
def world(monkeypatch):
    """Install a list of fakes as the catalog; returns it."""

    def install(*tools):
        by_key = {t.key: t for t in tools}
        monkeypatch.setattr(registry, "all_tools", lambda: list(tools))

        def missing_requires(tool):
            out: list[str] = []
            seen = {tool.key}

            def visit(key):
                if key in seen:
                    return
                seen.add(key)
                dep = by_key.get(key)
                if dep is not None:
                    for sub in dep.requires:
                        visit(sub)
                if dep is None or not dep.installed:
                    out.append(key)

            for key in tool.requires:
                visit(key)
            return out

        monkeypatch.setattr(registry, "missing_requires", missing_requires)
        return tools

    return install


@pytest.fixture
def tty(monkeypatch):
    """A terminal that answers the confirmation; `tty(True)` says yes, `tty(False)` says no."""

    def answer(yes: bool | None):
        monkeypatch.setattr(profile_cmd, "_can_prompt", lambda: yes is not None)

        def confirm(prompt, default=False):
            PROMPTS.append(prompt)
            return bool(yes)

        monkeypatch.setattr(ui, "confirm", confirm)

    return answer


def write(tmp_path: Path, **entries) -> Path:
    path = tmp_path / "work.yaml"
    path.write_text(profile.dumps(Profile({k: Entry(v) for k, v in entries.items()})))
    return path


def apply(*args, extra=()):
    return CliRunner().invoke(cli, [*extra, "profile", "apply", *args])


def flat(out: str) -> str:
    return " ".join("".join(ch for ch in out if ch not in "│╭╮╰╯─├┤┬┴┼").split())


def verbs() -> list[tuple]:
    return [(e[0], e[1]) for e in EVENTS]


# -- the profile (FR-1) ---------------------------------------------------------------------------


def test_an_invalid_profile_is_exit_2_before_anything_is_probed(world, tmp_path):
    tool = Fake("a")
    tool.is_installed = lambda: pytest.fail("probed before the profile was validated")
    world(tool)
    bad = tmp_path / "bad.yaml"
    bad.write_text("version: 2\ntools: {}\n")

    result = apply(str(bad), "--yes")

    assert result.exit_code == 2 and "bad.yaml" in result.output and EVENTS == []


def test_a_missing_profile_file_is_exit_2(world, tmp_path):
    world(Fake("a"))

    result = apply(str(tmp_path / "nope.yaml"), "--yes")

    assert result.exit_code == 2 and "nope.yaml" in result.output


# -- confirming (FR-12–14) ------------------------------------------------------------------------


def test_dry_run_shows_the_plan_and_changes_nothing(world, tmp_path, tty):
    world(Fake("a"), Fake("b", installed=True))
    tty(True)

    result = apply(str(write(tmp_path, a=None, b=None)), "--dry-run")

    assert result.exit_code == 0 and EVENTS == [] and PROMPTS == []
    assert "install" in flat(result.output) and "a" in result.output


def test_dry_run_exits_0_even_when_the_plan_has_unactionable_entries(world, tmp_path):
    world(Fake("a"))

    result = apply(str(write(tmp_path, a=None, ghost=None)), "--dry-run")

    assert result.exit_code == 0 and EVENTS == []
    assert "ghost" in result.output and "unknown-key" in result.output


def test_without_a_terminal_and_without_yes_it_refuses_and_changes_nothing(world, tmp_path, tty):
    world(Fake("a"))
    tty(None)  # no terminal

    result = apply(str(write(tmp_path, a=None)))

    assert result.exit_code == 2 and EVENTS == []
    assert "--yes" in result.output and "--dry-run" in result.output


def test_without_a_terminal_a_run_with_nothing_to_do_is_not_refused(world, tmp_path, tty):
    # FR-14: there is nothing to confirm, so nobody is asked and nothing can be refused.
    world(Fake("a", installed=True))
    tty(None)

    result = apply(str(write(tmp_path, a=None)))

    assert result.exit_code == 0 and "matches" in flat(result.output)


def test_yes_runs_without_asking(world, tmp_path, tty):
    world(Fake("a"))
    tty(None)

    result = apply(str(write(tmp_path, a=None)), "--yes")

    assert result.exit_code == 0 and verbs() == [("install", "a")] and PROMPTS == []


def test_confirming_runs_the_plan(world, tmp_path, tty):
    world(Fake("a"))
    tty(True)

    result = apply(str(write(tmp_path, a=None)))

    assert result.exit_code == 0 and verbs() == [("install", "a")] and len(PROMPTS) == 1


def test_declining_changes_nothing_and_says_so(world, tmp_path, tty):
    world(Fake("a"))
    tty(False)

    result = apply(str(write(tmp_path, a=None)))

    assert result.exit_code == 1 and EVENTS == []
    assert "nothing was changed" in flat(result.output).lower()


def test_nothing_to_do_asks_no_question(world, tmp_path, tty):
    world(Fake("a", installed=True))
    tty(True)

    result = apply(str(write(tmp_path, a=None)))

    assert result.exit_code == 0 and PROMPTS == [] and EVENTS == []


# -- what it does (FR-2, 3, 6, 7) -------------------------------------------------------------------


def test_installs_what_is_missing_dependencies_first(world, tmp_path):
    world(Fake("nvm", "bash"), Fake("pkg", requires=["nvm"]))

    result = apply(str(write(tmp_path, pkg=None)), "--yes")

    assert result.exit_code == 0 and verbs() == [("install", "nvm"), ("install", "pkg")]
    assert "needed by pkg" in flat(result.output)


def test_a_pinned_missing_tool_is_one_update_call_and_never_install(world, tmp_path):
    world(Fake("a"))

    result = apply(str(write(tmp_path, a="1.2.3")), "--yes")

    assert result.exit_code == 0 and EVENTS == [("update", "a", "1.2.3")]


def test_drift_is_moved_to_the_pin(world, tmp_path):
    world(Fake("a", installed=True, version="2.0"))

    result = apply(str(write(tmp_path, a="1.0")), "--yes")

    assert result.exit_code == 0 and EVENTS == [("update", "a", "1.0")]


def test_missing_only_leaves_drift_alone_and_exits_1_because_it_is_still_there(world, tmp_path):
    world(Fake("a", installed=True, version="2.0"), Fake("b"))

    result = apply(str(write(tmp_path, a="1.0", b=None)), "--yes", "--missing-only")

    assert verbs() == [("install", "b")]
    assert result.exit_code == 1 and "drift" in result.output


def test_ok_and_extra_tools_are_never_touched(world, tmp_path):
    world(Fake("fine", installed=True), Fake("stray", installed=True), Fake("new"))

    result = apply(str(write(tmp_path, fine=None, new=None)), "--yes")

    assert verbs() == [("install", "new")] and result.exit_code == 0


def test_an_unpinnable_missing_tool_is_skipped_not_installed_at_latest(world, tmp_path):
    world(Fake("script-tool", "bash"))

    result = apply(str(write(tmp_path, **{"script-tool": "1.0"})), "--yes")

    assert EVENTS == [] and result.exit_code == 1
    assert "can't be pinned" in flat(result.output)


def test_it_never_offers_a_setup_wizard(world, tmp_path, monkeypatch):
    from dev_setup.commands import configure_cmd

    monkeypatch.setattr(configure_cmd, "offer_after_install", lambda key: pytest.fail("wizard"))
    world(Fake("a"))

    assert apply(str(write(tmp_path, a=None)), "--yes").exit_code == 0


# -- the verdict is a re-measurement (FR-15–17, SD-3) ---------------------------------------------------


def test_an_installer_that_returns_without_installing_is_not_success(world, tmp_path):
    world(Fake("liar", lies=True))

    result = apply(str(write(tmp_path, liar=None)), "--yes")

    assert verbs() == [("install", "liar")]
    text = flat(result.output)
    assert result.exit_code == 1  # the step "worked"; the machine still doesn't match
    assert "missing" in result.output and "0 of 1 entries match" in text
    assert "now matches" not in text  # never claims success it didn't measure


def test_success_reports_the_count_and_exits_0(world, tmp_path):
    world(Fake("a"), Fake("b"), Fake("c", installed=True))

    result = apply(str(write(tmp_path, a=None, b=None, c=None)), "--yes")

    assert result.exit_code == 0
    assert "matches" in flat(result.output) and "3 of 3" in flat(result.output)


def test_a_failure_does_not_stop_the_run_and_dependents_are_skipped(world, tmp_path):
    world(
        Fake("base", fail="network down"),
        Fake("dep", requires=["base"]),
        Fake("other"),
    )

    result = apply(str(write(tmp_path, dep=None, other=None)), "--yes")

    assert verbs() == [("install", "base"), ("install", "other")]  # `dep` never attempted
    text = flat(result.output)
    assert result.exit_code == 1
    assert "network down" in text and "needs base, which failed" in text
    assert "1 of 2 entries match" in text and "now matches" not in text


def test_a_pinned_step_whose_version_cannot_be_read_back_is_unverified_not_failed(world, tmp_path):
    world(Fake("a", unreadable_after=True))

    result = apply(str(write(tmp_path, a="1.0")), "--yes")

    text = flat(result.output)
    assert result.exit_code == 1 and EVENTS == [("update", "a", "1.0")]
    assert "version unverified" in text and "unverifiable" in text


def test_a_step_that_succeeded_but_is_still_not_detected_says_so_and_where_to_look(world, tmp_path):
    # Seen live: a uv tool installed fine into a bin directory that wasn't on PATH, so the
    # re-measurement (rightly) said "missing" right under a ✔. The bare table is baffling.
    world(Fake("liar", lies=True))

    result = apply(str(write(tmp_path, liar=None)), "--yes")

    text = flat(result.output)
    assert "liar: the installer reported success" in text and "PATH" in text


def test_a_failed_step_does_not_also_get_the_not_detected_hint(world, tmp_path):
    world(Fake("a", fail="boom"))

    text = flat(apply(str(write(tmp_path, a=None)), "--yes").output)

    assert "reported success" not in text


def test_unactionable_entries_are_exit_1_even_when_there_is_nothing_to_run(world, tmp_path):
    world(Fake("a", installed=True))

    result = apply(str(write(tmp_path, a=None, ghost=None)), "--yes")

    assert EVENTS == [] and result.exit_code == 1
    assert "ghost" in result.output


def test_extras_never_affect_the_exit_status(world, tmp_path):
    world(Fake("a"), Fake("stray", installed=True))

    assert apply(str(write(tmp_path, a=None)), "--yes").exit_code == 0


def test_running_it_twice_changes_nothing_the_second_time(world, tmp_path):
    # NFR-4: the test that catches every "reported success, changed nothing" bug.
    world(Fake("nvm", "bash"), Fake("pkg", requires=["nvm"]), Fake("pinned"))
    path = write(tmp_path, pkg=None, pinned="1.0")

    first = apply(str(path), "--yes")
    after_first = list(EVENTS)
    second = apply(str(path), "--yes")

    assert first.exit_code == 0 and len(after_first) == 3
    assert second.exit_code == 0 and after_first == EVENTS  # nothing new happened


# -- interruption (FR-19) -----------------------------------------------------------------------------


def test_ctrl_c_stops_the_run_but_still_reports_and_exits_nonzero(world, tmp_path):
    world(Fake("a"), Fake("b", interrupt=True), Fake("c"))

    result = apply(str(write(tmp_path, a=None, b=None, c=None)), "--yes")

    assert verbs() == [("install", "a"), ("install", "b")]  # `c` never started
    text = flat(result.output)
    assert result.exit_code == 1 and "the run stopped early" in text
    assert "b: interrupted" in text and "c: not run" in text
    assert "1 of 3 entries match" in text  # `a` finished and stays counted


# -- JSON (FR-18, NFR-1) -------------------------------------------------------------------------------


def test_json_requires_yes_or_dry_run(world, tmp_path):
    world(Fake("a", installed=True))

    result = apply(str(write(tmp_path, a=None)), "--json")

    assert result.exit_code == 2 and EVENTS == []
    assert "--yes" in result.stderr and result.stdout == ""


def test_json_dry_run_is_one_document_with_no_run(world, tmp_path):
    world(Fake("a"))

    result = apply(str(write(tmp_path, a=None)), "--dry-run", "--json")

    doc = json.loads(result.stdout)
    assert result.exit_code == 0 and EVENTS == []
    assert set(doc) == {"plan", "run", "rows"} and doc["run"] is None
    assert doc["plan"]["steps"][0]["key"] == "a"
    assert doc["rows"][0]["state"] == "missing"


def test_json_run_reports_the_steps_and_the_final_rows(world, tmp_path):
    world(Fake("a"), Fake("b", fail="nope"))

    result = apply(str(write(tmp_path, a=None, b=None)), "--yes", "--json")

    doc = json.loads(result.stdout)
    assert result.exit_code == 1
    assert [(s["key"], s["status"]) for s in doc["run"]["steps"]] == [
        ("a", "done"), ("b", "failed"),
    ]
    assert {r["key"]: r["state"] for r in doc["rows"]} == {"a": "ok", "b": "missing"}


def test_json_stdout_is_pure_even_when_installers_print_and_verbose_is_on(world, tmp_path):
    world(Fake("a", chatty=True))

    result = apply(str(write(tmp_path, a=None)), "--yes", "--json", extra=["-v"])

    json.loads(result.stdout)  # parses as exactly one document: nothing else on stdout
    assert "installer output for a" in result.stderr  # the noise went somewhere visible
    assert EVENTS_LEVEL == [verbose.QUIET]  # streaming installer output is off under --json
    assert verbose.level() == verbose.VERBOSE  # and -v is back afterwards, as the user set it


def test_json_restores_the_verbosity_it_found(world, tmp_path):
    world(Fake("a"))
    verbose.set_level(verbose.VERBOSE)

    CliRunner().invoke(
        cli, ["profile", "apply", str(write(tmp_path, a=None)), "--yes", "--json"]
    )

    assert verbose.level() == verbose.VERBOSE


# -- display (FR-4, FR-5, FR-16) ------------------------------------------------------------------------


def test_the_plan_marks_steps_that_may_ask_for_a_password(world, tmp_path):
    world(
        Fake("apt-tool", "apt", apt_packages="pkg"),
        Fake("script-tool", "bash", install_script="curl x | sudo bash"),
        Fake("plain-tool", "npm"),
    )

    result = apply(str(write(tmp_path, **{k: None for k in ("apt-tool", "script-tool", "plain-tool")})),
                   "--dry-run")

    lines = {k: next(ln for ln in result.output.splitlines() if k in ln)
             for k in ("apt-tool", "script-tool", "plain-tool")}
    assert "sudo" in lines["apt-tool"] and "sudo" in lines["script-tool"]
    assert "sudo" not in lines["plain-tool"]


def test_the_plan_shows_both_versions_for_a_drift_fix(world, tmp_path):
    world(Fake("a", installed=True, version="2.0"))

    result = apply(str(write(tmp_path, a="1.0")), "--dry-run")

    assert "2.0 → 1.0" in flat(result.output)


def test_a_long_installer_error_is_shortened_to_its_first_line(world, tmp_path):
    world(Fake("a", fail="uv exploded\n  cause: " + "x" * 400))

    result = apply(str(write(tmp_path, a=None)), "--yes")

    text = flat(result.output)
    assert "uv exploded" in text and "xxxx" not in text


def test_a_failure_message_with_brackets_is_not_read_as_markup(world, tmp_path):
    world(Fake("a", fail="error [docs] and [/bold] closing"))

    result = apply(str(write(tmp_path, a=None)), "--yes")

    # Once in the progress line and once in the summary: both are printed through Rich markup.
    assert flat(result.output).count("[docs]") == 2


def test_help_lists_apply(monkeypatch):
    result = CliRunner().invoke(cli, [])

    assert "snapshot|diff|apply" in result.output


def test_a_pinned_install_is_confirmed_through_a_real_memoised_probe(world, tmp_path):
    """The whole chain, with a real `_once` probe behind the version reader.

    Planning reads the version (pinned entry), which warms the memo with the *old* answer; the
    install then changes the machine. If any layer failed to forget, the final comparison would
    read the old answer and call a correct pin unverifiable (spec F-4). Fakes alone can't show it.
    """
    from dev_setup import generic

    tool = Fake("a", installed=True, version="2.0")
    machine = generic._once(lambda: {"a": tool.version})
    tool.installed_version = lambda: machine()["a"]
    world(tool)
    generic.forget_probes()

    result = apply(str(write(tmp_path, a="1.0")), "--yes")

    assert EVENTS == [("update", "a", "1.0")]
    assert result.exit_code == 0 and "now matches" in flat(result.output)
    generic.forget_probes()


# -- bundles (FR-20, FR-21; development-plan A5) -----------------------------------------------------


@pytest.fixture
def bundle_dir(tmp_path, monkeypatch):
    """A directory standing in for the shipped bundles, with `@mine` = {a, b}."""
    root = tmp_path / "bundles"
    root.mkdir()
    (root / "mine.yaml").write_text("version: 1\ntools:\n  a: {}\n  b: {}\n")
    monkeypatch.setattr(bundles, "_root", lambda: root)
    return root


def test_apply_accepts_a_bundle_name_in_place_of_a_file(world, bundle_dir, tty):
    world(Fake("a"), Fake("b"))
    tty(True)

    result = apply("@mine")

    assert result.exit_code == 0, result.output
    assert verbs() == [("install", "a"), ("install", "b")]
    assert PROMPTS  # a bundle is confirmed like any other plan


def test_the_plan_and_the_verdict_name_the_bundle_not_a_file(world, bundle_dir):
    world(Fake("a"), Fake("b"))

    dry = apply("@mine", "--dry-run")
    ran = apply("@mine", "--yes")

    assert "Plan for @mine" in flat(dry.output)
    assert "matches @mine" in flat(ran.output)


def test_a_dry_run_of_a_bundle_changes_nothing(world, bundle_dir):
    world(Fake("a"), Fake("b"))

    result = apply("@mine", "--dry-run")

    assert result.exit_code == 0 and EVENTS == []


def test_an_unknown_bundle_is_exit_2_listing_the_real_ones_unprobed(world, bundle_dir):
    tool = Fake("a")
    tool.is_installed = lambda: pytest.fail("probed before the bundle was resolved")
    world(tool)

    result = apply("@nope", "--yes")

    assert result.exit_code == 2 and EVENTS == []
    assert "@nope" in result.output and "@mine" in result.output


def test_json_with_a_bundle_is_still_one_document(world, bundle_dir):
    world(Fake("a"), Fake("b"))

    result = apply("@mine", "--yes", "--json")

    doc = json.loads(result.stdout)
    assert [s["key"] for s in doc["run"]["steps"]] == ["a", "b"]


def test_dot_slash_names_a_file_that_looks_like_a_bundle(world, bundle_dir, tmp_path, monkeypatch):
    world(Fake("x"))
    monkeypatch.chdir(tmp_path)
    Path("@mine").write_text(profile.dumps(Profile({"x": Entry(None)})))

    result = apply("./@mine", "--yes")

    assert result.exit_code == 0 and verbs() == [("install", "x")]


def test_diff_accepts_a_bundle_name_too(world, bundle_dir):
    world(Fake("a", installed=True), Fake("b"))

    result = CliRunner().invoke(cli, ["profile", "diff", "@mine", "--json"])

    rows = {r["key"]: r["state"] for r in json.loads(result.stdout)}
    assert result.exit_code == 0 and rows == {"a": "ok", "b": "missing"}


def test_diff_with_an_unknown_bundle_is_exit_2(world, bundle_dir):
    world(Fake("a"))

    result = CliRunner().invoke(cli, ["profile", "diff", "@nope"])

    assert result.exit_code == 2 and "@mine" in result.output
