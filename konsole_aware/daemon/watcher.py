"""Background tab watcher and monitor daemon for Konsole sessions."""

import os
import signal
import sys
import time
from typing import Any, Dict, List, Optional
from konsole_aware.config import load_config
from konsole_aware.terminal.konsole import KonsoleTerminal
from konsole_aware.tasks.classifier import CommandClassifier
from konsole_aware.notify.notifier import Notifier
from konsole_aware.state.manager import StateManager


class TabWatcher:
    """Continuously monitors all Konsole tabs, tracking tasks and state changes."""

    def __init__(self, poll_interval: float = 1.0):
        self.poll_interval = poll_interval
        self.config = load_config()
        self.classifier = CommandClassifier()
        self.notifier = Notifier()
        self.state_mgr = StateManager()
        self._running = False
        # session_key -> state dict
        self._tracked: Dict[str, Dict[str, Any]] = {}

    def run_daemon(self) -> None:
        """Run continuous monitoring loop."""
        self._running = True

        def _sig_handler(sig, frame):
            self._running = False

        signal.signal(signal.SIGINT, _sig_handler)
        signal.signal(signal.SIGTERM, _sig_handler)

        while self._running:
            try:
                self.tick()
            except Exception:
                pass
            time.sleep(self.poll_interval)

    def tick(self) -> None:
        """Perform one scan of all Konsole sessions and process state transitions."""
        sessions = KonsoleTerminal.list_all_sessions()
        now = time.time()
        active_keys = set()

        for s in sessions:
            s_key = f"{s['service']}:{s['session_path']}"
            active_keys.add(s_key)

            fg_pid = s["foreground_pid"]
            cmd = s["command"]
            s_num = s["session_num"]
            is_active_tab = s["is_active_tab"]

            prev = self._tracked.get(s_key)

            if prev is None:
                # First time seeing this session
                classified = self.classifier.classify(cmd) if cmd else None
                self._tracked[s_key] = {
                    "fg_pid": fg_pid,
                    "command": cmd,
                    "start_time": now,
                    "is_busy": bool(cmd and classified and classified["category"] != "idle"),
                    "category": classified["category"] if classified else "idle",
                    "task_name": classified["name"] if classified else "",
                    "session_num": s_num,
                    "is_active_tab": is_active_tab,
                    "original_title": s["title0"],
                }
                continue

            # Check if foreground process changed
            prev_pid = prev["fg_pid"]
            prev_cmd = prev["command"]

            if fg_pid != prev_pid:
                # Process changed!
                # 1. Did a previous long-running task finish?
                if prev["is_busy"]:
                    duration = max(0.0, now - prev["start_time"])
                    min_dur = self.config.get("min_duration_seconds", 3.0)
                    is_agent_or_transfer = prev["category"] in ("agent", "transfer", "build", "download")

                    if duration >= min_dur or is_agent_or_transfer:
                        mins, secs = divmod(int(duration), 60)
                        dur_str = f"{mins}m {secs}s" if mins else f"{secs}s"
                        task_label = prev["task_name"] or prev_cmd[:20]

                        # Alert user if tab was unfocused or config allows
                        unfocused_only = self.config.get("alert_only_unfocused", True)
                        if not unfocused_only or not prev["is_active_tab"]:
                            self.notifier.notify(
                                title=f"Task Finished: {task_label}",
                                message=f"Completed in {dur_str} (Tab {s_num})",
                                urgency="normal",
                                is_error=False,
                                play_sound=True,
                            )

                        # Update Konsole tab appearance
                        kterm = KonsoleTerminal(
                            service=s["service"], session_path=s["session_path"]
                        )
                        kterm.set_title(f"✅ [Done {dur_str}] {task_label}")
                        kterm.set_tab_color(self.config.get("colors", {}).get("done", "#10b981"))
                        kterm.set_badge("DONE", enabled=True)

                # 2. Is a new task starting?
                classified = self.classifier.classify(cmd) if cmd else None
                is_busy = bool(cmd and classified and classified["category"] != "idle")

                if is_busy and classified:
                    kterm = KonsoleTerminal(
                        service=s["service"], session_path=s["session_path"]
                    )
                    kterm.set_title(f"{classified['icon']} [{classified['name']}] {classified['detail']}")
                    kterm.set_tab_color(classified["color"])
                    kterm.set_badge(classified["badge"], enabled=True)

                self._tracked[s_key] = {
                    "fg_pid": fg_pid,
                    "command": cmd,
                    "start_time": now,
                    "is_busy": is_busy,
                    "category": classified["category"] if classified else "idle",
                    "task_name": classified["name"] if classified else "",
                    "session_num": s_num,
                    "is_active_tab": is_active_tab,
                    "original_title": prev.get("original_title", s["title0"]),
                }
            else:
                # Process is still running; update active status
                prev["is_active_tab"] = is_active_tab

        # Clean up closed sessions
        for old_key in list(self._tracked.keys()):
            if old_key not in active_keys:
                del self._tracked[old_key]

    def get_dashboard_data(self) -> List[Dict[str, Any]]:
        """Collect current status of all tabs for dashboard display."""
        sessions = KonsoleTerminal.list_all_sessions()
        rows = []
        for s in sessions:
            s_key = f"{s['service']}:{s['session_path']}"
            tracked = self._tracked.get(s_key, {})
            cmd = s["command"]
            classified = self.classifier.classify(cmd) if cmd else None

            status_str = "Idle"
            duration_str = "-"
            if tracked.get("is_busy"):
                dur = int(time.time() - tracked.get("start_time", time.time()))
                mins, secs = divmod(dur, 60)
                duration_str = f"{mins}m {secs}s" if mins else f"{secs}s"
                icon = classified["icon"] if classified else "⏳"
                name = classified["name"] if classified else (cmd[:15] if cmd else "busy")
                status_str = f"{icon} {name}"
            elif cmd:
                status_str = f"Running: {cmd[:20]}"

            rows.append({
                "window": s["window"].split("/")[-1] if "/" in s["window"] else s["window"],
                "session_num": s["session_num"],
                "active": "●" if s["is_active_tab"] else "○",
                "title": s["title0"] or "-",
                "command": cmd or "-",
                "status": status_str,
                "duration": duration_str,
                "color": s["tab_color"] or "-",
            })
        return rows
