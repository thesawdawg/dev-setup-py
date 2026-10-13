"""Catalog script bodies exercised against the real bash.

These run the remove_script the bundled catalog actually ships, with shim
`apt-get`/`sudo` binaries on PATH — the script's own output and the shims'
side effects are the assertions, not a reimplementation of the logic.
"""

from __future__ import annotations

import os
import shutil
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


# -- fzf: the ~/.bashrc block and its remove --------------------------------
#
# The block has to work on fzf 0.44 (Ubuntu 24.04, no `fzf --bash`) and on
# >= 0.48, and must never make a shell's startup fail — it sits in ~/.bashrc.
# The fallback path in the shipped block is `_FZF_FALLBACK` below; tests point
# it at a tmp file because a real /usr/share/doc one may or may not exist.

_FZF_MARKER = "# fzf key bindings"
_FZF_FALLBACK = "/usr/share/doc/fzf/examples/key-bindings.bash"


def _fzf_scripts() -> tuple[str, str]:
    registry.init()
    tool = registry.get("fzf")
    assert tool is not None and tool.install_script and tool.remove_script
    assert _FZF_MARKER in tool.install_script and _FZF_FALLBACK in tool.install_script
    return tool.install_script, tool.remove_script


def _run_script(script: str, tmp_path: Path, name: str = "run.sh") -> subprocess.CompletedProcess:
    script_path = tmp_path / name
    script_path.write_text(script)
    return subprocess.run(["bash", str(script_path)], capture_output=True, text=True)


def _install_fzf(shim_dir: Path, monkeypatch) -> Path:
    """Run the shipped install_script against a tmp HOME; return the bashrc."""
    home = shim_dir / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    install_script, _ = _fzf_scripts()
    result = _run_script(install_script, shim_dir, "install.sh")
    assert result.returncode == 0, result.stderr
    return home / ".bashrc"


def test_fzf_install_appends_the_marked_block_exactly_once(shim_dir, monkeypatch):
    bashrc = _install_fzf(shim_dir, monkeypatch)
    # Second install run must not duplicate the block (grep -Fxq guard).
    install_script, _ = _fzf_scripts()
    second = _run_script(install_script, shim_dir, "install2.sh")
    assert second.returncode == 0, second.stderr
    text = bashrc.read_text()
    assert text.count(_FZF_MARKER) == 1
    assert "command -v fzf" in text and "key-bindings.bash" in text
    sudo_log = (shim_dir / "sudo.log").read_text()
    assert "apt-get install -y fzf" in sudo_log


def test_fzf_remove_strips_only_the_block(shim_dir, monkeypatch):
    bashrc = shim_dir / "home" / ".bashrc"
    (shim_dir / "home").mkdir()
    bashrc.write_text('export EDITOR=vim\nalias ll="ls -l"\n')
    _install_fzf(shim_dir, monkeypatch)  # HOME already points at shim_dir/home
    bashrc.write_text(bashrc.read_text() + 'export PAGER=less\n')

    _, remove_script = _fzf_scripts()
    result = _run_script(remove_script, shim_dir, "remove.sh")
    assert result.returncode == 0, result.stderr

    remaining = bashrc.read_text()
    assert _FZF_MARKER not in remaining
    assert "command -v fzf" not in remaining
    assert "key-bindings.bash" not in remaining
    assert remaining.splitlines() == ["export EDITOR=vim", 'alias ll="ls -l"', "", "export PAGER=less"]
    sudo_log = (shim_dir / "sudo.log").read_text()
    assert "apt-get remove -y fzf" in sudo_log


def _fzf_check_cmd() -> str:
    registry.init()
    tool = registry.get("fzf")
    assert tool is not None and tool.check_cmd
    return tool.check_cmd


def _run_fzf_check(fzf_on_path: bool, bashrc: str | None, tmp_path: Path) -> subprocess.CompletedProcess:
    """Run the real check_cmd with PATH limited to a shim dir and HOME to tmp.

    The check greps "$HOME/.bashrc" for the marker as well as probing `command
    -v fzf`, so both sides of it are controlled here.
    """
    bin_dir = tmp_path / "check-bin"
    bin_dir.mkdir()
    if fzf_on_path:
        _shim("fzf", "#!/bin/sh\n", bin_dir)
    # `command -v` is a builtin but `grep` is external: pass the real one through
    # so the check genuinely reads the bashrc while PATH stays shim-only.
    _shim("grep", f'#!/bin/sh\nexec "{shutil.which("grep")}" "$@"\n', bin_dir)
    home = tmp_path / "check-home"
    home.mkdir()
    if bashrc is not None:
        (home / ".bashrc").write_text(bashrc)
    env = {"PATH": str(bin_dir), "HOME": str(home)}
    return subprocess.run(
        ["/bin/bash", "-c", _fzf_check_cmd()], env=env, capture_output=True, text=True
    )


def test_fzf_check_needs_the_marker_not_just_the_binary(tmp_path):
    # An fzf installed by other means has no block — it must read as not
    # installed so `devstuff install fzf` adds the bindings.
    result = _run_fzf_check(True, "export EDITOR=vim\n", tmp_path)
    assert result.returncode != 0


def test_fzf_check_passes_with_binary_and_marker(tmp_path):
    result = _run_fzf_check(True, f"{_FZF_MARKER}\ncommand -v fzf\n", tmp_path)
    assert result.returncode == 0


def test_fzf_check_needs_the_binary_not_just_the_marker(tmp_path):
    result = _run_fzf_check(False, f"{_FZF_MARKER}\n", tmp_path)
    assert result.returncode != 0


def test_fzf_check_is_quiet_without_a_bashrc(tmp_path):
    result = _run_fzf_check(True, None, tmp_path)
    assert result.returncode != 0
    assert result.stderr == ""  # grep's "No such file" is swallowed by 2>/dev/null


# -- the block itself, sourced in a bare bash --------------------------------


def _written_bashrc(shim_dir: Path, monkeypatch, tmp_path: Path) -> Path:
    """The bashrc the install script wrote, with the fallback retargeted to tmp."""
    bashrc = _install_fzf(shim_dir, monkeypatch)
    text = bashrc.read_text()
    assert _FZF_FALLBACK in text
    bashrc.write_text(text.replace(_FZF_FALLBACK, str(tmp_path / "key-bindings.bash")))
    return bashrc


def _shim(name: str, body: str, bin_dir: Path) -> None:
    f = bin_dir / name
    f.write_text(body)
    f.chmod(f.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _source_bashrc(shim_dir: Path, bin_dir: Path) -> subprocess.CompletedProcess:
    """Source the written bashrc in a clean bash with PATH limited to bin_dir.

    `command -v`, `eval`, `.` and `[` are builtins, so a real fzf can never
    leak in through PATH.
    """
    env = {"PATH": str(bin_dir), "HOME": str(shim_dir / "home")}
    return subprocess.run(
        ["/bin/bash", "--norc", "--noprofile", "-c",
         '. "$HOME/.bashrc"; printf %s "${FZF_LOADED-}"'],
        env=env, capture_output=True, text=True,
    )


def test_fzf_block_uses_fzf_bash_when_available(shim_dir, monkeypatch, tmp_path):
    _written_bashrc(shim_dir, monkeypatch, tmp_path)
    bin_dir = tmp_path / "bin-a"
    bin_dir.mkdir()
    _shim("fzf", '#!/bin/sh\nif [ "$1" = "--bash" ]; then echo "FZF_LOADED=bash"; fi\n', bin_dir)
    result = _source_bashrc(shim_dir, bin_dir)
    assert result.returncode == 0 and result.stderr == ""
    assert result.stdout == "bash"


def test_fzf_block_falls_back_to_the_example_file(shim_dir, monkeypatch, tmp_path):
    _written_bashrc(shim_dir, monkeypatch, tmp_path)
    bin_dir = tmp_path / "bin-b"
    bin_dir.mkdir()
    # fzf 0.44: --bash is an unknown option (stderr swallowed by the block).
    _shim("fzf", '#!/bin/sh\necho "fzf: unknown option: --bash" >&2\nexit 2\n', bin_dir)
    (tmp_path / "key-bindings.bash").write_text("FZF_LOADED=file\n")
    result = _source_bashrc(shim_dir, bin_dir)
    assert result.returncode == 0 and result.stderr == ""
    assert result.stdout == "file"


def test_fzf_block_is_silent_when_neither_path_works(shim_dir, monkeypatch, tmp_path):
    _written_bashrc(shim_dir, monkeypatch, tmp_path)
    bin_dir = tmp_path / "bin-c"
    bin_dir.mkdir()
    _shim("fzf", '#!/bin/sh\necho "fzf: unknown option: --bash" >&2\nexit 2\n', bin_dir)
    # No fallback file — a minimized image may have no /usr/share/doc.
    result = _source_bashrc(shim_dir, bin_dir)
    assert result.returncode == 0 and result.stderr == ""
    assert result.stdout == ""


def test_fzf_block_does_nothing_without_fzf(shim_dir, monkeypatch, tmp_path):
    _written_bashrc(shim_dir, monkeypatch, tmp_path)
    bin_dir = tmp_path / "bin-d"
    bin_dir.mkdir()  # no fzf at all — e.g. removed by hand later
    (tmp_path / "key-bindings.bash").write_text("FZF_LOADED=file\n")
    result = _source_bashrc(shim_dir, bin_dir)
    assert result.returncode == 0 and result.stderr == ""
    assert result.stdout == ""
