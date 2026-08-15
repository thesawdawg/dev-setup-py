from __future__ import annotations

import hashlib
from unittest import mock

import pytest

from dev_setup import generic, ui
from dev_setup.generic import GenericTool, _download_script, _is_simple_command


def make_tool(**kwargs) -> GenericTool:
    kwargs.setdefault("key", "demo")
    kwargs.setdefault("name", "Demo")
    return GenericTool(**kwargs)


# -- dataclass / dict round-trip ------------------------------------------------


def test_from_dict_to_dict_round_trip():
    data = {
        "name": "Demo",
        "description": "a tool",
        "category": "tools",
        "type": "bash",
        "check_cmd": "demo",
        "install_script": "echo hi",
        "remove_script": "echo bye",
        "docs_url": "https://example.com",
    }
    tool = GenericTool.from_dict(data, key="demo")
    assert tool.install_type == "bash"
    assert tool.to_dict() == data


def test_auto_requires_derived_and_not_persisted():
    npm = GenericTool.from_dict({"type": "npm", "npm_name": "x"}, key="x")
    uvx = GenericTool.from_dict({"type": "uvx", "pip_name": "y"}, key="y")
    plain = GenericTool.from_dict({"type": "bash"}, key="z")

    assert npm.requires == ["nvm"]
    assert uvx.requires == ["uv"]
    assert plain.requires == []
    assert "requires" not in npm.to_dict()
    assert "requires" not in uvx.to_dict()


def test_explicit_requires_persisted():
    tool = GenericTool.from_dict({"type": "npm", "npm_name": "x", "requires": ["docker"]}, key="x")
    assert tool.to_dict()["requires"] == ["docker"]


def test_name_defaults_to_key():
    assert GenericTool(key="mytool").name == "mytool"


def test_sha256_field_round_trips():
    tool = GenericTool.from_dict(
        {"type": "script", "script_url": "https://x/i.sh", "sha256": "abc"}, key="s"
    )
    assert tool.sha256 == "abc"
    assert tool.to_dict()["sha256"] == "abc"


# -- strategy dispatch -----------------------------------------------------------


def test_install_unknown_type_raises():
    with pytest.raises(RuntimeError, match="Unsupported install type"):
        make_tool(install_type="nope").install()


def test_remove_unknown_type_raises():
    with pytest.raises(RuntimeError, match="Unsupported remove type"):
        make_tool(install_type="nope").remove()


def test_install_npm_requires_npm_name():
    with pytest.raises(RuntimeError, match="npm_name not set"):
        make_tool(install_type="npm").install()


def test_install_bash_runs_script(monkeypatch):
    ran = {}
    monkeypatch.setattr(generic, "_run_bash_script", lambda s: ran.setdefault("script", s))
    tool = make_tool(install_type="bash", install_script="echo hi")
    with mock.patch.object(GenericTool, "get_version", return_value="1.0"):
        assert tool.install() == "1.0"
    assert ran["script"] == "echo hi"


def test_remove_script_type_uses_remove_script(monkeypatch):
    ran = {}
    monkeypatch.setattr(generic, "_run_bash_script", lambda s: ran.setdefault("script", s))
    tool = make_tool(install_type="script", script_url="https://x/i.sh", remove_script="echo bye")
    tool.remove()
    assert ran["script"] == "echo bye"


def test_remove_script_type_without_remove_script_raises():
    tool = make_tool(install_type="script", script_url="https://x/i.sh")
    with pytest.raises(RuntimeError, match="Remove manually"):
        tool.remove()


def test_is_installed_uses_type_checker(monkeypatch):
    # dpkg -s <pkg>, with the status marker the apt manager looks for.
    def fake_probe(cmd, **kwargs):
        installed = cmd[-1] == "good"
        return mock.Mock(
            returncode=0 if installed else 1,
            stdout="Status: install ok installed\n" if installed else "",
        )

    monkeypatch.setattr(generic.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(generic, "_probe", fake_probe)
    assert make_tool(install_type="apt", apt_packages="good extra").is_installed()
    assert not make_tool(install_type="apt", apt_packages="bad").is_installed()
    # `system` is the canonical spelling of the same type, and `packages` of the
    # same field — both dispatch to the identical checker.
    assert make_tool(install_type="system", packages="good").is_installed()
    assert not make_tool(install_type="system", packages="bad").is_installed()


def test_is_installed_unknown_type_is_false():
    assert not make_tool(install_type="mystery").is_installed()


# -- sha256 verification -----------------------------------------------------------


def _fake_urlopen(payload: bytes):
    m = mock.MagicMock()
    m.__enter__.return_value.read.return_value = payload
    return m


def test_download_script_verifies_checksum():
    payload = b"echo hi\n"
    digest = hashlib.sha256(payload).hexdigest()
    with mock.patch("urllib.request.urlopen", return_value=_fake_urlopen(payload)):
        assert _download_script("https://x/i.sh", expected_sha256=digest) == "echo hi\n"


def test_download_script_rejects_bad_checksum():
    with (
        mock.patch("urllib.request.urlopen", return_value=_fake_urlopen(b"evil")),
        pytest.raises(RuntimeError, match="Checksum mismatch"),
    ):
        _download_script("https://x/i.sh", expected_sha256="0" * 64)


def test_download_script_skips_check_when_no_sha256():
    with mock.patch("urllib.request.urlopen", return_value=_fake_urlopen(b"ok")):
        assert _download_script("https://x/i.sh") == "ok"


# -- helpers -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "cmd,expected",
    [
        ("docker", True),
        ("git-lfs", True),
        ("", False),
        ("test -d ~/.nvm", False),
        ("command -v x | grep x", False),
        ('echo "hi"', False),
    ],
)
def test_is_simple_command(cmd, expected):
    assert _is_simple_command(cmd) is expected


# -- check_cmd probing ---------------------------------------------------------------


def _probed_script(run_mock) -> str:
    """The shell text passed to `bash -lc` by the last _check_cmd_installed probe."""
    return run_mock.call_args[0][0][2]


@pytest.mark.parametrize("cmd", ["llm-checker --version", "llm-checker"])
def test_npm_check_cmd_sources_nvm_whether_simple_or_complex(cmd):
    """`bash -lc` never loads nvm on its own: it reads ~/.profile, whose sourcing of
    ~/.bashrc returns early for a non-interactive shell. Without the explicit source,
    an npm-type tool reports "not installed" right after installing successfully."""
    with mock.patch("dev_setup.generic.shutil.which", return_value=None), \
         mock.patch("subprocess.run") as run:
        run.return_value.returncode = 0
        assert generic._check_cmd_installed(cmd, install_type="npm")
    assert ".nvm/nvm.sh" in _probed_script(run)


def test_non_npm_check_cmd_does_not_pay_for_nvm():
    with mock.patch("dev_setup.generic.shutil.which", return_value=None), \
         mock.patch("subprocess.run") as run:
        run.return_value.returncode = 0
        generic._check_cmd_installed("test -d ~/.somewhere", install_type="bash")
    assert ".nvm/nvm.sh" not in _probed_script(run)


def test_complex_check_cmd_still_reports_a_failing_command_as_not_installed():
    assert not generic._check_cmd_installed("false --version", install_type="npm")


# -- uvx install flags ----------------------------------------------------------


def _capture_uvx_argv(monkeypatch, tool) -> list[str]:
    calls: list[list[str]] = []
    monkeypatch.setattr(generic.shutil, "which", lambda _c: "/usr/bin/uv")
    monkeypatch.setattr(generic, "_run", lambda cmd, **kw: calls.append(cmd))
    with mock.patch.object(GenericTool, "get_version", return_value="1.0"):
        tool.install()
    return calls[0]


def test_uvx_install_without_uv_fields_is_a_bare_install(monkeypatch):
    tool = make_tool(install_type="uvx", pip_name="ipython")

    argv = _capture_uvx_argv(monkeypatch, tool)

    assert argv == ["/usr/bin/uv", "tool", "install", "ipython"]


def test_uvx_install_passes_executables_from(monkeypatch):
    # The ansible case: entry points live in ansible-core, not in ansible.
    tool = make_tool(
        install_type="uvx", pip_name="ansible", uv_executables_from=["ansible-core"]
    )

    argv = _capture_uvx_argv(monkeypatch, tool)

    assert argv == [
        "/usr/bin/uv",
        "tool",
        "install",
        "--with-executables-from",
        "ansible-core",
        "ansible",
    ]


def test_uvx_install_passes_python_and_with_flags(monkeypatch):
    tool = make_tool(
        install_type="uvx",
        pip_name="ansible",
        uv_python="3.12",
        uv_with=["jmespath", "netaddr"],
        uv_executables_from=["ansible-core"],
    )

    argv = _capture_uvx_argv(monkeypatch, tool)

    assert argv == [
        "/usr/bin/uv",
        "tool",
        "install",
        "--python",
        "3.12",
        "--with",
        "jmespath",
        "--with",
        "netaddr",
        "--with-executables-from",
        "ansible-core",
        "ansible",
    ]
    # the package must stay last, after every flag
    assert argv[-1] == "ansible"


def test_uvx_remove_prefers_remove_script(monkeypatch):
    # ansible-vault shares ansible's pip_name; `uv tool uninstall ansible` there would
    # remove ansible-playbook and friends too.
    ran = {}
    monkeypatch.setattr(generic, "_run_bash_script", lambda s: ran.setdefault("script", s))
    monkeypatch.setattr(generic, "_run", lambda cmd, **kw: pytest.fail("uv was invoked"))
    tool = make_tool(install_type="uvx", pip_name="ansible", remove_script="echo bye")

    tool.remove()

    assert ran["script"] == "echo bye"


# -- uvx update -----------------------------------------------------------------


def _capture_uvx_update_argv(monkeypatch, tool, version) -> list[str]:
    calls: list[list[str]] = []
    monkeypatch.setattr(generic.shutil, "which", lambda _c: "/usr/bin/uv")
    monkeypatch.setattr(generic, "_run", lambda cmd, **kw: calls.append(cmd))
    with mock.patch.object(GenericTool, "get_version", return_value="1.0"):
        tool.update(version)
    return calls[0]


def test_uvx_update_unpinned_installs_latest(monkeypatch):
    tool = make_tool(install_type="uvx", pip_name="commitizen")

    argv = _capture_uvx_update_argv(monkeypatch, tool, None)

    # NOT `uv tool upgrade`: that is a no-op on a tool someone previously pinned,
    # which would leave them with no route back to latest through devstuff.
    assert argv == [
        "/usr/bin/uv",
        "tool",
        "install",
        "--force",
        "commitizen@latest",
    ]


def test_uvx_update_pinned_uses_forced_install(monkeypatch):
    # `uv tool upgrade "pkg==1.2.3"` reads the whole string as a tool name and fails.
    tool = make_tool(install_type="uvx", pip_name="commitizen")

    argv = _capture_uvx_update_argv(monkeypatch, tool, "4.16.0")

    assert argv == [
        "/usr/bin/uv",
        "tool",
        "install",
        "--force",
        "commitizen==4.16.0",
    ]
    assert "upgrade" not in argv


def test_uvx_update_pinned_reapplies_uv_flags(monkeypatch):
    # --force writes a fresh receipt, so the flags must be passed again or the
    # pinned install silently loses its extra executables.
    tool = make_tool(
        install_type="uvx",
        pip_name="ansible",
        uv_executables_from=["ansible-core"],
        uv_python="3.12",
    )

    argv = _capture_uvx_update_argv(monkeypatch, tool, "14.2.0")

    assert argv == [
        "/usr/bin/uv",
        "tool",
        "install",
        "--force",
        "--python",
        "3.12",
        "--with-executables-from",
        "ansible-core",
        "ansible==14.2.0",
    ]


def test_uvx_update_pinned_warns_about_the_pin(monkeypatch):
    warnings: list[str] = []
    monkeypatch.setattr(ui, "warn", warnings.append)
    tool = make_tool(install_type="uvx", pip_name="commitizen")

    _capture_uvx_update_argv(monkeypatch, tool, "4.16.0")

    assert len(warnings) == 1
    assert "4.16.0" in warnings[0]
    # The stated escape route must be the one that actually works.
    assert "devstuff update demo" in warnings[0]
    assert "--version" in warnings[0]
