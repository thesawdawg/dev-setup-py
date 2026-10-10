"""`devstuff outdated` (docs/specs/outdated, FR-1–14, FR-20, FR-22)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from dev_setup import generic, registry, verbose
from dev_setup.cli import cli
from dev_setup.generic import UpdateStatus


@pytest.fixture(autouse=True)
def quiet_level():
    verbose.set_level(verbose.QUIET)
    yield
    verbose.set_level(verbose.QUIET)


def _forbidden(name):
    def call(*_a, **_k):
        raise AssertionError(f"outdated must be read-only, but called {name}()")
    return call


def fake(key, install_type="npm", *, installed=True, status=None, check=None):
    return SimpleNamespace(
        key=key,
        name=key.title(),
        install_type=install_type,
        is_installed=lambda: installed,
        check_for_update=check or (lambda: status or UpdateStatus()),
        # FR-3: anything that mutates must blow up the test if the command touches it.
        install=_forbidden("install"),
        remove=_forbidden("remove"),
        update=_forbidden("update"),
    )


OUTDATED = UpdateStatus(current="1.0.0", latest="1.1.0", available=True)
CURRENT = UpdateStatus(current="2.0.0", latest="2.0.0", available=False)

CATALOG = [
    fake("codex", "npm", status=OUTDATED),
    fake("eslint", "npm", status=CURRENT),
    fake("flaky", "npm", status=UpdateStatus()),  # a checker that couldn't answer
    fake("starship", "bash"),  # no checker exists
    fake("nvm", "bash"),
    fake("absent", "npm", installed=False),
]


@pytest.fixture
def catalog(monkeypatch):
    monkeypatch.setattr(registry, "all_tools", lambda: list(CATALOG))
    monkeypatch.setattr(registry, "get", lambda key: next((t for t in CATALOG if t.key == key), None))
    return CATALOG


def run(*args):
    return CliRunner().invoke(cli, ["outdated", *args])


def cells(out):
    """Output lines with the table frame stripped, so a row starts with its package key."""
    return [ln.strip("│ ").strip() for ln in out.splitlines()]


def rows(result):
    return {r["key"]: r for r in json.loads(result.stdout)}


# -- the five states -----------------------------------------------------------------


def test_every_state_is_reported_with_the_right_state(catalog):
    got = rows(run("--json", "absent", "codex", "eslint", "flaky", "starship"))

    assert {k: r["state"] for k, r in got.items()} == {
        "codex": "outdated",
        "eslint": "current",
        "flaky": "unknown",
        "starship": "unsupported",
        "absent": "not-installed",
    }


def test_unknown_and_unsupported_never_render_as_current(catalog):
    """The requirement the command exists to protect (FR-7)."""
    out = run("--all").output

    by_key = {ln.split()[0]: ln for ln in cells(out) if ln.split()}
    assert "current" in by_key["eslint"] and "✔" in by_key["eslint"]
    for key in ("flaky", "starship", "nvm"):
        assert "current" not in by_key[key], by_key[key]
        assert "✔" not in by_key[key], by_key[key]
    # ...and the two kinds of "can't tell" read differently from each other.
    assert "unknown" in by_key["flaky"]
    assert "unknown" not in by_key["starship"]


def test_summary_counts_each_state_and_unknown_is_not_folded_into_current(catalog):
    out = run().output

    assert "1 outdated" in out
    assert "1 current" in out
    assert "1 unknown" in out
    assert "2 unsupported" in out
    assert "up to date" not in out


def test_outdated_rows_show_both_versions(catalog):
    line = next(ln for ln in cells(run().output) if ln.startswith("codex"))

    assert "1.0.0" in line and "1.1.0" in line


# -- layout --------------------------------------------------------------------------


def test_unsupported_rows_are_collapsed_to_one_footer_by_default(catalog):
    out = run().output

    assert not any(ln.startswith(("starship", "nvm")) for ln in cells(out))
    footer = next(ln for ln in out.splitlines() if "can't be checked" in ln)
    assert "2 tools" in footer
    assert "starship" in out and "nvm" in out  # still named, so the blindness stays visible


def test_all_expands_unsupported_rows(catalog):
    lines = cells(run("--all").output)

    assert any(ln.startswith("starship") for ln in lines)
    assert any(ln.startswith("nvm") for ln in lines)
    assert not any("can't be checked" in ln for ln in lines)


def test_rows_are_ordered_outdated_unknown_current(catalog):
    keys = [ln.split()[0] for ln in cells(run().output) if ln.split()]
    order = [k for k in keys if k in {"codex", "flaky", "eslint"}]

    assert order == ["codex", "flaky", "eslint"]


def test_updates_only_hides_everything_but_outdated_but_still_counts_everything(catalog):
    out = run("--updates-only").output

    assert any(ln.startswith("codex") for ln in cells(out))
    assert not any(ln.startswith(("eslint", "flaky")) for ln in cells(out))
    assert "1 current" in out and "1 unknown" in out and "2 unsupported" in out


def test_updates_only_with_nothing_outdated_says_so_and_still_reports_what_it_could_not_check(
    monkeypatch,
):
    only = [fake("eslint", "npm", status=CURRENT), fake("nvm", "bash")]
    monkeypatch.setattr(registry, "all_tools", lambda: only)

    result = run("--updates-only")

    assert result.exit_code == 0
    assert "No updates" in result.output
    assert "1 unsupported" in result.output


def test_nothing_installed_is_a_quiet_success(monkeypatch):
    monkeypatch.setattr(registry, "all_tools", lambda: [fake("absent", installed=False)])

    result = run()

    assert result.exit_code == 0
    assert "No installed packages" in result.output


# -- selecting tools ------------------------------------------------------------------


def test_named_keys_check_only_those(catalog):
    got = rows(run("--json", "codex"))

    assert list(got) == ["codex"]


def test_a_named_tool_that_is_not_installed_is_a_row_not_an_error(catalog):
    result = run("absent")

    assert result.exit_code == 0
    assert "not installed" in result.output
    assert "1 not installed" in result.output


def test_unknown_key_exits_1_and_checks_nothing(catalog):
    probed = []
    catalog[0].check_for_update = lambda: probed.append(1) or OUTDATED
    try:
        result = run("codex", "no-such-tool")
    finally:
        catalog[0].check_for_update = lambda: OUTDATED

    assert result.exit_code == 1
    assert "no-such-tool" in result.output + result.stderr
    assert probed == []


# -- exit status (FR-14) --------------------------------------------------------------


def test_exit_status_is_0_even_when_things_are_outdated(catalog):
    assert run().exit_code == 0
    assert run("--json").exit_code == 0


# -- read-only (FR-3) ------------------------------------------------------------------


def test_command_is_read_only(catalog):
    # Every fake raises if install/remove/update is called; completing proves it never was.
    result = run("--all")

    assert result.exit_code == 0, result.output


# -- robustness (FR-16) ---------------------------------------------------------------


def test_one_tool_raising_does_not_stop_the_others(monkeypatch):
    def boom():
        raise RuntimeError("registry exploded")

    tools = [fake("bad", "npm", check=boom), fake("good", "npm", status=OUTDATED)]
    monkeypatch.setattr(registry, "all_tools", lambda: tools)

    got = rows(run("--json"))

    assert got["bad"]["state"] == "unknown"
    assert got["good"]["state"] == "outdated"


# -- --json (FR-12, NFR-1, FR-22) -------------------------------------------------------


def test_json_is_an_array_of_exactly_six_fields(catalog):
    data = json.loads(run("--json").stdout)

    assert isinstance(data, list) and len(data) == 5  # the five installed; `absent` wasn't asked for
    for rec in data:
        assert set(rec) == {"key", "type", "state", "installed", "latest", "note"}


def test_json_includes_unsupported_rows_even_though_the_table_collapses_them(catalog):
    got = rows(run("--json"))

    assert got["starship"]["state"] == "unsupported"
    assert got["starship"]["installed"] is None and got["starship"]["latest"] is None


def test_json_updates_only_filters(catalog):
    assert list(rows(run("--json", "--updates-only"))) == ["codex"]


def test_json_stdout_is_pure_json_even_at_vv(monkeypatch):
    """Verbose output is for stderr only; stdout is parsed by whatever called us (NFR-1)."""

    def noisy_check():
        generic._probe(["echo", "hello"])  # logs `$ echo hello` at -vv
        return OUTDATED

    monkeypatch.setattr(registry, "all_tools", lambda: [fake("codex", check=noisy_check)])

    result = CliRunner().invoke(cli, ["-vv", "outdated", "--json"])

    assert result.exit_code == 0
    assert "echo hello" in result.stderr  # the log really fired, so this test isn't vacuous
    assert json.loads(result.stdout)[0]["key"] == "codex"


def test_json_has_no_banner_or_spinner(catalog):
    out = run("--json").stdout

    assert out.lstrip().startswith("[")
    assert out.rstrip().endswith("]")


def test_command_is_registered_and_advertises_verbose():
    result = CliRunner().invoke(cli, ["outdated", "--help"])

    assert result.exit_code == 0
    assert "--json" in result.output and "--updates-only" in result.output
    assert "-v" in result.output
