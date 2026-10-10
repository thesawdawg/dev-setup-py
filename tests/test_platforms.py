"""Platform detection, the package-manager abstraction, and catalog resolution.

The Termux facts asserted here were taken from termux-tools and termux-packages
sources (see docs/specs/platform-compat/specifications.md for the citations), not
from recollection — where a test looks arbitrary, that is why.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from dev_setup import catalog, generic, platforms


@pytest.fixture(autouse=True)
def _clean_platform_cache():
    """Every test starts and ends with no cached platform."""
    platforms.reset()
    yield
    platforms.reset()


@pytest.fixture
def clear_env(monkeypatch):
    for var in (
        "DEVSTUFF_PLATFORM", "PREFIX", "TERMUX_VERSION", "TERMUX__ROOTFS",
        "TERMUX_APP_PACKAGE_MANAGER", "TERMUX_MAIN_PACKAGE_FORMAT",
    ):
        monkeypatch.delenv(var, raising=False)


def termux(**kwargs) -> platforms.Platform:
    base = dict(
        id="termux", name="Termux (Android)", family="termux",
        package_manager=platforms.TERMUX_PKG, traits=frozenset({platforms.ANDROID}),
    )
    return platforms.Platform(**{**base, **kwargs})


def debian(**kwargs) -> platforms.Platform:
    base = dict(
        id="ubuntu", name="Ubuntu", family="debian",
        package_manager=platforms.APT_GET,
        traits=frozenset({platforms.GLIBC, platforms.FHS, platforms.SUDO, platforms.APT}),
    )
    return platforms.Platform(**{**base, **kwargs})


# ── detection ────────────────────────────────────────────────────────────────


def test_termux_detected_from_prefix_and_version(monkeypatch, tmp_path, clear_env):
    prefix = tmp_path / "data" / "data" / "com.termux" / "files" / "usr"
    prefix.mkdir(parents=True)
    monkeypatch.setenv("PREFIX", str(prefix))
    monkeypatch.setenv("TERMUX_VERSION", "0.119.0")

    p = platforms.detect()
    assert p.id == "termux"
    assert p.family == "termux"
    assert p.prefix == prefix
    assert p.bin_dir == prefix / "bin"


def test_a_bare_prefix_variable_is_not_termux(monkeypatch, tmp_path, clear_env):
    """PREFIX is a common build variable; on its own it must not mean Android.

    Guarded by requiring the path to have the /files/usr shape *and* a corroborating
    Termux signal, so `PREFIX=/usr/local make install` in a shell doesn't reroute
    every install through `pkg`.
    """
    monkeypatch.setenv("PREFIX", str(tmp_path))
    assert platforms.detect().id != "termux"


def test_termux_prefix_shape_alone_needs_com_termux(monkeypatch, tmp_path, clear_env):
    prefix = tmp_path / "somefork" / "files" / "usr"
    prefix.mkdir(parents=True)
    monkeypatch.setenv("PREFIX", str(prefix))
    # No TERMUX_* env and not a com.termux path → not enough to claim Termux.
    assert platforms.detect().id != "termux"
    # …but a fork that exports TERMUX_VERSION is Termux enough.
    monkeypatch.setenv("TERMUX_VERSION", "0.119.0")
    assert platforms.detect().id == "termux"


def test_termux_pacman_build_is_detected(monkeypatch, tmp_path, clear_env):
    """`pkg` fronts apt *or* pacman, chosen by $TERMUX_APP_PACKAGE_MANAGER."""
    monkeypatch.setenv("TERMUX_VERSION", "0.119.0")
    monkeypatch.setenv("TERMUX_APP_PACKAGE_MANAGER", "pacman")
    monkeypatch.setattr(platforms, "_is_root", lambda: False)
    p = platforms.detect()
    assert p.package_manager is platforms.TERMUX_PKG_PACMAN
    assert p.package_manager.query[0] == "pacman"


def test_termux_falls_back_to_legacy_package_format_var(monkeypatch, clear_env):
    """Older termux-app exports TERMUX_MAIN_PACKAGE_FORMAT instead."""
    monkeypatch.setenv("TERMUX_VERSION", "0.118.0")
    monkeypatch.setenv("TERMUX_MAIN_PACKAGE_FORMAT", "pacman")
    monkeypatch.setattr(platforms, "_is_root", lambda: False)
    assert platforms.detect().package_manager is platforms.TERMUX_PKG_PACMAN


def test_root_termux_bypasses_the_pkg_wrapper(monkeypatch, clear_env):
    """`pkg` exits immediately when `id -u` is 0, so root has to use apt directly."""
    monkeypatch.setenv("TERMUX_VERSION", "0.119.0")
    monkeypatch.setattr(platforms, "_is_root", lambda: True)
    pm = platforms.detect().package_manager
    assert pm is platforms.TERMUX_APT
    assert pm.binary == "apt"


def test_termux_never_claims_sudo(monkeypatch, clear_env):
    """Termux ships a `sudo` package (a root-device wrapper). Finding it on PATH
    must not turn into `sudo pkg install`."""
    monkeypatch.setenv("TERMUX_VERSION", "0.119.0")
    monkeypatch.setattr(platforms, "_is_root", lambda: False)
    monkeypatch.setattr(platforms.shutil, "which", lambda name: f"/usr/bin/{name}")
    p = platforms.detect()
    assert not p.has(platforms.SUDO)
    assert not p.needs_escalation
    assert platforms.escalate(["pkg", "install", "bat"], platform=p) == ["pkg", "install", "bat"]


def test_termux_lacks_glibc_fhs_and_apt_repos(monkeypatch, clear_env):
    monkeypatch.setenv("TERMUX_VERSION", "0.119.0")
    p = platforms.detect()
    for trait in (platforms.GLIBC, platforms.FHS, platforms.APT, platforms.SYSTEMD):
        assert not p.has(trait), trait
    assert p.has(platforms.ANDROID)


def test_termux_disables_uv_managed_python_downloads(monkeypatch, clear_env):
    """python-build-standalone has no Bionic target, so uv must never try."""
    monkeypatch.setenv("TERMUX_VERSION", "0.119.0")
    p = platforms.detect()
    assert p.env["UV_PYTHON_DOWNLOADS"] == "never"
    env = platforms.child_env(p)
    assert env is not None and env["UV_PYTHON_DOWNLOADS"] == "never"


def test_ordinary_host_adds_no_child_env(clear_env):
    assert platforms.child_env(debian()) is None


def test_os_release_parsing(tmp_path):
    f = tmp_path / "os-release"
    f.write_text('NAME="Ubuntu"\nID=ubuntu\nID_LIKE=debian\n# comment\n\nVERSION_ID="24.04"\n')
    data = platforms.read_os_release(f)
    assert data["ID"] == "ubuntu"
    assert data["ID_LIKE"] == "debian"
    assert data["VERSION_ID"] == "24.04"


def test_os_release_missing_returns_empty(tmp_path):
    assert platforms.read_os_release(tmp_path / "nope") == {}


def test_linux_family_comes_from_id_like(monkeypatch, tmp_path, clear_env):
    f = tmp_path / "os-release"
    f.write_text('PRETTY_NAME="Linux Mint 22"\nID=linuxmint\nID_LIKE="ubuntu debian"\n')
    data = platforms.read_os_release(f)
    monkeypatch.setattr(platforms, "read_os_release", lambda path=None: data)
    monkeypatch.setattr(platforms.PackageManager, "available", lambda self: self.id == "apt")
    p = platforms._detect_linux()
    assert p.family == "debian"
    assert p.package_manager is platforms.APT_GET


def test_linux_probes_path_when_os_release_lies(monkeypatch, tmp_path, clear_env):
    """os-release can name a family whose manager isn't installed (minimal images).
    Probing PATH is the measured answer."""
    f = tmp_path / "os-release"
    f.write_text("ID=debian\n")
    data = platforms.read_os_release(f)
    monkeypatch.setattr(platforms, "read_os_release", lambda path=None: data)
    monkeypatch.setattr(platforms.PackageManager, "available", lambda self: self.id == "apk")
    p = platforms._detect_linux()
    assert p.package_manager is platforms.APK
    assert p.detected_from == "PATH probe"


def test_unknown_linux_has_no_package_manager(monkeypatch, clear_env):
    monkeypatch.setattr(platforms, "read_os_release", lambda path=None: {})
    monkeypatch.setattr(platforms.PackageManager, "available", lambda self: False)
    p = platforms._detect_linux()
    assert p.package_manager is None
    with pytest.raises(RuntimeError, match="No supported system package manager"):
        platforms.package_manager(p)


def test_forced_platform_override(monkeypatch, clear_env):
    monkeypatch.setenv("DEVSTUFF_PLATFORM", "termux")
    p = platforms.detect()
    assert p.id == "termux"
    assert "override" in p.detected_from


def test_unknown_forced_platform_is_ignored(monkeypatch, clear_env):
    monkeypatch.setenv("DEVSTUFF_PLATFORM", "haiku")
    assert platforms.detect().id != "haiku"


# ── package manager argv ─────────────────────────────────────────────────────


def test_install_and_remove_argv():
    assert platforms.APT_GET.install_argv(["htop"]) == ["apt-get", "install", "-y", "htop"]
    assert platforms.TERMUX_PKG.install_argv(["bat"]) == ["pkg", "install", "-y", "bat"]
    assert platforms.TERMUX_PKG.remove_argv(["bat"]) == ["pkg", "uninstall", "-y", "bat"]
    assert platforms.DNF.install_argv(["htop"]) == ["dnf", "install", "-y", "htop"]


def test_version_pinning_uses_the_manager_separator():
    assert platforms.APT_GET.install_argv(["gh"], version="2.0") == [
        "apt-get", "install", "-y", "gh=2.0",
    ]
    assert platforms.DNF.install_argv(["gh"], version="2.0") == [
        "dnf", "install", "-y", "gh-2.0",
    ]


def test_pinning_refused_where_the_manager_cannot_express_it():
    with pytest.raises(RuntimeError, match="not supported by pacman"):
        platforms.PACMAN.install_argv(["gh"], version="2.0")


def test_pinning_refused_for_multiple_packages():
    with pytest.raises(RuntimeError, match="single package"):
        platforms.APT_GET.install_argv(["a", "b"], version="1.0")


def test_upgrade_with_a_version_becomes_a_pinned_install():
    assert platforms.APT_GET.upgrade_argv(["gh"], version="2.0") == [
        "apt-get", "install", "-y", "gh=2.0",
    ]
    assert platforms.APT_GET.upgrade_argv(["gh"]) == [
        "apt-get", "install", "--only-upgrade", "-y", "gh",
    ]


def test_termux_pkg_has_no_separate_refresh():
    """`pkg install` already runs mirror selection and an apt-cache refresh."""
    assert platforms.TERMUX_PKG.refresh_argv() is None
    assert platforms.APT_GET.refresh_argv() == ["apt-get", "update", "-q"]


def test_query_marker_matching():
    assert platforms.APT_GET.query_ok("htop", 0, "Status: install ok installed\n")
    assert not platforms.APT_GET.query_ok("htop", 0, "Status: deinstall ok config-files\n")
    assert not platforms.APT_GET.query_ok("htop", 1, "")
    # apk prints the package name when present and nothing when absent
    assert platforms.APK.query_ok("htop", 0, "htop-3.3.0-r0\n")
    assert not platforms.APK.query_ok("htop", 0, "")
    # pacman has no marker — the exit code is the answer
    assert platforms.PACMAN.query_ok("htop", 0, "")


# ── escalation ───────────────────────────────────────────────────────────────


def test_escalate_prefixes_sudo_on_debian(monkeypatch):
    monkeypatch.setattr(platforms, "_is_root", lambda: False)
    assert platforms.escalate(["apt-get", "install"], platform=debian()) == [
        "sudo", "apt-get", "install",
    ]


def test_escalate_skips_sudo_when_already_root(monkeypatch):
    """A root container often has no sudo at all, which used to turn every system
    install into 'sudo: command not found'."""
    monkeypatch.setattr(platforms, "_is_root", lambda: True)
    assert platforms.escalate(["apt-get", "install"], platform=debian()) == [
        "apt-get", "install",
    ]


def test_escalate_skips_sudo_for_unprivileged_managers(monkeypatch):
    monkeypatch.setattr(platforms, "_is_root", lambda: False)
    brew_host = platforms.Platform(
        id="macos", name="macOS", family="macos",
        package_manager=platforms.BREW, traits=frozenset({platforms.FHS, platforms.SUDO}),
    )
    assert platforms.escalate(["brew", "install", "yq"], platform=brew_host) == [
        "brew", "install", "yq",
    ]


# ── script prelude ───────────────────────────────────────────────────────────


def test_prelude_sets_every_variable(monkeypatch):
    monkeypatch.setattr(platforms, "_is_root", lambda: False)
    text = platforms.script_prelude(debian())
    assert "DEVSTUFF_SUDO=sudo" in text
    assert "DEVSTUFF_PKG_INSTALL='sudo apt-get install -y'" in text
    assert "DEVSTUFF_PLATFORM=ubuntu" in text
    assert text.rstrip().splitlines()[-1].startswith("export ")


def test_prelude_leaves_sudo_empty_on_termux():
    """`$DEVSTUFF_SUDO cmd` has to degrade to `cmd`, not to `'' cmd`."""
    text = platforms.script_prelude(termux())
    assert "DEVSTUFF_SUDO=''" in text
    assert "DEVSTUFF_PKG_INSTALL='pkg install -y'" in text


def test_prelude_is_inserted_after_a_shebang():
    body = platforms.with_prelude("#!/usr/bin/env bash\nset -eu\necho hi\n", platform=termux())
    lines = body.splitlines()
    assert lines[0] == "#!/usr/bin/env bash"
    assert "DEVSTUFF_PLATFORM=termux" in body
    assert lines[-1] == "echo hi"


def test_prelude_prepends_when_there_is_no_shebang():
    body = platforms.with_prelude("set -eu\necho hi\n", platform=termux())
    assert body.startswith("# devstuff platform prelude")
    assert body.endswith("echo hi\n")


def test_script_runner_injects_the_prelude(monkeypatch):
    seen: dict = {}

    def capture(cmd, **kwargs):
        seen["body"] = Path(cmd[-1]).read_text()

    monkeypatch.setattr(generic, "_run", capture)
    platforms.set_current(termux())
    generic._run_bash_script("set -eu\necho hi\n")
    assert "DEVSTUFF_PKG_INSTALL='pkg install -y'" in seen["body"]


# ── generic.py dispatch through the platform ─────────────────────────────────


def test_system_install_uses_the_host_manager(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(generic, "_run", lambda cmd, **kw: calls.append(cmd))
    monkeypatch.setattr(generic, "_probe", lambda cmd, **kw: calls.append(cmd))
    monkeypatch.setattr(platforms, "_is_root", lambda: False)

    platforms.set_current(termux())
    generic.GenericTool(key="bat", install_type="system", packages="bat").install()
    # No refresh call on Termux, and no sudo.
    assert calls == [["pkg", "install", "-y", "bat"]]

    calls.clear()
    platforms.set_current(debian())
    generic.GenericTool(key="bat", install_type="system", packages="bat").install()
    assert calls == [
        ["sudo", "apt-get", "update", "-q"],
        ["sudo", "apt-get", "install", "-y", "bat"],
    ]


def test_apt_type_is_an_alias_of_system(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(generic, "_run", lambda cmd, **kw: calls.append(cmd))
    monkeypatch.setattr(generic, "_probe", lambda cmd, **kw: None)
    monkeypatch.setattr(platforms, "_is_root", lambda: True)
    platforms.set_current(debian())
    generic.GenericTool(key="java", install_type="apt", apt_packages="openjdk-21-jdk").install()
    assert calls == [["apt-get", "install", "-y", "openjdk-21-jdk"]]


def test_system_remove_prefers_an_explicit_remove_script(monkeypatch):
    ran: dict = {}
    monkeypatch.setattr(generic, "_run_bash_script", lambda s: ran.setdefault("script", s))
    platforms.set_current(termux())
    generic.GenericTool(
        key="git-lfs", install_type="system", packages="git-lfs",
        remove_script="git lfs uninstall",
    ).remove()
    assert ran["script"] == "git lfs uninstall"


def test_unsupported_tool_refuses_to_install():
    tool = generic.GenericTool(
        key="docker", name="Docker", install_type="bash", install_script="true",
        unsupported_reason="Android has no container support", alternative="ollama",
    )
    assert not tool.supported
    with pytest.raises(RuntimeError) as exc:
        tool.install()
    assert "Android has no container support" in str(exc.value)
    assert "devstuff install ollama" in str(exc.value)


def test_unsupported_tool_refuses_to_update():
    tool = generic.GenericTool(
        key="docker", install_type="bash", install_script="true",
        unsupported_reason="nope",
    )
    with pytest.raises(RuntimeError, match="not available on this platform"):
        tool.update()


def test_unsupported_state_is_never_written_to_the_catalog():
    tool = generic.GenericTool(
        key="x", install_type="system", packages="x",
        unsupported_reason="nope", alternative="y",
    )
    assert "unsupported_reason" not in tool.to_dict()
    assert "alternative" not in tool.to_dict()


def test_system_packages_prefers_the_canonical_field():
    assert generic.GenericTool(key="x", packages="a b").system_packages == ["a", "b"]
    assert generic.GenericTool(key="x", apt_packages="c").system_packages == ["c"]


# ── catalog resolution ───────────────────────────────────────────────────────


def _resolve(entry: dict, platform: platforms.Platform) -> catalog.ResolvedTool:
    validated = catalog.validate_catalog({"version": 1, "tools": {"demo": entry}})
    return catalog.resolve_for_platform(validated["demo"], platform)


def test_platform_override_replaces_fields():
    resolved = _resolve(
        {
            "type": "bash", "install_script": "curl ...",
            "platforms": {"termux": {"type": "system", "packages": "bat"}},
        },
        termux(),
    )
    assert resolved.data["type"] == "system"
    assert resolved.data["packages"] == "bat"
    assert resolved.supported
    # The block itself never survives into the runtime record.
    assert "platforms" not in resolved.data


def test_family_override_applies_then_id_narrows_it():
    entry = {
        "type": "system", "packages": "base",
        "platforms": {"debian": {"packages": "deb"}, "ubuntu": {"packages": "ubu"}},
    }
    assert _resolve(entry, debian()).data["packages"] == "ubu"
    assert _resolve(entry, debian(id="debian")).data["packages"] == "deb"


def test_other_platforms_are_untouched():
    entry = {
        "type": "bash", "install_script": "x",
        "platforms": {"termux": {"type": "system", "packages": "bat"}},
    }
    assert _resolve(entry, debian()).data["type"] == "bash"


def test_explicit_unsupported_carries_reason_and_alternative():
    resolved = _resolve(
        {
            "type": "bash", "install_script": "x",
            "platforms": {"termux": {"supported": False, "reason": "no daemon", "alternative": "ollama"}},
        },
        termux(),
    )
    assert not resolved.supported
    assert resolved.unsupported_reason == "no daemon"
    assert resolved.alternative == "ollama"


def test_missing_traits_make_a_tool_unsupported():
    resolved = _resolve(
        {"type": "bash", "install_script": "x", "requires_traits": ["glibc", "sudo"]},
        termux(),
    )
    assert not resolved.supported
    assert "prebuilt *-linux-gnu binaries run here" in resolved.unsupported_reason
    assert "privilege escalation via sudo" in resolved.unsupported_reason


def test_satisfied_traits_leave_a_tool_supported():
    resolved = _resolve(
        {"type": "bash", "install_script": "x", "requires_traits": ["glibc", "sudo"]},
        debian(),
    )
    assert resolved.supported


def test_override_resets_traits_so_the_swap_takes_effect():
    resolved = _resolve(
        {
            "type": "bash", "install_script": "x", "requires_traits": ["glibc"],
            "platforms": {"termux": {"requires_traits": [], "type": "system", "packages": "bat"}},
        },
        termux(),
    )
    assert resolved.supported
    assert resolved.data["packages"] == "bat"


def test_explicit_supported_true_overrules_the_trait_table():
    resolved = _resolve(
        {
            "type": "bash", "install_script": "x", "requires_traits": ["glibc"],
            "platforms": {"termux": {"supported": True}},
        },
        termux(),
    )
    assert resolved.supported


def test_requires_traits_is_not_a_runtime_field():
    """It drives resolution only; GenericTool must never see it."""
    resolved = _resolve({"type": "bash", "install_script": "x", "requires_traits": []}, debian())
    assert "requires_traits" not in resolved.data
    generic.GenericTool.from_dict(resolved.data, key="demo")  # must not raise


# ── catalog validation ───────────────────────────────────────────────────────


def _validate(entry: dict):
    return catalog.validate_catalog({"version": 1, "tools": {"demo": entry}})


def test_unknown_trait_is_rejected():
    with pytest.raises(catalog.CatalogError, match="unknown trait"):
        _validate({"type": "bash", "requires_traits": ["gilbc"]})


def test_packages_rejected_on_a_non_system_type():
    with pytest.raises(catalog.CatalogError, match="only valid on type system/apt"):
        _validate({"type": "bash", "packages": "htop"})


def test_packages_and_apt_packages_together_are_rejected():
    with pytest.raises(catalog.CatalogError, match="both 'packages' and 'apt_packages'"):
        _validate({"type": "system", "packages": "a", "apt_packages": "b"})


def test_unsupported_without_a_reason_is_rejected():
    with pytest.raises(catalog.CatalogError, match="gives no 'reason'"):
        _validate({"type": "bash", "platforms": {"termux": {"supported": False}}})


def test_unknown_field_inside_a_platform_block_is_rejected():
    with pytest.raises(catalog.CatalogError, match="unknown field"):
        _validate({"type": "bash", "platforms": {"termux": {"pakcages": "bat"}}})


def test_platform_blocks_do_not_nest():
    with pytest.raises(catalog.CatalogError, match="unknown field"):
        _validate({"type": "bash", "platforms": {"termux": {"platforms": {}}}})


def test_override_swapping_mechanism_must_restate_traits():
    """Merging is literal per key, so an override that changed `type` but inherited
    `requires_traits: [glibc]` would report itself unavailable on the very platform
    it was written for. That has to be a load error, not a surprise."""
    with pytest.raises(catalog.CatalogError, match="does not restate 'requires_traits'"):
        _validate({
            "type": "bash", "install_script": "x", "requires_traits": ["glibc"],
            "platforms": {"termux": {"type": "system", "packages": "bat"}},
        })


def test_override_marking_unsupported_need_not_restate_traits():
    _validate({
        "type": "bash", "install_script": "x", "requires_traits": ["glibc"],
        "platforms": {"termux": {"supported": False, "reason": "no"}},
    })


def test_type_rules_apply_inside_platform_blocks():
    with pytest.raises(catalog.CatalogError, match="only valid on type pip/uvx"):
        _validate({
            "type": "uvx", "pip_name": "x",
            "platforms": {
                "termux": {"type": "system", "packages": "x", "uv_python": "3.12"},
            },
        })


# ── the bundled catalog ──────────────────────────────────────────────────────


def test_bundled_catalog_resolves_on_every_known_platform():
    """Every platform must produce a loadable registry — no crashes, no half-records."""
    bundled = catalog.load_bundled_catalog()
    for spec in ("termux", "debian", "fedora", "arch", "alpine", "suse", "macos"):
        p = platforms._build_forced(spec)
        assert p is not None, spec
        for key, data in bundled.items():
            resolved = catalog.resolve_for_platform(data, p)
            tool = generic.GenericTool.from_dict(resolved.data, key=key)
            if resolved.supported and tool.install_type in ("system", "apt"):
                assert tool.system_packages, f"{key} on {spec} has no packages"


def test_every_termux_override_names_a_real_mechanism():
    """A termux block must leave the tool installable: either it is marked
    unsupported with a reason, or it resolves to a type with the fields that type
    needs. Catches an override that swaps `type` and forgets the package name."""
    bundled = catalog.load_bundled_catalog()
    p = platforms._build_forced("termux")
    required_field = {
        "system": "packages", "apt": "packages", "npm": "npm_name",
        "uvx": "pip_name", "pip": "pip_name", "git": "git_url",
        "script": "script_url", "bash": "install_script",
    }
    for key, data in bundled.items():
        if "termux" not in (data.get("platforms") or {}):
            continue
        resolved = catalog.resolve_for_platform(data, p)
        if not resolved.supported:
            assert resolved.unsupported_reason
            continue
        tool = generic.GenericTool.from_dict(resolved.data, key=key)
        field_name = required_field[tool.install_type]
        value = tool.system_packages if field_name == "packages" else getattr(tool, field_name)
        assert value, f"{key}: termux override has no {field_name}"


# Which script fields a resolved tool will actually execute, by install type.
# Merging is key-wise, so a record keeps the base entry's install_script even after
# an override swaps `type` to system — that copy is dead data, and only the scripts
# the resolved type still dispatches to are worth asserting on.
_LIVE_SCRIPTS = {
    "bash": ("install_script", "remove_script"),
    "script": ("remove_script",),
    "system": ("remove_script",),
    "apt": ("remove_script",),
    "git": (),
    "npm": (),
    "uvx": (),
    "pip": (),
}


def test_no_termux_override_leaves_a_sudo_apt_script_live():
    """The hazard a `remove_script: ""` guards against.

    A block that swaps `type` to system but keeps the base `remove_script` looks
    fine — until removal runs `sudo apt-get remove` on a host with neither.
    """
    bundled = catalog.load_bundled_catalog()
    p = platforms._build_forced("termux")
    for key, data in bundled.items():
        resolved = catalog.resolve_for_platform(data, p)
        if not resolved.supported:
            continue
        tool = generic.GenericTool.from_dict(resolved.data, key=key)
        for script_field in _LIVE_SCRIPTS.get(tool.install_type, ()):
            body = getattr(tool, script_field) or ""
            assert "sudo " not in body, f"{key}.{script_field} still calls sudo on Termux"
            assert "apt-get" not in body, f"{key}.{script_field} still calls apt-get on Termux"
