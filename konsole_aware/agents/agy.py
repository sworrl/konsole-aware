"""Antigravity (agy) agent lifecycle hook handler."""

import os
from pathlib import Path
from typing import Any, Dict
from konsole_aware.agents.base import BaseAgentHandler
from konsole_aware.config import load_config
from konsole_aware.terminal import get_terminal
from konsole_aware.notify.notifier import Notifier
from konsole_aware.state.manager import StateManager


class AgyHandler(BaseAgentHandler):
    """Integrates with Antigravity lifecycle hooks."""

    def __init__(self):
        self.config = load_config()
        self.notifier = Notifier()
        self.state_mgr = StateManager()

    def handle_event(self, event_name: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        term = get_terminal()
        session_id = term.get_session_id()
        colors = self.config.get("colors", {})
        emojis = self.config.get("emojis", {})

        # Extract workspace context
        workspace_paths = payload.get("workspacePaths", [])
        workspace_name = (
            Path(workspace_paths[0]).name
            if workspace_paths
            else Path.cwd().name
        )

        response: Dict[str, Any] = {}

        if event_name in ("PreInvocation", "pre_invocation"):
            # Model is thinking / planning
            term.set_title(f"🧠 agy: thinking...", role=0)
            term.set_tab_color(colors.get("busy", "#f59e0b"))
            term.set_badge("THINK", enabled=True)
            self.state_mgr.start_task(
                session_id,
                task_name="agy",
                task_type="agent",
                detail="thinking",
            )
            response = {"injectSteps": []}

        elif event_name in ("PreToolUse", "pre_tool_use"):
            tool_call = payload.get("toolCall", {})
            name = tool_call.get("name", "tool")
            args = tool_call.get("args", {})

            # Clean and format tool description
            detail = self._format_tool_detail(name, args)
            term.set_title(f"🛠️ agy: {detail}", role=0)
            term.set_tab_color(colors.get("busy", "#f59e0b"))

            if name == "ask_question":
                # Special high-priority state: Agent is waiting for user answer!
                term.set_tab_color(colors.get("waiting", "#8b5cf6"))
                term.set_badge("WAIT", enabled=True)
                sess_num = getattr(term, "get_session_num", lambda: None)()
                sess_label = f"Tab {sess_num}" if sess_num is not None else "Terminal"
                self.notifier.notify(
                    title="Antigravity Needs Input",
                    message=f"Antigravity is asking a question in {sess_label} ({workspace_name})",
                    urgency="critical",
                    is_error=False,
                    play_sound=True,
                )
            else:
                term.set_badge("TOOL", enabled=True)

            self.state_mgr.start_task(
                session_id,
                task_name="agy",
                task_type="agent",
                detail=f"{name}: {detail}",
            )
            response = {"decision": "allow"}

        elif event_name in ("PostToolUse", "post_tool_use"):
            error = payload.get("error")
            if error:
                term.set_tab_color(colors.get("error", "#ef4444"))
                term.set_badge("ERR", enabled=True)
            response = {}

        elif event_name in ("Stop", "stop"):
            # Turn or run has completed
            finished_state = self.state_mgr.finish_task(session_id, exit_code=0, status="done")
            duration_str = ""
            if "duration" in finished_state:
                dur = int(finished_state["duration"])
                mins, secs = divmod(dur, 60)
                duration_str = f" in {mins}m {secs}s" if mins else f" in {secs}s"

            term.set_title(f"✅ [Ready] agy", role=0)
            term.set_tab_color(colors.get("done", "#10b981"))
            term.set_badge("DONE", enabled=True)

            # Check focus before sending desktop notification & audio
            unfocused_only = self.config.get("alert_only_unfocused", True)
            is_focused = term.is_focused()

            if not unfocused_only or not is_focused:
                sess_num = getattr(term, "get_session_num", lambda: None)()
                sess_label = f"Tab {sess_num}" if sess_num is not None else "Terminal"
                self.notifier.notify(
                    title="Antigravity Complete",
                    message=f"Antigravity finished its task{duration_str} in {sess_label} ({workspace_name})",
                    urgency="normal",
                    is_error=False,
                    play_sound=True,
                )

            response = {}

        return response

    def _format_tool_detail(self, name: str, args: Dict[str, Any]) -> str:
        """Format tool call arguments into a concise, readable summary."""
        if name == "run_command":
            cmd = args.get("CommandLine", "")
            return f"cmd({cmd[:24]}...)" if len(cmd) > 24 else f"cmd({cmd})"

        if name in ("write_to_file", "replace_file_content"):
            target = args.get("TargetFile", "")
            return f"edit({Path(target).name})" if target else name

        if name == "view_file":
            path = args.get("AbsolutePath", "")
            return f"view({Path(path).name})" if path else name

        if name == "search_web":
            q = args.get("query", "")
            return f"web({q[:20]})" if q else "web search"

        if name == "read_url_content":
            url = args.get("Url", "")
            domain = url.split("/")[2] if "://" in url else url
            return f"url({domain[:20]})"

        if name == "invoke_subagent":
            subagents = args.get("Subagents", [])
            roles = [s.get("Role", "subagent") for s in subagents]
            return f"agents({', '.join(roles[:2])})" if roles else "subagents"

        if name == "ask_question":
            return "question"

        return name[:20]
