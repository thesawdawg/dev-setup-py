"""
Integration tests: verify each builtin tool installs successfully in a clean environment.

Run inside the devstuff-ci Docker image (see dev/Dockerfile.ci) or any fresh Ubuntu system
with sudo access:

    pytest tests/integration/ -m integration -v
"""
from __future__ import annotations

import shutil

import pytest

from dev_setup import registry

# Extra executables a tool must put on PATH beyond its own check_cmd.
#
# `uv tool install` only exposes console scripts of the *requested* package, and the
# `ansible` distribution declares none — every real entry point belongs to its
# ansible-core dependency. So a broken install still yields an `ansible` binary via
# some other route while ansible-playbook and friends are missing, and a check_cmd-only
# assertion passes. Name them explicitly.
_EXTRA_EXECUTABLES: dict[str, tuple[str, ...]] = {
    "ansible": ("ansible-playbook", "ansible-galaxy", "ansible-vault", "ansible-doc"),
    "ansible-vault": ("ansible-playbook",),
}

# Tools excluded from automated CI testing — with explicit reasons
_SKIP: dict[str, str] = {
    "docker": "requires privileged Docker-in-Docker; cannot run reliably inside a container",
    "ollama": "install script registers a systemd service that cannot start without systemd",
}


def _builtin_tools():
    registry.init()
    return [t for t in registry.all_tools() if t.builtin and t.key not in _SKIP]


def _ensure_installed(key: str) -> None:
    """Install a dependency tool if it is not already present."""
    tool = registry.get(key)
    if tool and not tool.is_installed():
        tool.install()


@pytest.mark.integration
@pytest.mark.parametrize("tool", _builtin_tools(), ids=lambda t: t.key)
def test_install(tool):
    """Each builtin tool must install and report is_installed() == True afterwards."""
    # Satisfy declared inter-tool dependencies before attempting the install
    for dep_key in tool.requires:
        _ensure_installed(dep_key)

    if tool.is_installed():
        pytest.skip(f"{tool.key} is already present in this environment")

    tool.install()

    assert tool.is_installed(), (
        f"{tool.name} ({tool.key}): install() completed without raising "
        "but is_installed() still returns False"
    )

    for exe in _EXTRA_EXECUTABLES.get(tool.key, ()):
        assert shutil.which(exe), (
            f"{tool.name} ({tool.key}): installed, but {exe!r} is not on PATH"
        )
