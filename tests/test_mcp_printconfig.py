"""`devstuff mcp --print-config TARGET` (docs/specs/mcp, FR-31, G-1, F-16).

The snippet is what a person pastes into a client, so it is held to two standards: it must be *true*
(the command it prints connects — `tests/integration/test_mcp_claude.py` runs it as printed) and it must
never be *dangerous* (the bearer token is the one secret on the page, and it is a path, never a value).
"""

from __future__ import annotations

import json
import os
import shlex
from pathlib import Path

import pytest
from click.testing import CliRunner

from dev_setup.cli import cli
from dev_setup.mcp import printconfig as pc

SECRET = "super-secret-token-value-0123456789abcdef"
LAUNCH = ("/opt/tools/devstuff",)


def spec(**kw) -> pc.Spec:
    base = dict(
        launcher=LAUNCH, http=False, host="127.0.0.1", port=8765,
        token_file=Path("/home/me/.local/state/devstuff/mcp-token"),
        allow_install=False, allow_functions=False,
    )
    base.update(kw)
    return pc.Spec(**base)


# -- the launcher: "the absolute path of the running devstuff" ------------------------------------


def test_a_console_script_is_used_by_its_absolute_path(tmp_path):
    script = tmp_path / "bin" / "devstuff"
    script.parent.mkdir()
    script.write_text("#!/bin/sh\n")
    script.chmod(0o755)
    assert pc.launcher(str(script), "/usr/bin/python3") == (str(script),)


def test_a_relative_console_script_is_made_absolute(tmp_path, monkeypatch):
    (tmp_path / "devstuff").write_text("#!/bin/sh\n")
    (tmp_path / "devstuff").chmod(0o755)
    monkeypatch.chdir(tmp_path)
    assert pc.launcher("./devstuff", "/usr/bin/python3") == (str(tmp_path / "devstuff"),)


def test_a_symlinked_console_script_keeps_the_symlink_path(tmp_path):
    """`~/.local/bin/devstuff` is the stable name; what it points into is a venv that gets rebuilt."""
    real = tmp_path / "venv" / "bin" / "devstuff"
    real.parent.mkdir(parents=True)
    real.write_text("#!/bin/sh\n")
    real.chmod(0o755)
    link = tmp_path / "bin" / "devstuff"
    link.parent.mkdir()
    link.symlink_to(real)
    assert pc.launcher(str(link), "/usr/bin/python3") == (str(link),)


def test_run_as_a_module_falls_back_to_this_interpreter(tmp_path):
    """`python -m dev_setup` and the `./devstuff` wrapper: argv[0] is `__main__.py`, so name the
    interpreter that is actually running this code instead of guessing a `devstuff` on PATH."""
    assert pc.launcher("/x/src/dev_setup/__main__.py", "/venv/bin/python") == (
        "/venv/bin/python", "-m", "dev_setup",
    )


def test_a_devstuff_that_does_not_exist_is_not_trusted(tmp_path):
    got = pc.launcher(str(tmp_path / "devstuff"), "/venv/bin/python")
    assert got == ("/venv/bin/python", "-m", "dev_setup")


def test_the_launcher_is_always_absolute():
    for argv0 in ("pytest", "-c", "", "/x/__main__.py"):
        first = pc.launcher(argv0, "/venv/bin/python")[0]
        assert os.path.isabs(first), argv0


# -- claude-code ---------------------------------------------------------------------------------


def test_claude_code_stdio_is_the_add_command_with_the_absolute_path():
    out = pc.render("claude-code", spec())
    assert out.text.strip() == "claude mcp add devstuff -- /opt/tools/devstuff mcp"


def test_claude_code_stdio_carries_the_gate_flags_that_were_given():
    both = pc.render("claude-code", spec(allow_install=True, allow_functions=True)).text.strip()
    assert both == "claude mcp add devstuff -- /opt/tools/devstuff mcp --allow-install --allow-functions"
    only = pc.render("claude-code", spec(allow_functions=True)).text.strip()
    assert only.endswith("mcp --allow-functions") and "--allow-install" not in only


def test_a_launcher_path_with_a_space_survives_the_shell():
    out = pc.render("claude-code", spec(launcher=("/opt/my tools/devstuff",))).text.strip()
    words = ["claude", "mcp", "add", "devstuff", "--", "/opt/my tools/devstuff", "mcp"]
    assert shlex.split(out) == words


def test_a_module_launcher_is_four_words_after_the_dashes():
    out = pc.render("claude-code", spec(launcher=("/v/bin/python", "-m", "dev_setup"))).text.strip()
    assert shlex.split(out)[5:] == ["/v/bin/python", "-m", "dev_setup", "mcp"]


def test_claude_code_http_uses_the_header_form_and_reads_the_token_from_its_file():
    out = pc.render("claude-code", spec(http=True)).text.strip()
    assert out == (
        "claude mcp add --transport http devstuff http://127.0.0.1:8765/mcp "
        '--header "Authorization: Bearer $(cat /home/me/.local/state/devstuff/mcp-token)"'
    )


def test_the_http_command_does_not_repeat_server_side_flags():
    """`--allow-install` is a decision of the server's launcher; the client cannot turn it on."""
    out = pc.render("claude-code", spec(http=True, allow_install=True, allow_functions=True)).text
    assert "--allow" not in out


def test_a_token_path_with_a_space_is_quoted_inside_the_substitution():
    out = pc.render("claude-code", spec(http=True, token_file=Path("/home/me/my state/token"))).text
    assert "$(cat '/home/me/my state/token')" in out


def test_a_wildcard_listen_address_is_not_a_place_a_client_can_connect_to():
    for host in ("0.0.0.0", "::", ""):
        out = pc.render("claude-code", spec(http=True, host=host))
        assert "http://127.0.0.1:8765/mcp" in out.text, host
        assert any("listening on every address" in n for n in out.notes), host


def test_an_ipv6_literal_host_is_bracketed_in_the_url():
    assert "http://[::1]:8765/mcp" in pc.render("claude-code", spec(http=True, host="::1")).text


def test_a_named_host_is_used_as_given():
    out = pc.render("claude-code", spec(http=True, host="box.lan", port=9000))
    assert "http://box.lan:9000/mcp" in out.text


def test_port_zero_cannot_be_printed_because_nobody_knows_the_port_yet():
    with pytest.raises(pc.ConfigError, match="port"):
        pc.render("claude-code", spec(http=True, port=0))


# -- claude-desktop ------------------------------------------------------------------------------


def test_claude_desktop_is_the_mcpservers_json_for_the_config_file():
    data = json.loads(pc.render("claude-desktop", spec(allow_install=True)).text)
    assert data == {"mcpServers": {"devstuff": {
        "command": "/opt/tools/devstuff", "args": ["mcp", "--allow-install"],
    }}}


def test_claude_desktop_splits_a_module_launcher_into_command_and_args():
    data = json.loads(pc.render("claude-desktop", spec(launcher=("/v/bin/python", "-m", "dev_setup"))).text)
    entry = data["mcpServers"]["devstuff"]
    assert entry == {"command": "/v/bin/python", "args": ["-m", "dev_setup", "mcp"]}


def test_claude_desktop_has_no_http_form():
    """Desktop launches a command and talks to it over stdio; there is no honest snippet to print."""
    with pytest.raises(pc.ConfigError, match="stdio"):
        pc.render("claude-desktop", spec(http=True))


# -- openapi -------------------------------------------------------------------------------------


def test_openapi_names_the_base_url_the_document_url_and_the_header():
    out = pc.render("openapi", spec(http=True)).text
    assert "http://127.0.0.1:8765" in out
    assert "http://127.0.0.1:8765/openapi.json" in out
    assert "Authorization: Bearer" in out
    assert "/home/me/.local/state/devstuff/mcp-token" in out


def test_openapi_needs_the_http_server():
    with pytest.raises(pc.ConfigError, match="--http"):
        pc.render("openapi", spec())


def test_an_unknown_target_is_refused():
    with pytest.raises(pc.ConfigError, match="target"):
        pc.render("emacs", spec())


# -- the token never appears ---------------------------------------------------------------------


@pytest.mark.parametrize("target", ["claude-code", "openapi"])
def test_the_token_value_is_not_in_any_snippet(tmp_path, target):
    path = tmp_path / "mcp-token"
    path.write_text(SECRET + "\n")
    path.chmod(0o600)
    out = pc.render(target, spec(http=True, token_file=path))
    assert SECRET not in out.text and not any(SECRET in n for n in out.notes)


# -- the command ---------------------------------------------------------------------------------


def run(*args, env=None):
    return CliRunner().invoke(cli, ["mcp", *args], env=env, catch_exceptions=False)


@pytest.fixture
def tripwire(monkeypatch):
    """`--print-config` starts nothing: no detach, no socket, no token file."""
    from dev_setup.mcp import http, process, stdio, token

    def boom(name):
        def f(*a, **k):
            raise AssertionError(f"--print-config must not call {name}")
        return f

    monkeypatch.setattr(process, "detach", boom("detach"))
    monkeypatch.setattr(http, "make_server", boom("make_server"))
    monkeypatch.setattr(stdio, "run", boom("stdio.run"))
    monkeypatch.setattr(stdio, "isolate_stdio", boom("isolate_stdio"))
    monkeypatch.setattr(token, "load", boom("token.load"))


def test_the_command_prints_only_the_snippet_on_stdout(tripwire):
    result = run("--print-config", "claude-code")
    assert result.exit_code == 0
    assert result.stdout.strip().startswith("claude mcp add devstuff -- ")
    assert result.stdout.strip().endswith(" mcp")
    assert result.stdout.count("\n") == 1  # redirectable: one command, nothing else


def test_the_command_json_target_is_parseable_as_printed(tripwire):
    result = run("--print-config", "claude-desktop", "--allow-functions")
    entry = json.loads(result.stdout)["mcpServers"]["devstuff"]
    assert os.path.isabs(entry["command"])  # under pytest this is the interpreter: `-m dev_setup mcp …`
    assert entry["args"][-2:] == ["mcp", "--allow-functions"]


def test_the_command_reads_http_flags_into_the_snippet(tripwire, tmp_path):
    tok = tmp_path / "tok"
    result = run("--http", "--port", "9123", "--token-file", str(tok), "--print-config", "claude-code")
    assert result.exit_code == 0
    assert "http://127.0.0.1:9123/mcp" in result.stdout and f"cat {tok}" in result.stdout


def test_the_default_token_path_follows_xdg_state_home(tripwire, tmp_path):
    result = run("--http", "--print-config", "openapi", env={"XDG_STATE_HOME": str(tmp_path / "xdg")})
    assert str(tmp_path / "xdg" / "devstuff" / "mcp-token") in result.stdout


def test_a_relative_token_file_is_printed_absolute(tripwire, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = run("--http", "--token-file", "rel-token", "--print-config", "openapi")
    assert str(tmp_path / "rel-token") in result.stdout


def test_a_missing_token_file_is_reported_and_not_created(tripwire, tmp_path):
    tok = tmp_path / "tok"
    result = run("--http", "--token-file", str(tok), "--print-config", "claude-code")
    assert result.exit_code == 0 and not tok.exists()
    assert "does not exist yet" in result.stderr  # the note; stdout is still just the command
    assert "does not exist" not in result.stdout


def test_an_existing_token_file_is_not_echoed_to_either_stream(tripwire, tmp_path):
    tok = tmp_path / "tok"
    tok.write_text(SECRET + "\n")
    tok.chmod(0o600)
    result = run("--http", "--token-file", str(tok), "--print-config", "claude-code")
    assert SECRET not in result.stdout and SECRET not in result.stderr
    assert "does not exist" not in result.stderr


def test_a_server_side_note_says_how_to_start_the_server(tripwire):
    result = run("--http", "--allow-install", "--print-config", "claude-code")
    assert "mcp --http" in result.stderr and "--allow-install" in result.stderr


@pytest.mark.parametrize(("args", "fragment"), [
    (["--print-config", "emacs"], "emacs"),                                # click.Choice
    (["--print-config", "openapi"], "--http"),                             # needs the HTTP server
    (["--http", "--print-config", "claude-desktop"], "stdio"),
    (["--http", "--port", "0", "--print-config", "claude-code"], "port"),
    (["--port", "9000", "--print-config", "claude-code"], "only apply with --http"),
    (["--list-tools", "--print-config", "claude-code"], "--list-tools"),
])
def test_a_snippet_that_cannot_be_honest_is_a_usage_error(tripwire, args, fragment):
    result = run(*args)
    assert result.exit_code == 2, result.output
    assert fragment in result.stderr


def test_a_bad_host_name_is_still_refused_before_printing(tripwire):
    assert run("--http", "--allow-host", "a/b", "--print-config", "openapi").exit_code == 2
