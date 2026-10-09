"""Builds tab titles: project, agent, model, mode, task dot and status."""

import os
from pathlib import Path
from typing import Any, Dict, Optional

from konsole_aware.agents import identity as agent_identity
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

# Order of title segments. Project first, so each tab reads as WHAT is being worked on,
# then WHO is working on it (agent, model, mode, background activity), then the state.
DEFAULT_TITLE_ORDER = ["job", "pin", "group", "identity", "model", "mode", "activity", "dot", "status"]

STATUS_ICONS = {
    "thinking": "🧠",
    "tool": "🛠️",
    "busy": "⏳",
    "done": "✅",
    "error": "❌",
    "waiting": "❓",
    "idle": "💤",
    "compacting": "🗜️",
    "blocked": "🛡️",
    "limited": "⌛",
    "background": "⧗",
    "subagents": "🤖",
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
            # The agent session's own name (Claude /rename or --name, agy --project), while
            # that agent is still running in the tab.
            if sess and sess.get("session_name") and sess.get("agent"):
                return sess["session_name"]

        source = self.config.get("job_name_source", "auto")
        if source == "cwd":
            try:
                return Path(cwd or ".").resolve().name
            except Exception:
                return "term"
        if source == "git":
            return find_project_name(cwd)

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
        agent: Optional[str] = None,
        mode: Optional[str] = None,
        order: Optional[list] = None,
    ) -> str:
        """
        Compose full tab title, segments in `title_order` (project first by default):
        Example: [🐍 konsole_aware] ✳️ CLAUDE Opus 5.5·hi 🟠 🧠 thinking...
        Example: [🧪 konsole_aware/tests] 🪐 AGY ⚡ BYPASS 🔵 🛠️ cmd(cargo test)
        Example: [🦀 indexer] 🟡 ✅ cargo build (42s)
        """
        always_show_job = self.config.get("always_show_job", True)
        show_task_dot = self.config.get("show_task_dot", True)
        enable_icons = self.config.get("enable_project_icons", True)
        show_identity = self.config.get("show_agent_identity", True)
        show_mode = self.config.get("show_agent_mode", True)

        sess = self.state_mgr.get_session(session_key) if session_key else {}
        if agent is None:
            agent = sess.get("agent") or agent_identity.USER
        if mode is None and agent != agent_identity.USER:
            mode = sess.get("agent_mode")

        resolved_job = self.get_job_name(session_key=session_key, cwd=cwd, explicit_job=job_name)
        resolved_icon = (
            self.icon_resolver.resolve(session_key=session_key, cwd=cwd, explicit_icon=icon)
            if enable_icons
            else ""
        )
        status_icon = self.get_status_icon(status)

        # Clean and compact detail (e.g. 'ready in 55s' -> 'ready (55s)', 'ready in 3h 44m' -> 'ready (3h 44m)')
        clean_detail = detail.strip() if detail else ""
        if clean_detail.startswith("ready in "):
            clean_detail = f"ready ({clean_detail[9:].strip()})"

        # Limit overly long project names
        max_job = self.config.get("max_job_length", 16)
        if resolved_job and len(resolved_job) > max_job:
            resolved_job = resolved_job[:max_job - 1] + "…"

        seg: Dict[str, str] = {}

        # Project / job: read first, so you can see WHAT each tab is working on.
        if always_show_job and (resolved_job or resolved_icon):
            if resolved_icon and resolved_job:
                seg["job"] = f"[{resolved_icon} {resolved_job}]"
            else:
                seg["job"] = f"[{resolved_job or resolved_icon}]"

        if session_key and self.group_mgr.is_anchored(session_key):
            seg["pin"] = "📌"

        if session_key:
            grp = self.group_mgr.get_tab_group(session_key)
            if grp:
                g_icon = grp.get("icon", "")
                g_name = grp["name"].capitalize()
                seg["group"] = f"[{g_icon} {g_name}]".strip() if g_icon else f"[{g_name}]"

        # Agent identity (✳️ CLAUDE / 🪐 AGY / 👤 USER), model·effort and mode
        is_agent = agent != agent_identity.USER
        show_user_tag = self.config.get("show_user_identity", False)
        if show_identity and (is_agent or show_user_tag or task_category == "idle"):
            seg["identity"] = agent_identity.identity_tag(agent)
        if is_agent:
            seg["model"] = agent_identity.model_tag(sess.get("model"), sess.get("effort"))
            if show_mode:
                seg["mode"] = agent_identity.mode_tag(mode)
            seg["activity"] = agent_identity.activity_tag(sess)

        # Task colored dot: distinct agent dot (🟠 Claude, 🔵 AGY) or category dot
        if show_task_dot and task_category != "idle":
            seg["dot"] = agent_identity.agent_dot(agent) if is_agent else self.get_task_dot(task_category)

        # Status icon + detail
        if clean_detail:
            first_char = clean_detail[0]
            if any(clean_detail.startswith(ic) for ic in STATUS_ICONS.values()) or ord(first_char) > 1000:
                seg["status"] = clean_detail
            else:
                seg["status"] = f"{status_icon} {clean_detail}".strip()
        else:
            seg["status"] = status_icon

        if order is None:
            order = self.config.get("title_order") or DEFAULT_TITLE_ORDER
            if not isinstance(order, list):
                order = DEFAULT_TITLE_ORDER
            # Segments missing from a custom order still show, at the end, so nothing silently disappears.
            order = list(order) + [k for k in DEFAULT_TITLE_ORDER if k not in order]
        parts = [seg.get(k, "") for k in order]

        return " ".join(p for p in parts if p).strip()
