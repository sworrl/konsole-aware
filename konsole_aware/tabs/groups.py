"""Tab groups, pinned (anchored) tabs, and grouping tabs by project."""

import copy
import fcntl
import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional

from konsole_aware.config import config_home, load_config
from konsole_aware.terminal.colors import resolve_color_hex, get_color_tag

GROUPS_FILE = config_home() / "konsole-aware" / "groups.json"

DEFAULT_GROUPS: Dict[str, Dict[str, str]] = {
    "agents": {"color": "#8b5cf6", "icon": "🤖"},
    "backend": {"color": "#3b82f6", "icon": "⚙️"},
    "frontend": {"color": "#ec4899", "icon": "🎨"},
    "ops": {"color": "#f59e0b", "icon": "🛠️"},
    "transfers": {"color": "#10b981", "icon": "📥"},
}


class TabGroupManager:
    """Manages tab groupings, anchoring (pinning), and group color definitions."""

    def __init__(self):
        self.config = load_config()
        GROUPS_FILE.parent.mkdir(parents=True, exist_ok=True)
        self.groups_file = GROUPS_FILE

    def _load_data(self) -> Dict[str, Any]:
        """Load groups.json. Raises ValueError if the file exists but is corrupt, so a
        mutation never replaces the user's groups and pins with an empty document."""
        data: Dict[str, Any] = {
            "groups": copy.deepcopy(DEFAULT_GROUPS),
            "session_groups": {},    # session_key -> group_name
            "anchored_sessions": [], # list of session_keys
        }
        try:
            with open(self.groups_file, "r", encoding="utf-8") as f:
                loaded = json.load(f)
        except FileNotFoundError:
            return data
        if not isinstance(loaded, dict):
            raise ValueError(f"{self.groups_file} is not a JSON object")
        data.update(loaded)
        data["groups"] = {**copy.deepcopy(DEFAULT_GROUPS), **(loaded.get("groups") or {})}
        return data

    def _read(self) -> Dict[str, Any]:
        try:
            return self._load_data()
        except (OSError, ValueError):
            return {"groups": copy.deepcopy(DEFAULT_GROUPS), "session_groups": {}, "anchored_sessions": []}

    def _save_data(self, data: Dict[str, Any]) -> None:
        tmp = self.groups_file.with_name(f".groups.json.tmp.{os.getpid()}")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        tmp.replace(self.groups_file)

    @contextmanager
    def _locked(self) -> Iterator[None]:
        fd = os.open(self.groups_file.with_name(".groups.lock"), os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    def _mutate(self, fn: Callable[[Dict[str, Any]], Any]) -> Any:
        with self._locked():
            data = self._load_data()
            result = fn(data)
            self._save_data(data)
            return result

    def define_group(
        self,
        name: str,
        color: Optional[str] = None,
        icon: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Define or update a group definition with color and icon."""
        group_key = name.strip().lower()
        if not group_key:
            raise ValueError("group name must not be empty")

        def _apply(data: Dict[str, Any]) -> Dict[str, Any]:
            grp = dict(data.setdefault("groups", {}).get(group_key, {}))
            if color:
                grp["color"] = resolve_color_hex(color)
            elif "color" not in grp:
                grp["color"] = "#3b82f6"
            if icon:
                grp["icon"] = icon.strip()
            data["groups"][group_key] = grp
            return grp

        return self._mutate(_apply)

    def assign_tab(self, session_key: str, group_name: str) -> None:
        """Assign a session to a group."""
        g_name = group_name.strip().lower()
        if not g_name:
            raise ValueError("group name must not be empty")

        def _apply(data: Dict[str, Any]) -> None:
            data.setdefault("groups", {}).setdefault(g_name, {"color": "#3b82f6"})
            data.setdefault("session_groups", {})[session_key] = g_name

        self._mutate(_apply)

    def unassign_tab(self, session_key: str) -> None:
        """Remove a session from any group."""
        self._mutate(lambda data: data.setdefault("session_groups", {}).pop(session_key, None))

    def get_tab_group(self, session_key: str) -> Optional[Dict[str, Any]]:
        """Get group details for a session."""
        data = self._read()
        g_name = data.get("session_groups", {}).get(session_key)
        if not g_name:
            return None
        grp = data.get("groups", {}).get(g_name, {"color": "#3b82f6", "icon": "📁"})
        return {"name": g_name, **grp}

    def anchor_tab(self, session_key: str) -> None:
        """Mark a session as anchored (pinned)."""
        def _apply(data: Dict[str, Any]) -> None:
            anchored = data.setdefault("anchored_sessions", [])
            if session_key not in anchored:
                anchored.append(session_key)

        self._mutate(_apply)

    def unanchor_tab(self, session_key: str) -> None:
        """Unmark a session as anchored."""
        def _apply(data: Dict[str, Any]) -> None:
            anchored = data.setdefault("anchored_sessions", [])
            if session_key in anchored:
                anchored.remove(session_key)

        self._mutate(_apply)

    def is_anchored(self, session_key: str) -> bool:
        """Check if a session is anchored."""
        return session_key in self._read().get("anchored_sessions", [])

    def list_groups(self) -> Dict[str, Any]:
        """List all groups and their assigned sessions."""
        data = self._read()
        groups_info = {k: dict(v) for k, v in data.get("groups", {}).items()}
        for g_name in groups_info:
            groups_info[g_name]["sessions"] = [
                s_key
                for s_key, grp in data.get("session_groups", {}).items()
                if grp == g_name
            ]
        return groups_info

    def get_all_anchored(self) -> List[str]:
        """Return list of all anchored session keys."""
        return list(self._read().get("anchored_sessions", []))

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
