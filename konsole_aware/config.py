"""Configuration manager for konsole-aware."""

import json
import os
from pathlib import Path
from typing import Any, Dict

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "konsole-aware"
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
    "colors": {
        "busy": "#f59e0b",       # Amber: active execution / thinking / transfer
        "done": "#10b981",       # Emerald: task finished / ready
        "error": "#ef4444",      # Red: command failed / error
        "waiting": "#8b5cf6",    # Purple: waiting for user input / permission
        "idle": "#3b82f6",       # Blue: interactive prompt / idle
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
        "generic": "⏳",
    },
    "always_show_job": True,
    "job_name_source": "auto",       # "auto" (git root or cwd), "git", "cwd"
    "show_subfolder_in_title": True, # Append subfolder e.g. [🧪 myproject/tests]
    "enable_project_icons": True,    # Display auto-detected or assigned folder/project icons
    "enable_badge_icons": True,      # Prefix folder/project icon next to top-right badge text
    "default_icon": "📁",            # Fallback icon if no specific folder/project icon matched
    "folder_icons": {},              # Custom user folder name -> icon overrides
    "project_file_icons": {},        # Custom user project file marker -> icon overrides
    "show_task_dot": True,
    "tab_color_mode": "status",      # "status", "task", "both"
    "task_dots": {
        "agent": "🟣",
        "transfer": "🟠",
        "download": "🟤",
        "build": "🟡",
        "test": "🟢",
        "git": "🔵",
        "container": "🟦",
        "archive": "📦",
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
        "command": "#6b7280",
        "idle": "#000000",
    },
    "known_agents": ["agy", "claude", "aider", "copilot", "cursor", "gemini", "openhands"],
    "known_transfers": ["rsync", "scp", "sftp", "rclone", "ftp", "bcp"],
    "known_downloads": ["curl", "wget", "aria2c", "axel", "yt-dlp"],
    "known_builds": [
        "cargo", "make", "ninja", "cmake", "go", "mvn", "gradle",
        "npm", "yarn", "pnpm", "rustc", "gcc", "clang", "g++"
    ]
}


def load_config() -> Dict[str, Any]:
    """Load configuration from ~/.config/konsole-aware/config.json with fallback to defaults."""
    if not CONFIG_FILE.exists():
        save_config(DEFAULT_CONFIG)
        return dict(DEFAULT_CONFIG)

    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            user_config = json.load(f)
        # Deep merge with defaults so new keys are always populated
        merged = dict(DEFAULT_CONFIG)
        for k, v in user_config.items():
            if isinstance(v, dict) and k in merged and isinstance(merged[k], dict):
                merged[k] = {**merged[k], **v}
            else:
                merged[k] = v
        return merged
    except Exception:
        return dict(DEFAULT_CONFIG)


def save_config(config_data: Dict[str, Any]) -> None:
    """Save configuration to disk."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config_data, f, indent=2)
