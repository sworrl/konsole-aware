"""Tab grouping, anchoring, and project-based organization management."""

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from konsole_aware.config import load_config
from konsole_aware.terminal.colors import resolve_color_hex, get_color_tag

GROUPS_FILE = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "konsole-aware" / "groups.json"


class TabGroupManager:
    """Manages tab groupings, anchoring (pinning), and group color definitions."""

    def __init__(self):
        self.config = load_config()
        GROUPS_FILE.parent.mkdir(parents=True, exist_ok=True)
        self.groups_file = GROUPS_FILE

    def _load_data(self) -> Dict[str, Any]:
        if not self.groups_file.exists():
            return {
                "groups": {
                    "agents": {"color": "#8b5cf6", "icon": "🤖"},
                    "backend": {"color": "#3b82f6", "icon": "⚙️"},
                    "frontend": {"color": "#ec4899", "icon": "🎨"},
                    "ops": {"color": "#f59e0b", "icon": "🛠️"},
                    "transfers": {"color": "#10b981", "icon": "📥"},
                },
                "session_groups": {},   # session_key -> group_name
                "anchored_sessions": [], # list of session_keys
            }
        try:
            with open(self.groups_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {"groups": {}, "session_groups": {}, "anchored_sessions": []}

    def _save_data(self, data: Dict[str, Any]) -> None:
        try:
            tmp = self.groups_file.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            tmp.replace(self.groups_file)
        except Exception:
            pass

    def define_group(
        self,
        name: str,
        color: Optional[str] = None,
        icon: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Define or update a group definition with color and icon."""
        data = self._load_data()
        group_key = name.strip().lower()
        grp = data.setdefault("groups", {}).get(group_key, {})
        if color:
            grp["color"] = resolve_color_hex(color)
        elif "color" not in grp:
            grp["color"] = "#3b82f6"
        if icon:
            grp["icon"] = icon.strip()
        data["groups"][group_key] = grp
        self._save_data(data)
        return grp

    def assign_tab(self, session_key: str, group_name: str) -> None:
        """Assign a session to a group."""
        data = self._load_data()
        g_name = group_name.strip().lower()
        if g_name not in data.get("groups", {}):
            self.define_group(g_name)
            data = self._load_data()
        data.setdefault("session_groups", {})[session_key] = g_name
        self._save_data(data)

    def unassign_tab(self, session_key: str) -> None:
        """Remove a session from any group."""
        data = self._load_data()
        if session_key in data.get("session_groups", {}):
            del data["session_groups"][session_key]
            self._save_data(data)

    def get_tab_group(self, session_key: str) -> Optional[Dict[str, Any]]:
        """Get group details for a session."""
        data = self._load_data()
        g_name = data.get("session_groups", {}).get(session_key)
        if not g_name:
            return None
        grp = data.get("groups", {}).get(g_name, {"color": "#3b82f6", "icon": "📁"})
        return {"name": g_name, **grp}

    def anchor_tab(self, session_key: str) -> None:
        """Mark a session as anchored (pinned)."""
        data = self._load_data()
        anchored = data.setdefault("anchored_sessions", [])
        if session_key not in anchored:
            anchored.append(session_key)
            self._save_data(data)

    def unanchor_tab(self, session_key: str) -> None:
        """Unmark a session as anchored."""
        data = self._load_data()
        anchored = data.get("anchored_sessions", [])
        if session_key in anchored:
            anchored.remove(session_key)
            self._save_data(data)

    def is_anchored(self, session_key: str) -> bool:
        """Check if a session is anchored."""
        data = self._load_data()
        return session_key in data.get("anchored_sessions", [])

    def list_groups(self) -> Dict[str, Any]:
        """List all groups and their assigned sessions."""
        data = self._load_data()
        groups_info = dict(data.get("groups", {}))
        for g_name in groups_info:
            groups_info[g_name]["sessions"] = [
                s_key
                for s_key, grp in data.get("session_groups", {}).items()
                if grp == g_name
            ]
        return groups_info

    def get_all_anchored(self) -> List[str]:
        """Return list of all anchored session keys."""
        data = self._load_data()
        return list(data.get("anchored_sessions", []))

    def format_title(
        self,
        session_key: str,
        base_title: str,
        include_color_tag: bool = True,
    ) -> str:
        """Create a composite tab title with anchor glyph, group tag, and color indicators."""
        parts = []

        # 1. Anchored prefix
        if self.is_anchored(session_key):
            parts.append("📌")

        # 2. Group prefix
        grp = self.get_tab_group(session_key)
        if grp:
            g_icon = grp.get("icon", "")
            g_name = grp["name"].capitalize()
            parts.append(f"[{g_icon} {g_name}]".strip() if g_icon else f"[{g_name}]")
            if include_color_tag and "color" in grp:
                parts.append(get_color_tag(grp["color"]))

        # 3. Base title content
        parts.append(base_title)

        return " ".join(parts).strip()
