from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from dev_setup import configure
from dev_setup.configure.docker import detect, render, validate, wizard
from dev_setup.configure.docker.model import (
    BUILTIN_DRIVERS,
    GROUPS,
    LOG_DRIVERS,
    PRESETS,
    SETTINGS,
    SYSTEM_PATH,
    DockerConfig,
    valid_size,
)

HAS_DOCKERD = shutil.which("dockerd") is not None
needs_dockerd = pytest.mark.skipif(not HAS_DOCKERD, reason="dockerd is not installed")


def cfg(preset: str = "rotation", **kwargs) -> DockerConfig:
    config = DockerConfig(preset=preset, **kwargs)
    if preset in PRESETS and not kwargs:
        detect.apply_preset(config, preset, detect.Docker())
    return config


def emitted(config: DockerConfig) -> dict:
    return json.loads(render.to_json(config))


# -- the model's own invariants --------------------------------------------------


def test_every_setting_names_a_group_that_exists():
    for setting in SETTINGS.values():
        assert setting.group in GROUPS, setting.key


def test_every_setting_maps_to_a_field_on_the_config():
    blank = DockerConfig()
    for key, setting in SETTINGS.items():
        if setting.kind == "logopts":
            continue  # derived from three fields rather than being one
        assert hasattr(blank, key), key


def test_setting_defaults_match_the_configs_own_defaults():
    """A default recorded in two places is a default that drifts — and the emitter
    omits a value equal to `Setting.default`, so a mismatch would silently write
    keys nobody asked for."""
    blank = DockerConfig()
    for key, setting in SETTINGS.items():
        if setting.kind in ("logopts", "list", "pools"):
            continue
        assert getattr(blank, key) == setting.default, key


def test_json_keys_are_unique():
    keys = [setting.json_key for setting in SETTINGS.values()]
    assert len(keys) == len(set(keys))


def test_every_modelled_key_is_in_the_emitters_order_table():
    for setting in SETTINGS.values():
        if setting.kind == "logopts":
            assert "log-opts" in render.KEY_ORDER
            continue
        assert setting.json_key in render.KEY_ORDER, setting.json_key


def test_every_preset_names_real_fields():
    blank = DockerConfig()
    aliases = {"log_max_size", "log_max_file"}
    for preset in PRESETS.values():
        for name in preset.values:
            assert hasattr(blank, name) or name in aliases, f"{preset.key}: {name}"


@pytest.mark.parametrize("preset", sorted(PRESETS))
def test_every_preset_produces_a_config_that_round_trips(preset):
    config = cfg(preset)
    assert render.matches(render.to_json(config), config)


@pytest.mark.parametrize(
    "preset", [key for key in sorted(PRESETS) if key not in ("empty", "current")]
)
def test_every_real_preset_caps_the_logs(preset):
    """The point of the wizard. A preset that leaves logs uncapped is the bug it
    exists to prevent, so `empty` and `current` are the only exemptions."""
    assert cfg(preset).logs_are_capped(), preset


def test_empty_preset_writes_an_empty_object():
    config = cfg("empty")
    assert emitted(config) == {}


# -- log options are driver-specific ---------------------------------------------


def test_journald_drops_rotation_options():
    """Measured against the daemon: journald rejects max-size/max-file/compress with
    "unknown log opt", and *every container fails to start*. `dockerd --validate`
    accepts it. So the model drops them rather than emitting them."""
    config = DockerConfig(
        log_driver="journald", log_max_size="10m", log_max_file="3", log_compress=True
    )
    assert config.log_opts() == {}
    assert config.dropped_log_opts() == ["max-size", "max-file", "compress"]
    assert "log-opts" not in emitted(config)


def test_dropping_options_produces_a_warning_rather_than_silence():
    config = DockerConfig(log_driver="journald", log_max_size="10m")
    assert any("does not take" in warning for warning in config.warnings())


def test_local_driver_does_take_tag_and_labels():
    """The obvious guess is that `local` only takes the rotation options. Measured:
    it takes tag/labels/env too."""
    for opt in ("tag", "labels", "env"):
        assert opt in LOG_DRIVERS["local"].opts


def test_log_opt_values_are_always_strings():
    """The one type error `dockerd --validate` catches, and it catches it by
    refusing to start the daemon."""
    config = cfg("server")
    for value in emitted(config)["log-opts"].values():
        assert isinstance(value, str)


def test_log_opts_carried_over_from_disk_survive_if_the_driver_takes_them():
    config = DockerConfig(log_driver="json-file", log_extra={"tag": "{{.Name}}"})
    assert config.log_opts()["tag"] == "{{.Name}}"


def test_log_opts_carried_over_are_dropped_if_the_driver_does_not():
    config = DockerConfig(log_driver="journald", log_extra={"max-size": "10m"})
    assert "max-size" not in config.log_opts()


@pytest.mark.parametrize(
    ("value", "ok"),
    [("10m", True), ("512k", True), ("2g", True), ("100", True), ("10mb", False),
     ("", False), ("m10", False), ("1.5g", True)],
)
def test_size_validation(value, ok):
    assert valid_size(value) is ok


# -- the checks dockerd does not do ----------------------------------------------


def test_compress_with_one_file_is_caught():
    """Measured: "compress cannot be true when max-file is less than 2 or max-size is
    not set" — and `dockerd --validate` says OK, then every container fails."""
    config = DockerConfig(log_max_size="10m", log_max_file="1", log_compress=True)
    report = validate.verify(config)
    assert not report.ok
    assert any("compression" in check.name for check in report.failures)


def test_compress_with_two_files_is_fine():
    config = DockerConfig(log_max_size="10m", log_max_file="2", log_compress=True)
    assert validate.verify(config).ok


def test_uncapped_logs_are_reported_as_a_failure():
    config = DockerConfig(log_driver="json-file")
    report = validate.verify(config)
    assert any("capped" in check.name for check in report.failures)


def test_journald_is_not_reported_as_uncapped():
    """journald does its own rotation, so "no max-size" is correct there rather than
    dangerous."""
    config = DockerConfig(log_driver="journald")
    assert config.logs_are_capped()
    assert all("capped" not in check.name for check in validate.verify(config).failures)


def test_address_pool_narrower_than_its_base_is_caught():
    config = DockerConfig(log_max_size="10m", address_pools=[("10.201.0.0/16", 8)])
    report = validate.verify(config)
    assert any("wider than" in check.detail for check in report.failures)


def test_address_pool_with_no_usable_addresses_is_caught():
    config = DockerConfig(log_max_size="10m", address_pools=[("10.201.0.0/16", 31)])
    assert not validate.verify(config).ok


def test_a_sane_address_pool_passes_and_reports_its_capacity():
    config = DockerConfig(log_max_size="10m", address_pools=[("10.201.0.0/16", 24)])
    report = validate.verify(config)
    assert report.ok
    assert any("256 networks" in check.detail for check in report.checks)


def test_unparseable_address_pool_is_caught_without_raising():
    config = DockerConfig(log_max_size="10m", address_pools=[("not-a-network", 24)])
    assert not validate.verify(config).ok


def test_hosts_conflicts_with_a_systemd_unit_that_passes_dash_h():
    """Both set the same thing and dockerd refuses to start with both. The error
    mentions flags, never daemon.json."""
    config = DockerConfig(log_max_size="10m", extra={"hosts": ["tcp://0.0.0.0:2375"]})
    found = detect.Docker(systemd=True, unit_sets_host=True)
    assert not validate.verify(config, found).ok


def test_hosts_is_fine_when_nothing_else_sets_it():
    config = DockerConfig(log_max_size="10m", extra={"hosts": ["tcp://0.0.0.0:2375"]})
    assert validate.verify(config, detect.Docker(systemd=True, unit_sets_host=False)).ok


def test_a_driver_this_daemon_cannot_load_is_caught():
    config = DockerConfig(log_driver="journald", log_max_size="")
    found = detect.Docker(daemon=True, log_plugins=("json-file", "local"))
    report = validate.verify(config, found)
    assert any("log driver exists" in check.name for check in report.failures)


def test_builtin_none_is_not_reported_missing_from_the_plugin_list():
    """`docker info` lists log *plugins*, and `none` is built in — measured: it works
    on a daemon whose plugin list does not mention it."""
    assert "none" in BUILTIN_DRIVERS
    found = detect.Docker(daemon=True, log_plugins=("json-file", "local"))
    report = validate.verify(DockerConfig(log_driver="none"), found)
    assert all("log driver exists" not in check.name for check in report.failures)


def test_driver_availability_is_silent_when_the_daemon_could_not_be_asked():
    """An unanswerable question must not become a false accusation."""
    found = detect.Docker(daemon=False, log_plugins=())
    report = validate.verify(DockerConfig(log_driver="journald"), found)
    assert all("log driver exists" not in check.name for check in report.checks)


# -- emitting --------------------------------------------------------------------


def test_defaults_are_omitted_rather_than_restated():
    """A config that writes out the defaults freezes them: a future Docker changing
    one would never reach this machine."""
    config = DockerConfig(log_max_size="10m", log_max_file="3")
    written = emitted(config)
    assert "log-driver" not in written  # json-file is the default
    assert "icc" not in written
    assert "shutdown-timeout" not in written
    assert written["log-opts"] == {"max-size": "10m", "max-file": "3"}


def test_a_non_default_is_written():
    assert emitted(DockerConfig(live_restore=True))["live-restore"] is True


def test_address_pools_are_emitted_as_json_lists_not_tuples():
    """The model holds tuples; JSON has no tuples. `matches()` is what keeps this
    conversion from being forgotten."""
    config = DockerConfig(address_pools=[("10.201.0.0/16", 24)])
    assert emitted(config)["default-address-pools"] == [{"base": "10.201.0.0/16", "size": 24}]
    assert render.matches(render.to_json(config), config)


def test_keys_are_emitted_in_the_tables_order():
    config = cfg("server")
    written = list(emitted(config))
    modelled = [key for key in render.KEY_ORDER if key in written]
    assert written[: len(modelled)] == modelled


def test_unmodelled_keys_are_carried_through_untouched():
    extra = {"features": {"containerd-snapshotter": True}, "exec-opts": ["a=b"]}
    written = emitted(DockerConfig(log_max_size="10m", extra=extra))
    for key, value in extra.items():
        assert written[key] == value


def test_the_file_ends_with_a_newline():
    assert render.to_json(cfg("rotation")).endswith("\n")


def test_matches_rejects_text_that_is_not_the_config():
    assert not render.matches('{"log-driver": "local"}', cfg("rotation"))


def test_matches_survives_text_that_is_not_json():
    assert not render.matches("not json at all", cfg("rotation"))


# -- reading an existing config back ---------------------------------------------


def test_a_hand_written_config_round_trips_without_loss():
    """daemon.json is a flat JSON object with no comments, so unlike a pre-commit
    config it *can* be read back faithfully — and anything unmodelled is preserved
    rather than dropped."""
    raw = {
        "log-driver": "local",
        "log-opts": {"max-size": "20m", "max-file": "4", "compress": "true", "tag": "{{.Name}}"},
        "live-restore": True,
        "dns": ["1.1.1.1"],
        "default-address-pools": [{"base": "10.5.0.0/16", "size": 26}],
        "insecure-registries": ["reg.internal:5000"],
        "features": {"containerd-snapshotter": True},
        "exec-opts": ["native.cgroupdriver=systemd"],
    }
    config = detect.from_existing(detect.Docker(existing=raw))
    assert emitted(config) == raw


def test_unmodelled_keys_land_in_extra():
    raw = {"features": {"x": True}, "log-driver": "local"}
    config = detect.from_existing(detect.Docker(existing=raw))
    assert set(config.extra) == {"features"}


def test_wrongly_typed_values_on_disk_are_ignored_not_crashed_on():
    raw = {"live-restore": "yes please", "shutdown-timeout": "soon", "dns": "1.1.1.1"}
    config = detect.from_existing(detect.Docker(existing=raw))
    assert config.live_restore is False
    assert config.shutdown_timeout == SETTINGS["shutdown_timeout"].default


def test_a_malformed_pool_entry_does_not_break_the_read():
    raw = {"default-address-pools": [{"base": "10.5.0.0/16"}, {"base": "10.6.0.0/16", "size": 24}]}
    config = detect.from_existing(detect.Docker(existing=raw))
    assert config.address_pools == [("10.6.0.0/16", 24)]


def test_reading_an_invalid_file_is_reported_rather_than_raising(tmp_path):
    path = tmp_path / "daemon.json"
    path.write_text("{ not json", encoding="utf-8")
    parsed, text, unreadable, invalid = detect._read_existing(path)
    assert invalid and not unreadable and parsed == {}
    assert text.startswith("{ not")


def test_reading_a_missing_file_is_not_an_error(tmp_path):
    parsed, text, unreadable, invalid = detect._read_existing(tmp_path / "nope.json")
    assert (parsed, text, unreadable, invalid) == ({}, "", False, False)


def test_a_json_array_on_disk_counts_as_invalid(tmp_path):
    path = tmp_path / "daemon.json"
    path.write_text("[1, 2]", encoding="utf-8")
    _, _, _, invalid = detect._read_existing(path)
    assert invalid


# -- presets and suggestion ------------------------------------------------------


def test_applying_a_preset_keeps_carried_over_keys():
    """A preset chooses settings; it does not decide to throw away keys the wizard
    never modelled."""
    config = DockerConfig(extra={"features": {"x": True}})
    detect.apply_preset(config, "server", detect.Docker())
    assert config.extra == {"features": {"x": True}}


def test_applying_a_preset_clears_the_previous_ones_values():
    config = cfg("server")
    assert config.metrics_addr
    detect.apply_preset(config, "rotation", detect.Docker())
    assert not config.metrics_addr


def test_suggest_adds_rotation_to_an_existing_config_that_lacks_it():
    """The overwhelmingly common real state: a daemon.json that sets something else
    and leaves the logs uncapped."""
    found = detect.Docker(existing={"live-restore": True})
    config = detect.suggest(found)
    assert config.logs_are_capped()
    assert config.live_restore is True


def test_suggest_leaves_an_already_capped_config_alone():
    found = detect.Docker(existing={"log-opts": {"max-size": "1g", "max-file": "2"}})
    config = detect.suggest(found)
    assert config.log_max_size == "1g"


def test_suggest_starts_from_rotation_when_there_is_no_config():
    config = detect.suggest(detect.Docker())
    assert config.preset == "rotation"
    assert config.logs_are_capped()


def test_rootless_daemons_get_their_own_config_path(monkeypatch):
    """A rootless daemon never opens /etc/docker/daemon.json, so writing there would
    need sudo for a file nothing reads."""
    monkeypatch.setattr(detect, "_info", lambda: {"SecurityOptions": ["name=rootless"]})
    monkeypatch.setattr(detect.shutil, "which", lambda _: "/usr/bin/docker")
    found = detect.inspect()
    assert found.rootless
    assert found.path != SYSTEM_PATH


# -- warnings --------------------------------------------------------------------


def test_insecure_registries_are_called_out():
    config = DockerConfig(log_max_size="10m", insecure_registries=["reg:5000"])
    assert any("certificate" in warning for warning in config.warnings())


def test_metrics_on_loopback_do_not_warn_but_a_public_bind_does():
    assert not any(
        "reachable off-host" in w
        for w in DockerConfig(log_max_size="10m", metrics_addr="127.0.0.1:9323").warnings()
    )
    assert any(
        "reachable off-host" in w
        for w in DockerConfig(log_max_size="10m", metrics_addr="0.0.0.0:9323").warnings()
    )


def test_moving_the_data_root_warns_that_it_does_not_migrate():
    config = DockerConfig(log_max_size="10m", data_root="/mnt/docker")
    assert any("already under the old" in warning for warning in config.warnings())


# -- writing ---------------------------------------------------------------------


def test_write_without_sudo_creates_the_file_and_its_parent(tmp_path):
    path = tmp_path / "etc" / "docker" / "daemon.json"
    ok, message = validate.write(cfg("rotation"), path, sudo=False)
    assert ok, message
    assert json.loads(path.read_text())["log-opts"]["max-size"] == "10m"


def test_backup_copies_the_previous_file(tmp_path):
    path = tmp_path / "daemon.json"
    path.write_text('{"debug": true}', encoding="utf-8")
    dest, _ = validate.backup(path, sudo=False)
    assert dest is not None
    assert json.loads(dest.read_text()) == {"debug": True}


def test_backup_of_a_missing_file_is_not_an_error(tmp_path):
    dest, message = validate.backup(tmp_path / "nope.json", sudo=False)
    assert dest is None and "no existing" in message


def test_diff_is_empty_when_nothing_changes():
    text = render.to_json(cfg("rotation"))
    assert render.diff(text, text) == []


def test_diff_shows_what_changes():
    lines = render.diff(render.to_json(cfg("rotation")), render.to_json(cfg("server")))
    assert any(line.startswith("+") for line in lines)


# -- registry wiring -------------------------------------------------------------


def test_docker_is_registered_as_a_configurator():
    assert configure.has("docker")
    assert configure.get("docker").module.endswith("docker.wizard")


def test_the_wizard_module_satisfies_the_configurator_contract():
    module = configure.get("docker").load()
    assert callable(module.run)
    assert callable(module.config_path)


def test_config_path_is_the_daemons_own(monkeypatch):
    monkeypatch.setattr(detect, "inspect", lambda: detect.Docker(path=Path("/somewhere/x.json")))
    assert wizard.config_path() == Path("/somewhere/x.json")


# -- against the real binary -----------------------------------------------------


@needs_dockerd
@pytest.mark.parametrize("preset", sorted(PRESETS))
def test_every_preset_is_accepted_by_real_dockerd(preset, tmp_path):
    path = tmp_path / "daemon.json"
    path.write_text(render.to_json(cfg(preset)), encoding="utf-8")
    result = subprocess.run(
        ["dockerd", "--validate", "--config-file", str(path)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout


@needs_dockerd
def test_a_config_with_every_setting_is_accepted_by_real_dockerd(tmp_path):
    config = DockerConfig(
        log_driver="local",
        log_max_size="10m",
        log_max_file="3",
        log_compress=True,
        live_restore=True,
        shutdown_timeout=30,
        max_concurrent_downloads=6,
        address_pools=[("10.201.0.0/16", 24)],
        dns=["1.1.1.1"],
        userland_proxy=False,
        registry_mirrors=["https://mirror.example.com"],
        insecure_registries=["reg.internal:5000"],
        data_root="/var/lib/docker-alt",
        no_new_privileges=True,
        icc=False,
        metrics_addr="127.0.0.1:9323",
        debug=True,
    )
    path = tmp_path / "daemon.json"
    path.write_text(render.to_json(config), encoding="utf-8")
    result = subprocess.run(
        ["dockerd", "--validate", "--config-file", str(path)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout


@needs_dockerd
def test_real_dockerd_rejects_a_key_typo():
    """The one thing `--validate` is genuinely good at, and the reason it is used at
    all rather than replaced by the wizard's own checks."""
    config = DockerConfig(log_max_size="10m", extra={"lof-driver": "json-file"})
    report = validate.verify(config)
    assert any("dockerd accepts" in check.name for check in report.failures)
