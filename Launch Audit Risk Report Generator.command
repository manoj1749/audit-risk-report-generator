#!/usr/bin/env bash
# Double-click entry point for macOS Finder — just hands off to run.sh so
# there's one script (run.sh) that owns the actual setup/launch logic.
cd "$(dirname "$0")"
./run.sh
