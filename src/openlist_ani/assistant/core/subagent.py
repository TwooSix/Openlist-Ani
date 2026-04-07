"""
SubAgent runner — spawns child agent loops with isolated contexts.

Creates an independent agentic while-loop for the child with its
own message list and tool set.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from openlist_ani.assistant.core.models import Message, Role
from openlist_ani.assistant.tool.orchestrator import ToolOrchestrator
from openlist_ani.assistant.tool.registry import ToolRegistry

if TYPE_CHECKING:
    from openlist_ani.assistant.memory.manager import MemoryManager
    from openlist_ani.assistant.provider.base import Provider

from loguru import logger


@dataclass
class SubAgentConfig:
    """Configuration for a sub-agent."""

    agent_type: str  # Type identifier (e.g., "general-purpose", "explore")
    system_prompt: str  # Independent system prompt for the sub-agent
    allowed_tool_names: list[str] | None = None  # Tool subset (None = all)
    max_rounds: int = 15  # Max tool-call rounds
    is_concurrency_safe: bool = False  # Whether the agent can run concurrently


# Built-in agent type configurations
BUILTIN_AGENT_CONFIGS: dict[str, SubAgentConfig] = {
    "general-purpose": SubAgentConfig(
        agent_type="general-purpose",
        system_prompt=(
            "You are a general-purpose agent. Given the user's task, "
            "use the tools available to complete it fully. "
            "When done, respond with a concise report."
        ),
        allowed_tool_names=None,  # All tools available
    ),
    "explore": SubAgentConfig(
        agent_type="explore",
        system_prompt=(
            "You are a read-only exploration agent specialized in "
            "searching and analyzing codebases. You must NOT create, "
            "modify, or delete any files. Only use read-only tools. "
            "Report your findings clearly and concisely."
        ),
        is_concurrency_safe=True,
        # allowed_tool_names will be set dynamically to read-only tools
    ),
}


def _build_filtered_registry(
    config: SubAgentConfig,
    registry: ToolRegistry,
) -> ToolRegistry:
    """Build a (possibly filtered) tool registry for the sub-agent."""
    if config.allowed_tool_names is None:
        return registry

    sub_registry = ToolRegistry()
    for tool in registry.all_tools():
        if tool.name in config.allowed_tool_names:
            sub_registry.register(tool)
    return sub_registry


def _build_subagent_messages(
    config: SubAgentConfig,
    prompt: str,
    parent_context: list[Message] | None,
) -> list[Message]:
    """Build the initial message list for a sub-agent."""
    messages: list[Message] = [
        Message(role=Role.SYSTEM, content=config.system_prompt),
    ]

    if parent_context:
        # Inherit parent context (excluding system messages to avoid duplication)
        messages.extend(msg for msg in parent_context if msg.role != Role.SYSTEM)

    messages.append(Message(role=Role.USER, content=prompt))
    return messages


async def run_subagent(
    config: SubAgentConfig,
    prompt: str,
    provider: Provider,
    registry: ToolRegistry,
    memory: MemoryManager | None = None,
    parent_context: list[Message] | None = None,
) -> str:
    """Run a sub-agent loop and return the final text output.

    1. Build independent message list (optionally inheriting parent context)
    2. If allowed_tool_names specified, filter registry to subset
    3. Run independent agentic while-loop (with ToolOrchestrator)
    4. Collect final text response and return

    Args:
        config: Sub-agent configuration.
        prompt: The task prompt for the sub-agent.
        provider: LLM provider to use.
        registry: Parent tool registry.
        memory: Optional memory manager (not used by sub-agent directly).
        parent_context: Optional parent conversation context to inherit.

    Returns:
        Final text output from the sub-agent.
    """
    sub_registry = _build_filtered_registry(config, registry)
    messages = _build_subagent_messages(config, prompt, parent_context)

    # Sub-agent's own orchestrator
    orchestrator = ToolOrchestrator(sub_registry)
    tool_defs = provider.format_tool_definitions(sub_registry.all_tools())

    # Independent agentic loop
    final_text = ""
    for round_num in range(config.max_rounds):
        logger.debug(
            f"SubAgent[{config.agent_type}] round {round_num + 1}/{config.max_rounds}"
        )

        response = await provider.chat_completion(messages, tool_defs if tool_defs else None)

        if not response.tool_calls:
            final_text = response.text
            break

        # Append assistant message with tool calls
        messages.append(
            Message(role=Role.ASSISTANT, tool_calls=response.tool_calls, content=response.text)
        )

        # Execute tool calls via orchestrator (parallel/serial)
        results = await orchestrator.execute_tool_calls(response.tool_calls)
        messages.append(Message(role=Role.TOOL, tool_results=results))
    else:
        final_text = (
            f"SubAgent[{config.agent_type}] reached maximum tool call rounds "
            f"({config.max_rounds})."
        )

    return final_text
