from __future__ import annotations

import os
import shutil
import stat
import subprocess
import tempfile
import types
from pathlib import Path

import pytest

from dev_setup import configure
from dev_setup.configure.btop import capability, detect, render, validate, wizard
from dev_setup.configure.btop.model import (
    BOXES,
    GPU_BOXES,
    PRESETS,
    SETTINGS,
    BtopConfig,
)

HAS_BTOP = shutil.which("btop") is not None
needs_btop = pytest.mark.skipif(not HAS_BTOP, reason="btop is not installed")

HEADER_146 = "#? Config file for btop v.1.4.6"
HEADER_130 = "#? Config file for btop v. 1.3.0"


def cfg(**kwargs) -> BtopConfig:
    return BtopConfig(**kwargs)


def found(**kwargs) -> detect.Btop:
    """A detection result as it would look for a 1.4.6 install, overridable."""
    base = detect.Btop(
        installed=True,
        binary="/usr/bin/btop",
        version="1.4.6",
        header=HEADER_146,
        keys=frozenset(SETTINGS) | {"shown_gpus"},
        defaults={"update_ms": "2000", "vim_keys": "false", "color_theme": '"Default"'},
        themes=("Default", "TTY", "nord", "dracula"),
    )
    for key, value in kwargs.items():
        setattr(base, key, value)
    return base


def existing(text: str, **kwargs) -> detect.Btop:
    f = found(**kwargs)
    f.existing_text = text
    f.existing = render.values(text)
    f.existing_raw = {
        line.key: line.raw_value for line in render.parse(text) if line.key is not None
    }
    return f


# -- the model's own invariants -------------------------------------------------


def test_configurator_is_registered():
    spec = configure.get("btop")
    assert spec is not None
    module = spec.load()
    assert callable(module.run) and callable(module.config_path)


def test_setting_defaults_match_the_configs_own_defaults():
    """The splice omits a value equal to `Setting.default`, so a drift would make
    the wizard stop writing a setting the user did choose."""
    blank = BtopConfig()
    for key, setting in SETTINGS.items():
        assert getattr(blank, key) == setting.default, key


def test_enum_defaults_are_in_their_choices():
    for key, setting in SETTINGS.items():
        if setting.kind == "enum":
            assert setting.default in setting.choices, key


def test_enum_choices_are_what_btops_own_comments_list():
    """The comment over each key in `btop --default-config` is the source."""
    assert SETTINGS["graph_symbol"].choices == ("braille", "block", "tty")
    assert SETTINGS["proc_sorting"].choices == (
        "pid", "program", "arguments", "threads", "user", "memory",
        "cpu lazy", "cpu direct",
    )
    assert SETTINGS["temp_scale"].choices == (
        "celsius", "fahrenheit", "kelvin", "rankine",
    )


def test_every_preset_names_real_settings_and_valid_values():
    allowed_boxes = set(BOXES) | set(GPU_BOXES)
    for preset in PRESETS.values():
        for name, value in preset.values.items():
            setting = SETTINGS[name]  # KeyError names the bad key
            if setting.kind == "enum":
                assert value in setting.choices, f"{preset.key}: {name}={value}"
            elif setting.kind == "bool":
                assert isinstance(value, bool), f"{preset.key}: {name}"
            elif setting.kind == "int":
                assert isinstance(value, int), f"{preset.key}: {name}"
                assert setting.minimum <= value <= setting.maximum
            elif setting.kind == "boxes":
                assert all(t in allowed_boxes for t in str(value).split())


def test_no_preset_sets_save_config_on_exit():
    """That key changes how btop treats the file itself — it must only ever be the
    user's explicit answer."""
    for preset in PRESETS.values():
        assert "save_config_on_exit" not in preset.values, preset.key


# -- parse ----------------------------------------------------------------------


def test_parse_quoted_bare_bool_int_and_word():
    lines = {
        line.key: line
        for line in render.parse(
            'color_theme = "nord"\nvim_keys = True\nother = false\n'
            "update_ms = 2000\ncolor_theme2x = nord\n"
        )
        if line.key
    }
    assert lines["color_theme"].value == "nord"
    assert lines["vim_keys"].value is True
    assert lines["other"].value is False
    assert lines["update_ms"].value == 2000
    assert lines["color_theme2x"].value == "nord"


def test_parse_strips_a_trailing_comment():
    (line,) = [ln for ln in render.parse('clock_format = "%H:%M" # trailing') if ln.key]
    assert line.value == "%H:%M"


def test_parse_strips_a_trailing_comment_on_a_bare_value():
    (line,) = [ln for ln in render.parse("update_ms = 2000 # faster") if ln.key]
    assert line.value == 2000


def test_a_string_ends_at_the_second_quote():
    """`clock_format = "a"b"` is saved by btop as `"a"` — a quote inside a string
    value cannot be written (measured)."""
    (line,) = [ln for ln in render.parse('clock_format = "a"b"') if ln.key]
    assert line.value == "a"


def test_parse_keeps_comments_and_blank_lines_verbatim():
    text = "#? header\n#* a comment\n\nkey = 1\n"
    lines = render.parse(text)
    assert [ln.raw for ln in lines] == text.splitlines()
    assert [ln.key for ln in lines[:3]] == [None, None, None]


def test_parse_tolerates_missing_spaces():
    (line,) = [ln for ln in render.parse("update_ms=2000") if ln.key]
    assert line.value == 2000


# -- the splice -----------------------------------------------------------------


def test_splice_replaces_only_the_changed_value():
    base = (
        f"{HEADER_146}\n\n#* comment stays\nupdate_ms = 2000\nunknown_key = 7\n"
    )
    with tempfile.TemporaryDirectory() as td:
        target = Path(td) / "btop.conf"
        target.write_text(base)
        out = render.to_text(cfg(target=target, update_ms=3000), found())
    assert out == base.replace("update_ms = 2000", "update_ms = 3000")


def test_splice_leaves_every_other_byte_identical():
    base = (
        f"{HEADER_146}\n# a comment\nupdate_ms = 2000\n"
        'color_theme = "Default"\nweird = "x = y"\nvim_keys = false\n'
    )
    with tempfile.TemporaryDirectory() as td:
        target = Path(td) / "btop.conf"
        target.write_text(base)
        out = render.to_text(cfg(target=target, color_theme="dracula"), found())
    assert out == base.replace('color_theme = "Default"', 'color_theme = "dracula"')


def test_splice_repairs_an_invalid_existing_value():
    base = f'{HEADER_146}\nvim_keys = yes\n'
    with tempfile.TemporaryDirectory() as td:
        target = Path(td) / "btop.conf"
        target.write_text(base)
        out = render.to_text(cfg(target=target), found())
    assert "vim_keys = false" in out


def test_an_equal_value_in_another_form_is_left_alone():
    """1.3.0 writes `True`; the same boolean must not churn the line."""
    base = f"{HEADER_130}\nvim_keys = True\n"
    with tempfile.TemporaryDirectory() as td:
        target = Path(td) / "btop.conf"
        target.write_text(base)
        out = render.to_text(cfg(target=target, vim_keys=True), found())
        assert out == base
        # …but a genuinely different value is still rewritten in place.
        out = render.to_text(cfg(target=target, vim_keys=False), found())
    assert "vim_keys = false" in out


def test_only_non_default_absent_keys_are_appended():
    with tempfile.TemporaryDirectory() as td:
        target = Path(td) / "btop.conf"
        target.write_text(f"{HEADER_146}\n")
        out = render.to_text(cfg(target=target, update_ms=3000), found())
    assert "update_ms = 3000" in out
    assert "vim_keys" not in out  # vim_keys=False is the default — not written


def test_keys_unknown_to_this_binary_are_never_written():
    f = found(keys=frozenset({"update_ms"}), defaults={"update_ms": "2000"})
    with tempfile.TemporaryDirectory() as td:
        target = Path(td) / "btop.conf"
        target.write_text(f"{HEADER_146}\nsave_config_on_exit = false\n")
        out = render.to_text(
            cfg(target=target, update_ms=3000, save_config_on_exit=False), f
        )
    assert out.count("save_config_on_exit") == 1  # the existing line, verbatim
    assert "update_ms = 3000" in out


def test_a_new_file_gets_this_binarys_header_verbatim():
    for header in (HEADER_146, HEADER_130):
        with tempfile.TemporaryDirectory() as td:
            out = render.to_text(
                cfg(target=Path(td) / "btop.conf"), found(header=header)
            )
        assert out.splitlines()[0] == header


def test_a_new_file_has_no_header_when_the_binarys_is_unknown():
    with tempfile.TemporaryDirectory() as td:
        out = render.to_text(cfg(target=Path(td) / "btop.conf"), found(header=""))
    assert not out.startswith("#?")


def test_matches_detects_a_mismatch():
    f = found()
    with tempfile.TemporaryDirectory() as td:
        target = Path(td) / "btop.conf"
        good = cfg(target=target, update_ms=3000)
        text = render.to_text(good, f)
        assert render.matches(text, good, f)
        bad = cfg(target=target, update_ms=4000)
        assert not render.matches(text, bad, f)
        assert not render.matches(text.replace("update_ms", "update_zz"), good, f)


def test_values_reads_back_a_file():
    assert render.values('a = 1\nb = "x"\n') == {"a": 1, "b": "x"}


# -- from_existing --------------------------------------------------------------


def test_from_existing_reads_values_back():
    f = existing(f'{HEADER_146}\nupdate_ms = 5000\nvim_keys = true\n')
    config = detect.from_existing(f)
    assert config.preset == "current"
    assert config.update_ms == 5000 and config.vim_keys is True


def test_from_existing_resets_what_btop_would_reject():
    f = existing(
        f'{HEADER_146}\nupdate_ms = "abc"\nvim_keys = yes\nshown_boxes = "cpu foo"\n'
    )
    config = detect.from_existing(f)
    assert config.update_ms == 2000
    assert config.vim_keys is False
    assert config.shown_boxes == "cpu mem net proc"
    keys = {key for key, _raw, _why in f.invalid_existing}
    assert keys == {"update_ms", "vim_keys", "shown_boxes"}


def test_from_existing_resets_an_out_of_range_int():
    f = existing(f"{HEADER_146}\nupdate_ms = 99\n")
    config = detect.from_existing(f)
    assert config.update_ms == 2000
    assert f.invalid_existing[0][0] == "update_ms"


def test_an_unknown_enum_string_is_kept_for_verify_to_flag():
    """`proc_sorting = "Memory"` is kept by btop silently — carrying it into cfg is
    what lets verify name it rather than silently fixing it."""
    f = existing(f'{HEADER_146}\nproc_sorting = "Memory"\n')
    config = detect.from_existing(f)
    assert config.proc_sorting == "Memory"
    assert not f.invalid_existing
    assert not validate.verify(config, f).ok


def test_an_empty_shown_boxes_is_kept_for_verify_to_flag():
    f = existing(f'{HEADER_146}\nshown_boxes = ""\n')
    config = detect.from_existing(f)
    assert config.shown_boxes == ""
    assert not validate.verify(config, f).ok


# -- verify ----------------------------------------------------------------------


def _failed(report: validate.Report) -> list[validate.Check]:
    return [c for c in report.checks if not c.ok]


def test_verify_passes_a_clean_config():
    assert validate.verify(cfg(), found()).ok


def test_verify_catches_an_out_of_range_int():
    assert _failed(validate.verify(cfg(update_ms=50), found()))
    assert _failed(validate.verify(cfg(update_ms=86400001), found()))


def test_verify_catches_a_bad_enum():
    report = validate.verify(cfg(proc_sorting="bogus"), found())
    assert any("enum" in c.name for c in _failed(report))


def test_verify_catches_an_unknown_theme():
    report = validate.verify(cfg(color_theme="nonexistent"), found())
    assert any("theme" in c.name for c in _failed(report))


def test_verify_says_nothing_about_themes_it_cannot_list():
    report = validate.verify(cfg(color_theme="nonexistent"), found(themes=()))
    assert all(c.ok for c in report.checks if "theme" in c.name)


def test_verify_catches_empty_boxes():
    report = validate.verify(cfg(shown_boxes=""), found())
    assert any("No boxes shown" in c.detail for c in _failed(report))


def test_verify_catches_a_gpu_box_without_gpu_keys():
    f = found(keys=frozenset(SETTINGS))  # no shown_gpus
    report = validate.verify(cfg(shown_boxes="cpu gpu0"), f)
    assert any("shown_boxes" in c.name for c in _failed(report))


def test_verify_accepts_a_gpu_box_on_a_gpu_build():
    report = validate.verify(cfg(shown_boxes="cpu gpu0"), found())
    assert all(c.ok for c in report.checks if c.name == "shown_boxes")


def test_verify_catches_an_unwritable_string():
    report = validate.verify(cfg(clock_format='a"b'), found())
    assert _failed(report)


def test_verify_flags_a_setting_this_build_cannot_hold():
    """On 1.3.0 `save_config_on_exit` does not exist: wanting a non-default for it
    is reported, not silently dropped."""
    f = found(keys=frozenset(k for k in SETTINGS if k != "save_config_on_exit"))
    report = validate.verify(cfg(save_config_on_exit=False), f)
    assert any("save_config_on_exit" in c.detail for c in _failed(report))


# -- rejections / live result framing --------------------------------------------


def test_rejections_reports_a_reset_value():
    candidate = f'{HEADER_146}\nupdate_ms = "abc"\n'
    after = f'{HEADER_146}\nupdate_ms = 2000\n'
    (rej,) = validate.rejections(candidate, after)
    assert rej.key == "update_ms" and rej.wrote == '"abc"' and rej.kept == "2000"


def test_rejections_reports_a_key_that_vanished_from_a_rewrite():
    candidate = f"{HEADER_146}\nnot_a_key = 1\n"
    after = f"{HEADER_146}\nupdate_ms = 2000\n"
    (rej,) = validate.rejections(candidate, after)
    assert rej.key == "not_a_key" and rej.kept == ""


def test_rejections_never_flags_a_key_from_a_file_btop_left_alone():
    """A header-carrying candidate that comes back byte-identical is a file btop
    left alone — "rewritten" is `after != candidate`, not the `#?` header, which
    our own candidates carry too."""
    candidate = "#? Config file for btop v.1.4.7\nmy_own_key = 1\n"
    assert validate.rejections(candidate, candidate) == []


def test_rejections_flags_a_vanished_key_without_a_header_signal():
    """And the header itself is not the discriminator: a rewritten file lacking
    a candidate key is a rejection whatever its first line says."""
    candidate = f"{HEADER_146}\nnot_a_key = 1\n"
    after = f"{HEADER_146}\nupdate_ms = 2000\n"
    assert [r.key for r in validate.rejections(candidate, after)] == ["not_a_key"]


def test_a_log_warning_alone_is_not_a_rejection():
    """The GPU build logs 'Intel GPU: Failed to initialize PMU' on every start —
    the value diff is the signal, not the log."""
    candidate = f"{HEADER_146}\nupdate_ms = 2000\n"
    log = "WARNING: Intel GPU: Failed to initialize PMU\n"
    assert validate.rejections(candidate, candidate, log) == []


def test_a_rejection_carries_the_matching_log_lines():
    candidate = f'{HEADER_146}\nupdate_ms = "abc"\n'
    after = f"{HEADER_146}\nupdate_ms = 2000\n"
    log = 'WARNING: Got an invalid integer value for config name: update_ms\n'
    (rej,) = validate.rejections(candidate, after, log)
    assert rej.log == (log.strip(),)


def _console_out(monkeypatch):
    import io

    from rich.console import Console

    out = io.StringIO()
    monkeypatch.setattr(
        wizard.ui, "console", Console(file=out, force_terminal=False, width=120)
    )
    return out


def test_check_details_with_markup_shaped_values_render(monkeypatch):
    """Outside text — a value like `[/x]` is a Rich closing tag and would raise
    MarkupError unescaped; `[bold]` would silently vanish. Escaping happens once,
    at display in `_show_checks`."""
    f = existing(f'{HEADER_146}\ncolor_theme = "[/x]"\nclock_format = "[bold]"\n')
    config = detect.from_existing(f)
    report = validate.verify(config, f)
    assert not report.ok  # the theme check names the bad value
    out = _console_out(monkeypatch)
    assert not wizard._show_checks(report)
    assert "[/x]" in out.getvalue()  # literal — escaped, not parsed as markup

    # A detail carrying `[bold]` would render as nothing without the escape.
    report = validate.Report(
        "1.4.6", [validate.Check("x", False, "value [bold] here")]
    )
    out = _console_out(monkeypatch)
    wizard._show_checks(report)
    assert "[bold]" in out.getvalue()


def test_a_markup_shaped_output_tail_renders(monkeypatch):
    result = validate.LiveResult(
        started=False, exit_code=1, output="oops [/usr/bin/x] blew up"
    )
    report = validate.live_report(result, found())
    out = _console_out(monkeypatch)
    assert not wizard._show_checks(report)
    assert "[/usr/bin/x]" in out.getvalue()


# -- detect ----------------------------------------------------------------------


def test_config_dir_honours_xdg_config_home(monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", "/xdg/cfg")
    assert detect.config_dir() == Path("/xdg/cfg/btop")
    monkeypatch.delenv("XDG_CONFIG_HOME")
    monkeypatch.setenv("HOME", "/home/me")
    assert detect.config_dir() == Path("/home/me/.config/btop")


def test_themes_scans_the_directories_btop_reads(tmp_path, monkeypatch):
    binary = tmp_path / "usr" / "bin" / "btop"
    binary.parent.mkdir(parents=True)
    binary.write_text("")
    themes_dir = tmp_path / "usr" / "share" / "btop" / "themes"
    themes_dir.mkdir(parents=True)
    (themes_dir / "nord.theme").write_text("")
    (themes_dir / "dracula.theme").write_text("")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    user_dir = tmp_path / "home" / ".config" / "btop" / "themes"
    user_dir.mkdir(parents=True)
    (user_dir / "mine.theme").write_text("")
    found_ = detect.Btop(binary=str(binary))
    themes = detect.themes(found_)
    assert themes[:2] == ("Default", "TTY")
    assert "nord" in themes and "dracula" in themes and "mine" in themes


def test_running_finds_own_btop_only(tmp_path, monkeypatch):
    for pid, comm in ((4242, "btop"), (4243, "bash")):
        d = tmp_path / str(pid)
        d.mkdir()
        (d / "comm").write_text(comm)
    monkeypatch.setattr(os, "getuid", lambda: os.stat(tmp_path / "4242").st_uid)
    assert detect.running(tmp_path) == [4242]
    # A btop owned by someone else does not count — with a foreign uid every fake
    # proc dir reads as other-owned.
    monkeypatch.setattr(os, "getuid", lambda: 999999)
    assert detect.running(tmp_path) == []


def test_version_parses_ansi_and_the_plain_form(monkeypatch):
    def fake_run(cmd):
        return subprocess.CompletedProcess(
            cmd, 0, stdout="btop version: \x1b[1m1.4.6\x1b[0m\n", stderr=""
        )

    monkeypatch.setattr(detect, "_run", fake_run)
    assert detect.version("/x/btop") == "1.4.6"

    monkeypatch.setattr(
        detect, "_run",
        lambda cmd: subprocess.CompletedProcess(
            cmd, 0, stdout="btop version: 1.3.0\nCompiled with:\n", stderr=""
        ),
    )
    assert detect.version("/x/btop") == "1.3.0"


def test_defaults_fall_back_to_a_first_run_probe(monkeypatch, tmp_path):
    """1.3.0 has no `--default-config`; the file its first run writes is the
    same thing."""
    monkeypatch.setattr(
        detect, "_run",
        lambda cmd: subprocess.CompletedProcess(cmd, 1, stdout="", stderr=""),
    )

    def fake_pty(binary, home, wait=0):
        conf = home / ".config" / "btop" / "btop.conf"
        conf.parent.mkdir(parents=True)
        conf.write_text(f'{HEADER_130}\nupdate_ms = 2000\n')
        return validate.PtyRun(0, b"x" * 1000, True)

    monkeypatch.setattr(validate, "run_pty", fake_pty)
    text = detect.defaults_text("/x/btop")
    header, keys, defaults = detect._parse_defaults(text)
    assert header == HEADER_130 and keys == frozenset({"update_ms"})


def test_defaults_failure_means_keys_unknown(monkeypatch):
    monkeypatch.setattr(
        detect, "_run",
        lambda cmd: subprocess.CompletedProcess(cmd, 1, stdout="", stderr=""),
    )
    monkeypatch.setattr(detect, "_first_run_config", lambda binary: "")
    assert detect.defaults_text("/x/btop") == ""


def test_on_slow_fires_only_on_the_first_run_fallback(monkeypatch):
    fired = []
    monkeypatch.setattr(
        detect, "_run",
        lambda cmd: subprocess.CompletedProcess(cmd, 0, stdout="#? hdr\n", stderr=""),
    )
    assert detect.defaults_text("/x/btop", on_slow=fired.append) == "#? hdr\n"
    assert fired == []  # the fast path never warns about a pause it didn't take

    monkeypatch.setattr(
        detect, "_run",
        lambda cmd: subprocess.CompletedProcess(cmd, 1, stdout="", stderr=""),
    )
    monkeypatch.setattr(detect, "_first_run_config", lambda binary: "text")
    assert detect.defaults_text("/x/btop", on_slow=fired.append) == "text"
    assert len(fired) == 1 and "hidden terminal" in fired[0]


# -- capability ------------------------------------------------------------------


def test_refusal_when_setcap_is_missing(monkeypatch):
    monkeypatch.setattr(capability, "tool", lambda name: None)
    assert "setcap" in capability.refusal("/usr/bin/btop")


def test_refusal_for_a_binary_not_owned_by_root(tmp_path, monkeypatch):
    binary = tmp_path / "btop"
    binary.write_text("x")
    monkeypatch.setattr(capability, "tool", lambda name: "/usr/sbin/setcap")
    assert "root" in capability.refusal(str(binary))


def test_refusal_for_a_group_writable_binary(tmp_path, monkeypatch):
    binary = tmp_path / "btop"
    binary.write_text("x")
    monkeypatch.setattr(capability, "tool", lambda name: "/usr/sbin/setcap")

    fake_stat = types.SimpleNamespace(st_uid=0, st_mode=stat.S_IFREG | 0o775)
    monkeypatch.setattr(capability.os, "stat", lambda p: fake_stat)
    assert "writable" in capability.refusal(str(binary))

    fake_stat.st_mode = stat.S_IFREG | 0o755
    assert capability.refusal(str(binary)) is None


def test_has_perfmon_reads_getcap(monkeypatch):
    monkeypatch.setattr(capability, "tool", lambda name: "/usr/sbin/getcap")
    monkeypatch.setattr(
        capability.subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(
            a[0], 0, stdout="/usr/bin/btop cap_perfmon=ep\n", stderr=""
        ),
    )
    assert capability.has_perfmon("/usr/bin/btop") is True
    monkeypatch.setattr(
        capability.subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 0, stdout="", stderr=""),
    )
    assert capability.has_perfmon("/usr/bin/btop") is False


def test_has_perfmon_reads_combined_and_old_getcap_forms(monkeypatch):
    """`cap_perfmon=` alone misses a combined capability set and the older
    `= cap_perfmon+ep` getcap form."""
    monkeypatch.setattr(capability, "tool", lambda name: "/usr/sbin/getcap")

    def out(stdout):
        return lambda *a, **k: subprocess.CompletedProcess(
            a[0], 0, stdout=stdout, stderr=""
        )

    monkeypatch.setattr(
        capability.subprocess, "run",
        out("/usr/bin/btop cap_perfmon,cap_net_raw=ep\n"),
    )
    assert capability.has_perfmon("/usr/bin/btop") is True
    monkeypatch.setattr(
        capability.subprocess, "run", out("/usr/bin/btop = cap_perfmon+ep\n")
    )
    assert capability.has_perfmon("/usr/bin/btop") is True
    monkeypatch.setattr(
        capability.subprocess, "run", out("/usr/bin/btop cap_net_raw=ep\n")
    )
    assert capability.has_perfmon("/usr/bin/btop") is False


def test_has_perfmon_is_none_without_getcap(monkeypatch):
    monkeypatch.setattr(capability, "tool", lambda name: None)
    assert capability.has_perfmon("/usr/bin/btop") is None


def test_grant_runs_the_exact_command_and_verifies(monkeypatch):
    calls = []
    monkeypatch.setattr(capability, "_run", lambda cmd: calls.append(cmd))
    monkeypatch.setattr(capability, "tool", lambda name: "/usr/sbin/setcap")
    monkeypatch.setattr(capability, "has_perfmon", lambda p: True)
    capability.grant("/usr/bin/btop")
    assert calls == [["sudo", "/usr/sbin/setcap", "cap_perfmon=+ep", "/usr/bin/btop"]]


def test_grant_fails_when_getcap_does_not_show_it(monkeypatch):
    monkeypatch.setattr(capability, "_run", lambda cmd: None)
    monkeypatch.setattr(capability, "has_perfmon", lambda p: False)
    with pytest.raises(RuntimeError):
        capability.grant("/usr/bin/btop")


def test_revoke_verifies_the_capability_is_gone(monkeypatch):
    calls = []
    monkeypatch.setattr(capability, "_run", lambda cmd: calls.append(cmd))
    monkeypatch.setattr(capability, "tool", lambda name: "/usr/sbin/setcap")
    monkeypatch.setattr(capability, "has_perfmon", lambda p: False)
    capability.revoke("/usr/bin/btop")
    assert calls == [["sudo", "/usr/sbin/setcap", "-r", "/usr/bin/btop"]]


def test_capability_is_never_offered_under_output(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(wizard, "_capability", lambda f: calls.append(f))
    wizard._capability_if(found(), tmp_path / "out.conf")
    assert calls == []
    wizard._capability_if(found(), None)
    assert calls


def test_capability_is_not_offered_without_btop(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(wizard, "_capability", lambda f: calls.append(f))
    wizard._capability_if(detect.Btop(), None)
    assert calls == []


def test_wizard_run_with_output_saves_and_skips_capability(tmp_path, monkeypatch):
    """Drive run() with stubbed prompts: preset defaults, straight to save."""
    calls = []
    monkeypatch.setattr(wizard, "_capability", lambda f: calls.append(f))
    monkeypatch.setattr(
        wizard.detect, "inspect",
        lambda **kw: detect.Btop(installed=True, binary="/x/btop", version="1.4.6"),
    )
    monkeypatch.setattr(
        wizard.detect, "suggest",
        lambda f: BtopConfig(target=tmp_path / "btop.conf"),
    )
    answers = {"Start from:": "defaults", "Looks good?": "save"}
    monkeypatch.setattr(wizard.ui, "select", lambda prompt, *a, **k: answers[prompt])
    monkeypatch.setattr(wizard.ui, "confirm", lambda *a, **k: True)
    monkeypatch.setattr(wizard.ui, "dim", lambda *a, **k: None)
    monkeypatch.setattr(wizard.ui.console, "print", lambda *a, **k: None)
    monkeypatch.setattr(wizard.validate, "launch", lambda *a, **k: None)

    out = tmp_path / "out.conf"
    result = wizard.run(target=out)
    assert result is not None and out.exists()
    assert calls == []


# -- live: skipped unless btop is on PATH ------------------------------------------


@needs_btop
def test_live_detect_reads_the_real_binary():
    found_ = detect.inspect()
    assert found_.installed and found_.version
    assert found_.keys is not None and len(found_.keys) > 50
    assert found_.header.startswith("#?")


@needs_btop
def test_live_model_defaults_match_this_btops():
    """Every modelled default is this binary's default for every key it knows.
    Where a version genuinely differs the effective-default logic covers it —
    drift here is a finding to record, not silently absorb."""
    found_ = detect.inspect()
    assert found_.keys is not None
    drift = []
    for key, setting in SETTINGS.items():
        if key in found_.keys and key in found_.defaults:
            theirs = render.parse_value(found_.defaults[key])
            if theirs != setting.default:
                drift.append((key, setting.default, theirs))
    assert drift == []


@needs_btop
@pytest.mark.parametrize("preset", [k for k in PRESETS if k != "current"])
def test_live_every_preset_config_survives_btop(preset):
    found_ = detect.inspect()
    config = BtopConfig(target=Path(tempfile.mkdtemp()) / "btop.conf")
    detect.apply_preset(config, preset, found_)
    result = validate.launch(render.to_text(config, found_), found_)
    assert result is not None and result.started
    assert result.rejections == []


@needs_btop
def test_live_rejected_values_are_reported():
    """The check can fail: values btop resets on load must surface."""
    found_ = detect.inspect()
    candidate = f'{found_.header}\nupdate_ms = "abc"\nvim_keys = yes\n'
    result = validate.launch(candidate, found_)
    assert result is not None and result.started
    keys = {r.key for r in result.rejections}
    assert {"update_ms", "vim_keys"} <= keys


@needs_btop
def test_live_a_bad_enum_is_kept_silently():
    """The asymmetry the verify half exists for: `proc_sorting = "bogus"` is
    *kept* by btop — no rejection — so only the offline enum check can flag it.
    If a future btop starts validating, this test notices."""
    found_ = detect.inspect()
    candidate = f'{found_.header}\nproc_sorting = "bogus"\n'
    result = validate.launch(candidate, found_)
    assert result is not None and result.started
    assert all(r.key != "proc_sorting" for r in result.rejections)


@needs_btop
def test_live_a_generated_file_is_left_byte_identical():
    """The stability claim: a file with this version's header and no rejected
    values is not rewritten by btop."""
    found_ = detect.inspect()
    config = BtopConfig()
    detect.apply_preset(config, "low-overhead", found_)
    text = render.to_text(config, found_)
    with tempfile.TemporaryDirectory() as td:
        home = Path(td)
        conf = home / ".config" / "btop" / "btop.conf"
        conf.parent.mkdir(parents=True)
        conf.write_text(text)
        run = validate.run_pty(found_.binary, home)
        assert run is not None
        assert conf.read_text() == text
