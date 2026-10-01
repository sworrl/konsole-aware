"""Tab organizer: reordering, project snapping, sorting, detaching, and split-view clipping.

Konsole's D-Bus API can only move the *active* tab one step at a time
(Window.moveSessionLeft/Right). So every reorder selects a tab, nudges it, then
restores the tab the user had focused, and re-applies tab colours (Konsole re-inserts
the moved tabs, which drops their colour from the tab bar).
"""

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from konsole_aware.agents import identity as agent_identity
from konsole_aware.state.manager import StateManager
from konsole_aware.tabs.groups import TabGroupManager
from konsole_aware.tabs.icons import IconResolver
from konsole_aware.terminal import dbus
from konsole_aware.terminal.konsole import read_cmdline, read_cwd

WIN_IFACE = "org.kde.konsole.Window"
SESS_IFACE = "org.kde.konsole.Session"


def _to_int(val: Optional[str], default: int = -1) -> int:
    try:
        return int(val) if val not in (None, "") else default
    except ValueError:
        return default


def snap_order(tabs: List[Dict[str, Any]], include_user_sessions: bool = True) -> List[Dict[str, Any]]:
    """Compute the snapped tab order for one window.

    - Anchored (pinned) tabs stay first, in their current order.
    - Tabs of the same project form one contiguous cluster, placed where the project's
      first tab currently is.
    - Inside a cluster, agent tabs come first and side by side, ordered by
      `agent_order` (claude, agy, ...), then plain user shells; ties keep current order.
    - With include_user_sessions=False, only agent tabs are pulled together; plain
      shells keep their relative positions.
    """
    anchored = [t for t in tabs if t.get("anchored")]
    rest = [t for t in tabs if not t.get("anchored")]

    def is_member(t: Dict[str, Any]) -> bool:
        return include_user_sessions or t.get("agent") not in (None, agent_identity.USER)

    clusters: Dict[str, List[Dict[str, Any]]] = {}
    for t in rest:
        if t.get("project") and is_member(t):
            clusters.setdefault(t["project"], []).append(t)

    def in_cluster(key: str) -> List[Dict[str, Any]]:
        members = clusters[key]
        return sorted(members, key=lambda t: (agent_identity.agent_rank(t.get("agent")), t["index"]))

    ordered: List[Dict[str, Any]] = []
    emitted = set()
    for t in rest:
        key = t.get("project")
        if key in clusters and is_member(t):
            if key in emitted:
                continue
            emitted.add(key)
            ordered.extend(in_cluster(key))
        else:
            ordered.append(t)
    return anchored + ordered


class TabOrganizer:
    """Handles tab reordering, project snapping, group sorting, detachment, and splits."""

    def __init__(self, service: Optional[str] = None):
        self.qdbus_bin = dbus.qdbus_bin()
        self.service = dbus.canonical_service(service) if service else None
        self.group_mgr = TabGroupManager()
        self.state_mgr = StateManager()
        self.icon_resolver = IconResolver()
        if not self.service:
            services = dbus.konsole_services()
            self.service = services[0] if len(services) == 1 else None
            self._all_services = services
        else:
            self._all_services = [self.service]

    # ---------------------------------------------------------------- queries
    def _win(self, window_path: str, method: str, *args: str) -> Optional[str]:
        return dbus.call(self.service, window_path, f"{WIN_IFACE}.{method}", *args)

    def _sess(self, session_num: str, method: str, *args: str) -> Optional[str]:
        return dbus.call(self.service, f"/Sessions/{session_num}", f"{SESS_IFACE}.{method}", *args)

    def list_windows(self) -> List[str]:
        if not self.service:
            return []
        return [p for p in dbus.list_paths(self.service) if p.startswith("/Windows/")]

    def get_window_sessions(self, window_path: str) -> List[str]:
        """Session ids of a window in visual order (split tabs contribute several ids)."""
        return (self._win(window_path, "sessionList") or "").split()

    def get_window_tabs(self, window_path: str) -> List[List[str]]:
        """Tabs of a window in visual order, each a list of its pane session ids."""
        sessions = self.get_window_sessions(window_path)
        hierarchy = (self._win(window_path, "viewHierarchy") or "").split()
        return dbus.split_tabs(sessions, hierarchy)

    def find_session_window(self, session_num: str) -> Optional[Tuple[str, str]]:
        """Find (window_path, service) of the window containing a session."""
        for srv in self._all_services if not self.service else [self.service]:
            prev, self.service = self.service, srv
            for win in self.list_windows():
                if str(session_num) in self.get_window_sessions(win):
                    return (win, srv)
            self.service = prev
        return None

    def describe_tab(self, panes: List[str], index: int) -> Dict[str, Any]:
        """Collect project/agent metadata for one tab (first pane is representative)."""
        lead = panes[0]
        s_key = f"{self.service}:/Sessions/{lead}"
        state = self.state_mgr.get_session(s_key)
        agent = agent_identity.USER
        agent_pid = None
        cwd = ""
        for pane in panes:
            p_key = f"{self.service}:/Sessions/{pane}"
            p_state = state if pane == lead else self.state_mgr.get_session(p_key)
            shell_pid = _to_int(self._sess(pane, "processId"))
            fg_pid = _to_int(self._sess(pane, "foregroundProcessId"))
            found, found_pid = agent_identity.resolve_session_agent(p_state, fg_pid, shell_pid)
            if not cwd:
                cwd = read_cwd(found_pid or -1) or read_cwd(shell_pid)
            if found != agent_identity.USER:
                agent, agent_pid = found, found_pid
                cwd = read_cwd(found_pid or -1) or read_cwd(shell_pid) or cwd
                break
        project = ""
        if cwd:
            try:
                root = self.icon_resolver.find_project_root(cwd)
                if root not in (Path.home(), Path("/")) and root not in Path.home().parents:
                    project = str(root)
            except Exception:
                project = ""
        grp = self.group_mgr.get_tab_group(s_key)
        return {
            "session_num": lead,
            "panes": panes,
            "index": index,
            "session_key": s_key,
            "anchored": self.group_mgr.is_anchored(s_key),
            "agent": agent,
            "agent_pid": agent_pid,
            "cwd": cwd,
            "project": project,
            "group_name": grp["name"] if grp else "",
            "status": state.get("status", "idle"),
            "title": self._sess(lead, "title", "1") or "",
            "id_num": _to_int(lead, 9999),
        }

    def describe_window(self, window_path: str) -> List[Dict[str, Any]]:
        return [self.describe_tab(panes, i) for i, panes in enumerate(self.get_window_tabs(window_path))]

    # ---------------------------------------------------------------- moving
    def _tab_colors(self, window_path: str) -> Dict[str, str]:
        colors = {}
        for panes in self.get_window_tabs(window_path):
            for pane in panes:
                colors[pane] = self._sess(pane, "tabColor") or ""
        return colors

    def _reapply_colors(self, colors: Dict[str, str]) -> None:
        """Konsole only repaints a tab colour when it changes, so bounce through a
        near-identical colour to force the moved tabs to get theirs back."""
        for pane, color in colors.items():
            if not color or color.lower() in ("#000000", "#ff000000"):
                continue
            bounce = "#000001" if color.lower() != "#000001" else "#000002"
            self._sess(pane, "setTabColor", bounce)
            self._sess(pane, "setTabColor", color)

    def _move_tab(self, window_path: str, session_num: str, target_idx: int) -> bool:
        tabs = [t[0] for t in self.get_window_tabs(window_path)]
        lead = next((t[0] for t in self.get_window_tabs(window_path) if session_num in t), None)
        if lead is None:
            return False
        cur_idx = tabs.index(lead)
        target_idx = max(0, min(len(tabs) - 1, target_idx))
        diff = cur_idx - target_idx
        if diff == 0:
            return True
        method = "moveSessionLeft" if diff > 0 else "moveSessionRight"
        for _ in range(abs(diff)):
            # moveSession* acts on the active tab: re-select ours before every step so a
            # focus change in between can never move some other tab.
            self._win(window_path, "setCurrentSession", str(session_num))
            if self._win(window_path, method) is None:
                return False
        return True

    def move_session_to_index(self, window_path: str, session_num: str, target_idx: int) -> bool:
        """Move a tab to a visual tab index, keeping the user's focused tab and tab colours."""
        if not self.qdbus_bin or not self.service:
            return False
        current = self._win(window_path, "currentSession")
        colors = self._tab_colors(window_path)
        ok = self._move_tab(window_path, str(session_num), target_idx)
        if current:
            self._win(window_path, "setCurrentSession", current)
        self._reapply_colors(colors)
        return ok

    def apply_order(self, window_path: str, desired: List[str]) -> List[str]:
        """Reorder a window's tabs to `desired` (list of lead session ids)."""
        current = self._win(window_path, "currentSession")
        colors = self._tab_colors(window_path)
        moved = False
        for target_idx, s_num in enumerate(desired):
            tabs = [t[0] for t in self.get_window_tabs(window_path)]
            if s_num in tabs and tabs.index(s_num) != target_idx:
                self._move_tab(window_path, s_num, target_idx)
                moved = True
        if moved:
            if current:
                self._win(window_path, "setCurrentSession", current)
            self._reapply_colors(colors)
        return [t[0] for t in self.get_window_tabs(window_path)]

    # ---------------------------------------------------------------- snapping
    def snap_window(self, window_path: str, dry_run: bool = False) -> Dict[str, Any]:
        """Cluster same-project tabs and put agent tabs side by side in one window."""
        from konsole_aware.config import load_config
        include_user = load_config().get("snap_include_user_sessions", True)
        tabs = self.describe_window(window_path)
        ordered = snap_order(tabs, include_user_sessions=include_user)
        desired = [t["session_num"] for t in ordered]
        before = [t["session_num"] for t in tabs]
        after = before if dry_run or desired == before else self.apply_order(window_path, desired)
        return {"window": window_path, "before": before, "after": after, "tabs": ordered, "changed": before != after}

    def snap_session(self, session_num: str, require_current: bool = False) -> bool:
        """Move one tab next to the other tabs of its project in the same window.

        With require_current=True nothing happens unless the tab is the window's active
        tab, so auto-snapping never steals focus from another tab.
        """
        if not self.qdbus_bin:
            return False
        found = self.find_session_window(session_num)
        if not found:
            return False
        window_path, _srv = found
        if require_current and self._win(window_path, "currentSession") != str(session_num):
            return False
        tabs = self.describe_window(window_path)
        me = next((t for t in tabs if session_num in t["panes"]), None)
        if me is None or not me["project"] or me["anchored"]:
            return False
        from konsole_aware.config import load_config
        include_user = load_config().get("snap_include_user_sessions", True)
        mates = [
            t for t in tabs
            if t is not me and t["project"] == me["project"] and not t["anchored"]
            and (include_user or t["agent"] != agent_identity.USER)
        ]
        if not mates:
            return False
        cluster = sorted(mates + [me], key=lambda t: (agent_identity.agent_rank(t["agent"]), t["index"]))
        pos = cluster.index(me)
        # Place me right after the mate that precedes me in cluster order (or before the first one).
        if pos > 0:
            anchor = cluster[pos - 1]
            target = anchor["index"] + (1 if anchor["index"] < me["index"] else 0)
        else:
            anchor = cluster[1]
            target = anchor["index"] - (1 if anchor["index"] > me["index"] else 0)
        if target == me["index"]:
            return True
        current = self._win(window_path, "currentSession")
        colors = self._tab_colors(window_path)
        ok = self._move_tab(window_path, me["session_num"], target)
        if current:
            self._win(window_path, "setCurrentSession", current)  # keep the focused pane
        self._reapply_colors(colors)
        return ok

    def snap_all(self, dry_run: bool = False) -> List[Dict[str, Any]]:
        results = []
        for srv in self._all_services:
            self.service = srv
            for win in self.list_windows():
                results.append(self.snap_window(win, dry_run=dry_run))
        return results

    # ---------------------------------------------------------------- organize
    def organize_window(self, window_path: str, sort_by: str = "status") -> List[str]:
        """
        Sort and reorder tabs in a window according to policy:
        - 'status': Anchored first, then Busy, then Waiting, then Done, then Idle
        - 'group': Anchored first, then grouped by group name, then ungrouped
        - 'name': Anchored first, then alphabetical by tab title
        - 'project': Anchored first, then same-project clusters with agents side by side
        - 'id': Numerical order
        """
        if sort_by == "project":
            return self.snap_window(window_path)["after"]

        tabs = self.describe_window(window_path)
        if len(tabs) <= 1:
            return [t["session_num"] for t in tabs]

        state_rank = {"busy": 0, "waiting": 1, "blocked": 2, "error": 2, "done": 3}

        def sort_key(item: Dict[str, Any]) -> Tuple:
            anchor_rank = 0 if item["anchored"] else 1
            if sort_by == "status":
                status = item["status"]
                if status in ("thinking", "tool", "subagents", "compacting"):
                    status = "busy"
                return (anchor_rank, state_rank.get(status, 4), item["index"])
            if sort_by == "group":
                return (anchor_rank, item["group_name"] or "￿", item["index"])
            if sort_by == "name":
                return (anchor_rank, item["title"].lower(), item["index"])
            return (anchor_rank, item["id_num"])

        desired = [t["session_num"] for t in sorted(tabs, key=sort_key)]
        return self.apply_order(window_path, desired)

    def organize_all(self, sort_by: str = "status") -> Dict[str, List[str]]:
        """Sort and organize tabs in all Konsole windows."""
        results = {}
        for srv in self._all_services:
            self.service = srv
            for win in self.list_windows():
                results[win] = self.organize_window(win, sort_by=sort_by)
        return results

    # ------------------------------------------------------- window actions
    def _main_window_action(self, session_num: str, action: str) -> bool:
        """Trigger a KXMLGui action on the main window that holds the session.

        /konsole/MainWindow_N numbering is KMainWindow's own and need not match
        /Windows/N, so the right main window is the one whose active session we just set.
        """
        found = self.find_session_window(session_num)
        if not found:
            return False
        win_path, _srv = found
        self._win(win_path, "setCurrentSession", str(session_num))
        candidates = [p for p in dbus.list_paths(self.service) if p.startswith("/konsole/MainWindow_")]
        win_idx = win_path.rsplit("/", 1)[-1]
        preferred = f"/konsole/MainWindow_{win_idx}"
        if len(candidates) == 1:
            targets = candidates
        elif preferred in candidates:
            targets = [preferred]
        else:
            return False
        return dbus.call(self.service, targets[0], "activateAction", action) is not None

    def detach_tab(self, session_num: str) -> bool:
        """Detach a tab into its own new Konsole window."""
        return self._main_window_action(str(session_num), "detach-tab")

    def split_view(self, session_num: str, orientation: str = "left-right") -> bool:
        """Split view horizontally (left-right) or vertically (top-bottom)."""
        action = "split-view-left-right" if orientation in ("left-right", "horizontal", "h") else "split-view-top-bottom"
        return self._main_window_action(str(session_num), action)

    def equalize_splits(self, session_num: str) -> bool:
        """Balance all split views in the window."""
        return self._main_window_action(str(session_num), "equal-size-view")

    def toggle_maximize_split(self, session_num: str) -> bool:
        """Toggle maximize on the currently focused split view."""
        return self._main_window_action(str(session_num), "toggle-maximize-current-view")

    # Exposed for callers that need raw command lines (dashboard).
    read_cmdline = staticmethod(read_cmdline)
