from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import click
import questionary

from dev_setup import ui

_TARGETS: dict[str, Path] = {
    "claude": Path.home() / ".claude" / "skills",
    "codex": Path.home() / ".codex" / "skills",
    "pi": Path.home() / ".pi" / "skills",
}


@click.group("skills", invoke_without_command=True)
@click.pass_context
def skills_cmd(ctx: click.Context) -> None:
    """Append skills from a GitHub repository to claude, codex, and/or pi."""
    if ctx.invoked_subcommand is None:
        ctx.invoke(add_cmd)


@skills_cmd.command("add")
def add_cmd() -> None:
    """Clone a GitHub repo and copy its skills into the selected tools."""
    ui.section("Add Skills From GitHub")

    repo = ui.text_input(
        "GitHub repository (e.g. owner/repo or a full URL):", required=True
    )

    targets = ui.checkbox(
        "Apply skills to which tools?",
        [
            questionary.Choice(title="claude", checked=True),
            questionary.Choice(title="codex", checked=True),
            questionary.Choice(title="pi", checked=True),
        ],
    )
    if not targets:
        ui.warn("No targets selected — aborted.")
        return

    tmp_dir = Path(tempfile.mkdtemp(prefix="devstuff-skills-"))
    try:
        clone_url = _clone_repo(repo, tmp_dir)
        if clone_url is None:
            return

        skill_dirs = _discover_skills(tmp_dir, repo)
        if not skill_dirs:
            ui.error("No skills found in the repository.")
            ui.dim(
                "Expected a top-level skills/<name>/ directory, or a repo whose "
                "root itself is a single skill."
            )
            return

        ui.console.print()
        ui.info(f"Found {len(skill_dirs)} skill(s): {', '.join(d.name for d in skill_dirs)}")
        ui.console.print()

        _install_skills(skill_dirs, targets)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _clone_repo(repo: str, dest: Path) -> str | None:
    """Clone `repo` into `dest`, prompting for auth if the initial attempt fails."""
    https_url = _to_https_url(repo)

    with ui.spinner(f"Cloning {https_url}..."):
        result = _git_clone(https_url, dest)
    if result.returncode == 0:
        ui.success("Repository cloned.")
        return https_url

    ui.warn("Could not clone the repository anonymously — it may be private.")
    auth_method = ui.select(
        "How would you like to authenticate?",
        ["SSH key file", "Personal access token", "Abort"],
    )

    if auth_method == "SSH key file":
        ssh_key = ui.text_input(
            "Path to SSH private key file:", default="~/.ssh/id_ed25519", required=True
        )
        ssh_key_path = Path(ssh_key).expanduser()
        if not ssh_key_path.is_file():
            ui.error(f"No such file: {ssh_key_path}")
            return None

        ssh_url = _to_ssh_url(repo)
        env_overrides = {
            "GIT_SSH_COMMAND": (
                f"ssh -i {ssh_key_path} -o IdentitiesOnly=yes "
                f"-o StrictHostKeyChecking=accept-new"
            )
        }
        with ui.spinner(f"Cloning {ssh_url}..."):
            result = _git_clone(ssh_url, dest, env_overrides=env_overrides)
        if result.returncode != 0:
            ui.error("Clone failed with the provided SSH key.")
            ui.dim(result.stderr.strip()[-500:])
            return None
        ui.success("Repository cloned.")
        return ssh_url

    if auth_method == "Personal access token":
        token = questionary.password("GitHub personal access token:").ask()
        if not token:
            ui.warn("No token provided — aborted.")
            return None
        owner_repo = _owner_repo(repo)
        token_url = f"https://x-access-token:{token}@github.com/{owner_repo}.git"
        with ui.spinner("Cloning with token..."):
            result = _git_clone(token_url, dest)
        if result.returncode != 0:
            ui.error("Clone failed with the provided token.")
            ui.dim(result.stderr.strip()[-500:])
            return None
        ui.success("Repository cloned.")
        return https_url  # don't retain the token in the returned URL

    ui.warn("Aborted.")
    return None


def _git_clone(
    url: str, dest: Path, env_overrides: dict[str, str] | None = None
) -> subprocess.CompletedProcess:
    import os

    env = os.environ.copy()
    if env_overrides:
        env.update(env_overrides)
    # dest already exists (mkdtemp); clone into it directly.
    return subprocess.run(
        ["git", "clone", "--depth", "1", url, str(dest)],
        capture_output=True,
        text=True,
        env=env,
    )


def _owner_repo(repo: str) -> str:
    """Normalize 'owner/repo', a full https URL, or an ssh URL to 'owner/repo'."""
    r = repo.strip()
    r = r.removeprefix("https://github.com/").removeprefix("git@github.com:")
    r = r.removesuffix(".git")
    return r.strip("/")


def _to_https_url(repo: str) -> str:
    if repo.startswith(("http://", "https://", "git@")):
        return repo
    return f"https://github.com/{_owner_repo(repo)}.git"


def _to_ssh_url(repo: str) -> str:
    if repo.startswith("git@"):
        return repo
    return f"git@github.com:{_owner_repo(repo)}.git"


def _find_skill_dirs(root: Path) -> list[Path]:
    """Recursively find directories marked as skills by a SKILL.md file.

    Does not descend into a directory once it's identified as a skill, since
    a skill's own subdirectories (scripts/, references/) aren't skills.
    """
    found: list[Path] = []

    def _walk(d: Path) -> None:
        if d.name == ".git":
            return
        if (d / "SKILL.md").is_file():
            found.append(d)
            return
        for child in d.iterdir():
            if child.is_dir():
                _walk(child)

    _walk(root)
    return sorted(found)


def _discover_skills(repo_dir: Path, repo: str) -> list[Path]:
    """Return the list of skill directories found in a cloned repo."""
    by_marker = _find_skill_dirs(repo_dir)
    if by_marker:
        return by_marker

    skills_root = repo_dir / "skills"
    if skills_root.is_dir():
        return sorted(
            p for p in skills_root.iterdir()
            if p.is_dir() and not p.name.startswith(".")
        )

    # No SKILL.md markers or skills/ subdir — if the repo root looks like a
    # single skill, use it.
    has_content = any(
        p.name not in (".git",) for p in repo_dir.iterdir()
    )
    if not has_content:
        return []

    name = _owner_repo(repo).split("/")[-1]
    single = repo_dir.parent / f"_single_skill_{name}"
    if single.exists():
        shutil.rmtree(single)
    shutil.copytree(repo_dir, single, ignore=shutil.ignore_patterns(".git"))
    return [single]


def _install_skills(skill_dirs: list[Path], targets: list[str]) -> None:
    for target in targets:
        base = _TARGETS[target]
        base.mkdir(parents=True, exist_ok=True)
        for skill_dir in skill_dirs:
            dest = base / skill_dir.name
            if dest.exists():
                if not ui.confirm(
                    f"'{skill_dir.name}' already exists for {target} — overwrite?",
                    default=False,
                ):
                    ui.dim(f"Skipped {skill_dir.name} for {target}.")
                    continue
                shutil.rmtree(dest)
            shutil.copytree(skill_dir, dest, ignore=shutil.ignore_patterns(".git"))
            ui.success(f"Installed '{skill_dir.name}' → {dest}")
