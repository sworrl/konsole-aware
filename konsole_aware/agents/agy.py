"""Antigravity (agy) agent lifecycle hook handler.

agy hook contract (from the CLI's built-in hooks.json documentation): payloads are
camelCase JSON with common fields conversationId, workspacePaths, transcriptPath,
modelName. Events: PreInvocation (before each model call), PreToolUse (toolCall.name /
toolCall.args), PostToolUse (error present if the tool failed), PostInvocation, Stop
(terminationReason, error, fullyIdle). Hooks run with their working directory set to
the directory holding hooks.json, so the project comes from workspacePaths.

The hook is purely observational. It returns `{}` for every event: in particular it
never returns a PreToolUse "decision" ("allow" would silently auto-approve every tool
call and bypass agy's permission prompts) and never asks a Stop to continue.
"""

from pathlib import Path
from typing import Any, Dict, Optional

from konsole_aware.agents.base import BaseAgentHandler, short

STOP_REASONS = {
    "model_stop": None,
    "max_steps_exceeded": "max steps reached",
    "error": "error",
}


class AgyHandler(BaseAgentHandler):
    """Integrates with Antigravity lifecycle hooks."""

    agent_name = "agy"
    display_name = "Antigravity"

    def handle_event(self, event_name: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        event = event_name.replace("_", "").lower()
        workspace_paths = payload.get("workspacePaths") or []
        cwd: Optional[str] = workspace_paths[0] if workspace_paths else None
        if not cwd:
            cwd = getattr(self.term, "get_cwd", lambda: None)()
        project = Path(cwd).name if cwd else "workspace"

        before = self.attach()
        if before.get("nested"):
            return {}
        model = payload.get("modelName")
        effort = self._argv_effort(before)
        updates = {k: v for k, v in (("model", model), ("effort", effort)) if v and v != before.get(k)}
        if updates:
            self.state_mgr.set_session(self.session_key, **updates)
        prev = before.get("agent_status") or before.get("status")
        if before.get("new_agent"):
            self.maybe_auto_snap()

        if event == "preinvocation":
            self.begin_turn_if_idle(prev)
            self.start_watchdog()
            self.render("thinking", "thinking…", cwd=cwd)

        elif event == "pretooluse":
            tool_call = payload.get("toolCall") or {}
            name = tool_call.get("name") or "tool"
            args = tool_call.get("args") or {}
            self.begin_turn_if_idle(prev)
            if name == "ask_question":
                self.render("waiting", "question", cwd=cwd)
                self.alert("Antigravity Needs Input",
                           f"Antigravity is asking a question in {self.session_label()} ({project})",
                           urgency="critical", dedupe_key="question")
            else:
                self.render("tool", self.format_tool_detail(name, args), cwd=cwd, last_tool=name)

        elif event == "posttooluse":
            if payload.get("error"):
                last = self.state_mgr.get_session(self.session_key).get("last_tool") or "tool"
                self.render("thinking", f"{last} failed", cwd=cwd, icon_status="error")
            else:
                self.render("thinking", "thinking…", cwd=cwd)

        elif event == "postinvocation":
            pass

        elif event == "stop":
            dur = self.turn_duration()
            reason = payload.get("terminationReason") or "model_stop"
            error = payload.get("error") or ""
            failed = bool(error) or reason in ("error", "max_steps_exceeded")
            if failed:
                why = short(error or STOP_REASONS.get(reason) or reason, 32)
                self.state_mgr.finish_task(self.session_key, exit_code=1, status="error")
                self.render("error", f"stopped: {why}", cwd=cwd)
                self.alert("Antigravity Stopped",
                           f"Antigravity stopped ({why}) in {self.session_label()} ({project})",
                           urgency="critical", is_error=True)
            else:
                self.state_mgr.finish_task(self.session_key, exit_code=0, status="done")
                bg = "" if payload.get("fullyIdle", True) else " · bg tasks running"
                self.render("done", (f"ready ({dur})" if dur else "ready") + bg, cwd=cwd)
                self.alert("Antigravity Complete",
                           f"Antigravity finished{' in ' + dur if dur else ''} in {self.session_label()} ({project})")

        return {}

    def _argv_effort(self, before: Dict[str, Any]) -> Optional[str]:
        from konsole_aware.agents import identity as agent_identity
        _, pid = agent_identity.find_agent_ancestor(self.agent_name)
        argv = agent_identity._read_argv(pid) if pid else []
        for i, tok in enumerate(argv):
            if tok == "--effort" and i + 1 < len(argv):
                return argv[i + 1]
            if tok.startswith("--effort="):
                return tok.split("=", 1)[1]
        return None

    def format_tool_detail(self, name: str, args: Dict[str, Any]) -> str:
        """Format tool call arguments into a concise, readable summary."""
        if not isinstance(args, dict):
            args = {}
        if name == "run_command":
            return f"cmd({short(args.get('CommandLine', ''), 24)})"

        if name in ("write_to_file", "replace_file_content", "multi_replace_file_content"):
            target = args.get("TargetFile", "")
            return f"edit({Path(target).name})" if target else name

        if name == "view_file":
            path = args.get("AbsolutePath", "")
            return f"view({Path(path).name})" if path else name

        if name == "search_web":
            q = args.get("query", "")
            return f"web({short(q, 20)})" if q else "web search"

        if name == "read_url_content":
            url = args.get("Url", "")
            domain = url.split("/")[2] if "://" in url else url
            return f"url({short(domain, 20)})"

        if name == "invoke_subagent":
            subagents = args.get("Subagents", [])
            roles = [s.get("Role", "subagent") for s in subagents if isinstance(s, dict)]
            return f"agents({', '.join(roles[:2])})" if roles else "subagents"

        if name == "ask_question":
            return "question"

        return short(name, 20)

    # Backwards-compatible alias used by older tests/scripts.
    _format_tool_detail = format_tool_detail
