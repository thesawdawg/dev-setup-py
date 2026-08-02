from __future__ import annotations

import shutil
from dataclasses import replace

import pytest

from dev_setup import configure
from dev_setup.configure.ansible import detect, render, validate, wizard
from dev_setup.configure.ansible.model import (
    CONFIG_FILE,
    GROUPS,
    PRESETS,
    RETIRED_SECTIONS,
    SECTIONS,
    SETTINGS,
    STDOUT_CALLBACKS,
    AnsibleConfig,
)

HAS_ANSIBLE = shutil.which("ansible-config") is not None
needs_ansible = pytest.mark.skipif(not HAS_ANSIBLE, reason="ansible is not installed")


def cfg(preset: str = "project", **kwargs) -> AnsibleConfig:
    config = AnsibleConfig(preset=preset, **kwargs)
    if preset in PRESETS and not kwargs:
        detect.apply_preset(config, preset, detect.Project())
    return config


# -- the model's own invariants --------------------------------------------------


def test_every_setting_names_a_real_section_and_group():
    for setting in SETTINGS.values():
        assert setting.section in SECTIONS, setting.key
        assert setting.group in GROUPS, setting.key


def test_ssh_connection_is_not_a_section_this_wizard_writes():
    """The most-copied ansible.cfg stanza in existence, and ansible-core no longer
    reads it. Measured: `[ssh_connection] pipelining = True` produces nothing in
    `ansible-config dump --only-changed`."""
    assert "ssh_connection" not in SECTIONS
    assert "ssh_connection" in RETIRED_SECTIONS


def test_pipelining_is_a_defaults_setting():
    assert SETTINGS["pipelining"].section == "defaults"


def test_every_setting_maps_to_a_field_on_the_config():
    blank = AnsibleConfig()
    for key in SETTINGS:
        assert hasattr(blank, key), key


def test_setting_defaults_match_the_configs_own_defaults():
    """The emitter omits a value equal to `Setting.default`, so a drift between the
    two would silently write settings nobody asked for."""
    blank = AnsibleConfig()
    for key, setting in SETTINGS.items():
        expected = setting.default if setting.default is not None else ""
        assert getattr(blank, key) == expected, key


def test_section_and_key_pairs_are_unique():
    pairs = [(s.section, s.ini_key) for s in SETTINGS.values()]
    assert len(pairs) == len(set(pairs))


def test_env_names_are_unique():
    names = [s.env_name for s in SETTINGS.values()]
    assert len(names) == len(set(names))


def test_every_preset_names_real_fields_and_valid_choices():
    blank = AnsibleConfig()
    for preset in PRESETS.values():
        for name, value in preset.values.items():
            assert hasattr(blank, name), f"{preset.key}: {name}"
            setting = SETTINGS.get(name)
            if setting and setting.choices:
                assert value in setting.choices, f"{preset.key}: {name}={value}"


def test_the_removed_yaml_callback_is_not_offered():
    """`stdout_callback = yaml` is the standard advice everywhere and the plugin was
    removed — ansible-core 2.20 says so explicitly. Setting it passes every check and
    produces JSON anyway. The replacement is `callback_result_format`."""
    assert "yaml" not in STDOUT_CALLBACKS
    assert "debug" not in STDOUT_CALLBACKS
    assert SETTINGS["callback_result_format"].choices == ("json", "yaml")


def test_no_preset_sets_the_removed_yaml_callback():
    for preset in PRESETS.values():
        assert preset.values.get("stdout_callback") != "yaml", preset.key


def test_a_plugin_option_is_marked_as_one():
    """`ansible-config validate` only knows core settings, so it reports a plugin
    option as an unknown key even though ansible honours it."""
    assert SETTINGS["callback_result_format"].plugin_option
    assert SETTINGS["callback_result_format"].dump_type == "callback"
    assert not SETTINGS["forks"].plugin_option


def test_expected_env_separates_core_from_plugin_settings():
    config = AnsibleConfig(forks=20, callback_result_format="yaml")
    assert set(config.expected_env()) == {"DEFAULT_FORKS"}
    assert set(config.expected_env("callback")) == {"result_format"}
    assert config.dump_types() == ["", "callback"]


def test_a_plugin_option_warns_about_the_validators_false_positive():
    config = AnsibleConfig(callback_result_format="yaml")
    assert any("false" in w or "unknown key" in w for w in config.warnings())


def test_presets_only_use_callbacks_the_model_knows():
    for preset in PRESETS.values():
        callback = preset.values.get("stdout_callback")
        if callback:
            assert callback in STDOUT_CALLBACKS, preset.key


# -- rendering -------------------------------------------------------------------


@pytest.mark.parametrize("preset", sorted(PRESETS))
def test_every_preset_round_trips(preset):
    config = cfg(preset)
    assert render.matches(render.to_text(config), config)


def test_an_empty_config_is_comments_only():
    """A bare `[defaults]` would parse back as `{"defaults": {}}` and break the
    round-trip check. Measured: a comments-only file is valid to ansible and is still
    reported as CONFIG_FILE."""
    text = render.to_text(cfg("empty"))
    assert "[defaults]" not in text
    assert render.parse(text)[0] == {}


def test_booleans_are_written_as_True_and_False():
    """What `ansible-config init` itself writes."""
    text = render.to_text(AnsibleConfig(pipelining=True, host_key_checking=False))
    assert "pipelining = True" in text
    assert "host_key_checking = False" in text


def test_values_are_never_quoted():
    """Measured: `inventory = "./inv"` becomes a path containing quote characters,
    and `pipelining = "True"` is read as **False**."""
    text = render.to_text(AnsibleConfig(inventory="./inventory", pipelining=True))
    assert "inventory = ./inventory" in text
    assert '"' not in text.split("[defaults]", 1)[1]


def test_defaults_are_omitted_rather_than_restated():
    text = render.to_text(AnsibleConfig())
    parsed, _ = render.parse(text)
    assert parsed == {}


def test_settings_land_in_the_section_the_binary_declares():
    parsed, _ = render.parse(render.to_text(cfg("become")))
    assert "become" in parsed["privilege_escalation"]
    assert "pipelining" in parsed["defaults"]


def test_the_generated_header_is_a_comment():
    text = render.to_text(cfg())
    assert text.startswith(render.GENERATED_HEADER)
    assert text.splitlines()[0].startswith("#")


def test_carried_over_sections_are_written_back():
    config = AnsibleConfig(extra={"galaxy": {"server": "https://galaxy.example.com"}})
    parsed, _ = render.parse(render.to_text(config))
    assert parsed["galaxy"] == {"server": "https://galaxy.example.com"}


def test_a_carried_over_key_does_not_overwrite_a_modelled_one():
    config = AnsibleConfig(forks=20, extra={"defaults": {"forks": "999"}})
    parsed, _ = render.parse(render.to_text(config))
    assert parsed["defaults"]["forks"] == "20"


def test_parse_survives_a_broken_file():
    parsed, ok = render.parse("this is not ini [[[")
    assert not ok and parsed == {}


def test_parse_tolerates_a_duplicate_key():
    """A real ansible.cfg can carry one; ansible takes the last rather than refusing
    to load, so the parser must not be strict."""
    parsed, ok = render.parse("[defaults]\nforks = 5\nforks = 20\n")
    assert ok and parsed["defaults"]["forks"] == "20"


def test_matches_rejects_text_that_is_not_the_config():
    assert not render.matches("[defaults]\nforks = 1\n", cfg("fast"))


# -- reading an existing config back ---------------------------------------------


def test_a_hand_written_config_round_trips():
    text = "[defaults]\nforks = 30\npipelining = True\ninventory = ./inv\n"
    found = detect.Project(existing_text=text)
    found.existing, found.parse_ok = render.parse(text)
    config = detect.from_existing(found)
    assert config.forks == 30
    assert config.pipelining is True
    assert config.inventory == "./inv"


def test_unmodelled_sections_and_keys_land_in_extra():
    text = "[defaults]\nforks = 30\nsome_new_option = x\n\n[galaxy]\nserver = y\n"
    found = detect.Project()
    found.existing, _ = render.parse(text)
    config = detect.from_existing(found)
    assert config.extra["defaults"] == {"some_new_option": "x"}
    assert config.extra["galaxy"] == {"server": "y"}


def test_a_retired_section_is_preserved_rather_than_deleted():
    """Deleting it silently would hide the fact that it was doing nothing."""
    found = detect.Project()
    found.existing, _ = render.parse("[ssh_connection]\npipelining = True\n")
    assert detect.from_existing(found).extra["ssh_connection"] == {"pipelining": "True"}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("True", True), ("true", True), ("yes", True), ("on", True), ("1", True),
     ("False", False), ("no", False), ("0", False)],
)
def test_booleans_are_read_the_way_ansible_reads_them(raw, expected):
    found = detect.Project()
    found.existing, _ = render.parse(f"[defaults]\npipelining = {raw}\n")
    assert detect.from_existing(found).pipelining is expected


def test_a_quoted_boolean_is_read_as_false_like_ansible_does():
    """Measured: `pipelining = "True"` makes `ansible-config dump` report **False**.
    Quoting a boolean silently inverts it, so the reader must agree with ansible
    rather than with intuition."""
    found = detect.Project()
    found.existing, _ = render.parse('[defaults]\npipelining = "True"\n')
    assert detect.from_existing(found).pipelining is False


def test_a_non_numeric_integer_on_disk_is_ignored():
    found = detect.Project()
    found.existing, _ = render.parse("[defaults]\nforks = many\n")
    assert detect.from_existing(found).forks == SETTINGS["forks"].default


# -- presets and suggestion ------------------------------------------------------


def test_applying_a_preset_keeps_carried_over_sections():
    config = AnsibleConfig(extra={"galaxy": {"server": "x"}})
    detect.apply_preset(config, "fast", detect.Project())
    assert config.extra == {"galaxy": {"server": "x"}}


def test_applying_a_preset_clears_the_previous_ones_values():
    config = cfg("ci")
    assert config.host_key_checking is False
    detect.apply_preset(config, "fast", detect.Project())
    assert config.host_key_checking is True


def test_suggest_drops_paths_that_do_not_exist_here():
    """The project preset names ./roles and ./collections; suggesting them in a
    directory without them would write paths that resolve to nothing."""
    config = detect.suggest(detect.Project(has_roles=False, has_collections=False))
    assert config.roles_path == ""
    assert config.collections_path == ""


def test_suggest_keeps_paths_that_do_exist():
    config = detect.suggest(detect.Project(has_roles=True, has_inventory=True))
    assert config.roles_path == "./roles"
    assert config.inventory == "./inventory"


def test_suggest_picks_up_an_existing_vault_password_file():
    assert detect.suggest(detect.Project(has_vault_file=True)).vault_password_file


def test_suggest_starts_from_an_existing_config():
    found = detect.Project()
    found.existing, _ = render.parse("[defaults]\nforks = 77\n")
    assert detect.suggest(found).forks == 77


# -- the checks ------------------------------------------------------------------


def test_a_retired_section_is_reported():
    config = AnsibleConfig(extra={"ssh_connection": {"pipelining": "True"}})
    report = validate.verify(config)
    assert any("ssh_connection" in check.name for check in report.failures)


def test_an_unknown_section_is_reported():
    config = AnsibleConfig(extra={"nonsense": {"a": "b"}})
    assert not validate.verify(config).ok


def test_a_known_carried_section_is_not_reported():
    config = AnsibleConfig(extra={"galaxy": {"server": "x"}})
    assert all("galaxy" not in check.name for check in validate.verify(config).failures)


def test_expected_env_lists_what_must_read_back():
    config = AnsibleConfig(forks=20, pipelining=True)
    assert set(config.expected_env()) == {"DEFAULT_FORKS", "ANSIBLE_PIPELINING"}


def test_nothing_set_means_nothing_to_read_back():
    assert AnsibleConfig().expected_env() == {}


def test_unknown_callback_is_detected():
    found = detect.Project(callbacks=("ansible.builtin.default",))
    assert detect.unknown_callback(AnsibleConfig(stdout_callback="nope"), found)


def test_a_builtin_callback_is_found_by_its_short_name():
    """`ansible-doc` prints fully-qualified names, so a short name has to be resolved
    or every builtin looks unavailable."""
    found = detect.Project(callbacks=("ansible.builtin.default", "ansible.builtin.minimal"))
    assert not detect.unknown_callback(AnsibleConfig(stdout_callback="default"), found)


def test_unknown_callback_is_silent_without_a_plugin_list():
    """An unanswerable question must not become a false accusation."""
    assert not detect.unknown_callback(AnsibleConfig(stdout_callback="nope"), detect.Project())


# -- warnings --------------------------------------------------------------------


def test_disabling_host_key_checking_warns():
    assert any("man in the middle" in w for w in AnsibleConfig(host_key_checking=False).warnings())


def test_blanket_become_without_a_password_prompt_warns():
    config = AnsibleConfig(become=True, become_ask_pass=False)
    assert any("passwordless sudo" in w for w in config.warnings())


def test_a_vault_password_file_carries_its_caution():
    config = AnsibleConfig(vault_password_file="./.vault-pass")
    assert any("out of git" in w for w in config.warnings())


def test_a_log_path_warns_about_rotation_and_secrets():
    assert any("rotate" in w for w in AnsibleConfig(log_path="/tmp/a.log").warnings())


def test_pipelining_with_su_warns():
    config = AnsibleConfig(pipelining=True, become=True, become_method="su")
    assert any("su" in w for w in config.warnings())


# -- saving ----------------------------------------------------------------------


def test_save_writes_the_file(tmp_path):
    path = tmp_path / CONFIG_FILE
    written, saved = wizard.save(cfg("fast"), path)
    assert written.exists() and saved is None
    assert "forks = 50" in written.read_text()


def test_save_backs_up_an_existing_file(tmp_path):
    path = tmp_path / CONFIG_FILE
    path.write_text("[defaults]\nforks = 1\n", encoding="utf-8")
    _, saved = wizard.save(cfg("fast"), path)
    assert saved is not None and "forks = 1" in saved.read_text()


def test_world_writable_directories_are_detected(tmp_path):
    """ansible ignores ./ansible.cfg outright in a world-writable directory — the
    quietest failure it has: the file exists, parses, and does nothing."""
    tmp_path.chmod(0o777)
    assert detect.inspect(tmp_path).world_writable


def test_a_normal_directory_is_not_flagged(tmp_path):
    tmp_path.chmod(0o755)
    assert not detect.inspect(tmp_path).world_writable


# -- registry wiring -------------------------------------------------------------


def test_ansible_is_registered_as_a_configurator():
    assert configure.has("ansible")
    assert configure.get("ansible").module.endswith("ansible.wizard")


def test_the_wizard_module_satisfies_the_configurator_contract():
    module = configure.get("ansible").load()
    assert callable(module.run)
    assert callable(module.config_path)


def test_config_path_is_the_project_local_file():
    assert wizard.config_path().name == CONFIG_FILE


# -- against the real binary -----------------------------------------------------


@needs_ansible
@pytest.mark.parametrize("preset", sorted(PRESETS))
def test_every_preset_passes_real_ansible_checks(preset):
    report = validate.verify(cfg(preset))
    assert report.ok, [check.detail for check in report.failures]


@needs_ansible
@pytest.mark.parametrize("preset", sorted(PRESETS))
def test_every_setting_written_is_one_ansible_reads(preset):
    """The check that matters. A setting in a section this version does not read
    parses cleanly, is reported nowhere, and does nothing."""
    config = cfg(preset)
    expected = config.expected_env()
    if not expected:
        pytest.skip("this preset sets nothing")
    got = validate.dump(config)
    assert got is not None
    assert set(expected) <= set(got), sorted(set(expected) - set(got))


@needs_ansible
def test_the_reads_back_check_catches_a_setting_in_the_wrong_section(monkeypatch):
    """Proves the check can fail, by moving a setting into the section ansible-core
    dropped. Without this the passing case above proves only that nothing is
    currently wrong."""
    monkeypatch.setitem(
        SETTINGS, "pipelining", replace(SETTINGS["pipelining"], section="ssh_connection")
    )
    config = AnsibleConfig(pipelining=True)
    got = validate.dump(config)
    assert got is not None
    assert "ANSIBLE_PIPELINING" not in got


@needs_ansible
def test_every_modelled_section_is_one_this_ansible_knows():
    """`SECTIONS` came from `ansible-config list`. If a future ansible drops one, the
    wizard would be writing into a section that does nothing."""
    for section in SECTIONS:
        config = AnsibleConfig(extra={section: {}})
        assert all(
            section not in check.name for check in validate.verify(config).failures
        ), section


@needs_ansible
def test_ansible_reference_config_can_be_generated(tmp_path):
    ok, message = validate.init_reference(tmp_path / "ansible.cfg.reference")
    assert ok, message
    assert "[defaults]" in (tmp_path / "ansible.cfg.reference").read_text()


@needs_ansible
def test_real_ansible_rejects_a_retired_section():
    """The other half of the ssh_connection finding: validate does flag the section,
    which is why the wizard reports it rather than silently rewriting the file."""
    config = AnsibleConfig(extra={"ssh_connection": {"pipelining": "True"}})
    report = validate.verify(config)
    assert any("accepts the file" in check.name for check in report.failures)
