"""Profile files: which catalog tools a machine should have (docs/specs/profile).

A profile is a small YAML file shaped like the catalog (`version`, `tools`) but with a
different meaning: the catalog says what a tool *is*, a profile says what a *machine has*.
It names catalog keys and, optionally, a version — never a definition or a script, so a
profile from somewhere else cannot carry code past the catalog's validation (SD-2).

Everything here is pure: no UI, no registry lookup, no subprocess. Whether a key exists in
*this* machine's catalog is deliberately not checked at load time (FR-6) — a profile written
on a machine with custom tools has to load on one without them — so that is `diff`'s job.

Loading is strict because a profile says what should be installed, and a value YAML quietly
changed on the way in is worse than an error. `version: 1.10` reaches Python as the float
`1.1` — the trailing zero is gone from the data, not just from the printing — so there is
nothing to recover and the only safe move is to refuse (SD-6).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml

VERSION = 1

_TOP_FIELDS = ("version", "tools")
_ENTRY_FIELDS = ("version",)

_HEADER = (
    "# devstuff profile: the tools this machine should have.\n"
    "# Compare a machine against this file with: devstuff profile diff <this file>\n"
)


class ProfileError(RuntimeError):
    pass


def _check_version(value: Any, where: str) -> str:
    """Return `value` if it is a usable version string, else raise ProfileError.

    One rule for both a file and a hand-built `Entry`, so a version `snapshot` constructs
    can always be read back by `load`.
    """
    if value is None:
        raise ProfileError(
            f"{where} has no value (a bare `version:` or `~` is null). "
            "Remove the line, or write a quoted string, e.g. version: '1.2.3'"
        )
    if type(value) is not str:
        raise ProfileError(
            f"{where} was read by YAML as {type(value).__name__} ({value!r}), not as text. "
            "A version has to be a quoted string, e.g. version: '1.10' — unquoted, YAML "
            "turns 1.10 into 1.1, 0.40 into 0.4, and 2026-09-30 into a date"
        )
    if not value or value != value.strip():
        raise ProfileError(f"{where} is blank or has leading/trailing whitespace: {value!r}")
    return value


@dataclass(frozen=True)
class Entry:
    """What a profile says about one tool. `version` None means "present, any version"."""

    version: str | None = None

    def __post_init__(self) -> None:
        if self.version is not None:
            _check_version(self.version, "Entry version")


@dataclass(frozen=True)
class Profile:
    tools: dict[str, Entry] = field(default_factory=dict)


# -- Loading ------------------------------------------------------------------------------------


class _DuplicateKey(yaml.YAMLError):
    def __init__(self, key: object, line: int) -> None:
        super().__init__(f"line {line}: duplicate key {key!r}")


class _StrictLoader(yaml.SafeLoader):
    """SafeLoader that refuses a mapping key that appears twice.

    PyYAML keeps the last one and drops the first without a word, which in a file that says
    what should be installed is a quiet way to lose an entry (spec F-5).
    """

    def construct_mapping(self, node, deep=False):
        if isinstance(node, yaml.MappingNode):
            seen: set[Any] = set()
            for key_node, _value_node in node.value:
                if key_node.tag == "tag:yaml.org,2002:merge":
                    continue
                try:
                    key = self.construct_object(key_node, deep=True)
                    if key in seen:
                        raise _DuplicateKey(key, key_node.start_mark.line + 1)
                    seen.add(key)
                except TypeError:
                    continue  # unhashable key: let the base class report it its own way
        return super().construct_mapping(node, deep=deep)


def loads(text: str, *, source: Path | str = "<profile>") -> Profile:
    try:
        raw = yaml.load(text, Loader=_StrictLoader)  # a SafeLoader subclass
    except _DuplicateKey as exc:
        raise ProfileError(f"{source}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ProfileError(f"{source}: invalid YAML: {exc}") from exc
    return _validate(raw, source)


def load(path: Path) -> Profile:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ProfileError(f"{path}: no such file") from exc
    except (OSError, UnicodeDecodeError) as exc:
        raise ProfileError(f"{path}: cannot read as a text file: {exc}") from exc
    return loads(text, source=path)


def _validate(raw: Any, source: Path | str) -> Profile:
    if raw is None:
        raise ProfileError(f"{source}: profile is empty")
    if not isinstance(raw, dict):
        raise ProfileError(f"{source}: profile must be a mapping with `version` and `tools`")

    unknown = [k for k in raw if k not in _TOP_FIELDS]
    if unknown:
        raise ProfileError(f"{source}: unknown field {unknown[0]!r} (expected: version, tools)")

    # `type(...) is int`, not `== VERSION`: in Python `True == 1`, so `version: true` would pass.
    if "version" not in raw:
        raise ProfileError(f"{source}: missing required field `version` (must be {VERSION})")
    if type(raw["version"]) is not int or raw["version"] != VERSION:
        raise ProfileError(f"{source}: version must be {VERSION}, got {raw['version']!r}")

    if "tools" not in raw:
        raise ProfileError(f"{source}: missing required field `tools`")
    tools = raw["tools"]
    if not isinstance(tools, dict):
        raise ProfileError(
            f"{source}: tools must be a mapping of tool key to entry "
            "(write `tools: {}` for an empty profile)"
        )

    return Profile({key: _entry(key, value, source) for key, value in tools.items()})


def _entry(key: object, value: Any, source: Path | str) -> Entry:
    if not isinstance(key, str):
        raise ProfileError(
            f"{source}: tools: key {key!r} was read by YAML as {type(key).__name__}, "
            "not a string — quote it"
        )
    if value is None:  # a bare `uv:`
        return Entry()
    if not isinstance(value, dict):
        raise ProfileError(
            f"{source}: tools.{key}: entry must be a mapping, got {type(value).__name__} "
            f"(write `{key}: {{}}` for any version)"
        )
    for name in value:
        if name not in _ENTRY_FIELDS:
            raise ProfileError(
                f"{source}: tools.{key}: unknown field {name!r} (only `version` is supported)"
            )
    if "version" not in value:
        return Entry()
    return Entry(_check_version(value["version"], f"{source}: tools.{key}.version"))


# -- Dumping ------------------------------------------------------------------------------------


def dumps(profile: Profile) -> str:
    """Serialise deterministically: sorted by key, a fixed header, nothing machine-specific.

    Two dumps of the same profile are byte-identical, so a profile kept in git shows only
    real changes, and one handed to someone else leaks neither hostname nor time (SD-7).
    `yaml.safe_dump` is what makes version-shaped strings safe on the way out: it quotes
    `'1.10'` precisely because the bare form would reload as a number.
    """
    tools = {
        key: ({} if entry.version is None else {"version": entry.version})
        for key, entry in sorted(profile.tools.items(), key=lambda kv: kv[0])
    }
    body = yaml.safe_dump(
        {"version": VERSION, "tools": tools},
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
    )
    return _HEADER + body


# -- Comparing a machine to a profile ---------------------------------------------------------------
# Pure, like the rest of this module: `compare` takes *facts* about the machine (`MachineTool`)
# rather than tool objects, so every rule below is testable without a registry or a subprocess.
# Gathering the facts is the caller's job.


class DiffState(StrEnum):
    """What can honestly be said about one key. Values are the `--json` spellings (FR-16)."""

    OK = "ok"
    MISSING = "missing"
    DRIFT = "drift"
    # The three ways the comparison could not be made. None of them is ever `ok` (FR-17), and
    # each has a different remedy, which is why they are three states and not one "unknown".
    UNVERIFIABLE = "unverifiable"  # pinned and installed, but the version cannot be read
    UNPINNABLE = "unpinnable"      # pinned, but this install type cannot honour a pin
    UNKNOWN_KEY = "unknown-key"    # this machine's catalog has no such key
    EXTRA = "extra"


@dataclass(frozen=True)
class MachineTool:
    """What this machine says about one catalog key. Present in the mapping = the catalog knows it."""

    install_type: str
    installed: bool
    pinnable: bool
    # The installed version, if it was read ("" otherwise). Only read for pinned, pinnable tools.
    version: str = ""
    # Why `version` is empty, when it is — shown on an `unverifiable` row.
    why_unreadable: str = ""


@dataclass(frozen=True)
class DiffRow:
    key: str
    state: DiffState
    type: str | None            # None for `unknown-key`: the catalog doesn't say
    pinned: str | None          # what the profile asked for
    installed: str | None       # the installed version, where it was read
    note: str = ""

    def to_json(self) -> dict[str, str | None]:
        return {
            "key": self.key,
            "state": self.state.value,
            "type": self.type,
            "pinned": self.pinned,
            "installed": self.installed,
            "note": self.note,
        }


def _row(
    key: str, state: DiffState, tool: MachineTool | None, entry: Entry | None, note: str = ""
) -> DiffRow:
    return DiffRow(
        key,
        state,
        tool.install_type if tool else None,
        entry.version if entry else None,
        (tool.version or None) if tool else None,
        note,
    )


def _compare_one(key: str, entry: Entry, tool: MachineTool | None) -> DiffRow:
    if tool is None:
        return _row(
            key, DiffState.UNKNOWN_KEY, None, entry,
            "not in this machine's catalog — if it is a custom tool, bring its definition over "
            "with `devstuff catalog import`",
        )
    if not tool.installed:
        # Dominates a pin that couldn't be honoured: there is nothing to compare yet.
        return _row(key, DiffState.MISSING, tool, entry)
    if entry.version is None:
        return _row(key, DiffState.OK, tool, entry)
    if not tool.pinnable:
        # Decided before the version is looked at: a tool that cannot be pinned must not read
        # as `ok` just because some version text happens to equal the pin.
        return _row(
            key, DiffState.UNPINNABLE, tool, entry,
            f"a {tool.install_type} install can't be pinned, so the pin is ignored",
        )
    if not tool.version:
        return _row(
            key, DiffState.UNVERIFIABLE, tool, entry,
            tool.why_unreadable or "couldn't read the installed version",
        )
    # String equality, no ordering and no normalisation (FR-18): `0.45` is not `0.45.0`.
    state = DiffState.OK if tool.version == entry.version else DiffState.DRIFT
    return _row(key, state, tool, entry)


def compare(profile: Profile, machine: Mapping[str, MachineTool]) -> list[DiffRow]:
    """One row per profile key, plus one per installed catalog tool the profile doesn't name.

    `machine` holds the keys this machine's catalog knows; a profile key absent from it is
    `unknown-key`. A tool that is neither in the profile nor installed is not a row at all.
    """
    rows = [_compare_one(key, entry, machine.get(key)) for key, entry in profile.tools.items()]
    rows += [
        _row(key, DiffState.EXTRA, tool, None)
        for key, tool in machine.items()
        if key not in profile.tools and tool.installed
    ]
    return rows


# Display order (FR-19): what needs attention first, `ok` last. Public so the summary line
# the command prints uses the same order as the rows, not a second copy of it.
DIFF_ORDER: tuple[DiffState, ...] = (
    DiffState.MISSING,
    DiffState.DRIFT,
    DiffState.UNVERIFIABLE,
    DiffState.UNPINNABLE,
    DiffState.UNKNOWN_KEY,
    DiffState.EXTRA,
    DiffState.OK,
)
_DIFF_RANK = {state: i for i, state in enumerate(DIFF_ORDER)}


def sort_diff_rows(rows: Iterable[DiffRow]) -> list[DiffRow]:
    return sorted(rows, key=lambda r: (_DIFF_RANK[r.state], r.key))


def summarize_diff(rows: Iterable[DiffRow]) -> dict[DiffState, int]:
    """Count per state. Every state is present, so the counts always sum to the rows (FR-17)."""
    counts = dict.fromkeys(DiffState, 0)
    for r in rows:
        counts[r.state] += 1
    return counts


def differs(rows: Iterable[DiffRow], *, ignore_extras: bool) -> bool:
    """Whether `--exit-code` should report a difference (FR-22, OQ-2).

    Anything other than `ok` is one. `extra` counts too unless the caller opted out — "is this
    machine exactly what the profile says" is what a gate asks — but opting out of extras never
    hides a real difference.
    """
    return any(
        r.state is not DiffState.OK and not (ignore_extras and r.state is DiffState.EXTRA)
        for r in rows
    )
