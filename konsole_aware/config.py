"""Configuration manager for konsole-aware."""

import copy
import json
import os
from pathlib import Path
from typing import Any, Dict


def config_home() -> Path:
    """Return $XDG_CONFIG_HOME, ignoring empty or relative values as the XDG spec requires."""
    xdg = os.environ.get("XDG_CONFIG_HOME", "")
    if xdg and os.path.isabs(xdg):
        return Path(xdg)
    return Path.home() / ".config"


CONFIG_DIR = config_home() / "konsole-aware"
CONFIG_FILE = CONFIG_DIR / "config.json"

DEFAULT_CONFIG: Dict[str, Any] = {
    "version": 1,
    "min_duration_seconds": 3.0,
    "alert_sound": True,
    "alert_sound_file": "/usr/share/sounds/freedesktop/stereo/complete.oga",
    "alert_sound_error_file": "/usr/share/sounds/freedesktop/stereo/dialog-warning.oga",
    "alert_only_unfocused": True,
    "enable_tab_colors": True,
    "enable_badges": True,
    "enable_desktop_notifications": True,
    # Desktop toasts disappear after this many seconds. Plasma never auto-expires
    # "critical" notifications, so attention alerts are sent as "normal" unless
    # sticky_alerts is true (then permission/question/blocked alerts stay until dismissed).
    "notification_timeout_seconds": 4,
    "sticky_alerts": False,
    "title_update_min_interval": 1.0,   # seconds between busy tool-detail title updates per tab
    "notification_burst": 4,             # at most this many toasts ...
    "notification_window_seconds": 20,   # ... per this many seconds (all tabs combined)
    "colors": {
        "busy": "#f59e0b",       # Amber: active execution / thinking / transfer
        "done": "#10b981",       # Emerald: task finished / ready
        "error": "#ef4444",      # Red: command failed / error
        "waiting": "#8b5cf6",    # Purple: waiting for user input / permission
        "idle": "#3b82f6",       # Blue: interactive prompt / idle
        "blocked": "#db2777",    # Pink: response blocked by the model provider's safeguards
        "limited": "#f97316",    # Orange: usage limit reached (resumes when the limit resets)
        "reset": "#000000",      # Default Konsole tab color
    },
    "emojis": {
        "thinking": "🧠",
        "tool": "🛠️",
        "done": "✅",
        "error": "❌",
        "waiting": "❓",
        "idle": "💤",
        "transfer": "📥",
        "build": "🔨",
        "git": "🐙",
        "container": "🐳",
        "archive": "📦",
        "package": "📦",
        "generic": "⏳",
    },
    "always_show_job": True,
    "job_name_source": "auto",       # "auto" (git root, project markers, or cwd), "git", "cwd"
    "show_subfolder_in_title": True, # Append subfolder e.g. [🧪 myproject/tests]
    "enable_project_icons": True,    # Display auto-detected or assigned folder/project icons
    "enable_badge_icons": True,      # Prefix folder/project icon next to top-right badge text
    "default_icon": "📁",            # Fallback icon if no specific folder/project icon matched
    "folder_icons": {},              # Custom user folder name -> icon overrides
    "project_file_icons": {},        # Custom user project file marker -> icon overrides
    "show_task_dot": True,
    # Agent identity: every tab shows which agent runs in it (✳️ CLAUDE, 🪐 AGY, ...)
    # or 👤 USER when it is a plain shell with no agent.
    "show_agent_identity": True,
    "show_agent_mode": True,         # Show permission / execution mode (📋 PLAN, ✏️ EDITS, ⚡ BYPASS, ...)
    "show_agent_model": True,        # Show model and effort (💎 Fable 5.1·hi, Opus 5.5·max, ...)
    "model_icons": {},               # e.g. {"fable": "💎", "opus": "🟣"}; "" hides the icon
    "agent_identities": {},          # Per-agent overrides: {"claude": {"icon": "✳️", "label": "CLAUDE"}}
    "agent_order": ["claude", "agy", "gemini", "codex", "aider", "copilot", "cursor", "opencode"],
    # Snapping: when an agent starts in a tab, move that tab next to the other tabs
    # of the same project in the same window (agents side by side).
    "auto_snap_agents": True,
    "combine_split_tab_titles": True,  # split tab with several agents: one combined title on every pane
    # Patterned tab backgrounds, painted by the context-menu library inside Konsole:
    # "project-status" (two-tone: project color | agent/status color), "project" (solid
    # project color), "gradient", "two-tone", "stripes", "rainbow", "flag", or "none".
    "tab_pattern": "project-status",
    "flag_chance": 0.1,
    "update_check": True,            # look for new releases on GitHub; a notification and a tray icon when one is out
    "update_check_hours": 12,        # how often to look              # share of tabs that get the American flag instead of their colors (0 turns it off)
    "tab_pattern_opacity": 0.6,      # strength of unselected tab fills; the selected tab is drawn at full strength
    "terminal_tint": 0.2,            # tint each terminal's background with its tab's colors (0 turns it off, max 0.5)
    "terminal_band": True,           # a strip of the tab's colors along the top of its terminal
    # Colors belong to the tab and follow it when you drag it. Set true to color tabs by
    # their position instead (first tab blue, second green, ...), so dragging recolors them.
    "tab_colors_follow_position": False,
    "agent_watchdog": True,          # End a turn when the agent is interrupted (Esc) or exits without a hook
    "snap_include_user_sessions": True,  # Pull plain shells of the same project into the cluster too
    "tab_color_mode": "agent",      # "agent": distinct brand color per agent identity; "status": state color; "agent_status": agent color while running, status color on exit
    "compact_tab_titles": True,      # Shorter, glanceable titles when many tabs are open
    "max_job_length": 16,            # Truncate overly long project names in tab titles
    "task_dots": {
        "agent": "🟣",
        "transfer": "🟠",
        "download": "🟤",
        "build": "🟡",
        "test": "🟢",
        "git": "🔵",
        "container": "🟦",
        "archive": "📦",
        "package": "🟦",
        "remote": "🟪",
        "server": "🟩",
        "monitor": "👀",
        "interactive": "✏️",
        "command": "⚪",
        "idle": "⚫",
    },
    "task_colors": {
        "agent": "#8b5cf6",
        "transfer": "#f97316",
        "download": "#06b6d4",
        "build": "#eab308",
        "test": "#84cc16",
        "git": "#3b82f6",
        "container": "#0ea5e9",
        "archive": "#a855f7",
        "package": "#0284c7",
        "command": "#6b7280",
        "idle": "#000000",
    },
    "known_agents": ["agy", "claude", "aider", "copilot", "cursor-agent", "gemini", "codex", "opencode", "openhands"],
    "known_transfers": ["rsync", "scp", "sftp", "rclone", "ftp", "bcp"],
    "known_downloads": ["curl", "wget", "aria2c", "axel", "yt-dlp"],
    "known_packages": [
        "apt", "apt-get", "dpkg", "nala", "aptitude", "pkcon",
        "pacman", "yay", "paru", "pamac",
        "dnf", "yum", "rpm",
        "zypper", "apk",
        "snap", "flatpak", "brew",
        "do-release-upgrade", "neon-upgrade"
    ],
    "known_builds": [
        "cargo", "make", "ninja", "cmake", "go", "mvn", "gradle",
        "npm", "yarn", "pnpm", "rustc", "gcc", "clang", "g++"
    ]
}

_cache: Dict[str, Any] = {}


def _merge(user_config: Dict[str, Any]) -> Dict[str, Any]:
    merged = copy.deepcopy(DEFAULT_CONFIG)
    for k, v in user_config.items():
        if isinstance(v, dict) and isinstance(merged.get(k), dict):
            merged[k] = {**merged[k], **v}
        else:
            merged[k] = v
    return merged


def load_config() -> Dict[str, Any]:
    """Load ~/.config/konsole-aware/config.json merged over the defaults.

    Returns a fresh deep copy each call so callers can mutate it freely. A config
    file that fails to parse is left untouched on disk (defaults are used instead)
    so a typo never gets silently overwritten by a later save.
    """
    try:
        stat = CONFIG_FILE.stat()
    except FileNotFoundError:
        try:
            save_config(DEFAULT_CONFIG)
        except OSError:
            pass
        return copy.deepcopy(DEFAULT_CONFIG)
    except OSError:
        return copy.deepcopy(DEFAULT_CONFIG)

    key = (stat.st_mtime_ns, stat.st_size)
    if _cache.get("key") != key:
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                user_config = json.load(f)
            if not isinstance(user_config, dict):
                raise ValueError("config root is not an object")
            _cache["value"] = _merge(user_config)
            _cache["valid"] = True
        except Exception:
            _cache["value"] = copy.deepcopy(DEFAULT_CONFIG)
            _cache["valid"] = False
        _cache["key"] = key
    return copy.deepcopy(_cache["value"])


def config_is_valid() -> bool:
    """False when the on-disk config exists but could not be parsed."""
    load_config()
    return _cache.get("valid", True)


def save_config(config_data: Dict[str, Any]) -> None:
    """Atomically save configuration to disk.

    Refuses to overwrite an existing config file that failed to parse.
    """
    if CONFIG_FILE.exists() and not config_is_valid():
        raise RuntimeError(f"{CONFIG_FILE} contains invalid JSON; fix it before saving settings")
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_FILE.with_name(f".config.json.tmp.{os.getpid()}")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(config_data, f, indent=2, ensure_ascii=False)
    tmp.replace(CONFIG_FILE)
    _cache.clear()
