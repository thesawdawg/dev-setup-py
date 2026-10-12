"""Catalog script bodies exercised against the real bash.

These run the remove_script the bundled catalog actually ships, with shim
`apt-get`/`sudo` binaries on PATH — the script's own output and the shims'
side effects are the assertions, not a reimplementation of the logic.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

from dev_setup import registry


@pytest.fixture()
def shim_dir(tmp_path, monkeypatch):
    """A bin dir of fake system tools, first on PATH.

    `apt-get` echoes whatever the test put in `apt-get.out`; `sudo` appends
    its argv to `sudo.log` — so a sudo that should never happen is proven by
    the file's absence, not by the script's exit code alone.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    apt_get = bin_dir / "apt-get"
    apt_get.write_text('#!/bin/sh\ncat "$APT_GET_OUT"\n')
    sudo = bin_dir / "sudo"
    sudo.write_text('#!/bin/sh\necho "$@" >> "$SUDO_LOG"\n')
    for f in (apt_get, sudo):
        f.chmod(f.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    monkeypatch.setenv("APT_GET_OUT", str(tmp_path / "apt-get.out"))
    monkeypatch.setenv("SUDO_LOG", str(tmp_path / "sudo.log"))
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return tmp_path


def _remove_script() -> str:
    registry.init()
    tool = registry.get("bubblewrap")
    assert tool is not None and tool.remove_script
    return tool.remove_script


def _run(script: str, tmp_path: Path) -> subprocess.CompletedProcess:
    script_path = tmp_path / "remove.sh"
    script_path.write_text(script)
    return subprocess.run(["bash", str(script_path)], capture_output=True, text=True)


def _check_cmd() -> str:
    registry.init()
    tool = registry.get("bubblewrap")
    assert tool is not None and tool.check_cmd
    return tool.check_cmd


def _run_check(executables: tuple[str, ...], tmp_path: Path) -> subprocess.CompletedProcess:
    """Run the real check_cmd with PATH limited to the shim dir.

    Absolute bash path on argv — PATH can't resolve it; `command -v` is a
    builtin, so the shims alone decide what it finds.
    """
    bin_dir = tmp_path / "check-bin"
    bin_dir.mkdir()
    for name in executables:
        f = bin_dir / name
        f.write_text("#!/bin/sh\n")
        f.chmod(f.stat().st_mode | stat.S_IXUSR)
    env = {"PATH": str(bin_dir)}
    return subprocess.run(
        ["/bin/bash", "-c", _check_cmd()], env=env, capture_output=True, text=True
    )


def test_check_cmd_requires_socat_not_just_bwrap(tmp_path):
    # flatpak Depends: bubblewrap, and the homebrew entry installs it — a bare
    # `check_cmd: bwrap` would read as installed while socat never arrives.
    result = _run_check(("bwrap",), tmp_path)
    assert result.returncode != 0


def test_check_cmd_passes_with_both_binaries(tmp_path):
    result = _run_check(("bwrap", "socat"), tmp_path)
    assert result.returncode == 0


def test_guard_refuses_when_dependents_would_be_removed(shim_dir):
    (shim_dir / "apt-get.out").write_text(
        "Remv bubblewrap [0.11.1]\nRemv socat [1.8.0]\nRemv flatpak [1.16.1-1]\n"
    )
    result = _run(_remove_script(), shim_dir)
    assert result.returncode == 1
    assert "flatpak" in result.stderr
    # The refusal happens before any sudo — the shim was never invoked.
    assert not (shim_dir / "sudo.log").exists()


def test_guard_removes_when_only_the_packages_themselves_go(shim_dir):
    (shim_dir / "apt-get.out").write_text(
        "Remv bubblewrap [0.11.1]\nRemv socat [1.8.0]\n"
    )
    result = _run(_remove_script(), shim_dir)
    assert result.returncode == 0, result.stderr
    sudo_log = shim_dir / "sudo.log"
    assert sudo_log.read_text().strip() == "apt-get remove -y bubblewrap socat"
