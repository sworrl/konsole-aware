"""Patterned tab backgrounds: project colors, two-tone, gradient, rainbow, stripes.

Konsole itself only draws a tab's color as a thin line under the title. The context
menu library (menu_hook/) paints the whole tab background from a spec published here,
in $XDG_RUNTIME_DIR/konsole-aware/styles/tab-styles.json:

    {"_settings": {"follow_position": false, "palette": [...]},
     "org.kde.konsole-5659:/Sessions/13": {"pattern": "two-tone",
                                           "colors": ["#2563eb", "#d97757"],
                                           "opacity": 0.6, "tint": 0.2, "band": true}}

The library watches the file and repaints. It picks black or white text per tab from
the pattern's brightness, so titles stay readable on any colors. It also tints the
terminal under each tab with the tab's colors (`tint`) and draws a strip of them along
the top of the terminal (`band`). Every pane of a split tab uses the tab's colors, so
tabs joined with attach or pair share one color.

Every project gets its own color automatically. Assignments are kept in
~/.config/konsole-aware/project-colors.json so a project keeps its color across
sessions; edit that file, or use `konsole-aware colors set`, to choose your own.
"""

import colorsys
import fcntl
import hashlib
import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from konsole_aware.config import config_home, load_config
from konsole_aware.state.manager import STATE_DIR

# Own folder: the Konsole library watches it, and the state folder next to it changes
# on almost every hook event.
STYLES_FILE = STATE_DIR / "styles" / "tab-styles.json"
PROJECT_COLORS_FILE = config_home() / "konsole-aware" / "project-colors.json"

PATTERNS = ("none", "solid", "two-tone", "gradient", "rainbow", "stripes")

# 16 hues spaced around the wheel at a medium lightness, so white or black text reads on
# all of them and neighbors in the list are far apart.
PROJECT_PALETTE = [
    "#2563eb", "#16a34a", "#d97706", "#9333ea", "#0891b2", "#dc2626", "#65a30d", "#c026d3",
    "#0d9488", "#ea580c", "#4f46e5", "#ca8a04", "#db2777", "#059669", "#7c3aed", "#0284c7",
]

RAINBOW = ["#ef4444", "#f59e0b", "#eab308", "#22c55e", "#06b6d4", "#3b82f6", "#8b5cf6"]


@contextmanager
def _locked(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path.with_name("." + path.name + ".lock"), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def _read(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(path: Path, data: Dict[str, Any]) -> None:
    tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


# ------------------------------------------------------------------ project colors
def project_colors() -> Dict[str, Any]:
    return _read(PROJECT_COLORS_FILE)


def project_entry(project: str) -> Dict[str, Any]:
    """Color entry for a project, assigning the next unused palette color the first time."""
    if not project:
        return {}
    with _locked(PROJECT_COLORS_FILE):
        data = _read(PROJECT_COLORS_FILE)
        entry = data.get(project)
        if isinstance(entry, str):
            entry = {"color": entry}
        if not isinstance(entry, dict) or not entry.get("color"):
            used = {(v.get("color") if isinstance(v, dict) else v) for v in data.values()}
            free = [c for c in PROJECT_PALETTE if c not in used]
            if free:
                color = free[0]
            else:  # more projects than palette colors: derive a stable hue from the name
                h = int(hashlib.sha1(project.encode()).hexdigest()[:4], 16) / 0xFFFF
                r, g, b = colorsys.hls_to_rgb(h, 0.45, 0.65)
                color = "#%02x%02x%02x" % (int(r * 255), int(g * 255), int(b * 255))
            entry = {"color": color}
            data[project] = entry
            _write(PROJECT_COLORS_FILE, data)
        return dict(entry)


def set_project_colors(project: str, colors: List[str], pattern: Optional[str] = None) -> Dict[str, Any]:
    with _locked(PROJECT_COLORS_FILE):
        data = _read(PROJECT_COLORS_FILE)
        entry = {"color": colors[0]}
        if len(colors) > 1:
            entry["colors"] = colors
        if pattern:
            entry["pattern"] = pattern
        data[project] = entry
        _write(PROJECT_COLORS_FILE, data)
        return entry


def forget_project(project: str) -> None:
    with _locked(PROJECT_COLORS_FILE):
        data = _read(PROJECT_COLORS_FILE)
        if data.pop(project, None) is not None:
            _write(PROJECT_COLORS_FILE, data)


# ------------------------------------------------------------------ tab specs
def build_spec(project: str, status_color: Optional[str], override: Optional[Dict[str, Any]] = None,
               config: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """The background spec for one tab.

    Default (`tab_pattern: "project-status"`): two-tone, project color on the left and the
    agent/status color on the right; solid project color when there is no status color.
    A per-tab override (from `konsole-aware tab-style`) or a per-project pattern wins.
    """
    config = config or load_config()
    opacity = float(config.get("tab_pattern_opacity", 0.6))
    pattern = config.get("tab_pattern", "project-status")
    if pattern == "none" and not override:
        return None
    entry = project_entry(project) if project else {}
    project_color = entry.get("color")
    if override and override.get("pattern"):
        colors = list(override.get("colors") or [])
        if not colors and override["pattern"] != "rainbow":
            colors = [c for c in (project_color, status_color) if c and c.lower() != "#000000"]
            if len(colors) == 1 and override["pattern"] != "solid":
                colors.append(_shift(colors[0]))
        spec = {"pattern": override["pattern"], "colors": colors}
    elif entry.get("pattern"):
        spec = {"pattern": entry["pattern"], "colors": list(entry.get("colors") or [project_color])}
    elif pattern == "project-status":
        if project_color and status_color and status_color.lower() not in ("#000000", ""):
            spec = {"pattern": "two-tone", "colors": [project_color, status_color]}
        elif project_color:
            spec = {"pattern": "solid", "colors": [project_color]}
        else:
            return None
    elif pattern in ("solid", "project"):
        spec = {"pattern": "solid", "colors": [project_color or status_color]}
    elif pattern == "rainbow":
        spec = {"pattern": "rainbow", "colors": []}
    else:
        spec = {"pattern": pattern, "colors": [c for c in (project_color, status_color) if c]}
    if spec["pattern"] == "rainbow" and not spec["colors"]:
        spec["colors"] = list(RAINBOW)
    spec["colors"] = [c for c in spec["colors"] if c]
    if not spec["colors"] or spec["pattern"] == "none":
        return None
    spec["opacity"] = max(0.15, min(1.0, opacity))
    spec["tint"] = max(0.0, min(0.5, float(config.get("terminal_tint", 0.2) or 0)))
    spec["band"] = bool(config.get("terminal_band", True))
    return spec


def window_settings(config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Settings for the whole tab bar, stored under "_settings" in the styles file."""
    config = config or load_config()
    return {"follow_position": bool(config.get("tab_colors_follow_position", False)),
            "palette": list(PROJECT_PALETTE)}


def _shift(hex_color: str, amount: float = 0.33) -> str:
    """A related second color (hue rotated) for patterns given only one color."""
    c = hex_color.lstrip("#")
    r, g, b = (int(c[i:i + 2], 16) / 255 for i in (0, 2, 4))
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    r, g, b = colorsys.hls_to_rgb((h + amount) % 1.0, l, s)
    return "#%02x%02x%02x" % (int(r * 255), int(g * 255), int(b * 255))


def publish(session_key: str, spec: Optional[Dict[str, Any]]) -> None:
    """Record one tab's spec for the Konsole library (no write if unchanged)."""
    settings = window_settings()
    with _locked(STYLES_FILE):
        data = _read(STYLES_FILE)
        same = data.get(session_key) == spec or (spec is None and session_key not in data)
        if same and data.get("_settings") == settings:
            return
        data["_settings"] = settings
        if spec is None:
            data.pop(session_key, None)
        else:
            data[session_key] = spec
        _write(STYLES_FILE, data)


def prune(live_keys: set) -> None:
    with _locked(STYLES_FILE):
        data = _read(STYLES_FILE)
        stale = [k for k in data if k not in live_keys and not k.startswith("_")]
        if stale:
            for k in stale:
                data.pop(k)
            _write(STYLES_FILE, data)


def apply(session_key: str, project_root: Optional[str], status_color: Optional[str]) -> None:
    """Compute and publish the spec for a tab. Safe to call on every title update."""
    from konsole_aware.state.manager import StateManager
    override = StateManager().get_session(session_key).get("tab_style")
    project = Path(project_root).name if project_root else ""
    try:
        publish(session_key, build_spec(project, status_color, override))
    except OSError:
        pass
