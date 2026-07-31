"""Whether this machine has a Nerd Font, so the wizard can offer to install one.

The check is deliberately broad — *any* Nerd Font, not the one devstuff installs. A
user who already runs FiraCode Nerd Font has everything the icon presets need, and
nagging them to install a second font would be noise.

`detect()` is allowed to say "I don't know" (`None`): without fontconfig there is no
way to enumerate fonts, and guessing wrong in either direction is worse than saying so.
"""

from __future__ import annotations

import os
import shutil
import subprocess

# The catalog tool the wizard offers. Its own `check_cmd` is narrower than `detect()`
# on purpose: it answers "is *this* font installed", which is what install/remove act on.
NERD_FONT_KEY = "nerd-font"
NERD_FONT_URL = "https://www.nerdfonts.com/"

TIMEOUT = 5


def detect() -> bool | None:
    """True/False if a Nerd Font is present, None if it cannot be determined."""
    if shutil.which("fc-list") is None:
        return None
    try:
        proc = subprocess.run(
            ["fc-list", ":", "family"], capture_output=True, text=True, timeout=TIMEOUT
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return "nerd font" in proc.stdout.lower()


def is_remote_session() -> bool:
    """Whether this shell is being driven from another machine.

    A font is rendered by the terminal emulator, not by the shell: over SSH the fonts
    that matter are the ones on the *client*, so installing one here would achieve
    nothing and `detect()`'s answer is about the wrong computer.
    """
    return any(os.environ.get(var) for var in ("SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY"))
