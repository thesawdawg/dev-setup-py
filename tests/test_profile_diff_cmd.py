"""`devstuff profile diff` (docs/specs/profile, FR-15–22, FR-17, NFR-1, NFR-4)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from dev_setup import generic, profile, registry, verbose
from dev_setup.cli import cli
from dev_setup.profile import Entry, Profile


@pytest.fixture(autouse=True)
def quiet_level():
    verbose.set_level(verbose.QUIET)
    yield
    verbose.set_level(verbose.QUIET)


def forbidden(name):
    def call(*_a, **_k):
        raise AssertionError(f"diff must be read-only, but called {name}()")
    return call


def fake(key, install_type="npm", *, installed=True, version="", apt_packages="", probe=None, builtin=True):
    calls: list[str] = []

    def installed_version():
        calls.append("installed_version")
        if probe:
            probe()
        return version

    return SimpleNamespace(
        key=key,
        name=key.title(),
        install_type=install_type,
        apt_packages=apt_packages,
        builtin=builtin,
        is_installed=lambda: installed,
        installed_version=installed_version,
        install=forbidden("install"),
        remove=forbidden("remove"),
        update=forbidden("update"),
        calls=calls,
    )


# One machine that produces every state. The profile below is written to match it.
def make_machine():
    return [
        fake("t-ok", "npm", version="1.0"),                     # pinned 1.0, installed 1.0
        fake("t-plain", "uvx"),                                  # unpinned, installed
        fake("t-missing", "npm", installed=False),               # in profile, not installed
        fake("t-drift", "npm", version="2.0"),                   # pinned 1.0, installed 2.0
        fake("t-unverifiable", "uvx", version=""),               # pinned, but unreadable
        fake("t-unpinnable", "bash", version="1.0"),             # pinned, but bash cannot be pinned
        fake("t-stray", "npm"),                                  # installed, not in profile
        fake("t-idle", "npm", installed=False),                  # not installed, not in profile: no row
    ]


PROFILE = Profile(
    {
        "t-ok": Entry("1.0"),
        "t-plain": Entry(),
        "t-missing": Entry(),
        "t-drift": Entry("1.0"),
        "t-unverifiable": Entry("1.0"),
        "t-unpinnable": Entry("1.0"),
        "t-ghost": Entry(),  # not in this machine's catalog at all
    }
)


@pytest.fixture
def machine(monkeypatch):
    tools = make_machine()
    monkeypatch.setattr(registry, "all_tools", lambda: tools)
    return tools


@pytest.fixture
def pfile(tmp_path: Path) -> Path:
    path = tmp_path / "work.yaml"
    path.write_text(profile.dumps(PROFILE))
    return path


def run(*args):
    return CliRunner().invoke(cli, ["profile", "diff", *args])


def cells(out: str) -> list[str]:
    return [ln.strip("│ ").strip() for ln in out.splitlines()]


def flat(out: str) -> str:
    """The output with the table frame removed and wrapped lines joined — text, not layout.

    Rich wraps at the terminal width (80 under CliRunner), so a phrase can be split across
    two lines of a cell. Tests about *what is said* should not depend on where it wrapped.
    """
    return " ".join("".join(ch for ch in out if ch not in "│╭╮╰╯─├┤┬┴┼").split())


def line_for(out: str, key: str) -> str:
    return next(ln for ln in cells(out) if ln.startswith(key + " ") or ln == key)


def json_rows(result) -> dict[str, dict]:
    return {r["key"]: r for r in json.loads(result.stdout)}


# -- every state, end to end (FR-16) ------------------------------------------------------------


def test_every_state_is_reported(machine, pfile):
    got = {k: r["state"] for k, r in json_rows(run(str(pfile), "--json")).items()}

    assert got == {
        "t-ok": "ok",
        "t-plain": "ok",
        "t-missing": "missing",
        "t-drift": "drift",
        "t-unverifiable": "unverifiable",
        "t-unpinnable": "unpinnable",
        "t-ghost": "unknown-key",
        "t-stray": "extra",
    }


def test_a_tool_neither_installed_nor_in_the_profile_is_not_mentioned(machine, pfile):
    assert "t-idle" not in run(str(pfile), "--all").output
    assert "t-idle" not in json_rows(run(str(pfile), "--json"))


# -- the table (FR-19, FR-20) ------------------------------------------------------------------


def test_by_default_only_differences_are_listed(machine, pfile):
    out = run(str(pfile)).output

    for key in ("t-missing", "t-drift", "t-unverifiable", "t-unpinnable", "t-ghost"):
        assert any(ln.startswith(key) for ln in cells(out)), key
    assert not any(ln.startswith(("t-ok", "t-plain")) for ln in cells(out))


def test_all_also_lists_the_matching_tools_and_the_extras(machine, pfile):
    lines = cells(run(str(pfile), "--all").output)

    assert any(ln.startswith("t-ok") for ln in lines)
    assert any(ln.startswith("t-plain") for ln in lines)
    assert any(ln.startswith("t-stray") for ln in lines)


def test_extras_collapse_to_one_footer_line_naming_them(machine, pfile):
    out = run(str(pfile)).output

    assert not any(ln.startswith("t-stray") for ln in cells(out))  # not a table row
    footer = [ln for ln in out.splitlines() if "t-stray" in ln]
    assert len(footer) == 1
    assert "in the profile" in footer[0] and "--all" in footer[0]


def test_the_summary_counts_every_non_zero_state_in_attention_order(machine, pfile):
    out = flat(run(str(pfile)).output)

    assert "1 missing · 1 drift · 1 unverifiable · 1 unpinnable · 1 unknown-key · 1 extra · 2 ok" in out


def test_pinned_and_installed_versions_are_shown_for_a_drifting_tool(machine, pfile):
    line = line_for(run(str(pfile)).output, "t-drift")

    assert "1.0" in line and "2.0" in line


def test_notes_explain_the_states_that_could_not_be_compared(machine, pfile):
    out = flat(run(str(pfile)).output)

    assert "uv doesn't list it as a tool" in out         # unverifiable: why it could not be read
    assert "can't be pinned" in out                      # unpinnable
    assert "catalog import" in out                       # unknown-key: where to go next


def test_a_clean_machine_says_so(monkeypatch, tmp_path: Path):
    tools = [fake("a", "npm", version="1.0")]
    monkeypatch.setattr(registry, "all_tools", lambda: tools)
    path = tmp_path / "p.yaml"
    path.write_text(profile.dumps(Profile({"a": Entry("1.0")})))

    result = run(str(path))

    assert result.exit_code == 0
    assert "matches" in result.output
    assert "1 ok" in result.output


# -- FR-17: could-not-compare never reads as ok ------------------------------------------------


@pytest.mark.parametrize("key", ["t-unverifiable", "t-unpinnable", "t-ghost"])
def test_a_could_not_compare_row_never_looks_like_ok(machine, pfile, key):
    line = line_for(run(str(pfile)).output, key)

    assert "✔" not in line, line
    assert not line.rstrip().endswith(" ok"), line


def test_ok_rows_are_marked_as_ok_so_the_check_above_means_something(machine, pfile):
    assert "✔" in line_for(run(str(pfile), "--all").output, "t-ok")


def test_the_three_could_not_compare_states_read_differently_from_one_another(machine, pfile):
    out = run(str(pfile)).output

    words = {key: line_for(out, key) for key in ("t-unverifiable", "t-unpinnable", "t-ghost")}
    assert "unverifiable" in words["t-unverifiable"]
    assert "unpinnable" in words["t-unpinnable"]
    assert "unknown-key" in words["t-ghost"]
    assert "unverifiable" not in words["t-unpinnable"] and "unpinnable" not in words["t-unverifiable"]


# -- --json (FR-21, NFR-1) --------------------------------------------------------------------


def test_json_has_exactly_the_documented_fields_for_every_row(machine, pfile):
    data = json.loads(run(str(pfile), "--json").stdout)

    assert len(data) == 8
    for row in data:
        assert set(row) == {"key", "state", "type", "pinned", "installed", "note"}


def test_json_includes_ok_and_extra_rows_always(machine, pfile):
    states = {r["state"] for r in json.loads(run(str(pfile), "--json").stdout)}

    assert {"ok", "extra"} <= states


def test_json_ignore_extras_does_not_filter_rows(machine, pfile):
    # --ignore-extras is about the exit status; filtering is a display concern (FR-21).
    assert "t-stray" in json_rows(run(str(pfile), "--json", "--ignore-extras"))


def test_json_values_use_null_for_absent(machine, pfile):
    rows = json_rows(run(str(pfile), "--json"))

    assert rows["t-ghost"]["type"] is None and rows["t-ghost"]["installed"] is None
    assert rows["t-missing"]["installed"] is None
    assert rows["t-drift"]["pinned"] == "1.0" and rows["t-drift"]["installed"] == "2.0"


def test_json_rows_are_sorted_problems_first(machine, pfile):
    keys = [r["key"] for r in json.loads(run(str(pfile), "--json").stdout)]

    assert keys[0] == "t-missing" and keys[-1] in {"t-ok", "t-plain"}


def test_json_stdout_is_pure_at_vv(monkeypatch, tmp_path: Path):
    def noisy():
        generic._probe(["echo", "hello"])  # logs `$ echo hello` at -vv

    tools = [fake("claude-code", "npm", version="1.0", probe=noisy)]
    monkeypatch.setattr(registry, "all_tools", lambda: tools)
    path = tmp_path / "p.yaml"
    path.write_text(profile.dumps(Profile({"claude-code": Entry("1.0")})))

    result = CliRunner().invoke(cli, ["-vv", "profile", "diff", str(path), "--json"])

    assert result.exit_code == 0
    assert "echo hello" in result.stderr  # the log fired, so this is not vacuous
    assert json.loads(result.stdout)[0]["state"] == "ok"


# -- banner and spinner only for a person at a terminal (NFR-1) -----------------------------------


@pytest.fixture
def on_a_terminal(monkeypatch):
    """Make CliRunner's stdout claim to be a TTY, so the interactive path is the one exercised.

    Without this the banner/spinner guard is never reached under test (CliRunner is not a
    terminal), and "no banner under --json" would pass whatever the code did.
    """
    from click.testing import _NamedTextIOWrapper

    monkeypatch.setattr(_NamedTextIOWrapper, "isatty", lambda self: True)
    shown: list[str] = []
    monkeypatch.setattr("dev_setup.ui.print_banner", lambda: shown.append("banner"))
    return shown


def test_json_prints_no_banner_even_on_a_terminal(machine, pfile, on_a_terminal):
    result = run(str(pfile), "--json")

    assert on_a_terminal == [], "a banner on stdout would corrupt the JSON"
    assert json.loads(result.stdout)  # the whole of stdout is the array


def test_a_person_at_a_terminal_does_get_the_banner(machine, pfile, on_a_terminal):
    run(str(pfile))

    assert on_a_terminal == ["banner"], "otherwise the test above proves nothing"


def test_a_pipe_gets_no_banner(machine, pfile, monkeypatch):
    shown: list[str] = []
    monkeypatch.setattr("dev_setup.ui.print_banner", lambda: shown.append("banner"))

    run(str(pfile))  # CliRunner's stdout is not a terminal

    assert shown == []


# -- exit status (FR-22, SD-8) ------------------------------------------------------------------


def test_default_exit_status_is_0_even_when_everything_differs(machine, pfile):
    assert run(str(pfile)).exit_code == 0
    assert run(str(pfile), "--json").exit_code == 0


def test_exit_code_flag_is_1_when_the_machine_differs(machine, pfile):
    assert run(str(pfile), "--exit-code").exit_code == 1
    assert run(str(pfile), "--exit-code", "--json").exit_code == 1


def _clean(monkeypatch, tmp_path, *, with_extra=False):
    tools = [fake("a", "npm", version="1.0")] + ([fake("stray", "npm")] if with_extra else [])
    monkeypatch.setattr(registry, "all_tools", lambda: tools)
    path = tmp_path / "p.yaml"
    path.write_text(profile.dumps(Profile({"a": Entry("1.0")})))
    return path


def test_exit_code_flag_is_0_on_a_clean_match(monkeypatch, tmp_path):
    assert run(str(_clean(monkeypatch, tmp_path)), "--exit-code").exit_code == 0


def test_an_extra_tool_is_a_difference_unless_ignored(monkeypatch, tmp_path):
    path = _clean(monkeypatch, tmp_path, with_extra=True)

    assert run(str(path), "--exit-code").exit_code == 1
    assert run(str(path), "--exit-code", "--ignore-extras").exit_code == 0


def test_ignore_extras_never_hides_a_real_difference(machine, pfile):
    assert run(str(pfile), "--exit-code", "--ignore-extras").exit_code == 1


# -- a bad profile is 2, so 1 can only mean "differs" (SD-8) -----------------------------------------


@pytest.mark.parametrize("flags", [[], ["--exit-code"], ["--json"], ["--exit-code", "--json"]])
def test_a_missing_file_is_exit_2_with_nothing_on_stdout(machine, tmp_path, flags):
    result = run(str(tmp_path / "nope.yaml"), *flags)

    assert result.exit_code == 2
    assert result.stdout == ""
    assert "nope.yaml" in result.stderr


@pytest.mark.parametrize(
    ("text", "fragment"),
    [
        ("version: 2\ntools: {}\n", "version"),
        ("version: 1\ntools:\n  a:\n    version: 1.10\n", "quote"),
        ("version: 1\ntools:\n  a: {}\n  a: {}\n", "duplicate"),
        ("not: a profile\n", "unknown field"),
    ],
)
def test_an_invalid_profile_is_exit_2_naming_the_file(machine, tmp_path, text, fragment):
    path = tmp_path / "bad.yaml"
    path.write_text(text)

    result = run(str(path), "--exit-code")

    assert result.exit_code == 2
    assert result.stdout == ""
    assert "bad.yaml" in result.stderr and fragment in result.stderr.lower()
    assert "Traceback" not in result.output


def test_a_directory_instead_of_a_file_is_exit_2(machine, tmp_path):
    assert run(str(tmp_path)).exit_code == 2


def test_no_profile_argument_is_a_usage_error(machine):
    assert run().exit_code == 2


# -- what it reads, and what it must not touch (FR-15, FR-11) -------------------------------------


def test_only_pinned_pinnable_installed_entries_have_their_version_read(machine, pfile):
    run(str(pfile))
    by_key = {t.key: t.calls for t in machine}

    assert by_key["t-ok"] == ["installed_version"]            # pinned, pinnable, installed
    assert by_key["t-drift"] == ["installed_version"]
    assert by_key["t-unverifiable"] == ["installed_version"]
    assert by_key["t-unpinnable"] == []                       # pinned but bash: never asked
    assert by_key["t-plain"] == []                            # unpinned: never asked
    assert by_key["t-missing"] == []                          # not installed: nothing to read
    assert by_key["t-stray"] == []                            # not in the profile


def test_a_pinned_tool_that_is_not_installed_is_missing_and_nothing_is_read(monkeypatch, tmp_path):
    # The shared fixture's missing tool is unpinned, so it never tempts a read; this one does.
    gone = fake("gone", "npm", installed=False, version="9.9.9")
    monkeypatch.setattr(registry, "all_tools", lambda: [gone])
    path = tmp_path / "p.yaml"
    path.write_text(profile.dumps(Profile({"gone": Entry("1.0")})))

    got = json_rows(run(str(path), "--json"))

    assert got["gone"]["state"] == "missing"
    assert got["gone"]["pinned"] == "1.0" and got["gone"]["installed"] is None
    assert gone.calls == [], "reading the version of a tool that isn't installed is wasted work"


def test_a_version_reader_that_raises_makes_the_row_unverifiable_not_fatal(monkeypatch, tmp_path):
    flaky = fake("flaky", "npm")

    def explode():
        raise RuntimeError("boom")

    flaky.installed_version = explode
    monkeypatch.setattr(registry, "all_tools", lambda: [flaky, fake("fine", "npm", version="1.0")])
    path = tmp_path / "p.yaml"
    path.write_text(profile.dumps(Profile({"flaky": Entry("1.0"), "fine": Entry("1.0")})))

    result = run(str(path), "--json")

    assert result.exit_code == 0
    got = json_rows(result)
    assert got["flaky"]["state"] == "unverifiable" and "npm" in got["flaky"]["note"]
    assert got["fine"]["state"] == "ok"


def test_a_note_with_square_brackets_is_printed_literally_not_parsed_as_markup(capsys):
    # Notes today are fixed strings, so only a direct call can prove the escaping — and Rich
    # raises MarkupError on a stray closing tag like `[/x]`, which would end the command.
    from dev_setup.commands import profile_cmd

    row = profile.DiffRow("k", profile.DiffState.UNVERIFIABLE, "npm", "1", None, "see [docs] and [/x]")

    profile_cmd._render([row], show_all=True)

    assert "see [docs] and [/x]" in flat(capsys.readouterr().out)


def test_an_unpinned_profile_reads_no_versions_at_all(monkeypatch, tmp_path):
    tools = [fake("a"), fake("b", "uvx")]
    monkeypatch.setattr(registry, "all_tools", lambda: tools)
    path = tmp_path / "p.yaml"
    path.write_text(profile.dumps(Profile({"a": Entry(), "b": Entry()})))

    run(str(path))

    assert all(t.calls == [] for t in tools)


def test_diff_is_read_only(machine, pfile):
    # Every fake raises if install/remove/update is touched; completing proves it never was.
    assert run(str(pfile), "--all").exit_code == 0


def test_a_tool_whose_checks_raise_does_not_stop_the_diff(monkeypatch, tmp_path):
    flaky = fake("flaky", "npm", version="1.0")
    flaky.is_installed = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    tools = [flaky, fake("fine", "npm", version="1.0")]
    monkeypatch.setattr(registry, "all_tools", lambda: tools)
    path = tmp_path / "p.yaml"
    path.write_text(profile.dumps(Profile({"flaky": Entry("1.0"), "fine": Entry("1.0")})))

    got = json_rows(run(str(path), "--json"))

    assert got["fine"]["state"] == "ok"
    assert got["flaky"]["state"] == "missing"  # cannot claim it is installed


# -- round trip with snapshot (NFR-4) ---------------------------------------------------------------


@pytest.mark.parametrize("versions", [False, True])
def test_a_fresh_snapshot_diffs_clean_against_the_machine_that_made_it(machine, tmp_path, versions):
    path = tmp_path / "snap.yaml"
    args = ["profile", "snapshot", "-o", str(path)] + (["--versions"] if versions else [])
    assert CliRunner().invoke(cli, args).exit_code == 0

    result = run(str(path), "--exit-code", "--json")

    states = {r["state"] for r in json.loads(result.stdout)}
    # The one tool snapshot could not read a version for is `unverifiable` only if it was pinned,
    # and snapshot never pins what it could not read — so a fresh snapshot has no differences.
    assert states == {"ok"}, states
    assert result.exit_code == 0


# -- the command group (FR-23) ------------------------------------------------------------------


def test_diff_is_listed_under_profile_and_documents_its_flags():
    listing = CliRunner().invoke(cli, ["profile"]).output
    assert "diff" in listing and "snapshot" in listing

    result = CliRunner().invoke(cli, ["profile", "diff", "--help"])
    assert result.exit_code == 0
    for flag in ("--all", "--json", "--exit-code", "--ignore-extras", "-v"):
        assert flag in result.output


def test_the_help_table_mentions_diff():
    assert "diff" in CliRunner().invoke(cli, []).output
