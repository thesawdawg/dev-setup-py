"""FR-11a: the server has no controlling terminal (docs/specs/mcp, F-11, SD-12).

Two layers. The decision logic (`setsid`, else fork-and-supervise, then verify and fail closed) is
tested with injected fakes, because the refusal path cannot otherwise be reached on a machine where
detaching works. The real thing — a process that was a group leader, under a real controlling
terminal — is tested with real processes and a pty, because a fake `setsid` proves nothing about
what the kernel does.
"""

from __future__ import annotations

import errno
import json
import os
import pty
import select
import shutil
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from dev_setup.mcp import process

# -- decision logic, with fakes ---------------------------------------------------------------


def eperm(*_a):
    raise PermissionError(errno.EPERM, "Operation not permitted")


def test_setsid_succeeding_means_no_fork():
    forks = []
    assert process.detach(setsid=lambda: None, fork=lambda: forks.append(1)) == "setsid"
    assert forks == []


def test_eperm_forks_and_the_child_becomes_the_server():
    calls = []

    def setsid():
        calls.append("setsid")
        if len(calls) == 1:
            raise PermissionError(errno.EPERM, "x")  # the parent is a process-group leader

    assert process.detach(setsid=setsid, fork=lambda: 0) == "forked"
    assert calls == ["setsid", "setsid"]  # the child calls it again, as a non-leader


def test_eperm_in_the_parent_hands_the_child_pid_to_the_supervisor():
    supervised = []
    out = process.detach(setsid=eperm, fork=lambda: 4242, supervise=supervised.append)
    assert supervised == [4242]
    assert out == "supervisor"  # only reachable with an injected supervisor; the real one never returns


def test_any_other_setsid_failure_is_not_swallowed():
    def broken():
        raise OSError(errno.ENOMEM, "no memory")

    with pytest.raises(OSError, match="no memory"):
        process.detach(setsid=broken, fork=lambda: pytest.fail("must not fork"))


def test_a_reachable_terminal_is_detected():
    assert process.terminal_reachable(open_=lambda *a: os.open(os.devnull, os.O_RDONLY)) is True


@pytest.mark.parametrize("err", [errno.ENXIO, errno.ENOENT, errno.EACCES])
def test_no_terminal_whatever_the_reason_the_open_failed(err):
    def refuse(*_a):
        raise OSError(err, "no")

    assert process.terminal_reachable(open_=refuse) is False


def test_a_reachable_terminal_with_a_gate_flag_refuses_to_start():
    with pytest.raises(process.TerminalReachable, match="terminal"):
        process.require_no_terminal(gated=True, warn=lambda m: None, reachable=lambda: True)


def test_a_reachable_terminal_without_a_gate_flag_warns_and_goes_on():
    warned: list[str] = []
    process.require_no_terminal(gated=False, warn=warned.append, reachable=lambda: True)
    assert warned and "terminal" in warned[0]


@pytest.mark.parametrize("gated", [True, False])
def test_no_reachable_terminal_is_silent(gated):
    warned: list[str] = []
    process.require_no_terminal(gated=gated, warn=warned.append, reachable=lambda: False)
    assert warned == []


# -- the real thing -----------------------------------------------------------------------------

REPO = Path(__file__).resolve().parent.parent


def _script(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "probe.py"
    path.write_text(
        "import json, os, signal, sys, time\n"
        "from dev_setup.mcp import process\n" + textwrap.dedent(body)
    )
    return path


def _env():
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO / "src") + os.pathsep + env.get("PYTHONPATH", "")
    return env


REPORT = """
out = sys.argv[1]
how = process.detach()
rep = {"how": how, "pid": os.getpid(), "sid": os.getsid(0), "ppid": os.getppid(),
       "tty_reachable": process.terminal_reachable()}
open(out, "w").write(json.dumps(rep))
"""


def test_a_plain_child_loses_its_terminal_via_setsid(tmp_path):
    out = tmp_path / "rep.json"
    # Not a group leader (it shares pytest's group), so setsid succeeds, as when a client spawns it.
    subprocess.run([sys.executable, str(_script(tmp_path, REPORT)), str(out)], env=_env(), check=True,
                   stdin=subprocess.DEVNULL, timeout=30)
    rep = json.loads(out.read_text())
    assert rep["how"] == "setsid"
    assert rep["sid"] == rep["pid"]  # its own session
    assert rep["tty_reachable"] is False


def _under_pty(argv: list[str], timeout: float = 30) -> int:
    """Run argv as the child of a pty: a session leader *with a controlling terminal*, which is
    exactly what an interactive shell hands `devstuff mcp --http`."""
    pid, fd = pty.fork()
    if pid == 0:
        os.execvpe(argv[0], argv, _env())
    deadline = time.monotonic() + timeout
    open_ = True
    while time.monotonic() < deadline:
        # Drain the master so the child never blocks on a full pty buffer; EIO means it has gone.
        if open_ and select.select([fd], [], [], 0.1)[0]:
            try:
                open_ = bool(os.read(fd, 4096))
            except OSError:
                open_ = False
        done, status = os.waitpid(pid, os.WNOHANG)
        if done:
            os.close(fd)
            return os.waitstatus_to_exitcode(status)
        if not open_:
            time.sleep(0.05)
    os.kill(pid, signal.SIGKILL)
    raise AssertionError("timed out")


def test_a_group_leader_under_a_real_terminal_forks_and_the_server_has_no_terminal(tmp_path):
    out = tmp_path / "rep.json"
    script = _script(tmp_path, REPORT + "\nos._exit(0)\n")
    assert _under_pty([sys.executable, str(script), str(out)]) == 0
    rep = json.loads(out.read_text())
    assert rep["how"] == "forked"  # setsid raised EPERM in the leader, so it forked
    assert rep["sid"] == rep["pid"] and rep["ppid"] != rep["sid"]
    assert rep["tty_reachable"] is False


def test_the_terminal_really_is_reachable_without_the_detach(tmp_path):
    """The control: the same probe under the same pty, with no detach, can open /dev/tty — so the
    test above is shown to be able to fail."""
    out = tmp_path / "rep.json"
    script = tmp_path / "ctl.py"
    script.write_text(
        "import json, sys\nfrom dev_setup.mcp import process\n"
        f"open({str(out)!r}, 'w').write(json.dumps(process.terminal_reachable()))\n"
    )
    assert _under_pty([sys.executable, str(script)]) == 0
    assert json.loads(out.read_text()) is True


def _passwordless_sudo() -> bool:
    """A CI runner usually has it: then `sudo` never asks, and there is no prompt to show gone."""
    if shutil.which("sudo") is None:
        return True
    probe = subprocess.run(["sudo", "-n", "true"], capture_output=True, stdin=subprocess.DEVNULL)
    return probe.returncode == 0


@pytest.mark.skipif(_passwordless_sudo(), reason="needs sudo that asks for a password")
def test_sudo_fails_fast_in_the_detached_server_instead_of_prompting(tmp_path):
    out = tmp_path / "rep.json"
    body = """
    out = sys.argv[1]
    process.detach()
    import subprocess
    subprocess.run(["sudo", "-k"], stdin=subprocess.DEVNULL)
    r = subprocess.run(["sudo", "true"], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=20)
    open(out, "w").write(json.dumps({"rc": r.returncode, "err": r.stderr}))
    os._exit(0)
    """
    assert _under_pty([sys.executable, str(_script(tmp_path, body)), str(out)]) == 0
    rep = json.loads(out.read_text())
    assert rep["rc"] != 0
    assert "terminal is required" in rep["err"]  # F-11: exits at once, never prompts on the terminal


SERVER = """
out = sys.argv[1]
how = process.detach()
def on_term(signum, frame):
    open(out, "a").write(f"got {signal.Signals(signum).name}\\n")
    os._exit(7)
signal.signal(signal.SIGTERM, on_term)
signal.signal(signal.SIGINT, on_term)
open(out, "w").write(f"ready {how}\\n")
time.sleep(30)
"""


def _wait_for(path: Path, text: str, timeout=15.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if path.exists() and text in path.read_text():
            return True
        time.sleep(0.05)
    return False


@pytest.mark.parametrize(("sig", "seen"), [
    (signal.SIGTERM, "got SIGTERM"),
    (signal.SIGINT, "got SIGINT"),
    (signal.SIGHUP, "got SIGTERM"),  # a closed terminal stops the server, it does not orphan it
])
def test_the_supervisor_forwards_signals_and_mirrors_the_exit_status(tmp_path, sig, seen):
    out = tmp_path / "rep.txt"
    # start_new_session makes the child a group leader, so setsid raises EPERM and it must fork.
    proc = subprocess.Popen([sys.executable, str(_script(tmp_path, SERVER)), str(out)], env=_env(),
                            start_new_session=True, stdin=subprocess.DEVNULL)
    try:
        assert _wait_for(out, "ready forked")
        proc.send_signal(sig)
        assert proc.wait(timeout=15) == 7  # the server's own status, through the supervisor
        assert seen in out.read_text()
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_a_server_killed_by_a_signal_reads_as_128_plus_it_to_the_caller(tmp_path):
    body = """
    process.detach()
    os.kill(os.getpid(), signal.SIGKILL)
    """
    proc = subprocess.run([sys.executable, str(_script(tmp_path, body))], env=_env(), timeout=30,
                          start_new_session=True, stdin=subprocess.DEVNULL)
    assert proc.returncode == 128 + signal.SIGKILL
