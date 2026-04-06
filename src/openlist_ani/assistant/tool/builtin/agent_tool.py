"""
AgentTool — spawns sub-agents from within the main agent loop.

The model invokes this tool to delegate complex tasks to a child
agent with its own loop.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from openlist_ani.assistant.core.subagent import (
    BUILTIN_AGENT_CONFIGS,
    SubAgentConfig,
    run_subagent,
)
from openlist_ani.assistant.tool.base import BaseTool

if TYPE_CHECKING:
    from openlist_ani.assistant.memory.manager import MemoryManager
    from openlist_ani.assistant.provider.base import Provider
    from openlist_ani.assistant.tool.registry import ToolRegistry


class AgentTool(BaseTool):
    """Tool that spawns a sub-agent to handle complex tasks.

    The model calls this tool with a prompt and optional agent_type.
    A child agent loop runs independently and returns its final result.
    """

    def __init__(
        self,
        provider: Provider,
        registry: ToolRegistry,
        memory: MemoryManager | None = None,
    ) -> None:
        self._provider = provider
        self._registry = registry
        self._memory = memory

    @property
    def name(self) -> str:
        return "agent"

    @property
    def description(self) -> str:
        return (
            "Launch a sub-agent to handle complex, multi-step tasks autonomously. "
            "The sub-agent has its own tool loop and returns a final result. "
            "Use agent_type='explore' for read-only search/analysis tasks, "
            "or 'general-purpose' for tasks that may modify files."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "The task for the sub-agent to perform.",
                },
                "agent_type": {
                    "type": "string",
                    "description": (
                        "Type of agent: 'general-purpose' (default) or 'explore' (read-only)."
                    ),
                    "default": "general-purpose",
                },
            },
            "required": ["prompt"],
        }

    def is_concurrency_safe(self, tool_input: dict | None = None) -> bool:
        return False  # Sub-agents may perform write operations

    async def execute(self, **kwargs: object) -> str:
        prompt = str(kwargs.get("prompt", ""))
        if not prompt:
            return "Error: prompt is required."

        agent_type = str(kwargs.get("agent_type", "general-purpose"))
        config = self._resolve_agent_config(agent_type)

        try:
            result = await run_subagent(
                config=config,
                prompt=prompt,
                provider=self._provider,
                registry=self._registry,
                memory=self._memory,
            )
            return result
        except Exception as e:
            return f"SubAgent error: {e}"

    def _resolve_agent_config(self, agent_type: str) -> SubAgentConfig:
        """Resolve agent type to configuration.

        Falls back to general-purpose for unknown types.
        """
        config = BUILTIN_AGENT_CONFIGS.get(agent_type)
        if config is not None:
            return config

        # Fall back to general-purpose
        return BUILTIN_AGENT_CONFIGS["general-purpose"]
