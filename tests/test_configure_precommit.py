from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from dev_setup import configure
from dev_setup.configure.precommit import detect, render, validate, wizard
from dev_setup.configure.precommit.model import (
    CONFIG_FILE,
    DEFAULT_STAGE,
    GROUPS,
    HOOKS,
    HOOKS_BY_KEY,
    LANGUAGE_HOOKS,
    PRESETS,
    REPOS,
    STAGES,
    PreCommitConfig,
)


def cfg(preset: str = "essentials", **kwargs) -> PreCommitConfig:
    kwargs.setdefault("hooks", list(PRESETS[preset].hooks))
    return PreCommitConfig(preset=preset, **kwargs)


def parsed(config: PreCommitConfig) -> dict:
    return yaml.safe_load(render.to_yaml(config))


# -- the model's own invariants --------------------------------------------------


def test_every_hook_points_at_a_real_repo_and_group():
    for hook in HOOKS:
        assert hook.repo in REPOS, hook.key
        assert hook.group in GROUPS, hook.key
        for stage in hook.effective_stages():
            assert stage in STAGES, hook.key


def test_hook_keys_are_unique():
    keys = [hook.key for hook in HOOKS]
    assert len(keys) == len(set(keys))


def test_hook_scalars_are_strings():
    """Everything emitted into `args` reaches pre-commit's schema, which requires
    strings. An int here would only fail at validate-config time."""
    for hook in HOOKS:
        for value in (*hook.args, *hook.additional_dependencies, *hook.types_or):
            assert isinstance(value, str), hook.key


def test_every_preset_names_hooks_that_exist():
    for preset in PRESETS.values():
        for key in preset.hooks:
            assert key in HOOKS_BY_KEY, f"{preset.key} -> {key}"


def test_every_language_maps_to_hooks_that_exist():
    for language, keys in LANGUAGE_HOOKS.items():
        for key in keys:
            assert key in HOOKS_BY_KEY, f"{language} -> {key}"


def test_presets_are_free_of_self_conflicts():
    """A shipped preset must never pair two formatters that undo each other — the
    failure mode is not an error, it is two hooks reformatting on alternate commits."""
    for preset in PRESETS.values():
        config = PreCommitConfig(preset=preset.key, hooks=list(preset.hooks))
        assert config.conflicts() == [], f"{preset.key}: {config.conflicts()}"


def test_conflicts_are_detected_when_the_user_builds_one():
    assert cfg(hooks=["ruff-format", "black"]).conflicts()
    assert not cfg(hooks=["ruff-format"]).conflicts()


def test_selected_is_ordered_by_the_catalog_not_by_the_user():
    config = cfg(hooks=["gitleaks", "trailing-whitespace", "ruff-check"])
    order = [hook.key for hook in config.selected()]
    assert order == ["trailing-whitespace", "ruff-check", "gitleaks"]


def test_unknown_hook_keys_are_dropped_rather_than_raising():
    config = cfg(hooks=["trailing-whitespace", "a-hook-that-was-removed"])
    assert [h.key for h in config.selected()] == ["trailing-whitespace"]


# -- install_hook_types: the quiet-breakage guard ---------------------------------


def test_install_hook_types_is_just_pre_commit_by_default():
    assert cfg().install_hook_types() == [DEFAULT_STAGE]


def test_a_commit_msg_hook_adds_its_own_install_type():
    """`pre-commit install` installs only the pre-commit hook unless told otherwise,
    so a commit-msg hook without this is a config that validates and never runs."""
    config = cfg(hooks=["trailing-whitespace", "commitizen"])
    assert config.install_hook_types() == ["pre-commit", "commit-msg"]
    assert parsed(config)["default_install_hook_types"] == ["pre-commit", "commit-msg"]


def test_a_pre_push_hook_adds_its_own_install_type():
    config = cfg(hooks=["commitizen-branch"])
    assert "pre-push" in config.install_hook_types()


def test_default_install_hook_types_is_omitted_when_it_is_the_default():
    assert "default_install_hook_types" not in parsed(cfg())


def test_manual_stage_never_becomes_an_install_type(monkeypatch):
    """`manual` is the one stage with no git hook behind it — it exists so a hook can
    be run explicitly and never automatically."""
    from dataclasses import replace

    from dev_setup.configure.precommit import model

    monkeypatch.setitem(
        model.HOOKS_BY_KEY, "gitleaks", replace(HOOKS_BY_KEY["gitleaks"], stages=("manual",))
    )
    config = cfg(hooks=["gitleaks"])
    assert config.selected()[0].effective_stages() == ("manual",)
    assert config.install_hook_types() == [DEFAULT_STAGE]


# -- rendering -------------------------------------------------------------------


@pytest.mark.parametrize("preset", sorted(PRESETS))
def test_every_preset_round_trips_through_yaml(preset):
    """The emitter writes YAML by hand for the sake of its comments; this is what
    stops a quoting bug in it from saying something other than the model does."""
    config = cfg(preset)
    assert render.matches(render.to_yaml(config), config)


def test_emitted_args_stay_strings():
    """shfmt's indent width is `2`. Written bare, YAML reads it as an int and
    pre-commit's schema rejects the hook."""
    config = cfg(hooks=["shfmt"])
    args = parsed(config)["repos"][0]["hooks"][0]["args"]
    assert args == ["-w", "-i", "2", "-ci"]
    assert all(isinstance(a, str) for a in args)


def test_revs_stay_strings_even_when_they_look_numeric():
    from dataclasses import replace

    config = cfg(hooks=["black"])
    config.revs = {"black": "1.0"}
    rev = parsed(config)["repos"][0]["rev"]
    assert rev == "1.0" and isinstance(rev, str)
    assert replace(REPOS["black"], rev="1.0").rev == "1.0"


def test_exclude_regex_survives_verbatim():
    pattern = r"^(.*/)?(uv\.lock|package-lock\.json)$"
    config = cfg(exclude=pattern)
    assert parsed(config)["exclude"] == pattern


def test_a_quote_in_an_exclude_does_not_break_the_file():
    config = cfg(exclude="don't-touch/.*")
    assert parsed(config)["exclude"] == "don't-touch/.*"


def test_empty_hook_list_still_produces_a_loadable_file():
    """`repos:` with nothing under it parses as null, which pre-commit rejects."""
    config = cfg(hooks=[])
    assert parsed(config)["repos"] == []
    assert render.matches(render.to_yaml(config), config)


def test_hooks_are_grouped_into_one_entry_per_repo():
    config = cfg("python-black")
    urls = [repo["repo"] for repo in parsed(config)["repos"]]
    assert len(urls) == len(set(urls))


def test_repo_order_follows_the_model_table():
    config = cfg("python")
    keys = [repo.key for repo, _ in config.by_repo()]
    assert keys == [key for key in REPOS if key in keys]


def test_hook_entry_omits_what_the_upstream_repo_already_declares():
    """A config restating a hook's own defaults goes stale when upstream moves them."""
    entry = render.hook_entry(HOOKS_BY_KEY["commitizen"])
    assert entry == {"id": "commitizen"}
    assert "stages" not in entry


def test_the_generated_header_is_what_the_overwrite_guard_looks_for(tmp_path):
    path = tmp_path / CONFIG_FILE
    path.write_text(render.to_yaml(cfg()), encoding="utf-8")
    assert wizard._looks_generated(path)
    path.write_text("repos: []\n", encoding="utf-8")
    assert not wizard._looks_generated(path)


# -- the pre-commit.ci block -----------------------------------------------------


def test_ci_block_is_absent_unless_asked_for():
    assert "ci" not in parsed(cfg())


def test_ci_skips_exactly_the_docker_backed_hooks():
    """pre-commit.ci runs no Docker daemon, so those hooks fail there with a container
    error rather than a lint failure."""
    config = cfg("shell", use_ci=True)
    assert set(render.ci_skip(config)) == {"shellcheck", "shfmt", "hadolint-docker"} & set(
        h.id for h in config.selected()
    )
    assert parsed(config)["ci"]["skip"] == render.ci_skip(config)


def test_ci_block_has_no_skip_key_when_nothing_needs_skipping():
    assert "skip" not in parsed(cfg("python", use_ci=True))["ci"]


# -- argument overrides ----------------------------------------------------------


def test_arg_overrides_reach_the_file_and_leave_the_table_alone():
    config = cfg(hooks=["check-added-large-files"])
    config.args["check-added-large-files"] = ("--maxkb=2000",)
    assert parsed(config)["repos"][0]["hooks"][0]["args"] == ["--maxkb=2000"]
    # The model table is the source of truth and must be unchanged.
    assert HOOKS_BY_KEY["check-added-large-files"].args == ("--maxkb=500",)


def test_clearing_args_removes_the_key_entirely():
    config = cfg(hooks=["check-added-large-files"])
    config.args["check-added-large-files"] = ()
    assert "args" not in parsed(config)["repos"][0]["hooks"][0]


# -- detection -------------------------------------------------------------------


def make_repo(root: Path, files: dict[str, str]) -> Path:
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    return root


def test_languages_ignores_a_single_stray_file():
    """One `.sh` in a Python repo must not pull in two Docker-backed hooks."""
    files = ["a.py", "b.py", "c.py", "tools.sh"]
    assert detect.languages(files) == {"python": 3}


def test_languages_counts_a_real_language():
    assert detect.languages(["a.sh", "b.bash"]) == {"shell": 2}


def test_dockerfile_is_matched_by_name_not_suffix():
    assert detect.languages(["Dockerfile", "api/Dockerfile"]) == {"docker": 2}


def test_suggested_exclude_only_names_files_that_exist():
    pattern = detect.suggested_exclude(["uv.lock", "src/app.py"])
    assert "uv" in pattern and "package-lock" not in pattern
    assert detect.suggested_exclude(["src/app.py"]) == ""


def test_suggested_exclude_is_a_usable_regex():
    import re

    pattern = detect.suggested_exclude(["uv.lock", "web/package-lock.json"])
    compiled = re.compile(pattern)
    assert compiled.match("uv.lock")
    assert compiled.match("web/package-lock.json")
    assert not compiled.match("src/app.py")


def test_existing_hook_ids_reads_a_hand_written_config(tmp_path):
    path = tmp_path / CONFIG_FILE
    path.write_text(
        "repos:\n"
        "  - repo: local\n"
        "    hooks:\n"
        "      - id: my-own-hook\n"
        "        name: whatever\n"
        "        entry: ./check\n"
        "        language: script\n",
        encoding="utf-8",
    )
    assert detect.existing_hook_ids(path) == ["my-own-hook"]


def test_existing_hook_ids_survives_a_broken_config(tmp_path):
    path = tmp_path / CONFIG_FILE
    path.write_text("repos: [\n  unclosed", encoding="utf-8")
    assert detect.existing_hook_ids(path) == []


def test_hooks_installed_recognises_a_pre_commit_hook(tmp_path):
    hooks = tmp_path / ".git" / "hooks"
    hooks.mkdir(parents=True)
    (hooks / "pre-commit").write_text(
        "#!/usr/bin/env bash\n# File generated by pre-commit: https://pre-commit.com\n",
        encoding="utf-8",
    )
    (hooks / "pre-push.sample").write_text("# generated by pre-commit\n", encoding="utf-8")
    (hooks / "commit-msg").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    # Only the real, pre-commit-generated hook counts: a sample is inert and somebody
    # else's commit-msg hook is not ours to claim.
    assert detect.hooks_installed(tmp_path) == ["pre-commit"]


def test_inspect_reports_an_unconfigured_project(tmp_path):
    make_repo(tmp_path, {"app.py": "x = 1\n", "lib.py": "y = 2\n"})
    project = detect.inspect(tmp_path)
    assert project.is_git
    assert project.config is None and not project.configured
    assert project.languages == {"python": 2}
    assert project.installed_hook_types == []


def test_inspect_notices_the_yml_spelling_pre_commit_ignores(tmp_path):
    """pre-commit reads only `.pre-commit-config.yaml`; a repo with the `.yml`
    spelling believes it is configured and is not."""
    make_repo(tmp_path, {".pre-commit-config.yml": "repos: []\n", "a.py": "", "b.py": ""})
    project = detect.inspect(tmp_path)
    assert project.legacy_config is not None
    assert project.config is None


def test_detected_hooks_follow_the_languages_present(tmp_path):
    make_repo(tmp_path, {"a.py": "", "b.py": "", "x.sh": "", "y.sh": ""})
    project = detect.inspect(tmp_path)
    hooks = detect.detected_hooks(project)
    assert "ruff-check" in hooks and "shellcheck" in hooks
    assert "prettier" not in hooks


def test_the_commitizen_hook_is_only_suggested_with_rules_to_check(tmp_path):
    """The hook runs `cz check`, which fails every commit when no commitizen config
    exists — so suggesting it unconditionally would break the user's next commit."""
    make_repo(tmp_path, {"a.py": "", "b.py": ""})
    assert "commitizen" not in detect.detected_hooks(detect.inspect(tmp_path))


def test_the_commitizen_hook_is_suggested_when_rules_exist(tmp_path):
    make_repo(tmp_path, {"a.py": "", "b.py": "", ".cz.toml": "[tool.commitizen]\n"})
    project = detect.inspect(tmp_path)
    assert project.has_commitizen
    assert "commitizen" in detect.detected_hooks(project)


def test_suggest_produces_a_config_that_renders(tmp_path):
    make_repo(tmp_path, {"a.py": "", "b.py": "", "uv.lock": "version = 1\n"})
    config = detect.suggest(detect.inspect(tmp_path))
    assert config.preset == "detected"
    assert "uv" in config.exclude
    assert render.matches(render.to_yaml(config), config)


def test_gitignored_files_do_not_decide_the_language(tmp_path):
    """`git ls-files` is used precisely so a vendored node_modules cannot make a
    Python project look like a JavaScript one."""
    make_repo(
        tmp_path,
        {"a.py": "", "b.py": "", ".gitignore": "node_modules/\n"},
    )
    vendored = tmp_path / "node_modules" / "pkg"
    vendored.mkdir(parents=True)
    for name in ("a.js", "b.js", "c.js"):
        (vendored / name).write_text("", encoding="utf-8")
    assert detect.languages(detect.tracked_files(tmp_path)) == {"python": 2}


# -- saving ----------------------------------------------------------------------


def test_save_writes_the_config(tmp_path):
    path = tmp_path / CONFIG_FILE
    written, backup = wizard.save(cfg("python"), path)
    assert written == path and backup is None
    assert yaml.safe_load(path.read_text(encoding="utf-8"))["repos"]


def test_save_backs_up_what_was_there(tmp_path):
    path = tmp_path / CONFIG_FILE
    path.write_text("repos: []\n", encoding="utf-8")
    written, backup = wizard.save(cfg(), path)
    assert backup is not None and backup.exists()
    assert backup.read_text(encoding="utf-8") == "repos: []\n"
    assert written.read_text(encoding="utf-8") != "repos: []\n"


def test_config_path_is_the_only_name_pre_commit_reads(tmp_path, monkeypatch):
    make_repo(tmp_path, {"a.py": ""})
    monkeypatch.chdir(tmp_path)
    assert wizard.config_path().name == CONFIG_FILE


# -- the configurator registry ---------------------------------------------------


def test_pre_commit_is_registered_under_its_catalog_key():
    spec = configure.get("pre-commit")
    assert spec is not None and spec.key == "pre-commit"
    assert configure.has("pre-commit")
    module = spec.load()
    assert callable(module.run) and callable(module.config_path)


# -- against the real binary -----------------------------------------------------

needs_pre_commit = pytest.mark.skipif(
    not validate.available(), reason="pre-commit is not installed"
)


@needs_pre_commit
@pytest.mark.parametrize("preset", sorted(PRESETS))
def test_every_preset_is_accepted_by_the_real_pre_commit(preset):
    """`validate-config` is offline and fast, so every preset is checked on every run.
    It verifies the file's *shape*; the hook ids are proved by `validate.resolve`,
    which needs the network and is a wizard action rather than a test."""
    report = validate.verify(cfg(preset, use_ci=True, exclude=r"^vendor/"))
    assert report is not None
    assert report.ok, [f"{c.name}: {c.detail}" for c in report.failures]


@needs_pre_commit
def test_a_stage_that_would_never_fire_is_reported():
    """The check that catches the quiet breakage: a commit-msg hook whose stage is
    missing from default_install_hook_types installs cleanly and never runs."""
    config = cfg(hooks=["trailing-whitespace", "commitizen"])
    with validate.sandbox(config) as root:
        assert root is not None
    report = validate.verify(config)
    assert report is not None and report.ok
    assert any("commit-msg" in check.name for check in report.checks)
