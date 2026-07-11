#!/usr/bin/env bash
# Installs devstuff from PyPI using pipx (preferred) or pip.
set -euo pipefail

PACKAGE="devstuff"

_find_python() {
    local py
    for py in python3.13 python3.12 python3.11 python3; do
        if command -v "$py" &>/dev/null; then
            if "$py" -c "import sys; exit(0 if sys.version_info >= (3,11) else 1)" 2>/dev/null; then
                echo "$py"
                return 0
            fi
        fi
    done
    echo "  ✖ Python 3.11+ is required." >&2
    echo "    Install from https://www.python.org/downloads/ or via your package manager." >&2
    exit 1
}

_ensure_local_bin_on_path() {
    local dir="$HOME/.local/bin"
    local bashrc="$HOME/.bashrc"
    if ! grep -qF '.local/bin' "$bashrc" 2>/dev/null; then
        printf '\n# dev-setup\nexport PATH="%s:$PATH"\n' "$dir" >> "$bashrc"
        echo "  ✔ Added $dir to PATH in $bashrc"
        NEED_RELOAD=1
    fi
}

PYTHON=$(_find_python)
NEED_RELOAD=0
echo "  ✔ Python: $($PYTHON --version)"

# --- pipx path (preferred: isolated env, clean uninstall) ---
if command -v pipx &>/dev/null || "$PYTHON" -m pipx --version &>/dev/null 2>&1; then
    PIPX=$(command -v pipx 2>/dev/null || echo "$PYTHON -m pipx")
    echo "  ❯ Installing $PACKAGE via pipx..."
    $PIPX install "$PACKAGE"
    echo ""
    echo "  ✔ Done. Run: devstuff --help"
    exit 0
fi

# --- bootstrap pipx then use it ---
if "$PYTHON" -m pip --version &>/dev/null 2>&1; then
    echo "  ❯ pipx not found — installing pipx first..."
    "$PYTHON" -m pip install --user pipx --quiet
    "$PYTHON" -m pipx ensurepath --quiet 2>/dev/null || true
    _ensure_local_bin_on_path

    echo "  ❯ Installing $PACKAGE via pipx..."
    "$PYTHON" -m pipx install "$PACKAGE"
    echo ""
    echo "  ✔ Done. Run: devstuff --help"
    [ "$NEED_RELOAD" -eq 1 ] && echo "    (run 'source ~/.bashrc' first if the command isn't found)"
    exit 0
fi

# --- last resort: pip --user ---
echo "  ❯ Installing $PACKAGE via pip..."
"$PYTHON" -m pip install --user "$PACKAGE" --quiet
_ensure_local_bin_on_path
echo ""
echo "  ✔ Done. Run: devstuff --help"
[ "$NEED_RELOAD" -eq 1 ] && echo "    (run 'source ~/.bashrc' first if the command isn't found)"
