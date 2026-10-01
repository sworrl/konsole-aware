"""Base interface for terminal controllers."""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class BaseTerminal(ABC):
    """Abstract base terminal controller."""

    @abstractmethod
    def set_title(self, title: str, role: int = 0) -> bool:
        """Set terminal / tab title."""
        pass

    @abstractmethod
    def set_tab_color(self, hex_color: str) -> bool:
        """Set tab color (if supported)."""
        pass

    @abstractmethod
    def reset_tab_color(self) -> bool:
        """Reset tab color to default."""
        pass

    @abstractmethod
    def set_badge(self, text: str, enabled: bool = True) -> bool:
        """Set badge text (if supported)."""
        pass

    @abstractmethod
    def is_focused(self) -> bool:
        """Check if this tab/terminal is the currently active/focused one."""
        pass

    @abstractmethod
    def get_session_id(self) -> str:
        """Get unique session identifier."""
        pass
