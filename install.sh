#!/usr/bin/env bash
# ==============================================================================
# Overmind Universal Installer for macOS & Linux 🧠⚡
# ==============================================================================
set -e

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
LOCAL_BIN="$HOME/.local/bin"

echo "=== Installing Overmind Self-Evolving Agent OS 🧠⚡ ==="
echo "• Repository: $REPO_DIR"

chmod +x "$REPO_DIR/bin/overmind"
mkdir -p "$LOCAL_BIN"

ln -sf "$REPO_DIR/bin/overmind" "$LOCAL_BIN/overmind"
echo "✓ Linked CLI: $LOCAL_BIN/overmind -> $REPO_DIR/bin/overmind"

export PATH="$LOCAL_BIN:$PATH"
echo ""
"$LOCAL_BIN/overmind" status || true
echo ""
echo "🎉 Overmind is installed and ready! Run 'overmind --help' from any terminal."
