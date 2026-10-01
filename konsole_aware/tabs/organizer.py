"""Tab organizer: reordering, sorting, detaching, and split-view clipping."""

import os
import shutil
import subprocess
from typing import Any, Dict, List, Optional, Tuple

from konsole_aware.terminal.konsole import KonsoleTerminal
from konsole_aware.tabs.groups import TabGroupManager
from konsole_aware.state.manager import StateManager
from konsole_aware.tasks.classifier import CommandClassifier


class TabOrganizer:
    """Handles tab reordering, group sorting, window detachment, and split-view clipping."""

    def __init__(self, service: Optional[str] = None):
        self.qdbus_bin = (
            shutil.which("qdbus")
            or shutil.which("qdbus-qt6")
            or shutil.which("qdbus-qt5")
        )
        self.service = service or os.environ.get("KONSOLE_DBUS_SERVICE")
        self.group_mgr = TabGroupManager()
        self.state_mgr = StateManager()
        self.classifier = CommandClassifier()

        if not self.service and self.qdbus_bin:
            self._discover_service()

    def _discover_service(self) -> None:
        try:
            res = subprocess.run([self.qdbus_bin], capture_output=True, text=True, timeout=1.0)
            for line in res.stdout.splitlines():
                s = line.strip()
                if "org.kde.konsole" in s.lower() or s.startswith(":"):
                    self.service = s
                    break
        except Exception:
            pass

    def get_window_sessions(self, window_path: str) -> List[str]:
        """Return list of session numbers in window in visual order."""
        if not self.qdbus_bin or not self.service:
            return []
        try:
            res = subprocess.run(
                [self.qdbus_bin, self.service, window_path, "org.kde.konsole.Window.sessionList"],
                capture_output=True,
                text=True,
                timeout=1.0,
            )
            return res.stdout.strip().split()
        except Exception:
            return []

    def move_session_to_index(self, window_path: str, session_num: str, target_idx: int) -> bool:
        """Move a session to a specific tab index within its window."""
        if not self.qdbus_bin or not self.service:
            return False

        cur_list = self.get_window_sessions(window_path)
        if session_num not in cur_list:
            return False

        cur_idx = cur_list.index(session_num)
        diff = cur_idx - target_idx
        if diff == 0:
            return True

        # Activate the session first
        subprocess.run(
            [self.qdbus_bin, self.service, window_path, "org.kde.konsole.Window.setCurrentSession", str(session_num)],
            capture_output=True,
            timeout=1.0,
        )

        if diff > 0:
            # Need to move left
            for _ in range(diff):
                subprocess.run(
                    [self.qdbus_bin, self.service, window_path, "org.kde.konsole.Window.moveSessionLeft"],
                    capture_output=True,
                    timeout=1.0,
                )
        else:
            # Need to move right
            for _ in range(abs(diff)):
                subprocess.run(
                    [self.qdbus_bin, self.service, window_path, "org.kde.konsole.Window.moveSessionRight"],
                    capture_output=True,
                    timeout=1.0,
                )

        return True

    def find_session_window(self, session_num: str) -> Optional[Tuple[str, str]]:
        """Find the (window_path, mainwindow_path) containing a given session."""
        if not self.qdbus_bin or not self.service:
            return None
        try:
            res = subprocess.run([self.qdbus_bin, self.service], capture_output=True, text=True, timeout=1.0)
            windows = [l.strip() for l in res.stdout.splitlines() if l.strip().startswith("/Windows/")]
            for win in windows:
                s_list = self.get_window_sessions(win)
                if str(session_num) in s_list:
                    win_idx = win.split("/")[-1]
                    main_win = f"/konsole/MainWindow_{win_idx}"
                    return (win, main_win)
        except Exception:
            pass
        return None

    def detach_tab(self, session_num: str) -> bool:
        """Detach a tab into its own new Konsole window."""
        win_info = self.find_session_window(session_num)
        if not win_info:
            return False
        win_path, main_win_path = win_info

        # Select the session
        subprocess.run(
            [self.qdbus_bin, self.service, win_path, "org.kde.konsole.Window.setCurrentSession", str(session_num)],
            capture_output=True,
            timeout=1.0,
        )

        # Trigger detach-tab action on MainWindow
        res = subprocess.run(
            [self.qdbus_bin, self.service, main_win_path, "activateAction", "detach-tab"],
            capture_output=True,
            text=True,
            timeout=1.0,
        )
        return res.returncode == 0

    def split_view(self, session_num: str, orientation: str = "left-right") -> bool:
        """Split view horizontally (left-right) or vertically (top-bottom)."""
        win_info = self.find_session_window(session_num)
        if not win_info:
            return False
        win_path, main_win_path = win_info

        # Select session
        subprocess.run(
            [self.qdbus_bin, self.service, win_path, "org.kde.konsole.Window.setCurrentSession", str(session_num)],
            capture_output=True,
            timeout=1.0,
        )

        action_name = "split-view-left-right" if orientation in ("left-right", "horizontal", "h") else "split-view-top-bottom"
        res = subprocess.run(
            [self.qdbus_bin, self.service, main_win_path, "activateAction", action_name],
            capture_output=True,
            text=True,
            timeout=1.0,
        )
        return res.returncode == 0

    def equalize_splits(self, session_num: str) -> bool:
        """Balance all split views in the window."""
        win_info = self.find_session_window(session_num)
        if not win_info:
            return False
        _, main_win_path = win_info
        res = subprocess.run(
            [self.qdbus_bin, self.service, main_win_path, "activateAction", "equal-size-view"],
            capture_output=True,
            text=True,
            timeout=1.0,
        )
        return res.returncode == 0

    def toggle_maximize_split(self, session_num: str) -> bool:
        """Toggle maximize on the currently focused split view."""
        win_info = self.find_session_window(session_num)
        if not win_info:
            return False
        _, main_win_path = win_info
        res = subprocess.run(
            [self.qdbus_bin, self.service, main_win_path, "activateAction", "toggle-maximize-current-view"],
            capture_output=True,
            text=True,
            timeout=1.0,
        )
        return res.returncode == 0

    def organize_window(self, window_path: str, sort_by: str = "status") -> List[str]:
        """
        Sort and reorder tabs in a window according to policy:
        - 'status': Anchored first, then Busy, then Done, then Idle
        - 'group': Anchored first, then grouped by group name, then ungrouped
        - 'name': Anchored first, then alphabetical by tab title
        - 'id': Numerical order
        """
        sessions = self.get_window_sessions(window_path)
        if len(sessions) <= 1:
            return sessions

        # Gather metadata for each session in window
        sess_data = []
        for s_num in sessions:
            s_path = f"/Sessions/{s_num}"
            s_key = f"{self.service}:{s_path}"
            is_anchored = self.group_mgr.is_anchored(s_key)
            grp = self.group_mgr.get_tab_group(s_key)
            grp_name = grp["name"] if grp else "zzz_ungrouped"

            # Check status
            tracked = self.state_mgr.get_session(s_key)
            status = tracked.get("status", "idle")

            # Check foreground process
            fg_pid_str = subprocess.run(
                [self.qdbus_bin, self.service, s_path, "org.kde.konsole.Session.foregroundProcessId"],
                capture_output=True,
                text=True,
                timeout=1.0,
            ).stdout.strip()
            title = subprocess.run(
                [self.qdbus_bin, self.service, s_path, "org.kde.konsole.Session.title", "0"],
                capture_output=True,
                text=True,
                timeout=1.0,
            ).stdout.strip()

            cmd = ""
            if fg_pid_str.isdigit() and int(fg_pid_str) > 0:
                try:
                    with open(f"/proc/{fg_pid_str}/cmdline", "rb") as f:
                        cmd = f.read().replace(b"\x00", b" ").decode(errors="ignore").strip()
                except Exception:
                    pass

            is_busy = status == "busy" or bool(cmd and any(a in cmd for a in ("agy", "claude", "rsync", "cargo", "make")))

            sess_data.append({
                "session_num": s_num,
                "session_key": s_key,
                "is_anchored": is_anchored,
                "group_name": grp_name,
                "is_busy": is_busy,
                "status": status,
                "title": title,
                "id_num": int(s_num) if s_num.isdigit() else 9999,
            })

        # Sorting key based on sort_by
        def sort_key(item):
            # Anchored ALWAYS comes first (0 for anchored, 1 for unanchored)
            anchor_rank = 0 if item["is_anchored"] else 1

            if sort_by == "status":
                # Busy rank (0 for busy, 1 for done, 2 for idle)
                if item["is_busy"]:
                    state_rank = 0
                elif item["status"] == "done":
                    state_rank = 1
                else:
                    state_rank = 2
                return (anchor_rank, state_rank, item["id_num"])

            elif sort_by == "group":
                return (anchor_rank, item["group_name"], item["id_num"])

            elif sort_by == "name":
                return (anchor_rank, item["title"].lower(), item["id_num"])

            elif sort_by == "id":
                return (anchor_rank, item["id_num"])

            return (anchor_rank, item["id_num"])

        sorted_items = sorted(sess_data, key=sort_key)
        desired_order = [item["session_num"] for item in sorted_items]

        # Reorder sessions to match desired_order
        for target_idx, s_num in enumerate(desired_order):
            self.move_session_to_index(window_path, s_num, target_idx)

        return desired_order

    def organize_all(self, sort_by: str = "status") -> Dict[str, List[str]]:
        """Sort and organize tabs in all Konsole windows."""
        if not self.qdbus_bin or not self.service:
            return {}
        results = {}
        try:
            res = subprocess.run([self.qdbus_bin, self.service], capture_output=True, text=True, timeout=1.0)
            windows = [l.strip() for l in res.stdout.splitlines() if l.strip().startswith("/Windows/")]
            for win in windows:
                results[win] = self.organize_window(win, sort_by=sort_by)
        except Exception:
            pass
        return results
