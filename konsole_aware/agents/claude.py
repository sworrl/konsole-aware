"""Claude Code agent hook handler.

Covers every Claude Code lifecycle event that affects what a tab should show:
SessionStart / SessionEnd, UserPromptSubmit, PreToolUse / PostToolUse /
PostToolUseFailure, PermissionRequest / PermissionDenied, Notification
(permission_prompt, idle_prompt, elicitation_dialog), Stop / StopFailure,
SubagentStart / SubagentStop, PreCompact / PostCompact, Elicitation /
ElicitationResult — and every permission mode (default, plan, acceptEdits, auto,
dontAsk, bypassPermissions), which every payload carries as `permission_mode`.

The hook is purely observational: it never prints anything (stdout of
UserPromptSubmit/SessionStart hooks is injected into the model's context) and never
returns a permission decision.
"""

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from konsole_aware.agents.base import BaseAgentHandler, short

# Canonical event names, accepting snake_case aliases.
EVENTS = {
    re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower(): name
    for name in (
        "SessionStart", "SessionEnd", "UserPromptSubmit", "PreToolUse", "PostToolUse",
        "PostToolUseFailure", "PermissionRequest", "PermissionDenied", "Notification",
        "Stop", "StopFailure", "SubagentStart", "SubagentStop", "PreCompact", "PostCompact",
        "Elicitation", "ElicitationResult", "PostModelSwitch",
    )
}

# Text Claude Code shows when the model provider's safeguards block a response
# ("…'s safeguards flagged this message…", "Details: `[cyber]`", the Cyber
# Verification Program link, the AUP link). These blocks are frequently false
# positives on legitimate work, so they get their own 🛡️ BLOCKED state instead of
# looking like an ordinary error or a finished turn.
SAFEGUARD_RE = re.compile(
    r"safeguards flagged|Cyber Verification Program|real-time-cyber-safeguards|"
    r"anthropic\.com/legal/aup|can't respond to (?:this|your last) message|Details: `\[[a-z_-]+\]`",
    re.IGNORECASE,
)
SAFEGUARD_CATEGORY_RE = re.compile(r"Details:\s*`\[([a-z_-]+)\]`", re.IGNORECASE)


def find_safeguard_block(*texts: Any) -> Optional[str]:
    """Return the flag category ('cyber', 'bio', ...) or 'flagged' if any text shows a safeguards block."""
    for text in texts:
        if not isinstance(text, str) or not text:
            continue
        if SAFEGUARD_RE.search(text):
            m = SAFEGUARD_CATEGORY_RE.search(text)
            if m:
                return m.group(1).lower()
            if "cyber" in text.lower():
                return "cyber"
            return "flagged"
    return None


def transcript_tail(path: Any, max_bytes: int = 65536) -> List[Dict[str, Any]]:
    """Parsed transcript entries of the current turn (after the last real user prompt)."""
    if not isinstance(path, str) or not path:
        return []
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - max_bytes))
            lines = f.read().decode("utf-8", errors="ignore").splitlines()
    except OSError:
        return []
    entries: List[Dict[str, Any]] = []
    for line in reversed(lines):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict):
            continue
        msg = entry.get("message")
        if entry.get("type") == "user" and isinstance(msg, dict) and isinstance(msg.get("content"), str):
            break  # start of this turn
        entries.append(entry)
    return list(reversed(entries))


def _entry_text(entry: Dict[str, Any]) -> str:
    msg = entry.get("message")
    parts = [str(entry.get("content") or "")]
    if isinstance(msg, dict):
        content = msg.get("content")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            parts.extend(str(c.get("text", "")) for c in content if isinstance(c, dict))
    return "\n".join(p for p in parts if p)


def transcript_safeguard_events(entries: List[Dict[str, Any]]) -> Dict[str, Optional[str]]:
    """Find safeguard refusals in this turn's transcript entries.

    Returns {"blocked": category|None, "fallback": category|None}:
    - fallback: an assistant refusal whose content is a {"type": "fallback"} block (Claude
      Code retried on another model), or a system "model_refusal_fallback" warning
    - blocked: a refusal with no fallback, or the synthetic "API Error: …'s safeguards
      flagged this message" assistant message that ends the turn
    The category ("cyber", "bio", ...) comes from message.stop_details.category.
    """
    found: Dict[str, Optional[str]] = {"blocked": None, "fallback": None}
    for entry in entries:
        msg = entry.get("message") if isinstance(entry.get("message"), dict) else {}
        details = msg.get("stop_details") if isinstance(msg.get("stop_details"), dict) else {}
        category = details.get("category") or find_safeguard_block(_entry_text(entry))
        content = msg.get("content") if isinstance(msg.get("content"), list) else []
        if entry.get("type") == "system" and entry.get("subtype") == "model_refusal_fallback":
            found["fallback"] = category or "flagged"
        elif entry.get("type") == "assistant" and msg.get("stop_reason") == "refusal":
            if any(isinstance(c, dict) and c.get("type") == "fallback" for c in content):
                found["fallback"] = category or "flagged"
            else:
                found["blocked"] = category or "flagged"
        elif entry.get("type") == "assistant" and is_block_message(_entry_text(entry)):
            found["blocked"] = is_block_message(_entry_text(entry))
    return found


_BLOCK_MESSAGE_RE = re.compile(r"^(?:API Error:\s*)?[\w .()-]{1,40}'s safeguards flagged this message", re.IGNORECASE)


def is_block_message(text: Any) -> Optional[str]:
    """Strict check for a final assistant message that *is* a safeguards block (not one
    that merely talks about safeguards): Claude Code renders it as
    "API Error: <Model>'s safeguards flagged this message. …"."""
    if not isinstance(text, str):
        return None
    stripped = text.strip()
    if len(stripped) > 1500 or not _BLOCK_MESSAGE_RE.match(stripped):
        return None
    return find_safeguard_block(stripped)


_LIMIT_RE = re.compile(
    r"You've (?:hit|reached) your (?P<what>[\w .-]{1,30}?) limit(?:\s*·\s*resets (?P<when>[^\n]{1,60}))?",
    re.IGNORECASE)


def find_usage_limit(text: Any) -> Optional[str]:
    """'You've hit your weekly limit · resets Oct 1, 2am (…)' -> 'weekly limit · resets Oct 1, 2am'."""
    if not isinstance(text, str) or len(text) > 600:
        return None
    m = _LIMIT_RE.search(text.strip())
    if not m:
        return None
    when = re.sub(r"\s*\([^)]*\)\s*$", "", m.group("when") or "").strip()
    return f"{m.group('what').strip()} limit" + (f" · resets {when}" if when else "")


def short_model(model: Any) -> str:
    """claude-opus-4-8-20260101 -> Opus 4.8"""
    text = str(model or "")
    m = re.match(r"claude-([a-z]+)-(\d+)(?:-(\d{1,2}))?(?:-\d{8})?", text)
    if m:
        return f"{m.group(1).capitalize()} {m.group(2)}" + (f".{m.group(3)}" if m.group(3) else "")
    return short(text, 18)

SESSION_START_DETAIL = {
    "startup": "ready",
    "resume": "resumed",
    "clear": "cleared",
    "compact": "compacted",
    "fork": "forked",
}


def canonical_event(name: str) -> str:
    return EVENTS.get(name, EVENTS.get(name.lower(), name))


class ClaudeHandler(BaseAgentHandler):
    """Integrates with Claude Code lifecycle hooks."""

    agent_name = "claude"
    display_name = "Claude Code"

    def handle_event(self, event_name: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        event = canonical_event(event_name)
        mode = payload.get("permission_mode") or None
        cwd = payload.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
        project = Path(cwd).name

        if event == "SessionEnd":
            if payload.get("reason") in ("clear", "resume"):
                # /clear ends the old conversation; a SessionStart(clear) follows.
                self.render("idle", "cleared", cwd=cwd, mode=mode)
            else:
                self.detach()
                self.render_user_session(cwd=cwd)
            return {}

        before = self.attach(mode=mode)
        if before.get("nested"):
            return {}
        if payload.get("transcript_path") and payload["transcript_path"] != before.get("transcript_path"):
            self.state_mgr.set_session(self.session_key, transcript_path=payload["transcript_path"])
        prev = before.get("agent_status") or before.get("status")
        in_subagent = bool(payload.get("agent_id")) and self._subagent_count() > 0
        if before.get("new_agent"):
            self.maybe_auto_snap()

        prefix = self._subagent_prefix(payload)

        if event == "SessionStart":
            source = payload.get("source", "startup")
            if source == "compact" and prev in ("compacting", "thinking", "tool", "subagents"):
                self.render("thinking", "thinking… (compacted)", cwd=cwd, mode=mode)
            else:
                self.render("idle", SESSION_START_DETAIL.get(source, source), cwd=cwd, mode=mode)

        elif event == "UserPromptSubmit":
            self.begin_turn_if_idle(None)
            self.start_watchdog()
            self.state_mgr.set_session(self.session_key, safeguard_switch=None, safeguard_block=None)
            self.render("thinking", "thinking…", cwd=cwd, mode=mode)

        elif event == "PostModelSwitch":
            to_model = short_model(payload.get("to_model"))
            if payload.get("source") == "auto":
                # Claude Code switched models on its own: after a safeguards flag (the
                # "Switch models when a message is flagged" setting), or because the model
                # was overloaded/unavailable. The transcript says which.
                from_model = short_model(payload.get("from_model"))
                flagged = transcript_safeguard_events(transcript_tail(payload.get("transcript_path")))["fallback"]
                self.state_mgr.set_session(self.session_key, safeguard_switch={
                    "from": from_model, "to": to_model, "category": flagged} if flagged else None)
                if flagged:
                    label = f" [{flagged}]" if flagged != "flagged" else ""
                    self.render("thinking", f"flagged{label}: {from_model} → {to_model}", cwd=cwd, mode=mode,
                                icon_status="blocked")
                    self.alert("Claude Switched Models",
                               f"{from_model}'s safeguards flagged the request{label}; continuing on {to_model} in "
                               f"{self.session_label()} ({project})", dedupe_key=f"switch:{to_model}")
                else:
                    self.render("thinking", f"switched: {from_model} → {to_model}", cwd=cwd, mode=mode)

        elif event == "PreToolUse":
            tool = payload.get("tool_name") or "tool"
            tool_input = payload.get("tool_input") or {}
            self.begin_turn_if_idle(prev)
            if tool == "AskUserQuestion":
                self.render("waiting", "question", cwd=cwd, mode=mode)
                self.alert("Claude Has a Question",
                           f"Claude is asking you a question in {self.session_label()} ({project})",
                           urgency="critical", dedupe_key="question")
            elif tool == "ExitPlanMode":
                self.render("waiting", "plan ready for review", cwd=cwd, mode=mode)
                self.alert("Claude Plan Ready",
                           f"Claude finished planning in {self.session_label()} ({project})",
                           urgency="critical", dedupe_key="plan")
            elif in_subagent:
                n = self._subagent_count()
                self.render("subagents", f"{n} subagent{'s' if n != 1 else ''} · " + prefix
                            + self.format_tool_detail(tool, tool_input), cwd=cwd, mode=mode)
            else:
                self.render("tool", prefix + self.format_tool_detail(tool, tool_input), cwd=cwd, mode=mode,
                            last_tool=tool)

        elif event == "PermissionRequest" and payload.get("tool_name") in ("AskUserQuestion", "ExitPlanMode"):
            pass  # already shown as "question" / "plan ready" by PreToolUse

        elif event == "PermissionRequest":
            tool = payload.get("tool_name") or "tool"
            detail = self.format_tool_detail(tool, payload.get("tool_input") or {})
            self.render("waiting", f"permission: {detail}", cwd=cwd, mode=mode)
            self.alert("Claude Needs Permission",
                       f"Claude wants to run {detail} in {self.session_label()} ({project})",
                       urgency="critical", dedupe_key="permission")

        elif event == "PermissionDenied":
            tool = payload.get("tool_name") or "tool"
            self.render("thinking", prefix + f"denied: {self.format_tool_detail(tool, payload.get('tool_input') or {})}",
                        cwd=cwd, mode=mode)

        elif event == "PostToolUse":
            if not in_subagent and self._subagent_count() == 0:
                self.render("thinking", "thinking…", cwd=cwd, mode=mode)

        elif event == "PostToolUseFailure":
            tool = payload.get("tool_name") or "tool"
            if payload.get("is_interrupt"):
                self.state_mgr.finish_task(self.session_key, exit_code=130, status="idle")
                self.render("idle", "interrupted", cwd=cwd, mode=mode, icon_status="waiting")
            else:
                self.render("thinking", prefix + f"{tool} failed", cwd=cwd, mode=mode, icon_status="error")

        elif event == "Notification":
            self._handle_notification(payload, cwd, mode, project)

        elif event == "Elicitation":
            self.render("waiting", f"input for {short(payload.get('mcp_server_name') or 'MCP', 16)}", cwd=cwd, mode=mode)
            self.alert("Claude Needs Input",
                       f"An MCP server is asking for input in {self.session_label()} ({project})",
                       urgency="critical", dedupe_key="elicitation")

        elif event == "ElicitationResult":
            self.render("thinking", "thinking…", cwd=cwd, mode=mode)

        elif event == "SubagentStart":
            agents = self._subagents_add(payload.get("agent_id") or payload.get("agent_type") or "agent")
            kind = payload.get("agent_type") or "subagent"
            self.render("subagents", f"{len(agents)} subagent{'s' if len(agents) != 1 else ''} ({short(kind, 16)})",
                        cwd=cwd, mode=mode)

        elif event == "SubagentStop":
            agents = self._subagents_remove(payload.get("agent_id") or payload.get("agent_type") or "agent")
            if agents:
                self.render("subagents", f"{len(agents)} subagent{'s' if len(agents) != 1 else ''}", cwd=cwd, mode=mode)
            else:
                self.render("thinking", "thinking…", cwd=cwd, mode=mode)

        elif event == "PreCompact":
            trigger = payload.get("trigger") or "auto"
            self.render("compacting", f"compacting ({trigger})", cwd=cwd, mode=mode)

        elif event == "PostCompact":
            if payload.get("trigger") == "manual":
                self.state_mgr.finish_task(self.session_key, status="idle")
                self.render("idle", "compacted", cwd=cwd, mode=mode)
            else:
                self.render("thinking", "thinking… (compacted)", cwd=cwd, mode=mode)

        elif event == "Stop":
            dur = self.turn_duration()
            self._subagents_clear()
            events = transcript_safeguard_events(transcript_tail(payload.get("transcript_path")))
            limit = find_usage_limit(payload.get("last_assistant_message"))
            if limit:
                self._render_limited(limit, cwd, mode, project)
                return {}
            category = is_block_message(payload.get("last_assistant_message")) or events["blocked"]
            if category:
                self._render_blocked(category, cwd, mode, project)
                return {}
            self.state_mgr.finish_task(self.session_key, exit_code=0, status="done")
            switch = self.state_mgr.get_session(self.session_key).get("safeguard_switch") or {}
            if not switch and events["fallback"]:
                switch = {"to": "fallback model", "category": events["fallback"]}
            note = f" · 🛡️ on {switch['to']}" if switch.get("to") else ""
            self.render("done", (f"ready ({dur})" if dur else "ready") + note, cwd=cwd, mode=mode)
            self.alert("Claude Code Complete",
                       f"Claude finished{' in ' + dur if dur else ''} in {self.session_label()} ({project})"
                       + (f" (switched to {switch['to']} after a flag)" if switch.get("to") else ""))

        elif event == "StopFailure":
            self._subagents_clear()
            category = find_safeguard_block(payload.get("error_details"), payload.get("last_assistant_message"))
            if not category:
                category = transcript_safeguard_events(transcript_tail(payload.get("transcript_path")))["blocked"]
            if category:
                self._render_blocked(category, cwd, mode, project)
                return {}
            limit = find_usage_limit(payload.get("last_assistant_message")) or find_usage_limit(
                payload.get("error_details"))
            if limit or payload.get("error") == "rate_limit":
                self._render_limited(limit or "rate limited", cwd, mode, project)
                return {}
            error = short(payload.get("error") or payload.get("error_type") or "API error", 32)
            self.state_mgr.finish_task(self.session_key, exit_code=1, status="error")
            self.render("error", f"failed: {error}", cwd=cwd, mode=mode)
            self.alert("Claude Code Failed",
                       f"Claude stopped with an error ({error}) in {self.session_label()} ({project})",
                       urgency="critical", is_error=True)

        return {}

    # ---------------------------------------------------------------- helpers
    def _render_blocked(self, category: str, cwd: str, mode: Optional[str], project: str) -> None:
        """The turn was stopped by a safeguards flag (often a false positive): say so plainly."""
        label = f"[{category}]" if category != "flagged" else ""
        self.state_mgr.finish_task(self.session_key, exit_code=1, status="blocked", safeguard_block=category)
        self.render("blocked", f"blocked by safeguards {label} · rephrase or /model".replace("  ", " "),
                    cwd=cwd, mode=mode)
        self.alert("Claude Blocked by Safeguards",
                   f"A {category if category != 'flagged' else 'safeguards'} flag stopped Claude in "
                   f"{self.session_label()} ({project}). Often a false positive: rephrase, switch "
                   "model (/model), or start a new session.",
                   urgency="critical", is_error=True, dedupe_key=f"blocked:{category}")

    def _render_limited(self, limit: str, cwd: str, mode: Optional[str], project: str) -> None:
        self.state_mgr.finish_task(self.session_key, exit_code=1, status="limited")
        self.render("limited", short(limit, 48), cwd=cwd, mode=mode)
        self.alert("Claude Usage Limit", f"Claude stopped: {limit} ({self.session_label()}, {project})",
                   dedupe_key=f"limit:{limit}")

    def _handle_notification(self, payload: Dict[str, Any], cwd: str, mode: Optional[str], project: str) -> None:
        kind = payload.get("notification_type") or ""
        message = payload.get("message") or ""
        if kind == "permission_prompt" or (not kind and "permission" in message.lower()):
            self.render("waiting", "permission", cwd=cwd, mode=mode)
            self.alert("Claude Needs Permission",
                       message or f"Claude is waiting for permission in {self.session_label()} ({project})",
                       urgency="critical", dedupe_key="permission")
        elif kind == "idle_prompt":
            # Sent ~60 s after a turn ended. The turn is over (even if Claude's last text
            # asked something), so it must not look like a live prompt you can answer now:
            # "waiting" is reserved for open questions/permission dialogs.
            pass
        elif kind == "elicitation_dialog":
            self.render("waiting", "question", cwd=cwd, mode=mode)
            self.alert("Claude Needs Input", message or f"Claude needs input in {self.session_label()} ({project})",
                       urgency="critical", dedupe_key="elicitation")

    @staticmethod
    def _subagent_prefix(payload: Dict[str, Any]) -> str:
        """Events fired inside a subagent carry agent_id/agent_type."""
        if payload.get("agent_id") and payload.get("agent_type"):
            return f"{short(payload['agent_type'], 12)}› "
        return ""

    def _subagent_count(self) -> int:
        return len(self.state_mgr.get_session(self.session_key).get("subagents") or [])

    def _subagents_add(self, agent_id: str) -> list:
        sess = self.state_mgr.update_session(
            self.session_key,
            lambda s: {"subagents": list(dict.fromkeys((s.get("subagents") or []) + [agent_id]))},
        )
        return sess.get("subagents") or []

    def _subagents_remove(self, agent_id: str) -> list:
        sess = self.state_mgr.update_session(
            self.session_key,
            lambda s: {"subagents": [a for a in (s.get("subagents") or []) if a != agent_id]},
        )
        return sess.get("subagents") or []

    def _subagents_clear(self) -> None:
        self.state_mgr.set_session(self.session_key, subagents=[])

    def format_tool_detail(self, name: str, inp: Dict[str, Any]) -> str:
        """Format Claude tool inputs into short summaries."""
        if not isinstance(inp, dict):
            inp = {}
        lname = name.lower()

        if lname == "bash":
            return f"Bash({short(inp.get('command', ''), 24)})"
        if lname in ("edit", "multiedit", "write", "read", "notebookedit"):
            fpath = inp.get("file_path") or inp.get("notebook_path") or ""
            return f"{name}({Path(fpath).name})" if fpath else name
        if lname in ("glob", "grep"):
            return f"{name}({short(inp.get('pattern', ''), 20)})"
        if lname == "webfetch":
            host = urlparse(inp.get("url", "")).netloc
            return f"WebFetch({short(host, 20)})" if host else "WebFetch"
        if lname == "websearch":
            return f"WebSearch({short(inp.get('query', ''), 20)})"
        if lname in ("task", "agent"):
            kind = inp.get("subagent_type") or "agent"
            desc = inp.get("description") or ""
            return f"Agent({short(kind, 14)}{': ' + short(desc, 16) if desc else ''})"
        if lname == "skill":
            return f"Skill({short(inp.get('skill') or inp.get('command') or '', 20)})"
        if lname == "todowrite":
            return "updating todos"
        if lname == "enterplanmode":
            return "entering plan mode"
        if name.startswith("mcp__"):
            parts = name.split("__")
            server = parts[1] if len(parts) > 1 else "mcp"
            tool = parts[-1] if len(parts) > 2 else ""
            return f"mcp:{short(server, 12)}.{short(tool, 14)}" if tool else f"mcp:{short(server, 20)}"
        return short(name, 20)

    # Backwards-compatible alias used by older tests/scripts.
    _format_tool_detail = format_tool_detail
