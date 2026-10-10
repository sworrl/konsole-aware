#!/usr/bin/env bash
# Stage the system install tree used by both the .deb and the .rpm:
#   <root>/usr/lib/konsole-aware/...   (program, shell scripts, prebuilt menu loader and module)
#   <root>/usr/bin/konsole-aware       (symlink)
#   <root>/usr/share/doc/konsole-aware/
# Usage: packaging/stage.sh <root>
set -euo pipefail
ROOT="$1"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LIB="$ROOT/usr/lib/konsole-aware"

make -C "$SRC/menu_hook" >/dev/null

install -d "$LIB/bin" "$LIB/shell" "$LIB/menu_hook" "$LIB/systemd" "$ROOT/usr/bin" "$ROOT/usr/share/doc/konsole-aware"
install -m 0755 "$SRC/bin/konsole-aware" "$LIB/bin/konsole-aware"
(cd "$SRC" && find konsole_aware -name '*.py' -print0 | while IFS= read -r -d '' f; do
    install -D -m 0644 "$f" "$LIB/$f"
done)
install -m 0644 "$SRC/shell/konsole-aware.zsh" "$SRC/shell/konsole-aware.bash" "$LIB/shell/"
install -m 0644 "$SRC/menu_hook/libkonsole_aware_menu.so" "$SRC/menu_hook/libkonsole_aware_module.so" "$LIB/menu_hook/"
install -m 0644 "$SRC/menu_hook/Makefile" "$SRC/menu_hook/konsole_aware_loader.cpp" "$SRC/menu_hook/konsole_aware_module.cpp" "$LIB/menu_hook/"
install -m 0644 "$SRC/systemd/konsole-aware.service" "$LIB/systemd/"
ln -sf ../lib/konsole-aware/bin/konsole-aware "$ROOT/usr/bin/konsole-aware"
install -m 0644 "$SRC/README.md" "$SRC/CHANGELOG.md" "$SRC/LICENSE" "$ROOT/usr/share/doc/konsole-aware/"
