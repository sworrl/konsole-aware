"""Base agent lifecycle handler."""

from abc import ABC, abstractmethod
from typing import Any, Dict


class BaseAgentHandler(ABC):
    """Abstract base handler for AI agent lifecycle hooks."""

    @abstractmethod
    def handle_event(self, event_name: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Handle lifecycle hook event and return response payload."""
        pass
