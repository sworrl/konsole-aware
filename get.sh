#!/usr/bin/env bash
# One-line installer for konsole-aware:
#   curl -fsSL https://raw.githubusercontent.com/sworrl/konsole-aware/HEAD/get.sh | bash
# Clones (or updates) the repository into ~/.local/share/konsole-aware and runs its installer.
# Extra arguments are passed to `konsole-aware install`, e.g. `| bash -s -- --no-menu`.
set -euo pipefail

REPO="${KONSOLE_AWARE_REPO:-https://github.com/sworrl/konsole-aware.git}"
DEST="${KONSOLE_AWARE_HOME:-$HOME/.local/share/konsole-aware}"

for cmd in git python3; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
        echo "konsole-aware needs '$cmd'. Install it with your package manager and run this again." >&2
        exit 1
    fi
done

if [ -d "$DEST/.git" ]; then
    echo "Updating $DEST"
    git -C "$DEST" pull --ff-only --quiet
else
    echo "Cloning konsole-aware into $DEST"
    mkdir -p "$(dirname "$DEST")"
    git clone --depth 1 --quiet "$REPO" "$DEST"
fi

python3 "$DEST/bin/konsole-aware" install "$@"
python3 "$DEST/bin/konsole-aware" refresh >/dev/null 2>&1 || true
