"""Terminal controller factory."""

import os
from typing import Optional
from konsole_aware.terminal.base import BaseTerminal
from konsole_aware.terminal.konsole import KonsoleTerminal
from konsole_aware.terminal.ansi import AnsiTerminal


def get_terminal(
    service: Optional[str] = None,
    session: Optional[str] = None,
) -> BaseTerminal:
    """Return KonsoleTerminal if running under Konsole, else AnsiTerminal."""
    # Check if Konsole DBus environment or process is active
    konsole = KonsoleTerminal(service=service, session_path=session)
    if konsole.is_available():
        return konsole
    return AnsiTerminal()
