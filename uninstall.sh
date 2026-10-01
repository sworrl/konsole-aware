#!/usr/bin/env bash
# Quick uninstaller for konsole-aware
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
chmod +x "$SCRIPT_DIR/bin/konsole-aware"

"$SCRIPT_DIR/bin/konsole-aware" uninstall
