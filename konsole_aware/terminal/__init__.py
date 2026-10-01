"""Terminal controller factory."""

import os
from typing import Optional
from konsole_aware.terminal.base import BaseTerminal
from konsole_aware.terminal.konsole import KonsoleTerminal
from konsole_aware.terminal.ansi import AnsiTerminal


class NullTerminal(AnsiTerminal):
    """Terminal that records nothing and writes nothing (KONSOLE_AWARE_TERMINAL=null, used by tests)."""

    def __init__(self):
        super().__init__()
        self.calls = []

    def _write_osc(self, code: str) -> bool:
        self.calls.append(code)
        return True

    def get_session_id(self) -> str:
        return os.environ.get("KONSOLE_AWARE_SESSION_KEY", "null:/Sessions/0")


def get_terminal(
    service: Optional[str] = None,
    session: Optional[str] = None,
) -> BaseTerminal:
    """Return KonsoleTerminal if running under Konsole, else AnsiTerminal."""
    if os.environ.get("KONSOLE_AWARE_TERMINAL") == "null":
        return NullTerminal()
    konsole = KonsoleTerminal(service=service, session_path=session)
    if konsole.is_available():
        return konsole
    return AnsiTerminal()
