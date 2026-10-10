"""Checkers set `UpdateStatus.note` only where they actually know why (FR-23)."""

from __future__ import annotations

import subprocess

from dev_setup import generic
from dev_setup.generic import GenericTool, UpdateStatus


def test_status_note_defaults_to_empty_so_existing_call_sites_are_unchanged():
    assert UpdateStatus().note == ""
    assert UpdateStatus(current="1", latest="2", available=True) == UpdateStatus(
        current="1", latest="2", available=True, note=""
    )


def test_npm_registry_failure_is_explained(monkeypatch):
    monkeypatch.setattr(generic, "_npm_installed_version", lambda _p: "1.0.0")
    monkeypatch.setattr(generic, "_npm_latest_version", lambda _p: "")
    tool = GenericTool(key="x", name="X", install_type="npm", npm_name="x")

    status = tool.check_for_update()

    assert status.available is None and status.current == "1.0.0"
    assert "npm" in status.note


def test_npm_working_probe_has_no_note(monkeypatch):
    monkeypatch.setattr(generic, "_npm_installed_version", lambda _p: "1.0.0")
    monkeypatch.setattr(generic, "_npm_latest_version", lambda _p: "1.1.0")
    tool = GenericTool(key="x", name="X", install_type="npm", npm_name="x")

    assert tool.check_for_update().note == ""


def test_git_unreachable_remote_is_explained(monkeypatch, tmp_path):
    monkeypatch.setattr(generic, "_git_clone_dest", lambda _u: tmp_path)

    def fake_probe(cmd, **_kw):
        if "rev-parse" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout="abc1234\n", stderr="")
        return subprocess.CompletedProcess(cmd, 128, stdout="", stderr="fatal")  # ls-remote

    monkeypatch.setattr(generic, "_probe", fake_probe)
    tool = GenericTool(key="x", name="X", install_type="git", git_url="https://example.com/x.git")

    status = tool.check_for_update()

    assert status.available is None and status.current == "abc1234"
    assert "remote" in status.note


def test_git_missing_clone_is_explained(monkeypatch, tmp_path):
    monkeypatch.setattr(generic, "_git_clone_dest", lambda _u: tmp_path / "nope")
    tool = GenericTool(key="x", name="X", install_type="git", git_url="https://example.com/x.git")

    status = tool.check_for_update()

    assert status.available is None
    assert "clone" in status.note


def test_apt_without_a_candidate_is_explained(monkeypatch):
    def fake_probe(cmd, **_kw):
        if cmd[0] == "dpkg-query":
            return subprocess.CompletedProcess(cmd, 0, stdout="1.0-1", stderr="")
        out = "x:\n  Installed: 1.0-1\n  Candidate: (none)\n"
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")

    monkeypatch.setattr(generic, "_probe", fake_probe)
    tool = GenericTool(key="x", name="X", install_type="apt", apt_packages="x")

    status = tool.check_for_update()

    assert status.available is None and status.current == "1.0-1"
    assert "candidate" in status.note
