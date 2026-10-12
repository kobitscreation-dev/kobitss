#!/usr/bin/env bash
# Kobits CLI Installer (macOS & Linux)
set -e

echo "⚡ Installing Kobits CLI (Autonomous Multi-Agent Engineering Engine)..."

if command -v pip3 &>/dev/null; then
    PIP_CMD="pip3"
elif command -v pip &>/dev/null; then
    PIP_CMD="pip"
else
    echo "❌ Error: Python 3 and pip are required to install Kobits."
    exit 1
fi

$PIP_CMD install --upgrade "git+https://github.com/kobitscreation-dev/kobitss.git"

echo "✅ Kobits installed successfully!"
echo "Run 'kobits --help' or 'kobits run \"your objective\"' to start."
