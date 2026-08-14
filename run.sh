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
echo

# 1. Pick a Python interpreter. paddlepaddle lags behind the newest CPython
#    releases, so prefer 3.11/3.12 over whatever "python3" happens to be.
PYTHON_BIN=""
for candidate in python3.11 python3.12 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
        PYTHON_BIN="$candidate"
        break
    fi
done
if [ -z "$PYTHON_BIN" ]; then
    echo "❌ No python3 found. Install Python 3.11+ (e.g. 'brew install python@3.11') and re-run."
    exit 1
fi
echo "✅ Using $($PYTHON_BIN --version) ($PYTHON_BIN)"

# 2. macOS system dep: poppler, needed by pdf2image for PDF-to-image conversion.
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

# 3. Create / reuse the virtual environment.
if [ ! -d ".venv" ]; then
    echo "🐍 Creating virtual environment (.venv)..."
    "$PYTHON_BIN" -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
echo "✅ Virtual environment ready ($(python --version))"

# 4. Install/verify Python dependencies. pip skips anything already satisfied,
#    so re-runs after the first are fast.
echo "📦 Checking Python dependencies (first run can take 10-20 min — paddlepaddle,"
echo "    sentence-transformers, and the LLM backend are large)..."
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt
echo "✅ Dependencies installed"

# 5. .env
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo "✅ Created .env from .env.example (defaults are fine — no API keys needed)"
fi

# 6. Standards index — required, and not in git (see .gitignore).
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

# 7. Launch, opening the browser once the server responds.
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
