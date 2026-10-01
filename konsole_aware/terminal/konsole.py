"""KDE Konsole D-Bus terminal controller."""

import os
import shutil
import subprocess
import sys
from typing import Any, Dict, List, Optional, Tuple
from konsole_aware.terminal.base import BaseTerminal
from konsole_aware.terminal.ansi import AnsiTerminal


class KonsoleTerminal(BaseTerminal):
    """Deep integration controller for KDE Konsole via D-Bus."""

    def __init__(
        self,
        service: Optional[str] = None,
        session_path: Optional[str] = None,
    ):
        self.qdbus_bin = shutil.which("qdbus") or shutil.which("qdbus-qt6") or shutil.which("qdbus-qt5")
        self.ansi = AnsiTerminal()

        self.service = service or os.environ.get("KONSOLE_DBUS_SERVICE")
        self.session_path = session_path or os.environ.get("KONSOLE_DBUS_SESSION")

        if not self.service or not self.session_path:
            self._auto_discover()

    def _auto_discover(self) -> None:
        """Auto-discover Konsole D-Bus service and session by tracing process ancestry."""
        if not self.qdbus_bin:
            return

        # Find ancestors of current process
        ancestors = set()
        pid = os.getpid()
        while pid > 1:
            ancestors.add(pid)
            try:
                with open(f"/proc/{pid}/status", "r") as f:
                    for line in f:
                        if line.startswith("PPid:"):
                            pid = int(line.split()[1])
                            break
                    else:
                        break
            except Exception:
                break

        # If service was provided in env, use it
        services_to_check = []
        if self.service:
            services_to_check.append(self.service)

        # Also search active dbus names
        try:
            res = subprocess.run(
                [self.qdbus_bin],
                capture_output=True,
                text=True,
                timeout=1.0,
            )
            for line in res.stdout.splitlines():
                s = line.strip()
                if "konsole" in s.lower() or s.startswith(":"):
                    if s not in services_to_check:
                        services_to_check.append(s)
        except Exception:
            pass

        for srv in services_to_check:
            try:
                res = subprocess.run(
                    [self.qdbus_bin, srv],
                    capture_output=True,
                    text=True,
                    timeout=1.0,
                )
                sessions = [
                    l.strip()
                    for l in res.stdout.splitlines()
                    if l.strip().startswith("/Sessions/")
                ]
                for sess in sessions:
                    spid = self._dbus_call(srv, sess, "org.kde.konsole.Session.processId")
                    fg_pid = self._dbus_call(srv, sess, "org.kde.konsole.Session.foregroundProcessId")
                    try:
                        spid_int = int(spid.strip() if spid else 0)
                        fg_pid_int = int(fg_pid.strip() if fg_pid else 0)
                        if spid_int in ancestors or fg_pid_int in ancestors:
                            self.service = srv
                            self.session_path = sess
                            return
                    except Exception:
                        continue
            except Exception:
                continue

    def _dbus_call(self, service: str, path: str, method: str, *args) -> Optional[str]:
        if not self.qdbus_bin:
            return None
        cmd = [self.qdbus_bin, service, path, method] + list(args)
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=1.0)
            if res.returncode == 0:
                return res.stdout.strip()
            return None
        except Exception:
            return None

    def is_available(self) -> bool:
        return bool(self.service and self.session_path and self.qdbus_bin)

    def get_session_id(self) -> str:
        if self.session_path:
            return f"{self.service}:{self.session_path}"
        return self.ansi.get_session_id()

    def get_session_num(self) -> Optional[int]:
        if self.session_path and self.session_path.startswith("/Sessions/"):
            try:
                return int(self.session_path.split("/")[-1])
            except ValueError:
                pass
        return None

    def get_title(self, role: int = 0) -> str:
        if not self.is_available():
            return ""
        val = self._dbus_call(self.service, self.session_path, "org.kde.konsole.Session.title", str(role))
        return val or ""

    def get_tab_title_format(self, context: int = 0) -> str:
        if not self.is_available():
            return ""
        val = self._dbus_call(
            self.service,
            self.session_path,
            "org.kde.konsole.Session.tabTitleFormat",
            str(context),
        )
        return val or ""

    def set_tab_title_format(self, context: int, fmt: str) -> bool:
        if not self.is_available():
            return False
        res = self._dbus_call(
            self.service,
            self.session_path,
            "org.kde.konsole.Session.setTabTitleFormat",
            str(context),
            fmt,
        )
        return res is not None

    def set_title(self, title: str, role: int = 0) -> bool:
        """Set tab title via D-Bus and ANSI OSC."""
        success = False
        if self.is_available():
            # Role 0: NameRole (affects %n in tab format)
            res0 = self._dbus_call(
                self.service,
                self.session_path,
                "org.kde.konsole.Session.setTitle",
                "0",
                title,
            )
            # Role 1: SessionRole / Window Title (affects taskbar entry)
            res1 = self._dbus_call(
                self.service,
                self.session_path,
                "org.kde.konsole.Session.setTitle",
                "1",
                title,
            )
            success = res0 is not None or res1 is not None

        # Also emit OSC sequences for immediate terminal emulator response
        self.ansi.set_title(title, role)
        return success

    def get_tab_color(self) -> str:
        if not self.is_available():
            return ""
        val = self._dbus_call(self.service, self.session_path, "org.kde.konsole.Session.tabColor")
        return val or ""

    def set_tab_color(self, hex_color: str) -> bool:
        if not self.is_available():
            return False
        res = self._dbus_call(
            self.service,
            self.session_path,
            "org.kde.konsole.Session.setTabColor",
            hex_color,
        )
        return res is not None

    def reset_tab_color(self) -> bool:
        if not self.is_available():
            return False
        res = self._dbus_call(
            self.service,
            self.session_path,
            "org.kde.konsole.Session.setTabColor",
            "",
        )
        return res is not None

    def set_badge(self, text: str, enabled: bool = True) -> bool:
        if not self.is_available():
            return False
        self._dbus_call(
            self.service,
            self.session_path,
            "org.kde.konsole.Session.setBadgeText",
            text,
        )
        res = self._dbus_call(
            self.service,
            self.session_path,
            "org.kde.konsole.Session.setBadgeEnabled",
            "true" if enabled else "false",
        )
        return res is not None

    def get_foreground_process(self) -> Tuple[int, str]:
        """Get foreground PID and full command line of the session."""
        if not self.is_available():
            return (-1, "")
        pid_str = self._dbus_call(
            self.service,
            self.session_path,
            "org.kde.konsole.Session.foregroundProcessId",
        )
        try:
            pid = int(pid_str or -1)
        except ValueError:
            return (-1, "")

        cmd = ""
        if pid > 0:
            try:
                with open(f"/proc/{pid}/cmdline", "rb") as f:
                    cmd = f.read().replace(b"\x00", b" ").decode(errors="ignore").strip()
            except Exception:
                pass
        return (pid, cmd)

    def is_focused(self) -> bool:
        """Check if this session is the currently active/focused tab in its window."""
        if not self.is_available():
            return True  # Fallback: assume focused if unknown

        sess_num = self.get_session_num()
        if sess_num is None:
            return True

        # Find window containing this session
        try:
            res = subprocess.run(
                [self.qdbus_bin, self.service],
                capture_output=True,
                text=True,
                timeout=1.0,
            )
            windows = [
                l.strip()
                for l in res.stdout.splitlines()
                if l.strip().startswith("/Windows/")
            ]
            for win in windows:
                s_list_str = self._dbus_call(self.service, win, "org.kde.konsole.Window.sessionList")
                if s_list_str:
                    sessions = s_list_str.split()
                    if str(sess_num) in sessions:
                        cur_sess_str = self._dbus_call(
                            self.service, win, "org.kde.konsole.Window.currentSession"
                        )
                        if cur_sess_str:
                            try:
                                return int(cur_sess_str.strip()) == sess_num
                            except ValueError:
                                pass
        except Exception:
            pass

        return True

    @classmethod
    def list_all_sessions(cls, service: Optional[str] = None) -> List[Dict[str, Any]]:
        """List all Konsole windows, sessions, foreground processes, and statuses."""
        qdbus_bin = (
            shutil.which("qdbus")
            or shutil.which("qdbus-qt6")
            or shutil.which("qdbus-qt5")
        )
        if not qdbus_bin:
            return []

        results: List[Dict[str, Any]] = []
        services = []
        if service:
            services.append(service)
        elif os.environ.get("KONSOLE_DBUS_SERVICE"):
            services.append(os.environ["KONSOLE_DBUS_SERVICE"])
        else:
            # Find all konsole services on dbus
            try:
                res = subprocess.run([qdbus_bin], capture_output=True, text=True, timeout=1.0)
                for line in res.stdout.splitlines():
                    s = line.strip()
                    if "org.kde.konsole" in s.lower() and s not in services:
                        services.append(s)
            except Exception:
                pass

        for srv in services:
            try:
                res = subprocess.run([qdbus_bin, srv], capture_output=True, text=True, timeout=1.0)
                lines = res.stdout.splitlines()
                windows = [l.strip() for l in lines if l.strip().startswith("/Windows/")]
                sessions = [l.strip() for l in lines if l.strip().startswith("/Sessions/")]

                # Map sessions to their parent window and active status
                win_map: Dict[str, Tuple[str, bool]] = {}  # session_num -> (win_path, is_current)
                for win in windows:
                    try:
                        s_list = subprocess.run(
                            [qdbus_bin, srv, win, "org.kde.konsole.Window.sessionList"],
                            capture_output=True,
                            text=True,
                            timeout=1.0,
                        ).stdout.strip().split()
                        cur_sess = subprocess.run(
                            [qdbus_bin, srv, win, "org.kde.konsole.Window.currentSession"],
                            capture_output=True,
                            text=True,
                            timeout=1.0,
                        ).stdout.strip()
                        for s_num in s_list:
                            win_map[s_num] = (win, s_num == cur_sess)
                    except Exception:
                        pass

                for sess in sessions:
                    s_num = sess.split("/")[-1]
                    t0 = subprocess.run(
                        [qdbus_bin, srv, sess, "org.kde.konsole.Session.title", "0"],
                        capture_output=True,
                        text=True,
                        timeout=1.0,
                    ).stdout.strip()
                    t1 = subprocess.run(
                        [qdbus_bin, srv, sess, "org.kde.konsole.Session.title", "1"],
                        capture_output=True,
                        text=True,
                        timeout=1.0,
                    ).stdout.strip()
                    color = subprocess.run(
                        [qdbus_bin, srv, sess, "org.kde.konsole.Session.tabColor"],
                        capture_output=True,
                        text=True,
                        timeout=1.0,
                    ).stdout.strip()
                    fg_pid_str = subprocess.run(
                        [qdbus_bin, srv, sess, "org.kde.konsole.Session.foregroundProcessId"],
                        capture_output=True,
                        text=True,
                        timeout=1.0,
                    ).stdout.strip()
                    fg_pid = int(fg_pid_str) if fg_pid_str.isdigit() else -1

                    cmd = ""
                    if fg_pid > 0:
                        try:
                            with open(f"/proc/{fg_pid}/cmdline", "rb") as f:
                                cmd = f.read().replace(b"\x00", b" ").decode(errors="ignore").strip()
                        except Exception:
                            pass

                    win_info = win_map.get(s_num, ("unknown", False))

                    results.append({
                        "service": srv,
                        "session_path": sess,
                        "session_num": s_num,
                        "window": win_info[0],
                        "is_active_tab": win_info[1],
                        "title0": t0,
                        "title1": t1,
                        "tab_color": color,
                        "foreground_pid": fg_pid,
                        "command": cmd,
                    })
            except Exception:
                continue

        return results

    def switch_to(self) -> bool:
        """Switch Konsole's focus to this session."""
        if not self.is_available():
            return False
        s_num = self.get_session_num()
        if s_num is None:
            return False

        try:
            res = subprocess.run(
                [self.qdbus_bin, self.service],
                capture_output=True,
                text=True,
                timeout=1.0,
            )
            windows = [
                l.strip()
                for l in res.stdout.splitlines()
                if l.strip().startswith("/Windows/")
            ]
            for win in windows:
                s_list_str = self._dbus_call(self.service, win, "org.kde.konsole.Window.sessionList")
                if s_list_str and str(s_num) in s_list_str.split():
                    self._dbus_call(
                        self.service,
                        win,
                        "org.kde.konsole.Window.setCurrentSession",
                        str(s_num),
                    )
                    return True
        except Exception:
            pass
        return False
