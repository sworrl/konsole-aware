"""State management for tracking tab and session activities."""

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

STATE_DIR = Path(f"/tmp/konsole-aware-{os.environ.get('USER', 'default')}")
STATE_FILE = STATE_DIR / "sessions.json"


class StateManager:
    """Manages active tab states, original titles, durations, and task metadata."""

    def __init__(self):
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        self.state_file = STATE_FILE

    def _load_all(self) -> Dict[str, Any]:
        if not self.state_file.exists():
            return {}
        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_all(self, data: Dict[str, Any]) -> None:
        try:
            temp_file = STATE_DIR / f"sessions.json.tmp.{os.getpid()}"
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            temp_file.replace(self.state_file)
        except Exception:
            pass

    def get_session(self, session_key: str) -> Dict[str, Any]:
        """Get state for a specific session."""
        data = self._load_all()
        return data.get(session_key, {})

    def set_session(self, session_key: str, **kwargs) -> Dict[str, Any]:
        """Update or create state for a session."""
        data = self._load_all()
        sess = data.get(session_key, {})
        sess.update(kwargs)
        sess["updated_at"] = time.time()
        data[session_key] = sess
        self._save_all(data)
        return sess

    def start_task(
        self,
        session_key: str = "",
        task_name: str = "",
        task_type: str = "command",
        detail: str = "",
        original_title: str = "",
        original_color: str = "",
        session_id: str = "",
    ) -> Dict[str, Any]:
        """Record the start of a task or agent run."""
        s_key = session_key or session_id
        sess = self.get_session(s_key)
        now = time.time()
        updates = {
            "status": "busy",
            "task_name": task_name,
            "task_type": task_type,
            "detail": detail,
            "start_time": now,
            "end_time": None,
        }
        if original_title and not sess.get("original_title"):
            updates["original_title"] = original_title
        if original_color and not sess.get("original_color"):
            updates["original_color"] = original_color

        return self.set_session(s_key, **updates)

    def finish_task(
        self,
        session_key: str = "",
        exit_code: int = 0,
        status: str = "done",
        session_id: str = "",
    ) -> Dict[str, Any]:
        """Record the completion of a task or agent run."""
        s_key = session_key or session_id
        now = time.time()
        sess = self.get_session(s_key)
        start = sess.get("start_time", now)
        duration = max(0.0, now - start)

        updates = {
            "status": status,
            "exit_code": exit_code,
            "end_time": now,
            "duration": duration,
        }
        return self.set_session(session_key, **updates)

    def clear_session(self, session_key: str) -> None:
        """Remove a session from tracking."""
        data = self._load_all()
        if session_key in data:
            del data[session_key]
            self._save_all(data)

    def get_all_sessions(self) -> Dict[str, Any]:
        """Return all tracked sessions."""
        return self._load_all()
