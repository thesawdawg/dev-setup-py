"""Integration tests: the generated commitizen config, run past the real `cz`.

Unlike the rest of `tests/integration/`, these need neither sudo nor the network —
only `cz` and `git` on PATH. They are marked `integration` because each one builds a
throwaway git repo and shells out to commitizen several times (~3s per config),
which is too slow for the default loop:

    pytest tests/integration/test_commitizen_config.py -m integration -v

They are the check that the wizard's tables still describe what commitizen does. A
failure here after a commitizen upgrade means the emitter needs updating, not that
the test is flaky.
"""

from __future__ import annotations

import pytest

from dev_setup.configure.commitizen import render, validate
from dev_setup.configure.commitizen.model import (
    BREAKING_SECTION,
    SAMPLE_BUMPS,
    SAMPLE_VERSION,
    ChangeType,
    CommitizenConfig,
)

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _needs_cz():
    if not validate.available():
        pytest.skip("commitizen is not installed — `devstuff install commitizen`")


def report_for(config: CommitizenConfig) -> validate.Report:
    report = validate.verify(config)
    assert report is not None
    return report


CONFIGS = {
    "conventional": CommitizenConfig(convention="conventional"),
    "custom-defaults": CommitizenConfig(convention="custom"),
    "major-version-zero": CommitizenConfig(convention="custom", major_version_zero=True),
    "pep440-bare-tags": CommitizenConfig(
        convention="custom", version_scheme="pep440", tag_format="$version"
    ),
    "no-scope-no-body": CommitizenConfig(
        convention="custom", ask_scope=False, ask_body=False, ask_footer=False
    ),
    "two-types": CommitizenConfig(convention="custom", types=["feat", "fix"]),
    "renamed-sections": CommitizenConfig(
        convention="custom",
        types=["feat", "fix", "docs"],
        bumps={"docs": "PATCH"},
        sections={"feat": "What's New", "docs": "Docs"},
        in_changelog={"docs": True},
    ),
}


@pytest.mark.parametrize("name", list(CONFIGS), ids=list(CONFIGS))
def test_cz_agrees_with_the_generated_rules(name):
    """Every bump the wizard's table promises is replayed through `cz bump --dry-run`
    in a sandbox repo. This is the guarantee behind the review screen."""
    report = report_for(CONFIGS[name])
    assert report.ok, "\n".join(f"{c.name}: {c.detail}" for c in report.failures)


def test_a_user_added_type_bumps_the_way_it_was_declared():
    config = CommitizenConfig(
        convention="custom",
        types=["feat", "fix", "deploy"],
        extra_types=[ChangeType(
            key="deploy",
            section="Deployments",
            description="a production deployment",
            bump="MINOR",
            changelog=True,
            builtin=False,
        )],
    )
    report = report_for(config)
    assert report.ok, "\n".join(f"{c.name}: {c.detail}" for c in report.failures)

    with validate.sandbox(config) as root:
        assert root is not None
        tag = validate.tag_for(validate._preview_config(config), SAMPLE_VERSION)
        actual, output = validate._bump(root, "deploy: ship it", tag)
        assert actual == SAMPLE_BUMPS["MINOR"], output


def test_the_changelog_uses_the_headings_that_were_configured():
    config = CONFIGS["renamed-sections"]
    text = validate.changelog_preview(config)
    assert text is not None
    for heading, _ in render.changelog_rows(config):
        if heading == BREAKING_SECTION:
            # Only a `BREAKING CHANGE:` footer files a commit here, and the preview's
            # sample commits use the `!` form, which stays under its own type.
            continue
        assert f"### {heading}" in text, text


def test_a_type_left_out_of_the_changelog_stays_out():
    config = CommitizenConfig(convention="custom")
    text = validate.changelog_preview(config)
    assert text is not None
    for change_type in render.unchangelogged(config):
        assert change_type.description.lower() not in text.lower()


def test_cz_check_rejects_a_message_the_schema_does_not_allow():
    """The negative half of the message checks: `cz check` has to actually be
    enforcing the generated `schema_pattern`, not passing everything."""
    config = CommitizenConfig(convention="custom", types=["feat", "fix"])
    with validate.sandbox(config) as root:
        assert root is not None
        result = validate._run(
            ["cz", "--config", ".cz.toml", "check", "-m", "wibble: not a real type"], cwd=root
        )
        assert result is not None and result.returncode != 0


def test_the_config_commitizen_reads_is_the_one_the_wizard_wrote():
    """`cz info` prints `customize.info`, so it round-trips the file through
    commitizen's own parser rather than ours."""
    config = CommitizenConfig(convention="custom", types=["feat", "fix"])
    with validate.sandbox(config) as root:
        assert root is not None
        result = validate._run(["cz", "--config", ".cz.toml", "info"], cwd=root)
        assert result is not None and result.returncode == 0
        assert "feat" in result.stdout and "MINOR" in result.stdout
