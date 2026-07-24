from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from dev_setup.agent import sandbox
from dev_setup.agent.sandbox import SandboxError, Workspace


@pytest.fixture()
def ws(tmp_path):
    root = tmp_path / "workspace"
    (root / "src").mkdir(parents=True)
    (root / "src" / "main.py").write_text("print('hi')\n")
    return Workspace.create(root)


# -- construction ----------------------------------------------------------------


def test_create_resolves_and_starts_cwd_at_root(ws):
    assert ws.cwd == ws.root
    assert ws.root.is_absolute()


def test_create_rejects_missing_directory(tmp_path):
    with pytest.raises(SandboxError, match="does not exist"):
        Workspace.create(tmp_path / "ghost")


def test_create_rejects_a_file(tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("x")
    with pytest.raises(SandboxError, match="not a directory"):
        Workspace.create(target)


# -- containment -----------------------------------------------------------------


def test_resolves_relative_path_against_cwd(ws):
    assert ws.resolve("src/main.py") == ws.root / "src" / "main.py"


def test_resolves_path_that_does_not_exist_yet(ws):
    """write_file must be able to name a new file."""
    assert ws.resolve("src/new.py") == ws.root / "src" / "new.py"


def test_rejects_parent_traversal(ws):
    with pytest.raises(SandboxError, match="escapes the workspace root"):
        ws.resolve("../outside.txt")


def test_rejects_deep_parent_traversal(ws):
    with pytest.raises(SandboxError, match="escapes the workspace root"):
        ws.resolve("src/../../../../etc/passwd")


def test_rejects_absolute_path_outside_root(ws):
    with pytest.raises(SandboxError, match="escapes the workspace root"):
        ws.resolve("/etc/passwd")


def test_accepts_absolute_path_inside_root(ws):
    assert ws.resolve(str(ws.root / "src")) == ws.root / "src"


def test_rejects_symlink_escape(ws, tmp_path):
    """A symlink planted inside the workspace must not become a way out --
    resolve() follows links before the containment check."""
    secret = tmp_path / "secret.txt"
    secret.write_text("password")
    (ws.root / "escape").symlink_to(secret)

    with pytest.raises(SandboxError, match="escapes the workspace root"):
        ws.resolve("escape")


def test_rejects_symlinked_directory_escape(ws, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (ws.root / "link").symlink_to(outside, target_is_directory=True)

    with pytest.raises(SandboxError, match="escapes the workspace root"):
        ws.resolve("link/file.txt")


def test_rejects_home_expansion_outside_root(ws):
    with pytest.raises(SandboxError, match="escapes the workspace root"):
        ws.resolve("~/.bashrc")


def test_root_itself_is_in_bounds(ws):
    assert ws.resolve(".") == ws.root


# -- protected paths -------------------------------------------------------------


def test_credential_dirs_denied_even_when_inside_root(tmp_path, monkeypatch):
    """A workspace root of $HOME must not put ~/.ssh in bounds."""
    home = tmp_path / "home"
    (home / ".ssh").mkdir(parents=True)
    (home / ".ssh" / "id_ed25519").write_text("KEY")
    monkeypatch.setattr(sandbox.Path, "home", classmethod(lambda cls: home))

    ws = Workspace.create(home)
    with pytest.raises(SandboxError, match="is not permitted"):
        ws.resolve(".ssh/id_ed25519")


def test_credential_dirs_denied_for_read_not_only_write(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".aws").mkdir(parents=True)
    monkeypatch.setattr(sandbox.Path, "home", classmethod(lambda cls: home))

    ws = Workspace.create(home)
    with pytest.raises(SandboxError):
        ws.resolve(".aws/credentials", write=False)


def test_devstuff_config_is_readable_but_not_writable(tmp_path, monkeypatch):
    """FR-14a: the agent may consult the catalogs, never author them."""
    config = tmp_path / "workspace" / "cfg"
    config.mkdir(parents=True)
    monkeypatch.setattr(sandbox, "CONFIG_DIR", config)

    ws = Workspace.create(tmp_path / "workspace")
    assert ws.resolve("cfg/tools.yaml") == config / "tools.yaml"
    with pytest.raises(SandboxError, match="edited by hand"):
        ws.resolve("cfg/tools.yaml", write=True)


# -- cwd movement ----------------------------------------------------------------


def test_chdir_moves_within_root(ws):
    assert ws.chdir("src") == ws.root / "src"
    assert ws.cwd == ws.root / "src"
    # Relative paths now resolve against the new cwd.
    assert ws.resolve("main.py") == ws.root / "src" / "main.py"


def test_chdir_cannot_escape(ws):
    with pytest.raises(SandboxError, match="escapes the workspace root"):
        ws.chdir("..")
    assert ws.cwd == ws.root


def test_chdir_rejects_a_file(ws):
    with pytest.raises(SandboxError, match="not a directory"):
        ws.chdir("src/main.py")


def test_chdir_back_to_root_from_subdir(ws):
    ws.chdir("src")
    assert ws.chdir(str(ws.root)) == ws.root


def test_display_is_workspace_relative(ws):
    assert ws.display(ws.root / "src" / "main.py") == "./src/main.py"
    assert ws.display(ws.root) == "."


# -- launch guard ----------------------------------------------------------------


def test_assess_clean_directory_is_silent(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert sandbox.assess(plain) == []


def test_assess_warns_on_home_directory(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(sandbox.Path, "home", classmethod(lambda cls: home))
    assert any("home directory" in w for w in sandbox.assess(home))


@pytest.mark.parametrize("target", ["/", "/etc", "/usr"])
def test_assess_warns_on_system_directories(target):
    assert any("system directory" in w for w in sandbox.assess(Path(target)))


def _git_repo(path, *, dirty: bool):
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "t"], check=True)
    (path / "tracked.txt").write_text("v1\n")
    subprocess.run(["git", "-C", str(path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-qm", "init"], check=True)
    if dirty:
        (path / "tracked.txt").write_text("v2\n")


@pytest.mark.skipif(not sandbox.shutil.which("git"), reason="git not available")
def test_assess_warns_on_dirty_git_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_repo(repo, dirty=True)
    warnings = sandbox.assess(repo)
    assert any("uncommitted change" in w for w in warnings)


@pytest.mark.skipif(not sandbox.shutil.which("git"), reason="git not available")
def test_assess_quiet_on_clean_git_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_repo(repo, dirty=False)
    assert sandbox.assess(repo) == []


def test_assess_survives_missing_git(tmp_path, monkeypatch):
    monkeypatch.setattr(sandbox.shutil, "which", lambda _: None)
    plain = tmp_path / "plain"
    plain.mkdir()
    assert sandbox.assess(plain) == []
