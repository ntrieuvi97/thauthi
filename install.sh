#!/usr/bin/env bash
# ==============================================================================
# Overmind 3.0 Universal Installer (macOS / Linux / WSL / CI) 🧠⚡
# Supports both:
#   1) Remote 1-liner: curl -fsSL https://raw.githubusercontent.com/ntrieuvi97/overmind/main/install.sh | bash
#   2) Local clone:    ./install.sh
# ==============================================================================
set -e

OVERMIND_GIT_URL="${OVERMIND_GIT_URL:-https://github.com/ntrieuvi97/overmind.git}"
LOCAL_BIN="$HOME/.local/bin"

SCRIPT_DIR=""
if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "${BASH_SOURCE[0]}" ]; then
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fi

if [ -n "$SCRIPT_DIR" ] && [ -f "$SCRIPT_DIR/bin/overmind" ]; then
    REPO_DIR="$SCRIPT_DIR"
else
    REPO_DIR="$HOME/.overmind/repo"
    mkdir -p "$HOME/.overmind"
    if [ -d "$REPO_DIR/.git" ]; then
        echo "• Updating Overmind 3.0 in $REPO_DIR..."
        git -C "$REPO_DIR" pull --ff-only >/dev/null 2>&1 || true
    else
        echo "• Cloning Overmind 3.0 from $OVERMIND_GIT_URL..."
        git clone --depth 1 "$OVERMIND_GIT_URL" "$REPO_DIR"
    fi
fi

echo "=== Installing Overmind 3.0: Post-Developer Singularity Kernel 🧠⚡ ==="
echo "• Source Directory: $REPO_DIR"

chmod +x "$REPO_DIR/bin/overmind"
mkdir -p "$LOCAL_BIN"

ln -sf "$REPO_DIR/bin/overmind" "$LOCAL_BIN/overmind"
echo "✓ Linked Global CLI: $LOCAL_BIN/overmind -> $REPO_DIR/bin/overmind"

# 1. Google Antigravity Plugin Link
PLUGIN_DIR="$HOME/.gemini/config/plugins/overmind"
mkdir -p "$HOME/.gemini/config/plugins"
ln -sfn "$REPO_DIR" "$PLUGIN_DIR"
echo "✓ Linked Antigravity Plugin: $PLUGIN_DIR -> $REPO_DIR"

# 2. Claude Code Global Lifecycle Hook (~/.claude/settings.json)
python3 - <<'PYEOF'
import json
from pathlib import Path

claude_cfg = Path.home() / ".claude" / "settings.json"
claude_cfg.parent.mkdir(parents=True, exist_ok=True)
data = {}
if claude_cfg.exists():
    try:
        data = json.loads(claude_cfg.read_text(encoding="utf-8"))
    except Exception:
        data = {}

hooks = data.setdefault("hooks", {})
stop_list = hooks.setdefault("Stop", [])
cmd_str = str(Path.home() / ".local" / "bin" / "overmind") + " hook stop"
if not any("overmind hook stop" in json.dumps(item) for item in stop_list):
    stop_list.append({
        "matcher": "",
        "hooks": [{"type": "command", "command": cmd_str}]
    })
    claude_cfg.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
PYEOF
echo "✓ Registered Claude Code Global Lifecycle Hook (~/.claude/settings.json)"

export PATH="$LOCAL_BIN:$PATH"
echo ""
"$LOCAL_BIN/overmind" status || true
echo ""
echo "🎉 Overmind 3.0 is ready! Run 'overmind init' inside any repo to onboard your team & CI."
