"""Check a candidate `ansible.cfg` by running the real ansible against it.

There are two levels, and the second is the interesting one.

`ansible-config validate` is a genuine validator — measured, it rejects an unknown
key, an unknown section, and a value of the wrong type. That is better coverage than
`dockerd --validate` manages. But it answers "is this file well-formed", and the
question that actually matters is **"did ansible read what I wrote"**, which is not
the same thing:

    [ssh_connection]          validate: unknown section
    pipelining = True         dump --only-changed: nothing at all

    [defaults]                validate: fine
    pipelining = True         dump --only-changed: ANSIBLE_PIPELINING = True

`ansible-config dump --only-changed` lists every setting ansible took from the file,
with its source. Comparing that against what the wizard meant to write is a stronger
check than validation, and it is the only one that would catch a setting quietly
landing in a section this version does not read. `reads_back()` is that check.

Every failure path returns a failed `Check`, never an exception.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from dev_setup.configure.ansible import render
from dev_setup.configure.ansible.model import (
    CONFIG_FILE,
    RETIRED_SECTIONS,
    SECTIONS,
    AnsibleConfig,
)

TIMEOUT = 60

# "DEFAULT_FORKS(/path/to/ansible.cfg) = 20"
#
# Core settings dump as SHOUTING_NAMES; plugin options dump under their own lowercase
# key ("result_format(/path) = yaml"). Both spellings have to match or every plugin
# option looks unread.
_DUMP_RE = re.compile(r"^(?P<name>[A-Za-z0-9_]+)\((?P<source>[^)]*)\)\s*=\s*(?P<value>.*)$")


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class Report:
    version: str
    checks: list[Check]

    @property
    def ok(self) -> bool:
        return all(check.ok for check in self.checks)

    @property
    def failures(self) -> list[Check]:
        return [check for check in self.checks if not check.ok]


def available() -> bool:
    return shutil.which("ansible-config") is not None


def _run(cmd: list[str], cwd: Path, config: Path) -> subprocess.CompletedProcess[str] | None:
    env = {
        **os.environ,
        # The candidate, and nothing the user's shell may be pointing at.
        "ANSIBLE_CONFIG": str(config),
        "NO_COLOR": "1",
        "ANSIBLE_NOCOLOR": "1",
        "ANSIBLE_DEPRECATION_WARNINGS": "False",
    }
    try:
        return subprocess.run(
            cmd,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def version() -> str:
    try:
        result = subprocess.run(
            ["ansible", "--version"], capture_output=True, text=True, timeout=TIMEOUT, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    first = result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""
    return first.split("core", 1)[-1].strip(" ]") if "core" in first else "unknown"


@contextmanager
def sandbox(cfg: AnsibleConfig):
    """The candidate written to a throwaway directory. Yields (dir, config path).

    A directory of its own matters here: ansible resolves relative paths in the
    config against the directory it runs in, and `tempfile` creates 0700
    directories, which keeps the world-writable rule from firing on the sandbox
    itself.
    """
    try:
        with tempfile.TemporaryDirectory(prefix="devstuff-ansible-") as tmp:
            root = Path(tmp)
            path = root / CONFIG_FILE
            path.write_text(render.to_text(cfg), encoding="utf-8")
            yield root, path
    except OSError:  # pragma: no cover
        yield None, None


def dump(cfg: AnsibleConfig, dump_type: str = "") -> dict[str, tuple[str, str]] | None:
    """What ansible actually read from the candidate: {name: (value, source)}.

    Only entries sourced from the candidate file are returned — ansible also reports
    settings changed by environment variables, and those are not this file's doing.

    `dump_type` selects `ansible-config dump -t <type>`. A callback-plugin option
    such as `callback_result_format` is invisible to the plain dump and shows up
    under `-t callback`, so asking the wrong one would look like the setting was
    ignored.
    """
    if not available():
        return None
    command = ["ansible-config", "dump", "--only-changed"]
    if dump_type:
        command += ["-t", dump_type]
    with sandbox(cfg) as (root, path):
        if root is None or path is None:  # pragma: no cover
            return None
        result = _run(command, root, path)
        if result is None or result.returncode != 0:
            return None
        out: dict[str, tuple[str, str]] = {}
        for line in result.stdout.splitlines():
            match = _DUMP_RE.match(line.strip())
            if match and match.group("source") == str(path):
                out[match.group("name")] = (match.group("value").strip(), match.group("source"))
        return out


def verify(cfg: AnsibleConfig) -> Report:
    """Everything checkable offline. Always returns a report."""
    checks: list[Check] = []

    text = render.to_text(cfg)
    round_trip = render.matches(text, cfg)
    checks.append(Check(
        "generated config matches the model",
        round_trip,
        "parsed back identically" if round_trip else "the emitted file says something else",
    ))

    checks += _section_checks(cfg)

    if not available():
        checks.append(Check(
            "ansible accepts the file", True, "ansible is not installed here — skipped, not failed"
        ))
        return Report("unknown", checks)

    checks.append(_validate_check(cfg))
    checks.append(_reads_back_check(cfg))
    return Report(version(), checks)


def _section_checks(cfg: AnsibleConfig) -> list[Check]:
    """Sections this ansible no longer reads.

    Only reachable through a carried-over section, since the wizard writes only
    sections from `SECTIONS`. A file inherited from an older project is exactly where
    one turns up.
    """
    checks: list[Check] = []
    for section in cfg.extra:
        if section in RETIRED_SECTIONS:
            checks.append(Check(
                f"[{section}] is read",
                False,
                RETIRED_SECTIONS[section],
            ))
        elif section not in SECTIONS:
            checks.append(Check(
                f"[{section}] is read",
                False,
                "not a section ansible-core knows — its settings will be ignored",
            ))
    return checks


def _validate_check(cfg: AnsibleConfig) -> Check:
    with sandbox(cfg) as (root, path):
        if root is None or path is None:  # pragma: no cover
            return Check("ansible accepts the file", False, "could not write a temporary file")
        result = _run(["ansible-config", "validate"], root, path)
    if result is None:
        return Check("ansible accepts the file", False, "ansible-config did not run")
    output = f"{result.stdout}\n{result.stderr}".strip()
    problems = [line for line in output.splitlines() if "ERROR" in line]

    # `ansible-config validate` only knows *core* settings, so it reports a plugin
    # option as an unknown key even though ansible reads and honours it. Measured:
    # `callback_result_format = yaml` is rejected here and visibly changes a real
    # run's output. Dropping those complaints is not papering over a problem — the
    # setting is separately proved to work by `_reads_back_check`, which asks the
    # dump that can actually see it.
    plugin_keys = {setting.ini_key for setting in cfg.plugin_options()}
    problems = [line for line in problems if not any(f"'{key}'" in line for key in plugin_keys)]

    if not problems:
        detail = "no unknown sections, keys or values"
        if plugin_keys:
            detail += f" (ignoring the validator's false positive on {', '.join(sorted(plugin_keys))})"
        return Check("ansible accepts the file", True, detail)
    return Check("ansible accepts the file", False, problems[0])


def _reads_back_check(cfg: AnsibleConfig) -> Check:
    """The check that matters: did ansible read every setting we wrote?

    A setting written into a section this version does not read parses cleanly, is
    reported nowhere, and does nothing. Comparing what was meant against
    `dump --only-changed` is the only thing that notices.
    """
    total = 0
    missing: list[str] = []
    for dump_type in cfg.dump_types():
        expected = cfg.expected_env(dump_type)
        if not expected:
            continue
        total += len(expected)
        got = dump(cfg, dump_type)
        if got is None:
            return Check("ansible reads every setting", False, "ansible-config dump did not run")
        missing += [
            f"{expected[name].section}.{expected[name].ini_key}"
            for name in expected
            if name not in got
        ]
    if not total:
        return Check("ansible reads every setting", True, "nothing is set")
    if not missing:
        return Check(
            "ansible reads every setting",
            True,
            f"all {total} settings show up in `ansible-config dump`",
        )
    return Check(
        "ansible reads every setting", False, f"written but not read back: {', '.join(missing)}"
    )


def init_reference(path: Path) -> tuple[bool, str]:
    """Write ansible's own fully-commented reference config next to the result.

    `ansible-config init --disabled` emits every option this ansible has, commented
    out — a far better reference than anything this wizard could write, and it is
    generated by the version actually installed.
    """
    if not available():
        return False, "ansible-config is not installed"
    try:
        result = subprocess.run(
            ["ansible-config", "init", "--disabled"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
            check=False,
            env={**os.environ, "NO_COLOR": "1"},
        )
    except (OSError, subprocess.SubprocessError):
        return False, "ansible-config did not run"
    if result.returncode != 0 or not result.stdout.strip():
        return False, (result.stderr.strip() or "ansible-config init produced nothing")
    try:
        path.write_text(result.stdout, encoding="utf-8")
    except OSError as exc:
        return False, str(exc)
    return True, str(path)


__all__ = [
    "Check",
    "Report",
    "available",
    "dump",
    "init_reference",
    "sandbox",
    "verify",
    "version",
]
