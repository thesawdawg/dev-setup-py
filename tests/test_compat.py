"""Scanning an install source for things the host cannot do.

The bar this module has to clear is asymmetric: a missed incompatibility leaves
behaviour where it was, but a false one blocks a working install. Most of these tests
are therefore about *not* firing.
"""
from __future__ import annotations

import pytest

from dev_setup import catalog, compat, generic, platforms


@pytest.fixture(autouse=True)
def _clean():
    platforms.reset()
    compat.set_force(False)
    yield
    platforms.reset()
    compat.set_force(False)


def termux() -> platforms.Platform:
    return platforms.Platform(
        id="termux", name="Termux (Android)", family="termux",
        package_manager=platforms.TERMUX_PKG,
        prefix=platforms.Path(platforms.TERMUX_DEFAULT_PREFIX),
        traits=frozenset({platforms.ANDROID}),
    )


def debian() -> platforms.Platform:
    return platforms.Platform(
        id="ubuntu", name="Ubuntu", family="debian", package_manager=platforms.APT_GET,
        traits=frozenset({
            platforms.GLIBC, platforms.FHS, platforms.SUDO,
            platforms.APT, platforms.SYSTEMD,
        }),
    )


def signals(findings) -> set[str]:
    return {f.signal for f in findings}


# ── command extraction ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "script,expected",
    [
        ("sudo apt-get install -y x", {"sudo", "apt-get"}),
        ("if true; then sudo apt-get update; fi", {"sudo", "apt-get"}),
        ("while :; do apt-get x; done", {"apt-get"}),
        ("build && sudo dnf install y", {"sudo", "dnf"}),
        ("a || pacman -S z", {"pacman"}),
        ("curl x | sudo bash", {"sudo"}),
        ("exec sudo apk add q", {"sudo", "apk"}),
        ("DEBIAN_FRONTEND=noninteractive apt-get install x", {"apt-get"}),
    ],
)
def test_commands_found_in_every_shell_position(script, expected):
    assert compat.commands_in(script) & set(compat._REQUIRED_COMMANDS) == expected


def test_shell_keywords_do_not_hide_the_real_command():
    """`then` looks like a separator but isn't — the `;` before it already put us in
    command position, so treating `then` as one made it capture the command slot and
    swallow the `sudo apt-get` behind it."""
    assert "apt-get" in compat.commands_in("if [ -x /bin/x ]; then apt-get install y; fi")


def test_comments_are_not_invocations():
    """An installer that documents what it would do on Debian is not doing it."""
    script = "# on debian, run sudo apt-get install foo\n#   sudo dnf install foo\necho hi\n"
    assert not compat.commands_in(script) & set(compat._REQUIRED_COMMANDS)


def test_words_inside_strings_are_not_invocations():
    assert not compat.commands_in('echo "you may need sudo for this"') & {"sudo"}


# ── path and asset detection ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text,expected",
    [
        ("sudo mv f /usr/local/bin/f", "/usr/local/"),
        ("cp x /etc/foo.conf", "/etc/"),
        ("tar -C /opt/thing", "/opt/"),
        # A prefix-relative path is the *correct* one on Termux — flagging it would
        # be exactly backwards, so both expansion spellings must be skipped.
        ('install -m755 x "$PREFIX/etc/apt/f"', None),
        ('install -m755 x "${PREFIX}/usr/local/bin/f"', None),
        ("curl https://example.com/etc/release", None),
    ],
)
def test_fhs_path_detection(text, expected):
    match = compat._FHS_PATHS.search(text)
    assert (match.group(1) if match else None) == expected


# ── scanning a script ────────────────────────────────────────────────────────


def test_sudo_is_judged_by_the_trait_not_by_path(monkeypatch):
    """Termux ships a `sudo` package that is a root-device wrapper, so finding it on
    PATH proves nothing. The platform trait already encodes that."""
    monkeypatch.setattr(compat.shutil, "which", lambda name: f"/usr/bin/{name}")
    findings = compat.scan_script("sudo mv x y", platform=termux())
    assert "sudo" in signals(findings)


def test_sudo_is_fine_where_the_host_has_it(monkeypatch):
    monkeypatch.setattr(compat.shutil, "which", lambda name: f"/usr/bin/{name}")
    assert not compat.scan_script("sudo mv x y", platform=debian())


def test_a_missing_package_manager_binary_blocks(monkeypatch):
    """The measured rule: `set -e` plus "command not found" is the whole story."""
    monkeypatch.setattr(compat.shutil, "which", lambda name: None if name == "apt-get" else "/x")
    findings = compat.scan_script("apt-get install -y foo", platform=termux())
    assert "apt-get" in signals(findings)
    assert all(f.blocking for f in findings)
    # The message has to name what this host uses instead, or it isn't actionable.
    assert "`pkg`" in str(findings[0])


def test_a_present_package_manager_binary_does_not_block(monkeypatch):
    monkeypatch.setattr(compat.shutil, "which", lambda name: "/usr/bin/" + name)
    assert not signals(compat.scan_script("apt-get install -y foo", platform=debian()))


def test_systemctl_warns_but_never_blocks(monkeypatch):
    """`systemctl enable x 2>/dev/null || true` is an ordinary line in an installer
    that works fine without systemd. Fatal here would block working installs."""
    monkeypatch.setattr(compat.shutil, "which", lambda name: "/usr/bin/" + name)
    findings = compat.scan_script(
        "systemctl enable foo 2>/dev/null || true", platform=termux()
    )
    assert signals(findings) == {"systemctl"}
    assert not compat.blocking(findings)


def test_systemctl_is_silent_where_systemd_runs(monkeypatch):
    monkeypatch.setattr(compat.shutil, "which", lambda name: "/usr/bin/" + name)
    assert not compat.scan_script("systemctl enable foo", platform=debian())


def test_glibc_asset_blocks_on_a_non_glibc_host():
    script = "curl -fsSL -o x.tgz https://e.com/x-aarch64-unknown-linux-gnu.tar.gz"
    assert "linux-gnu" in signals(compat.scan_script(script, platform=termux()))
    assert not signals(compat.scan_script(script, platform=debian()))


def test_a_portable_script_produces_nothing(monkeypatch):
    """The prelude exists so scripts can be written this way — and a script that is
    must scan clean on every host."""
    monkeypatch.setattr(compat.shutil, "which", lambda name: "/usr/bin/" + name)
    script = (
        'set -euo pipefail\n'
        '$DEVSTUFF_PKG_INSTALL foo\n'
        '$DEVSTUFF_SUDO install -m755 ./foo "$DEVSTUFF_BIN/foo"\n'
    )
    assert not compat.scan_script(script, platform=termux())
    assert not compat.scan_script(script, platform=debian())


# ── scanning a tool ──────────────────────────────────────────────────────────


def _tool(**kwargs) -> generic.GenericTool:
    kwargs.setdefault("key", "demo")
    kwargs.setdefault("name", "Demo")
    return generic.GenericTool(**kwargs)


def test_system_type_blocks_when_no_manager_exists():
    nowhere = platforms.Platform(id="weird", name="Weird OS", family="unknown")
    findings = compat.scan(_tool(install_type="system", packages="htop"), platform=nowhere)
    assert signals(findings) == {"no-package-manager"}


def test_system_type_blocks_when_the_manager_is_not_installed(monkeypatch):
    monkeypatch.setattr(platforms.PackageManager, "available", lambda self: False)
    findings = compat.scan(_tool(install_type="system", packages="htop"), platform=debian())
    assert signals(findings) == {"apt-get"}


def test_system_type_is_clean_when_the_manager_is_there(monkeypatch):
    monkeypatch.setattr(platforms.PackageManager, "available", lambda self: True)
    assert not compat.scan(_tool(install_type="system", packages="htop"), platform=debian())


def test_git_install_cmd_is_scanned_too(monkeypatch):
    monkeypatch.setattr(compat.shutil, "which", lambda name: "/usr/bin/" + name)
    tool = _tool(install_type="git", git_url="https://x/y", git_install_cmd="sudo make install")
    assert "sudo" in signals(compat.scan(tool, platform=termux()))


def test_script_url_is_checked_for_glibc_assets():
    tool = _tool(install_type="script", script_url="https://e.com/x-linux-gnu.sh")
    assert "linux-gnu" in signals(compat.scan(tool, platform=termux()))


def test_remove_sources_are_never_scanned(monkeypatch):
    """Being refused permission to *remove* something already installed is the worst
    outcome available, so removal sources are deliberately out of scope."""
    monkeypatch.setattr(compat.shutil, "which", lambda name: "/usr/bin/" + name)
    tool = _tool(install_type="bash", install_script="echo hi", remove_script="sudo rm -rf /x")
    assert not compat.scan(tool, platform=termux())


# ── when the scan runs at all ────────────────────────────────────────────────


def _resolve(entry: dict, platform):
    validated = catalog.validate_catalog({"version": 1, "tools": {"demo": entry}})
    return catalog.resolve_for_platform(validated["demo"], platform)


def test_undeclared_entries_are_scanned():
    resolved = _resolve({"type": "bash", "install_script": "sudo x"}, termux())
    assert compat.should_scan(resolved)


def test_declaring_requires_traits_stands_the_scanner_down():
    """Even an empty list counts: the author considered portability, and a regex must
    not overrule them."""
    resolved = _resolve(
        {"type": "bash", "install_script": "sudo x", "requires_traits": []}, termux()
    )
    assert resolved.declared
    assert not compat.should_scan(resolved)


def test_a_matching_platform_block_stands_the_scanner_down():
    resolved = _resolve(
        {
            "type": "bash", "install_script": "sudo x",
            "platforms": {"termux": {"install_script": "pkg install x"}},
        },
        termux(),
    )
    assert not compat.should_scan(resolved)


def test_a_block_for_another_platform_does_not():
    resolved = _resolve(
        {
            "type": "bash", "install_script": "sudo x",
            "platforms": {"fedora": {"install_script": "dnf install x"}},
        },
        termux(),
    )
    assert compat.should_scan(resolved)


# ── refusal and --force ──────────────────────────────────────────────────────


def test_inferred_incompatibility_refuses_the_install():
    tool = _tool(
        install_type="bash", install_script="sudo x",
        unsupported_reason="runs sudo", unsupported_inferred=True,
    )
    with pytest.raises(RuntimeError, match="runs sudo"):
        tool.install()


def test_force_overrides_an_inferred_reason(monkeypatch):
    ran: dict = {}
    monkeypatch.setattr(generic, "_run_bash_script", lambda s: ran.setdefault("ok", True))
    compat.set_force(True)
    tool = _tool(
        install_type="bash", install_script="sudo x",
        unsupported_reason="runs sudo", unsupported_inferred=True,
    )
    tool.install()
    assert ran["ok"]


def test_force_never_overrides_a_declared_refusal():
    """A catalog's `supported: false` is an authored statement of fact, not a guess —
    unlike an inference, there is nothing for the user to know better about."""
    compat.set_force(True)
    tool = _tool(
        install_type="bash", install_script="true",
        unsupported_reason="Android has no container support",
    )
    with pytest.raises(RuntimeError, match="Android has no container support"):
        tool.install()


# ── the downloaded-script check ──────────────────────────────────────────────


def test_a_downloaded_installer_is_checked_before_it_runs(monkeypatch):
    """A `curl | sh` body does not exist until install time, so this is both the
    first chance to inspect it and the last before it changes anything."""
    monkeypatch.setattr(compat.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(
        generic, "_download_script", lambda url, expected_sha256="": "sudo rm -rf /usr/local/x"
    )
    ran: dict = {}
    monkeypatch.setattr(generic, "_run_bash_script", lambda s: ran.setdefault("ok", True))
    platforms.set_current(termux())

    tool = _tool(install_type="script", script_url="https://e.com/i.sh", compat_findings=[])
    with pytest.raises(RuntimeError, match="downloaded installer is not compatible"):
        generic._install_script_url(tool)
    assert "ok" not in ran


def test_a_downloaded_installer_runs_when_the_catalog_declared_the_platform(monkeypatch):
    """compat_findings is None when the entry declared something about this host. The
    author's judgement stands, downloaded body included."""
    monkeypatch.setattr(compat.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(
        generic, "_download_script", lambda url, expected_sha256="": "sudo rm -rf /usr/local/x"
    )
    ran: dict = {}
    monkeypatch.setattr(generic, "_run_bash_script", lambda s: ran.setdefault("ok", True))
    platforms.set_current(termux())

    tool = _tool(install_type="script", script_url="https://e.com/i.sh")
    assert tool.compat_findings is None
    generic._install_script_url(tool)
    assert ran["ok"]


def test_force_lets_a_downloaded_installer_run(monkeypatch):
    monkeypatch.setattr(compat.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(
        generic, "_download_script", lambda url, expected_sha256="": "sudo rm -rf /usr/local/x"
    )
    ran: dict = {}
    monkeypatch.setattr(generic, "_run_bash_script", lambda s: ran.setdefault("ok", True))
    platforms.set_current(termux())
    compat.set_force(True)

    generic._install_script_url(
        _tool(install_type="script", script_url="https://e.com/i.sh", compat_findings=[])
    )
    assert ran["ok"]


# ── registry integration ─────────────────────────────────────────────────────


def test_a_user_tool_with_an_incompatible_source_is_marked(monkeypatch, tmp_path):
    """The case this module exists for: nothing declared, so nothing else would have
    noticed until the script was half-run."""
    from dev_setup import registry

    user = tmp_path / "tools.yaml"
    user.write_text(
        "version: 1\n"
        "tools:\n"
        "  mytool:\n"
        "    name: My Tool\n"
        "    type: bash\n"
        "    check_cmd: mytool\n"
        "    install_script: |\n"
        "      set -euo pipefail\n"
        "      sudo mv ./mytool /usr/local/bin/mytool\n"
    )
    monkeypatch.setattr(catalog, "USER_CATALOG_PATH", user)
    monkeypatch.setattr(compat.shutil, "which", lambda name: "/usr/bin/" + name)
    platforms.set_current(termux())
    registry.reload()
    try:
        tool = registry.get("mytool")
        assert not tool.supported
        assert tool.unsupported_inferred
        assert "sudo" in tool.unsupported_reason
        assert "/usr/local/" in tool.unsupported_reason
    finally:
        platforms.reset()
        registry.reload()


def test_the_same_tool_is_fine_on_debian(monkeypatch, tmp_path):
    from dev_setup import registry

    user = tmp_path / "tools.yaml"
    user.write_text(
        "version: 1\n"
        "tools:\n"
        "  mytool:\n"
        "    name: My Tool\n"
        "    type: bash\n"
        "    check_cmd: mytool\n"
        "    install_script: |\n"
        "      sudo mv ./mytool /usr/local/bin/mytool\n"
    )
    monkeypatch.setattr(catalog, "USER_CATALOG_PATH", user)
    monkeypatch.setattr(compat.shutil, "which", lambda name: "/usr/bin/" + name)
    platforms.set_current(debian())
    registry.reload()
    try:
        assert registry.get("mytool").supported
    finally:
        platforms.reset()
        registry.reload()


def test_no_bundled_tool_is_blocked_by_the_scanner_on_its_own_host(monkeypatch):
    """Every bundled entry either declares its portability or scans clean here. A new
    builtin that trips the scanner on the platform it was written for is a bug in the
    entry — it should be saying so with `requires_traits`."""
    monkeypatch.setattr(compat.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(platforms.PackageManager, "available", lambda self: True)
    bundled = catalog.load_bundled_catalog()
    host = debian()
    for key, data in bundled.items():
        resolved = catalog.resolve_for_platform(data, host)
        if not resolved.supported or not compat.should_scan(resolved):
            continue
        tool = generic.GenericTool.from_dict(resolved.data, key=key)
        blockers = compat.blocking(compat.scan(tool, platform=host))
        assert not blockers, f"{key}: {[f.reason for f in blockers]}"
