"""Thin qdbus wrapper shared by the Konsole controller, organizer, and watcher."""

import os
import re
import shutil
import subprocess
from functools import lru_cache
from typing import Dict, List, Optional, Tuple


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


# --------------------------------------------------------------------------- Gio backend
# Talking to D-Bus in-process (GLib's Gio, available wherever KDE/GNOME runs) costs
# well under a millisecond per call; spawning `qdbus` costs ~20 ms and a process each
# time. Method signatures are discovered once per object kind via Introspect, so string
# arguments are converted exactly the way qdbus converts them.

_GIO = None
_SIGS: Dict[Tuple[str, str], Dict[str, Tuple[str, List[str], str]]] = {}


def _gio():
    global _GIO
    if _GIO is None:
        try:
            import gi
            gi.require_version("Gio", "2.0")
            from gi.repository import Gio, GLib
            conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            _GIO = (Gio, GLib, conn)
        except Exception:
            _GIO = False
    return _GIO or None


def _path_kind(path: str) -> str:
    return re.sub(r"\d+$", "N", path)


def _introspect(service: str, path: str) -> Optional[str]:
    g = _gio()
    if not g:
        return None
    Gio, GLib, conn = g
    try:
        res = conn.call_sync(service, path, "org.freedesktop.DBus.Introspectable", "Introspect",
                             None, GLib.VariantType.new("(s)"), Gio.DBusCallFlags.NO_AUTO_START, 1000, None)
        return res.unpack()[0]
    except Exception:
        return None


def _signatures(service: str, path: str) -> Dict[str, Tuple[str, List[str], str]]:
    """method name and iface.method -> (interface, [in types], out signature)."""
    key = (service, _path_kind(path))
    if key in _SIGS:
        return _SIGS[key]
    sigs: Dict[str, Tuple[str, List[str], str]] = {}
    xml = _introspect(service, path)
    if xml:
        import xml.etree.ElementTree as ET
        try:
            root = ET.fromstring(xml)
        except ET.ParseError:
            root = None
        for iface in (root.findall("interface") if root is not None else []):
            iname = iface.get("name", "")
            for m in iface.findall("method"):
                ins = [a.get("type", "") for a in m.findall("arg") if a.get("direction", "in") == "in"]
                outs = "".join(a.get("type", "") for a in m.findall("arg") if a.get("direction") == "out")
                entry = (iname, ins, outs)
                sigs[f"{iname}.{m.get('name')}"] = entry
                sigs.setdefault(m.get("name", ""), entry)
    if sigs:  # never cache a failed/empty lookup (e.g. a session that just closed)
        _SIGS[key] = sigs
    return sigs


def _to_variant_args(types: List[str], args: List[str]):
    from gi.repository import GLib
    values = []
    for t, a in zip(types, args):
        if t in ("i", "n", "x", "u", "q", "t", "y"):
            values.append(int(a))
        elif t == "b":
            values.append(str(a).lower() in ("true", "1", "yes"))
        elif t == "d":
            values.append(float(a))
        elif t == "as":
            values.append([x for x in str(a).split(",") if x] if isinstance(a, str) else list(a))
        else:
            values.append(str(a))
    return GLib.Variant("(" + "".join(types) + ")", tuple(values))


def _format(value) -> str:
    """Render a reply like qdbus prints it."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        return "\n".join(_format(v) for v in value)
    return str(value)


def _gio_call(service: str, path: str, method: str, args: List[str], timeout: float) -> Tuple[bool, Optional[str]]:
    """(handled, result). handled=False means: let the qdbus fallback try."""
    g = _gio()
    if not g:
        return False, None
    Gio, GLib, conn = g
    sigs = _signatures(service, path)
    if not sigs:
        return True, None  # object does not exist (closed tab, wrong service): fail fast
    sig = sigs.get(method)
    if sig is None:
        return False, None  # unusual method name format: let qdbus resolve it
    iface, ins, outs = sig
    if len(ins) != len(args):
        return True, None
    try:
        params = _to_variant_args(ins, args) if ins else None
    except (ValueError, TypeError):
        return True, None
    name = method.rsplit(".", 1)[-1]
    try:
        res = conn.call_sync(service, path, iface, name, params, None,
                             Gio.DBusCallFlags.NO_AUTO_START, int(timeout * 1000), None)
    except Exception:
        return True, None
    out = res.unpack() if res is not None else ()
    if not out:
        return True, ""
    return True, _format(out[0] if len(out) == 1 else list(out))


def _gio_list_names() -> Optional[List[str]]:
    g = _gio()
    if not g:
        return None
    Gio, GLib, conn = g
    try:
        res = conn.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "ListNames",
                             None, GLib.VariantType.new("(as)"), Gio.DBusCallFlags.NONE, 1000, None)
        return list(res.unpack()[0])
    except Exception:
        return None


def _gio_list_paths(service: str, path: str = "/", depth: int = 0) -> Optional[List[str]]:
    """All object paths below `path` (what `qdbus <service>` prints)."""
    xml = _introspect(service, path)
    if xml is None:
        return None if depth == 0 else []
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return []
    paths = [path] if path != "/" else []
    if depth > 6:
        return paths
    for node in root.findall("node"):
        child = node.get("name")
        if not child:
            continue
        sub = f"{path.rstrip('/')}/{child}"
        paths.extend(_gio_list_paths(service, sub, depth + 1) or [])
    return paths


# --------------------------------------------------------------------------- public API
def call(*args: str, timeout: float = 0.5) -> Optional[str]:
    """`qdbus`-compatible call: call(), call(service), or call(service, path, method, *args).

    Returns the reply as qdbus would print it (stripped), or None on failure.
    """
    if dbus_disabled():
        return None
    args = tuple(str(a) for a in args)
    if not args:
        names = _gio_list_names()
        if names is not None:
            return "\n".join(names)
    elif len(args) == 1:
        paths = _gio_list_paths(args[0])
        if paths is not None:
            return "\n".join(paths)
    elif len(args) >= 3:
        handled, result = _gio_call(args[0], args[1], args[2], list(args[3:]), timeout)
        if handled:
            return result.strip() if isinstance(result, str) else result
    return _qdbus_call(*args, timeout=max(timeout, 1.0))


def _qdbus_call(*args: str, timeout: float = 1.0) -> Optional[str]:
    binary = qdbus_bin()
    if not binary:
        return None
    try:
        res = subprocess.run([binary, *args], capture_output=True, text=True, timeout=timeout)
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
