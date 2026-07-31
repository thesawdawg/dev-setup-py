from __future__ import annotations

import contextlib
import itertools
import tomllib
import types

import pytest
from rich.console import Console

from dev_setup import configure
from dev_setup.configure.starship import preview as live
from dev_setup.configure.starship import wizard
from dev_setup.configure.starship.model import (
    LAYOUTS,
    PALETTES,
    POWERLINES,
    PRESETS,
    ROLES,
    SECTIONS,
    SECTIONS_BY_KEY,
    StarshipConfig,
)
from dev_setup.configure.starship.render import sample_markup, to_toml

ALL_SECTIONS = [s.key for s in SECTIONS]
PL_ARROW = POWERLINES["arrows"].sep


def table(data: dict, key: str) -> dict:
    """The module's table, resolving a custom module's dotted key (`custom.compose`
    is nested under `custom` once TOML is parsed)."""
    for part in key.split("."):
        data = data[part]
    return data


def cfg(**kwargs) -> StarshipConfig:
    return StarshipConfig(**kwargs)


# -- the model's own invariants --------------------------------------------------


def test_every_palette_defines_every_role():
    for palette in PALETTES.values():
        assert set(palette.colors) == set(ROLES), palette.key


def test_symbols_only_exist_where_the_body_takes_one():
    """A section carrying an icon whose body has no `$symbol` is dead data — the
    glyph would silently never render. `directory` is the module this guards."""
    for section in SECTIONS:
        if section.icon or section.plain:
            assert section.takes_symbol, f"{section.key} has a symbol it cannot emit"


def test_section_roles_and_groups_are_known():
    from dev_setup.configure.starship.model import GROUPS

    for section in SECTIONS:
        assert section.role in ROLES, section.key
        assert section.group in GROUPS, section.key


def test_default_config_is_usable():
    config = cfg()
    assert config.sections, "some sections must be on by default"
    assert config.preset in PRESETS
    assert config.palette in PALETTES
    assert config.layout in LAYOUTS


# -- TOML validity ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("preset", "palette", "layout"),
    list(itertools.product(PRESETS, PALETTES, LAYOUTS)),
)
def test_every_combination_emits_parseable_toml(preset, palette, layout):
    text = to_toml(cfg(preset=preset, palette=palette, layout=layout, sections=ALL_SECTIONS))
    data = tomllib.loads(text)
    assert data["palette"] == palette
    assert "$character" in data["format"]
    assert data["$schema"].startswith("https://starship.rs/")
    # Every selected module got a table, and is referenced from the prompt.
    for key in ALL_SECTIONS:
        assert table(data, key), key
        assert SECTIONS_BY_KEY[key].ref in data["format"] + data.get("right_format", ""), key


def test_generated_file_has_a_header_comment():
    text = to_toml(cfg())
    assert text.startswith(wizard.GENERATED_HEADER)
    assert "devstuff configure starship" in text
    assert "https://starship.rs/config/" in text


def test_unselected_modules_get_no_table_and_no_format_entry():
    data = tomllib.loads(to_toml(cfg(sections=["directory", "git_branch"])))
    assert "directory" in data and "git_branch" in data
    assert "kubernetes" not in data
    assert "$kubernetes" not in data["format"]
    assert "$nodejs" not in data["format"]


def test_sections_are_emitted_in_canonical_order_not_selection_order():
    reversed_selection = ["cmd_duration", "nodejs", "git_branch", "directory"]
    fmt = tomllib.loads(to_toml(cfg(sections=reversed_selection)))["format"]
    positions = [fmt.index(f"${key}") for key in
                 ("directory", "git_branch", "nodejs", "cmd_duration")]
    assert positions == sorted(positions)


def test_username_uses_its_own_style_key():
    """starship rejects a plain `style` on the username module."""
    data = tomllib.loads(to_toml(cfg(sections=["username"])))
    assert "style_user" in data["username"]
    assert "style" not in data["username"]
    # A root shell would otherwise fall back to starship's off-palette bold red.
    assert data["username"]["style_root"] == "bold fg:err"


def test_git_status_brackets_survive_as_literal_backslashes():
    data = tomllib.loads(to_toml(cfg(sections=["git_status"])))
    assert data["git_status"]["format"] == r"[\[$all_status$ahead_behind\]]($style) "


# -- symbols ---------------------------------------------------------------------


def test_plain_preset_blanks_symbols_instead_of_omitting_them():
    """Omitting `symbol` would let starship's own Nerd Font glyph through, which is
    exactly what the plain preset promises not to do."""
    data = tomllib.loads(to_toml(cfg(preset="plain", sections=["nodejs", "git_branch"])))
    assert data["nodejs"]["symbol"] == "node "
    assert data["git_branch"]["symbol"] == "on "


def test_nerd_font_presets_emit_the_glyph():
    for preset in ("icons", "powerline"):
        data = tomllib.loads(to_toml(cfg(preset=preset, sections=["nodejs"])))
        assert data["nodejs"]["symbol"] == SECTIONS_BY_KEY["nodejs"].icon


def test_modules_starship_ships_disabled_are_switched_back_on():
    keys = ["kubernetes", "time", "azure", "status", "shlvl"]
    data = tomllib.loads(to_toml(cfg(sections=[*keys, "nodejs"])))
    for key in keys:
        assert data[key]["disabled"] is False, key
    # Modules starship already enables must not carry a spurious `disabled` key.
    assert "disabled" not in data["nodejs"]


def test_prompt_symbols_escape_starship_format_metacharacters():
    """An unescaped `$` is read as the start of a variable name: the character module
    fails to parse and renders nothing, leaving the plain presets with no prompt."""
    data = tomllib.loads(to_toml(cfg(preset="plain")))
    assert data["character"]["success_symbol"] == r"[\$](bold fg:ok)"
    assert data["character"]["error_symbol"] == r"[\$](bold fg:err)"
    # A symbol with nothing to escape is left exactly as it is.
    assert tomllib.loads(to_toml(cfg(preset="icons")))["character"]["success_symbol"] == (
        "[❯](bold fg:ok)"
    )


# -- brackets, versions and custom modules ---------------------------------------


@pytest.mark.parametrize("preset", ["bracketed", "icons_bracketed"])
def test_bracketed_presets_wrap_each_body_but_never_double_up(preset):
    data = tomllib.loads(to_toml(cfg(preset=preset, sections=["nodejs", "git_status"])))
    assert data["nodejs"]["format"] == r"[\[$symbol$version\]]($style) "
    # git_status draws its own brackets; wrapping it again would render [[+2 ?1]].
    assert data["git_status"]["format"] == r"[\[$all_status$ahead_behind\]]($style) "


def test_hiding_versions_drops_the_variable_and_the_symbol_padding():
    data = tomllib.loads(to_toml(cfg(sections=["nodejs", "cmd_duration"], show_versions=False)))
    assert data["nodejs"]["format"] == "[$symbol]($style) "
    assert data["nodejs"]["symbol"] == SECTIONS_BY_KEY["nodejs"].icon.rstrip()
    # Only `$version` goes; a body that never had one is untouched.
    assert data["cmd_duration"]["format"] == "[took $duration]($style) "
    assert "runtime versions hidden" in to_toml(cfg(show_versions=False)).splitlines()[2]


def test_showing_versions_is_the_default_and_keeps_the_symbol_padding():
    data = tomllib.loads(to_toml(cfg(sections=["nodejs"])))
    assert data["nodejs"]["format"] == "[$symbol$version]($style) "
    assert data["nodejs"]["symbol"] == SECTIONS_BY_KEY["nodejs"].icon


def test_custom_modules_are_nested_tables_referenced_by_braced_name():
    """`$custom.compose` would parse as the `custom` module followed by `.compose`."""
    data = tomllib.loads(to_toml(cfg(sections=["directory", "custom.compose"])))
    assert "${custom.compose}" in data["format"]
    assert "$custom" not in data["format"].replace("${custom.compose}", "")
    compose = data["custom"]["compose"]
    assert compose["command"].strip().endswith('printf \'%s\' "$name"')
    assert compose["shell"] == ["sh"]
    assert "compose.yaml" in compose["when"]
    assert compose["format"] == "[$symbol$output]($style) "


def test_custom_module_scripts_survive_as_literal_multi_line_strings():
    """The command is shell, not starship format: no backslash or quote in it may be
    reinterpreted on the way through TOML."""
    section = SECTIONS_BY_KEY["custom.compose"]
    data = tomllib.loads(to_toml(cfg(sections=["custom.compose"])))
    # Round-trips byte for byte, up to the newline the closing `'''` sits on.
    assert data["custom"]["compose"]["command"] == section.extra["command"].rstrip() + "\n"
    assert "'''" not in section.extra["command"]


# -- powerline -------------------------------------------------------------------


def test_powerline_draws_one_bar_per_role_run():
    """Four languages share the `lang` role, so they belong to a single bar — a
    transition arrow between two identical backgrounds is the bug this prevents."""
    sections = ["directory", "nodejs", "python", "rust", "golang"]
    fmt = tomllib.loads(to_toml(cfg(preset="powerline", sections=sections)))["format"]
    # One leading cap, one dir→lang transition, one trailing arrow = 2 arrows.
    assert fmt.count(PL_ARROW) == 2


@pytest.mark.parametrize("preset", [k for k, p in PRESETS.items() if p.powerline])
def test_every_powerline_preset_draws_with_its_own_glyph_set(preset):
    pl = PRESETS[preset].powerline
    data = tomllib.loads(
        to_toml(cfg(preset=preset, layout="two_line_right",
                    sections=["directory", "git_branch", "cmd_duration"]))
    )
    # One opening cap, one dir→git transition, one trailing separator.
    assert data["format"].count(pl.cap_left) == 1
    assert data["format"].count(pl.sep) == 2
    # The right prompt is the mirror image: a leading separator and a closing cap.
    assert data["right_format"].count(pl.sep_left) == 1
    assert data["right_format"].count(pl.cap_right) == 1


@pytest.mark.parametrize("preset", [k for k, p in PRESETS.items() if not p.powerline])
def test_presets_without_bars_emit_no_bar_glyphs(preset):
    text = to_toml(cfg(preset=preset, sections=ALL_SECTIONS))
    for pl in POWERLINES.values():
        for glyph in (pl.cap_left, pl.sep, pl.sep_left, pl.cap_right):
            assert glyph not in text, (preset, hex(ord(glyph)))


def test_every_preset_uses_a_declared_glyph_set():
    known = list(POWERLINES.values())
    for preset in PRESETS.values():
        assert preset.powerline is None or preset.powerline in known, preset.key


def test_powerline_styles_carry_a_background_and_plain_presets_do_not():
    pl = tomllib.loads(to_toml(cfg(preset="powerline", sections=["directory"])))
    plain = tomllib.loads(to_toml(cfg(preset="plain", sections=["directory"])))
    assert pl["directory"]["style"] == "fg:bar_text bg:dir"
    assert plain["directory"]["style"] == "fg:dir"


# -- layout ----------------------------------------------------------------------


def test_two_line_layouts_break_before_the_prompt_character():
    single = tomllib.loads(to_toml(cfg(layout="single")))["format"]
    two = tomllib.loads(to_toml(cfg(layout="two_line")))["format"]
    assert "$line_break" not in single
    assert two.index("$line_break") < two.index("$character")


def test_right_layout_moves_shell_sections_out_of_the_left_prompt():
    data = tomllib.loads(to_toml(cfg(layout="two_line_right", sections=ALL_SECTIONS)))
    assert "$cmd_duration" in data["right_format"]
    assert "$cmd_duration" not in data["format"]
    assert "$directory" in data["format"]
    # The module table still has to exist, wherever the module is rendered.
    assert "cmd_duration" in data


def test_no_right_format_key_without_the_right_layout():
    assert "right_format" not in tomllib.loads(to_toml(cfg(layout="two_line")))


def test_blank_line_maps_to_add_newline():
    assert tomllib.loads(to_toml(cfg(blank_line=True)))["add_newline"] is True
    assert tomllib.loads(to_toml(cfg(blank_line=False)))["add_newline"] is False


# -- offline preview -------------------------------------------------------------


@pytest.mark.parametrize("preset", list(PRESETS))
def test_sample_markup_renders_and_mentions_every_selected_section(preset):
    # Wide and unwrapped, so a long prompt does not get folded mid-word and defeat
    # the assertions below. Rendering it at all also proves it is valid Rich markup.
    console = Console(width=600, force_terminal=False)
    config = cfg(preset=preset, sections=ALL_SECTIONS)
    with console.capture() as cap:
        for line in sample_markup(config, width=600):
            console.print(line, no_wrap=True, crop=False)
    text = cap.get()
    for section in config.selected():
        assert section.sample in text, f"{section.key} missing from the preview"


def test_sample_markup_line_count_follows_the_layout():
    assert len(sample_markup(cfg(layout="single"))) == 1
    assert len(sample_markup(cfg(layout="two_line"))) == 2


def test_sample_markup_puts_the_right_prompt_on_the_cursor_line():
    lines = sample_markup(cfg(layout="two_line_right", sections=ALL_SECTIONS), width=200)
    assert "took 2s" in lines[-1]
    assert "took 2s" not in lines[0]


# -- saving ----------------------------------------------------------------------


def test_config_path_honours_the_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("STARSHIP_CONFIG", str(tmp_path / "elsewhere.toml"))
    assert wizard.config_path() == tmp_path / "elsewhere.toml"
    monkeypatch.delenv("STARSHIP_CONFIG")
    assert wizard.config_path() == wizard.default_config_path()


def test_save_writes_a_config_starship_could_read(tmp_path):
    target = tmp_path / "starship.toml"
    path, backup = wizard.save(cfg(), target)
    assert path == target and backup is None
    tomllib.loads(target.read_text())


def test_save_backs_up_an_existing_config_byte_for_byte(tmp_path):
    target = tmp_path / "starship.toml"
    original = "# hand written\nformat = '$directory'\n"
    target.write_text(original)

    path, backup = wizard.save(cfg(), target)
    assert backup is not None
    assert backup.read_text() == original
    assert backup.name.startswith("starship.toml.bak.")
    assert path.read_text() != original


def test_looks_generated_distinguishes_our_files_from_hand_written_ones(tmp_path):
    ours = tmp_path / "ours.toml"
    wizard.save(cfg(), ours)
    theirs = tmp_path / "theirs.toml"
    theirs.write_text("format = '$directory'\n")
    assert wizard._looks_generated(ours) is True
    assert wizard._looks_generated(theirs) is False
    assert wizard._looks_generated(tmp_path / "missing.toml") is False


# -- the wizard flow -------------------------------------------------------------


class FakePrompts:
    """Scripts the ui.* calls the wizard makes, in order."""

    def __init__(self, monkeypatch, *, selects=None, checkboxes=None, confirms=None,
                 font_detected=True):
        self.selects = list(selects or [])
        self.checkboxes = list(checkboxes or [])
        self.confirms = list(confirms or [])
        self.select_prompts: list[str] = []
        # Default to "this machine has a Nerd Font": the font gate is a separate
        # concern with its own tests, and letting it fire here would eat a confirm.
        monkeypatch.setattr(wizard.fonts, "detect", lambda: font_detected)
        monkeypatch.setattr(wizard.fonts, "is_remote_session", lambda: False)
        monkeypatch.setattr(wizard.ui, "select", self._select)
        monkeypatch.setattr(wizard.ui, "checkbox", self._checkbox)
        monkeypatch.setattr(wizard.ui, "confirm", self._confirm)
        monkeypatch.setattr(wizard.ui, "code_block", lambda *a, **k: None)
        for name in ("section", "dim", "info", "success", "warn", "error"):
            monkeypatch.setattr(wizard.ui, name, lambda *a, **k: None)
        monkeypatch.setattr(wizard.ui, "spinner", lambda *a, **k: contextlib.nullcontext())
        monkeypatch.setattr(wizard.ui.console, "print", lambda *a, **k: None)
        # No starship in unit tests: force the offline path deterministically.
        monkeypatch.setattr(live, "available", lambda: False)

    def _select(self, prompt, choices, default=None):
        self.select_prompts.append(prompt)
        return self.selects.pop(0) if self.selects else ""

    def _checkbox(self, prompt, choices, **kwargs):
        return self.checkboxes.pop(0) if self.checkboxes else []

    def _confirm(self, prompt, default=False):
        return self.confirms.pop(0) if self.confirms else default


def test_wizard_writes_every_choice_it_was_given(tmp_path, monkeypatch):
    target = tmp_path / "starship.toml"
    FakePrompts(
        monkeypatch,
        # style, palette, layout, then the review menu
        selects=["powerline", "nord", "two_line", "save"],
        checkboxes=[["directory", "git_branch", "kubernetes"]],
        confirms=[False],  # blank line between prompts
    )
    result = wizard.run(target=target)

    assert result is not None
    assert (result.preset, result.palette, result.layout) == ("powerline", "nord", "two_line")
    data = tomllib.loads(target.read_text())
    assert data["palette"] == "nord"
    assert data["add_newline"] is False
    assert data["kubernetes"]["disabled"] is False
    assert "$nodejs" not in data["format"]


def test_wizard_toggles_versions_from_the_review_menu(tmp_path, monkeypatch):
    target = tmp_path / "starship.toml"
    FakePrompts(
        monkeypatch,
        selects=["icons", "nord", "single", "versions", "save"],
        checkboxes=[["directory", "nodejs"]],
        confirms=[True],
    )
    result = wizard.run(target=target)
    assert result is not None and result.show_versions is False
    assert tomllib.loads(target.read_text())["nodejs"]["format"] == "[$symbol]($style) "


def test_wizard_does_not_touch_bashrc_when_writing_elsewhere(tmp_path, monkeypatch):
    """`--output` to a scratch path is just a file; only the config starship really
    reads earns a shell hook."""
    called = []
    monkeypatch.setattr(wizard.base, "patch_bashrc", lambda *a: called.append(a) or True)
    FakePrompts(
        monkeypatch,
        selects=["icons", "nord", "single", "save"],
        checkboxes=[["directory"]],
        confirms=[True, True],
    )
    wizard.run(target=tmp_path / "scratch.toml")
    assert called == []


def test_wizard_cancel_writes_nothing(tmp_path, monkeypatch):
    target = tmp_path / "starship.toml"
    FakePrompts(
        monkeypatch,
        selects=["icons", "nord", "single", "cancel"],
        checkboxes=[["directory"]],
        confirms=[True],
    )
    assert wizard.run(target=target) is None
    assert not target.exists()


def test_wizard_revisits_a_step_from_the_review_menu(tmp_path, monkeypatch):
    target = tmp_path / "starship.toml"
    prompts = FakePrompts(
        monkeypatch,
        # first pass picks plain, then the menu changes the style to powerline
        selects=["plain", "nord", "single", "style", "powerline", "save"],
        checkboxes=[["directory"]],
        confirms=[True],
    )
    result = wizard.run(target=target)
    assert result is not None and result.preset == "powerline"
    # The style question was asked twice: once in the walk-through, once from the menu.
    assert prompts.select_prompts.count("Prompt style:") == 2


def test_wizard_keeps_the_previous_selection_when_nothing_is_ticked(tmp_path, monkeypatch):
    target = tmp_path / "starship.toml"
    FakePrompts(
        monkeypatch,
        selects=["icons", "nord", "single", "save"],
        checkboxes=[[]],  # user unticked everything
        confirms=[True],
    )
    result = wizard.run(target=target)
    assert result is not None
    assert result.sections == StarshipConfig().sections


def test_wizard_asks_before_replacing_a_hand_written_config(tmp_path, monkeypatch):
    target = tmp_path / "starship.toml"
    target.write_text("format = '$directory'\n")
    FakePrompts(
        monkeypatch,
        selects=["icons", "nord", "single", "save"],
        checkboxes=[["directory"]],
        confirms=[True, False],  # blank line = yes, overwrite = no
    )
    assert wizard.run(target=target) is None
    assert target.read_text() == "format = '$directory'\n"


# -- the Nerd Font gate ----------------------------------------------------------


class FakeFontTool:
    name = "JetBrainsMono Nerd Font"

    def __init__(self, installed=False):
        self._installed = installed

    def is_installed(self):
        return self._installed


class FontEnv:
    """Everything the gate reaches for, replaced: detection, the registry lookup and
    the installer. `installs` records the keys it tried to install."""

    def __init__(self, monkeypatch, *, detected=False, remote=False, tool=None, ok=True):
        from dev_setup import registry
        from dev_setup.commands import install_cmd

        self.installs: list[str] = []
        monkeypatch.setattr(wizard.fonts, "detect", lambda: detected)
        monkeypatch.setattr(wizard.fonts, "is_remote_session", lambda: remote)
        monkeypatch.setattr(registry, "get", lambda key: tool if tool else FakeFontTool())
        monkeypatch.setattr(
            install_cmd, "install_by_key",
            lambda key: (self.installs.append(key), ok)[1],
        )


def test_font_detection_reads_fontconfig(monkeypatch):
    from dev_setup.configure.starship import fonts

    def fake_run(families):
        monkeypatch.setattr(fonts.shutil, "which", lambda _: "/usr/bin/fc-list")
        monkeypatch.setattr(
            fonts.subprocess, "run",
            lambda *a, **k: types.SimpleNamespace(returncode=0, stdout=families),
        )

    fake_run("DejaVu Sans Mono\nJetBrainsMono Nerd Font Mono\n")
    assert fonts.detect() is True
    fake_run("DejaVu Sans Mono\nUbuntu Mono\n")
    assert fonts.detect() is False


def test_font_detection_says_unknown_rather_than_guessing(monkeypatch):
    """No fontconfig means the question cannot be answered — and a wrong `False` would
    nag someone whose terminal is perfectly capable of drawing the glyphs."""
    from dev_setup.configure.starship import fonts

    monkeypatch.setattr(fonts.shutil, "which", lambda _: None)
    assert fonts.detect() is None

    monkeypatch.setattr(fonts.shutil, "which", lambda _: "/usr/bin/fc-list")
    monkeypatch.setattr(fonts.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(OSError))
    assert fonts.detect() is None

    monkeypatch.setattr(
        fonts.subprocess, "run",
        lambda *a, **k: types.SimpleNamespace(returncode=1, stdout=""),
    )
    assert fonts.detect() is None


def test_remote_session_is_detected_from_the_ssh_variables(monkeypatch):
    from dev_setup.configure.starship import fonts

    for var in ("SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY"):
        monkeypatch.delenv(var, raising=False)
    assert fonts.is_remote_session() is False
    monkeypatch.setenv("SSH_TTY", "/dev/pts/3")
    assert fonts.is_remote_session() is True


def test_gate_offers_the_font_when_an_icon_preset_is_picked_without_one(monkeypatch):
    prompts = FakePrompts(monkeypatch, selects=["icons"], confirms=[True])
    env = FontEnv(monkeypatch, detected=False)
    gate = wizard._FontGate()
    assert "none installed here" in gate.note()

    wizard._ask_preset(StarshipConfig(), gate)
    assert env.installs == ["nerd-font"]
    assert prompts.confirms == []  # the offer consumed the scripted answer


def test_gate_takes_no_for_an_answer_and_asks_only_once(monkeypatch):
    FakePrompts(monkeypatch, selects=["icons", "powerline"], confirms=[False])
    env = FontEnv(monkeypatch, detected=False)
    gate = wizard._FontGate()

    wizard._ask_preset(StarshipConfig(), gate)
    wizard._ask_preset(StarshipConfig(), gate)  # revisited from the review menu
    assert env.installs == []


@pytest.mark.parametrize(("detected", "note"), [(True, "you have one"), (None, "Needs a Nerd")])
def test_gate_stays_quiet_when_a_font_is_present_or_undetectable(monkeypatch, detected, note):
    FakePrompts(monkeypatch, selects=["powerline"])
    env = FontEnv(monkeypatch, detected=detected)
    gate = wizard._FontGate()
    wizard._ask_preset(StarshipConfig(), gate)
    assert env.installs == []
    assert note in gate.note()


def test_gate_never_offers_for_a_preset_that_needs_no_font(monkeypatch):
    FakePrompts(monkeypatch, selects=["plain"])
    env = FontEnv(monkeypatch, detected=False)
    wizard._ask_preset(StarshipConfig(), wizard._FontGate())
    assert env.installs == []


def test_gate_does_not_install_a_font_the_local_terminal_will_never_use(monkeypatch):
    """Over SSH the glyphs are drawn by the client's terminal, so installing a font on
    this end would be pure noise."""
    FakePrompts(monkeypatch, selects=["icons"], confirms=[True])
    env = FontEnv(monkeypatch, detected=False, remote=True)
    wizard._ask_preset(StarshipConfig(), wizard._FontGate())
    assert env.installs == []


def test_gate_skips_the_install_when_the_font_is_already_there(monkeypatch):
    """Files present but fontconfig blind to them, or the terminal pointed elsewhere —
    either way there is nothing to install, only something to explain."""
    FakePrompts(monkeypatch, selects=["icons"], confirms=[True])
    env = FontEnv(monkeypatch, detected=False, tool=FakeFontTool(installed=True))
    wizard._ask_preset(StarshipConfig(), wizard._FontGate())
    assert env.installs == []


def test_wizard_run_offers_the_font_once_for_the_whole_session(tmp_path, monkeypatch):
    FakePrompts(
        monkeypatch,
        selects=["icons", "nord", "single", "style", "powerline", "save"],
        checkboxes=[["directory"]],
        # font offer, blank line — and nothing more, though the style was picked twice.
        confirms=[True, True],
    )
    env = FontEnv(monkeypatch, detected=False)
    assert wizard.run(target=tmp_path / "starship.toml") is not None
    assert env.installs == ["nerd-font"]


# -- the catalog entry the gate installs ------------------------------------------


def test_nerd_font_tool_is_a_builtin_the_wizard_can_install():
    from dev_setup import registry
    from dev_setup.configure.starship.fonts import NERD_FONT_KEY

    tool = registry.get(NERD_FONT_KEY)
    assert tool is not None and tool.builtin
    # The check must survive a machine with no fontconfig, so it looks for the files
    # first and only then asks fc-list.
    assert ".local/share/fonts" in tool.check_cmd
    assert "fc-list" in tool.check_cmd
    # python3 does the extracting: `unzip` is not on a minimal Ubuntu, python3 is.
    assert "zipfile" in tool.install_script
    assert tool.remove_script and "rm -rf" in tool.remove_script


# -- the configurator registry ---------------------------------------------------


def test_registry_resolves_starship_and_rejects_unknown_keys():
    spec = configure.get("starship")
    assert spec is not None and spec.key == "starship"
    assert configure.has("starship") and not configure.has("nope")
    assert configure.get("nope") is None
    assert "starship" in configure.CONFIGURATORS


def test_every_configurator_honours_the_module_contract():
    """`run(target=...)` and `config_path()` are what the command layer calls."""
    for spec in configure.CONFIGURATORS.values():
        module = spec.load()
        assert callable(module.run)
        assert callable(module.config_path)
        # Keyed by a real catalog tool, or `devstuff configure` would offer a
        # tool that cannot be installed.
        from dev_setup import registry

        assert registry.exists(spec.key), spec.key
