"""Bundled profiles (docs/specs/profile-apply, FR-20, FR-21, OQ-6; development-plan A5).

A bundle is a profile that ships inside the package, so the loader is the ordinary one. What is
new, and tested here, is the *name* lookup and the two guards on the shipped data: every key a
bundle names must exist in the bundled catalog, and every file in the directory must be reachable
by name — the same two-direction enforcement the CI matrix has, so a stale or forgotten bundle
fails the suite and not a user's machine.
"""

from __future__ import annotations

import re
from importlib import resources
from pathlib import Path

import pytest

from dev_setup import bundles, catalog, profile
from dev_setup.profile import ProfileError

# The bundles agreed with the maintainer for the first release (development-plan A5). Pinned so
# that dropping or renaming one is a deliberate edit to this list, not an accident.
SHIPPED = {"essentials", "python", "devops", "languages", "ai-agents", "ai-local"}


def shipped_files() -> list[Path]:
    return sorted(Path(str(resources.files("dev_setup") / "bundles")).iterdir())


class Backwards:
    """A directory that lists its entries in reverse name order, so that "sorted" is a property of
    the code under test and not a coincidence of how the filesystem happens to iterate."""

    def __init__(self, path: Path):
        self.path = path

    def iterdir(self):
        return iter(sorted(self.path.iterdir(), key=lambda p: p.name, reverse=True))


@pytest.fixture
def fake_root(tmp_path, monkeypatch):
    """Point the lookup at a scratch directory so lookup rules can be tested on arbitrary data."""
    monkeypatch.setattr(bundles, "_root", lambda: Backwards(tmp_path))
    return tmp_path


def put(root: Path, name: str, body: str = "version: 1\ntools:\n  gh: {}\n") -> None:
    (root / f"{name}.yaml").write_text(body)


# -- the shipped data (OQ-6) -----------------------------------------------------------------------


def test_the_agreed_bundles_are_the_ones_shipped():
    assert set(bundles.names()) == SHIPPED


@pytest.mark.parametrize("name", sorted(SHIPPED))
def test_every_shipped_bundle_loads_cleanly_and_is_not_empty(name):
    loaded = bundles.resolve(f"@{name}")

    assert loaded.tools, f"@{name} lists nothing"


@pytest.mark.parametrize("name", sorted(SHIPPED))
def test_every_key_a_bundle_names_exists_in_the_bundled_catalog(name):
    known = set(catalog.load_bundled_catalog())

    stale = sorted(set(bundles.resolve(f"@{name}").tools) - known)

    assert stale == [], f"@{name} names tools the catalog does not have: {stale}"


@pytest.mark.parametrize("name", sorted(SHIPPED))
def test_a_bundle_pins_nothing(name):
    # A shipped version would go stale the day it is released; "the latest" is the honest default.
    pinned = {k: e.version for k, e in bundles.resolve(f"@{name}").tools.items() if e.version}

    assert pinned == {}, f"@{name} pins {pinned}"


def test_every_file_in_the_directory_is_a_reachable_bundle():
    # The reverse direction: a file dropped in and not found by name is a forgotten bundle.
    files = shipped_files()

    assert all(f.suffix == ".yaml" for f in files), [f.name for f in files]
    assert {f.stem for f in files} == set(bundles.names())


def test_every_bundle_name_is_a_valid_name():
    assert all(re.fullmatch(r"[a-z][a-z0-9-]*", n) for n in bundles.names())


# -- looking a name up (FR-20, FR-21) ---------------------------------------------------------------


def test_names_are_sorted_and_without_the_extension(fake_root):
    for n in ("zeta", "alpha", "mid"):
        put(fake_root, n)

    assert bundles.names() == ["alpha", "mid", "zeta"]


def test_files_that_are_not_bundles_are_not_listed(fake_root):
    put(fake_root, "real")
    (fake_root / "notes.txt").write_text("x")
    (fake_root / "plain").write_text("version: 1\ntools: {}\n")  # right content, no .yaml
    (fake_root / "other.yml").write_text("version: 1\ntools: {}\n")  # wrong extension
    for odd in ("Bad Name", "Upper", "9lives", ".hidden", "-dash"):
        (fake_root / f"{odd}.yaml").write_text("version: 1\ntools: {}\n")
    (fake_root / "sub").mkdir()
    (fake_root / "folder.yaml").mkdir()  # a directory with a bundle's name is not a bundle

    assert bundles.names() == ["real"]


def test_resolve_returns_the_profile_the_file_describes(fake_root):
    put(fake_root, "mine", "version: 1\ntools:\n  gh: {}\n  uv: {}\n")

    assert sorted(bundles.resolve("@mine").tools) == ["gh", "uv"]


def test_an_unknown_bundle_is_an_error_that_lists_the_ones_that_exist(fake_root):
    put(fake_root, "alpha")
    put(fake_root, "beta")

    put(fake_root, "gamma")

    with pytest.raises(ProfileError) as err:
        bundles.resolve("@nope")

    text = str(err.value)
    assert "@nope" in text and "@alpha, @beta, @gamma" in text  # in order, whatever the listing


def test_an_unknown_bundle_says_so_even_when_there_are_none(fake_root):
    with pytest.raises(ProfileError, match="@nope"):
        bundles.resolve("@nope")


@pytest.mark.parametrize(
    "arg",
    ["@", "@../x", "@a/b", "@Alpha", "@alpha.yaml", "@ alpha", "@..", "@/etc/passwd", "@@alpha",
     "alpha", "./alpha", "alpha.yaml"],
)
def test_a_name_that_is_not_a_bundle_name_never_touches_the_filesystem(fake_root, arg):
    # Lookup is by listing the directory, not by building a path from the argument, so there is
    # nothing to traverse. Even `@alpha.yaml` — a real file name — is not a bundle *name*.
    put(fake_root, "alpha")
    (fake_root.parent / "x.yaml").write_text("version: 1\ntools:\n  gh: {}\n")

    with pytest.raises(ProfileError, match="no bundle"):
        bundles.resolve(arg)


def test_a_broken_bundle_file_reports_its_own_name(fake_root):
    put(fake_root, "broken", "version: 2\ntools: {}\n")

    with pytest.raises(ProfileError, match="broken"):
        bundles.resolve("@broken")


@pytest.mark.parametrize(
    "arg, expected",
    [("@devops", True), ("devops", False), ("./@devops", False), ("", False), ("a@b", False)],
)
def test_only_a_leading_at_sign_marks_a_bundle(arg, expected):
    assert bundles.is_bundle(arg) is expected


# -- the one loader (FR-1, FR-20) -------------------------------------------------------------------


def test_load_arg_reads_a_bundle_for_an_at_name_and_a_file_otherwise(fake_root, tmp_path_factory):
    put(fake_root, "mine", "version: 1\ntools:\n  gh: {}\n")
    other = tmp_path_factory.mktemp("elsewhere") / "p.yaml"
    other.write_text(profile.dumps(profile.Profile({"uv": profile.Entry(None)})))

    assert list(bundles.load_arg("@mine").tools) == ["gh"]
    assert list(bundles.load_arg(str(other)).tools) == ["uv"]


def test_a_file_whose_name_starts_with_an_at_sign_is_reachable_with_a_path(
    fake_root, tmp_path_factory, monkeypatch
):
    # `@x` always means the bundle x; `./@x` is the way to name a file that begins with `@`.
    directory = tmp_path_factory.mktemp("cwd")
    (directory / "@x").write_text("version: 1\ntools:\n  uv: {}\n")
    monkeypatch.chdir(directory)

    assert list(bundles.load_arg("./@x").tools) == ["uv"]
    with pytest.raises(ProfileError, match="no bundle"):
        bundles.load_arg("@x")
