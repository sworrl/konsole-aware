"""Per-tab watcher that ends an agent turn when no hook will.

Agent hooks report turn ends (Stop), but some endings fire no hook at all:
- Esc in Claude Code interrupts the turn without a Stop event; the transcript gets a
  "[Request interrupted by user…]" entry instead.
- An agent process that exits or crashes without SessionEnd (agy has no exit hook).
- A turn that ends on a usage-limit message.

`spawn()` starts one detached watcher per tab (guarded by a flock); it polls while the
tab shows an active agent status and exits as soon as the turn is over.
"""

import fcntl
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

from konsole_aware.agents import identity as agent_identity
from konsole_aware.state.manager import STATE_DIR, StateManager

POLL_SECONDS = 2.0
MAX_LIFETIME = 12 * 3600
ACTIVE = ("thinking", "tool", "busy", "subagents", "compacting", "waiting")


def _lock_path(session_key: str) -> Path:
    return STATE_DIR / f"watch-{hashlib.sha1(session_key.encode()).hexdigest()[:16]}.lock"


def spawn(session_key: str, agent: str) -> None:
    """Start the watcher for a tab unless one is already running."""
    lock = _lock_path(session_key)
    try:
        fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
    except OSError:
        return
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return  # a watcher already holds the lock
    os.close(fd)  # release; the child takes it for real
    bin_path = Path(__file__).resolve().parent.parent.parent / "bin" / "konsole-aware"
    try:
        subprocess.Popen(
            [sys.executable, str(bin_path), "watchdog", "--key", session_key, "--agent", agent],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True, close_fds=True,
        )
    except OSError:
        pass


def _last_turn_entry(path: Optional[str]) -> Optional[Dict[str, Any]]:
    """Newest meaningful transcript entry (user/assistant), skipping bookkeeping lines."""
    if not path:
        return None
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - 65536))
            lines = f.read().decode("utf-8", errors="ignore").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict):
            continue
        if entry.get("type") in ("user", "assistant"):
            return entry
        if entry.get("type") == "system" and entry.get("subtype") == "turn_duration":
            return entry  # Claude Code writes this when a turn has finished
    return None


def _entry_text(entry: Dict[str, Any]) -> str:
    msg = entry.get("message") if isinstance(entry.get("message"), dict) else {}
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(str(c.get("text", "")) for c in content if isinstance(c, dict))
    return ""


def _guess_claude_transcript(cwd: Optional[str]) -> Optional[str]:
    """Newest transcript in ~/.claude/projects/<cwd, non-alphanumerics as '-'>/ (for state
    recorded before transcript paths were stored)."""
    if not cwd:
        return None
    import re
    folder = Path.home() / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", cwd)
    try:
        files = sorted(folder.glob("*.jsonl"), key=lambda f: f.stat().st_mtime, reverse=True)
    except OSError:
        return None
    return str(files[0]) if files else None


def _entry_time(entry: Optional[Dict[str, Any]]) -> float:
    """Epoch seconds of a transcript entry's ISO timestamp (0 if missing)."""
    from datetime import datetime
    ts = (entry or {}).get("timestamp")
    if not isinstance(ts, str):
        return 0.0
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def classify_transcript_end(entry: Optional[Dict[str, Any]]) -> Optional[str]:
    """'interrupted' or a usage-limit description if the transcript shows the turn ended."""
    if not entry:
        return None
    if entry.get("type") == "system" and entry.get("subtype") == "turn_duration":
        return "ended"
    text = _entry_text(entry).strip()
    if entry.get("type") == "user" and text.startswith("[Request interrupted by user"):
        return "interrupted"
    if entry.get("type") == "assistant":
        from konsole_aware.agents.claude import find_usage_limit
        limit = find_usage_limit(text)
        if limit:
            return f"limit:{limit}"
    return None


def _terminal_for(session_key: str):
    from konsole_aware.terminal import KonsoleTerminal
    from konsole_aware.terminal.ansi import AnsiTerminal
    if ":/Sessions/" in session_key:
        service, path = session_key.split(":/Sessions/", 1)
        return KonsoleTerminal(service=service, session_path=f"/Sessions/{path}")
    if session_key.startswith("/dev/"):
        return AnsiTerminal(tty_path=session_key)
    return None


def _handler(agent: str, term):
    if agent == "claude":
        from konsole_aware.agents.claude import ClaudeHandler
        h = ClaudeHandler(term=term)
    else:
        from konsole_aware.agents.agy import AgyHandler
        h = AgyHandler(term=term)
    h.agent_name = agent
    return h


def check_once(session_key: str, agent: str, state_mgr: StateManager, term=None) -> bool:
    """One watchdog check. Returns True while the watcher should keep running."""
    sess = state_mgr.get_session(session_key)
    if sess.get("agent") != agent:
        return False
    status = sess.get("agent_status") or sess.get("status")
    agent_pid = sess.get("agent_pid")

    if agent_pid and not agent_identity.pid_alive(agent_pid):
        handler = _handler(agent, term or _terminal_for(session_key))
        handler.detach()
        handler.render_user_session(cwd=sess.get("cwd"))
        return False

    if status not in ACTIVE:
        return False

    entry = _last_turn_entry(sess.get("transcript_path") or (
        _guess_claude_transcript(sess.get("cwd")) if agent == "claude" else None))
    end = classify_transcript_end(entry)
    turn_start = sess.get("turn_start") or sess.get("start_time") or 0
    if end and _entry_time(entry) < turn_start - 1:
        end = None  # left over from an earlier turn
    if end:
        handler = _handler(agent, term or _terminal_for(session_key))
        cwd = sess.get("cwd")
        if end == "ended":
            from konsole_aware.agents.base import format_duration
            ms = entry.get("durationMs") if isinstance(entry, dict) else None
            dur = format_duration(ms / 1000) if isinstance(ms, (int, float)) else handler.turn_duration()
            state_mgr.finish_task(session_key, exit_code=0, status="done")
            handler.render("done", f"ready in {dur}" if dur else "ready", cwd=cwd)
        elif end == "interrupted":
            state_mgr.finish_task(session_key, exit_code=130, status="idle")
            handler.render("idle", "interrupted", cwd=cwd, icon_status="waiting")
        else:
            handler._render_limited(end.split(":", 1)[1], cwd, sess.get("agent_mode"),
                                    Path(cwd).name if cwd else "")
        return False
    return True


def run(session_key: str, agent: str) -> int:
    """Watcher main loop (runs detached, holds the per-tab lock)."""
    try:
        fd = os.open(_lock_path(session_key), os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return 0
    state_mgr = StateManager()
    started = time.time()
    try:
        while time.time() - started < MAX_LIFETIME:
            time.sleep(POLL_SECONDS)
            try:
                if not check_once(session_key, agent, state_mgr):
                    break
            except Exception:
                break
    finally:
        os.close(fd)
    return 0
