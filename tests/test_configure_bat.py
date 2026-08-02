from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from dev_setup import configure
from dev_setup.configure.bat import detect, preview, render, wizard
from dev_setup.configure.bat.model import (
    AUTO_THEME,
    COMPONENT_CONFLICTS,
    COMPONENTS,
    DARK,
    DEFAULT_COMPONENTS,
    LIGHT,
    PRESETS,
    SETTINGS,
    SHELL_BITS,
    TERMINAL,
    THEMES,
    BatConfig,
    component_conflicts,
)

HAS_BAT = shutil.which("bat") is not None
needs_bat = pytest.mark.skipif(not HAS_BAT, reason="bat is not installed")


def cfg(preset: str = "balanced", **kwargs) -> BatConfig:
    config = BatConfig(preset=preset, **kwargs)
    if preset in PRESETS and not kwargs:
        detect.apply_preset(config, preset, detect.Bat())
    return config


# -- the model's own invariants --------------------------------------------------


def test_every_theme_has_a_known_mode():
    for theme in THEMES.values():
        assert theme.mode in (DARK, LIGHT, TERMINAL), theme.name


def test_both_theme_modes_have_options_to_offer():
    """The auto-theme step asks for one of each, so an empty side would be a dead
    prompt."""
    for mode in (DARK, LIGHT):
        assert [t for t in THEMES.values() if t.mode == mode]


def test_the_solarized_pair_is_classified_by_name_not_luminance():
    """Recorded because the automatic classification gets this pair wrong: Solarized
    light and dark share one palette by design, so both measure at the same
    foreground luminance."""
    assert THEMES["Solarized (light)"].mode == LIGHT
    assert THEMES["Solarized (dark)"].mode == DARK


def test_terminal_themes_are_neither_light_nor_dark():
    for name in ("ansi", "base16", "base16-256"):
        assert THEMES[name].mode == TERMINAL


def test_default_components_are_all_real_components():
    for key in DEFAULT_COMPONENTS:
        assert key in COMPONENTS


def test_every_setting_maps_to_a_field():
    blank = BatConfig()
    for key in SETTINGS:
        if key == "style":
            continue  # derived from `components`
        assert hasattr(blank, key), key


def test_every_preset_names_real_fields_and_real_components():
    blank = BatConfig()
    for preset in PRESETS.values():
        for name, value in preset.values.items():
            assert hasattr(blank, name), f"{preset.key}: {name}"
            if name == "components":
                for component in value:
                    assert component in COMPONENTS, f"{preset.key}: {component}"


def test_no_shipped_preset_contains_a_conflicting_component_pair():
    """`rule` inside `grid` is invisible and bat says so. An "enable everything"
    preset is exactly how that pair ships by accident."""
    for preset in PRESETS.values():
        components = preset.values.get("components")
        if components is None:
            continue
        assert component_conflicts(components) == [], preset.key


def test_a_user_built_conflict_is_still_detected():
    assert component_conflicts(["grid", "rule"])
    assert any("subset" in warning for warning in BatConfig(components=["grid", "rule"]).warnings())


def test_the_conflict_table_only_names_real_components():
    for a, b, _ in COMPONENT_CONFLICTS:
        assert a in COMPONENTS and b in COMPONENTS


def test_shell_bits_never_contain_a_blank_line():
    """`base.remove_bashrc_block` ends its block at the first blank line, so a blank
    line inside one would orphan everything after it on removal."""
    for bit in SHELL_BITS.values():
        assert bit.line.strip()
        assert "\n" not in bit.line


def test_the_rendered_shell_block_has_no_blank_lines():
    config = BatConfig(shell_bits=list(SHELL_BITS))
    block = render.shell_block(config)
    assert block
    assert all(line.strip() for line in block.splitlines())


# -- rendering -------------------------------------------------------------------


@pytest.mark.parametrize("preset", sorted(PRESETS))
def test_every_preset_round_trips(preset):
    config = cfg(preset)
    assert render.matches(render.to_text(config), config)


def test_a_theme_with_spaces_is_quoted():
    """Measured: unquoted, bat reads `--theme=Solarized (dark)` as two arguments and
    tries to open a file called `(dark)`."""
    text = render.to_text(BatConfig(theme="Solarized (dark)"))
    assert '--theme="Solarized (dark)"' in text


def test_a_quoted_theme_parses_back_to_the_same_name():
    config = BatConfig(theme="Solarized (dark)")
    parsed, _ = render.parse(render.to_text(config))
    assert ("--theme", "Solarized (dark)") in parsed


def test_defaults_are_omitted_rather_than_restated():
    text = render.to_text(BatConfig())
    assert "--paging" not in text
    assert "--wrap" not in text
    assert "--italic-text" not in text
    assert "--tabs" not in text


def test_the_default_component_set_is_not_written():
    """bat's own default. Writing it out would freeze it."""
    assert "--style" not in render.to_text(BatConfig(components=list(DEFAULT_COMPONENTS)))


def test_no_components_is_written_as_plain():
    assert '--style="plain"' in render.to_text(BatConfig(components=[]))


def test_auto_theme_alone_writes_no_theme_flag():
    """`auto` is bat's default; it is only worth writing when steered by a pair."""
    assert "--theme" not in render.to_text(BatConfig(theme=AUTO_THEME))


def test_auto_theme_with_a_pair_writes_both_halves():
    text = render.to_text(BatConfig(theme=AUTO_THEME, theme_dark="Nord", theme_light="GitHub"))
    assert '--theme-dark="Nord"' in text
    assert '--theme-light="GitHub"' in text
    assert "--theme=" not in text


def test_a_fixed_theme_writes_theme_and_not_the_pair():
    text = render.to_text(BatConfig(theme="Nord"))
    assert '--theme="Nord"' in text
    assert "--theme-dark" not in text


def test_the_generated_header_is_present_and_is_a_comment():
    text = render.to_text(cfg())
    assert text.startswith(render.GENERATED_HEADER)
    assert text.splitlines()[0].startswith("#")


def test_unrecognised_lines_are_carried_through():
    config = BatConfig(extra=['--map-syntax="*.conf:INI"'])
    assert '--map-syntax="*.conf:INI"' in render.to_text(config)


def test_matches_rejects_text_that_is_not_the_config():
    assert not render.matches('--theme="Nord"', cfg("minimal"))


# -- parsing ---------------------------------------------------------------------


def test_parse_ignores_comments_and_blank_lines():
    flags, extra = render.parse("# a comment\n\n--theme=\"Nord\"\n")
    assert flags == [("--theme", "Nord")]
    assert extra == []


def test_parse_keeps_unknown_options_as_extra():
    flags, extra = render.parse('--map-syntax="*.conf:INI"\n--theme="Nord"\n')
    assert flags == [("--theme", "Nord")]
    assert extra == ['--map-syntax="*.conf:INI"']


def test_parse_handles_the_space_separated_form():
    """bat accepts `--theme "Nord"` as well as `--theme="Nord"`."""
    flags, _ = render.parse('--theme "Nord"\n')
    assert flags == [("--theme", "Nord")]


def test_parse_survives_an_unbalanced_quote():
    _, extra = render.parse('--theme="Nord\n')
    assert extra == ['--theme="Nord']


# -- reading an existing config back ---------------------------------------------


def test_an_existing_config_round_trips():
    text = render.to_text(
        BatConfig(theme=AUTO_THEME, theme_dark="Nord", theme_light="GitHub",
                  components=["numbers"], paging="never", tabs=2)
    )
    found = detect.Bat(existing_text=text)
    found.existing_flags, found.existing_extra = render.parse(text)
    config = detect.from_existing(found)
    assert config.theme_dark == "Nord"
    assert config.theme_light == "GitHub"
    assert config.uses_auto_theme()
    assert config.components == ["numbers"]
    assert config.paging == "never"
    assert config.tabs == 2


def test_reading_plain_gives_no_components():
    found = detect.Bat()
    found.existing_flags = [("--style", "plain")]
    assert detect.from_existing(found).components == []


def test_a_non_numeric_tab_width_on_disk_is_ignored():
    found = detect.Bat()
    found.existing_flags = [("--tabs", "wide")]
    assert detect.from_existing(found).tabs == SETTINGS["tabs"].default


def test_unmodelled_lines_survive_the_round_trip():
    found = detect.Bat(existing_extra=['--map-syntax="*.conf:INI"'])
    assert detect.from_existing(found).extra == ['--map-syntax="*.conf:INI"']


def test_a_theme_pair_on_disk_implies_auto_mode():
    found = detect.Bat()
    found.existing_flags = [("--theme-dark", "Nord")]
    assert detect.from_existing(found).uses_auto_theme()


# -- presets and suggestion ------------------------------------------------------


def test_applying_a_preset_keeps_carried_over_lines_and_shell_bits():
    config = BatConfig(extra=["--map-syntax=x"], shell_bits=["manpager"])
    detect.apply_preset(config, "minimal", detect.Bat())
    assert config.extra == ["--map-syntax=x"]
    assert config.shell_bits == ["manpager"]


def test_applying_a_preset_clears_the_previous_ones_values():
    config = cfg("minimal")
    assert config.paging == "never"
    detect.apply_preset(config, "balanced", detect.Bat())
    assert config.paging == "auto"


def test_suggest_starts_from_balanced_with_no_config():
    config = detect.suggest(detect.Bat())
    assert config.preset == "balanced"
    assert config.uses_auto_theme()


def test_suggest_starts_from_an_existing_config():
    text = render.to_text(BatConfig(theme="Dracula"))
    found = detect.Bat(existing_text=text)
    found.existing_flags, found.existing_extra = render.parse(text)
    assert detect.suggest(found).theme == "Dracula"


# -- theme validation ------------------------------------------------------------


def test_an_unknown_theme_is_caught():
    """The quiet failure this wizard exists for: bat warns once on stderr and exits
    zero, so a typo silently gives you the default theme forever."""
    found = detect.Bat(themes=("Nord", "GitHub"))
    assert detect.unknown_themes(BatConfig(theme="Nope"), found) == ["Nope"]


def test_both_halves_of_an_auto_pair_are_validated():
    found = detect.Bat(themes=("Nord",))
    config = BatConfig(theme=AUTO_THEME, theme_dark="Nord", theme_light="Nope")
    assert detect.unknown_themes(config, found) == ["Nope"]


def test_theme_validation_is_silent_when_the_list_could_not_be_read():
    """An unanswerable question must not become a false accusation."""
    assert detect.unknown_themes(BatConfig(theme="Anything"), detect.Bat(themes=())) == []


def test_a_user_built_theme_is_accepted():
    found = detect.Bat(themes=("Nord", "MyOwnTheme"))
    assert detect.unknown_themes(BatConfig(theme="MyOwnTheme"), found) == []


def test_known_themes_falls_back_to_the_shipped_table():
    assert detect.known_themes(detect.Bat(themes=())) == tuple(THEMES)


def test_an_unknown_style_component_is_caught():
    """Unlike a bad theme, bat refuses to start — but the wizard should say so before
    the config is saved rather than after."""
    report = preview.check(BatConfig(components=["nosuchthing"]), detect.Bat())
    assert any("components are real" in check.name for check in report.failures)


# -- warnings --------------------------------------------------------------------


def test_filesize_without_filename_warns():
    config = BatConfig(components=["header-filesize"])
    assert any("no name above it" in warning for warning in config.warnings())


def test_the_cat_alias_carries_its_caution():
    config = BatConfig(shell_bits=["cat_alias"])
    assert any("interactive-only" in warning for warning in config.warnings())


def test_auto_theme_with_no_pair_is_called_out_as_a_no_op():
    assert any("just" in warning for warning in BatConfig(theme=AUTO_THEME).warnings())


# -- saving ----------------------------------------------------------------------


def test_save_writes_the_file_and_creates_its_parent(tmp_path):
    path = tmp_path / "config" / "bat" / "config"
    written, saved = wizard.save(cfg("minimal"), path)
    assert written.exists() and saved is None
    assert '--style="plain"' in written.read_text()


def test_save_backs_up_an_existing_file(tmp_path):
    path = tmp_path / "config"
    path.write_text("--theme=\"Old\"\n", encoding="utf-8")
    _, saved = wizard.save(cfg("minimal"), path)
    assert saved is not None
    assert "Old" in saved.read_text()


def test_the_generated_header_is_what_the_overwrite_guard_keys_on(tmp_path):
    path = tmp_path / "config"
    wizard.save(cfg(), path)
    assert path.read_text().lstrip().startswith(render.GENERATED_HEADER)


def test_diff_is_empty_when_nothing_changes():
    text = render.to_text(cfg())
    assert render.diff(text, text) == []


# -- registry wiring -------------------------------------------------------------


def test_bat_is_registered_as_a_configurator():
    assert configure.has("bat")
    assert configure.get("bat").module.endswith("bat.wizard")


def test_the_wizard_module_satisfies_the_configurator_contract():
    module = configure.get("bat").load()
    assert callable(module.run)
    assert callable(module.config_path)


def test_config_path_asks_the_binary(monkeypatch):
    monkeypatch.setattr(detect, "inspect", lambda: detect.Bat(path=Path("/x/bat/config")))
    assert wizard.config_path() == Path("/x/bat/config")


# -- against the real binary -----------------------------------------------------


@needs_bat
def test_the_live_theme_list_is_not_empty():
    assert detect.themes()


@needs_bat
def test_every_shipped_theme_still_exists_in_this_bat():
    """The shipped table is a fallback for when bat cannot be asked. If it names a
    theme this bat does not have, the fallback is actively wrong."""
    live = set(detect.themes())
    assert set(THEMES) <= live, sorted(set(THEMES) - live)


@needs_bat
@pytest.mark.parametrize("preset", sorted(PRESETS))
def test_every_preset_renders_without_a_complaint_from_bat(preset):
    found = detect.inspect()
    report = preview.check(cfg(preset), found)
    assert report.ok, [check.detail for check in report.failures]


@needs_bat
def test_the_preview_actually_renders_something():
    rendered = preview.render_live(cfg("review"))
    assert rendered is not None
    stdout, _ = rendered
    assert "parse_size" in stdout


def _plain_text(rendered: str) -> list[str]:
    """bat's output with the colour escapes removed, so assertions read the text
    rather than the escape codes (which are full of digits)."""
    return [preview._ANSI.sub("", line) for line in rendered.splitlines()]


@needs_bat
def test_a_plain_style_produces_no_line_numbers():
    plain, _ = preview.render_live(cfg("minimal"))
    numbered, _ = preview.render_live(cfg("numbers"))
    assert not _plain_text(plain)[0].startswith(" 1")
    assert _plain_text(numbered)[0].lstrip().startswith("1")


@needs_bat
def test_real_bat_warns_rather_than_failing_on_an_unknown_theme():
    """The measurement the theme check is built on. If a future bat starts exiting
    nonzero for this, the check is redundant and should be revisited."""
    rendered = preview.render_live(BatConfig(theme="DefinitelyNotATheme"))
    assert rendered is not None
    stdout, stderr = rendered
    assert stdout.strip(), "bat still rendered the file"
    assert "theme" in stderr.lower()


@needs_bat
def test_real_bat_confirms_grid_and_rule_conflict():
    """The conflict table is one entry because a sweep of all component pairs found
    exactly one. This asserts that entry is still real."""
    _, stderr = preview.render_live(BatConfig(components=["grid", "rule"]))
    assert "rule" in stderr
