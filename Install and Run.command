#!/usr/bin/env bash
# Standalone installer — hand just this one file to someone and they can
# double-click it with nothing pre-installed. It clones (or updates) the repo
# and hands off to run.sh, which does the rest (Homebrew, Python, deps, launch).
set -euo pipefail

REPO_URL="https://github.com/manoj1749/audit-risk-report-generator.git"
TARGET_DIR="$HOME/audit-risk-report-generator"

echo "=== audit-risk-report-generator — installer ==="
echo "This downloads the app to $TARGET_DIR, then sets everything else up."
echo

# 1. Xcode Command Line Tools — git itself needs this on a blank Mac.
if [[ "$(uname)" == "Darwin" ]] && ! xcode-select -p >/dev/null 2>&1; then
    echo "🔧 Command Line Tools not found — installing (a system dialog will pop up)..."
    xcode-select --install
    echo "   Waiting for it to finish — click 'Install' in the dialog that just appeared."
    until xcode-select -p >/dev/null 2>&1; do
        sleep 5
    done
    echo "✅ Command Line Tools installed"
fi

if ! command -v git >/dev/null 2>&1; then
    echo "❌ git still not found after installing Command Line Tools. Open a new Terminal"
    echo "   window and double-click this file again."
    exit 1
fi

# 2. Clone (first run) or update (re-run) the repo.
if [ -d "$TARGET_DIR/.git" ]; then
    echo "📂 Already downloaded at $TARGET_DIR — pulling the latest version..."
    git -C "$TARGET_DIR" pull
elif [ -e "$TARGET_DIR" ]; then
    echo "❌ $TARGET_DIR already exists but isn't the app's git repo."
    echo "   Move or rename it, then double-click this file again."
    exit 1
else
    echo "📥 Downloading into $TARGET_DIR..."
    git clone "$REPO_URL" "$TARGET_DIR"
fi

# 3. Hand off to the app's own setup/launch script.
cd "$TARGET_DIR"
chmod +x run.sh
exec ./run.sh
