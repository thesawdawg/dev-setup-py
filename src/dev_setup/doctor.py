"""Health checks and fix actions for `devstuff doctor`.

Each check is a pure function returning a :class:`CheckResult` — no UI, no side
effects. Fix actions are separate functions that perform the fix and verify the
result, returning ``True`` on success. The command layer
(:mod:`dev_setup.commands.doctor_cmd`) orchestrates the two: run checks, display
results, and offer fixes for any check that carries one.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from dev_setup.base import remove_bashrc_block

# Status constants — kept as plain strings so tests can compare without importing.
PASS = "pass"
WARN = "warn"
FAIL = "fail"

# Pre-v1.19 the package shipped a `dev-setup` command alias and wrote config/data
# under ~/.config/dev-setup and ~/.local/share/dev-setup, with bashrc blocks
# marked `# dev-setup: ...` / `# dev-setup-fn:...`.
_OLD_CONFIG_DIR = Path.home() / ".config" / "dev-setup"
_OLD_DATA_DIR = Path.home() / ".local" / "share" / "dev-setup"
_NEW_CONFIG_DIR = Path.home() / ".config" / "devstuff"
_NEW_DATA_DIR = Path.home() / ".local" / "share" / "devstuff"

_OLD_BLOCK_RE = re.compile(r"^#\s+dev-setup(?::\s*\S+|-fn:\S+)")


@dataclass
class CheckResult:
    """The outcome of one health check.

    ``fix`` is a zero-arg callable that attempts to resolve the problem and
    returns ``True`` on success. ``None`` means the check is informational only
    (either it passed, or the problem can't be auto-fixed).
    """

    name: str
    status: str  # PASS | WARN | FAIL
    message: str
    detail: str = ""
    fix: Callable[[], bool] | None = field(default=None, repr=False)

    @property
    def ok(self) -> bool:
        return self.status == PASS


# ─── checks ──────────────────────────────────────────────────────────────────


def check_python_version() -> CheckResult:
    """Python meets the minimum version (3.11+)."""
    v = sys.version_info
    version_str = f"{v.major}.{v.minor}.{v.micro}"
    if (v.major, v.minor) >= (3, 11):
        return CheckResult("python-version", PASS, f"Python {version_str}")
    return CheckResult("python-version", FAIL, f"Python {version_str} — 3.11+ required")


def check_runtime_deps() -> CheckResult:
    """All runtime dependencies are importable."""
    missing: list[str] = []
    for mod in ("click", "yaml", "rich", "questionary"):
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)
    if missing:
        return CheckResult("runtime-deps", FAIL, f"Cannot import: {', '.join(missing)}")
    return CheckResult("runtime-deps", PASS, "All runtime dependencies importable")


def check_config_dir() -> CheckResult:
    """Config directory exists and is writable."""
    from dev_setup.catalog import CONFIG_DIR

    if not CONFIG_DIR.exists():
        return CheckResult(
            "config-dir",
            WARN,
            f"{CONFIG_DIR} does not exist",
            "It will be created on first write, but creating it now avoids surprises.",
            fix=lambda: _fix_create_dir(CONFIG_DIR),
        )
    if not os.access(CONFIG_DIR, os.W_OK):
        return CheckResult("config-dir", FAIL, f"{CONFIG_DIR} is not writable")
    return CheckResult("config-dir", PASS, f"{CONFIG_DIR} exists and is writable")


def check_bundled_tools_catalog() -> CheckResult:
    """Bundled tools.yaml loads and validates."""
    from dev_setup.catalog import CatalogError, load_bundled_catalog

    try:
        tools = load_bundled_catalog()
    except CatalogError as exc:
        return CheckResult("bundled-tools-catalog", FAIL, f"Bundled tools.yaml invalid: {exc}")
    return CheckResult("bundled-tools-catalog", PASS, f"Bundled tools.yaml valid ({len(tools)} tools)")


def check_user_tools_catalog() -> CheckResult:
    """User tools.yaml (if present) loads and validates."""
    from dev_setup import catalog as cat

    if not cat.USER_CATALOG_PATH.exists():
        return CheckResult("user-tools-catalog", PASS, "No user tools.yaml (using bundled only)")
    try:
        tools = cat.read_user_catalog()
    except cat.CatalogError as exc:
        return CheckResult("user-tools-catalog", FAIL, f"User tools.yaml invalid: {exc}")
    return CheckResult("user-tools-catalog", PASS, f"User tools.yaml valid ({len(tools)} tools)")


def check_bundled_functions_catalog() -> CheckResult:
    """Bundled functions.yaml loads and validates."""
    from dev_setup import functions_catalog as fc

    try:
        fns = fc.load_bundled_catalog()
    except fc.CatalogError as exc:
        return CheckResult("bundled-functions-catalog", FAIL, f"Bundled functions.yaml invalid: {exc}")
    return CheckResult(
        "bundled-functions-catalog", PASS, f"Bundled functions.yaml valid ({len(fns)} functions)"
    )


def check_user_functions_catalog() -> CheckResult:
    """User functions.yaml (if present) loads and validates."""
    from dev_setup import functions_catalog as fc

    if not fc.USER_CATALOG_PATH.exists():
        return CheckResult("user-functions-catalog", PASS, "No user functions.yaml")
    try:
        fns = fc.read_user_catalog()
    except fc.CatalogError as exc:
        return CheckResult("user-functions-catalog", FAIL, f"User functions.yaml invalid: {exc}")
    return CheckResult(
        "user-functions-catalog", PASS, f"User functions.yaml valid ({len(fns)} functions)"
    )


def check_bundled_agent_catalog() -> CheckResult:
    """Bundled agent_tools.yaml loads and validates."""
    from dev_setup.agent import catalog as ac

    try:
        cat = ac.load_bundled_catalog()
    except ac.CatalogError as exc:
        return CheckResult("bundled-agent-catalog", FAIL, f"Bundled agent_tools.yaml invalid: {exc}")
    count = len(cat.get("tools", {}))
    return CheckResult(
        "bundled-agent-catalog", PASS, f"Bundled agent_tools.yaml valid ({count} agent tools)"
    )


def check_user_agent_catalog() -> CheckResult:
    """User agent_tools.yaml (if present) loads and validates."""
    from dev_setup.agent import catalog as ac

    if not ac.USER_CATALOG_PATH.exists():
        return CheckResult("user-agent-catalog", PASS, "No user agent_tools.yaml")
    try:
        cat = ac.load_catalog_file(ac.USER_CATALOG_PATH)
    except ac.CatalogError as exc:
        return CheckResult("user-agent-catalog", FAIL, f"User agent_tools.yaml invalid: {exc}")
    count = len(cat.get("tools", {}))
    return CheckResult(
        "user-agent-catalog", PASS, f"User agent_tools.yaml valid ({count} agent tools)"
    )


def check_registry_loads() -> CheckResult:
    """Effective catalog builds into GenericTool instances without error."""
    from dev_setup import registry

    try:
        registry.reload()
        tools = registry.all_tools()
    except Exception as exc:
        return CheckResult("registry", FAIL, f"Registry failed to load: {exc}")
    # Sanity: every tool can answer is_installed() without crashing.
    crashed: list[str] = []
    for tool in tools:
        try:
            tool.is_installed()
        except Exception:
            crashed.append(tool.key)
    if crashed:
        return CheckResult(
            "registry", WARN, f"{len(crashed)} tool(s) crashed on is_installed(): {', '.join(crashed)}"
        )
    return CheckResult("registry", PASS, f"Registry loaded ({len(tools)} tools, all probes OK)")


def check_bashrc_writable() -> CheckResult:
    """~/.bashrc is writable (needed for configurators and function enable)."""
    bashrc = Path.home() / ".bashrc"
    if not bashrc.exists():
        # Not an error — bashrc is created on first patch. But warn so the user
        # knows configurators will create it.
        return CheckResult(
            "bashrc",
            WARN,
            f"{bashrc} does not exist",
            "Configurators and `functions enable` will create it on first use.",
        )
    if not os.access(bashrc, os.W_OK):
        return CheckResult("bashrc", FAIL, f"{bashrc} is not writable")
    return CheckResult("bashrc", PASS, f"{bashrc} is writable")


def check_stale_devsetup_executable() -> CheckResult:
    """No stale `dev-setup` executable on $PATH."""
    found = shutil.which("dev-setup")
    if not found:
        return CheckResult("stale-executable", PASS, "No stale dev-setup executable on $PATH")
    path = Path(found)
    detail = f"symlink -> {path.resolve()}" if path.is_symlink() else "regular file"
    return CheckResult(
        "stale-executable",
        WARN,
        f"Stale `dev-setup` found: {path}",
        detail,
        fix=lambda: _fix_remove_stale_symlink(path),
    )


def check_stale_devsetup_packages() -> CheckResult:
    """No old `dev-setup` package install reported by uv/pipx."""
    managers = _find_stale_package_managers()
    if not managers:
        return CheckResult("stale-packages", PASS, "No stale dev-setup package installs")
    return CheckResult(
        "stale-packages",
        WARN,
        f"Old dev-setup install reported by: {', '.join(managers)}",
        fix=lambda: _fix_uninstall_packages(managers),
    )


def check_old_config_dirs() -> CheckResult:
    """No old ~/.config/dev-setup or ~/.local/share/dev-setup directories."""
    pairs = _find_old_dirs()
    if not pairs:
        return CheckResult("old-dirs", PASS, "No old dev-setup config/data directories")
    paths = ", ".join(str(old) for old, _ in pairs)
    return CheckResult(
        "old-dirs",
        WARN,
        f"Old directories found: {paths}",
        fix=lambda: _fix_move_dirs(pairs),
    )


def check_stale_bashrc_blocks() -> CheckResult:
    """No stale `# dev-setup:` / `# dev-setup-fn:` blocks in ~/.bashrc."""
    blocks = _find_stale_bashrc_blocks()
    if not blocks:
        return CheckResult("stale-bashrc-blocks", PASS, "No stale dev-setup bashrc blocks")
    return CheckResult(
        "stale-bashrc-blocks",
        WARN,
        f"Stale bashrc blocks: {', '.join(repr(b) for b in blocks)}",
        fix=lambda: _fix_remove_bashrc_blocks(blocks),
    )


ALL_CHECKS: list[Callable[[], CheckResult]] = [
    check_python_version,
    check_runtime_deps,
    check_config_dir,
    check_bundled_tools_catalog,
    check_user_tools_catalog,
    check_bundled_functions_catalog,
    check_user_functions_catalog,
    check_bundled_agent_catalog,
    check_user_agent_catalog,
    check_registry_loads,
    check_bashrc_writable,
    check_stale_devsetup_executable,
    check_stale_devsetup_packages,
    check_old_config_dirs,
    check_stale_bashrc_blocks,
]


def run_all_checks() -> list[CheckResult]:
    """Run every check and return the results in order."""
    return [check() for check in ALL_CHECKS]


# ─── detection helpers (read-only, used by checks) ───────────────────────────


def _find_stale_package_managers() -> list[str]:
    """Return managers ('uv', 'pipx') that report a `dev-setup` install."""
    found: list[str] = []
    for mgr, list_cmd in (("uv", ["tool", "list"]), ("pipx", ["list"])):
        exe = shutil.which(mgr)
        if not exe:
            continue
        try:
            r = subprocess.run([exe, *list_cmd], capture_output=True, text=True, timeout=15)
        except (subprocess.SubprocessError, OSError):
            continue
        if r.returncode == 0 and re.search(r"\bdev-setup\b", r.stdout):
            found.append(mgr)
    return found


def _find_old_dirs() -> list[tuple[Path, Path]]:
    """Return (old, new) pairs for old config/data dirs that still exist."""
    pairs: list[tuple[Path, Path]] = []
    for old, new in ((_OLD_CONFIG_DIR, _NEW_CONFIG_DIR), (_OLD_DATA_DIR, _NEW_DATA_DIR)):
        if old.exists():
            pairs.append((old, new))
    return pairs


def _find_stale_bashrc_blocks() -> list[str]:
    """Return block_name strings for old dev-setup markers in ~/.bashrc."""
    bashrc = Path.home() / ".bashrc"
    if not bashrc.exists():
        return []
    blocks: list[str] = []
    for line in bashrc.read_text().splitlines():
        if _OLD_BLOCK_RE.match(line):
            blocks.append(line.lstrip("# ").strip())
    return blocks


# ─── fix actions (destructive; each verifies its own result) ─────────────────


def _fix_create_dir(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    return path.exists() and os.access(path, os.W_OK)


def _fix_uninstall_packages(managers: list[str]) -> bool:
    """Uninstall `dev-setup` from every manager that reported it."""
    all_ok = True
    for mgr in managers:
        cmd = (
            [mgr, "tool", "uninstall", "dev-setup"]
            if mgr == "uv"
            else [mgr, "uninstall", "dev-setup"]
        )
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        except (subprocess.SubprocessError, OSError):
            all_ok = False
            continue
        if r.returncode != 0:
            all_ok = False
            continue
        # verify: re-list and confirm dev-setup is gone from this manager
        list_cmd = ["uv", "tool", "list"] if mgr == "uv" else [mgr, "list"]
        try:
            check = subprocess.run(list_cmd, capture_output=True, text=True, timeout=15)
            if check.returncode == 0 and re.search(r"\bdev-setup\b", check.stdout):
                all_ok = False
        except (subprocess.SubprocessError, OSError):
            pass  # list failed; trust the uninstall exit code
    return all_ok


def _fix_remove_stale_symlink(path: Path) -> bool:
    """Remove a stale `dev-setup` executable (symlink only)."""
    if not path.is_symlink():
        return False  # regular file — don't auto-delete binaries
    target = path.resolve()
    # Refuse to delete if the symlink points inside the *current* devstuff
    # install — that would break the running tool.
    if "devstuff" in str(target) and "dev-setup" not in str(target):
        return False
    try:
        path.unlink()
    except OSError:
        return False
    return shutil.which("dev-setup") is None


def _fix_move_dirs(pairs: list[tuple[Path, Path]]) -> bool:
    """Move each old dir to its new location (merging if new exists)."""
    all_ok = True
    for old, new in pairs:
        if not _move_one_dir(old, new):
            all_ok = False
    return all_ok


def _move_one_dir(old: Path, new: Path) -> bool:
    if not old.exists():
        return True
    new.parent.mkdir(parents=True, exist_ok=True)
    if new.exists():
        # Merge: move each child of old into new, skipping collisions.
        failed = 0
        for child in list(old.iterdir()):
            dest = new / child.name
            if dest.exists():
                continue
            try:
                shutil.move(str(child), str(dest))
            except OSError:
                failed += 1
        if failed:
            return False
        try:
            old.rmdir()
        except OSError:
            pass  # contents moved; empty-dir cleanup is cosmetic
        return True
    try:
        shutil.move(str(old), str(new))
    except OSError:
        return False
    return not old.exists() and new.exists()


def _fix_remove_bashrc_blocks(blocks: list[str]) -> bool:
    """Remove every old bashrc block and verify all are gone."""
    all_ok = True
    for block in blocks:
        if not remove_bashrc_block(block):
            all_ok = False
            continue
        # verify this specific block is gone
        remaining = _find_stale_bashrc_blocks()
        if block in remaining:
            all_ok = False
    return all_ok
