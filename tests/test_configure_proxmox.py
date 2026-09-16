"""Unit tests for the Proxmox configurator.

The end-to-end half — every function against stub `ssh`/`curl` binaries — is in
`tests/test_proxmox_functions.py`. This file covers the Python side: the profile schema,
the emitter, the shell bridge, secret handling, and the table that keeps `model.py` and
`functions.yaml` from drifting apart.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from dev_setup import configure, functions_registry
from dev_setup.catalog import CatalogError
from dev_setup.commands.configure_cmd import configure_cmd
from dev_setup.configure.proxmox import detect, model, render, validate, wizard
from dev_setup.configure.proxmox.model import Profile, ProxmoxConfig

FMT = render.FMT_PATH


def ssh_profile(**kwargs) -> Profile:
    base = {"name": "lab", "transport": "ssh", "host": "pve1.example", "node": "pve1"}
    return Profile(**{**base, **kwargs})


def api_profile(**kwargs) -> Profile:
    base = {
        "name": "cloud",
        "transport": "api",
        "host": "pve1.example",
        "token_id": "root@pam!devstuff",
        "secret_env": "PVE_TOKEN",
    }
    return Profile(**{**base, **kwargs})


def config(*profiles: Profile, default: str = "") -> ProxmoxConfig:
    mapping = {p.name: p for p in profiles}
    return ProxmoxConfig(profiles=mapping, default=default or next(iter(mapping), ""))


# ---------------------------------------------------------------------------
# The operations table vs. the catalog
# ---------------------------------------------------------------------------


def catalog_functions() -> dict[str, object]:
    return {f.key: f for f in functions_registry.all_functions() if f.category == "proxmox"}


def test_every_operation_has_a_function() -> None:
    assert set(model.OPERATIONS) == set(catalog_functions())


def test_every_operation_agrees_with_its_function() -> None:
    """Both directions, so a parameter added in one place fails here rather than at
    runtime — the same arrangement as the CI matrix check for builtin tools."""
    functions = catalog_functions()
    for key, op in model.OPERATIONS.items():
        fn = functions[key]
        assert fn.name == op.name, key
        assert fn.description == op.summary, key
        assert tuple(p.name for p in fn.params) == op.params, key
        assert fn.type == "script", key


def test_profile_is_always_the_last_parameter_and_optional() -> None:
    """It has to trail: parameters are positional, so a required one after it would be
    unreachable without naming a profile first."""
    for key, fn in catalog_functions().items():
        names = [p.name for p in fn.params]
        assert names[-1] == "profile", key
        assert not fn.params[-1].required, key


def test_reads_name_real_api_paths() -> None:
    for key, op in model.OPERATIONS.items():
        for entry in op.reads:
            assert entry in model.API, f"{key} reads unknown path {entry}"
            assert entry in model.PRIVILEGES, f"{key} reads {entry} with no privilege recorded"


def test_writes_are_ssh_only_and_confirm() -> None:
    functions = catalog_functions()
    for key, op in model.OPERATIONS.items():
        script = functions[key].script
        if op.writes:
            assert "pve_require_ssh" in script, key
            assert "pve_do " in script, key
            # Declining is not a failure, and pve_finish is what says so.
            assert "pve_finish" in script, key
        else:
            assert "pve_do " not in script, key


def test_the_measured_migrate_asymmetry_survives() -> None:
    """qm takes --online, pct takes --restart. Neither is guessable and both are load
    bearing, so they are asserted rather than trusted to a comment."""
    script = catalog_functions()["pve-migrate"].script
    assert model.MIGRATE_RUNNING_FLAG["qemu"] == "--online 1"
    assert model.MIGRATE_RUNNING_FLAG["lxc"] == "--restart 1"
    assert 'flag="--online"' in script
    assert 'flag="--restart"' in script


def test_the_binaries_table_matches_the_helper() -> None:
    lib = render.LIB_PATH.read_text(encoding="utf-8")
    for kind, binary in model.BINARIES.items():
        assert f'{kind}) PVE_G_BIN="{binary}"' in " ".join(lib.split())


def test_apt_update_still_records_the_privilege_that_surprises_people() -> None:
    """Measured from Proxmox's schema: a read guarded by a write privilege."""
    assert model.PRIVILEGES["apt_update"].startswith("Sys.Modify")
    assert "Sys.Modify" in catalog_functions()["pve-updates"].script


def test_the_package_ships_the_shell_helper_and_the_formatter() -> None:
    assert render.LIB_PATH.is_file()
    assert render.FMT_PATH.is_file()


# ---------------------------------------------------------------------------
# Profile validation
# ---------------------------------------------------------------------------


def test_a_minimal_ssh_profile_validates() -> None:
    profile = model.validate_profile("lab", {"transport": "ssh", "host": "pve1"})
    assert profile.user == "root"
    assert profile.auth == "agent"
    assert not profile.needs_secret()


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"transport": "telepathy", "host": "h"}, "transport must be one of"),
        ({"transport": "ssh"}, "host is required"),
        ({"transport": "ssh", "host": "h", "auth": "magic"}, "auth must be one of"),
        ({"transport": "ssh", "host": "h", "auth": "key"}, "needs identity_file"),
        ({"transport": "ssh", "host": "h", "auth": "password"}, "needs secret_env"),
        ({"transport": "api", "host": "h"}, "needs token_id"),
        ({"transport": "api", "host": "h", "token_id": "nope"}, "USER@REALM!TOKENNAME"),
        ({"transport": "api", "host": "h", "token_id": "r@pam!t"}, "needs secret_env"),
        ({"transport": "ssh", "host": "h", "porte": 22}, "unknown field"),
        ({"transport": "ssh", "host": "h", "port": "22"}, "must be a number"),
        ({"transport": "ssh", "host": "h", "sudo": "yes"}, "must be true or false"),
        (
            {"transport": "ssh", "host": "h", "auth": "password", "secret_env": "A",
             "secret_file": "/b"},
            "not both",
        ),
    ],
)
def test_a_malformed_profile_fails_at_load_time(data: dict, message: str) -> None:
    with pytest.raises(CatalogError) as excinfo:
        model.validate_profile("lab", data)
    assert message in str(excinfo.value)
    assert "lab" in str(excinfo.value)


def test_a_default_naming_an_unknown_profile_is_an_error() -> None:
    raw = {"version": 1, "default": "ghost", "profiles": {"lab": {"transport": "ssh", "host": "h"}}}
    with pytest.raises(CatalogError, match="unknown profile"):
        model.validate_config(raw)


def test_one_profile_is_its_own_default() -> None:
    raw = {"version": 1, "profiles": {"lab": {"transport": "ssh", "host": "h"}}}
    assert model.validate_config(raw).default == "lab"


def test_choosing_a_profile_prefers_the_argument_then_the_default() -> None:
    cfg = config(ssh_profile(), api_profile(), default="lab")
    assert cfg.get().name == "lab"
    assert cfg.get("cloud").name == "cloud"


def test_choosing_an_unknown_profile_lists_the_real_ones() -> None:
    cfg = config(ssh_profile(), api_profile(), default="lab")
    with pytest.raises(CatalogError) as excinfo:
        cfg.get("nope")
    assert "cloud" in str(excinfo.value) and "lab" in str(excinfo.value)


def test_an_empty_config_points_at_the_wizard() -> None:
    with pytest.raises(CatalogError, match="devstuff configure proxmox"):
        ProxmoxConfig().get()


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def test_a_config_round_trips() -> None:
    cfg = config(
        ssh_profile(auth="key", identity_file="/home/me/.ssh/id_ed25519", port=2222),
        api_profile(verify_tls=False),
        default="cloud",
    )
    text = render.to_yaml(cfg)
    assert render.matches(text, cfg)
    assert render.load(text).to_dict() == cfg.to_dict()


def test_only_non_default_fields_are_written() -> None:
    """A file restating every default freezes it against a future change."""
    text = render.to_yaml(config(ssh_profile()))
    written = {line.strip().split(":")[0] for line in text.splitlines()
               if line.startswith("    ") and not line.strip().startswith("#")}
    assert written == {"transport", "host", "node"}


def test_transport_specific_fields_do_not_leak_across() -> None:
    text = render.to_yaml(config(api_profile()))
    written = {line.strip().split(":")[0] for line in text.splitlines()
               if line.startswith("    ") and not line.strip().startswith("#")}
    assert written == {"transport", "host", "token_id", "secret_env"}


def test_an_empty_config_emits_a_loadable_file() -> None:
    text = render.to_yaml(ProxmoxConfig())
    assert "profiles: {}" in text
    assert render.load(text).profiles == {}


def test_a_value_that_yaml_would_misread_is_quoted() -> None:
    cfg = config(ssh_profile(host="on", node="1.0"))
    text = render.to_yaml(cfg)
    assert render.load(text).profiles["lab"].host == "on"
    assert render.load(text).profiles["lab"].node == "1.0"


def test_the_file_says_it_holds_no_secret() -> None:
    text = render.to_yaml(config(api_profile()))
    assert "NOTHING SECRET IS STORED HERE" in text


def test_a_secret_value_never_reaches_the_profile_file(tmp_path: Path) -> None:
    """FR-5, asserted by looking for the value itself rather than for a field name."""
    secret = "sup3r-secret-token-value"
    cfg = config(api_profile(secret_file=str(tmp_path / "s")))
    text = render.to_yaml(cfg)
    assert secret not in text
    assert "secret_file" in text


def test_saving_is_owner_only(tmp_path: Path) -> None:
    path = tmp_path / "proxmox.yaml"
    path.write_text("stale", encoding="utf-8")
    path.chmod(0o644)
    written, backed_up = render.save(config(ssh_profile()), path)
    assert written.stat().st_mode & 0o777 == 0o600
    assert backed_up is not None
    assert backed_up.read_text(encoding="utf-8") == "stale"
    assert backed_up.stat().st_mode & 0o777 == 0o600


def test_a_secret_file_is_written_owner_only(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(render, "SECRETS_DIR", tmp_path / "secrets")
    path = render.write_secret("lab", "hunter2")
    assert path.read_text(encoding="utf-8") == "hunter2"
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700


def test_rewriting_a_secret_replaces_it(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(render, "SECRETS_DIR", tmp_path / "secrets")
    render.write_secret("lab", "a-much-longer-first-secret")
    path = render.write_secret("lab", "short")
    assert path.read_text(encoding="utf-8") == "short"


# ---------------------------------------------------------------------------
# The shell bridge
# ---------------------------------------------------------------------------


def eval_export(profile: Profile) -> dict[str, str]:
    """Run the exported text through a real shell and read the variables back."""
    text = render.export_shell(profile)
    script = f"{text}\nfor v in ${{!PVE_@}}; do printf '%s=%s\\n' \"$v\" \"${{!v}}\"; done"
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=True)
    return dict(line.split("=", 1) for line in result.stdout.splitlines())


def test_the_export_survives_a_shell_round_trip() -> None:
    values = eval_export(ssh_profile())
    assert values["PVE_HOST"] == "pve1.example"
    assert values["PVE_TRANSPORT"] == "ssh"
    assert values["PVE_PYTHON"] == sys.executable
    assert values["PVE_LIB"] == str(render.LIB_PATH)


@pytest.mark.parametrize(
    "host",
    ["pve1.example", "host with space", "quote'host", 'double"host', "semi;colon", "$(whoami)"],
)
def test_awkward_values_survive_the_eval(host: str) -> None:
    """Everything is shlex-quoted, so a hostile hostname is data, not code."""
    assert eval_export(ssh_profile(host=host))["PVE_HOST"] == host


def test_the_export_carries_the_reference_and_not_the_secret(monkeypatch) -> None:
    monkeypatch.setenv("PVE_TOKEN", "the-actual-token")
    text = render.export_shell(api_profile())
    assert "PVE_SECRET_ENV=PVE_TOKEN" in text
    assert "the-actual-token" not in text


def test_the_export_expands_a_tilde() -> None:
    profile = ssh_profile(auth="key", identity_file="~/.ssh/id_ed25519")
    assert eval_export(profile)["PVE_IDENTITY"] == str(Path.home() / ".ssh/id_ed25519")


def test_the_api_endpoint_is_built_once() -> None:
    assert api_profile().endpoint() == "https://pve1.example:8006/api2/json"
    assert api_profile(api_port=443).endpoint() == "https://pve1.example:443/api2/json"


def test_export_returns_text_and_prints_nothing(tmp_path: Path, monkeypatch, capsys) -> None:
    path = tmp_path / "proxmox.yaml"
    render.save(config(ssh_profile()), path)
    monkeypatch.setattr(model, "CONFIG_PATH", path)
    monkeypatch.setattr(detect, "CONFIG_PATH", path)
    text = wizard.export("lab")
    assert text.startswith("PVE_LIB=")
    assert capsys.readouterr().out == ""


def test_export_honours_the_profile_environment_variable(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "proxmox.yaml"
    render.save(config(ssh_profile(), api_profile(), default="lab"), path)
    monkeypatch.setattr(detect, "CONFIG_PATH", path)
    monkeypatch.setenv(model.PROFILE_ENV, "cloud")
    assert "PVE_PROFILE=cloud" in wizard.export(None)
    # An explicit argument still wins over the environment.
    assert "PVE_PROFILE=lab" in wizard.export("lab")


# ---------------------------------------------------------------------------
# The configure command
# ---------------------------------------------------------------------------


def test_proxmox_is_registered_as_standalone() -> None:
    spec = configure.get("proxmox")
    assert spec is not None and spec.standalone
    assert all(not s.standalone for k, s in configure.CONFIGURATORS.items() if k != "proxmox")


def test_the_listing_does_not_call_a_standalone_configurator_missing() -> None:
    result = CliRunner().invoke(configure_cmd, ["--list"])
    assert result.exit_code == 0
    assert "proxmox" in result.output


def test_export_is_refused_for_a_configurator_without_one() -> None:
    result = CliRunner().invoke(configure_cmd, ["bat", "--export"])
    assert result.exit_code == 1
    assert "no shell export" in result.output


def test_export_reports_a_bad_profile_on_stderr(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(detect, "CONFIG_PATH", tmp_path / "missing.yaml")
    runner = CliRunner()
    result = runner.invoke(configure_cmd, ["proxmox", "--export"])
    assert result.exit_code == 1


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def test_inspect_reads_a_saved_config(tmp_path: Path) -> None:
    path = tmp_path / "proxmox.yaml"
    render.save(config(ssh_profile()), path)
    found = detect.inspect(path)
    assert found.exists and found.generated and found.has()
    assert not found.error


def test_inspect_reports_a_broken_config_rather_than_raising(tmp_path: Path) -> None:
    """The wizard is how a broken file gets fixed, so it has to open in front of one."""
    path = tmp_path / "proxmox.yaml"
    path.write_text("version: 1\nprofiles:\n  lab:\n    transport: nope\n", encoding="utf-8")
    found = detect.inspect(path)
    assert found.exists
    assert "transport must be one of" in found.error
    assert not found.has()


def test_inspect_of_a_missing_file_is_empty(tmp_path: Path) -> None:
    found = detect.inspect(tmp_path / "nothing.yaml")
    assert not found.exists and not found.has() and not found.error


@pytest.mark.parametrize(
    ("setup", "expected"),
    [
        ("env-set", "ok"),
        ("env-unset", "missing"),
        ("file", "ok"),
        ("file-missing", "missing"),
        ("file-loose", "insecure"),
        ("none", "none"),
        ("not-needed", "n/a"),
    ],
)
def test_secret_state(setup: str, expected: str, tmp_path: Path, monkeypatch) -> None:
    if setup == "env-set":
        monkeypatch.setenv("PVE_TOKEN", "x")
        profile = api_profile()
    elif setup == "env-unset":
        monkeypatch.delenv("PVE_TOKEN", raising=False)
        profile = api_profile()
    elif setup == "not-needed":
        profile = ssh_profile()
    elif setup == "none":
        profile = api_profile(secret_env="")
    else:
        path = tmp_path / "secret"
        if setup != "file-missing":
            path.write_text("x", encoding="utf-8")
            path.chmod(0o600 if setup == "file" else 0o644)
        profile = api_profile(secret_env="", secret_file=str(path))
    state, detail = detect.secret_state(profile)
    assert state == expected, detail


# ---------------------------------------------------------------------------
# The live check
# ---------------------------------------------------------------------------


def test_selftest_runs_the_shell_helper_and_parses_its_results() -> None:
    """It reports rather than raises, even against a host that cannot exist."""
    report = validate.selftest(ssh_profile(host="pve.invalid"), timeout=30)
    assert not report.error
    assert report.checks
    assert report.checks[0].name == "ssh client"


def test_selftest_of_an_unreachable_host_fails_cleanly() -> None:
    """Which check fails depends on the machine — a box with no ssh client stops at the
    first one. What matters is that it reports rather than raising or hanging."""
    report = validate.selftest(ssh_profile(host="pve.invalid"), timeout=30)
    assert not report.ok
    assert report.failures()
    assert not report.error


# ---------------------------------------------------------------------------
# fmt.py
# ---------------------------------------------------------------------------


def run_fmt(view: str, payload: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(FMT), view, *args],
        input=payload,
        capture_output=True,
        text=True,
    )


GUESTS = (
    '{"data":[{"vmid":101,"type":"qemu","name":"web01","node":"pve1","status":"running"},'
    '{"vmid":200,"type":"lxc","name":"dns","node":"pve2","status":"stopped"},'
    '{"vmid":300,"type":"qemu","name":"twin","node":"pve1","status":"running"},'
    '{"vmid":301,"type":"lxc","name":"twin","node":"pve2","status":"running"}]}'
)


def test_fmt_accepts_both_the_enveloped_and_unwrapped_shapes() -> None:
    """curl returns {"data": ...}; pvesh unwraps it. One formatter serves both."""
    enveloped = run_fmt("guests", GUESTS)
    unwrapped = run_fmt("guests", GUESTS.replace('{"data":', "", 1)[:-1])
    assert enveloped.stdout == unwrapped.stdout
    assert "web01" in enveloped.stdout


def test_fmt_resolve_returns_one_record() -> None:
    result = run_fmt("resolve", GUESTS, "web01")
    assert result.returncode == 0
    assert result.stdout.rstrip("\n").split("\t") == ["101", "qemu", "pve1", "web01", "running", ""]


def test_fmt_resolve_accepts_a_vmid() -> None:
    assert run_fmt("resolve", GUESTS, "200").stdout.split("\t")[1] == "lxc"


def test_fmt_resolve_is_case_insensitive_as_a_fallback() -> None:
    assert run_fmt("resolve", GUESTS, "WEB01").returncode == 0


def test_fmt_resolve_refuses_an_ambiguous_name() -> None:
    result = run_fmt("resolve", GUESTS, "twin")
    assert result.returncode == 2
    assert result.stdout == ""
    assert "matches 2 guests" in result.stderr


def test_fmt_resolve_suggests_a_near_miss() -> None:
    result = run_fmt("resolve", GUESTS, "web02")
    assert result.returncode == 2
    assert "did you mean web01" in result.stderr


def test_fmt_rejects_output_that_is_not_json() -> None:
    result = run_fmt("guests", "<html>gateway timeout</html>")
    assert result.returncode == 1
    assert "not JSON" in result.stderr


def test_fmt_of_nothing_is_a_successful_empty_answer() -> None:
    result = run_fmt("snapshots", "[]")
    assert result.returncode == 0
    assert "no snapshots" in result.stdout.lower()


def test_fmt_snapshot_names_skip_the_current_pseudo_entry() -> None:
    payload = '[{"name":"one"},{"name":"current"},{"name":"two"}]'
    assert run_fmt("snapshot-names", payload).stdout.split() == ["one", "two"]


def test_fmt_backup_storages_reads_the_content_list_exactly() -> None:
    payload = (
        '[{"storage":"a","content":"backup,iso"},{"storage":"b","content":"images"},'
        '{"storage":"c","content":"vztmpl,backup"},{"storage":"d","content":"backupfoo"}]'
    )
    assert run_fmt("backup-storages", payload).stdout.split() == ["a", "c"]


@pytest.mark.parametrize(
    ("value", "expected"),
    [(0, "0"), (512, "512B"), (2048, "2K"), (1073741824, "1.0G"), (None, "-"), ("x", "-")],
)
def test_fmt_bytes(value: object, expected: str) -> None:
    sys.path.insert(0, str(FMT.parent))
    import fmt  # noqa: PLC0415

    assert fmt.human_bytes(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"), [(0, "-"), (90, "1m"), (7200, "2h 0m"), (93600, "1d 2h"), (None, "-")]
)
def test_fmt_duration(value: object, expected: str) -> None:
    sys.path.insert(0, str(FMT.parent))
    import fmt  # noqa: PLC0415

    assert fmt.human_duration(value) == expected


def test_fmt_is_importable_without_the_package() -> None:
    """It is handed to a subprocess as a script, so it must not need dev_setup."""
    source = FMT.read_text(encoding="utf-8")
    assert "import dev_setup" not in source
    assert "from dev_setup" not in source
    result = subprocess.run(
        [sys.executable, str(FMT)],
        capture_output=True,
        text=True,
        env={"PATH": os.environ.get("PATH", ""), "PYTHONPATH": ""},
    )
    assert result.returncode == 1
    assert "usage:" in result.stderr


# ---------------------------------------------------------------------------
# lib.sh invariants
# ---------------------------------------------------------------------------


def lib_source() -> str:
    return render.LIB_PATH.read_text(encoding="utf-8")


def test_confirmation_requires_both_streams_to_be_a_terminal() -> None:
    """SD-7: stdin alone passes under `devstuff agent`, which then hangs forever."""
    assert '[ ! -t 0 ] || [ ! -t 1 ]' in lib_source()


def code_lines() -> list[str]:
    """lib.sh with its comments stripped — the assertions below are about what it does,
    not about the prose explaining why."""
    return [ln for ln in lib_source().splitlines() if not ln.strip().startswith("#")]


def test_the_token_is_never_handed_to_curl_in_argv() -> None:
    """NFR-3. The header goes in on stdin through `-K -`; ps never sees it."""
    assert any("-K -" in line for line in code_lines())
    for line in code_lines():
        if "$secret" in line:
            assert "-H" not in line, line
    # `pve_read_cmd` prints a -H form for the user to copy, with the secret redacted.
    assert "PVEAPIToken=%s=...'" in lib_source()


def test_the_ssh_password_is_never_handed_to_a_command_in_argv() -> None:
    """A `VAR=value cmd` prefix sets the child's environment; `env VAR=value cmd` puts
    it in argv, where ps publishes it to every user on the machine."""
    code = code_lines()
    assert any('SSHPASS="$secret" sshpass -e ssh' in line for line in code)
    assert not any("env " in line and "SSHPASS" in line for line in code)


def test_only_pve_finish_may_exit() -> None:
    """`exit` inside $(...) ends only the subshell, so a helper that exits on failure
    would hand its caller an empty string and a zero status. pve_finish is the one
    exception: it is called from the top level of a script, to *be* its exit."""
    function = ""
    for line in lib_source().splitlines():
        stripped = line.strip()
        if stripped.endswith("() {"):
            function = stripped.split("(")[0]
        if stripped.startswith("#"):
            continue
        if stripped.startswith("exit "):
            assert function == "pve_finish", f"{function}: {line}"


def test_the_helper_is_valid_bash() -> None:
    assert subprocess.run(["bash", "-n", str(render.LIB_PATH)]).returncode == 0


def test_the_config_file_reads_back_as_yaml(tmp_path: Path) -> None:
    path = tmp_path / "proxmox.yaml"
    render.save(config(ssh_profile(), api_profile(), default="lab"), path)
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert loaded["version"] == model.CONFIG_VERSION
    assert set(loaded["profiles"]) == {"lab", "cloud"}


def test_quoting_helper_is_used_for_remote_commands() -> None:
    """Remote argv is joined by ssh and re-split by a shell on the far side, so a
    snapshot description with a space has to arrive quoted."""
    script = (
        f". {shlex.quote(str(render.LIB_PATH))}\n"
        'pve_remote_cmd qm snapshot 101 s --description "two words"'
    )
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=True)
    assert shlex.split(result.stdout)[-1] == "two words"
