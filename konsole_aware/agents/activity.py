"""Work happening around a Claude Code turn: ultracode mode and background tasks.

Ultracode is session-only state that Claude Code never puts in a hook payload (the
`effort` field and CLAUDE_EFFORT only ever hold low..max). The transcript records it:
the `/effort ultracode [on|off]` command with its "Ultracode on/off" output, and the
`ultra_effort_enter` / `ultra_effort_exit` reminder attachments written at the next
prompt. The newest of those entries decides.

Background work (workflows, background agents and shells, monitors, teammates) is
listed by Claude Code in every Stop payload as `background_tasks`, already filtered to
running/pending. Between Stops, launches are visible in PostToolUse responses
(`status: async_launched`, `backgroundTaskId`) and completions arrive as a
`<task-notification>` prompt on UserPromptSubmit.
"""

import json
import os
import re
from typing import Any, Dict, List, Optional

_EFFORT_CMD_RE = re.compile(r"<command-name>/effort</command-name>.*?<command-args>([^<]*)</command-args>", re.S)
_TASK_ID_RE = re.compile(r"<task-id>([^<]+)</task-id>")
_TASK_STATUS_RE = re.compile(r"<status>([^<]+)</status>")


def _content_text(entry: Dict[str, Any]) -> str:
    msg = entry.get("message") if isinstance(entry.get("message"), dict) else {}
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(str(c.get("text", "")) for c in content if isinstance(c, dict))
    return ""


def ultracode_from_entry(entry: Dict[str, Any]) -> Optional[bool]:
    """True/False if this transcript entry turns ultracode on/off, None if unrelated."""
    if entry.get("type") == "attachment":
        kind = (entry.get("attachment") or {}).get("type")
        if kind == "ultra_effort_enter":
            return True
        if kind == "ultra_effort_exit":
            return False
        return None
    if entry.get("type") != "user":
        return None
    text = _content_text(entry)
    if "Ultracode on" in text and "local-command-stdout" in text:
        return True
    if "Ultracode off" in text and "local-command-stdout" in text:
        return False
    m = _EFFORT_CMD_RE.search(text)
    if m:
        args = m.group(1).strip().lower()
        if args in ("ultracode", "ultracode on"):
            return True
        if args == "ultracode off":
            return False
    return None


def scan_transcript(path: Any, max_bytes: int = 262144) -> Dict[str, Any]:
    """One backwards pass over the transcript tail: newest model/effort and ultracode state."""
    found: Dict[str, Any] = {"model": None, "effort": None, "ultracode": None, "ultracode_at": 0.0}
    if not isinstance(path, str) or not path:
        return found
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - max_bytes))
            lines = f.read().decode("utf-8", errors="ignore").splitlines()
    except OSError:
        return found
    for line in reversed(lines):
        if found["model"] and found["ultracode"] is not None:
            break
        if not ('"assistant"' in line or "ultra" in line.lower() or "/effort" in line):
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict):
            continue
        if found["ultracode"] is None:
            uc = ultracode_from_entry(entry)
            if uc is not None:
                found["ultracode"] = uc
                found["ultracode_at"] = _entry_epoch(entry)
        if not found["model"] and entry.get("type") == "assistant":
            msg = entry.get("message") if isinstance(entry.get("message"), dict) else {}
            model = msg.get("model")
            if model and model != "<synthetic>":
                found["model"] = model
                found["effort"] = entry.get("effort")
    return found


def _entry_epoch(entry: Dict[str, Any]) -> float:
    from datetime import datetime
    ts = entry.get("timestamp")
    if not isinstance(ts, str):
        return 0.0
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def ultracode_from_argv(argv: List[str]) -> bool:
    """`claude --effort ultracode` starts the session with ultracode on."""
    for i, tok in enumerate(argv):
        if tok == "--effort" and i + 1 < len(argv) and argv[i + 1] == "ultracode":
            return True
        if tok == "--effort=ultracode":
            return True
    return False


# --------------------------------------------------------------------- background
TYPE_LABELS = {
    "workflow": "workflow",
    "local_workflow": "workflow",
    "subagent": "agent",
    "local_agent": "agent",
    "shell": "shell",
    "local_bash": "shell",
    "monitor": "monitor",
    "teammate": "teammate",
    "in_process_teammate": "teammate",
}


def counts_from_background_tasks(tasks: Any) -> Dict[str, int]:
    """Stop payload `background_tasks` -> {"workflow": 1, "agent": 2, "shell": 1, ...}."""
    counts: Dict[str, int] = {}
    if not isinstance(tasks, list):
        return counts
    for t in tasks:
        if not isinstance(t, dict) or t.get("status") not in (None, "running", "pending"):
            continue
        label = TYPE_LABELS.get(str(t.get("type", "")), str(t.get("type") or "task"))
        counts[label] = counts.get(label, 0) + 1
    return counts


def launched_task(tool_name: str, response: Any) -> Optional[Dict[str, str]]:
    """Background task started by this tool call, from its PostToolUse tool_response."""
    if not isinstance(response, dict):
        return None
    if response.get("status") == "async_launched":
        if tool_name == "Workflow" or response.get("taskType") == "local_workflow":
            return {"id": str(response.get("taskId") or response.get("runId") or ""), "type": "workflow",
                    "name": str(response.get("workflowName") or "")}
        task_id = response.get("agentId") or response.get("taskId")
        if task_id:
            return {"id": str(task_id), "type": "agent", "name": str(response.get("description") or "")}
    if response.get("backgroundTaskId"):
        return {"id": str(response["backgroundTaskId"]), "type": "shell", "name": ""}
    return None


def finished_task_ids(prompt: Any) -> List[str]:
    """IDs of background tasks that a `<task-notification>` prompt reports as ended."""
    if not isinstance(prompt, str) or "<task-notification>" not in prompt:
        return []
    ids = []
    for block in prompt.split("<task-notification>")[1:]:
        tid = _TASK_ID_RE.search(block)
        status = _TASK_STATUS_RE.search(block)
        if tid and (not status or status.group(1).strip() in ("completed", "failed", "killed", "stopped", "blocked")):
            ids.append(tid.group(1).strip())
    return ids


def counts_from_ids(bg_ids: Dict[str, Any]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for info in (bg_ids or {}).values():
        label = (info or {}).get("type", "task") if isinstance(info, dict) else str(info)
        counts[label] = counts.get(label, 0) + 1
    return counts


def describe_counts(counts: Dict[str, int]) -> str:
    """{"workflow": 1, "agent": 2} -> '1 workflow, 2 agents'."""
    parts = []
    for label in ("workflow", "agent", "teammate", "shell", "monitor"):
        n = counts.get(label, 0)
        if n:
            parts.append(f"{n} {label}{'s' if n != 1 else ''}")
    for label, n in counts.items():
        if label not in ("workflow", "agent", "teammate", "shell", "monitor") and n:
            parts.append(f"{n} {label}")
    return ", ".join(parts)
