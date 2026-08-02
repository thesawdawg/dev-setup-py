from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from dev_setup import configure
from dev_setup.configure.lazygit import detect, render, validate, wizard
from dev_setup.configure.lazygit.model import (
    CONFIG_FILE,
    GROUPS,
    PAGERS,
    PRESETS,
    RETIRED_KEYS,
    SETTINGS,
    LazygitConfig,
)

HAS_LAZYGIT = shutil.which("lazygit") is not None
needs_lazygit = pytest.mark.skipif(not HAS_LAZYGIT, reason="lazygit is not installed")


def cfg(preset: str = "recommended", **kwargs) -> LazygitConfig:
    config = LazygitConfig(preset=preset, **kwargs)
    if preset in PRESETS and not kwargs:
        detect.apply_preset(config, preset, detect.Lazygit())
    return config


# -- the model's own invariants --------------------------------------------------


def test_every_setting_names_a_real_group_and_field():
    blank = LazygitConfig()
    for key, setting in SETTINGS.items():
        assert setting.group in GROUPS, key
        assert hasattr(blank, key), key


def test_setting_defaults_match_the_configs_own_defaults():
    """The emitter omits a value equal to `Setting.default`, so a drift would make the
    wizard stop writing a setting the user did choose."""
    blank = LazygitConfig()
    for key, setting in SETTINGS.items():
        assert getattr(blank, key) == setting.default, key


def test_setting_paths_are_unique():
    paths = [s.path for s in SETTINGS.values()]
    assert len(paths) == len(set(paths))


def test_side_panel_width_is_a_float_not_a_string():
    """Caught by the default-drift check against `lazygit --config`, which is the
    reason that check exists."""
    assert SETTINGS["side_panel_width"].kind == "float"
    assert isinstance(SETTINGS["side_panel_width"].default, float)


def test_no_setting_uses_a_retired_key():
    retired = set(RETIRED_KEYS)
    for setting in SETTINGS.values():
        assert setting.path not in retired, setting.path


def test_every_preset_names_real_fields_and_valid_choices():
    blank = LazygitConfig()
    for preset in PRESETS.values():
        for name, value in preset.values.items():
            assert hasattr(blank, name), f"{preset.key}: {name}"
            setting = SETTINGS.get(name)
            if setting and setting.choices:
                assert value in setting.choices, f"{preset.key}: {name}={value}"


def test_the_delta_preset_disables_deltas_own_pager():
    """delta without --paging=never opens its own pager inside lazygit's, leaving a
    pane you cannot get out of."""
    assert "--paging=never" in PRESETS["delta"].values["pager"]


def test_every_offered_delta_pager_disables_its_own_paging():
    for command in PAGERS:
        if "delta" in command:
            assert "--paging=never" in command, command


def test_no_preset_turns_on_icons_without_a_font_version():
    for preset in PRESETS.values():
        if preset.values.get("show_icons"):
            assert preset.values.get("nerd_fonts_version"), preset.key


# -- rendering -------------------------------------------------------------------


@pytest.mark.parametrize("preset", sorted(PRESETS))
def test_every_preset_round_trips(preset):
    config = cfg(preset)
    assert render.matches(render.to_yaml(config), config)


def test_an_empty_config_is_comments_only():
    text = render.to_yaml(cfg("empty"))
    assert render.load(text)[0] == {}


def test_defaults_are_omitted_rather_than_restated():
    assert render.load(render.to_yaml(LazygitConfig()))[0] == {}


def test_nested_paths_are_emitted_as_nested_yaml():
    parsed, _ = render.load(render.to_yaml(LazygitConfig(pager="delta --paging=never")))
    assert parsed["git"]["paging"]["pager"] == "delta --paging=never"


def test_booleans_are_emitted_as_yaml_booleans():
    parsed, _ = render.load(render.to_yaml(LazygitConfig(show_icons=True)))
    assert parsed["gui"]["showIcons"] is True


def test_a_numeric_string_setting_stays_a_string():
    """`nerdFontsVersion: "3"` must not become the integer 3."""
    parsed, _ = render.load(render.to_yaml(LazygitConfig(nerd_fonts_version="3")))
    assert parsed["gui"]["nerdFontsVersion"] == "3"


def test_the_generated_header_is_a_comment():
    text = render.to_yaml(cfg())
    assert text.startswith(render.GENERATED_HEADER)
    assert text.splitlines()[0].startswith("#")


def test_carried_over_subtrees_are_written_back():
    extra = {"customCommands": [{"key": "C", "command": "git cz"}]}
    parsed, _ = render.load(render.to_yaml(LazygitConfig(extra=extra)))
    assert parsed["customCommands"] == extra["customCommands"]


def test_a_carried_over_key_does_not_overwrite_a_modelled_one():
    config = LazygitConfig(show_icons=True, extra={"gui": {"showIcons": False}})
    parsed, _ = render.load(render.to_yaml(config))
    assert parsed["gui"]["showIcons"] is True


def test_matches_rejects_text_that_is_not_the_config():
    assert not render.matches("gui:\n  showIcons: true\n", cfg("minimal"))


# -- the `=` keybinding ----------------------------------------------------------


def test_safe_load_really_does_choke_on_the_equals_keybinding():
    """The reason `render.load` exists. lazygit's own default config contains
    `expandAll: =`, and PyYAML maps a bare `=` to a special value tag."""
    with pytest.raises(yaml.YAMLError):
        yaml.safe_load("keybinding:\n  files:\n    expandAll: =\n")


def test_render_load_reads_the_equals_keybinding_as_a_string():
    parsed, ok = render.load("keybinding:\n  files:\n    expandAll: =\n")
    assert ok
    assert parsed["keybinding"]["files"]["expandAll"] == "="


def test_an_equals_value_is_quoted_on_the_way_out():
    parsed, ok = render.load(render.to_yaml(LazygitConfig(extra={"k": {"expandAll": "="}})))
    assert ok
    assert parsed["k"]["expandAll"] == "="


def test_load_reports_broken_yaml_rather_than_raising():
    parsed, ok = render.load("gui:\n  - [[[\n")
    assert not ok and parsed == {}


def test_load_treats_an_empty_document_as_an_empty_config():
    assert render.load("") == ({}, True)


def test_load_rejects_a_document_that_is_not_a_mapping():
    assert render.load("- a\n- b\n") == ({}, False)


# -- reading an existing config back ---------------------------------------------


def test_an_existing_config_round_trips():
    text = render.to_yaml(
        LazygitConfig(nerd_fonts_version="3", show_icons=True, pager="delta", scroll_height=4)
    )
    found = detect.Lazygit(existing_text=text)
    found.existing, found.parse_ok = render.load(text)
    config = detect.from_existing(found)
    assert config.nerd_fonts_version == "3"
    assert config.show_icons is True
    assert config.pager == "delta"
    assert config.scroll_height == 4


def test_custom_commands_and_keybindings_survive_untouched():
    """The two things a lazygit user has most invested in, and the wizard models
    neither."""
    found = detect.Lazygit()
    found.existing = {
        "customCommands": [{"key": "C", "command": "git cz"}],
        "keybinding": {"files": {"expandAll": "="}},
        "gui": {"showIcons": True},
    }
    config = detect.from_existing(found)
    assert config.extra["customCommands"] == [{"key": "C", "command": "git cz"}]
    assert config.extra["keybinding"] == {"files": {"expandAll": "="}}
    assert config.show_icons is True
    assert "gui" not in config.extra  # consumed, and the empty branch dropped


def test_a_wrongly_typed_value_on_disk_is_left_alone():
    """lazygit refuses to start on one, so the user has a real problem the wizard
    should not paper over by reinterpreting it."""
    found = detect.Lazygit()
    found.existing = {"gui": {"scrollHeight": "lots"}}
    config = detect.from_existing(found)
    assert config.scroll_height == SETTINGS["scroll_height"].default


def test_a_retired_key_on_disk_is_detected():
    found = detect.Lazygit()
    found.existing = {"git": {"paging": {"useConfig": True}}}
    config = detect.from_existing(found)
    assert [path for path, _ in detect.retired_keys(config)] == ["git.paging.useConfig"]


def test_retired_keys_are_preserved_not_deleted():
    found = detect.Lazygit()
    found.existing = {"git": {"paging": {"useConfig": True}}}
    parsed, _ = render.load(render.to_yaml(detect.from_existing(found)))
    assert parsed["git"]["paging"]["useConfig"] is True


# -- presets and suggestion ------------------------------------------------------


def test_applying_a_preset_keeps_carried_over_subtrees():
    config = LazygitConfig(extra={"customCommands": [{"key": "C"}]})
    detect.apply_preset(config, "minimal", detect.Lazygit())
    assert config.extra == {"customCommands": [{"key": "C"}]}


def test_applying_a_preset_clears_the_previous_ones_values():
    config = cfg("careful")
    assert config.confirm_on_quit is True
    detect.apply_preset(config, "recommended", detect.Lazygit())
    assert config.confirm_on_quit is False


def test_suggest_avoids_icons_when_there_is_no_nerd_font():
    config = detect.suggest(detect.Lazygit(nerd_font=False))
    assert not config.wants_icons()


def test_suggest_uses_icons_when_a_nerd_font_is_present():
    assert detect.suggest(detect.Lazygit(nerd_font=True)).wants_icons()


def test_suggest_uses_icons_when_the_font_cannot_be_checked():
    """`None` means "cannot tell", which is not the same as "no"."""
    assert detect.suggest(detect.Lazygit(nerd_font=None)).wants_icons()


def test_suggest_starts_from_an_existing_config():
    found = detect.Lazygit()
    found.existing = {"gui": {"scrollHeight": 9}}
    assert detect.suggest(found).scroll_height == 9


# -- the checks ------------------------------------------------------------------


def test_an_invalid_enum_value_is_caught():
    """lazygit accepts `nerdFontsVersion: "9"` and silently draws no icons."""
    report = validate.verify(LazygitConfig(nerd_fonts_version="9"))
    assert any("values are ones lazygit accepts" in check.name for check in report.failures)


def test_valid_enum_values_pass():
    assert validate.verify(LazygitConfig(nerd_fonts_version="3")).ok


def test_a_retired_key_is_its_own_failed_check():
    config = LazygitConfig(extra={"git": {"paging": {"useConfig": True}}})
    assert not validate.verify(config).ok


def test_default_drift_is_empty_without_a_defaults_dump():
    assert detect.default_drift(detect.Lazygit(defaults={})) == []


def test_a_setting_with_no_default_in_the_dump_is_not_drift():
    """`git.paging.pager` has no default and is absent from `lazygit --config`. Absence
    is not disagreement — treating it as such is the mistake that would have made the
    wizard refuse to write a valid setting."""
    found = detect.Lazygit(defaults={"gui": {"showIcons": False}})
    assert all(path != "git.paging.pager" for path, _, _ in detect.default_drift(found))


def test_default_drift_is_reported_when_it_is_real():
    found = detect.Lazygit(defaults={"gui": {"showIcons": True}})
    assert any(path == "gui.showIcons" for path, _, _ in detect.default_drift(found))


# -- warnings --------------------------------------------------------------------


def test_icons_without_a_font_version_warns():
    assert any("falls back" in w for w in LazygitConfig(show_icons=True).warnings())


def test_a_pager_with_colour_disabled_warns():
    config = LazygitConfig(pager="delta --paging=never", color_arg="never")
    assert any("no colour" in w for w in config.warnings())


def test_delta_without_paging_never_warns():
    assert any("own pager" in w for w in LazygitConfig(pager="delta --dark").warnings())


# -- saving ----------------------------------------------------------------------


def test_save_writes_the_file_and_creates_its_parent(tmp_path):
    path = tmp_path / "lazygit" / CONFIG_FILE
    written, saved = wizard.save(cfg("delta"), path)
    assert written.exists() and saved is None
    assert "delta" in written.read_text()


def test_save_backs_up_an_existing_file(tmp_path):
    path = tmp_path / CONFIG_FILE
    path.write_text("gui:\n  showIcons: true\n", encoding="utf-8")
    _, saved = wizard.save(cfg("plain"), path)
    assert saved is not None and "showIcons" in saved.read_text()


def test_diff_is_empty_when_nothing_changes():
    text = render.to_yaml(cfg())
    assert render.diff(text, text) == []


# -- registry wiring -------------------------------------------------------------


def test_lazygit_is_registered_as_a_configurator():
    assert configure.has("lazygit")
    assert configure.get("lazygit").module.endswith("lazygit.wizard")


def test_the_wizard_module_satisfies_the_configurator_contract():
    module = configure.get("lazygit").load()
    assert callable(module.run)
    assert callable(module.config_path)


def test_config_path_asks_the_binary(monkeypatch):
    monkeypatch.setattr(detect, "inspect", lambda: detect.Lazygit(path=Path("/x/config.yml")))
    assert wizard.config_path() == Path("/x/config.yml")


# -- against the real binary -----------------------------------------------------


@needs_lazygit
def test_the_defaults_dump_is_readable():
    """It contains `expandAll: =`, so this also asserts `render.load` handles it."""
    assert detect.defaults()


@needs_lazygit
def test_the_model_does_not_disagree_with_this_lazygits_defaults():
    """The check that caught `sidePanelWidth` being a float."""
    assert detect.default_drift(detect.inspect()) == []


@needs_lazygit
@pytest.mark.parametrize("preset", sorted(PRESETS))
def test_every_preset_passes_the_offline_checks(preset):
    report = validate.verify(cfg(preset), detect.inspect())
    assert report.ok, [check.detail for check in report.failures]


@needs_lazygit
def test_real_lazygit_starts_with_the_recommended_config():
    result = validate.launch(cfg("recommended"))
    assert result is not None
    started, message = result
    assert started, message


@needs_lazygit
def test_real_lazygit_refuses_a_wrongly_typed_value():
    """The one thing lazygit does check, and what makes the type probe in `model.py`
    able to distinguish a real key from an unknown one."""
    config = LazygitConfig(extra={"gui": {"scrollHeight": "not-a-number"}})
    result = validate.launch(config)
    assert result is not None
    started, message = result
    assert not started
    assert "config" in message.lower()


@needs_lazygit
def test_real_lazygit_ignores_an_unknown_key():
    """The asymmetry the whole package is built around: this starts cleanly, which is
    why the wizard has to verify keys itself."""
    config = LazygitConfig(extra={"totallyMadeUp": {"nested": "value"}})
    result = validate.launch(config)
    assert result is not None
    started, _ = result
    assert started
