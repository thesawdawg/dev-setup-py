"""The complete state of a commitizen setup the wizard can build.

Everything the emitter, the offline preview and the live `cz` check need lives in
these tables, so adding a commit type or a version provider is a data change. See
docs/specs/commitizen-config/.

The numbers here are not guesses: the bump levels, changelog membership and shortcut
keys below are what `commitizen.defaults` and `ConventionalCommitsCz` actually ship
(read out of the installed package, not a blog post), so "Conventional Commits" and
"Custom types, starting from the conventional set" describe the same behaviour until
the user changes something (SD-4).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

# ---------------------------------------------------------------------------
# Bump levels
# ---------------------------------------------------------------------------

MAJOR = "MAJOR"
MINOR = "MINOR"
PATCH = "PATCH"
NONE = ""  # the type exists, but a release never happens because of it alone


@dataclass(frozen=True)
class BumpLevel:
    key: str
    label: str
    description: str


BUMP_LEVELS: dict[str, BumpLevel] = {
    MAJOR: BumpLevel(MAJOR, "MAJOR", "Breaking — 1.4.2 becomes 2.0.0"),
    MINOR: BumpLevel(MINOR, "MINOR", "New feature — 1.4.2 becomes 1.5.0"),
    PATCH: BumpLevel(PATCH, "PATCH", "Fix or internal change — 1.4.2 becomes 1.4.3"),
    NONE: BumpLevel(NONE, "no release", "Never bumps the version on its own"),
}

# What each level does to 1.4.2, for the offline bump table. The live check runs the
# real `cz bump --dry-run` against these same numbers so the two can be compared.
SAMPLE_VERSION = "1.4.2"
SAMPLE_BUMPS: dict[str, str] = {
    MAJOR: "2.0.0",
    MINOR: "1.5.0",
    PATCH: "1.4.3",
    NONE: SAMPLE_VERSION,
}

# ---------------------------------------------------------------------------
# Commit types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ChangeType:
    """One commit type: the prefix, what it does to the version, and where it lands
    in the changelog.

    `section` is the changelog heading (commitizen's `change_type_map` value).
    `changelog=False` means the type is a valid commit prefix but is deliberately
    absent from the release notes — which is what `docs`/`style`/`ci` are for.
    """

    key: str
    section: str
    description: str
    bump: str = NONE
    # `use_shortcuts` shows these next to each choice in `cz commit`. Must be a
    # single [a-z0-9] and unique across the selected types — commitizen does not
    # check that, it just gives two types the same hotkey.
    shortcut: str = ""
    default: bool = False
    changelog: bool = False
    builtin: bool = True


# Declaration order is the canonical order: it is the order of the `cz commit`
# picker, of `change_type_order`, and of every generated regex alternation.
TYPES: tuple[ChangeType, ...] = (
    ChangeType(
        key="feat",
        section="Features",
        description="A new feature",
        bump=MINOR,
        shortcut="f",
        default=True,
        changelog=True,
    ),
    ChangeType(
        key="fix",
        section="Bug Fixes",
        description="A bug fix",
        bump=PATCH,
        shortcut="x",
        default=True,
        changelog=True,
    ),
    ChangeType(
        key="refactor",
        section="Refactors",
        description="A code change that neither fixes a bug nor adds a feature",
        bump=PATCH,
        shortcut="r",
        default=True,
        changelog=True,
    ),
    ChangeType(
        key="perf",
        section="Performance",
        description="A code change that improves performance",
        bump=PATCH,
        shortcut="p",
        default=True,
        changelog=True,
    ),
    ChangeType(
        key="docs",
        section="Documentation",
        description="Documentation only changes",
        shortcut="d",
        default=True,
    ),
    ChangeType(
        key="style",
        section="Styling",
        description="Formatting, white-space, missing semi-colons — no behaviour change",
        shortcut="s",
        default=True,
    ),
    ChangeType(
        key="test",
        section="Tests",
        description="Adding missing tests or correcting existing ones",
        shortcut="t",
        default=True,
    ),
    ChangeType(
        key="build",
        section="Build System",
        description="Build system or dependency changes (pip, docker, npm)",
        shortcut="b",
        default=True,
    ),
    ChangeType(
        key="ci",
        section="CI",
        description="CI configuration files and scripts",
        shortcut="c",
        default=True,
    ),
    # Off by default for the same reason `cz_conventional_commits` leaves them out of
    # its picker: both are catch-alls that make it easy to avoid choosing a real type.
    ChangeType(
        key="chore",
        section="Chores",
        description="Maintenance work with no user-visible effect",
        shortcut="z",
    ),
    ChangeType(
        key="revert",
        section="Reverts",
        description="Reverts a previous commit",
        bump=PATCH,
        shortcut="v",
        changelog=True,
    ),
    ChangeType(
        key="deps",
        section="Dependencies",
        description="Dependency upgrades",
        bump=PATCH,
        shortcut="e",
        changelog=True,
    ),
    ChangeType(
        key="security",
        section="Security",
        description="A fix for a security issue",
        bump=PATCH,
        shortcut="u",
        changelog=True,
    ),
)

TYPES_BY_KEY: dict[str, ChangeType] = {t.key: t for t in TYPES}

# `cz bump` writes its own commit using `bump_message`, whose default starts with
# `bump:`. If the generated schema_pattern does not accept that prefix, `cz check`
# over a range that includes a release rejects commitizen's own commit — which is
# why `cz_conventional_commits` accepts `bump` without ever offering it. Same here.
ALWAYS_ACCEPTED = ("bump",)

BREAKING_KEYWORD = "BREAKING CHANGE"
BREAKING_SECTION = "BREAKING CHANGE"


def default_types() -> list[str]:
    return [t.key for t in TYPES if t.default]


# ---------------------------------------------------------------------------
# Conventions
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Convention:
    key: str
    name: str  # the value of `name` in the config — a commitizen plugin name
    label: str
    description: str
    customizable: bool


CONVENTIONS: dict[str, Convention] = {
    "conventional": Convention(
        key="conventional",
        name="cz_conventional_commits",
        label="Conventional Commits (built in)",
        description=(
            "commitizen's own rules: feat/fix/docs/style/refactor/perf/test/build/ci, "
            "feat bumps MINOR, fix/refactor/perf bump PATCH. Types and bump rules are "
            "fixed — nothing to configure."
        ),
        customizable=False,
    ),
    "custom": Convention(
        key="custom",
        name="cz_customize",
        label="Custom types and bump rules",
        description=(
            "Same shape as Conventional Commits, but you choose which types exist, what "
            "each one does to the version, and which changelog section it lands in."
        ),
        customizable=True,
    ),
}

# ---------------------------------------------------------------------------
# Versioning
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VersionProvider:
    key: str
    label: str
    description: str
    # Files whose presence means this provider is the right guess. Checked in
    # `detect.py`; declaration order below is the tie-break order.
    markers: tuple[str, ...] = ()
    # Whether the config has to carry `version = "x.y.z"` itself.
    needs_version: bool = False


VERSION_PROVIDERS: dict[str, VersionProvider] = {
    "commitizen": VersionProvider(
        key="commitizen",
        label="commitizen",
        description="The version lives in this config file — works for any language.",
        needs_version=True,
    ),
    "scm": VersionProvider(
        key="scm",
        label="scm (git tags)",
        description="Read from the latest git tag; nothing is written back to a file.",
    ),
    "pep621": VersionProvider(
        key="pep621",
        label="pep621 (pyproject.toml)",
        description="`[project] version` in pyproject.toml.",
        markers=("pyproject.toml",),
    ),
    "uv": VersionProvider(
        key="uv",
        label="uv (uv.lock + pyproject.toml)",
        description="Like pep621, and keeps uv.lock in step.",
        markers=("uv.lock",),
    ),
    "poetry": VersionProvider(
        key="poetry",
        label="poetry (pyproject.toml)",
        description="`[tool.poetry] version` in pyproject.toml.",
    ),
    "cargo": VersionProvider(
        key="cargo",
        label="cargo (Cargo.toml)",
        description="`[package] version` in Cargo.toml.",
        markers=("Cargo.toml",),
    ),
    "npm": VersionProvider(
        key="npm",
        label="npm (package.json)",
        description="`version` in package.json, plus the lockfiles when present.",
        markers=("package.json",),
    ),
    "composer": VersionProvider(
        key="composer",
        label="composer (composer.json)",
        description="`version` in composer.json.",
        markers=("composer.json",),
    ),
}


@dataclass(frozen=True)
class VersionScheme:
    key: str
    label: str
    description: str


VERSION_SCHEMES: dict[str, VersionScheme] = {
    "semver": VersionScheme(
        key="semver",
        label="semver",
        description="1.2.3, 1.2.3-rc1 — the usual choice outside Python.",
    ),
    "semver2": VersionScheme(
        key="semver2",
        label="semver2",
        description="Strict SemVer 2.0.0: prereleases are dotted (1.2.3-rc.1).",
    ),
    "pep440": VersionScheme(
        key="pep440",
        label="pep440",
        description="1.2.3, 1.2.3rc1 — what PyPI requires.",
    ),
}


@dataclass(frozen=True)
class TagFormat:
    key: str
    label: str
    description: str


TAG_FORMATS: dict[str, TagFormat] = {
    "v$version": TagFormat("v$version", "v1.4.2", "The common git convention."),
    "$version": TagFormat("$version", "1.4.2", "Bare version, no prefix."),
    "v$major.$minor.$patch": TagFormat(
        "v$major.$minor.$patch",
        "v1.4.2 (no prerelease part)",
        "Drops any prerelease/dev suffix from the tag.",
    ),
}

# ---------------------------------------------------------------------------
# Where the config goes
# ---------------------------------------------------------------------------

# commitizen's own search order (commitizen.defaults.CONFIG_FILES). The first file
# in this order that carries a `commitizen` section wins, which is why writing
# `.cz.toml` into a project already configured through pyproject.toml silently
# shadows it — the wizard warns rather than letting that happen quietly (FR-13).
CONFIG_FILES: tuple[str, ...] = (
    ".cz.toml",
    "cz.toml",
    ".cz.json",
    "cz.json",
    ".cz.yaml",
    "cz.yaml",
    "pyproject.toml",
)


@dataclass(frozen=True)
class Target:
    key: str  # the filename
    label: str
    description: str


TARGETS: dict[str, Target] = {
    "pyproject.toml": Target(
        key="pyproject.toml",
        label="pyproject.toml",
        description="Under [tool.commitizen], alongside the rest of the project config.",
    ),
    ".cz.toml": Target(
        key=".cz.toml",
        label=".cz.toml",
        description="A standalone file — the right choice outside Python projects.",
    ),
}

DEFAULT_CHANGELOG = "CHANGELOG.md"
DEFAULT_BUMP_MESSAGE = "bump: version $current_version → $new_version"

# ---------------------------------------------------------------------------
# The config
# ---------------------------------------------------------------------------


@dataclass
class CommitizenConfig:
    convention: str = "conventional"
    # Selected type keys, in TYPES order once `selected()` has normalised them.
    types: list[str] = field(default_factory=default_types)
    # Per-type overrides of the table above, so the tables stay the source of truth
    # and the wizard only records the difference.
    bumps: dict[str, str] = field(default_factory=dict)
    sections: dict[str, str] = field(default_factory=dict)
    in_changelog: dict[str, bool] = field(default_factory=dict)
    extra_types: list[ChangeType] = field(default_factory=list)

    # Versioning
    version_provider: str = "commitizen"
    version: str = "0.1.0"
    version_scheme: str = "semver"
    tag_format: str = "v$version"
    version_files: list[str] = field(default_factory=list)
    major_version_zero: bool = False

    # Bump behaviour
    update_changelog_on_bump: bool = True
    annotated_tag: bool = False
    gpg_sign: bool = False
    bump_message: str = ""  # "" keeps commitizen's default

    # Changelog
    changelog_file: str = DEFAULT_CHANGELOG
    changelog_incremental: bool = True
    changelog_merge_prerelease: bool = True

    # Commit prompt
    use_shortcuts: bool = True
    ask_scope: bool = True
    ask_body: bool = True
    ask_footer: bool = True
    allow_abort: bool = False

    # Where it lands
    target: str = ".cz.toml"

    # -- resolved views the emitter, previews and live check all share -------

    @property
    def convention_spec(self) -> Convention:
        return CONVENTIONS[self.convention]

    @property
    def customizable(self) -> bool:
        return self.convention_spec.customizable

    @property
    def provider_spec(self) -> VersionProvider:
        return VERSION_PROVIDERS[self.version_provider]

    def catalog(self) -> dict[str, ChangeType]:
        """Every type this config knows about — the built-in table plus any the user
        added, with their overrides already applied.

        Under `cz_conventional_commits` the overrides are ignored: commitizen's own
        rules are what will run, so reporting anything else would make the preview
        lie about a config it cannot influence.
        """
        if not self.customizable:
            return dict(TYPES_BY_KEY)
        merged = dict(TYPES_BY_KEY)
        for extra in self.extra_types:
            merged[extra.key] = extra
        return {
            key: replace(
                change_type,
                bump=self.bumps.get(key, change_type.bump),
                section=self.sections.get(key, change_type.section),
                changelog=self.in_changelog.get(key, change_type.changelog),
            )
            for key, change_type in merged.items()
        }

    def selected(self) -> list[ChangeType]:
        """Selected types in canonical order: the built-in table's order first, then
        user-added types in the order they were added. Unknown keys are dropped.

        Conventional Commits has a fixed set, so the user's selection does not apply.
        """
        if not self.customizable:
            return [t for t in TYPES if t.default]
        catalog = self.catalog()
        chosen = set(self.types)
        ordered = [t.key for t in TYPES] + [t.key for t in self.extra_types]
        seen: set[str] = set()
        out: list[ChangeType] = []
        for key in ordered:
            if key in chosen and key not in seen and key in catalog:
                seen.add(key)
                out.append(catalog[key])
        return out

    def changelog_types(self) -> list[ChangeType]:
        return [t for t in self.selected() if t.changelog]

    def bump_of(self, key: str) -> str:
        change_type = self.catalog().get(key)
        return change_type.bump if change_type else NONE

    def duplicate_shortcuts(self) -> dict[str, list[str]]:
        """Shortcut key → the types fighting over it. commitizen does not validate
        this; two types sharing a hotkey just means one of them is unreachable."""
        seen: dict[str, list[str]] = {}
        for change_type in self.selected():
            if change_type.shortcut:
                seen.setdefault(change_type.shortcut, []).append(change_type.key)
        return {key: keys for key, keys in seen.items() if len(keys) > 1}
