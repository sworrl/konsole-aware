"""Patterned tab backgrounds: project colors, two-tone, gradient, rainbow, stripes, flag.

Konsole itself only draws a tab's color as a thin line under the title. The context
menu library (menu_hook/) paints the whole tab background from a spec published here,
in $XDG_RUNTIME_DIR/konsole-aware/styles/tab-styles-2.json:

    {"_settings": {"follow_position": false, "palette": [...]},
     "org.kde.konsole-5659:/Sessions/13": {"pattern": "two-tone",
                                           "colors": ["#d04e56", "#d97757"],
                                           "opacity": 0.6, "tint": 0.2, "band": true}}

The library watches the file and repaints. It picks black or white text per tab from
the pattern's brightness, so titles stay readable on any colors. It also tints the
terminal under each tab with the tab's colors (`tint`) and draws a strip of them along
the top of the terminal (`band`). The terminal shows the same pattern as its tab, drawn
at the terminal's size. Every pane of a split tab uses the tab's colors, so tabs joined
with attach or pair share one color.

Now and then a tab gets the American flag instead of its usual colors (`flag_chance`,
1 in 10 by default). The pick is made from the tab's id, so a tab keeps it for as long
as it's open.

Every project gets its own color automatically. Assignments are kept in
~/.config/konsole-aware/project-colors.json so a project keeps its color across
sessions; edit that file, or use `konsole-aware colors set`, to choose your own.
"""

import colorsys
import fcntl
import hashlib
import json
import math
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from konsole_aware.config import config_home, load_config
from konsole_aware.state.manager import STATE_DIR

# Own folder: the Konsole library watches it, and the state folder next to it changes
# on almost every hook event. 1.2 libraries read tab-styles.json, which is no longer
# written; `hotload` removes it once the 1.4 module runs in every Konsole, so a 1.2
# library still loaded next to it stops painting over the new one.
STYLES_FILE = STATE_DIR / "styles" / "tab-styles-2.json"
LEGACY_STYLES_FILE = STATE_DIR / "styles" / "tab-styles.json"
PROJECT_COLORS_FILE = config_home() / "konsole-aware" / "project-colors.json"

PATTERNS = ("none", "solid", "two-tone", "gradient", "rainbow", "stripes", "flag")

# 16 hues evenly spaced around the color wheel (OKLCH), in an order that keeps neighbors
# in the list far apart. Lightness alternates a little between neighboring hues so close
# hues still tell apart, and stays where white or black text reads on all of them.
PROJECT_PALETTE = [
    "#d04e56", "#12b49b", "#b953a7", "#72b036", "#816ada", "#c09506", "#0486d3", "#ed733e",
    "#0d9298", "#e96a9c", "#0f9958", "#be79e4", "#868604", "#7095fe", "#b66c00", "#14acd3",
]

# The palette before 1.3.0. Projects still on one of these move to the color at the same
# place in the new palette.
LEGACY_PALETTE = [
    "#2563eb", "#16a34a", "#d97706", "#9333ea", "#0891b2", "#dc2626", "#65a30d", "#c026d3",
    "#0d9488", "#ea580c", "#4f46e5", "#ca8a04", "#db2777", "#059669", "#7c3aed", "#0284c7",
]

# Red to violet at one lightness, so a title reads the same across the whole tab.
RAINBOW = ["#f3625d", "#ca8a10", "#81a904", "#15b194", "#17a7d2", "#7c8cfc", "#ce6bd4"]

# Old Glory red, white and Old Glory blue. The library draws the flag itself; these are
# for the title color and the terminal swatch.
FLAG = ["#b22234", "#ffffff", "#3c3b6e"]


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
def _migrate(data: Dict[str, Any]) -> bool:
    """Move projects on the old palette to the new one. True when anything changed."""
    old = {c: PROJECT_PALETTE[i] for i, c in enumerate(LEGACY_PALETTE)}
    changed = False
    for name, entry in data.items():
        color = entry.get("color") if isinstance(entry, dict) else entry
        if isinstance(color, str) and color.lower() in old and not (isinstance(entry, dict) and entry.get("colors")):
            data[name] = dict(entry, color=old[color.lower()]) if isinstance(entry, dict) else {"color": old[color.lower()]}
            changed = True
    return changed


def project_colors() -> Dict[str, Any]:
    with _locked(PROJECT_COLORS_FILE):
        data = _read(PROJECT_COLORS_FILE)
        if _migrate(data):
            _write(PROJECT_COLORS_FILE, data)
        return data


def project_entry(project: str) -> Dict[str, Any]:
    """Color entry for a project, assigning the next unused palette color the first time."""
    if not project:
        return {}
    with _locked(PROJECT_COLORS_FILE):
        data = _read(PROJECT_COLORS_FILE)
        migrated = _migrate(data)
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
            migrated = True
        if migrated:
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
def flag_pick(seed: str, chance: float) -> bool:
    """Whether the tab with id `seed` gets the flag. The same id always gets the same answer."""
    if not seed or chance <= 0:
        return False
    roll = int(hashlib.sha1(("flag:" + seed).encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    return roll < chance


def build_spec(project: str, status_color: Optional[str], override: Optional[Dict[str, Any]] = None,
               config: Optional[Dict[str, Any]] = None, seed: str = "") -> Optional[Dict[str, Any]]:
    """The background spec for one tab.

    Default (`tab_pattern: "project-status"`): two-tone, project color on the left and the
    agent/status color on the right; solid project color when there is no status color.
    A per-tab override (from `konsole-aware tab-style`) or a per-project pattern wins.
    Otherwise a tab whose id (`seed`) comes up in the `flag_chance` roll gets the flag.
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
        if override["pattern"] == "flag":
            colors = list(FLAG)
        elif not colors and override["pattern"] != "rainbow":
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
    elif pattern == "flag":
        spec = {"pattern": "flag", "colors": list(FLAG)}
    else:
        spec = {"pattern": pattern, "colors": [c for c in (project_color, status_color) if c]}
    if not (override and override.get("pattern")) and not entry.get("pattern") \
            and flag_pick(seed, float(config.get("flag_chance", 0.1) or 0)):
        spec = {"pattern": "flag", "colors": list(FLAG)}
    if spec["pattern"] == "flag":
        spec["colors"] = list(FLAG)
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


def _to_oklab(hex_color: str) -> Tuple[float, float, float]:
    c = hex_color.lstrip("#")
    r, g, b = (int(c[i:i + 2], 16) / 255 for i in (0, 2, 4))
    r, g, b = ((v / 12.92) if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in (r, g, b))
    l = (0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b) ** (1 / 3)
    m = (0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b) ** (1 / 3)
    s = (0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b) ** (1 / 3)
    return (0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s,
            1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
            0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s)


def _from_oklab(lab: Tuple[float, float, float]) -> Optional[str]:
    """Hex for an OKLab color, or None when it falls outside sRGB."""
    L, a, b = lab
    l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3
    m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3
    s = (L - 0.0894841775 * a - 1.2914855480 * b) ** 3
    rgb = (4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
           -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
           -0.0041960863 * l - 0.7034186147 * m + 1.7076127010 * s)
    if any(v < -0.002 or v > 1.002 for v in rgb):
        return None
    out = []
    for v in rgb:
        v = min(1.0, max(0.0, v))
        v = 12.92 * v if v <= 0.0031308 else 1.055 * v ** (1 / 2.4) - 0.055
        out.append(round(v * 255))
    return "#%02x%02x%02x" % tuple(out)


def _shift(hex_color: str, amount: float = 0.1) -> str:
    """A neighboring color for patterns given only one color: the hue turned by `amount`
    of a full turn at the same lightness, so the pair blends cleanly."""
    L, a, b = _to_oklab(hex_color)
    chroma = math.hypot(a, b)
    hue = math.atan2(b, a) + amount * 2 * math.pi
    while chroma > 0.005:  # pull the chroma in until the color fits in sRGB
        out = _from_oklab((L, chroma * math.cos(hue), chroma * math.sin(hue)))
        if out:
            return out
        chroma -= 0.005
    return _from_oklab((L, 0.0, 0.0)) or hex_color


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
        publish(session_key, build_spec(project, status_color, override, seed=session_key))
    except OSError:
        pass
