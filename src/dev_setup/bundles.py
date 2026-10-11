"""Profiles that ship with devstuff, named with a leading `@` (docs/specs/profile-apply, FR-20, FR-21).

`devstuff profile apply @devops` is `apply` on a file that lives in the package. A bundle is an
ordinary profile — same format, same strict loader — so there is exactly one thing to document
and to validate (SD-9), and any bundle can be copied out and edited.

The name is never turned into a path. Lookup lists the bundle directory and matches the name
against what it finds, so `@../x` or `@/etc/passwd` has nothing to traverse: it is simply not in
the list.
"""

from __future__ import annotations

import re
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path

from dev_setup import profile
from dev_setup.profile import Profile, ProfileError

_SUFFIX = ".yaml"
_NAME = re.compile(r"[a-z][a-z0-9-]*")


def _root() -> Traversable:
    return resources.files("dev_setup") / "bundles"


def _files() -> dict[str, Traversable]:
    """Bundle name -> its file. Anything in the directory that is not a well-named YAML file is
    not a bundle: the data tests insist the directory holds nothing else."""
    found: dict[str, Traversable] = {}
    for entry in _root().iterdir():
        stem = entry.name.removesuffix(_SUFFIX)
        if entry.is_file() and entry.name.endswith(_SUFFIX) and _NAME.fullmatch(stem):
            found[stem] = entry
    return found


def names() -> list[str]:
    return sorted(_files())


def is_bundle(arg: str) -> bool:
    """`@name` is a bundle; anything else is a file. A file that really starts with `@` is `./@x`."""
    return arg.startswith("@")


def resolve(arg: str) -> Profile:
    """Load the bundle `arg` (`@name`). An unknown name lists the ones that exist (FR-21)."""
    files = _files()
    entry = files.get(arg[1:]) if is_bundle(arg) else None
    if entry is None:
        available = ", ".join(f"@{n}" for n in sorted(files)) or "(none)"
        raise ProfileError(f"no bundle named {arg!r}. Bundles: {available}")
    return profile.loads(entry.read_text(encoding="utf-8"), source=arg)


def load_arg(arg: str) -> Profile:
    """The one way a command turns its PROFILE argument into a profile."""
    return resolve(arg) if is_bundle(arg) else profile.load(Path(arg))
