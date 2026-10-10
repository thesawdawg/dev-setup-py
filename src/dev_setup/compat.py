"""Inspect a tool's *install source* for things this host cannot do.

`requires_traits` and `platforms:` in the catalog are **declarations** — they work
only for entries whose author thought about portability. Nothing added through
`devstuff add`, imported with `catalog import`, or hand-written into
`~/.config/devstuff/tools.yaml` carries any. On Termux such an entry reports itself
installable, runs `sudo apt-get install …` under `set -euo pipefail`, and dies on the
first line, having possibly already done half its work.

This module closes that gap by reading the source itself: which commands the script
invokes, which absolute paths it writes to, which release assets it downloads. It is
the *undeclared* case only — see `should_scan()`.

## Measured, not guessed

The strongest rule here is not a heuristic at all: if the source invokes `apt-get`
and `apt-get` is not on `$PATH`, the install *will* fail. That is checked with
`shutil.which`, the same way `platforms.py` decides everything else. The trait-based
rules (`/usr/local/bin` needs `fhs`, `*-linux-gnu` needs `glibc`) are inference, and
they are the reason `--force` exists.

## Two severities, because a false positive is expensive

A `blocking` finding refuses the install. A non-blocking one is reported and gets out
of the way — `systemctl` is the motivating case: `systemctl enable x 2>/dev/null ||
true` is a perfectly ordinary line in an installer that works fine without systemd,
so treating its presence as fatal would block working installs.

## Known blind spots

This is a text scan, and it is biased towards missing things rather than inventing
them, because a false block stops a working install while a false pass just returns
the behaviour to what it was before this module existed.

* A path glued to a flag (`curl -o/usr/local/bin/x`) is not matched. The lookbehind
  that skips the `w` in `$PREFIX/etc` cannot tell it from the `o` in `-o`, and
  protecting `$PREFIX/…` matters more: on Termux those are the *correct* paths, so
  flagging them would be exactly backwards.
* Commands built at run time (`$PKG install`, `eval`) are invisible.
* A `script`-type entry's body does not exist until install time; `generic.py`
  re-scans the downloaded text before running it, which is the real check for those.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass

from dev_setup import platforms

# ─── findings ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Finding:
    """One reason an install source looks incompatible with this host."""

    signal: str          # what was spotted: "apt-get", "/usr/local", "linux-gnu", …
    reason: str          # the sentence shown to the user
    blocking: bool = True

    def __str__(self) -> str:
        return self.reason


# ─── what the scanner looks for ──────────────────────────────────────────────

# Commands whose *absence from PATH* is a hard failure for a script that calls them.
# Checked with shutil.which, so this is measurement rather than inference: `set -e`
# plus "command not found" is the whole story.
_REQUIRED_COMMANDS = {
    "apt-get": "installs Debian packages",
    "apt": "installs Debian packages",
    "dpkg": "queries the Debian package database",
    "add-apt-repository": "adds an apt PPA",
    "dnf": "installs Fedora/RHEL packages",
    "yum": "installs RHEL packages",
    "pacman": "installs Arch packages",
    "apk": "installs Alpine packages",
    "zypper": "installs openSUSE packages",
    "brew": "installs Homebrew packages",
    "pkg": "installs Termux packages",
    "sudo": "escalates privileges",
}

# Commands that often appear guarded (`… 2>/dev/null || true`) and therefore must not
# block, but are still worth saying out loud.
_ADVISORY_COMMANDS = {
    "systemctl": (
        platforms.SYSTEMD,
        "manages a systemd service, and systemd is not running here",
    ),
    "service": (
        platforms.SYSTEMD,
        "manages a sysv/systemd service, which this host has no init for",
    ),
    "snap": (platforms.FHS, "installs a snap, which is not available here"),
}

# Absolute paths that only exist under a conventional filesystem hierarchy. The
# lookbehind keeps "$PREFIX/etc/apt" and "${PREFIX}/usr/local" from matching — on
# Termux those are the *correct* paths, and flagging them would be exactly backwards.
_FHS_PATHS = re.compile(r"(?<![\w$}/])(/usr/local/|/usr/share/|/usr/lib/|/etc/|/opt/|/var/lib/)")

# Release-asset naming that means "built against glibc".
_GLIBC_ASSETS = re.compile(r"linux-gnu|linux-glibc|_glibc")

# Commands in the source, roughly. Anchored to command position — start of a line or
# after a shell operator — so that prose in a comment or a word inside a string does
# not read as an invocation.
#
# Shell keywords are *skipped over* rather than treated as separators. `then` looks
# like a separator but isn't: in `if x; then sudo apt-get …` the `;` already puts us
# in command position, and if `then` were only a separator the cmd group would
# capture it and the real command would never be seen. (It was, in the first draft.)
_COMMAND = re.compile(
    r"""
    (?:^|[;&|(]|\|\||&&)                            # command position
    \s*
    (?:(?:then|do|else|elif|fi|done|time|exec|!)\s+)*   # skippable keywords
    (?:[A-Za-z_][A-Za-z0-9_]*=\S*\s+)*              # VAR=value prefixes
    (?P<sudo>sudo\s+)?                              # optional escalation
    (?P<cmd>[A-Za-z][A-Za-z0-9_.\-]*)
    """,
    re.MULTILINE | re.VERBOSE,
)

_COMMENT_LINE = re.compile(r"^\s*#.*$", re.MULTILINE)


def commands_in(script: str) -> set[str]:
    """Every command the script appears to invoke, in command position.

    Whole-line comments are stripped first — an installer that *documents* what it
    would do on Debian must not be read as doing it.
    """
    body = _COMMENT_LINE.sub("", script)
    found: set[str] = set()
    for match in _COMMAND.finditer(body):
        if match.group("sudo"):
            found.add("sudo")
        found.add(match.group("cmd"))
    return found


# ─── the scan ────────────────────────────────────────────────────────────────


def scan_script(script: str, *, platform: platforms.Platform | None = None) -> list[Finding]:
    """Findings for one script body."""
    p = platform or platforms.current()
    findings: list[Finding] = []
    invoked = commands_in(script)

    if "sudo" in invoked and not p.has(platforms.SUDO):
        # The trait, not shutil.which, is the authority for sudo — Termux ships a
        # `sudo` package that is a root-device wrapper, so finding it on PATH proves
        # nothing (see platforms.py). The trait already encodes that.
        findings.append(Finding(
            "sudo",
            f"the install source runs `sudo`, and {p.name} has no privilege "
            f"escalation (its prefix, {p.prefix}, needs none)",
        ))

    for cmd in sorted(invoked & set(_REQUIRED_COMMANDS) - {"sudo"}):
        if shutil.which(cmd) is not None:
            continue
        # Naming the host's own manager turns "this won't work" into something the
        # user can act on.
        hint = ""
        if p.package_manager and cmd != p.package_manager.binary:
            hint = f" — this host uses `{p.package_manager.binary}`"
        findings.append(Finding(
            cmd,
            f"the install source runs `{cmd}` ({_REQUIRED_COMMANDS[cmd]}), "
            f"which is not available on {p.name}{hint}",
        ))

    for cmd in sorted(invoked & set(_ADVISORY_COMMANDS)):
        trait, description = _ADVISORY_COMMANDS[cmd]
        if p.has(trait):
            continue
        findings.append(Finding(
            cmd,
            f"the install source runs `{cmd}`, which {description}",
            blocking=False,
        ))

    if not p.has(platforms.FHS):
        match = _FHS_PATHS.search(_COMMENT_LINE.sub("", script))
        if match:
            findings.append(Finding(
                match.group(1),
                f"the install source writes to {match.group(1)}, which does not exist "
                f"on {p.name} (its prefix is {p.prefix})",
            ))

    if not p.has(platforms.GLIBC):
        match = _GLIBC_ASSETS.search(script)
        if match:
            findings.append(Finding(
                match.group(0),
                f"the install source downloads a {match.group(0)} build, which will "
                f"not run on {p.name}",
            ))

    return findings


def scan(tool, *, platform: platforms.Platform | None = None) -> list[Finding]:
    """Findings for everything a tool would run or need in order to install.

    Removal sources are deliberately not scanned: if a tool did get installed, being
    refused permission to remove it is the worst possible outcome.
    """
    p = platform or platforms.current()
    findings: list[Finding] = []

    if tool.install_type in ("system", "apt"):
        if p.package_manager is None:
            findings.append(Finding(
                "no-package-manager",
                f"this package installs through the system package manager, and no "
                f"supported one was found on {p.name}",
            ))
        elif not p.package_manager.available():
            findings.append(Finding(
                p.package_manager.binary,
                f"this package installs through `{p.package_manager.binary}`, which "
                f"is not on PATH",
            ))
        return findings

    for source in (tool.install_script, tool.git_install_cmd):
        if source:
            findings += scan_script(source, platform=p)

    # A `script`-type entry's body only exists after the download, so the URL is all
    # there is to go on here; generic.py re-scans the real body before running it.
    if tool.script_url and _GLIBC_ASSETS.search(tool.script_url) and not p.has(platforms.GLIBC):
        findings.append(Finding(
            "linux-gnu",
            f"the install URL points at a glibc build, which will not run on {p.name}",
        ))

    return _dedupe(findings)


def _dedupe(findings: list[Finding]) -> list[Finding]:
    seen: set[str] = set()
    out: list[Finding] = []
    for finding in findings:
        if finding.signal in seen:
            continue
        seen.add(finding.signal)
        out.append(finding)
    return out


def blocking(findings: list[Finding]) -> list[Finding]:
    return [f for f in findings if f.blocking]


def summarise(findings: list[Finding]) -> str:
    """One sentence naming every blocking reason, for `unsupported_reason`."""
    reasons = [f.reason for f in blocking(findings)]
    if not reasons:
        return ""
    if len(reasons) == 1:
        return reasons[0]
    return "; ".join(reasons)


def should_scan(resolved) -> bool:
    """Whether an entry's source is worth inspecting.

    No, when the catalog already said something about this host — an explicit
    `requires_traits` (even `[]`) or a `platforms:` block matching this platform means
    the author has considered portability, and second-guessing them with a regex would
    turn a deliberate decision into a false alarm. Yes for everything else, which is
    every user-authored entry.
    """
    return not resolved.declared


# ─── force ───────────────────────────────────────────────────────────────────
#
# Process-wide, like `verbose`, rather than threaded through every signature. Set by
# `devstuff install --force`.

_force = False


def set_force(value: bool) -> None:
    global _force
    _force = value


def forced() -> bool:
    return _force
