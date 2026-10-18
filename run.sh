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
#    installing it via Homebrew if nothing suitable exists yet. gradio>=5.0.0
#    also requires 3.10+, so a candidate is only accepted if it's actually
#    new enough — macOS's own /usr/bin/python3 (3.9.x on many machines) must
#    NOT win just because it exists; otherwise pip install fails deep into
#    requirements.txt with a confusing "no matching distribution" error.
PYTHON_BIN=""
for candidate in python3.11 python3.12 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
        ver="$("$candidate" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")' 2>/dev/null)"
        major="${ver%%.*}"; minor="${ver##*.}"
        if [ -n "$major" ] && [ "$major" -eq 3 ] 2>/dev/null && [ "$minor" -ge 10 ] 2>/dev/null; then
            PYTHON_BIN="$candidate"
            break
        fi
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

# 3b. macOS 26 (Tahoe) can leave platform.mac_ver() returning all-empty
#     values, which crashes pip>=24.2 (and therefore ensurepip/venv
#     creation below) the moment it tries to build an SSL context. Fix it
#     before touching pip at all. No-op on machines without the bug.
"$PYTHON_BIN" scripts/fix_macos_mac_ver.py

# 3c. Some Homebrew python@3.11 bottles were linked against a newer
#     libexpat than the one the OS actually ships, breaking pyexpat —
#     which pip's own code imports transitively, breaking every pip
#     command. Fix it the same way: no-op on machines without the bug.
"$PYTHON_BIN" scripts/fix_macos_pyexpat.py

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

# 5. Create / reuse the virtual environment. A .venv built before this
#    script checked interpreter versions may be stuck on a too-old Python
#    (e.g. 3.9) — rebuild it in that case instead of silently reusing it.
if [ -d ".venv" ]; then
    venv_ver="$(.venv/bin/python -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")' 2>/dev/null || echo "0.0")"
    venv_major="${venv_ver%%.*}"; venv_minor="${venv_ver##*.}"
    if ! { [ "$venv_major" -eq 3 ] 2>/dev/null && [ "$venv_minor" -ge 10 ] 2>/dev/null; }; then
        echo "🐍 Existing .venv uses Python $venv_ver (too old) — rebuilding with $($PYTHON_BIN --version)..."
        rm -rf .venv
    fi
fi
if [ ! -d ".venv" ]; then
    echo "🐍 Creating virtual environment (.venv)..."
    # On pip >=26.1, ensurepip's own self-install (installing a wheel
    # literally named "pip" from loose files on sys.path) can trip pip's
    # own post-CVE import guard and fail outright. If that happens, fall
    # back to installing pip ourselves straight from a PyPI wheel —
    # see scripts/bootstrap_pip.py for the full explanation.
    if ! "$PYTHON_BIN" -m venv .venv; then
        echo "⚠️  venv creation hit a known pip self-install bug — installing pip manually instead..."
        rm -rf .venv
        "$PYTHON_BIN" -m venv --without-pip .venv
        "$PYTHON_BIN" scripts/bootstrap_pip.py .venv
    fi
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

# 8. Standards index — required, and not in git (see .gitignore: the standards
#    PDFs are ICAI/MCA-licensed material, kept out of the public repo).
STANDARDS_INDEX_DRIVE_ID="1hdLopPijZDlzZNNcFlFcA3cDqTVrrwe6"

if [ ! -f "data/chroma_db/chroma.sqlite3" ]; then
    echo "⚠️  Standards index (data/chroma_db) not found."
    if ls data/raw_zips/*.zip >/dev/null 2>&1; then
        echo "📚 Building it now from data/raw_zips (one-time, ~10-20 min)..."
        python scripts/setup_standards.py
    else
        echo "📥 Downloading the prebuilt standards index (~83MB)..."
        pip install --quiet gdown
        ARCHIVE="$PROJECT_ROOT/.standards_index_download.zip"
        if gdown "https://drive.google.com/uc?id=${STANDARDS_INDEX_DRIVE_ID}" -O "$ARCHIVE"; then
            echo "📦 Extracting..."
            unzip -q -o "$ARCHIVE" -d "$PROJECT_ROOT"
            rm -f "$ARCHIVE"
            echo "✅ Standards index downloaded and extracted"
        else
            rm -f "$ARCHIVE"
            echo "❌ Download failed (no network, or the Drive link changed/lost access) and"
            echo "   data/raw_zips has no source ZIPs either — the app cannot run without the"
            echo "   indexed standards corpus. Copy 'data/standards/' and 'data/chroma_db/'"
            echo "   here manually (AirDrop, zip transfer, etc.), then re-run."
            exit 1
        fi
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

# python app.py runs as the literal foreground command (not backgrounded/waited)
# so Ctrl+C's SIGINT — delivered by the terminal to the whole foreground process
# group at once — reaches it directly. (Tried a trap+`wait $PID` version first;
# macOS ships bash 3.2, where a trap does not reliably interrupt a `wait` on a
# backgrounded child, so that approach silently left the server running.)
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
