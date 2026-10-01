"""ANSI and OSC escape sequence controller for standard Linux terminals."""

import os
import sys
from typing import Optional
from konsole_aware.terminal.base import BaseTerminal


class AnsiTerminal(BaseTerminal):
    """Terminal controller using standard ANSI / OSC escape sequences."""

    def __init__(self, tty_path: Optional[str] = None):
        self.tty_path = tty_path or "/dev/tty"
        self._tty_fd = None

    def _write_osc(self, code: str) -> bool:
        """Write escape sequence to controlling terminal or stdout."""
        try:
            # Prefer direct /dev/tty write so subshell pipes don't swallow escape sequences
            with open(self.tty_path, "w", encoding="utf-8", errors="ignore") as f:
                f.write(code)
                f.flush()
                return True
        except Exception:
            try:
                sys.stdout.write(code)
                sys.stdout.flush()
                return True
            except Exception:
                return False

    def set_title(self, title: str, role: int = 0) -> bool:
        """Set tab and window title via standard OSC 0 and OSC 2 sequences."""
        # Clean title of newlines or control chars
        clean_title = " ".join(title.split())
        # OSC 0 sets both window and icon/tab title
        # OSC 30 sets Konsole tab title explicitly
        seq = f"\033]0;{clean_title}\007\033]30;{clean_title}\007"
        return self._write_osc(seq)

    def set_tab_color(self, hex_color: str) -> bool:
        """Tab color is not standardized in ANSI OSC; no-op for generic ANSI."""
        return False

    def reset_tab_color(self) -> bool:
        return False

    def set_badge(self, text: str, enabled: bool = True, icon: Optional[str] = None) -> bool:
        """No-op for generic ANSI."""
        return False

    def is_focused(self) -> bool:
        """Generic ANSI has no standard focus query without DEC private modes."""
        return False

    def get_session_id(self) -> str:
        """Return TTY or PID as session ID."""
        try:
            return os.ttyname(sys.stdin.fileno())
        except Exception:
            return f"tty-{os.getpid()}"

    def send_terminal_notification(self, title: str, body: str) -> bool:
        """Send OSC 777 (kitty/foot/rxvt) and OSC 9 (iterm/conemu) desktop notification."""
        clean_title = " ".join(title.split())
        clean_body = " ".join(body.split())
        # OSC 777 notify
        seq = f"\033]777;notify;{clean_title};{clean_body}\007\033]9;{clean_title}: {clean_body}\007"
        return self._write_osc(seq)
