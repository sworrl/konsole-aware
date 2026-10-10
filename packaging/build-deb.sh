#!/usr/bin/env bash
# Build dist/konsole-aware_<version>_<arch>.deb. Needs: dpkg-deb, g++, make, Qt 6 headers.
set -euo pipefail
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="${1:-$(python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); import konsole_aware; print(konsole_aware.__version__)' "$SRC")}"
ARCH="$(dpkg --print-architecture)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

"$SRC/packaging/stage.sh" "$WORK/root"
install -d "$WORK/root/DEBIAN"
SIZE="$(du -sk "$WORK/root" | cut -f1)"
cat > "$WORK/root/DEBIAN/control" <<CONTROL
Package: konsole-aware
Version: $VERSION
Architecture: $ARCH
Maintainer: sworrl <github@falcontechnix.com>
Installed-Size: $SIZE
Depends: python3 (>= 3.8), konsole, libqt6widgets6 | libqt6widgets6t64, libqt6dbus6 | libqt6dbus6t64
Recommends: python3-gi, libnotify-bin, pulseaudio-utils | pipewire-bin, gdb
Section: utils
Priority: optional
Homepage: https://github.com/sworrl/konsole-aware
Description: Konsole tab awareness for AI coding agents and shell tasks
 Tab titles, colors and badges in KDE Konsole that show which AI coding agent
 (Claude Code, Antigravity, Gemini, Codex, ...) runs in each tab, its model,
 effort and mode, and whether it is working, done, or waiting for input. Also
 tracks long-running shell commands and snaps same-project tabs together.
 .
 After installing, each user runs: konsole-aware install
CONTROL
cat > "$WORK/root/DEBIAN/postinst" <<'POSTINST'
#!/bin/sh
set -e
if [ "$1" = "configure" ]; then
    echo "konsole-aware installed. Each user enables it with: konsole-aware install"
fi
POSTINST
chmod 0755 "$WORK/root/DEBIAN/postinst"

mkdir -p "$SRC/dist"
OUT="$SRC/dist/konsole-aware_${VERSION}_${ARCH}.deb"
dpkg-deb --root-owner-group --build "$WORK/root" "$OUT" >/dev/null
echo "$OUT"
