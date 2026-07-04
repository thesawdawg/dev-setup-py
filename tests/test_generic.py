from __future__ import annotations

import hashlib
from unittest import mock

import pytest

from dev_setup import generic
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
    monkeypatch.setattr(generic, "_apt_installed", lambda pkg: pkg == "good")
    assert make_tool(install_type="apt", apt_packages="good extra").is_installed()
    assert not make_tool(install_type="apt", apt_packages="bad").is_installed()


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
