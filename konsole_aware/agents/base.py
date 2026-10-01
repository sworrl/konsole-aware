"""Shared lifecycle handling for AI agent hooks (Claude Code, Antigravity agy, ...)."""

import os
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

from konsole_aware.agents import identity as agent_identity
from konsole_aware.config import load_config
from konsole_aware.notify.notifier import Notifier
from konsole_aware.state.manager import StateManager
from konsole_aware.tabs.title import TitleComposer
from konsole_aware.terminal import get_terminal
from konsole_aware.terminal.base import BaseTerminal

# Status -> (tab colour key, badge text)
STATUS_STYLE = {
    "thinking": ("busy", "THINK"),
    "tool": ("busy", "TOOL"),
    "busy": ("busy", "BUSY"),
    "subagents": ("busy", "AGENTS"),
    "compacting": ("busy", "COMPACT"),
    "waiting": ("waiting", "WAIT"),
    "done": ("done", "DONE"),
    "ready": ("done", "READY"),
    "error": ("error", "ERR"),
    "blocked": ("blocked", "BLOCKED"),
    "limited": ("limited", "LIMIT"),
    "idle": ("idle", "READY"),
}

# Statuses during which an agent turn is in progress.
ACTIVE_STATUSES = ("thinking", "tool", "busy", "subagents", "compacting", "waiting")


def format_duration(seconds: float) -> str:
    mins, secs = divmod(int(max(0, seconds)), 60)
    hours, mins = divmod(mins, 60)
    if hours:
        return f"{hours}h {mins}m"
    return f"{mins}m {secs}s" if mins else f"{secs}s"


def short(text: Any, limit: int = 28) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


class BaseAgentHandler(ABC):
    """Base handler: owns the terminal, state and rendering for one agent hook call."""

    agent_name = ""
    display_name = ""

    def __init__(self, term: Optional[BaseTerminal] = None):
        self.config = load_config()
        self.notifier = Notifier()
        self.state_mgr = StateManager()
        self.composer = TitleComposer()
        self._term = term

    @property
    def term(self) -> BaseTerminal:
        if self._term is None:
            self._term = get_terminal()
        return self._term

    @property
    def session_key(self) -> str:
        return self.term.get_session_id()

    @abstractmethod
    def handle_event(self, event_name: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Handle lifecycle hook event and return response payload."""

    # ------------------------------------------------------------------ state
    def attach(self, mode: Optional[str] = None) -> Dict[str, Any]:
        """Record that this agent runs in the session; detect a newly started agent process.

        Returns the session state *before* this call, plus 'new_agent': True when this
        is the first hook from a new agent process in this tab.
        """
        _, agent_pid = agent_identity.find_agent_ancestor(self.agent_name)
        ancestors = agent_identity.ancestor_pids()
        argv_mode = agent_identity.agent_mode_from_pid(agent_pid) if mode is None else None
        before: Dict[str, Any] = {}

        def _apply(sess: Dict[str, Any]) -> Optional[Dict[str, Any]]:
            before.update(sess)
            owner = sess.get("agent_pid")
            if (owner and agent_pid and owner != agent_pid and owner in ancestors
                    and agent_identity.pid_alive(owner)):
                # A claude/agy started *by* the tab's agent (e.g. `claude -p` in a tool
                # call): its hooks must not take over the parent's tab.
                before["nested"] = True
                return None
            mode_now = mode
            if mode_now is None:
                same_agent = sess.get("agent") == self.agent_name and sess.get("agent_pid") == agent_pid
                mode_now = sess.get("agent_mode") if same_agent and sess.get("agent_mode") else argv_mode
            updates: Dict[str, Any] = {"agent": self.agent_name, "hooked": True}
            if agent_pid:
                updates["agent_pid"] = agent_pid
            if mode_now:
                updates["agent_mode"] = mode_now
            new_agent = sess.get("agent") != self.agent_name or (
                agent_pid and sess.get("agent_pid") != agent_pid
            )
            if new_agent:
                updates.update({"agent_started_at": time.time(), "subagents": [], "status": "idle",
                                "model": None, "effort": None})
                before["status"] = "idle"
            before["new_agent"] = bool(new_agent)
            return updates

        self.state_mgr.update_session(self.session_key, _apply)
        return before

    def begin_turn_if_idle(self, prev_status: Optional[str]) -> None:
        """Start the turn timer when moving from an idle/finished state into activity."""
        if prev_status not in ACTIVE_STATUSES:
            self.state_mgr.set_session(self.session_key, turn_start=time.time(), start_time=time.time())

    def detach(self) -> None:
        """The agent exited: forget it so the tab reads as a plain user session again."""
        self.state_mgr.update_session(
            self.session_key,
            lambda _s: {
                "agent": None,
                "agent_pid": None,
                "agent_mode": None,
                "agent_status": None,
                "detail": "",
                "turn_start": None,
                "subagents": [],
                "status": "idle",
                "task_type": "command",
                "task_name": "",
            },
        )

    # ------------------------------------------------------------- rendering
    def render(
        self,
        status: str,
        detail: str = "",
        cwd: Optional[str] = None,
        mode: Optional[str] = None,
        color_status: Optional[str] = None,
        icon_status: Optional[str] = None,
        **state: Any,
    ) -> str:
        """Update state, tab title, colour and badge for this agent's current status."""
        updates: Dict[str, Any] = {
            "status": status if status in ACTIVE_STATUSES or status in ("done", "error", "blocked", "limited", "idle") else "busy",
            "agent_status": status,
            "task_name": self.agent_name,
            "task_type": "agent",
            "detail": detail,
            **state,
        }
        if cwd:
            updates["cwd"] = cwd
        self.state_mgr.set_session(self.session_key, **updates)
        title = self.composer.compose(
            session_key=self.session_key,
            task_category="agent",
            status=icon_status or status,
            detail=detail,
            cwd=cwd,
            agent=self.agent_name,
            mode=mode,
        )
        color_key, badge = STATUS_STYLE.get(color_status or status, STATUS_STYLE["busy"])
        colors = self.config.get("colors", {})
        color = colors.get(color_key, "#f59e0b")
        ident = agent_identity.identity(self.agent_name)
        tab_mode = self.config.get("tab_color_mode", "agent")
        if tab_mode == "agent" and color_key not in ("waiting", "error", "blocked", "limited"):
            color = ident.get("color", color)
        elif tab_mode == "agent_status" and color_key not in ("waiting", "error", "blocked", "limited", "done"):
            color = ident.get("color", color)
        sess = self.state_mgr.get_session(self.session_key)
        m_tag = agent_identity.model_tag(sess.get("model"), sess.get("effort"))
        label = f"{ident.get('label', '')} {m_tag}".strip()
        badge_text = f"{label} · {badge}"
        self._apply_ui(sess, status, title, color, badge_text, ident.get("icon", ""))
        return title

    # Tool-call titles change several times a second while an agent works. Each change on
    # a window's active tab also changes the window title, which the Plasma taskbar
    # re-renders, so routine "busy" detail updates are throttled and unchanged values
    # are never re-sent. State changes (waiting/done/error/...) always go through.
    _THROTTLED = ("tool",)

    def _apply_ui(self, sess: Dict[str, Any], status: str, title: str, color: str, badge: str, icon: str) -> None:
        last = sess.get("ui") or {}
        now = time.time()
        min_gap = float(self.config.get("title_update_min_interval", 1.0))
        if (status in self._THROTTLED and last.get("status") == status
                and now - last.get("at", 0) < min_gap and last.get("title") != title):
            return  # same busy state, just a new tool detail: wait for the next one
        if last.get("title") != title:
            self.term.set_title(title, role=0)
        if last.get("color") != color:
            self.term.set_tab_color(color)
        if last.get("badge") != badge:
            self.term.set_badge(badge, enabled=True, icon=icon)
        self.state_mgr.set_session(self.session_key, ui={
            "status": status, "title": title, "color": color, "badge": badge, "at": now})

    def render_user_session(self, cwd: Optional[str] = None) -> None:
        """Show the tab as a plain user shell (no agent)."""
        title = self.composer.compose(
            session_key=self.session_key,
            task_category="idle",
            status="idle",
            detail="",
            cwd=cwd,
            agent=agent_identity.USER,
        )
        self.term.set_title(title, role=0)
        self.state_mgr.set_session(self.session_key, ui=None)
        self.term.reset_tab_color()
        self.term.set_badge("", enabled=False)

    # ---------------------------------------------------------------- alerts
    def session_label(self) -> str:
        num = getattr(self.term, "get_session_num", lambda: None)()
        return f"Tab {num}" if num is not None else "Terminal"

    def alert(self, title: str, message: str, urgency: str = "normal", is_error: bool = False,
              dedupe_key: Optional[str] = None) -> None:
        """Desktop notification, suppressed while the tab is focused (if configured).

        dedupe_key suppresses repeats of the same alert within 15 s (e.g. Claude sends
        both PermissionRequest and a permission_prompt Notification for one prompt).
        """
        if dedupe_key:
            sess = self.state_mgr.get_session(self.session_key)
            last = sess.get("last_alert") or {}
            if last.get("key") == dedupe_key and time.time() - last.get("at", 0) < 15:
                return
            self.state_mgr.set_session(self.session_key, last_alert={"key": dedupe_key, "at": time.time()})
        if self.config.get("alert_only_unfocused", True) and self.term.is_focused():
            return
        self.notifier.notify(title=title, message=message, urgency=urgency, is_error=is_error, play_sound=True)

    def turn_duration(self) -> str:
        sess = self.state_mgr.get_session(self.session_key)
        start = sess.get("turn_start") or sess.get("start_time")
        return format_duration(time.time() - start) if start else ""

    # --------------------------------------------------------------- watchdog
    def start_watchdog(self) -> None:
        """Start (once per tab) a detached watcher that ends the turn when no hook will:
        Esc interrupts (Claude fires no Stop) and agents that exit without SessionEnd."""
        if os.environ.get("KONSOLE_AWARE_TERMINAL") == "null" or not self.config.get("agent_watchdog", True):
            return
        from konsole_aware.agents import watchdog
        watchdog.spawn(self.session_key, self.agent_name)

    # --------------------------------------------------------------- snapping
    def maybe_auto_snap(self) -> None:
        """Move this tab next to its project's other tabs when a new agent starts here."""
        if not self.config.get("auto_snap_agents", True):
            return
        service = getattr(self.term, "service", None)
        num = getattr(self.term, "get_session_num", lambda: None)()
        if not service or num is None:
            return
        try:
            from konsole_aware.tabs.organizer import TabOrganizer
            TabOrganizer(service=service).snap_session(str(num), require_current=True)
        except Exception:
            pass
