"""KDE Konsole D-Bus terminal controller."""

import os
from typing import Any, Dict, List, Optional, Tuple
from konsole_aware.terminal import dbus
from konsole_aware.terminal.base import BaseTerminal
from konsole_aware.terminal.ansi import AnsiTerminal, sanitize
from konsole_aware.config import load_config


def read_cmdline(pid: int) -> str:
    if pid <= 0:
        return ""
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            return f.read().replace(b"\x00", b" ").decode(errors="ignore").strip()
    except OSError:
        return ""


def read_cwd(pid: int) -> str:
    if pid <= 0:
        return ""
    try:
        return os.readlink(f"/proc/{pid}/cwd")
    except OSError:
        return ""


def _ancestor_pids() -> set:
    ancestors = set()
    pid = os.getpid()
    while pid > 1 and pid not in ancestors:
        ancestors.add(pid)
        try:
            with open(f"/proc/{pid}/stat", "r", encoding="utf-8", errors="ignore") as f:
                pid = int(f.read().rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            break
    return ancestors


def _to_int(val: Optional[str], default: int = -1) -> int:
    try:
        return int(val) if val not in (None, "") else default
    except ValueError:
        return default


def konsole_title_text(title: str) -> str:
    """Make text safe for Session.setTitle(1, ...): Konsole stores it as the tab title
    *format*, so %d/%n/%w/... would be expanded. Replace % with a fullwidth percent."""
    return sanitize(title).replace("%", "％")


_DISCOVERY_CACHE: Dict[int, Tuple[str, str]] = {}


def badge_text(text: str) -> str:
    """Badge-safe text. Konsole sizes the badge box with the (bold) terminal font's metrics,
    but emoji are drawn from a wider fallback font, so any emoji makes the text overflow and
    get cut off. Keep letters, digits, punctuation and the middle dot; drop pictographs."""
    import unicodedata
    out = []
    for ch in sanitize(text):
        if ch in "·•":
            out.append(ch)
        elif ord(ch) < 0x2000 or unicodedata.category(ch)[0] in ("L", "N"):
            out.append(ch)
    return " ".join("".join(out).split())


class KonsoleTerminal(BaseTerminal):
    """Deep integration controller for KDE Konsole via D-Bus."""

    def __init__(
        self,
        service: Optional[str] = None,
        session_path: Optional[str] = None,
    ):
        self.config = load_config()
        self.qdbus_bin = dbus.qdbus_bin()
        self.ansi = AnsiTerminal()

        explicit_session = session_path
        self.service = dbus.canonical_service(service or os.environ.get("KONSOLE_DBUS_SERVICE"))
        self.session_path = explicit_session or os.environ.get("KONSOLE_DBUS_SESSION")
        if self.session_path and not self.session_path.startswith("/Sessions/"):
            self.session_path = f"/Sessions/{self.session_path}"

        if explicit_session and not self.service:
            # A specific session was requested (e.g. --tab from the menu hook or another
            # shell): find which Konsole instance owns it instead of guessing by ancestry.
            for srv in dbus.konsole_services():
                if self.session_path in dbus.list_paths(srv):
                    self.service = srv
                    break
        elif not self.service or not self.session_path:
            self._auto_discover()

    def _auto_discover(self) -> None:
        """Find the Konsole session this process runs in by matching process ancestry."""
        if not self.qdbus_bin or dbus.dbus_disabled():
            return
        ancestors = _ancestor_pids()
        for pid in ancestors:
            if pid in _DISCOVERY_CACHE:
                cached_srv, cached_sess = _DISCOVERY_CACHE[pid]
                self.service = cached_srv
                self.session_path = cached_sess
                return

        services = [self.service] if self.service else dbus.konsole_services()
        for srv in services:
            for sess in (p for p in dbus.list_paths(srv) if p.startswith("/Sessions/")):
                spid = _to_int(dbus.call(srv, sess, "org.kde.konsole.Session.processId"))
                if spid in ancestors:
                    self.service = srv
                    self.session_path = sess
                    for a in ancestors:
                        _DISCOVERY_CACHE[a] = (srv, sess)
                    return

    def _dbus_call(self, service: str, path: str, method: str, *args) -> Optional[str]:
        return dbus.call(service, path, method, *args)

    def _call(self, method: str, *args) -> Optional[str]:
        return dbus.call(self.service, self.session_path, f"org.kde.konsole.Session.{method}", *args)

    def is_available(self) -> bool:
        return bool(self.service and self.session_path and self.qdbus_bin and not dbus.dbus_disabled())

    def get_session_id(self) -> str:
        if self.service and self.session_path:
            return f"{self.service}:{self.session_path}"
        return self.ansi.get_session_id()

    def get_session_num(self) -> Optional[int]:
        if self.session_path and self.session_path.startswith("/Sessions/"):
            try:
                return int(self.session_path.split("/")[-1])
            except ValueError:
                pass
        return None

    def get_title(self, role: int = 1) -> str:
        if not self.is_available():
            return ""
        return self._call("title", str(role)) or ""

    def get_tab_title_format(self, context: int = 0) -> str:
        if not self.is_available():
            return ""
        return self._call("tabTitleFormat", str(context)) or ""

    def set_tab_title_format(self, context: int, fmt: str) -> bool:
        if not self.is_available():
            return False
        return self._call("setTabTitleFormat", str(context), fmt) is not None

    def set_title(self, title: str, role: int = 0) -> bool:
        """Set the tab title and taskbar entry.

        DisplayedTitleRole (1) is what the tab shows; Konsole stores it as the literal
        tab title format, so it survives the shell's / agent's OSC 0/2 title updates.
        NameRole (0) is only a fallback, kept in sync for tools that read it.
        """
        if not self.is_available():
            return self.ansi.set_title(title, role)
        text = konsole_title_text(title)
        res1 = self._call("setTitle", "1", text)
        self._call("setTitle", "0", text)
        return res1 is not None

    def get_tab_color(self) -> str:
        if not self.is_available():
            return ""
        return self._call("tabColor") or ""

    def set_tab_color(self, hex_color: str) -> bool:
        if not self.is_available() or not self.config.get("enable_tab_colors", True):
            return False
        return self._call("setTabColor", hex_color) is not None

    def reset_tab_color(self) -> bool:
        if not self.is_available():
            return False
        # An invalid colour clears the tab colour back to the theme default.
        return self._call("setTabColor", "") is not None

    def get_process_id(self) -> int:
        """Get the PID of the shell running in this session."""
        if not self.is_available():
            return -1
        return _to_int(self._call("processId"))

    def get_cwd(self) -> str:
        """Get the current working directory of the shell running in this session."""
        cwd = read_cwd(self.get_process_id())
        return cwd or os.getcwd()

    def set_badge(self, text: str, enabled: bool = True, icon: Optional[str] = None) -> bool:
        if not self.is_available() or not self.config.get("enable_badges", True):
            return False

        final_text = text
        if enabled and text:
            if icon:
                final_text = f"{icon} {text}".strip()
            elif self.config.get("enable_badge_icons", True):
                try:
                    from konsole_aware.tabs.icons import IconResolver
                    auto_icon = IconResolver().resolve(session_key=self.get_session_id(), cwd=self.get_cwd())
                    if auto_icon and not text.startswith(auto_icon):
                        final_text = f"{auto_icon} {text}".strip()
                except Exception:
                    pass

        self._call("setBadgeText", badge_text(final_text) if enabled else "")
        res = self._call("setBadgeEnabled", "true" if enabled else "false")
        return res is not None

    def get_foreground_process(self) -> Tuple[int, str]:
        """Get foreground PID and full command line of the session."""
        if not self.is_available():
            return (-1, "")
        pid = _to_int(self._call("foregroundProcessId"))
        return (pid, read_cmdline(pid))

    def find_window(self) -> Optional[str]:
        """Return the /Windows/N path whose tab list contains this session."""
        sess_num = self.get_session_num()
        if not self.is_available() or sess_num is None:
            return None
        env_win = os.environ.get("KONSOLE_DBUS_WINDOW")
        candidates = dbus.list_paths(self.service)
        windows = [p for p in candidates if p.startswith("/Windows/")]
        if env_win in windows:
            windows.remove(env_win)
            windows.insert(0, env_win)
        for win in windows:
            s_list = dbus.call(self.service, win, "org.kde.konsole.Window.sessionList")
            if s_list and str(sess_num) in s_list.split():
                return win
        return None

    def is_focused(self) -> bool:
        """Check if this session is the active tab of its window."""
        if not self.is_available():
            return False
        sess_num = self.get_session_num()
        win = self.find_window()
        if sess_num is None or not win:
            return False
        cur = dbus.call(self.service, win, "org.kde.konsole.Window.currentSession")
        return _to_int(cur) == sess_num

    @classmethod
    def list_all_sessions(cls, service: Optional[str] = None) -> List[Dict[str, Any]]:
        """List all Konsole windows, tabs, sessions, shells, foreground processes and titles."""
        if not dbus.qdbus_bin() or dbus.dbus_disabled():
            return []

        if service:
            services = [dbus.canonical_service(service)]
        else:
            services = dbus.konsole_services()

        results: List[Dict[str, Any]] = []
        for srv in services:
            paths = dbus.list_paths(srv)
            windows = [p for p in paths if p.startswith("/Windows/")]
            sessions = [p for p in paths if p.startswith("/Sessions/")]

            # session_num -> (window, is_current, tab_index, pane_index)
            win_map: Dict[str, Tuple[str, bool, int, int]] = {}
            for win in windows:
                s_list = (dbus.call(srv, win, "org.kde.konsole.Window.sessionList") or "").split()
                hierarchy = (dbus.call(srv, win, "org.kde.konsole.Window.viewHierarchy") or "").split()
                cur_sess = dbus.call(srv, win, "org.kde.konsole.Window.currentSession") or ""
                for t_idx, tab in enumerate(dbus.split_tabs(s_list, hierarchy)):
                    for p_idx, s_num in enumerate(tab):
                        win_map[s_num] = (win, s_num == cur_sess, t_idx, p_idx)

            for sess in sessions:
                s_num = sess.split("/")[-1]
                shell_pid = _to_int(dbus.call(srv, sess, "org.kde.konsole.Session.processId"))
                fg_pid = _to_int(dbus.call(srv, sess, "org.kde.konsole.Session.foregroundProcessId"))
                win_info = win_map.get(s_num, ("unknown", False, -1, 0))
                results.append({
                    "service": srv,
                    "session_path": sess,
                    "session_num": s_num,
                    "session_key": f"{srv}:{sess}",
                    "window": win_info[0],
                    "is_active_tab": win_info[1],
                    "tab_index": win_info[2],
                    "pane_index": win_info[3],
                    "title0": dbus.call(srv, sess, "org.kde.konsole.Session.title", "0") or "",
                    "title1": dbus.call(srv, sess, "org.kde.konsole.Session.title", "1") or "",
                    "tab_color": dbus.call(srv, sess, "org.kde.konsole.Session.tabColor") or "",
                    "shell_pid": shell_pid,
                    "foreground_pid": fg_pid,
                    "is_idle_shell": fg_pid <= 0 or fg_pid == shell_pid,
                    "command": read_cmdline(fg_pid) if fg_pid != shell_pid else "",
                    "cwd": read_cwd(shell_pid),
                })

        results.sort(key=lambda r: (r["service"], r["window"], r["tab_index"], r["pane_index"]))
        return results

    def switch_to(self) -> bool:
        """Switch Konsole's focus to this session."""
        win = self.find_window()
        s_num = self.get_session_num()
        if not win or s_num is None:
            return False
        return dbus.call(self.service, win, "org.kde.konsole.Window.setCurrentSession", str(s_num)) is not None
