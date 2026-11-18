"""Package F fixture spike: run `.venv/bin/python -B dev/tui_preview.py`.

Thin wrapper over the real `dev_setup.tui.browse` renderer with seven static
entries — the same view `devstuff tui` gets, without reading any catalog.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from dev_setup.tui import Entry, browse

ENTRIES = (
    Entry("docker", "Container engine and Compose plugin", "core", "apt",
          ("Add package source (sudo)", "Install engine and plugins (sudo)",
           "Verify CLI and Compose plugin",
           "[fixture] Engine present; Compose not found.")),
    Entry("lazygit", "Terminal UI for git", "tools", "archive",
          ("Fetch release archive", "Replace binary", "Verify version",
           "[fixture] Version check passed.")),
    Entry("ripgrep", "Fast recursive search", "tools", "apt",
          ("No version change planned", "[fixture] Already current.")),
    Entry("shellcheck", "Shell script linter", "tools", "apt",
          ("Install package (sudo)", "Verify binary",
           "[fixture] Waiting for apt.")),
    Entry("uv", "Python package and project manager", "core", "script",
          ("Fetch installer", "Run installer", "Verify version",
           "[fixture] No output from installer for 31s.")),
    Entry("commitizen", "Conventional-commit helper", "tools", "uv tool",
          ("Resolve package via uv", "Expose CLI", "Verify binary",
           "[fixture] Waiting for uv update.")),
    Entry("fzf", "Fuzzy finder", "tools", "apt",
          ("No version change planned", "[fixture] Already current.")),
)


if __name__ == "__main__":
    browse(ENTRIES, title="devstuff TUI preview - fixtures only")
