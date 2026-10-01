"""State management for tracking tab and session activities.

The state file is shared by many short-lived processes (shell preexec/precmd hooks,
Claude Code and agy hooks, the daemon), so every read-modify-write happens under an
exclusive flock.
"""

import fcntl
import json
import os
import stat
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, Optional


def _state_dir() -> Path:
    override = os.environ.get("KONSOLE_AWARE_STATE_DIR")
    if override:
        return Path(override)
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime and os.path.isabs(runtime) and os.path.isdir(runtime):
        return Path(runtime) / "konsole-aware"
    return Path(f"/tmp/konsole-aware-{os.getuid()}")


STATE_DIR = _state_dir()
STATE_FILE = STATE_DIR / "sessions.json"
LOCK_FILE = STATE_DIR / "sessions.lock"


def _ensure_private_dir(path: Path) -> None:
    """Create the state dir 0700 and refuse one that is a symlink or owned by someone else."""
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    st = os.lstat(path)
    if stat.S_ISLNK(st.st_mode) or st.st_uid != os.getuid():
        raise PermissionError(f"refusing to use state dir {path}: not a directory owned by this user")
    if st.st_mode & 0o077:
        os.chmod(path, 0o700)


class StateManager:
    """Manages active tab states, original titles, durations, and task metadata."""

    def __init__(self):
        self.state_dir = STATE_DIR
        self.state_file = STATE_FILE
        self.lock_file = LOCK_FILE
        try:
            _ensure_private_dir(self.state_dir)
            self._usable = True
        except OSError:
            self._usable = False

    @contextmanager
    def _locked(self) -> Iterator[None]:
        if not self._usable:
            yield
            return
        fd = os.open(self.lock_file, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    def _load_all(self) -> Dict[str, Any]:
        if not self._usable:
            return {}
        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_all(self, data: Dict[str, Any]) -> None:
        if not self._usable:
            return
        try:
            temp_file = self.state_dir / f"sessions.json.tmp.{os.getpid()}"
            fd = os.open(temp_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            temp_file.replace(self.state_file)
        except OSError:
            pass

    def update_session(
        self,
        session_key: str,
        fn: Callable[[Dict[str, Any]], Optional[Dict[str, Any]]],
    ) -> Dict[str, Any]:
        """Atomically apply fn(session_dict) -> updates under the state lock."""
        with self._locked():
            data = self._load_all()
            sess = data.get(session_key, {})
            updates = fn(dict(sess))
            if updates:
                sess.update(updates)
                sess["updated_at"] = time.time()
                data[session_key] = sess
                self._save_all(data)
            return sess

    def get_session(self, session_key: str) -> Dict[str, Any]:
        """Get state for a specific session."""
        return self._load_all().get(session_key, {})

    def set_session(self, session_key: str, **kwargs) -> Dict[str, Any]:
        """Update or create state for a session."""
        return self.update_session(session_key, lambda _s: kwargs)

    def start_task(
        self,
        session_key: str = "",
        task_name: str = "",
        task_type: str = "command",
        detail: str = "",
        original_title: str = "",
        original_color: str = "",
        session_id: str = "",
        **extra: Any,
    ) -> Dict[str, Any]:
        """Record the start of a task or agent run."""
        s_key = session_key or session_id
        now = time.time()

        def _apply(sess: Dict[str, Any]) -> Dict[str, Any]:
            updates = {
                "status": "busy",
                "task_name": task_name,
                "task_type": task_type,
                "detail": detail,
                "start_time": now,
                "end_time": None,
                **extra,
            }
            if original_title and not sess.get("original_title"):
                updates["original_title"] = original_title
            if original_color and not sess.get("original_color"):
                updates["original_color"] = original_color
            return updates

        return self.update_session(s_key, _apply)

    def finish_task(
        self,
        session_key: str = "",
        exit_code: int = 0,
        status: str = "done",
        session_id: str = "",
        **extra: Any,
    ) -> Dict[str, Any]:
        """Record the completion of a task or agent run."""
        s_key = session_key or session_id
        now = time.time()

        def _apply(sess: Dict[str, Any]) -> Dict[str, Any]:
            start = sess.get("start_time") or now
            return {
                "status": status,
                "exit_code": exit_code,
                "end_time": now,
                "duration": max(0.0, now - start),
                **extra,
            }

        return self.update_session(s_key, _apply)

    def clear_session(self, session_key: str) -> None:
        """Remove a session from tracking."""
        with self._locked():
            data = self._load_all()
            if session_key in data:
                del data[session_key]
                self._save_all(data)

    def prune(self, live_keys: set) -> None:
        """Drop state for sessions that no longer exist (keeps custom job/icon of live ones)."""
        with self._locked():
            data = self._load_all()
            stale = [k for k in data if k not in live_keys]
            if stale:
                for k in stale:
                    del data[k]
                self._save_all(data)

    def get_all_sessions(self) -> Dict[str, Any]:
        """Return all tracked sessions."""
        return self._load_all()
