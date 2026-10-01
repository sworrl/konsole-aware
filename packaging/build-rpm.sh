#!/usr/bin/env bash
# Build dist/konsole-aware-<version>-1.<dist>.<arch>.rpm. Needs: rpmbuild, g++, make, Qt 6 devel headers.
set -euo pipefail
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="${1:-$(python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); import konsole_aware; print(konsole_aware.__version__)' "$SRC")}"
TOP="$(mktemp -d)"
trap 'rm -rf "$TOP"' EXIT
mkdir -p "$TOP"/{SOURCES,SPECS,BUILD,RPMS,SRPMS}

tar --exclude=.git --exclude=dist --exclude=build --exclude='*.egg-info' --exclude=__pycache__ \
    --exclude='menu_hook/*.so' --transform "s,^\.,konsole-aware-$VERSION," \
    -czf "$TOP/SOURCES/konsole-aware-$VERSION.tar.gz" -C "$SRC" .
rpmbuild -bb --define "_topdir $TOP" --define "ka_version $VERSION" "$SRC/packaging/konsole-aware.spec" >/dev/null
mkdir -p "$SRC/dist"
find "$TOP/RPMS" -name '*.rpm' -exec cp {} "$SRC/dist/" \; -print | sed "s,$TOP/RPMS/[^/]*/,$SRC/dist/,"
