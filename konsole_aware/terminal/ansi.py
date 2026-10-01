"""ANSI and OSC escape sequence controller for standard Linux terminals."""

import os
import re
import sys
from typing import Optional
from konsole_aware.terminal.base import BaseTerminal

_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def sanitize(text: str) -> str:
    """Collapse whitespace and drop control characters (ESC, BEL, C1) so text can't break out of an OSC."""
    return " ".join(_CONTROL_RE.sub(" ", text or "").split())


def _ancestor_tty() -> Optional[str]:
    """Find the pty of the nearest ancestor that has one.

    Agent hooks (Claude Code, agy) run detached from the controlling terminal, so
    /dev/tty is unavailable there; the agent process itself still holds the pty.
    """
    pid = os.getpid()
    for _ in range(16):
        for fd in (0, 1, 2):
            try:
                target = os.readlink(f"/proc/{pid}/fd/{fd}")
            except OSError:
                continue
            if target.startswith("/dev/pts/") or re.fullmatch(r"/dev/tty\d+", target):
                return target
        try:
            with open(f"/proc/{pid}/stat", "r", encoding="utf-8", errors="ignore") as f:
                ppid = int(f.read().rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            return None
        if ppid <= 1:
            return None
        pid = ppid
    return None


class AnsiTerminal(BaseTerminal):
    """Terminal controller using standard ANSI / OSC escape sequences."""

    def __init__(self, tty_path: Optional[str] = None):
        self.tty_path = tty_path

    def _write_osc(self, code: str) -> bool:
        """Write an escape sequence to the terminal.

        Never falls back to stdout unless stdout is itself a terminal: agent hooks'
        stdout is parsed as JSON (and for some events injected into the model context).
        """
        targets = [self.tty_path] if self.tty_path else ["/dev/tty", _ancestor_tty()]
        for target in targets:
            if not target:
                continue
            try:
                fd = os.open(target, os.O_WRONLY | os.O_NOCTTY | os.O_NONBLOCK)
            except OSError:
                continue
            try:
                os.write(fd, code.encode("utf-8", errors="ignore"))
                return True
            except OSError:
                continue
            finally:
                os.close(fd)
        try:
            if sys.stdout.isatty():
                sys.stdout.write(code)
                sys.stdout.flush()
                return True
        except (OSError, ValueError):
            pass
        return False

    def set_title(self, title: str, role: int = 0) -> bool:
        """Set tab and window title via OSC 0 (icon + window title)."""
        return self._write_osc(f"\033]0;{sanitize(title)}\007")

    def set_tab_color(self, hex_color: str) -> bool:
        """Tab color is not standardized in ANSI OSC; no-op for generic ANSI."""
        return False

    def reset_tab_color(self) -> bool:
        return False

    def set_badge(self, text: str, enabled: bool = True, icon: Optional[str] = None) -> bool:
        """No-op for generic ANSI."""
        return False

    def is_focused(self) -> bool:
        """Generic ANSI has no focus query; assume unfocused so alerts are not lost."""
        return False

    def get_session_id(self) -> str:
        """Return the controlling tty path as a stable session ID."""
        for fd in (0, 1, 2):
            try:
                return os.ttyname(fd)
            except OSError:
                continue
        tty = _ancestor_tty()
        if tty:
            return tty
        return f"ppid-{os.getppid()}"

    def send_terminal_notification(self, title: str, body: str) -> bool:
        """Send OSC 777 (kitty/foot/rxvt) and OSC 9 (iterm/conemu) desktop notification."""
        clean_title = sanitize(title).replace(";", ",")
        clean_body = sanitize(body)
        seq = f"\033]777;notify;{clean_title};{clean_body}\007\033]9;{clean_title}: {clean_body}\007"
        return self._write_osc(seq)
