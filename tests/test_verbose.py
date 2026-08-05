from __future__ import annotations

from unittest import mock

import pytest

from dev_setup import function_runner as runner
from dev_setup import generic, verbose
from dev_setup.cli import cli
from dev_setup.functions_registry import FunctionDef, FunctionParam


@pytest.fixture(autouse=True)
def quiet_level():
    """The level is process-wide state; no test may leak it into the next."""
    verbose.set_level(verbose.QUIET)
    yield
    verbose.set_level(verbose.QUIET)


# -- level ----------------------------------------------------------------------


def test_set_level_clamps_to_the_three_levels():
    verbose.set_level(9)
    assert verbose.level() == verbose.TRACE
    verbose.set_level(-3)
    assert verbose.level() == verbose.QUIET


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", verbose.QUIET),
        ("0", verbose.QUIET),
        ("1", verbose.VERBOSE),
        ("2", verbose.TRACE),
        ("7", verbose.TRACE),
        ("true", verbose.VERBOSE),
        ("nonsense", verbose.QUIET),
    ],
)
def test_env_var_levels(monkeypatch, raw, expected):
    monkeypatch.setenv("DEVSTUFF_VERBOSE", raw)
    assert verbose._from_env() == expected


def test_enabled_defaults_to_asking_about_level_one():
    verbose.set_level(verbose.VERBOSE)
    assert verbose.enabled()
    assert not verbose.enabled(verbose.TRACE)


# -- output routing -------------------------------------------------------------


def test_everything_logs_to_stderr_never_stdout(capsys):
    """`devstuff run` in eval mode puts shell code on stdout for the caller to eval —
    a verbose line landing there would be executed in the user's shell."""
    verbose.set_level(verbose.TRACE)
    verbose.log("a note")
    verbose.trace("a trace")
    verbose.command(["echo", "hi there"])
    verbose.result(1, "some output")
    verbose.block("a body")

    captured = capsys.readouterr()
    assert captured.out == ""
    for fragment in ("a note", "a trace", "echo", "some output", "a body"):
        assert fragment in captured.err


def test_command_is_quoted_so_it_can_be_pasted_back(capsys):
    verbose.set_level(verbose.VERBOSE)
    verbose.command(["bash", "-lc", "echo one two"], cwd="/tmp")
    err = capsys.readouterr().err
    assert "$ bash -lc 'echo one two'" in err
    assert "/tmp" in err


def test_nothing_is_logged_when_quiet(capsys):
    verbose.log("note")
    verbose.trace("trace")
    verbose.command(["echo"])
    verbose.block("body")
    assert capsys.readouterr() == ("", "")


def test_trace_level_messages_are_silent_at_v(capsys):
    verbose.set_level(verbose.VERBOSE)
    verbose.log("shown")
    verbose.trace("hidden")
    verbose.block("also hidden")
    err = capsys.readouterr().err
    assert "shown" in err
    assert "hidden" not in err


# -- step -----------------------------------------------------------------------


def test_step_swaps_the_spinner_for_a_logged_line_when_verbose(capsys):
    """A spinner repaints its own line, so it can't share a terminal with streamed
    subprocess output."""
    with mock.patch("dev_setup.ui.spinner") as spinner:
        with verbose.step("Working..."):
            pass
        assert spinner.called

    verbose.set_level(verbose.VERBOSE)
    with mock.patch("dev_setup.ui.spinner") as spinner:
        with verbose.step("Working..."):
            pass
        assert not spinner.called
    assert "Working..." in capsys.readouterr().err


# -- generic._run / _probe ------------------------------------------------------


def test_run_captures_and_raises_with_stderr_when_quiet():
    with pytest.raises(RuntimeError, match="boom"):
        generic._run(["bash", "-c", "echo boom >&2; exit 3"])


def test_run_reports_a_bare_exit_code_when_verbose():
    """The output already streamed past — the exception shouldn't repeat the argv."""
    verbose.set_level(verbose.VERBOSE)
    with pytest.raises(RuntimeError, match="^exit code 3$"):
        generic._run(["bash", "-c", "exit 3"])


def test_run_streams_instead_of_capturing_when_verbose():
    verbose.set_level(verbose.VERBOSE)
    with mock.patch("subprocess.run") as run:
        generic._run(["echo", "hi"])
    _, kwargs = run.call_args
    assert not kwargs.get("capture_output")


def test_run_logs_the_command_at_v(capsys):
    verbose.set_level(verbose.VERBOSE)
    generic._run(["true"])
    assert "$ true" in capsys.readouterr().err


def test_probe_is_silent_at_v_and_reports_exit_and_output_at_vv(capsys):
    verbose.set_level(verbose.VERBOSE)
    generic._probe(["bash", "-c", "echo out; exit 2"], text=True)
    assert capsys.readouterr().err == ""

    verbose.set_level(verbose.TRACE)
    proc = generic._probe(["bash", "-c", "echo out; exit 2"], text=True)
    err = capsys.readouterr().err
    assert proc.returncode == 2
    assert "exit 2" in err
    assert "out" in err


def test_probe_marked_as_an_action_logs_at_v(capsys):
    """The few best-effort *actions* that use _probe (apt update, a git remove
    command) are work the user should see at -v, not background chatter."""
    verbose.set_level(verbose.VERBOSE)
    generic._probe(["true"], log_at=verbose.VERBOSE)
    assert "$ true" in capsys.readouterr().err


def test_probe_never_raises_on_a_failing_command():
    verbose.set_level(verbose.TRACE)
    assert generic._probe(["bash", "-c", "exit 9"]).returncode == 9


# -- bash -x tracing ------------------------------------------------------------


def test_install_script_gets_bash_x_only_at_vv():
    with mock.patch.object(generic, "_run") as run:
        generic._run_bash_script("echo hi")
        assert "-x" not in run.call_args[0][0]

        verbose.set_level(verbose.TRACE)
        generic._run_bash_script("echo hi")
        assert run.call_args[0][0][:2] == ["bash", "-x"]


def _fn(**kwargs) -> FunctionDef:
    kwargs.setdefault("key", "demo")
    kwargs.setdefault("name", "Demo")
    kwargs.setdefault("description", "demo function")
    kwargs.setdefault("type", "script")
    kwargs.setdefault("script", "echo hello")
    kwargs.setdefault("params", [])
    return FunctionDef(**kwargs)


def test_function_gets_bash_x_only_at_vv():
    with mock.patch("subprocess.run") as run:
        runner.run_script_function(_fn(), ())
        assert "-x" not in run.call_args[0][0]

        verbose.set_level(verbose.TRACE)
        runner.run_script_function(_fn(), ())
        assert run.call_args[0][0][:2] == ["bash", "-x"]


def test_function_output_streams_to_the_terminal(capfd):
    """The point of -v on `devstuff run`: a function's own diagnostics reach the user."""
    verbose.set_level(verbose.TRACE)
    runner.run_script_function(_fn(script="echo from-the-function"), ())
    out = capfd.readouterr()
    assert "from-the-function" in out.out
    assert "+ echo from-the-function" in out.err  # the bash -x trace


def test_function_params_are_logged_at_vv(capsys):
    verbose.set_level(verbose.TRACE)
    fn = _fn(script="echo $target", params=[FunctionParam(name="target", required=True)])
    with mock.patch("subprocess.run"):
        runner.run_script_function(fn, ("a value",))
    assert "target='a value'" in capsys.readouterr().err


def test_eval_script_stays_alone_on_stdout_at_vv(capsys):
    """`eval "$(devstuff -vv run key)"` must still evaluate only the function."""
    verbose.set_level(verbose.TRACE)
    fn = _fn(type="shell-eval", register="eval", script="export FOO=bar")
    script = runner.render_eval_script(fn, ())

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "export FOO=bar" in captured.err  # logged, not printed
    assert script == "export FOO=bar"


def test_eval_script_is_never_traced_with_set_x():
    """It runs in the caller's interactive shell, where xtrace would persist."""
    verbose.set_level(verbose.TRACE)
    fn = _fn(type="shell-eval", register="eval", script="export FOO=bar")
    assert "set -x" not in runner.render_eval_script(fn, ())


# -- CLI wiring -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["list"], verbose.QUIET),
        (["-v", "list"], verbose.VERBOSE),
        (["-vv", "list"], verbose.TRACE),
        (["list", "-v"], verbose.VERBOSE),
        (["list", "-vv"], verbose.TRACE),
        (["-v", "list", "-v"], verbose.VERBOSE),
    ],
)
def test_cli_sets_the_level_before_and_after_the_subcommand(argv, expected):
    from click.testing import CliRunner

    seen = {}
    with mock.patch(
        "dev_setup.commands.list_cmd.list_cmd.callback",
        side_effect=lambda **kw: seen.setdefault("level", verbose.level()),
    ):
        result = CliRunner().invoke(cli, argv)
    assert result.exit_code == 0, result.output
    assert seen["level"] == expected
