"""tests for dev_setup.runner — the supervised child lifecycle.

Real (tiny) children, no installers, no sudo: the point is the pipe/drain/
termination semantics, which fakes can't exercise.
"""

from __future__ import annotations

import os
import time

import pytest

from dev_setup import runner

_ENV = dict(os.environ)


def _run_quiet(cmd, **kw):
    kw.setdefault("cwd", None)
    kw.setdefault("env", dict(_ENV))
    return runner.run(cmd, **kw)


def test_managed_run_returns_after_detached_grandchild_holds_pipe(monkeypatch):
    """The primary exits while a backgrounded child keeps the inherited pipe —
    EOF never comes; the run returns on the (shortened) drain window, before
    the orphan exits."""
    monkeypatch.setattr(runner, "_DRAIN_S", 0.3)
    chunks: list[str] = []
    start = time.monotonic()
    _run_quiet(["bash", "-c", "echo hi; sleep 3 &"], on_output=chunks.append)
    assert time.monotonic() - start < 3
    assert "hi" in "".join(chunks)


def test_managed_run_waits_for_primary_past_early_eof():
    """All pipe writers closed but the primary still runs: we poll until it
    exits rather than returning early."""
    start = time.monotonic()
    _run_quiet(["bash", "-c", "exec 1>&- 2>/dev/null; sleep 2"])
    elapsed = time.monotonic() - start
    assert 1.5 < elapsed < 10


def test_quiet_failure_raises_failed_with_bounded_tail():
    with pytest.raises(runner.Failed) as exc:
        _run_quiet(["bash", "-c", "echo out; echo err >&2; exit 3"])
    assert isinstance(exc.value, RuntimeError)
    assert exc.value.rc == 3
    assert "out" in exc.value.tail and "err" in exc.value.tail


def test_on_output_gets_decoded_text_across_cr_unicode_and_no_newline():
    chunks: list[str] = []
    _run_quiet(
        ["bash", "-c", "printf 'a\\rb\\ncaf\\xc3\\xa9'; sleep 0.1"],
        on_output=chunks.append,
    )
    text = "".join(chunks)
    assert "a\rb\ncafé" in text


def test_timeout_terminates_and_reports():
    start = time.monotonic()
    with pytest.raises(runner.TimedOut, match="timed out"):
        _run_quiet(["sleep", "30"], timeout=0.3)
    assert time.monotonic() - start < 15


def test_keyboardinterrupt_reaps_child_process_group(monkeypatch):
    """An interrupt (e.g. a Ctrl+C surfacing through on_output) signals the
    child's own process group — the fake child reaps within shortened grace."""
    monkeypatch.setattr(runner, "_TERMINATE_GRACE_S", 0.3)
    pid_box: list[int] = []

    # $$ is the bash pid; `exec sleep` keeps it, so it doubles as the pgid.
    # Interrupt on the first chunk — the only output the child ever makes.
    def record_then_interrupt(text: str) -> None:
        pid_box.append(int(text.strip()))
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        _run_quiet(["bash", "-c", "echo $$; exec sleep 30"], on_output=record_then_interrupt)

    pid = pid_box[0]
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        pytest.fail(f"child pid {pid} still alive after interrupt cleanup")


def test_on_draining_fires_once_after_primary_exit(monkeypatch):
    """UI hook: primary is gone but the pipe is still draining."""
    monkeypatch.setattr(runner, "_DRAIN_S", 0.2)
    calls: list[str] = []
    _run_quiet(
        ["bash", "-c", "echo hi; sleep 2 &"],
        on_draining=lambda: calls.append("draining"),
    )
    assert calls == ["draining"]


def test_machine_mode_flag_roundtrip():
    assert not runner.is_machine_mode()
    with runner.machine_mode():
        assert runner.is_machine_mode()
    assert not runner.is_machine_mode()


def test_passthrough_needs_env_tty_and_no_machine_mode(monkeypatch):
    monkeypatch.delenv("DEVSTUFF_TERMINAL_INPUT", raising=False)
    assert not runner.passthrough_allowed()
    monkeypatch.setenv("DEVSTUFF_TERMINAL_INPUT", "1")
    # stdin/stderr aren't real ttys under pytest
    assert not runner.passthrough_allowed()
    # With both tty flags faked, env alone allows it — machine mode forbids.
    monkeypatch.setattr(runner, "_isatty", lambda _f: True)
    assert runner.passthrough_allowed()
    with runner.machine_mode():
        assert not runner.passthrough_allowed()


def test_action_log_creates_private_file_with_output_not_argv(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.delenv("DEVSTUFF_TERMINAL_INPUT", raising=False)
    with runner.action_log("demo", "install") as log:
        assert log is not None
        _run_quiet(["bash", "-c", "echo visible-output"])
        path = log.path
    assert runner.last_log_path() == path
    text = path.read_text()
    assert "visible-output" in text
    assert "action=install tool=demo" in text
    assert "exit 0" in text
    # execution metadata, never the command line
    assert "bash -c" not in text
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o077 == 0


def test_suppress_log_output_keeps_metadata_but_drops_output(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.delenv("DEVSTUFF_TERMINAL_INPUT", raising=False)
    with runner.action_log("demo", "install") as log:
        with runner.suppress_log_output():
            _run_quiet(["bash", "-c", "echo a-secret-trace"])
        path = log.path
    text = path.read_text()
    assert "a-secret-trace" not in text
    assert "exit 0" in text


def test_action_log_cap_counts_bytes_and_stops_all_writes(monkeypatch, tmp_path):
    """Multibyte output counts as bytes; once capped, every later write —
    including metadata — is dropped and the file never exceeds the cap."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.delenv("DEVSTUFF_TERMINAL_INPUT", raising=False)
    monkeypatch.setattr(runner, "_LOG_MAX_BYTES", 200)
    with runner.action_log("demo", "install") as log:
        log.write("é" * 100)  # 200 bytes — fills and trips the cap
        log.write("more output")
        log.meta("exit 0")
        path = log.path
    data = path.read_bytes()
    assert len(data) <= 200
    assert b"truncated" in data
    assert b"more output" not in data
    assert b"exit 0" not in data


def test_run_log_path_falls_back_to_runs_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    # The last-action path is process-wide by design; with none recorded the
    # call sites get the runs dir itself.
    monkeypatch.setattr(runner, "_last_log_path", None)
    from dev_setup import generic

    assert generic.run_log_path() == tmp_path / "devstuff" / "runs"
