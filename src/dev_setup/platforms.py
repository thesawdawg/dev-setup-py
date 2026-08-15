"""Host platform detection and the system-package-manager abstraction.

devstuff started life assuming one host shape: a Debian/Ubuntu box with `sudo`,
`apt-get`, and the FHS (`/usr/local/bin`, `/etc`). That assumption is spread across
`generic.py` (``sudo apt-get install -y``) and every ``install_script`` in
``tools.yaml``. This module is the single place that knows what host we are actually
on, so the rest of the code can ask instead of assume.

Three things it answers:

* **Which package manager**, and how to drive it (:class:`PackageManager`).
* **Whether to prefix a command with sudo** (:func:`escalate`) — which is *not* the
  same question as "is sudo installed".
* **What the host can't do** (:attr:`Platform.traits`) — so a tool that unpacks a
  ``*-linux-gnu`` tarball into ``/usr/local/bin`` can say so once and be correctly
  refused on Termux *and* Alpine, rather than failing halfway through.

Everything here is measured at run time — ``/etc/os-release``, env vars the platform
itself exports, and ``shutil.which`` — never inferred from ``sys.platform`` alone.

Termux specifics, verified against termux-tools and termux-packages sources rather
than recalled; each is load-bearing and is explained where it is used below:

* ``pkg`` is a shell wrapper that dispatches to **apt or pacman** depending on
  ``$TERMUX_APP_PACKAGE_MANAGER``. Both builds exist in the wild.
* ``pkg`` **refuses to run as root** (``id -u == 0`` is a hard exit), so a root
  Termux session has to talk to apt/pacman directly.
* There is no privilege escalation to ask for: ``$PREFIX`` is owned by the app user.
  Termux's ``sudo`` package is agnostic-apollo's wrapper for *rooted* devices and its
  presence on PATH must not be read as "sudo works here".
* ``$PREFIX`` is ``/data/data/com.termux/files/usr``. There is no ``/usr/local/bin``,
  and binaries are Bionic-linked, so ``*-linux-gnu`` release tarballs do not run.
"""

from __future__ import annotations

import os
import shlex
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

# ─── traits ──────────────────────────────────────────────────────────────────
#
# A trait is a *capability of the host*, not a property of a distro. Tools declare
# the ones their install mechanism needs (`requires_traits` in tools.yaml) and the
# catalog marks them unsupported where a trait is absent, with the text below as the
# explanation the user sees.

GLIBC = "glibc"
FHS = "fhs"
SUDO = "sudo"
SYSTEMD = "systemd"
APT = "apt"
ANDROID = "android"

TRAITS: dict[str, str] = {
    GLIBC: "prebuilt *-linux-gnu binaries run here",
    FHS: "a filesystem hierarchy with /usr/local/bin",
    SUDO: "privilege escalation via sudo",
    SYSTEMD: "systemd is running as init",
    APT: "Debian-style apt repositories (add-apt-repository, /etc/apt/sources.list.d)",
    ANDROID: "the Android runtime",
}


# ─── package managers ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PackageManager:
    """How to drive one system package manager.

    Held as argv fragments rather than shell strings: every caller passes these
    straight to :mod:`subprocess` as a list, so a package name is one argv element
    and never reaches a shell.
    """

    id: str
    binary: str
    install: tuple[str, ...]
    remove: tuple[str, ...]
    upgrade: tuple[str, ...]
    query: tuple[str, ...]
    refresh: tuple[str, ...] | None = None
    # Substring stdout must contain for `query` to count as installed. "{pkg}" is
    # substituted with the package name. Empty means "trust the exit code".
    query_marker: str = ""
    # How a pinned version is joined to the package name ("pkg=1.2", "pkg-1.2").
    # None means this manager has no way to request one, and pinning is refused.
    version_sep: str | None = None
    # False for managers that must NOT be run through sudo (Termux's pkg, brew).
    privileged: bool = True

    def install_argv(self, packages: list[str], *, version: str | None = None) -> list[str]:
        return [self.binary, *self.install, *self._pin(packages, version)]

    def remove_argv(self, packages: list[str]) -> list[str]:
        return [self.binary, *self.remove, *packages]

    def upgrade_argv(self, packages: list[str], *, version: str | None = None) -> list[str]:
        if version:
            # Upgrading *to a specific version* is an install of that version — the
            # dedicated upgrade verb takes no requirement on any manager here.
            return self.install_argv(packages, version=version)
        return [self.binary, *self.upgrade, *packages]

    def refresh_argv(self) -> list[str] | None:
        return [self.binary, *self.refresh] if self.refresh is not None else None

    def query_argv(self, package: str) -> list[str]:
        return [*self.query, package]

    def query_ok(self, package: str, returncode: int, stdout: str) -> bool:
        if returncode != 0:
            return False
        if not self.query_marker:
            return True
        return self.query_marker.format(pkg=package) in stdout

    def available(self) -> bool:
        return shutil.which(self.binary) is not None

    def _pin(self, packages: list[str], version: str | None) -> list[str]:
        if not version:
            return packages
        if self.version_sep is None:
            raise RuntimeError(
                f"Version pinning is not supported by {self.id}."
            )
        if len(packages) != 1:
            raise RuntimeError(
                "Version pinning is only supported for a single package."
            )
        return [f"{packages[0]}{self.version_sep}{version}"]


APT_GET = PackageManager(
    id="apt",
    binary="apt-get",
    install=("install", "-y"),
    remove=("remove", "-y"),
    upgrade=("install", "--only-upgrade", "-y"),
    refresh=("update", "-q"),
    query=("dpkg", "-s"),
    query_marker="Status: install ok installed",
    version_sep="=",
)

# `pkg install` already does mirror selection and an apt-cache refresh of its own
# (see select_mirror/update_apt_cache in termux-tools' pkg script), so refresh is
# None here — a separate `pkg update` would just re-run the mirror probe.
TERMUX_PKG = PackageManager(
    id="termux-pkg",
    binary="pkg",
    install=("install", "-y"),
    remove=("uninstall", "-y"),
    upgrade=("install", "-y"),  # apt install on an installed package upgrades it
    refresh=None,
    query=("dpkg", "-s"),
    query_marker="Status: install ok installed",
    version_sep="=",
    privileged=False,
)

# Termux's pacman-flavoured build. `pkg` fronts both, so the wrapper is still the
# right entry point; only the installed-state query differs (no dpkg).
TERMUX_PKG_PACMAN = PackageManager(
    id="termux-pkg-pacman",
    binary="pkg",
    install=("install", "--noconfirm"),
    remove=("uninstall", "--noconfirm"),
    upgrade=("install", "--noconfirm"),
    refresh=None,
    query=("pacman", "-Q"),
    version_sep=None,
    privileged=False,
)

# Root sessions can't use `pkg` at all — it exits immediately on `id -u == 0` — so a
# root Termux talks to the backend directly, losing only mirror selection.
TERMUX_APT = PackageManager(
    id="termux-apt",
    binary="apt",
    install=("install", "-y"),
    remove=("remove", "-y"),
    upgrade=("install", "-y"),
    refresh=("update",),
    query=("dpkg", "-s"),
    query_marker="Status: install ok installed",
    version_sep="=",
    privileged=False,
)

TERMUX_PACMAN = PackageManager(
    id="termux-pacman",
    binary="pacman",
    install=("-Sy", "--needed", "--noconfirm"),
    remove=("-Rcns", "--noconfirm"),
    upgrade=("-Sy", "--needed", "--noconfirm"),
    refresh=("-Sy",),
    query=("pacman", "-Q"),
    privileged=False,
)

DNF = PackageManager(
    id="dnf",
    binary="dnf",
    install=("install", "-y"),
    remove=("remove", "-y"),
    upgrade=("upgrade", "-y"),
    refresh=("makecache",),
    query=("rpm", "-q"),
    version_sep="-",
)

YUM = PackageManager(
    id="yum",
    binary="yum",
    install=("install", "-y"),
    remove=("remove", "-y"),
    upgrade=("update", "-y"),
    refresh=("makecache",),
    query=("rpm", "-q"),
    version_sep="-",
)

# No version_sep: Arch has no supported way to request an older version from the
# repos, so a pin is refused up front rather than silently installing latest.
PACMAN = PackageManager(
    id="pacman",
    binary="pacman",
    install=("-S", "--needed", "--noconfirm"),
    remove=("-Rns", "--noconfirm"),
    upgrade=("-S", "--noconfirm"),
    refresh=("-Sy",),
    query=("pacman", "-Q"),
)

APK = PackageManager(
    id="apk",
    binary="apk",
    install=("add",),
    remove=("del",),
    upgrade=("upgrade",),
    refresh=("update",),
    query=("apk", "info", "-e"),
    query_marker="{pkg}",  # `apk info -e` prints the name when present, nothing when not
    version_sep="=",
)

ZYPPER = PackageManager(
    id="zypper",
    binary="zypper",
    install=("--non-interactive", "install"),
    remove=("--non-interactive", "remove"),
    upgrade=("--non-interactive", "update"),
    refresh=("--non-interactive", "refresh"),
    query=("rpm", "-q"),
    version_sep="-",
)

BREW = PackageManager(
    id="brew",
    binary="brew",
    install=("install",),
    remove=("uninstall",),
    upgrade=("upgrade",),
    refresh=("update",),
    query=("brew", "list", "--versions"),
    privileged=False,  # Homebrew refuses to run under sudo
)

PACKAGE_MANAGERS: dict[str, PackageManager] = {
    pm.id: pm
    for pm in (
        APT_GET, TERMUX_PKG, TERMUX_PKG_PACMAN, TERMUX_APT, TERMUX_PACMAN,
        DNF, YUM, PACMAN, APK, ZYPPER, BREW,
    )
}


# ─── platforms ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Platform:
    """The host devstuff is running on."""

    id: str
    name: str
    family: str
    package_manager: PackageManager | None = None
    prefix: Path = Path("/usr/local")
    bin_dir: Path = Path("/usr/local/bin")
    traits: frozenset[str] = frozenset()
    # Environment applied to every child process devstuff runs on this platform.
    env: dict[str, str] = field(default_factory=dict)
    # Set when detection fell back to a generic answer, so `devstuff platform` and
    # `doctor` can say so rather than claiming more confidence than they have.
    detected_from: str = ""

    def has(self, trait: str) -> bool:
        return trait in self.traits

    def missing_traits(self, required: list[str] | tuple[str, ...]) -> list[str]:
        return [t for t in required if t not in self.traits]

    @property
    def needs_escalation(self) -> bool:
        """Whether system-package commands should be prefixed with sudo here."""
        pm = self.package_manager
        return bool(pm and pm.privileged and self.has(SUDO))

    def describe(self) -> str:
        pm = self.package_manager.id if self.package_manager else "none"
        return f"{self.name} ({self.id}/{self.family}, packages: {pm})"


UNKNOWN = Platform(
    id="unknown",
    name="Unknown",
    family="unknown",
    detected_from="fallback",
)


# ─── detection ───────────────────────────────────────────────────────────────

TERMUX_DEFAULT_PREFIX = "/data/data/com.termux/files/usr"

# uv's managed interpreters come from python-build-standalone, which publishes
# linux-gnu, linux-musl, macOS and Windows builds and nothing for Bionic. Left to
# its default preference uv tries to download one, fails, and reports the failure as
# if the *package* were unavailable. Pointing it at Termux's own CPython
# (`pkg install python`) is the only thing that can work here.
TERMUX_ENV = {"UV_PYTHON_DOWNLOADS": "never"}

# os-release ID / ID_LIKE -> (family, preferred package managers in order)
_FAMILY_MANAGERS: dict[str, tuple[str, ...]] = {
    "debian": ("apt",),
    "rhel": ("dnf", "yum"),
    "fedora": ("dnf", "yum"),
    "arch": ("pacman",),
    "alpine": ("apk",),
    "suse": ("zypper",),
}

# Probed in order when os-release is missing or names a family we can't place.
_PROBE_ORDER = ("apt", "dnf", "yum", "pacman", "apk", "zypper")

_FAMILY_TRAITS: dict[str, frozenset[str]] = {
    "debian": frozenset({GLIBC, FHS, APT}),
    "rhel": frozenset({GLIBC, FHS}),
    "fedora": frozenset({GLIBC, FHS}),
    "arch": frozenset({GLIBC, FHS}),
    "suse": frozenset({GLIBC, FHS}),
    # Alpine is musl: it has the FHS but *-linux-gnu release tarballs do not run.
    "alpine": frozenset({FHS}),
    "macos": frozenset({FHS}),
    "unknown": frozenset({FHS}),
}


def detect() -> Platform:
    """Work out the current platform. Never raises; worst case returns a generic one."""
    forced = os.environ.get("DEVSTUFF_PLATFORM", "").strip()
    if forced:
        built = _build_forced(forced)
        if built is not None:
            return built

    termux = _detect_termux()
    if termux is not None:
        return termux

    if sys.platform == "darwin":
        return _detect_macos()

    return _detect_linux()


def _detect_termux() -> Platform | None:
    """Termux, or None.

    Layered on purpose. ``$PREFIX`` is the signal that actually matters (it is what
    every Termux package is built against) but it is a generic-sounding name, so it
    only counts when it points at a real ``com.termux``-shaped rootfs. Termux forks
    ship under other application IDs, hence matching on the ``/files/usr`` shape plus
    a corroborating Termux env var rather than on the literal default path.
    """
    prefix = os.environ.get("PREFIX", "")
    rootfs = os.environ.get("TERMUX__ROOTFS", "")
    version = os.environ.get("TERMUX_VERSION", "")

    looks_termux = False
    if prefix.endswith("/files/usr") and Path(prefix).is_dir():
        looks_termux = bool(version or rootfs or "com.termux" in prefix)
    if not looks_termux and (version or rootfs):
        looks_termux = True
    if not looks_termux and Path(TERMUX_DEFAULT_PREFIX).is_dir():
        looks_termux = True
    if not looks_termux:
        return None

    if prefix:
        prefix_path = Path(prefix)
    elif rootfs:
        prefix_path = Path(rootfs) / "usr"
    else:
        prefix_path = Path(TERMUX_DEFAULT_PREFIX)

    # Which backend `pkg` will dispatch to. termux-app v0.119.0+ exports
    # TERMUX_APP_PACKAGE_MANAGER; older builds set TERMUX_MAIN_PACKAGE_FORMAT
    # ("debian"/"pacman") from the login script. Same fallback chain as
    # termux-setup-package-manager, so we agree with `pkg` about which one it is.
    backend = os.environ.get("TERMUX_APP_PACKAGE_MANAGER", "").strip()
    if not backend:
        fmt = os.environ.get("TERMUX_MAIN_PACKAGE_FORMAT", "").strip()
        backend = "pacman" if fmt == "pacman" else "apt" if fmt == "debian" else ""
    if not backend:
        backend = "pacman" if (prefix_path / "bin" / "pacman").exists() else "apt"

    if _is_root():
        # `pkg` hard-exits on `id -u == 0`, so under root the wrapper is unusable and
        # we have to drive the backend directly.
        pm = TERMUX_PACMAN if backend == "pacman" else TERMUX_APT
    else:
        pm = TERMUX_PKG_PACMAN if backend == "pacman" else TERMUX_PKG

    return Platform(
        id="termux",
        name="Termux (Android)",
        family="termux",
        package_manager=pm,
        prefix=prefix_path,
        bin_dir=prefix_path / "bin",
        # No SUDO: $PREFIX belongs to the app user, so there is nothing to escalate
        # to. Termux's `sudo` package is a root-device wrapper and its presence on
        # PATH is not an escalation path — see the module docstring.
        # No GLIBC: Bionic. No FHS: there is no /usr/local. No APT: the apt here is
        # Termux's own, and Debian repo tooling (add-apt-repository, PPAs) is absent.
        traits=frozenset({ANDROID}),
        env=dict(TERMUX_ENV),
        detected_from="termux env" if (version or rootfs) else "termux prefix",
    )


def _detect_macos() -> Platform:
    return Platform(
        id="macos",
        name="macOS",
        family="macos",
        package_manager=BREW if BREW.available() else None,
        prefix=Path("/usr/local"),
        bin_dir=Path("/usr/local/bin"),
        traits=_traits_for("macos"),
        detected_from="sys.platform",
    )


def _detect_linux() -> Platform:
    os_release = read_os_release()
    distro_id = os_release.get("ID", "").strip().lower()
    like = os_release.get("ID_LIKE", "").strip().lower().split()
    pretty = os_release.get("PRETTY_NAME") or os_release.get("NAME") or "Linux"

    family = ""
    for candidate in [distro_id, *like]:
        if candidate in _FAMILY_MANAGERS:
            family = candidate
            break
        if candidate in ("ubuntu", "raspbian", "linuxmint", "pop"):
            family = "debian"
            break
        if candidate in ("centos", "rocky", "almalinux", "amzn"):
            family = "rhel"
            break

    pm = None
    source = "os-release"
    if family:
        for pm_id in _FAMILY_MANAGERS[family]:
            candidate_pm = PACKAGE_MANAGERS[pm_id]
            if candidate_pm.available():
                pm = candidate_pm
                break
    # os-release can be absent (minimal containers) or name a family whose manager
    # isn't actually installed. Probing PATH is the measured answer either way.
    if pm is None:
        for pm_id in _PROBE_ORDER:
            candidate_pm = PACKAGE_MANAGERS[pm_id]
            if candidate_pm.available():
                pm = candidate_pm
                source = "PATH probe"
                break
        if pm is not None and not family:
            family = {
                "apt": "debian", "dnf": "fedora", "yum": "rhel",
                "pacman": "arch", "apk": "alpine", "zypper": "suse",
            }[pm.id]

    return Platform(
        id=distro_id or family or "linux",
        name=pretty,
        family=family or "unknown",
        package_manager=pm,
        prefix=Path("/usr/local"),
        bin_dir=Path("/usr/local/bin"),
        traits=_traits_for(family or "unknown"),
        detected_from=source if pm else "no package manager found",
    )


def _traits_for(family: str) -> frozenset[str]:
    traits = set(_FAMILY_TRAITS.get(family, _FAMILY_TRAITS["unknown"]))
    if _can_escalate():
        traits.add(SUDO)
    if has_systemd():
        traits.add(SYSTEMD)
    return frozenset(traits)


def _build_forced(spec: str) -> Platform | None:
    """Build a platform from ``$DEVSTUFF_PLATFORM``.

    An escape hatch for previewing another host's view of the catalog
    (``DEVSTUFF_PLATFORM=termux devstuff list``) and for tests. It only overrides the
    *identity*; nothing here pretends the host can actually run those installs, which
    is why `devstuff platform` prints the override prominently.
    """
    spec = spec.strip().lower()
    if spec == "termux":
        return Platform(
            id="termux",
            name="Termux (Android)",
            family="termux",
            package_manager=TERMUX_PKG,
            prefix=Path(TERMUX_DEFAULT_PREFIX),
            bin_dir=Path(TERMUX_DEFAULT_PREFIX) / "bin",
            traits=frozenset({ANDROID}),
        env=dict(TERMUX_ENV),
            detected_from="DEVSTUFF_PLATFORM override",
        )
    family_of = {
        "debian": "debian", "ubuntu": "debian",
        "fedora": "fedora", "rhel": "rhel", "centos": "rhel",
        "arch": "arch", "alpine": "alpine", "suse": "suse", "opensuse": "suse",
        "macos": "macos", "darwin": "macos",
    }
    family = family_of.get(spec)
    if family is None:
        return None
    pm = BREW if family == "macos" else PACKAGE_MANAGERS[_FAMILY_MANAGERS[family][0]]
    return Platform(
        id=spec,
        name=spec.capitalize(),
        family=family,
        package_manager=pm,
        traits=_traits_for(family),
        detected_from="DEVSTUFF_PLATFORM override",
    )


def read_os_release(path: Path | None = None) -> dict[str, str]:
    """Parse /etc/os-release into a dict. Returns {} when it isn't there."""
    candidates = [path] if path else [Path("/etc/os-release"), Path("/usr/lib/os-release")]
    for candidate in candidates:
        if candidate is None or not candidate.exists():
            continue
        data: dict[str, str] = {}
        try:
            text = candidate.read_text(errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            data[key.strip()] = value.strip().strip('"').strip("'")
        if data:
            return data
    return {}


def has_systemd() -> bool:
    """systemd is the running init — measured, not inferred from the distro."""
    return Path("/run/systemd/system").is_dir()


def _is_root() -> bool:
    geteuid = getattr(os, "geteuid", None)
    return geteuid() == 0 if geteuid else False


def _can_escalate() -> bool:
    return _is_root() or shutil.which("sudo") is not None


# ─── the process-wide current platform ───────────────────────────────────────

_current: Platform | None = None


def current() -> Platform:
    """The detected platform, cached for the life of the process."""
    global _current
    if _current is None:
        _current = detect()
    return _current


def set_current(platform: Platform | None) -> None:
    """Override (or with None, clear) the cached platform. For tests and previews."""
    global _current
    _current = platform


def reset() -> None:
    set_current(None)


# ─── using the platform ──────────────────────────────────────────────────────


def escalate(cmd: list[str], *, platform: Platform | None = None) -> list[str]:
    """Prefix ``cmd`` with sudo iff this host both needs and has it.

    Three hosts get no prefix, for three different reasons: Termux (nothing to
    escalate to), a root shell (already there — and a root container often has no
    sudo at all, which used to turn every apt install into "sudo: not found"), and
    Homebrew (which refuses to run under sudo).
    """
    p = platform or current()
    if not p.needs_escalation or _is_root():
        return list(cmd)
    return ["sudo", *cmd]


def child_env(platform: Platform | None = None) -> dict[str, str] | None:
    """Environment for child processes, or None when the platform adds nothing.

    None rather than a copy of ``os.environ`` so that subprocess calls keep
    inheriting the parent environment untouched on every ordinary host.
    """
    p = platform or current()
    if not p.env:
        return None
    return {**os.environ, **p.env}


def package_manager(platform: Platform | None = None) -> PackageManager:
    """The host's package manager, or a RuntimeError naming what we're on."""
    p = platform or current()
    pm = p.package_manager
    if pm is None:
        raise RuntimeError(
            f"No supported system package manager found on {p.name}. "
            "Install the package by hand, or add a tool entry with an "
            "install_script that suits this host."
        )
    return pm


def script_prelude(platform: Platform | None = None) -> str:
    """Shell variables injected ahead of every install/remove script body.

    Lets a script written once adapt instead of hardcoding ``sudo apt-get``:
    ``$DEVSTUFF_SUDO`` is empty where there is nothing to escalate to (so
    ``$DEVSTUFF_SUDO cmd`` is just ``cmd``), and ``$DEVSTUFF_PKG_INSTALL`` is
    whatever this host's manager wants. Every variable is always set, because
    scripts run under ``set -u``.
    """
    p = platform or current()
    pm = p.package_manager
    sudo = "sudo" if (p.needs_escalation and not _is_root()) else ""
    install = " ".join(pm.install_argv([])) if pm else ""
    remove = " ".join(pm.remove_argv([])) if pm else ""
    if sudo and pm and pm.privileged:
        install = f"sudo {install}"
        remove = f"sudo {remove}"

    values = {
        "DEVSTUFF_PLATFORM": p.id,
        "DEVSTUFF_OS_FAMILY": p.family,
        "DEVSTUFF_SUDO": sudo,
        "DEVSTUFF_PREFIX": str(p.prefix),
        "DEVSTUFF_BIN": str(p.bin_dir),
        "DEVSTUFF_PKG": pm.id if pm else "",
        "DEVSTUFF_PKG_INSTALL": install,
        "DEVSTUFF_PKG_REMOVE": remove,
    }
    lines = ["# devstuff platform prelude — see `devstuff platform`"]
    lines += [f"{k}={shlex.quote(v)}" for k, v in values.items()]
    lines.append("export " + " ".join(values))
    return "\n".join(lines) + "\n"


def with_prelude(script: str, *, platform: Platform | None = None) -> str:
    """Insert :func:`script_prelude` into a script body, after any shebang line."""
    prelude = script_prelude(platform)
    lines = script.splitlines(keepends=True)
    if lines and lines[0].startswith("#!"):
        return lines[0] + prelude + "".join(lines[1:])
    return prelude + script


def unsupported_reason(required: list[str] | tuple[str, ...],
                       *, platform: Platform | None = None) -> str:
    """Human explanation for a tool whose required traits this host lacks."""
    p = platform or current()
    missing = p.missing_traits(required)
    if not missing:
        return ""
    parts = [TRAITS.get(t, t) for t in missing]
    return f"{p.name} does not provide: " + "; ".join(parts)


@dataclass
class PlatformReport:
    """Everything `devstuff platform` and `doctor` show. Pure data, no UI."""

    platform: Platform
    supported: list[str] = field(default_factory=list)
    unsupported: list[tuple[str, str]] = field(default_factory=list)

    @property
    def forced(self) -> bool:
        return "override" in self.platform.detected_from
