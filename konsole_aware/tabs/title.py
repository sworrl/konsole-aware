"""Tab title composer supporting permanent job/project context, task color dots, and status indicators."""

import os
from pathlib import Path
from typing import Any, Dict, Optional

from konsole_aware.config import load_config
from konsole_aware.state.manager import StateManager
from konsole_aware.tabs.groups import TabGroupManager
from konsole_aware.tabs.icons import IconResolver

DEFAULT_TASK_DOTS = {
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
}

STATUS_ICONS = {
    "thinking": "🧠",
    "tool": "🛠️",
    "busy": "⏳",
    "done": "✅",
    "error": "❌",
    "waiting": "❓",
    "idle": "💤",
}


def find_project_name(path: Optional[str] = None) -> str:
    """Find the root project name by searching for .git upwards, or fallback to folder name."""
    try:
        cur = Path(path or ".").resolve()
        for parent in [cur] + list(cur.parents):
            if (parent / ".git").exists():
                return parent.name
        # Fallback to current folder name unless root or home
        name = cur.name
        if name in ("", "/", "root"):
            return "root"
        return name
    except Exception:
        return "term"


class TitleComposer:
    """Composes unified tab titles with job name, task color dot, and status."""

    def __init__(self):
        self.config = load_config()
        self.state_mgr = StateManager()
        self.group_mgr = TabGroupManager()
        self.icon_resolver = IconResolver()

    def get_job_name(
        self,
        session_key: Optional[str] = None,
        cwd: Optional[str] = None,
        explicit_job: Optional[str] = None,
    ) -> str:
        """Resolve the job or project name to display."""
        if explicit_job and explicit_job.strip():
            return explicit_job.strip()

        if session_key:
            sess = self.state_mgr.get_session(session_key)
            if sess and sess.get("job_name"):
                return sess["job_name"]

        source = self.config.get("job_name_source", "auto")
        if source == "cwd":
            try:
                return Path(cwd or ".").resolve().name
            except Exception:
                return "term"

        try:
            cur = Path(cwd or ".").resolve()
        except Exception:
            return "term"

        root = self.icon_resolver.find_project_root(cur)
        root_name = root.name if root.name not in ("", "/", "root") else "root"

        if self.config.get("show_subfolder_in_title", True) and cur != root:
            try:
                rel = cur.relative_to(root)
                if rel.parts:
                    sub = rel.parts[0] if len(rel.parts) == 1 else f"{rel.parts[0]}/{rel.parts[-1]}"
                    return f"{root_name}/{sub}"
            except ValueError:
                pass

        return root_name

    def get_task_dot(self, task_category: str) -> str:
        """Get the colored dot emoji for the task category."""
        dots = self.config.get("task_dots", DEFAULT_TASK_DOTS)
        cat = task_category.lower().strip()
        return dots.get(cat, dots.get("command", "⚪"))

    def get_status_icon(self, status: str) -> str:
        """Get icon for execution status."""
        st = status.lower().strip()
        return STATUS_ICONS.get(st, "⏳")

    def compose(
        self,
        session_key: Optional[str] = None,
        job_name: Optional[str] = None,
        task_category: str = "command",
        status: str = "busy",
        detail: str = "",
        cwd: Optional[str] = None,
        icon: Optional[str] = None,
    ) -> str:
        """
        Compose full tab title:
        Example: [🐍 konsole_aware] 🟣 🧠 agy: thinking...
        Example: [🧪 konsole_aware/tests] 🟣 🛠️ agy: cmd(...)
        Example: [🟠 rsync] ⏳ rsync -> backup
        Example: [🟡 cargo] ✅ cargo build (42s)
        """
        always_show_job = self.config.get("always_show_job", True)
        show_task_dot = self.config.get("show_task_dot", True)
        enable_icons = self.config.get("enable_project_icons", True)

        resolved_job = self.get_job_name(session_key=session_key, cwd=cwd, explicit_job=job_name)
        resolved_icon = (
            self.icon_resolver.resolve(session_key=session_key, cwd=cwd, explicit_icon=icon)
            if enable_icons
            else ""
        )
        task_dot = self.get_task_dot(task_category) if show_task_dot else ""
        status_icon = self.get_status_icon(status)

        parts = []

        # 1. Anchored pin
        if session_key and self.group_mgr.is_anchored(session_key):
            parts.append("📌")

        # 2. Group tag if assigned
        if session_key:
            grp = self.group_mgr.get_tab_group(session_key)
            if grp:
                g_icon = grp.get("icon", "")
                g_name = grp["name"].capitalize()
                parts.append(f"[{g_icon} {g_name}]".strip() if g_icon else f"[{g_name}]")

        # 3. Permanent Job / Project Context with Icon
        if always_show_job and (resolved_job or resolved_icon):
            if resolved_icon and resolved_job:
                parts.append(f"[{resolved_icon} {resolved_job}]")
            elif resolved_job:
                parts.append(f"[{resolved_job}]")
            elif resolved_icon:
                parts.append(f"[{resolved_icon}]")

        # 4. Task Colored Dot
        if task_dot:
            parts.append(task_dot)

        # 5. Status icon + detail
        if detail:
            # If detail already has status icon (e.g. '🧠 ...' or '✅ ...'), don't duplicate
            if any(detail.startswith(ic) for ic in STATUS_ICONS.values()):
                parts.append(detail)
            else:
                parts.append(f"{status_icon} {detail}".strip())
        else:
            parts.append(status_icon)

        return " ".join(parts).strip()
