"""Background tab watcher and monitor daemon for Konsole sessions."""

import os
import signal
import time
from typing import Any, Dict, List, Optional
from konsole_aware.agents import identity as agent_identity
from konsole_aware.config import load_config
from konsole_aware.terminal.konsole import KonsoleTerminal
from konsole_aware.tasks.classifier import CommandClassifier
from konsole_aware.notify.notifier import Notifier
from konsole_aware.state.manager import StateManager
from konsole_aware.tabs.groups import TabGroupManager
from konsole_aware.tabs.icons import IconResolver
from konsole_aware.tabs.title import TitleComposer


def _fmt_duration(seconds: float) -> str:
    mins, secs = divmod(int(max(0, seconds)), 60)
    hours, mins = divmod(mins, 60)
    if hours:
        return f"{hours}h {mins}m"
    return f"{mins}m {secs}s" if mins else f"{secs}s"


class TabWatcher:
    """Continuously monitors all Konsole tabs, tracking tasks and state changes.

    Tabs whose shell or agent hooks are active (state `hooked`) are left alone: the
    hooks know exit codes and agent states precisely, so the daemon only covers tabs
    without hooks (e.g. shells that never sourced the integration).
    """

    def __init__(self, poll_interval: float = 1.0):
        self.poll_interval = poll_interval
        self.config = load_config()
        self.classifier = CommandClassifier()
        self.notifier = Notifier()
        self.state_mgr = StateManager()
        self.group_mgr = TabGroupManager()
        self.icon_resolver = IconResolver()
        self.composer = TitleComposer()
        self._running = False
        # session_key -> state dict
        self._tracked: Dict[str, Dict[str, Any]] = {}
        self._sessions: List[Dict[str, Any]] = []

    def run_daemon(self) -> None:
        """Run continuous monitoring loop."""
        self._running = True

        def _sig_handler(sig, frame):
            self._running = False

        signal.signal(signal.SIGINT, _sig_handler)
        signal.signal(signal.SIGTERM, _sig_handler)

        ticks = 0
        while self._running:
            try:
                self.tick(manage=True)
                ticks += 1
                if ticks % 40 == 0 and self._sessions:
                    self._prune_closed()
            except Exception:
                pass
            time.sleep(self.poll_interval)

    def _prune_closed(self) -> None:
        """Forget closed Konsole tabs. Only keys of Konsole services seen in this scan are
        candidates, so state of other terminals or of a briefly unreachable Konsole stays."""
        live = {s["session_key"] for s in self._sessions}
        services = {s["service"] for s in self._sessions}
        stale_ok = {k for k in self.state_mgr.get_all_sessions()
                    if k.split(":/Sessions/")[0] in services and k not in live}
        if stale_ok:
            keep = set(self.state_mgr.get_all_sessions()) - stale_ok
            self.state_mgr.prune(keep)

    def tick(self, manage: bool = False) -> None:
        """Scan all Konsole sessions; with manage=True also update titles and send alerts."""
        sessions = KonsoleTerminal.list_all_sessions()
        self._sessions = sessions
        now = time.time()
        active_keys = set()

        for s in sessions:
            s_key = s["session_key"]
            active_keys.add(s_key)
            fg_pid = s["foreground_pid"]
            cmd = "" if s["is_idle_shell"] else s["command"]
            prev = self._tracked.get(s_key)
            classified = self.classifier.classify(cmd) if cmd else None
            is_busy = bool(classified and classified["category"] != "idle")

            if prev is None or fg_pid != prev["fg_pid"]:
                if prev is not None and manage:
                    self._on_transition(s, prev, classified, is_busy, now)
                state = self.state_mgr.get_session(s_key)
                start = now
                if prev is None and state.get("status") in ("busy", "waiting") and state.get("start_time"):
                    start = state["start_time"]
                self._tracked[s_key] = {
                    "fg_pid": fg_pid,
                    "command": cmd,
                    "start_time": start,
                    "is_busy": is_busy,
                    "category": classified["category"] if classified else "idle",
                    "task_name": classified["name"] if classified else "",
                    "session_num": s["session_num"],
                    "is_active_tab": s["is_active_tab"],
                }
            else:
                prev["is_active_tab"] = s["is_active_tab"]

        self._reconcile_agents(active_keys)

        for old_key in list(self._tracked.keys()):
            if old_key not in active_keys:
                del self._tracked[old_key]

    def _reconcile_agents(self, live_keys: set) -> None:
        """End agent turns that no hook will end (Esc interrupts, agent exits, usage limits)."""
        from konsole_aware.agents import watchdog
        for key, sess in self.state_mgr.get_all_sessions().items():
            agent = sess.get("agent")
            if key in live_keys and agent in ("claude", "agy"):
                try:
                    watchdog.check_once(key, agent, self.state_mgr)
                except Exception:
                    pass

    def _on_transition(
        self,
        s: Dict[str, Any],
        prev: Dict[str, Any],
        classified: Optional[Dict[str, Any]],
        is_busy: bool,
        now: float,
    ) -> None:
        s_key = s["session_key"]
        if self.state_mgr.get_session(s_key).get("hooked"):
            return
        kterm = KonsoleTerminal(service=s["service"], session_path=s["session_path"])
        cwd = s.get("cwd") or None

        if prev["is_busy"]:
            duration = max(0.0, now - prev["start_time"])
            min_dur = self.config.get("min_duration_seconds", 3.0)
            task_label = prev["task_name"] or prev["command"][:20]
            dur_str = _fmt_duration(duration)
            if duration >= min_dur or prev["category"] in ("agent", "transfer", "build", "download"):
                if not self.config.get("alert_only_unfocused", True) or not prev["is_active_tab"]:
                    self.notifier.notify(
                        title=f"Task Finished: {task_label}",
                        message=f"Completed in {dur_str} (Tab {s['session_num']})",
                        urgency="normal",
                        is_error=False,
                        play_sound=True,
                    )
                kterm.set_title(self.composer.compose(
                    session_key=s_key,
                    task_category=prev.get("category", "command"),
                    status="done",
                    detail=f"{task_label} ({dur_str})",
                    cwd=cwd,
                    agent=agent_identity.USER,
                ))
                kterm.set_tab_color(self.config.get("colors", {}).get("done", "#10b981"))
                kterm.set_badge("DONE", enabled=True)
            elif not is_busy:
                kterm.set_title(self.composer.compose(
                    session_key=s_key, task_category="idle", status="idle", cwd=cwd, agent=agent_identity.USER,
                ))
                kterm.reset_tab_color()
                kterm.set_badge("", enabled=False)

        if is_busy and classified:
            agent = classified.get("agent") or agent_identity.USER
            kterm.set_title(self.composer.compose(
                session_key=s_key,
                task_category=classified["category"],
                status="busy",
                detail=classified["detail"] or classified["name"],
                cwd=cwd,
                agent=agent,
            ))
            kterm.set_tab_color(classified["color"])
            kterm.set_badge(classified["badge"], enabled=True)

    def get_dashboard_data(self) -> List[Dict[str, Any]]:
        """Collect current status of all tabs for dashboard display (uses the last tick's scan)."""
        sessions = self._sessions or KonsoleTerminal.list_all_sessions()
        now = time.time()
        rows = []
        for s in sessions:
            s_key = s["session_key"]
            tracked = self._tracked.get(s_key, {})
            state = self.state_mgr.get_session(s_key)
            cmd = "" if s["is_idle_shell"] else s["command"]
            agent, agent_pid = agent_identity.resolve_session_agent(state, s["foreground_pid"], s["shell_pid"])
            if agent == agent_identity.USER and state.get("agent"):
                state = {k: v for k, v in state.items() if not k.startswith("agent")}

            status_str = "Idle"
            duration_str = "-"
            start = None
            if agent != agent_identity.USER:
                a_status = state.get("agent_status") or state.get("status") or "running"
                detail = state.get("detail") or ""
                status_str = f"{a_status}: {detail}" if detail and a_status != "idle" else a_status
                if state.get("status") in ("busy", "waiting"):
                    start = state.get("turn_start") or state.get("start_time")
                elif state.get("status") in ("done", "error") and state.get("duration") is not None:
                    duration_str = _fmt_duration(state["duration"])
            elif tracked.get("is_busy") or cmd:
                classified = self.classifier.classify(cmd) if cmd else None
                icon = classified["icon"] if classified else "⏳"
                name = classified["name"] if classified else cmd[:15]
                status_str = f"{icon} {name}"
                start = state.get("start_time") if state.get("status") == "busy" else tracked.get("start_time")
            if start:
                duration_str = _fmt_duration(now - start)

            cwd = s.get("cwd") or ""
            if agent_pid:
                try:
                    cwd = os.readlink(f"/proc/{agent_pid}/cwd")
                except OSError:
                    pass
            project = self.icon_resolver.find_project_root(cwd).name if cwd else ""

            grp = self.group_mgr.get_tab_group(s_key)
            rows.append({
                "window": s["window"].split("/")[-1] if "/" in s["window"] else s["window"],
                "tab_index": s.get("tab_index", -1),
                "session_num": s["session_num"],
                "session_key": s_key,
                "active": "●" if s["is_active_tab"] else "○",
                "is_anchored": self.group_mgr.is_anchored(s_key),
                "agent": agent,
                "agent_tag": agent_identity.identity_tag(agent),
                "agent_mode": state.get("agent_mode") if agent != agent_identity.USER else None,
                "project": project,
                "cwd": cwd,
                "group": grp["name"] if grp else "",
                "group_color": grp.get("color", "") if grp else "",
                "group_icon": grp.get("icon", "") if grp else "",
                "title": s["title1"] or s["title0"] or "-",
                "command": cmd or "-",
                "status": status_str,
                "duration": duration_str,
                "color": s["tab_color"] or "-",
            })
        return rows
