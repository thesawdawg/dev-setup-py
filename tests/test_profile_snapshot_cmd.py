"""`devstuff profile snapshot` (docs/specs/profile, FR-7–14, FR-23, NFR-1)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from dev_setup import generic, profile, registry, verbose
from dev_setup.cli import cli


@pytest.fixture(autouse=True)
def quiet_level():
    verbose.set_level(verbose.QUIET)
    yield
    verbose.set_level(verbose.QUIET)


def forbidden(name):
    def call(*_a, **_k):
        raise AssertionError(f"snapshot must be read-only, but called {name}()")
    return call


def fake(key, install_type="npm", *, installed=True, version="", builtin=True, apt_packages="", probe=None):
    def installed_version():
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
    )


MACHINE = [
    fake("uv", "bash"),
    fake("claude-code", "npm", version="2.1.296"),
    fake("ipython", "uvx", version="9.17.1"),
    fake("commitizen", "uvx", version=""),  # pinnable, but not readable (not a `uv tool`)
    fake("absent", "npm", installed=False, version="9.9.9"),
]


@pytest.fixture
def machine(monkeypatch):
    monkeypatch.setattr(registry, "all_tools", lambda: list(MACHINE))
    return MACHINE


def run(*args):
    return CliRunner().invoke(cli, ["profile", "snapshot", *args])


# -- stdout is exactly the profile (FR-7, FR-14) -----------------------------------------------


def test_stdout_is_a_valid_profile_of_the_installed_tools(machine):
    result = run()

    assert result.exit_code == 0, result.output
    loaded = profile.loads(result.stdout)
    assert set(loaded.tools) == {"uv", "claude-code", "ipython", "commitizen"}
    assert "absent" not in loaded.tools


def test_default_snapshot_is_keys_only(machine):
    loaded = profile.loads(run().stdout)

    assert all(entry.version is None for entry in loaded.tools.values())
    assert "version:" not in run().stdout.split("tools:", 1)[1]


def test_output_is_sorted_and_byte_identical_across_runs(machine):
    first, second = run().stdout, run().stdout

    assert first == second
    assert list(profile.loads(first).tools) == sorted(profile.loads(first).tools)


def test_an_empty_machine_is_a_valid_empty_profile(monkeypatch):
    monkeypatch.setattr(registry, "all_tools", lambda: [fake("a", installed=False)])

    result = run()

    assert result.exit_code == 0
    assert profile.loads(result.stdout) == profile.Profile()


def test_stdout_has_nothing_but_the_profile_even_with_notes_to_print(monkeypatch):
    tools = [fake("mine", builtin=False), fake("lost", "uvx", version="")]
    monkeypatch.setattr(registry, "all_tools", lambda: tools)

    result = run("--versions")

    assert result.stderr.strip(), "this test needs notes on stderr to be meaningful"
    assert profile.loads(result.stdout)  # parses as a profile, so nothing else is in it
    assert result.stdout.startswith("# ")


# -- --versions (FR-10, FR-12) ---------------------------------------------------------------


def test_versions_flag_records_readable_pinnable_versions(machine):
    loaded = profile.loads(run("--versions").stdout)

    assert loaded.tools["claude-code"].version == "2.1.296"
    assert loaded.tools["ipython"].version == "9.17.1"
    assert loaded.tools["uv"].version is None  # bash: cannot be pinned, so never recorded


def test_an_unreadable_pinnable_tool_is_written_bare_with_a_named_warning(machine):
    result = run("--versions")

    assert profile.loads(result.stdout).tools["commitizen"].version is None
    warning = next(ln for ln in result.stderr.splitlines() if "commitizen" in ln)
    assert "version" in warning
    assert "uv" in warning  # says why: uv doesn't list it as a tool


def test_no_unreadable_warning_without_versions(machine):
    assert "commitizen" not in run().stderr


def test_warnings_say_why_per_install_type(monkeypatch):
    tools = [
        fake("n", "npm"),
        fake("u", "uvx"),
        fake("a", "apt", apt_packages="pkg"),
    ]
    monkeypatch.setattr(registry, "all_tools", lambda: tools)

    warnings = [ln for ln in run("--versions").stderr.splitlines() if ln.startswith("warning: ")]
    lines = {ln.split(":")[1].strip(): ln for ln in warnings}

    assert set(lines) == {"n", "u", "a"}
    assert "npm" in lines["n"]
    assert "uv" in lines["u"]
    assert "dpkg" in lines["a"]


# -- custom tools (FR-13) ---------------------------------------------------------------------


def test_custom_tools_are_included_with_one_stderr_line_pointing_at_catalog_export(monkeypatch):
    tools = [fake("bundled"), fake("mine", builtin=False), fake("yours", builtin=False)]
    monkeypatch.setattr(registry, "all_tools", lambda: tools)

    result = run()

    assert {"bundled", "mine", "yours"} == set(profile.loads(result.stdout).tools)
    notes = [ln for ln in result.stderr.splitlines() if "catalog" in ln]
    assert len(notes) == 1
    assert "2" in notes[0] and "mine" in notes[0] and "catalog export" in notes[0]


def test_no_custom_note_when_everything_is_bundled(machine):
    assert "catalog" not in run().stderr


# -- -o PATH (FR-7, FR-8) --------------------------------------------------------------------


def test_o_writes_the_file_and_leaves_stdout_empty(machine, tmp_path: Path):
    out = tmp_path / "work.yaml"

    result = run("-o", str(out))

    assert result.exit_code == 0, result.output
    assert result.stdout == ""
    assert set(profile.load(out).tools) == {"uv", "claude-code", "ipython", "commitizen"}


def test_o_reports_what_it_wrote_on_stderr(machine, tmp_path: Path):
    out = tmp_path / "work.yaml"

    result = run("-o", str(out))

    assert str(out) in result.stderr and "4" in result.stderr


def test_o_matches_what_stdout_would_have_been_byte_for_byte(machine, tmp_path: Path):
    out = tmp_path / "work.yaml"

    run("-o", str(out))

    assert out.read_text() == run().stdout


def test_an_existing_file_is_refused_and_left_untouched(machine, tmp_path: Path):
    out = tmp_path / "work.yaml"
    out.write_text("precious hand edits\n")

    result = run("-o", str(out))

    assert result.exit_code == 2
    assert "--force" in result.stderr and str(out) in result.stderr
    assert out.read_text() == "precious hand edits\n"


def test_force_overwrites(machine, tmp_path: Path):
    out = tmp_path / "work.yaml"
    out.write_text("old\n")

    result = run("-o", str(out), "--force")

    assert result.exit_code == 0
    assert profile.load(out).tools


def test_a_directory_target_is_an_error_not_a_traceback(machine, tmp_path: Path):
    result = run("-o", str(tmp_path))

    assert result.exit_code == 2
    assert "Traceback" not in result.output


def test_a_missing_parent_directory_is_a_clean_error(machine, tmp_path: Path):
    result = run("-o", str(tmp_path / "no" / "such" / "dir" / "p.yaml"))

    assert result.exit_code == 2
    assert "Traceback" not in result.output and "p.yaml" in result.stderr


# -- read-only, no network, verbosity (NFR-1, NFR-2) ------------------------------------------


def test_snapshot_changes_nothing_on_the_machine(machine):
    # Every fake raises if install/remove/update is touched; completing proves it never was.
    assert run("--versions").exit_code == 0


def test_stdout_stays_pure_at_vv(monkeypatch):
    def noisy():
        generic._probe(["echo", "hello"])  # logs `$ echo hello` at -vv

    tools = [fake("claude-code", "npm", version="1.0", probe=noisy)]
    monkeypatch.setattr(registry, "all_tools", lambda: tools)

    result = CliRunner().invoke(cli, ["-vv", "profile", "snapshot", "--versions"])

    assert result.exit_code == 0
    assert "echo hello" in result.stderr  # the log fired, so this is not vacuous
    assert profile.loads(result.stdout).tools["claude-code"].version == "1.0"


# -- the command group (FR-23) -----------------------------------------------------------------


def test_profile_alone_shows_help_listing_snapshot():
    result = CliRunner().invoke(cli, ["profile"])

    assert "snapshot" in result.output
    assert "Traceback" not in result.output


def test_snapshot_help_documents_its_flags_and_accepts_verbose():
    result = CliRunner().invoke(cli, ["profile", "snapshot", "--help"])

    assert result.exit_code == 0
    for flag in ("--versions", "--force", "-o", "-v"):
        assert flag in result.output


def test_profile_is_in_the_help_table():
    # The table is what bare `devstuff` prints; it is hand-maintained, so a new command can
    # silently be missing from it.
    result = CliRunner().invoke(cli, [])

    assert result.exit_code == 0
    assert "profile" in result.output and "snapshot" in result.output


def test_snapshot_shells_out_only_through_generics_helpers():
    # A direct `subprocess` call would be a hole in -v/-vv coverage (verbose-mode: generic.py's
    # `_run`/`_probe` are the only places allowed to spawn). Snapshot reaches the machine only
    # via tool.is_installed()/installed_version(), which go through them.
    import dev_setup.commands.profile_cmd as cmd
    import dev_setup.snapshot as snap

    for module in (cmd, snap):
        assert "subprocess" not in vars(module), module.__name__
