"""
Base class for assistant tools.
"""

from abc import ABC, abstractmethod
from typing import Any


class BaseTool(ABC):
    """Base class for all assistant tools."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Tool name for function calling."""
        pass

    @property
    @abstractmethod
    def description(self) -> str:
        """Tool description for the assistant."""
        pass

    @property
    @abstractmethod
    def parameters(self) -> dict[str, Any]:
        """JSON schema for tool parameters."""
        pass

    @abstractmethod
    async def execute(self, **kwargs) -> str:
        """Execute the tool with given arguments.

        Args:
            **kwargs: Tool-specific arguments

        Returns:
            Result message as string
        """
        pass

    def get_definition(self) -> dict[str, Any]:
        """Get OpenAI function calling definition.

        Returns:
            Tool definition dictionary
        """
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }
