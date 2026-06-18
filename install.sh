#!/usr/bin/env bash
set -euo pipefail

DEVSETUP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="${HOME}/.local/bin"
LINK="$BIN_DIR/dev-setup"

mkdir -p "$BIN_DIR"
chmod +x "$DEVSETUP_DIR/dev-setup"
ln -sf "$DEVSETUP_DIR/dev-setup" "$LINK"

echo "  ✔ Installed: $LINK → $DEVSETUP_DIR/dev-setup"

BASHRC="$HOME/.bashrc"
PATH_LINE='export PATH="$HOME/.local/bin:$PATH"'
if ! grep -qF '.local/bin' "$BASHRC" 2>/dev/null; then
    printf '\n# dev-setup\n%s\n' "$PATH_LINE" >> "$BASHRC"
    echo "  ✔ Added ~/.local/bin to PATH in $BASHRC"
    echo "    Run: source ~/.bashrc  (or open a new terminal)"
else
    echo "  ✔ ~/.local/bin already on PATH in $BASHRC"
fi

echo ""
echo "  Run 'dev-setup --help' to get started."
