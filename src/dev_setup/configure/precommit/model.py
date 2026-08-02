"""The complete state of a pre-commit setup the wizard can build.

`REPOS` and `HOOKS` are the catalog; `PRESETS` are named starting points cut from it.
The emitter, the offline preview and the live `pre-commit` check all read these same
tables, so adding a hook is one `Hook` record and it reaches the picker, the presets
it names, the detector and the generated YAML with no other edit.

**Everything here was read out of the real repositories, not from memory.** Every
`rev` below is what `pre-commit autoupdate` resolves to, and every `id` was checked
against that repo's own `.pre-commit-hooks.yaml` at that exact rev — `validate-config`
does *not* verify hook ids, so a typo would only surface the first time a user ran
the hooks. See docs/specs/precommit-config/.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Stages
# ---------------------------------------------------------------------------

# pre-commit >= 3.2 spells stages the same as the git hook they install into, so a
# hook's stage *is* the `install_hook_types` entry it needs. The old `commit`/`push`
# spellings are deprecated and deliberately not offered.
DEFAULT_STAGE = "pre-commit"
MANUAL_STAGE = "manual"


@dataclass(frozen=True)
class Stage:
    key: str
    label: str
    description: str


STAGES: dict[str, Stage] = {
    "pre-commit": Stage(
        "pre-commit", "pre-commit", "Runs on the staged files, before the commit is made."
    ),
    "commit-msg": Stage(
        "commit-msg", "commit-msg", "Runs on the commit message itself."
    ),
    "pre-push": Stage(
        "pre-push", "pre-push", "Runs before a push — the place for slow, whole-repo checks."
    ),
    "pre-merge-commit": Stage(
        "pre-merge-commit", "pre-merge-commit", "Runs when a merge creates a commit."
    ),
    MANUAL_STAGE: Stage(
        MANUAL_STAGE, "manual", "Never runs automatically; only via `pre-commit run --hook-stage manual`."
    ),
}

# ---------------------------------------------------------------------------
# Repositories
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Repo:
    key: str
    url: str
    rev: str
    label: str


# Declaration order is the order repos appear in the generated file.
REPOS: dict[str, Repo] = {
    "core": Repo(
        key="core",
        url="https://github.com/pre-commit/pre-commit-hooks",
        rev="v6.0.0",
        label="pre-commit-hooks",
    ),
    "ruff": Repo(
        key="ruff",
        url="https://github.com/astral-sh/ruff-pre-commit",
        rev="v0.16.1",
        label="ruff",
    ),
    "black": Repo(
        key="black",
        url="https://github.com/psf/black-pre-commit-mirror",
        rev="26.5.1",
        label="black",
    ),
    "isort": Repo(
        key="isort",
        url="https://github.com/PyCQA/isort",
        rev="8.0.1",
        label="isort",
    ),
    "flake8": Repo(
        key="flake8",
        url="https://github.com/PyCQA/flake8",
        rev="7.3.0",
        label="flake8",
    ),
    "pyupgrade": Repo(
        key="pyupgrade",
        url="https://github.com/asottile/pyupgrade",
        rev="v3.21.2",
        label="pyupgrade",
    ),
    "mypy": Repo(
        key="mypy",
        url="https://github.com/pre-commit/mirrors-mypy",
        rev="v2.3.0",
        label="mypy",
    ),
    # The upstream `pre-commit/mirrors-prettier` is archived and pinned to prettier 3.1;
    # this is the maintained mirror the pre-commit docs now point at.
    "prettier": Repo(
        key="prettier",
        url="https://github.com/rbubley/mirrors-prettier",
        rev="v3.9.6",
        label="prettier",
    ),
    "eslint": Repo(
        key="eslint",
        url="https://github.com/pre-commit/mirrors-eslint",
        rev="v10.8.0",
        label="eslint",
    ),
    "shellcheck": Repo(
        key="shellcheck",
        url="https://github.com/koalaman/shellcheck-precommit",
        rev="v0.11.0",
        label="shellcheck",
    ),
    "shfmt": Repo(
        key="shfmt",
        url="https://github.com/scop/pre-commit-shfmt",
        rev="v3.13.1-1",
        label="shfmt",
    ),
    "hadolint": Repo(
        key="hadolint",
        url="https://github.com/hadolint/hadolint",
        rev="v2.15.1",
        label="hadolint",
    ),
    "yamllint": Repo(
        key="yamllint",
        url="https://github.com/adrienverge/yamllint",
        rev="v1.38.0",
        label="yamllint",
    ),
    "markdownlint": Repo(
        key="markdownlint",
        url="https://github.com/igorshubovych/markdownlint-cli",
        rev="v0.49.1",
        label="markdownlint",
    ),
    "gitleaks": Repo(
        key="gitleaks",
        url="https://github.com/gitleaks/gitleaks",
        rev="v8.30.0",
        label="gitleaks",
    ),
    "commitizen": Repo(
        key="commitizen",
        url="https://github.com/commitizen-tools/commitizen",
        rev="v4.17.0",
        label="commitizen",
    ),
}

# ---------------------------------------------------------------------------
# Hook groups
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Group:
    key: str
    label: str
    description: str


# Declaration order is the order the picker groups hooks in.
GROUPS: dict[str, Group] = {
    "hygiene": Group("hygiene", "File hygiene", "Whitespace, line endings, stray large files"),
    "data": Group("data", "Config files", "Parse-check YAML, JSON, TOML and XML"),
    "python": Group("python", "Python", "Formatting, linting and type checking"),
    "web": Group("web", "JavaScript / web", "Prettier and ESLint"),
    "shell": Group("shell", "Shell", "shellcheck and shfmt"),
    "docker": Group("docker", "Docker", "Dockerfile linting"),
    "docs": Group("docs", "Docs and YAML style", "markdownlint and yamllint"),
    "security": Group("security", "Secrets and safety", "Stop credentials and bad branches"),
    "git": Group("git", "Commit messages", "Enforce a commit convention"),
}

# ---------------------------------------------------------------------------
# Hooks
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Hook:
    """One hook entry.

    The optional fields map one-for-one onto pre-commit's own per-hook keys, so what
    the emitter writes is a hook mapping a user can go on hand-editing. Anything
    beyond these is deliberately a code change: this is a curated catalog, not a
    generic YAML builder.
    """

    key: str  # unique in this catalog; equal to `id` unless one repo needs two variants
    id: str  # the hook id pre-commit resolves inside `repo`
    repo: str  # REPOS key
    description: str
    group: str
    args: tuple[str, ...] = ()
    additional_dependencies: tuple[str, ...] = ()
    # Empty means "whatever the hook itself declares", which is `pre-commit` for all
    # but a handful. Set it only to override, never to restate the default.
    stages: tuple[str, ...] = ()
    types_or: tuple[str, ...] = ()
    files: str = ""
    exclude: str = ""
    # File suffixes / names whose presence in the repo suggests this hook. Drives the
    # "what's in this project" preset; never a decision, only a default.
    markers: tuple[str, ...] = ()
    # Whether the hook rewrites files rather than only reporting. pre-commit fails the
    # commit either way, but a fixer means "re-stage and commit again".
    fixes: bool = False
    # A prerequisite the hook needs on the machine, if any (docker, a node toolchain).
    needs: str = ""

    def effective_stages(self) -> tuple[str, ...]:
        return self.stages or (DEFAULT_STAGE,)


# Declaration order is canonical: the picker, the generated file and every preview
# read hooks in this order (within their repo's position in REPOS).
HOOKS: tuple[Hook, ...] = (
    # -- file hygiene (pre-commit-hooks) ------------------------------------------
    Hook(
        key="trailing-whitespace",
        id="trailing-whitespace",
        repo="core",
        description="Strip trailing whitespace at the end of lines",
        group="hygiene",
        fixes=True,
    ),
    Hook(
        key="end-of-file-fixer",
        id="end-of-file-fixer",
        repo="core",
        description="Ensure every file ends in exactly one newline",
        group="hygiene",
        fixes=True,
    ),
    Hook(
        key="mixed-line-ending",
        id="mixed-line-ending",
        repo="core",
        description="Normalise CRLF/LF so a file does not mix both",
        group="hygiene",
        args=("--fix=lf",),
        fixes=True,
    ),
    Hook(
        key="check-added-large-files",
        id="check-added-large-files",
        repo="core",
        description="Refuse files over 500 kB — usually a build artifact or a blob",
        group="hygiene",
        args=("--maxkb=500",),
    ),
    Hook(
        key="check-merge-conflict",
        id="check-merge-conflict",
        repo="core",
        description="Refuse committed merge-conflict markers",
        group="hygiene",
    ),
    Hook(
        key="check-case-conflict",
        id="check-case-conflict",
        repo="core",
        description="Refuse names that collide on case-insensitive filesystems",
        group="hygiene",
    ),
    Hook(
        key="check-executables-have-shebangs",
        id="check-executables-have-shebangs",
        repo="core",
        description="An executable file must start with a shebang",
        group="hygiene",
    ),
    Hook(
        key="check-shebang-scripts-are-executable",
        id="check-shebang-scripts-are-executable",
        repo="core",
        description="…and the converse: a shebang means the file should be executable",
        group="hygiene",
    ),
    Hook(
        key="check-symlinks",
        id="check-symlinks",
        repo="core",
        description="Refuse symlinks that point nowhere",
        group="hygiene",
    ),
    Hook(
        key="fix-byte-order-marker",
        id="fix-byte-order-marker",
        repo="core",
        description="Remove UTF-8 byte-order marks",
        group="hygiene",
        fixes=True,
    ),
    # -- config files --------------------------------------------------------------
    Hook(
        key="check-yaml",
        id="check-yaml",
        repo="core",
        description="YAML files parse",
        group="data",
        markers=(".yaml", ".yml"),
    ),
    Hook(
        key="check-json",
        id="check-json",
        repo="core",
        description="JSON files parse",
        group="data",
        markers=(".json",),
    ),
    Hook(
        key="check-toml",
        id="check-toml",
        repo="core",
        description="TOML files parse",
        group="data",
        markers=(".toml",),
    ),
    Hook(
        key="check-xml",
        id="check-xml",
        repo="core",
        description="XML files parse",
        group="data",
        markers=(".xml",),
    ),
    Hook(
        key="pretty-format-json",
        id="pretty-format-json",
        repo="core",
        description="Reformat JSON with sorted keys and a fixed indent",
        group="data",
        args=("--autofix", "--indent=2", "--no-sort-keys"),
        markers=(".json",),
        fixes=True,
    ),
    # -- python --------------------------------------------------------------------
    Hook(
        key="check-ast",
        id="check-ast",
        repo="core",
        description="Python files parse",
        group="python",
        markers=(".py",),
    ),
    Hook(
        key="debug-statements",
        id="debug-statements",
        repo="core",
        description="Refuse a stray breakpoint() or pdb import",
        group="python",
        markers=(".py",),
    ),
    Hook(
        key="requirements-txt-fixer",
        id="requirements-txt-fixer",
        repo="core",
        description="Keep requirements.txt sorted",
        group="python",
        markers=("requirements.txt",),
        fixes=True,
    ),
    Hook(
        key="ruff-check",
        id="ruff-check",
        repo="ruff",
        description="Lint with ruff, fixing what it safely can",
        group="python",
        # `--exit-non-zero-on-fix` is what makes the commit stop after ruff rewrote a
        # file: without it the hook exits 0 having changed files out from under the
        # staged snapshot, and the fixes silently miss the commit.
        args=("--fix", "--exit-non-zero-on-fix"),
        markers=(".py",),
        fixes=True,
    ),
    Hook(
        key="ruff-format",
        id="ruff-format",
        repo="ruff",
        description="Format with ruff (a drop-in for black)",
        group="python",
        markers=(".py",),
        fixes=True,
    ),
    Hook(
        key="black",
        id="black",
        repo="black",
        description="Format with black",
        group="python",
        markers=(".py",),
        fixes=True,
    ),
    Hook(
        key="isort",
        id="isort",
        repo="isort",
        description="Sort imports",
        group="python",
        args=("--profile=black",),
        markers=(".py",),
        fixes=True,
    ),
    Hook(
        key="flake8",
        id="flake8",
        repo="flake8",
        description="Lint with flake8",
        group="python",
        markers=(".py",),
    ),
    Hook(
        key="pyupgrade",
        id="pyupgrade",
        repo="pyupgrade",
        description="Rewrite old syntax for a newer Python",
        group="python",
        args=("--py310-plus",),
        markers=(".py",),
        fixes=True,
    ),
    Hook(
        key="mypy",
        id="mypy",
        repo="mypy",
        description="Type-check with mypy",
        group="python",
        # mypy in pre-commit runs in its own isolated venv, so it cannot see the
        # project's dependencies and reports every third-party import as missing.
        # Listing the stubs the project needs here is the fix; `--ignore-missing-imports`
        # is the blunter one, and is what keeps a first run from being a wall of noise.
        args=("--ignore-missing-imports",),
        markers=(".py",),
    ),
    # -- web -----------------------------------------------------------------------
    Hook(
        key="prettier",
        id="prettier",
        repo="prettier",
        description="Format JS, TS, CSS, JSON, Markdown and YAML",
        group="web",
        markers=(".js", ".jsx", ".ts", ".tsx", ".css", ".scss", ".vue", ".svelte"),
        fixes=True,
        needs="node (pre-commit installs its own)",
    ),
    Hook(
        key="eslint",
        id="eslint",
        repo="eslint",
        description="Lint JS/TS — needs an eslint config in the project",
        group="web",
        markers=(".js", ".jsx", ".ts", ".tsx"),
        needs="an eslint config, and its plugins in additional_dependencies",
    ),
    # -- shell ---------------------------------------------------------------------
    Hook(
        key="shellcheck",
        id="shellcheck",
        repo="shellcheck",
        description="Lint shell scripts",
        group="shell",
        markers=(".sh", ".bash"),
        needs="docker",
    ),
    Hook(
        key="shfmt",
        id="shfmt",
        repo="shfmt",
        description="Format shell scripts",
        group="shell",
        args=("-w", "-i", "2", "-ci"),
        markers=(".sh", ".bash"),
        fixes=True,
        needs="docker",
    ),
    # -- docker --------------------------------------------------------------------
    Hook(
        key="hadolint",
        id="hadolint-docker",
        repo="hadolint",
        description="Lint Dockerfiles",
        group="docker",
        markers=("Dockerfile", ".dockerfile"),
        needs="docker",
    ),
    # -- docs ----------------------------------------------------------------------
    Hook(
        key="markdownlint",
        id="markdownlint",
        repo="markdownlint",
        description="Lint Markdown style",
        group="docs",
        markers=(".md",),
        needs="node (pre-commit installs its own)",
    ),
    Hook(
        key="yamllint",
        id="yamllint",
        repo="yamllint",
        description="Lint YAML style, beyond just parsing",
        group="docs",
        args=("--strict",),
        markers=(".yaml", ".yml"),
    ),
    # -- security ------------------------------------------------------------------
    Hook(
        key="detect-private-key",
        id="detect-private-key",
        repo="core",
        description="Refuse a committed private key",
        group="security",
    ),
    Hook(
        key="detect-aws-credentials",
        id="detect-aws-credentials",
        repo="core",
        description="Refuse committed AWS keys",
        group="security",
        # Without this the hook errors out on any machine that has no ~/.aws/credentials
        # to compare against, which is most CI runners.
        args=("--allow-missing-credentials",),
    ),
    Hook(
        key="no-commit-to-branch",
        id="no-commit-to-branch",
        repo="core",
        description="Refuse commits made directly on master/main",
        group="security",
        args=("--branch=main", "--branch=master"),
    ),
    Hook(
        key="gitleaks",
        id="gitleaks",
        repo="gitleaks",
        description="Scan the staged diff for secrets of any kind",
        group="security",
    ),
    # -- commit messages -----------------------------------------------------------
    Hook(
        key="commitizen",
        id="commitizen",
        repo="commitizen",
        description="Check the commit message against your commitizen rules",
        group="git",
        stages=("commit-msg",),
        markers=(".cz.toml", "cz.toml"),
        needs="a commitizen config — `devstuff configure commitizen`",
    ),
    Hook(
        key="commitizen-branch",
        id="commitizen-branch",
        repo="commitizen",
        description="Check every commit on the branch before it is pushed",
        group="git",
        stages=("pre-push",),
        needs="a commitizen config — `devstuff configure commitizen`",
    ),
)

HOOKS_BY_KEY: dict[str, Hook] = {h.key: h for h in HOOKS}

# ---------------------------------------------------------------------------
# Presets
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Preset:
    key: str
    label: str
    description: str
    hooks: tuple[str, ...]
    # Languages this preset is meant for, so `detect` can recommend one.
    languages: tuple[str, ...] = ()


# The language-agnostic base every other preset builds on. Kept small on purpose: a
# first pre-commit run that rewrites four hundred files is one people delete.
_ESSENTIALS = (
    "trailing-whitespace",
    "end-of-file-fixer",
    "check-merge-conflict",
    "check-added-large-files",
    "check-case-conflict",
    "detect-private-key",
    "check-yaml",
    "check-toml",
    "check-json",
)

_PYTHON = ("check-ast", "debug-statements", "ruff-check", "ruff-format")

PRESETS: dict[str, Preset] = {
    "minimal": Preset(
        key="minimal",
        label="Minimal",
        description="Four hooks that are right for any repository and fix rather than nag.",
        hooks=(
            "trailing-whitespace",
            "end-of-file-fixer",
            "check-merge-conflict",
            "check-added-large-files",
        ),
    ),
    "essentials": Preset(
        key="essentials",
        label="Essentials",
        description="File hygiene, config-file parsing and a private-key guard. No language tools.",
        hooks=_ESSENTIALS,
    ),
    "python": Preset(
        key="python",
        label="Python (ruff)",
        description="Essentials plus ruff for both linting and formatting — the modern default.",
        hooks=_ESSENTIALS + _PYTHON,
        languages=("python",),
    ),
    "python-strict": Preset(
        key="python-strict",
        label="Python, strict",
        description="The ruff set plus mypy type checking, yamllint and a secret scanner.",
        hooks=_ESSENTIALS + _PYTHON + ("mypy", "yamllint", "gitleaks"),
        languages=("python",),
    ),
    "python-black": Preset(
        key="python-black",
        label="Python (black + isort + flake8)",
        description="The pre-ruff toolchain, for a project already standardised on it.",
        hooks=_ESSENTIALS
        + ("check-ast", "debug-statements", "black", "isort", "flake8", "pyupgrade"),
        languages=("python",),
    ),
    "web": Preset(
        key="web",
        label="JavaScript / TypeScript",
        description="Essentials plus prettier formatting and eslint.",
        hooks=_ESSENTIALS + ("prettier", "eslint"),
        languages=("javascript",),
    ),
    "shell": Preset(
        key="shell",
        label="Shell scripts",
        description="Essentials plus shellcheck and shfmt. Both run in Docker.",
        hooks=_ESSENTIALS
        + ("check-executables-have-shebangs", "check-shebang-scripts-are-executable",
           "shellcheck", "shfmt"),
        languages=("shell",),
    ),
    "security": Preset(
        key="security",
        label="Security first",
        description="Essentials plus gitleaks, AWS-key detection and a no-commits-to-main guard.",
        hooks=_ESSENTIALS + ("detect-aws-credentials", "no-commit-to-branch", "gitleaks"),
    ),
    "docs": Preset(
        key="docs",
        label="Documentation",
        description="Essentials plus markdownlint and yamllint, for a docs or config repository.",
        hooks=_ESSENTIALS + ("markdownlint", "yamllint"),
    ),
    "detected": Preset(
        key="detected",
        label="Match this project",
        description="Essentials plus the hooks for the languages actually found here.",
        hooks=_ESSENTIALS,  # replaced by `detect.suggest` with what it found
    ),
    "empty": Preset(
        key="empty",
        label="Start from nothing",
        description="No hooks — pick every one yourself on the next screen.",
        hooks=(),
    ),
}

DEFAULT_PRESET = "detected"

# Which hooks a detected language pulls in, on top of the essentials. Keyed by the
# language names `detect.languages()` reports.
LANGUAGE_HOOKS: dict[str, tuple[str, ...]] = {
    "python": _PYTHON,
    "javascript": ("prettier",),
    "shell": ("shellcheck", "shfmt"),
    "docker": ("hadolint",),
    "markdown": ("markdownlint",),
    "yaml": ("yamllint",),
}

# ---------------------------------------------------------------------------
# Where the config goes
# ---------------------------------------------------------------------------

# pre-commit reads exactly this one filename, and `-c` is the only way to point it
# elsewhere. There is no search order to mirror, unlike commitizen.
CONFIG_FILE = ".pre-commit-config.yaml"
# The legacy spelling. pre-commit does not read it, but a repo carrying one is a repo
# whose author thinks it is configured — worth saying rather than silently ignoring.
LEGACY_CONFIG_FILE = ".pre-commit-config.yml"

DOCS_URL = "https://pre-commit.com/#pre-commit-configyaml---top-level"

# ---------------------------------------------------------------------------
# The config
# ---------------------------------------------------------------------------


@dataclass
class PreCommitConfig:
    preset: str = DEFAULT_PRESET
    # Selected hook keys, normalised into HOOKS order by `selected()`.
    hooks: list[str] = field(default_factory=lambda: list(PRESETS["essentials"].hooks))
    # Per-hook overrides of the table above, so the tables stay the source of truth
    # and only what the user changed is recorded.
    args: dict[str, tuple[str, ...]] = field(default_factory=dict)
    # Repo key → rev, filled in by `validate.autoupdate`. The pins in `REPOS` were
    # current when they were written; the only thing that knows what is current *now*
    # is `pre-commit autoupdate`, so the wizard offers to ask it rather than pretending
    # a table in a released package can stay fresh (SD-4).
    revs: dict[str, str] = field(default_factory=dict)

    # Top-level settings
    fail_fast: bool = False
    exclude: str = ""
    # `default_install_hook_types` is derived from the selected hooks' stages rather
    # than asked about — see `install_hook_types()`.
    autoupdate_schedule: str = "monthly"
    use_ci: bool = False

    # Wizard actions, not file contents
    install_hooks: bool = True

    target: str = CONFIG_FILE

    # -- resolved views ------------------------------------------------------

    def catalog(self) -> dict[str, Hook]:
        """Every hook this config knows about, with the user's arg overrides applied."""
        from dataclasses import replace

        return {
            key: (replace(hook, args=self.args[key]) if key in self.args else hook)
            for key, hook in HOOKS_BY_KEY.items()
        }

    def selected(self) -> list[Hook]:
        """Selected hooks in canonical order — the HOOKS table's order. Unknown keys
        are dropped rather than raising: a preset is data, and a stale one should
        degrade to the hooks that do exist."""
        catalog = self.catalog()
        chosen = set(self.hooks)
        return [catalog[h.key] for h in HOOKS if h.key in chosen and h.key in catalog]

    def by_repo(self) -> list[tuple[Repo, list[Hook]]]:
        """Selected hooks grouped into the `repos:` list, in REPOS order.

        pre-commit allows the same repo twice, but a file that lists it once is the
        one a human can read, and it is what `autoupdate` produces.
        """
        from dataclasses import replace

        grouped: dict[str, list[Hook]] = {}
        for hook in self.selected():
            grouped.setdefault(hook.repo, []).append(hook)
        return [
            (replace(REPOS[key], rev=self.revs[key]) if key in self.revs else REPOS[key],
             grouped[key])
            for key in REPOS
            if key in grouped
        ]

    def install_hook_types(self) -> list[str]:
        """The git hook types `pre-commit install` has to be given.

        This is derived, never asked. `pre-commit install` installs *only* the
        `pre-commit` hook unless told otherwise, so selecting the commitizen hook
        (stage `commit-msg`) and stopping there produces a config that validates,
        installs cleanly, and then never runs — the single most common way a
        pre-commit setup is quietly broken.
        """
        types = {DEFAULT_STAGE}
        for hook in self.selected():
            types.update(s for s in hook.effective_stages() if s != MANUAL_STAGE)
        return [key for key in STAGES if key in types and key != MANUAL_STAGE]

    def fixers(self) -> list[Hook]:
        return [h for h in self.selected() if h.fixes]

    def prerequisites(self) -> dict[str, list[str]]:
        """Requirement → the hooks needing it, for the review screen."""
        out: dict[str, list[str]] = {}
        for hook in self.selected():
            if hook.needs:
                out.setdefault(hook.needs, []).append(hook.key)
        return out

    def groups(self) -> list[tuple[Group, list[Hook]]]:
        grouped: dict[str, list[Hook]] = {}
        for hook in self.selected():
            grouped.setdefault(hook.group, []).append(hook)
        return [(GROUPS[key], grouped[key]) for key in GROUPS if key in grouped]

    def conflicts(self) -> list[str]:
        """Selected hooks that will fight each other over the same files.

        pre-commit runs hooks in file order and re-stages between them, so two
        formatters with different opinions do not error — they just reformat each
        other's output on alternate commits, forever.
        """
        chosen = set(self.hooks)
        out: list[str] = []
        for first, second, why in _CONFLICTS:
            if first in chosen and second in chosen:
                out.append(f"{first} and {second}: {why}")
        return out


# Pairs that both rewrite the same files with different rules.
_CONFLICTS: tuple[tuple[str, str, str], ...] = (
    ("ruff-format", "black", "both format Python, and they will undo each other"),
    ("ruff-check", "flake8", "ruff already implements flake8's checks"),
    ("ruff-check", "isort", "ruff's `I` rules already sort imports"),
    ("ruff-check", "pyupgrade", "ruff's `UP` rules already do this"),
    ("check-json", "pretty-format-json", "pretty-format-json parses the file anyway"),
)
