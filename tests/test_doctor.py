from __future__ import annotations

import os
from pathlib import Path

import pytest

from dev_setup import doctor as mod
from dev_setup.doctor import FAIL, PASS, WARN, CheckResult

# ─── CheckResult ──────────────────────────────────────────────────────────────


def test_check_result_ok_property():
    assert CheckResult("x", PASS, "ok").ok is True
    assert CheckResult("x", WARN, "w").ok is False
    assert CheckResult("x", FAIL, "f").ok is False


# ─── check_python_version ─────────────────────────────────────────────────────


def test_check_python_version_passes_on_311plus():
    r = mod.check_python_version()
    assert r.name == "python-version"
    # Running under 3.11+ (the project's minimum) so this must pass.
    assert r.status == PASS


# ─── check_runtime_deps ───────────────────────────────────────────────────────


def test_check_runtime_deps_passes():
    r = mod.check_runtime_deps()
    assert r.status == PASS


# ─── _find_old_dirs ───────────────────────────────────────────────────────────


def test_find_old_dirs_empty_when_neither_exists(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "_OLD_CONFIG_DIR", tmp_path / "cfg-old")
    monkeypatch.setattr(mod, "_OLD_DATA_DIR", tmp_path / "data-old")
    monkeypatch.setattr(mod, "_NEW_CONFIG_DIR", tmp_path / "cfg-new")
    monkeypatch.setattr(mod, "_NEW_DATA_DIR", tmp_path / "data-new")
    assert mod._find_old_dirs() == []


def test_find_old_dirs_reports_config_only(tmp_path, monkeypatch):
    old_cfg = tmp_path / "cfg-old"
    old_cfg.mkdir()
    monkeypatch.setattr(mod, "_OLD_CONFIG_DIR", old_cfg)
    monkeypatch.setattr(mod, "_OLD_DATA_DIR", tmp_path / "data-old")
    monkeypatch.setattr(mod, "_NEW_CONFIG_DIR", tmp_path / "cfg-new")
    monkeypatch.setattr(mod, "_NEW_DATA_DIR", tmp_path / "data-new")
    assert mod._find_old_dirs() == [(old_cfg, tmp_path / "cfg-new")]


def test_find_old_dirs_reports_both(tmp_path, monkeypatch):
    old_cfg = tmp_path / "cfg-old"
    old_data = tmp_path / "data-old"
    old_cfg.mkdir()
    old_data.mkdir()
    monkeypatch.setattr(mod, "_OLD_CONFIG_DIR", old_cfg)
    monkeypatch.setattr(mod, "_OLD_DATA_DIR", old_data)
    monkeypatch.setattr(mod, "_NEW_CONFIG_DIR", tmp_path / "cfg-new")
    monkeypatch.setattr(mod, "_NEW_DATA_DIR", tmp_path / "data-new")
    pairs = mod._find_old_dirs()
    assert (old_cfg, tmp_path / "cfg-new") in pairs
    assert (old_data, tmp_path / "data-new") in pairs


# ─── _find_stale_bashrc_blocks ────────────────────────────────────────────────


@pytest.fixture()
def fake_home(tmp_path, monkeypatch):
    """Point Path.home() at a tmp dir and return its .bashrc path."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return tmp_path / ".bashrc"


def test_find_stale_bashrc_blocks_no_file(fake_home):
    assert mod._find_stale_bashrc_blocks() == []


def test_find_stale_bashrc_blocks_finds_old_markers(fake_home):
    fake_home.write_text(
        "\n".join(
            [
                "# Starship prompt",
                'eval "$(starship init bash)"',
                "",
                "# dev-setup: bat",
                "export BAT_THEME=Catppuccin-mocha",
                "",
                "# dev-setup-fn:ssh-agent-key",
                "ssh-agent-key() { echo hi; }",
                "",
                "# devstuff: bat",
                "export BAT_THEME=other",
                "",
            ]
        )
        + "\n"
    )
    blocks = mod._find_stale_bashrc_blocks()
    assert "dev-setup: bat" in blocks
    assert "dev-setup-fn:ssh-agent-key" in blocks
    assert "devstuff: bat" not in blocks
    assert "Starship prompt" not in blocks


def test_find_stale_bashrc_blocks_ignores_lookalikes(fake_home):
    fake_home.write_text("# this is not a dev-setup marker\nsome line\n\n")
    assert mod._find_stale_bashrc_blocks() == []


# ─── check_stale_devsetup_executable ──────────────────────────────────────────


def test_check_stale_executable_passes_when_absent(monkeypatch):
    monkeypatch.setattr(mod.shutil, "which", lambda name: None)
    r = mod.check_stale_devsetup_executable()
    assert r.status == PASS
    assert r.fix is None


def test_check_stale_executable_warns_when_found(monkeypatch, tmp_path):
    fake_exe = tmp_path / "dev-setup"
    monkeypatch.setattr(mod.shutil, "which", lambda name: str(fake_exe))
    r = mod.check_stale_devsetup_executable()
    assert r.status == WARN
    assert r.fix is not None


# ─── _move_one_dir ────────────────────────────────────────────────────────────


def test_move_one_dir_moves_when_new_absent(tmp_path):
    old = tmp_path / "old"
    new = tmp_path / "new"
    old.mkdir()
    (old / "tools.yaml").write_text("tools: {}\n")
    assert mod._move_one_dir(old, new)
    assert not old.exists()
    assert (new / "tools.yaml").read_text() == "tools: {}\n"


def test_move_one_dir_merges_into_existing(tmp_path):
    old = tmp_path / "old"
    new = tmp_path / "new"
    old.mkdir()
    new.mkdir()
    (new / "existing.yaml").write_text("kept\n")
    (old / "tools.yaml").write_text("tools: {}\n")
    (old / "existing.yaml").write_text("would-clobber\n")
    assert mod._move_one_dir(old, new)
    assert (new / "tools.yaml").read_text() == "tools: {}\n"
    # existing file in new is preserved, not overwritten
    assert (new / "existing.yaml").read_text() == "kept\n"
    # skipped file is left behind in old
    assert old.exists()
    assert (old / "existing.yaml").read_text() == "would-clobber\n"


def test_move_one_dir_noop_when_old_missing(tmp_path):
    old = tmp_path / "old"
    new = tmp_path / "new"
    assert mod._move_one_dir(old, new)


# ─── _fix_remove_bashrc_blocks ────────────────────────────────────────────────


def test_fix_remove_bashrc_blocks_removes_and_verifies(fake_home):
    fake_home.write_text(
        "\n".join(
            [
                "# dev-setup: bat",
                "export BAT_THEME=Catppuccin-mocha",
                "",
                "# other-marker",
                "echo hi",
                "",
            ]
        )
        + "\n"
    )
    assert mod._fix_remove_bashrc_blocks(["dev-setup: bat"])
    text = fake_home.read_text()
    assert "dev-setup: bat" not in text
    assert "BAT_THEME" not in text
    assert "# other-marker" in text


def test_fix_remove_bashrc_blocks_returns_false_when_absent(fake_home):
    fake_home.write_text("# unrelated\nfoo\n\n")
    assert not mod._fix_remove_bashrc_blocks(["dev-setup: bat"])


# ─── _fix_remove_stale_symlink ────────────────────────────────────────────────


def test_fix_remove_stale_symlink_unlinks(tmp_path):
    target = tmp_path / "target"
    target.write_text("x")
    link = tmp_path / "dev-setup"
    os.symlink(target, link)
    assert mod._fix_remove_stale_symlink(link)
    assert not link.exists()


def test_fix_remove_stale_symlink_refuses_current_devstuff_link(tmp_path):
    devstuff_dir = tmp_path / "devstuff-bin"
    devstuff_dir.mkdir()
    (devstuff_dir / "devstuff").write_text("x")
    link = tmp_path / "dev-setup"
    os.symlink(devstuff_dir / "devstuff", link)
    assert not mod._fix_remove_stale_symlink(link)
    assert link.exists()


def test_fix_remove_stale_symlink_refuses_regular_file(tmp_path):
    f = tmp_path / "dev-setup"
    f.write_text("a real binary, maybe")
    assert not mod._fix_remove_stale_symlink(f)
    assert f.exists()


# ─── _fix_create_dir ──────────────────────────────────────────────────────────


def test_fix_create_dir_creates(tmp_path):
    d = tmp_path / "new" / "nested"
    assert mod._fix_create_dir(d)
    assert d.exists() and os.access(d, os.W_OK)


def test_fix_create_dir_idempotent(tmp_path):
    d = tmp_path / "exists"
    d.mkdir()
    assert mod._fix_create_dir(d)


# ─── check_old_config_dirs (integration of detection into check) ──────────────


def test_check_old_config_dirs_passes_when_clean(monkeypatch, tmp_path):
    monkeypatch.setattr(mod, "_OLD_CONFIG_DIR", tmp_path / "nope1")
    monkeypatch.setattr(mod, "_OLD_DATA_DIR", tmp_path / "nope2")
    monkeypatch.setattr(mod, "_NEW_CONFIG_DIR", tmp_path / "new1")
    monkeypatch.setattr(mod, "_NEW_DATA_DIR", tmp_path / "new2")
    r = mod.check_old_config_dirs()
    assert r.status == PASS
    assert r.fix is None


def test_check_old_config_dirs_warns_with_fix(monkeypatch, tmp_path):
    old = tmp_path / "old-cfg"
    old.mkdir()
    monkeypatch.setattr(mod, "_OLD_CONFIG_DIR", old)
    monkeypatch.setattr(mod, "_OLD_DATA_DIR", tmp_path / "nope")
    monkeypatch.setattr(mod, "_NEW_CONFIG_DIR", tmp_path / "new-cfg")
    monkeypatch.setattr(mod, "_NEW_DATA_DIR", tmp_path / "new-data")
    r = mod.check_old_config_dirs()
    assert r.status == WARN
    assert r.fix is not None
    # applying the fix moves the dir
    assert r.fix()
    assert not old.exists()
    assert (tmp_path / "new-cfg").exists()


# ─── run_all_checks ───────────────────────────────────────────────────────────


def test_run_all_checks_returns_results_for_every_check():
    results = mod.run_all_checks()
    assert len(results) == len(mod.ALL_CHECKS)
    # every result has the right fields
    for r in results:
        assert r.name
        assert r.status in (PASS, WARN, FAIL)
        assert r.message
