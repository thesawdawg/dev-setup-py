from __future__ import annotations

import re
import tomllib

import pytest

from dev_setup import configure
from dev_setup.configure.commitizen import detect, render, validate, wizard
from dev_setup.configure.commitizen.model import (
    ALWAYS_ACCEPTED,
    BREAKING_SECTION,
    BUMP_LEVELS,
    CONFIG_FILES,
    CONVENTIONS,
    MAJOR,
    MINOR,
    NONE,
    PATCH,
    SAMPLE_BUMPS,
    SAMPLE_VERSION,
    TAG_FORMATS,
    TYPES,
    TYPES_BY_KEY,
    VERSION_PROVIDERS,
    VERSION_SCHEMES,
    ChangeType,
    CommitizenConfig,
)

# What `commitizen.defaults` and `ConventionalCommitsCz` actually ship, read out of
# the installed package. The wizard's tables claim to reproduce these until the user
# changes something; if commitizen ever moves, this is the test that says so.
CZ_BUMP_MAP = {"feat": MINOR, "fix": PATCH, "refactor": PATCH, "perf": PATCH}
CZ_CHANGELOG_TYPES = {"feat", "fix", "refactor", "perf"}
CZ_PICKER_TYPES = {"fix", "feat", "docs", "style", "refactor", "perf", "test", "build", "ci"}


def cfg(**kwargs) -> CommitizenConfig:
    kwargs.setdefault("convention", "custom")
    return CommitizenConfig(**kwargs)


def settings(config: CommitizenConfig) -> dict:
    return tomllib.loads(render.to_toml(config))["tool"]["commitizen"]


# -- the model's own invariants --------------------------------------------------


def test_type_keys_are_regex_safe():
    """Every key is spliced raw into four generated regexes. A key needing an escape
    would silently change what those patterns match."""
    for change_type in TYPES:
        assert wizard.TYPE_KEY_RE.match(change_type.key), change_type.key
        assert re.escape(change_type.key) == change_type.key, change_type.key


def test_every_type_is_complete():
    for change_type in TYPES:
        assert change_type.section, change_type.key
        assert change_type.description, change_type.key
        assert change_type.bump in BUMP_LEVELS, change_type.key
        if change_type.shortcut:
            assert re.match(r"^[a-z0-9]$", change_type.shortcut), change_type.key


def test_builtin_shortcuts_are_unique():
    shortcuts = [t.shortcut for t in TYPES if t.shortcut]
    assert len(shortcuts) == len(set(shortcuts))


def test_defaults_reproduce_commitizens_own_rules():
    """The two conventions have to agree out of the box: "custom, starting from the
    conventional set" is the wizard's whole premise."""
    config = cfg()
    assert {t.key for t in config.selected()} == CZ_PICKER_TYPES
    assert {t.key: t.bump for t in config.selected() if t.bump} == CZ_BUMP_MAP
    assert {t.key for t in config.changelog_types()} == CZ_CHANGELOG_TYPES


def test_sample_bumps_are_a_real_semver_walk():
    major, minor, patch = SAMPLE_VERSION.split(".")
    assert SAMPLE_BUMPS[MAJOR] == f"{int(major) + 1}.0.0"
    assert SAMPLE_BUMPS[MINOR] == f"{major}.{int(minor) + 1}.0"
    assert SAMPLE_BUMPS[PATCH] == f"{major}.{minor}.{int(patch) + 1}"
    assert SAMPLE_BUMPS[NONE] == SAMPLE_VERSION


def test_config_file_order_matches_commitizens():
    """The precedence warning is only correct if this list is."""
    from commitizen import defaults

    assert CONFIG_FILES == defaults.CONFIG_FILES


def test_duplicate_shortcuts_are_reported():
    config = cfg(types=["feat", "fix"], extra_types=[
        ChangeType(key="frob", section="Frobs", description="frobs", shortcut="f", builtin=False),
    ])
    config.types.append("frob")
    assert config.duplicate_shortcuts() == {"f": ["feat", "frob"]}


def test_overrides_apply_only_to_the_customizable_convention():
    """`cz_conventional_commits` runs commitizen's rules whatever the user picked,
    so reporting an override would make the preview describe a config that cannot
    happen."""
    overridden = cfg(bumps={"docs": MAJOR})
    assert overridden.bump_of("docs") == MAJOR

    conventional = CommitizenConfig(convention="conventional", bumps={"docs": MAJOR})
    assert conventional.bump_of("docs") == NONE
    assert {t.key for t in conventional.selected()} == CZ_PICKER_TYPES


def test_selected_drops_unknown_keys_and_keeps_table_order():
    config = cfg(types=["ci", "feat", "nope", "fix"])
    assert [t.key for t in config.selected()] == ["feat", "fix", "ci"]


def test_extra_types_come_after_the_builtins():
    extra = ChangeType(key="deploy", section="Deploys", description="d", builtin=False)
    config = cfg(types=["feat", "deploy"], extra_types=[extra])
    assert [t.key for t in config.selected()] == ["feat", "deploy"]


# -- TOML validity ---------------------------------------------------------------


@pytest.mark.parametrize("convention", list(CONVENTIONS))
@pytest.mark.parametrize("provider", list(VERSION_PROVIDERS))
def test_generated_config_is_valid_toml(convention, provider):
    config = CommitizenConfig(convention=convention, version_provider=provider)
    data = tomllib.loads(render.to_toml(config))
    assert data["tool"]["commitizen"]["name"] == CONVENTIONS[convention].name


@pytest.mark.parametrize("scheme", list(VERSION_SCHEMES))
@pytest.mark.parametrize("tag_format", list(TAG_FORMATS))
def test_scheme_and_tag_format_round_trip(scheme, tag_format):
    data = settings(cfg(version_scheme=scheme, tag_format=tag_format))
    assert data["version_scheme"] == scheme
    assert data["tag_format"] == tag_format


def test_version_is_only_written_for_the_provider_that_needs_it():
    """Any other provider reads the version from a real file; a copy here would be
    a second source of truth that goes stale on the first hand-edit."""
    assert settings(cfg(version_provider="commitizen"))["version"] == "0.1.0"
    assert "version" not in settings(cfg(version_provider="pep621"))


def test_conventional_emits_no_customize_block():
    data = settings(CommitizenConfig(convention="conventional"))
    assert "customize" not in data
    assert data["name"] == "cz_conventional_commits"


def test_regexes_survive_as_written():
    """The emitter uses TOML *literal* strings, so `\\(.+\\)` must come back out of
    the parser with its backslashes intact rather than escape-processed away."""
    data = settings(cfg())["customize"]
    assert data["bump_pattern"] == render.bump_pattern(cfg())
    assert re.compile(data["bump_pattern"])
    assert re.compile(data["schema_pattern"])
    assert re.compile(data["commit_parser"])


def test_a_quote_in_a_section_name_does_not_break_the_file():
    config = cfg(types=["feat"], sections={"feat": "Bill's changes"})
    data = settings(config)["customize"]
    assert data["change_type_map"]["feat"] == "Bill's changes"


# -- the generated rules ---------------------------------------------------------


def test_bump_map_leads_with_the_breaking_rules():
    """commitizen walks the map and stops at the first `re.match`, so a `feat!` read
    as a plain MINOR is exactly what the wrong order produces."""
    keys = list(render.bump_map(cfg()))
    assert keys[:2] == [render.BREAKING_BANG, render.BREAKING_FOOTER]
    assert all(value in (MAJOR, MINOR, PATCH) for value in render.bump_map(cfg()).values())


def test_major_version_zero_demotes_only_the_breaking_rules():
    normal = render.bump_map(cfg())
    zeroed = render.bump_map(cfg(), major_version_zero=True)
    assert zeroed[render.BREAKING_BANG] == MINOR
    assert zeroed[render.BREAKING_FOOTER] == MINOR
    for key in normal:
        if key not in (render.BREAKING_BANG, render.BREAKING_FOOTER):
            assert zeroed[key] == normal[key]


def test_non_bumping_types_get_no_bump_map_entry_but_stay_in_the_pattern():
    config = cfg()
    assert "^docs" not in render.bump_map(config)
    assert re.search(render.bump_pattern(config), "docs: tweak the readme")


def test_bump_pattern_group_one_is_what_bump_map_matches():
    """The contract with commitizen: group 1 of `bump_pattern` is the string every
    `bump_map` key is `re.match`ed against."""
    config = cfg()
    pattern = re.compile(render.bump_pattern(config))
    mapping = render.bump_map(config)

    def increment(message: str) -> str:
        found = pattern.search(message).group(1)
        for key, value in mapping.items():
            if re.match(key, found):
                return value
        return NONE

    assert increment("feat: a thing") == MINOR
    assert increment("feat(api): a thing") == MINOR
    assert increment("fix: a thing") == PATCH
    assert increment("docs: a thing") == NONE
    assert increment("feat(api)!: a thing") == MAJOR
    assert increment("docs!: a thing") == MAJOR
    assert increment("BREAKING CHANGE: it moved") == MAJOR
    assert increment("BREAKING-CHANGE: it moved") == MAJOR


def test_bump_rows_agree_with_the_emitted_map():
    """The table the user reads and the map commitizen executes are generated
    separately; nothing but this stops them drifting."""
    config = cfg()
    pattern = re.compile(render.bump_pattern(config))
    mapping = render.bump_map(config)
    for change_type in config.selected():
        found = pattern.search(f"{change_type.key}: x").group(1)
        matched = next((v for k, v in mapping.items() if re.match(k, found)), NONE)
        assert matched == change_type.bump, change_type.key
        assert SAMPLE_BUMPS[matched] == dict(
            (prefix, version) for prefix, _, version in render.bump_rows(config)
        )[f"{change_type.key}: …"]


@pytest.mark.parametrize("breaking", [False, True])
def test_schema_pattern_accepts_the_wizards_own_messages(breaking):
    """`message_template` and `schema_pattern` are generated independently — a
    template producing a message `cz check` rejects is the failure this catches."""
    config = cfg()
    pattern = re.compile(render.schema_pattern(config))
    assert pattern.match(render.sample_message(config, breaking=breaking))
    assert pattern.match(render.example(config))


def test_schema_pattern_always_accepts_commitizens_own_bump_commit():
    """`cz bump` writes `bump: version …`. If the schema rejects it, `cz check` over
    a range containing a release fails on commitizen's own commit."""
    pattern = re.compile(render.schema_pattern(cfg()))
    for key in ALWAYS_ACCEPTED:
        assert pattern.match(f"{key}: version 1.0.0 → 1.1.0")


def test_schema_pattern_rejects_an_unknown_type():
    pattern = re.compile(render.schema_pattern(cfg(types=["feat", "fix"])))
    assert not pattern.match("wibble: a thing")
    assert not pattern.match("a thing with no type")


def test_commit_parser_exposes_the_groups_the_changelog_needs():
    parser = re.compile(render.commit_parser(cfg()))
    match = parser.match("feat(api)!: add rate limiting")
    assert match.group("change_type") == "feat"
    assert match.group("scope") == "api"
    assert match.group("breaking") == "!"
    assert match.group("message") == "add rate limiting"
    assert parser.match("BREAKING CHANGE: the api moved").group("change_type") == "BREAKING CHANGE"


def test_commit_parser_keeps_a_breaking_change_on_an_unlisted_type():
    """`docs` has no changelog section, but `docs!:` is still a breaking release and
    has to reach the notes somehow — the `\\w+!` alternative is what does it."""
    parser = re.compile(render.commit_parser(cfg()))
    assert parser.match("docs!: the config format changed")
    assert not parser.match("docs: tweak the readme")


def test_changelog_pattern_tracks_bump_pattern():
    config = cfg(types=["feat", "fix", "docs"])
    assert render.changelog_pattern(config) == render.bump_pattern(config)


def test_change_type_order_leads_with_breaking_and_has_no_duplicates():
    config = cfg(types=["feat", "fix"], sections={"fix": "Features"})
    order = render.change_type_order(config)
    assert order[0] == BREAKING_SECTION
    assert len(order) == len(set(order))


def test_change_type_map_holds_only_changelog_types():
    config = cfg()
    mapping = render.change_type_map(config)
    assert set(mapping) == CZ_CHANGELOG_TYPES
    assert all(t.key not in mapping for t in render.unchangelogged(config))


def test_questions_mirror_the_selected_types():
    config = cfg(types=["feat", "fix", "chore"])
    questions = {q["name"]: q for q in render.questions(config)}
    assert [c["value"] for c in questions["change_type"]["choices"]] == ["feat", "fix", "chore"]
    assert all("key" in c for c in questions["change_type"]["choices"])


@pytest.mark.parametrize(("field", "name"), [
    ("ask_scope", "scope"), ("ask_body", "body"), ("ask_footer", "footer"),
])
def test_optional_questions_can_be_switched_off(field, name):
    on = {q["name"] for q in render.questions(cfg(**{field: True}))}
    off = {q["name"] for q in render.questions(cfg(**{field: False}))}
    assert name in on and name not in off
    # The template reads the answers, so dropping a question has to drop its slot too.
    assert f"{{{{{name}}}}}" not in render.message_template(cfg(**{field: False}))


def test_a_type_with_no_shortcut_emits_no_key():
    extra = ChangeType(key="deploy", section="Deploys", description="d", builtin=False)
    config = cfg(types=["deploy"], extra_types=[extra])
    choices = render.questions(config)[0]["choices"]
    assert choices == [{"value": "deploy", "name": "deploy: d"}]


def test_added_types_reach_every_generated_rule():
    extra = ChangeType(
        key="deps", section="Dependencies", description="upgrades",
        bump=PATCH, changelog=True, builtin=False,
    )
    config = cfg(types=["feat", "deps"], extra_types=[extra])
    assert "^deps" in render.bump_map(config)
    assert re.match(render.schema_pattern(config), "deps: bump click")
    assert render.change_type_map(config)["deps"] == "Dependencies"
    assert "Dependencies" in render.change_type_order(config)


def test_changelog_rows_cover_every_section_once():
    config = cfg()
    rows = dict(render.changelog_rows(config))
    assert list(rows) == render.change_type_order(config)
    assert rows[BREAKING_SECTION]  # never empty: it explains what lands there


def test_empty_selection_still_renders():
    """The wizard refuses an empty selection, but nothing stops a hand-built config
    reaching the emitter — it must not raise on the way to the review screen."""
    config = cfg(types=[])
    assert tomllib.loads(render.to_toml(config))
    assert render.sample_message(config).startswith("feat")


# -- splicing into pyproject.toml -------------------------------------------------

PYPROJECT = """\
[project]
name = "demo"
version = "1.4.2"

[tool.ruff]
line-length = 110

[tool.pytest.ini_options]
addopts = "-q"
"""

WITH_CZ = """\
[project]
name = "demo"
version = "1.4.2"

# an old comment about commitizen
[tool.commitizen]
name = "cz_conventional_commits"
version_provider = "pep621"

[tool.commitizen.customize]
example = "old"

[tool.pytest.ini_options]
addopts = "-q"
"""


def spliced(text: str, config: CommitizenConfig | None = None) -> str:
    out = render.splice_pyproject(text, config or cfg())
    assert out is not None
    return out


def test_splice_appends_when_there_is_no_section():
    out = spliced(PYPROJECT)
    assert out.startswith(PYPROJECT.rstrip("\n"))
    assert tomllib.loads(out)["tool"]["commitizen"]["name"] == "cz_customize"


def test_splice_replaces_every_commitizen_table_and_keeps_the_rest():
    out = spliced(WITH_CZ)
    data = tomllib.loads(out)
    assert data["tool"]["commitizen"]["customize"]["example"] != "old"
    assert data["tool"]["pytest"]["ini_options"]["addopts"] == "-q"
    assert data["project"]["version"] == "1.4.2"
    assert "an old comment about commitizen" not in out


def test_splice_is_idempotent():
    once = spliced(WITH_CZ)
    assert render.splice_pyproject(once, cfg()) == once


def test_splice_leaves_other_tools_byte_for_byte():
    out = spliced(PYPROJECT)
    assert "[tool.ruff]\nline-length = 110" in out
    assert "[tool.pytest.ini_options]\naddopts = \"-q\"" in out


def test_splice_refuses_a_file_it_cannot_verify():
    """Fail closed: the splice is line-based, so a table header hidden inside a
    multi-line string is exactly the shape it gets wrong. The caller falls back to
    a standalone .cz.toml rather than damaging the project's own file."""
    hostile = '[project]\nname = "x"\ndescription = """\n[tool.commitizen]\nname = "no"\n"""\n'
    assert render.splice_pyproject(hostile, cfg()) is None


def test_splice_rejects_output_that_would_not_parse():
    assert render.splice_pyproject("this is not toml = = =", cfg()) is None


def test_a_similarly_named_table_is_not_mistaken_for_ours():
    text = '[tool.commitizen_helper]\nx = 1\n'
    out = spliced(text)
    assert tomllib.loads(out)["tool"]["commitizen_helper"]["x"] == 1


# -- detection --------------------------------------------------------------------


def test_pyproject_without_a_commitizen_section_is_not_a_config(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n')
    assert detect.existing_configs(tmp_path) == []


def test_existing_configs_follow_commitizens_search_order(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[tool.commitizen]\nname = "cz_conventional_commits"\n')
    (tmp_path / ".cz.toml").write_text('[tool.commitizen]\nname = "cz_customize"\n')
    found = [p.name for p in detect.existing_configs(tmp_path)]
    assert found == [".cz.toml", "pyproject.toml"]


def test_a_malformed_config_is_simply_not_a_config(tmp_path):
    (tmp_path / ".cz.toml").write_text("[[[ nope")
    assert detect.existing_configs(tmp_path) == []


@pytest.mark.parametrize(("filename", "text", "expected"), [
    (".cz.json", '{"commitizen": {"name": "cz_customize"}}', True),
    (".cz.json", '{"other": 1}', False),
    (".cz.yaml", "commitizen:\n  name: cz_customize\n", True),
    (".cz.yaml", "other: 1\n", False),
])
def test_non_toml_configs_are_recognised(tmp_path, filename, text, expected):
    (tmp_path / filename).write_text(text)
    assert bool(detect.existing_configs(tmp_path)) is expected


@pytest.mark.parametrize(("files", "provider", "version"), [
    ({"pyproject.toml": '[project]\nversion = "2.0.1"\n'}, "pep621", "2.0.1"),
    ({"pyproject.toml": '[project]\nversion = "2.0.1"\n', "uv.lock": ""}, "uv", "2.0.1"),
    ({"pyproject.toml": '[tool.poetry]\nversion = "3.1.0"\n'}, "poetry", "3.1.0"),
    ({"package.json": '{"version": "4.2.0"}'}, "npm", "4.2.0"),
    ({"Cargo.toml": '[package]\nversion = "0.9.0"\n'}, "cargo", "0.9.0"),
    ({"composer.json": '{"version": "1.1.1"}'}, "composer", "1.1.1"),
    ({}, "commitizen", "0.1.0"),
])
def test_the_version_provider_is_read_off_the_project(tmp_path, files, provider, version):
    for name, text in files.items():
        (tmp_path / name).write_text(text)
    project = detect.inspect(tmp_path)
    assert project.provider == provider
    assert project.version == version


def test_suggestions_follow_the_detected_project(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "0.4.0"\n')
    (tmp_path / "CHANGELOG.md").write_text("# changes\n")
    suggested = detect.suggest(detect.inspect(tmp_path))
    assert suggested.version_provider == "pep621"
    # PyPI will not accept anything else, so the scheme follows the provider.
    assert suggested.version_scheme == "pep440"
    assert suggested.target == "pyproject.toml"
    assert suggested.changelog_file == "CHANGELOG.md"
    # 0.x: a breaking change should not be allowed to declare 1.0 by accident.
    assert suggested.major_version_zero is True


def test_a_non_python_project_is_suggested_a_standalone_file(tmp_path):
    (tmp_path / "package.json").write_text('{"version": "4.2.0"}')
    suggested = detect.suggest(detect.inspect(tmp_path))
    assert suggested.target == ".cz.toml"
    assert suggested.version_scheme == "semver"


def test_read_existing_finds_both_toml_spellings(tmp_path):
    (tmp_path / "a.toml").write_text('[tool.commitizen]\nname = "x"\n')
    (tmp_path / "b.toml").write_text('[commitizen]\nname = "y"\n')
    assert detect.read_existing(tmp_path / "a.toml")["name"] == "x"
    assert detect.read_existing(tmp_path / "b.toml")["name"] == "y"
    assert detect.read_existing(tmp_path / "missing.toml") == {}


# -- saving ------------------------------------------------------------------------


def test_save_writes_a_standalone_config(tmp_path):
    written, backup = wizard.save(cfg(), tmp_path / ".cz.toml")
    assert backup is None
    assert tomllib.loads(written.read_text())["tool"]["commitizen"]["name"] == "cz_customize"
    assert wizard._looks_generated(written)


def test_save_backs_up_what_was_there(tmp_path):
    path = tmp_path / ".cz.toml"
    path.write_text("# hand written\n")
    written, backup = wizard.save(cfg(), path)
    assert backup is not None and backup.read_text() == "# hand written\n"
    assert written == path


def test_save_splices_an_existing_pyproject(tmp_path):
    path = tmp_path / "pyproject.toml"
    path.write_text(PYPROJECT)
    written, backup = wizard.save(cfg(target="pyproject.toml"), path)
    assert written == path
    assert backup is not None
    data = tomllib.loads(path.read_text())
    assert data["tool"]["commitizen"]["name"] == "cz_customize"
    assert data["tool"]["ruff"]["line-length"] == 110
    assert wizard._looks_generated(path)


def test_save_falls_back_to_cz_toml_when_the_splice_is_unsafe(tmp_path, monkeypatch):
    path = tmp_path / "pyproject.toml"
    path.write_text(PYPROJECT)
    monkeypatch.setattr(render, "splice_pyproject", lambda *_: None)
    written, _ = wizard.save(cfg(target="pyproject.toml"), path)
    assert written.name == ".cz.toml"
    # The file that could not be edited safely is left exactly as it was.
    assert path.read_text() == PYPROJECT


def test_a_hand_written_config_is_not_mistaken_for_ours(tmp_path):
    path = tmp_path / ".cz.toml"
    path.write_text('[tool.commitizen]\nname = "cz_conventional_commits"\n')
    assert not wizard._looks_generated(path)


# -- the live check (offline parts) -------------------------------------------------


@pytest.mark.parametrize(("tag_format", "expected"), [
    ("v$version", "v1.4.2"),
    ("$version", "1.4.2"),
    ("v$major.$minor.$patch", "v1.4.2"),
    ("release-${version}", "release-1.4.2"),
])
def test_tag_for_renders_every_placeholder(tag_format, expected):
    assert validate.tag_for(cfg(tag_format=tag_format), SAMPLE_VERSION) == expected


def test_a_report_is_ok_only_when_every_check_is():
    ok = validate.Report("4.0.0", [validate.Check("a", True, "")])
    bad = validate.Report("4.0.0", [validate.Check("a", True, ""), validate.Check("b", False, "")])
    assert ok.ok and not ok.failures
    assert not bad.ok and [c.name for c in bad.failures] == ["b"]


def test_the_preview_config_only_moves_the_version_source():
    original = cfg(version_provider="pep621", version_files=["src/x/__init__.py"], tag_format="r$version")
    preview = validate._preview_config(original)
    assert preview.version_provider == "commitizen"
    assert preview.version == SAMPLE_VERSION
    assert preview.version_files == []
    # Everything actually under test is carried through untouched.
    assert preview.tag_format == original.tag_format
    assert render.bump_map(preview) == render.bump_map(original)
    assert render.schema_pattern(preview) == render.schema_pattern(original)


# -- the interactive steps ----------------------------------------------------------


class Answers:
    """Scripted replies for the wizard's prompts, so a step can be exercised without
    a terminal. Each `ui.*` call takes the next value of its kind."""

    def __init__(self, monkeypatch, *, text=(), confirm=(), select=(), checkbox=()):
        self.text, self.confirm = list(text), list(confirm)
        self.select, self.checkbox = list(select), list(checkbox)
        self.warnings: list[str] = []
        self.errors: list[str] = []
        monkeypatch.setattr(wizard.ui, "text_input", self._text)
        monkeypatch.setattr(wizard.ui, "confirm", self._confirm)
        monkeypatch.setattr(wizard.ui, "select", self._select)
        monkeypatch.setattr(wizard.ui, "checkbox", self._checkbox)
        monkeypatch.setattr(wizard.ui, "warn", self.warnings.append)
        monkeypatch.setattr(wizard.ui, "error", self.errors.append)
        monkeypatch.setattr(wizard.ui, "success", lambda *_: None)

    def _text(self, _prompt, default="", required=False):
        return self.text.pop(0) if self.text else default

    def _confirm(self, _prompt, default=False):
        return self.confirm.pop(0) if self.confirm else default

    def _select(self, _prompt, choices, default=None):
        return self.select.pop(0) if self.select else getattr(default, "value", default)

    def _checkbox(self, _prompt, choices, **_kwargs):
        return self.checkbox.pop(0) if self.checkbox else [c.value for c in choices if c.checked]


def test_adding_a_type_records_it_everywhere(monkeypatch):
    config = cfg(types=["feat"])
    Answers(
        monkeypatch,
        text=["deploy", "a production deployment", "Deployments", "y"],
        confirm=[True],
        select=[PATCH],
    )
    wizard._ask_add_type(config)

    assert "deploy" in config.types
    added = config.catalog()["deploy"]
    assert (added.bump, added.section, added.shortcut, added.changelog) == (
        PATCH, "Deployments", "y", True,
    )
    assert "^deploy" in render.bump_map(config)


@pytest.mark.parametrize("key", ["Deps", "9lives", "with space", "de_ps", ""])
def test_a_type_key_the_regexes_could_not_carry_is_refused(monkeypatch, key):
    config = cfg(types=["feat"])
    answers = Answers(monkeypatch, text=[key])
    wizard._ask_add_type(config)
    assert config.types == ["feat"]
    assert config.extra_types == []
    # An empty answer is "never mind", not a mistake worth an error.
    assert bool(answers.errors) is bool(key)


def test_adding_a_type_that_already_exists_is_refused(monkeypatch):
    config = cfg(types=["feat"])
    answers = Answers(monkeypatch, text=["feat"])
    wizard._ask_add_type(config)
    assert config.extra_types == []
    assert answers.warnings


def test_a_bad_shortcut_is_dropped_rather_than_emitted(monkeypatch):
    config = cfg(types=["feat"])
    answers = Answers(
        monkeypatch, text=["deploy", "ships it", "Deployments", "EE"], confirm=[True], select=[PATCH]
    )
    wizard._ask_add_type(config)
    assert config.catalog()["deploy"].shortcut == ""
    assert answers.warnings


def test_bump_levels_are_recorded_as_overrides(monkeypatch):
    config = cfg(types=["feat", "docs"])
    Answers(monkeypatch, select=[MINOR, MAJOR], confirm=[True, True], text=["Features", "Docs"])
    wizard._ask_bump_levels(config)
    assert config.bumps == {"feat": MINOR, "docs": MAJOR}
    assert config.sections["docs"] == "Docs"
    assert {t.key for t in config.changelog_types()} == {"feat", "docs"}


def test_deselecting_everything_keeps_the_previous_types(monkeypatch):
    """An empty alternation matches everything, so "none" cannot mean none."""
    config = cfg(types=["feat", "fix"])
    answers = Answers(monkeypatch, checkbox=[[]])
    assert wizard._ask_types(config) == ["feat", "fix"]
    assert answers.warnings


def test_release_toggles_round_trip(monkeypatch):
    config = cfg()
    Answers(monkeypatch, checkbox=[["gpg_sign", "annotated_tag"]], text=["docs/CHANGES.md", ""])
    wizard._ask_bump_options(config)
    assert config.gpg_sign and config.annotated_tag
    assert not config.update_changelog_on_bump and not config.use_shortcuts
    assert config.changelog_file == "docs/CHANGES.md"


def test_the_commit_hook_is_written_executable(tmp_path, monkeypatch):
    hooks = tmp_path / ".git" / "hooks"
    hooks.mkdir(parents=True)
    Answers(monkeypatch, confirm=[True])
    wizard._offer_commit_hook(tmp_path)
    hook = hooks / "commit-msg"
    assert "cz check" in hook.read_text()
    assert hook.stat().st_mode & 0o111


def test_an_existing_commit_hook_is_never_touched(tmp_path, monkeypatch):
    hooks = tmp_path / ".git" / "hooks"
    hooks.mkdir(parents=True)
    (hooks / "commit-msg").write_text("#!/bin/sh\necho mine\n")
    Answers(monkeypatch, confirm=[True])
    wizard._offer_commit_hook(tmp_path)
    assert (hooks / "commit-msg").read_text() == "#!/bin/sh\necho mine\n"


def test_shadowing_another_config_is_called_out(tmp_path, monkeypatch):
    (tmp_path / "pyproject.toml").write_text('[tool.commitizen]\nname = "cz_conventional_commits"\n')
    (tmp_path / ".cz.toml").write_text('[tool.commitizen]\nname = "cz_customize"\n')
    answers = Answers(monkeypatch, confirm=[])
    messages: list[str] = []
    monkeypatch.setattr(wizard.ui, "dim", messages.append)
    wizard._warn_about_shadowing(detect.inspect(tmp_path), tmp_path / ".cz.toml")
    assert answers.warnings
    # The winner is the first in commitizen's search order, not the one just written.
    assert any(".cz.toml" in m for m in messages if "reads only" in m)


def test_no_warning_when_the_written_file_is_the_only_one(tmp_path, monkeypatch):
    (tmp_path / ".cz.toml").write_text('[tool.commitizen]\nname = "cz_customize"\n')
    answers = Answers(monkeypatch)
    wizard._warn_about_shadowing(detect.inspect(tmp_path), tmp_path / ".cz.toml")
    assert not answers.warnings


# -- registration -------------------------------------------------------------------


def test_the_configurator_is_registered_and_loads():
    spec = configure.get("commitizen")
    assert spec is not None and configure.has("commitizen")
    module = spec.load()
    assert callable(module.run)
    assert callable(module.config_path)


def test_config_path_points_at_the_file_commitizen_would_read(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(detect, "git_root", lambda *_, **__: tmp_path)
    assert wizard.config_path() == tmp_path / ".cz.toml"

    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n')
    assert wizard.config_path() == tmp_path / "pyproject.toml"

    (tmp_path / ".cz.toml").write_text('[tool.commitizen]\nname = "cz_customize"\n')
    assert wizard.config_path() == tmp_path / ".cz.toml"


def test_every_builtin_type_key_is_unique():
    assert len(TYPES_BY_KEY) == len(TYPES)
