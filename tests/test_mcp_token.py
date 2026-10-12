"""The bearer token file (docs/specs/mcp, FR-24, SD-7, SD-8).

The token is the only thing between the network and every enabled tool, so the file it lives in is
held to the standard of an ssh key: private to the user, and never printed.
"""

from __future__ import annotations

import os
import stat

import pytest

from dev_setup.mcp import token


def mode(path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


def test_first_use_creates_a_private_file_in_a_private_directory(tmp_path):
    path = tmp_path / "state" / "devstuff" / "mcp-token"
    value, used = token.load(path)
    assert used == path and path.read_text().strip() == value
    assert mode(path) == 0o600
    assert mode(path.parent) == 0o700  # the directory the file lives in, too
    assert len(value) >= 32 and value.isascii() and value.isprintable() and " " not in value


def test_the_token_survives_a_restart(tmp_path):
    """SD-8: a client's Authorization header must still be right after the server restarts."""
    path = tmp_path / "mcp-token"
    first, _ = token.load(path)
    second, _ = token.load(path)
    assert first == second


def test_rotate_replaces_the_contents_and_keeps_the_mode(tmp_path):
    path = tmp_path / "mcp-token"
    old, _ = token.load(path)
    new, _ = token.load(path, rotate=True)
    assert new != old and path.read_text().strip() == new and mode(path) == 0o600
    assert token.load(path)[0] == new  # and it sticks


def test_rotating_a_file_that_does_not_exist_yet_just_creates_it(tmp_path):
    value, _ = token.load(tmp_path / "t", rotate=True)
    assert value


@pytest.mark.parametrize("bits", [0o640, 0o604, 0o660, 0o666, 0o644, 0o607])
def test_a_file_readable_or_writable_by_group_or_others_is_refused(tmp_path, bits):
    path = tmp_path / "mcp-token"
    token.load(path)
    path.chmod(bits)
    with pytest.raises(token.TokenError, match="permissions"):
        token.load(path)


def test_rotate_does_not_launder_a_loose_file(tmp_path):
    """A loose mode is refused even when the caller is about to replace the contents: the old
    secret was exposed, and silently fixing it would hide that."""
    path = tmp_path / "mcp-token"
    token.load(path)
    path.chmod(0o644)
    with pytest.raises(token.TokenError):
        token.load(path, rotate=True)


def test_an_empty_or_short_token_is_refused_not_regenerated(tmp_path):
    path = tmp_path / "mcp-token"
    for content in ("", "   \n", "short"):
        path.write_text(content)
        path.chmod(0o600)
        with pytest.raises(token.TokenError, match="--rotate-token"):
            token.load(path)


def test_a_token_that_cannot_travel_in_a_header_is_refused(tmp_path):
    path = tmp_path / "mcp-token"
    path.write_text("a" * 20 + "\nsecond line")
    path.chmod(0o600)
    with pytest.raises(token.TokenError):
        token.load(path)


def test_a_hand_written_token_is_used_as_is(tmp_path):
    path = tmp_path / "mcp-token"
    path.write_text("my-own-long-enough-token-1234567890\n")
    path.chmod(0o600)
    assert token.load(path)[0] == "my-own-long-enough-token-1234567890"


def test_the_default_location_follows_xdg_state_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg"))
    assert token.default_path() == tmp_path / "xdg" / "devstuff" / "mcp-token"


def test_the_default_location_without_xdg_is_under_home(tmp_path, monkeypatch):
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert token.default_path() == tmp_path / ".local" / "state" / "devstuff" / "mcp-token"


def test_an_unusable_location_is_a_clear_error_not_a_traceback(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    with pytest.raises(token.TokenError, match="file"):
        token.load(blocker / "dir" / "mcp-token")


def test_the_token_is_not_in_the_error_text(tmp_path):
    path = tmp_path / "mcp-token"
    value, _ = token.load(path)
    path.chmod(0o644)
    with pytest.raises(token.TokenError) as err:
        token.load(path)
    assert value not in str(err.value)
