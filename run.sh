#!/usr/bin/env bash
# One-shot setup + launch for audit-risk-report-generator.
#
# Checks the interpreter, system deps, venv, Python deps, .env, and the
# indexed standards corpus, fixing what it safely can, then starts the app
# and opens it in the browser. Safe to re-run — every step is idempotent.
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"

echo "=== audit-risk-report-generator — setup & launch ==="
echo "This bootstraps everything from a blank machine: Command Line Tools,"
echo "Homebrew, Python, poppler, the venv, Python deps, and the standards index."
echo "First run on a fresh Mac can take 20-40+ min depending on your connection"
echo "(several GB of ML packages, then a ~4.7GB model on first analysis run)."
echo

# 1. Xcode Command Line Tools — required by Homebrew and by some pip packages
#    that build native extensions. Needs a real desktop session (GUI installer).
if [[ "$(uname)" == "Darwin" ]] && ! xcode-select -p >/dev/null 2>&1; then
    echo "🔧 Command Line Tools not found — installing (a system dialog will pop up)..."
    xcode-select --install
    echo "   Waiting for it to finish — click 'Install' in the dialog that just appeared."
    until xcode-select -p >/dev/null 2>&1; do
        sleep 5
    done
    echo "✅ Command Line Tools installed"
elif [[ "$(uname)" == "Darwin" ]]; then
    echo "✅ Command Line Tools found"
fi

# 2. Homebrew — used to install Python 3.11 and poppler below.
if [[ "$(uname)" == "Darwin" ]] && ! command -v brew >/dev/null 2>&1; then
    echo "🍺 Homebrew not found — installing (you may be prompted for your password)..."
    NONINTERACTIVE=1 /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    if [[ -x /opt/homebrew/bin/brew ]]; then
        eval "$(/opt/homebrew/bin/brew shellenv)"   # Apple Silicon
    elif [[ -x /usr/local/bin/brew ]]; then
        eval "$(/usr/local/bin/brew shellenv)"      # Intel
    fi
    if command -v brew >/dev/null 2>&1; then
        echo "✅ Homebrew installed"
    else
        echo "⚠️  Homebrew install didn't complete — Python/poppler auto-install below may fail."
        echo "   Install manually from https://brew.sh and re-run."
    fi
elif [[ "$(uname)" == "Darwin" ]]; then
    echo "✅ Homebrew found"
fi

# 3. Pick a Python interpreter. paddlepaddle lags behind the newest CPython
#    releases, so prefer 3.11/3.12 over whatever "python3" happens to be —
#    installing it via Homebrew if nothing suitable exists yet.
PYTHON_BIN=""
for candidate in python3.11 python3.12 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
        PYTHON_BIN="$candidate"
        break
    fi
done
if [ -z "$PYTHON_BIN" ] && command -v brew >/dev/null 2>&1; then
    echo "🐍 Python not found — installing python@3.11 via Homebrew..."
    brew install python@3.11
    PYTHON_BIN="$(brew --prefix python@3.11)/bin/python3.11"
fi
if [ -z "$PYTHON_BIN" ] || ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    echo "❌ No usable Python found and couldn't install one automatically."
    echo "   Install Python 3.11+ (e.g. https://www.python.org/downloads/) and re-run."
    exit 1
fi
echo "✅ Using $($PYTHON_BIN --version) ($PYTHON_BIN)"

# 4. macOS system dep: poppler, needed by pdf2image for PDF-to-image conversion.
if [[ "$(uname)" == "Darwin" ]]; then
    if ! command -v pdftoppm >/dev/null 2>&1; then
        if command -v brew >/dev/null 2>&1; then
            echo "📦 Installing poppler (pdf2image dependency) via Homebrew..."
            brew install poppler
        else
            echo "⚠️  poppler not found and Homebrew isn't installed."
            echo "   Install Homebrew (https://brew.sh), then run: brew install poppler"
            echo "   Without it, PDF uploads that need image conversion will fail."
        fi
    else
        echo "✅ poppler found"
    fi
fi

# 5. Create / reuse the virtual environment.
if [ ! -d ".venv" ]; then
    echo "🐍 Creating virtual environment (.venv)..."
    "$PYTHON_BIN" -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
echo "✅ Virtual environment ready ($(python --version))"

# 6. Install/verify Python dependencies. pip skips anything already satisfied,
#    so re-runs after the first are fast.
echo "📦 Checking Python dependencies (first run can take 10-20 min — paddlepaddle,"
echo "    sentence-transformers, and the LLM backend are large)..."
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt
echo "✅ Dependencies installed"

# 7. .env
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo "✅ Created .env from .env.example (defaults are fine — no API keys needed)"
fi

# 8. Standards index — required, and not in git (see .gitignore).
if [ ! -f "data/chroma_db/chroma.sqlite3" ]; then
    echo "⚠️  Standards index (data/chroma_db) not found."
    if ls data/raw_zips/*.zip >/dev/null 2>&1; then
        echo "📚 Building it now from data/raw_zips (one-time, ~10-20 min)..."
        python scripts/setup_standards.py
    else
        echo "❌ data/raw_zips has no source ZIPs and data/chroma_db is empty — the app"
        echo "   cannot run without the indexed standards corpus. Copy 'data/standards/'"
        echo "   and 'data/chroma_db/' here from a machine where it's already built"
        echo "   (they're gitignored, not in the repo — e.g. AirDrop or a zip transfer),"
        echo "   then re-run this script."
        exit 1
    fi
else
    echo "✅ Standards index found"
fi

# 9. Launch, opening the browser once the server responds.
echo
echo "🚀 Starting the app — it'll open in your browser automatically."
echo "   The first 'Run Audit Risk Analysis' click also downloads the local LLM"
echo "   (~4.7GB, one-time). Press Ctrl+C here to stop the server."
echo

(
    for _ in $(seq 1 30); do
        sleep 1
        if curl -s -o /dev/null "http://127.0.0.1:7860"; then
            if command -v open >/dev/null 2>&1; then
                open "http://127.0.0.1:7860"        # macOS
            elif command -v xdg-open >/dev/null 2>&1; then
                xdg-open "http://127.0.0.1:7860"    # Linux
            fi
            break
        fi
    done
) &

python app.py
