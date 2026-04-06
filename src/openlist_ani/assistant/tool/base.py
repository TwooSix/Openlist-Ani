"""
Abstract base class for tools.

Defines the tool interface:
- is_concurrency_safe: per-input concurrency-safe parallel dispatch
- prompt(): per-tool system prompt contribution
- is_enabled(): dynamic enable/disable
- should_defer: lazy-loaded tool deferral
- user_facing_name(): display name for UI
- is_read_only(): whether the tool only reads (no side effects)
- is_destructive(): whether the tool performs irreversible operations
- max_result_size_chars: threshold for result size before persistence
- aliases: alternative names for backward compatibility
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


class BaseTool(ABC):
    """Abstract tool that models can invoke via tool_use.

    Subclasses must implement name, description, parameters, and execute.
    All other methods have safe defaults.

    Key methods to override:
    - prompt(): contribute tool-specific instructions to the system prompt
    - is_concurrency_safe(): per-input concurrency decision
    - is_enabled(): dynamic enable/disable
    - is_read_only(): mark as read-only (no side effects)
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique tool name used in tool_use calls."""
        ...

    @property
    def aliases(self) -> list[str]:
        """Alternative names for backward compatibility.

        The tool can be looked up by any of these names in addition
        to its primary name.
        """
        return []

    @property
    @abstractmethod
    def description(self) -> str:
        """Human-readable description for the model."""
        ...

    @property
    @abstractmethod
    def parameters(self) -> dict:
        """JSON Schema format parameter definitions."""
        ...

    @property
    def search_hint(self) -> str:
        """One-line capability phrase for keyword matching.

        Helps the model find this tool via keyword search when it's
        deferred. 3-10 words, no trailing period.
        """
        return ""

    @property
    def should_defer(self) -> bool:
        """Whether this tool should be deferred (lazy-loaded).

        When True, the tool is sent with defer_loading: true and requires
        ToolSearch to be used before it can be called.
        """
        return False

    @property
    def max_result_size_chars(self) -> int:
        """Maximum size in characters for tool result before truncation.

        When exceeded, the result is truncated with a notice. Set to a
        very large number for tools whose output must never be truncated.
        """
        return 50_000

    def is_concurrency_safe(self, tool_input: dict | None = None) -> bool:
        """Whether this tool call is safe to run concurrently.

        The decision is **per-input**, not per-tool. The same tool may be
        concurrent for one input and serial for another (e.g., a bash tool
        could be safe for ``ls`` but not for ``rm``).

        Args:
            tool_input: The parsed arguments for this tool call.
                        ``None`` when the caller cannot provide input.

        Returns:
            True if this specific invocation can be run in parallel
            with other concurrency-safe calls.
            Defaults to False (conservative — assume writes).
        """
        return False

    def is_enabled(self) -> bool:
        """Whether this tool is currently enabled.

        Defaults to True. Override to dynamically enable/disable
        tools based on context.
        """
        return True

    def is_read_only(self, tool_input: dict | None = None) -> bool:
        """Whether this tool is read-only (no side effects).

        Defaults to False.
        """
        return False

    def is_destructive(self, tool_input: dict | None = None) -> bool:
        """Whether this tool performs irreversible operations.

        Defaults to False. Only set True for tools that delete,
        overwrite, or send data.
        """
        return False

    def user_facing_name(self, tool_input: dict | None = None) -> str:
        """Display name for UI/logging.

        Defaults to the tool's name.
        """
        return self.name

    def get_activity_description(self, tool_input: dict | None = None) -> str | None:
        """Human-readable present-tense activity description for spinner display.

        Examples: "Reading src/foo.ts", "Running bun test", "Searching for pattern"
        Returns None to fall back to tool name.
        """
        return None

    async def prompt(
        self,
        tools: list[BaseTool] | None = None,
    ) -> str:
        """Generate tool-specific system prompt contribution.

        Each tool contributes its own detailed instructions to the system
        prompt. This is more comprehensive than the short `description`
        used in the tool schema.

        The prompt is injected into the system message during context assembly.
        Override this to provide detailed usage instructions, examples,
        formatting guidelines, and behavioral rules for your tool.

        Args:
            tools: All registered tools (for cross-referencing).

        Returns:
            Prompt text to include in the system message.
            Empty string means no prompt contribution (default).
        """
        return ""

    @abstractmethod
    async def execute(self, **kwargs: object) -> str:
        """Execute the tool with the given arguments.

        Args:
            **kwargs: Tool-specific arguments.

        Returns:
            String result to feed back to the model.
        """
        ...
