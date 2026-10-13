"""`managers.py`: who manages — or could manage — a tool's installs.

The point of the module is that catalog metadata is only a claim; the machine
decides. These tests monkeypatch the probes (`_uv_tool_versions`,
`_nvm_npm_globals`, `_ROOT_PROBES`, `shutil.which`, `generic._probe`) rather than
the tool rows, so every case reaches the verdict logic it names.
"""

from __future__ import annotations

import io
import json
import os
import shutil
from types import SimpleNamespace

import pytest
from rich.console import Console

from dev_setup import generic, managers, registry, ui
from dev_setup.cli import cli
from dev_setup.generic import GenericTool


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    generic.forget_probes()
    # manager_of refuses keys outside the registry — keep the two real managers.
    monkeypatch.setattr(registry, "exists", lambda k: k in {"uv", "nvm"})
    yield
    generic.forget_probes()


def _uvx(key="ipython", pip_name="ipython", check_cmd="ipython"):
    return GenericTool(
        key=key, install_type="uvx", pip_name=pip_name, check_cmd=check_cmd
    )


def _npm(key="codex", npm_name="@openai/codex", check_cmd="codex"):
    return GenericTool(
        key=key, install_type="npm", npm_name=npm_name, check_cmd=check_cmd
    )


def _uv_tools(data=None, why=""):
    return lambda: generic._Answer(data, why)


def _nvm_globals(data=None, why=""):
    return lambda: generic._Answer(data, why)


# -- manager_of -----------------------------------------------------------------


def test_manager_of_derives_the_type_defaults():
    assert managers.manager_of(_uvx()) == "uv"
    assert managers.manager_of(_npm()) == "nvm"


def test_manager_of_is_none_for_types_with_no_manager():
    tool = GenericTool(key="docker", install_type="apt", check_cmd="docker")
    bash = GenericTool(key="x", install_type="bash", check_cmd="x")

    assert managers.manager_of(tool) is None
    assert managers.manager_of(bash) is None


def test_manager_of_is_none_when_the_manager_is_not_in_the_registry():
    tool = GenericTool(
        key="x", install_type="bash", managed_by="nope", check_cmd="x"
    )

    assert managers.manager_of(tool) is None


def test_manager_of_is_none_on_self_reference():
    tool = GenericTool(key="uv", install_type="bash", managed_by="uv", check_cmd="uv")

    assert managers.manager_of(tool) is None


# -- uv type signal -------------------------------------------------------------


def test_uvx_in_uv_tool_list_is_managed(monkeypatch):
    monkeypatch.setattr(generic, "_uv_tool_versions", _uv_tools({"ipython": "9.0"}))

    note = managers.note_for(_uvx(), installed=True)

    assert note.state == "managed" and note.manager == "uv"


def test_uvx_installed_but_absent_from_uv_is_outside(monkeypatch):
    # The commitizen case: `cz` resolves (repo venv) but isn't a uv tool.
    monkeypatch.setattr(generic, "_uv_tool_versions", _uv_tools({"hey-dave": "1.0"}))
    monkeypatch.setattr(shutil, "which", lambda n: "/repo/.venv/bin/cz" if n == "cz" else None)
    tool = _uvx(key="commitizen", pip_name="commitizen", check_cmd="cz")

    note = managers.note_for(tool, installed=True)

    assert note.state == "outside"
    assert note.path == os.path.realpath("/repo/.venv/bin/cz")


def test_uvx_outside_when_uv_itself_is_missing(monkeypatch):
    monkeypatch.setattr(generic, "_uv_tool_versions", _uv_tools(None, "uv is not installed"))
    monkeypatch.setattr(shutil, "which", lambda n: f"/usr/bin/{n}")

    note = managers.note_for(_uvx(), installed=True)

    # No uv means uv manages nothing — a definitive outside, not "unknown".
    assert note.state == "outside"


def test_uv_tool_list_failure_is_unknown_never_managed(monkeypatch):
    monkeypatch.setattr(generic, "_uv_tool_versions", _uv_tools(None, "`uv tool list` failed"))

    note = managers.note_for(_uvx(), installed=True)

    assert note.state == "unknown" and "uv tool list" in note.why


def test_uvx_not_installed_is_can_manage(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda n: None)

    note = managers.note_for(_uvx(), installed=False)

    assert note.state == "can-manage" and note.manager == "uv"


# -- npm type signal -------------------------------------------------------------


def test_npm_in_nvm_globals_is_managed(monkeypatch):
    monkeypatch.setattr(
        managers, "_nvm_npm_globals", _nvm_globals({"@openai/codex": "1.0"})
    )

    note = managers.note_for(_npm(), installed=True)

    assert note.state == "managed" and note.manager == "nvm"


def test_npm_installed_but_absent_from_nvm_is_outside(monkeypatch):
    monkeypatch.setattr(managers, "_nvm_npm_globals", _nvm_globals({"npm": "11.0"}))
    monkeypatch.setattr(shutil, "which", lambda n: f"/usr/bin/{n}")

    note = managers.note_for(_npm(), installed=True)

    assert note.state == "outside" and note.path == os.path.realpath("/usr/bin/codex")


def test_npm_outside_when_nvm_is_missing(monkeypatch):
    monkeypatch.setattr(
        managers, "_nvm_npm_globals", _nvm_globals(None, "nvm is not installed")
    )
    monkeypatch.setattr(shutil, "which", lambda n: f"/usr/bin/{n}")

    note = managers.note_for(_npm(), installed=True)

    assert note.state == "outside"


# -- the real _nvm_npm_globals probe --------------------------------------------


def _fake_nvm(tmp_path, monkeypatch):
    nvm_dir = tmp_path / "nvm"
    nvm_dir.mkdir()
    (nvm_dir / "nvm.sh").write_text("# stub\n")
    monkeypatch.setenv("NVM_DIR", str(nvm_dir))
    return nvm_dir


def test_nvm_globals_parse_valid_json_despite_a_nonzero_exit(tmp_path, monkeypatch):
    _fake_nvm(tmp_path, monkeypatch)
    payload = json.dumps({"name": "lib", "dependencies": {"@openai/codex": {"version": "1.0"}}})
    monkeypatch.setattr(
        generic, "_probe", lambda *a, **k: SimpleNamespace(returncode=1, stdout=payload)
    )

    answer = managers._nvm_npm_globals()

    assert answer.data == {"@openai/codex": "1.0"}


def test_nvm_globals_unparseable_output_is_unknown(tmp_path, monkeypatch):
    _fake_nvm(tmp_path, monkeypatch)
    monkeypatch.setattr(
        generic, "_probe", lambda *a, **k: SimpleNamespace(returncode=0, stdout="not json")
    )

    answer = managers._nvm_npm_globals()

    assert answer.data is None and answer.why


def test_nvm_globals_without_nvm_reports_absent(tmp_path, monkeypatch):
    monkeypatch.setenv("NVM_DIR", str(tmp_path / "no-nvm"))

    answer = managers._nvm_npm_globals()

    assert answer.data is None and answer.why == "nvm is not installed"


# -- path signal (explicit managed_by: python, pi) --------------------------------


def _python():
    return GenericTool(
        key="python",
        install_type="bash",
        managed_by="uv",
        version_cmd="python3",
        check_cmd="d=$(uv python dir); case x in esac",  # compound, like the catalog
    )


def test_python_inside_uv_python_dir_is_managed(tmp_path, monkeypatch):
    root = tmp_path / "uv" / "python"
    exe = root / "cpython-3.14" / "bin" / "python3"
    exe.parent.mkdir(parents=True)
    exe.touch()
    monkeypatch.setitem(
        managers._ROOT_PROBES, "uv", lambda: generic._Answer({os.path.realpath(root): ""})
    )
    monkeypatch.setattr(shutil, "which", lambda n: str(exe) if n == "python3" else None)

    note = managers.note_for(_python(), installed=True)

    assert note.state == "managed"


def test_python_in_a_sibling_dir_is_outside_not_fooled_by_prefix(tmp_path, monkeypatch):
    root = tmp_path / "uv" / "python"
    exe = tmp_path / "uv" / "python-other" / "bin" / "python3"
    exe.parent.mkdir(parents=True)
    exe.touch()
    monkeypatch.setitem(
        managers._ROOT_PROBES, "uv", lambda: generic._Answer({os.path.realpath(root): ""})
    )
    monkeypatch.setattr(shutil, "which", lambda n: str(exe) if n == "python3" else None)

    note = managers.note_for(_python(), installed=True)

    assert note.state == "outside"
    # The discriminating fact: a naive prefix check would have said managed.
    assert os.path.realpath(exe).startswith(os.path.realpath(root))


def test_python_uninstalled_with_a_system_copy_is_flagged(tmp_path, monkeypatch):
    monkeypatch.setattr(
        shutil, "which", lambda n: "/usr/bin/python3.14" if n == "python3" else None
    )

    note = managers.note_for(_python(), installed=False)

    assert note.state == "system-copy"
    assert note.executable == "python3"
    assert note.path == os.path.realpath("/usr/bin/python3.14")


def test_python_uninstalled_with_no_python3_is_can_manage(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda n: None)

    note = managers.note_for(_python(), installed=False)

    assert note.state == "can-manage"


def test_python_outside_when_uv_is_missing(monkeypatch):
    monkeypatch.setitem(managers._ROOT_PROBES, "uv", lambda: generic._Answer({}))

    note = managers.note_for(_python(), installed=True)

    assert note.state == "outside"


def test_python_unknown_when_the_uv_dir_probe_fails(monkeypatch):
    monkeypatch.setitem(
        managers._ROOT_PROBES, "uv",
        lambda: generic._Answer(None, "`uv tool dir` failed"),
    )

    note = managers.note_for(_python(), installed=True)

    assert note.state == "unknown" and "uv tool dir" in note.why


def test_pi_resolved_only_through_nvm_is_managed(tmp_path, monkeypatch):
    nvm_root = tmp_path / "nvm"
    pi = nvm_root / "versions" / "node" / "v22" / "bin" / "pi"
    pi.parent.mkdir(parents=True)
    pi.touch()
    tool = GenericTool(
        key="pi",
        install_type="bash",
        managed_by="nvm",
        version_cmd="pi",
        check_cmd='. "$HOME/.nvm/nvm.sh" && command -v pi',  # compound
    )
    monkeypatch.setitem(
        managers._ROOT_PROBES, "nvm",
        lambda: generic._Answer({os.path.realpath(nvm_root): ""}),
    )
    # pi is NOT directly on PATH — only an nvm-sourced shell finds it.
    monkeypatch.setattr(shutil, "which", lambda n: None)

    def fake_probe(cmd, **kw):
        assert "nvm.sh" in cmd[2] and "command -v pi" in cmd[2]
        return SimpleNamespace(returncode=0, stdout=str(pi) + "\n")

    monkeypatch.setattr(generic, "_probe", fake_probe)

    note = managers.note_for(tool, installed=True)

    assert note.state == "managed" and note.manager == "nvm"


def test_path_signal_unknown_when_the_executable_cannot_be_found(tmp_path, monkeypatch):
    monkeypatch.setitem(
        managers._ROOT_PROBES, "uv",
        lambda: generic._Answer({os.path.realpath(tmp_path): ""}),
    )
    monkeypatch.setattr(shutil, "which", lambda n: None)

    note = managers.note_for(_python(), installed=True)

    assert note.state == "unknown" and "python3" in note.why


def test_managed_by_a_key_with_no_probe_is_unknown(monkeypatch, tmp_path):
    # managed_by may name any catalog key; only uv/nvm have roots. An unknown
    # verdict must surface as unknown — never silently "managed".
    monkeypatch.setattr(registry, "exists", lambda k: True)
    tool = GenericTool(
        key="x", install_type="bash", managed_by="docker", check_cmd="x"
    )

    note = managers.note_for(tool, installed=True)

    assert note.state == "unknown"


# -- safety ----------------------------------------------------------------------


def test_note_for_never_raises_on_a_broken_probe(monkeypatch):
    def boom():
        raise RuntimeError("probe exploded")

    monkeypatch.setattr(generic, "_uv_tool_versions", boom)

    assert managers.note_for(_uvx(), installed=True) is None


# -- rendering --------------------------------------------------------------------


def test_list_line_escapes_machine_paths(monkeypatch):
    # A path holding "[x]" is valid Rich markup syntax; it must render literally.
    tool = _uvx(key="commitizen", pip_name="commitizen", check_cmd="cz")
    note = managers.ManagerNote("uv", "outside", path="/opt/[x]/bin/cz")
    out = io.StringIO()
    Console(file=out, force_terminal=False, color_system=None, width=200).print(
        managers.list_line(note, tool)
    )

    rendered = out.getvalue()
    assert "[x]" in rendered
    assert "installed outside uv" in rendered


def test_list_line_covers_every_state():
    tool = _uvx()
    cases = {
        "managed": "✓ managed by uv",
        "outside": "installed outside uv",
        "can-manage": "uv can manage this",
        "system-copy": "isn't managed by uv",
        "unknown": "couldn't confirm",
    }
    for state, text in cases.items():
        note = managers.ManagerNote(
            "uv", state, path="/p" if state in ("outside", "system-copy") else "",
            executable="x" if state == "system-copy" else "",
            why="why" if state == "unknown" else "",
        )
        assert text in managers.list_line(note, tool)


def test_picker_hint_covers_every_state():
    tool = _uvx()
    assert managers.picker_hint(managers.ManagerNote("uv", "managed"), tool) == (
        "", "installed · managed by uv"
    )
    assert managers.picker_hint(managers.ManagerNote("uv", "outside"), tool) == (
        "", "installed outside uv"
    )
    assert managers.picker_hint(managers.ManagerNote("uv", "can-manage"), tool) == (
        "  (via uv)", None
    )
    assert managers.picker_hint(
        managers.ManagerNote("uv", "system-copy", executable="python3"), tool
    ) == ("  (python3 found outside uv)", None)
    # Unknown keeps the default disabled text — never a positive claim.
    assert managers.picker_hint(managers.ManagerNote("uv", "unknown"), tool) == ("", None)


# -- list command ------------------------------------------------------------------


class _Row:
    """Minimal tool for the list/picker fakes — methods the commands call."""

    def __init__(self, key, installed=False, category="tools", install_type="uvx"):
        self.key = key
        self.name = key
        self.description = f"the {key} tool"
        self.category = category
        self.install_type = install_type
        self.help_cmd = ""
        self.installed = installed

    def is_installed(self):
        return self.installed

    def get_version(self):
        return "1.0" if self.installed else ""


def _list_world(monkeypatch, rows, notes):
    monkeypatch.setattr(registry, "all_tools", lambda: list(rows))
    monkeypatch.setattr(registry, "missing_requires", lambda t: [])
    monkeypatch.setattr(
        managers, "note_for", lambda t, inst: notes.get(t.key)
    )


def _flat(text: str) -> str:
    return " ".join("".join(c for c in text if c not in "│╭╮╰╯─├┤┬┴┼╼╾").split())


def test_list_shows_every_note_state(monkeypatch):
    rows = [
        _Row("man", installed=True),
        _Row("out", installed=True),
        _Row("sys"),
        _Row("can"),
        _Row("unk", installed=True),
    ]
    notes = {
        "man": managers.ManagerNote("uv", "managed"),
        "out": managers.ManagerNote("uv", "outside", path="/x"),
        "sys": managers.ManagerNote("uv", "system-copy", executable="python3", path="/usr/bin/python3.14"),
        "can": managers.ManagerNote("uv", "can-manage"),
        "unk": managers.ManagerNote("uv", "unknown", why="probe failed"),
    }
    _list_world(monkeypatch, rows, notes)
    from click.testing import CliRunner

    result = CliRunner().invoke(cli, ["list"])

    assert result.exit_code == 0, result.output
    flat = _flat(result.output)
    assert "✓ managed by uv" in flat
    assert "installed outside uv" in flat
    assert "isn't managed by uv" in flat
    assert "can manage this" in flat
    assert "couldn't confirm" in flat


def test_list_installed_and_available_filters_keep_the_notes(monkeypatch):
    rows = [_Row("man", installed=True), _Row("can")]
    notes = {
        "man": managers.ManagerNote("uv", "managed"),
        "can": managers.ManagerNote("uv", "can-manage"),
    }
    _list_world(monkeypatch, rows, notes)
    from click.testing import CliRunner

    installed = _flat(CliRunner().invoke(cli, ["list", "--installed"]).output)
    available = _flat(CliRunner().invoke(cli, ["list", "--available"]).output)

    assert "managed by uv" in installed and "can manage this" not in installed
    assert "can manage this" in available and "managed by uv" not in available


# -- install picker -----------------------------------------------------------------


def test_picker_shows_manager_text_and_suffixes(monkeypatch):
    rows = [
        _Row("man", installed=True),
        _Row("out", installed=True),
        _Row("unk", installed=True),
        _Row("can"),
        _Row("sys"),
        _Row("blocked"),
    ]
    notes = {
        "man": managers.ManagerNote("uv", "managed"),
        "out": managers.ManagerNote("uv", "outside", path="/x"),
        "unk": managers.ManagerNote("uv", "unknown", why="w"),
        "can": managers.ManagerNote("uv", "can-manage"),
        "sys": managers.ManagerNote("uv", "system-copy", executable="python3", path="/usr/bin/python3.14"),
    }
    _list_world(monkeypatch, rows, notes)
    monkeypatch.setattr(
        registry, "missing_requires", lambda t: ["dep"] if t.key == "blocked" else []
    )
    seen = {}

    def checkbox(prompt, choices, **kw):
        for c in choices:
            if hasattr(c, "value"):
                seen[c.value] = c
        return []

    monkeypatch.setattr(ui, "checkbox", checkbox)
    from click.testing import CliRunner

    result = CliRunner().invoke(cli, ["install"])

    assert result.exit_code == 0, result.output
    assert seen["man"].disabled == "installed · managed by uv"
    assert seen["out"].disabled == "installed outside uv"
    # unknown keeps the honest default, never a positive claim.
    assert seen["unk"].disabled == "installed"
    assert "(via uv)" in seen["can"].title
    assert "(python3 found outside uv)" in seen["sys"].title
    # requires-disabled rows keep their text; a note adds only a suffix.
    assert seen["blocked"].disabled == "requires dep"
