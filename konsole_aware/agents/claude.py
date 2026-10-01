"""Claude Code agent hook handler."""

import os
from pathlib import Path
from typing import Any, Dict
from konsole_aware.agents.base import BaseAgentHandler
from konsole_aware.config import load_config
from konsole_aware.terminal import get_terminal
from konsole_aware.notify.notifier import Notifier
from konsole_aware.state.manager import StateManager
from konsole_aware.tabs.title import TitleComposer


class ClaudeHandler(BaseAgentHandler):
    """Integrates with Claude Code lifecycle hooks."""

    def __init__(self):
        self.config = load_config()
        self.notifier = Notifier()
        self.state_mgr = StateManager()
        self.composer = TitleComposer()

    def handle_event(self, event_name: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        term = get_terminal()
        session_id = term.get_session_id()
        colors = self.config.get("colors", {})
        cwd = payload.get("cwd") or os.getcwd()
        cwd_name = Path(cwd).name

        response: Dict[str, Any] = {}

        if event_name in ("UserPromptSubmit", "user_prompt_submit"):
            title = self.composer.compose(
                session_key=session_id,
                task_category="agent",
                status="thinking",
                detail="claude: thinking...",
                cwd=cwd,
            )
            term.set_title(title, role=0)
            term.set_tab_color(colors.get("busy", "#f59e0b"))
            term.set_badge("THINK", enabled=True)
            self.state_mgr.start_task(
                session_key=session_id,
                task_name="claude",
                task_type="agent",
                detail="thinking",
            )
            return {}

        elif event_name in ("PreToolUse", "pre_tool_use"):
            tool_name = payload.get("tool", "") or payload.get("toolCall", {}).get("name", "tool")
            tool_input = payload.get("input", {}) or payload.get("toolCall", {}).get("args", {})

            detail = self._format_tool_detail(tool_name, tool_input)
            title = self.composer.compose(
                session_key=session_id,
                task_category="agent",
                status="tool",
                detail=f"claude: {detail}",
                cwd=cwd,
            )
            term.set_title(title, role=0)
            term.set_tab_color(colors.get("busy", "#f59e0b"))
            term.set_badge("TOOL", enabled=True)

            self.state_mgr.start_task(
                session_key=session_id,
                task_name="claude",
                task_type="agent",
                detail=f"{tool_name}: {detail}",
            )
            return {"decision": "allow"}

        elif event_name in ("PostToolUse", "post_tool_use"):
            if payload.get("error"):
                term.set_tab_color(colors.get("error", "#ef4444"))
                term.set_badge("ERR", enabled=True)
            return {}

        elif event_name in ("Stop", "stop"):
            finished_state = self.state_mgr.finish_task(session_key=session_id, exit_code=0, status="done")
            duration_str = ""
            if "duration" in finished_state:
                dur = int(finished_state["duration"])
                mins, secs = divmod(dur, 60)
                duration_str = f" in {mins}m {secs}s" if mins else f" in {secs}s"

            title = self.composer.compose(
                session_key=session_id,
                task_category="agent",
                status="done",
                detail=f"claude: ready{duration_str}",
                cwd=cwd,
            )
            term.set_title(title, role=0)
            term.set_tab_color(colors.get("done", "#10b981"))
            term.set_badge("DONE", enabled=True)

            unfocused_only = self.config.get("alert_only_unfocused", True)
            if not unfocused_only or not term.is_focused():
                sess_num = getattr(term, "get_session_num", lambda: None)()
                sess_label = f"Tab {sess_num}" if sess_num is not None else "Terminal"
                self.notifier.notify(
                    title="Claude Code Complete",
                    message=f"Claude finished its task{duration_str} in {sess_label} ({cwd_name})",
                    urgency="normal",
                    is_error=False,
                    play_sound=True,
                )
            return {}

        elif event_name in ("Notification", "notification"):
            notif_type = payload.get("type", "") or payload.get("matcher", "")
            sess_num = getattr(term, "get_session_num", lambda: None)()
            sess_label = f"Tab {sess_num}" if sess_num is not None else "Terminal"

            if "permission" in notif_type:
                term.set_title("❓ [Permission] claude", role=0)
                term.set_tab_color(colors.get("waiting", "#8b5cf6"))
                term.set_badge("PERM", enabled=True)
                self.notifier.notify(
                    title="Claude Needs Permission",
                    message=f"Claude is waiting for permission in {sess_label} ({cwd_name})",
                    urgency="critical",
                    is_error=False,
                    play_sound=True,
                )
            elif "idle" in notif_type:
                term.set_title("💤 [Idle] claude", role=0)
                term.set_tab_color(colors.get("idle", "#3b82f6"))
                term.set_badge("IDLE", enabled=True)

        return response

    def _format_tool_detail(self, name: str, inp: Dict[str, Any]) -> str:
        """Format Claude tool inputs into short summaries."""
        if name.lower() == "bash":
            cmd = inp.get("command", "")
            return f"Bash({cmd[:24]}...)" if len(cmd) > 24 else f"Bash({cmd})"

        if name.lower() in ("edit", "multiedit"):
            fpath = inp.get("file_path", "")
            return f"Edit({Path(fpath).name})" if fpath else name

        if name.lower() == "write":
            fpath = inp.get("file_path", "")
            return f"Write({Path(fpath).name})" if fpath else name

        if name.lower() == "view":
            fpath = inp.get("file_path", "")
            return f"View({Path(fpath).name})" if fpath else name

        if name.lower() in ("glob", "grep"):
            pattern = inp.get("pattern", "")
            return f"{name}({pattern[:20]})"

        return name[:20]
