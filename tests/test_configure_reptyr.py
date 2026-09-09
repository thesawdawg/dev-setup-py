from unittest.mock import Mock

import pytest
from click.testing import CliRunner

from dev_setup import configure
from dev_setup.commands.configure_cmd import configure_cmd, offer_after_install
from dev_setup.configure.reptyr import wizard


@pytest.fixture
def flow(monkeypatch, tmp_path):
    scope = tmp_path / "ptrace_scope"
    scope.write_text("1\n")
    monkeypatch.setattr(wizard, "SCOPE_PATH", scope)
    monkeypatch.setattr(wizard, "SYSTEM_PATH", tmp_path / "99-devstuff-reptyr.conf")
    runner = Mock()
    monkeypatch.setattr(wizard, "_run", runner)
    monkeypatch.setattr(wizard.ui, "confirm", lambda *a, **kw: True)
    return scope, runner


def answers(monkeypatch, *values):
    iterator = iter(values)
    monkeypatch.setattr(wizard.ui, "select", lambda *a, **kw: next(iterator))


def test_registered_and_path(flow):
    assert configure.get("reptyr").load() is wizard
    result = CliRunner().invoke(configure_cmd, ["reptyr", "--path"])
    assert result.exit_code == 0
    assert str(wizard.SYSTEM_PATH) in result.output


@pytest.mark.parametrize("value", ["0", "1", "2", "3", "broken", "4"])
def test_detection(flow, value):
    path, _ = flow
    path.write_text(value)
    assert wizard.read_scope() == (int(value) if value in "0123" else None)


@pytest.mark.parametrize("state", ["missing", "3"])
def test_unavailable_or_locked_cannot_mutate(flow, monkeypatch, state):
    path, runner = flow
    if state == "missing":
        path.unlink()
    else:
        path.write_text(state)
    answers(monkeypatch)  # no prompts should be needed
    assert wizard.run() is None
    runner.assert_not_called()


@pytest.mark.parametrize("selection", [None, "keep"])
def test_cancel_policy(flow, monkeypatch, selection):
    answers(monkeypatch, selection)
    assert wizard.run() is None
    flow[1].assert_not_called()


def test_declined_confirmation_writes_nothing(flow, monkeypatch, tmp_path):
    answers(monkeypatch, "0")
    monkeypatch.setattr(wizard.ui, "confirm", lambda *a, **kw: False)
    target = tmp_path / "export.conf"
    assert wizard.run(target=target) is None
    assert not target.exists()
    flow[1].assert_not_called()


@pytest.mark.parametrize("value", [0, 1, 2])
def test_offline_export_never_uses_sudo(flow, monkeypatch, tmp_path, value):
    flow[0].unlink()
    answers(monkeypatch, str(value))
    target = tmp_path / "export.conf"
    assert wizard.run(target=target) == value
    assert f"kernel.yama.ptrace_scope = {value}\n" in target.read_text()
    flow[1].assert_not_called()


def test_temporary_change_verified_without_file(flow, monkeypatch):
    path, runner = flow
    runner.side_effect = lambda cmd: path.write_text("0")
    answers(monkeypatch, "0", "temporary")
    assert wizard.run() == 0
    runner.assert_called_once_with(["sudo", "sysctl", "-w", "kernel.yama.ptrace_scope=0"])
    assert not wizard.SYSTEM_PATH.exists()


def test_persistent_backup_and_load_only_own_file(flow, monkeypatch):
    path, runner = flow

    def execute(cmd):
        if cmd[1] == "install":
            from pathlib import Path
            assert "--backup=numbered" in cmd
            assert "kernel.yama.ptrace_scope = 0" in Path(cmd[-2]).read_text()
        else:
            assert cmd == ["sudo", "sysctl", "--load", str(wizard.SYSTEM_PATH)]
            path.write_text("0")

    runner.side_effect = execute
    answers(monkeypatch, "0", "persistent")
    assert wizard.run() == 0
    assert runner.call_count == 2


@pytest.mark.parametrize("failure", [RuntimeError("denied"), FileNotFoundError("sudo")])
def test_failed_apply_never_reports_success(flow, monkeypatch, failure):
    flow[1].side_effect = failure
    success = Mock()
    monkeypatch.setattr(wizard.ui, "success", success)
    answers(monkeypatch, "0", "temporary")
    assert wizard.run() is None
    success.assert_not_called()


def test_saved_but_not_applied_is_reported(flow, monkeypatch):
    flow[1].side_effect = [None, RuntimeError("read-only sysctl")]
    warning = Mock()
    monkeypatch.setattr(wizard.ui, "warn", warning)
    answers(monkeypatch, "0", "persistent")
    assert wizard.run() is None
    assert "was saved, but the live change failed" in warning.call_args.args[0]


def test_readback_mismatch_is_failure(flow, monkeypatch):
    answers(monkeypatch, "0", "temporary")
    assert wizard.run() is None


def test_mode_three_cannot_be_generated():
    with pytest.raises(ValueError):
        wizard.render(3)


def test_post_install_offer_runs_wizard(monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr(wizard.ui, "confirm", lambda *a, **kw: True)
    run = Mock()
    monkeypatch.setattr(wizard, "run", run)
    offer_after_install("reptyr")
    run.assert_called_once_with()


def test_noninteractive_install_does_not_change_permissions(monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    run = Mock()
    monkeypatch.setattr(wizard, "run", run)
    offer_after_install("reptyr")
    run.assert_not_called()
