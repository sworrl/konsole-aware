"""Thin qdbus wrapper shared by the Konsole controller, organizer, and watcher."""

import os
import re
import shutil
import subprocess
from functools import lru_cache
from typing import List, Optional


@lru_cache(maxsize=1)
def qdbus_bin() -> Optional[str]:
    """Locate a working qdbus. qdbus6 first: on many distros plain `qdbus` is a qtchooser shim."""
    override = os.environ.get("KONSOLE_AWARE_QDBUS")
    if override:
        return override
    for name in ("qdbus6", "qdbus-qt6", "qdbus", "qdbus-qt5"):
        path = shutil.which(name)
        if path:
            return path
    for path in ("/usr/lib/qt6/bin/qdbus", "/usr/lib/x86_64-linux-gnu/qt6/bin/qdbus"):
        if os.access(path, os.X_OK):
            return path
    return None


def dbus_disabled() -> bool:
    return os.environ.get("KONSOLE_AWARE_NO_DBUS") == "1"


def call(*args: str, timeout: float = 0.3) -> Optional[str]:
    """Run `qdbus <args...>` and return stripped stdout, or None on any failure."""
    binary = qdbus_bin()
    if not binary or dbus_disabled():
        return None
    try:
        res = subprocess.run(
            [binary, *[str(a) for a in args]],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if res.returncode != 0:
        return None
    return res.stdout.strip()


def list_names() -> List[str]:
    out = call()
    return [l.strip() for l in out.splitlines() if l.strip()] if out else []


def konsole_services() -> List[str]:
    """Well-known Konsole bus names (org.kde.konsole or org.kde.konsole-<pid>)."""
    return [n for n in list_names() if n == "org.kde.konsole" or n.startswith("org.kde.konsole-")]


def list_paths(service: str) -> List[str]:
    out = call(service)
    return [l.strip() for l in out.splitlines() if l.strip()] if out else []


def connection_pid(service: str) -> Optional[int]:
    out = call(
        "org.freedesktop.DBus",
        "/org/freedesktop/DBus",
        "org.freedesktop.DBus.GetConnectionUnixProcessID",
        service,
    )
    try:
        return int(out) if out else None
    except ValueError:
        return None


def canonical_service(service: Optional[str]) -> Optional[str]:
    """Map a unique bus name (":1.122", what KONSOLE_DBUS_SERVICE holds) to Konsole's
    well-known name ("org.kde.konsole-5659") so that session keys written by the shell
    hooks, the agent hooks, the menu and the daemon all agree."""
    if not service or not service.startswith(":"):
        return service
    return _canonical_unique(service)


@lru_cache(maxsize=16)
def _canonical_unique(service: str) -> str:
    pid = connection_pid(service)
    if pid:
        candidate = f"org.kde.konsole-{pid}"
        names = konsole_services()
        if candidate in names:
            return candidate
        if "org.kde.konsole" in names and connection_pid("org.kde.konsole") == pid:
            return "org.kde.konsole"
    return service


_VIEW_ID_RE = re.compile(r"\((\d+)\)")


def split_tabs(session_list: List[str], view_hierarchy: List[str]) -> List[List[str]]:
    """Group a window's sessionList (one id per pane, in tab order) into tabs.

    Window.viewHierarchy() returns one entry per tab such as "(2)[2]" or
    "(5)[5|7]"/"(5){5|(9)[7|9]}" for split tabs; the number of panes in each entry tells
    how many consecutive sessionList ids belong to that tab.
    """
    if not view_hierarchy:
        return [[s] for s in session_list]
    tabs: List[List[str]] = []
    pos = 0
    for entry in view_hierarchy:
        panes = len(re.findall(r"\d+", _VIEW_ID_RE.sub("", entry))) or 1
        tabs.append(session_list[pos:pos + panes])
        pos += panes
    if pos < len(session_list):
        tabs.extend([s] for s in session_list[pos:])
    return [t for t in tabs if t]
