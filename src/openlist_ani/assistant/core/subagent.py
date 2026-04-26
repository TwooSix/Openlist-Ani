"""
SubAgent runner — spawns child agent loops with isolated contexts.

Creates an independent agentic while-loop for the child with its
own message list and tool set.

Error recovery mirrors the parent AgenticLoop:
- Per-call timeout (prevents indefinite hangs on provider calls)
- Overall timeout (caps total sub-agent execution time)
- Transient error retry with exponential backoff
- Cancellation support (asyncio.CancelledError propagation)

Tool filtering follows Claude Code's architecture:
- Sub-agents cannot call the ``agent`` tool (prevents recursion)
- Sub-agents cannot call ``send_message`` (no frontend callback)
- ``explore`` agents are restricted to read-only tools
"""

from __future__ import annotations

import asyncio
from asyncio import sleep as _async_sleep
from dataclasses import dataclass
from typing import TYPE_CHECKING

from openlist_ani.assistant._constants import (
    API_RETRY_BACKOFF_BASE,
    MAX_API_RETRIES,
)
from openlist_ani.assistant.core.models import Message, ProviderResponse, Role, ToolResult
from openlist_ani.assistant.tool.orchestrator import ToolOrchestrator
from openlist_ani.assistant.tool.registry import ToolRegistry

if TYPE_CHECKING:
    from openlist_ani.assistant.provider.base import Provider

from loguru import logger

# ── Constants ────────────────────────────────────────────────────

# Tools that must never be available to sub-agents.
# Mirrors Claude Code's ALL_AGENT_DISALLOWED_TOOLS pattern.
_SUBAGENT_DISALLOWED_TOOLS: frozenset[str] = frozenset({
    "agent",        # Prevent recursive sub-agent spawning
    "send_message", # No frontend callback in sub-agent context
})


@dataclass
class SubAgentConfig:
    """Configuration for a sub-agent."""

    agent_type: str  # Type identifier (e.g., "general-purpose", "explore")
    system_prompt: str  # Independent system prompt for the sub-agent
    allowed_tool_names: list[str] | None = None  # Tool subset (None = all)
    max_rounds: int = 15  # Max tool-call rounds
    is_concurrency_safe: bool = False  # Whether the agent can run concurrently
    timeout_seconds: float = 120.0  # Overall timeout for the entire sub-agent run
    per_call_timeout: float = 60.0  # Per-API-call timeout


# Built-in agent type configurations
BUILTIN_AGENT_CONFIGS: dict[str, SubAgentConfig] = {
    "general-purpose": SubAgentConfig(
        agent_type="general-purpose",
        system_prompt=(
            "You are a general-purpose agent. Given the user's task, "
            "use the tools available to complete it fully. "
            "When done, respond with a concise report."
        ),
        allowed_tool_names=None,  # All tools (minus disallowed)
    ),
    "explore": SubAgentConfig(
        agent_type="explore",
        system_prompt=(
            "You are a read-only exploration agent specialized in "
            "searching and analyzing information. You must NOT create, "
            "modify, or delete any files. Only use read-only tools. "
            "Report your findings clearly and concisely."
        ),
        allowed_tool_names=["skill_tool"],  # Read-only: search, query, etc.
        is_concurrency_safe=True,
        timeout_seconds=120.0,
        per_call_timeout=60.0,
    ),
}


def _build_filtered_registry(
    config: SubAgentConfig,
    registry: ToolRegistry,
) -> ToolRegistry:
    """Build a filtered tool registry for the sub-agent.

    Always excludes tools in ``_SUBAGENT_DISALLOWED_TOOLS`` (e.g. agent,
    send_message) to prevent recursion and broken callbacks.

    If ``config.allowed_tool_names`` is set, further restricts to only
    those tools.
    """
    sub_registry = ToolRegistry()
    for tool in registry.all_tools():
        # Always exclude disallowed tools from sub-agents
        if tool.name in _SUBAGENT_DISALLOWED_TOOLS:
            logger.debug(
                f"SubAgent[{config.agent_type}] excluded disallowed tool: {tool.name}"
            )
            continue

        # If allowed_tool_names is set, only include listed tools
        if config.allowed_tool_names is not None and tool.name not in config.allowed_tool_names:
            continue

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


def _is_transient_error(error: Exception) -> bool:
    """Check if the error is transient and worth retrying.

    Thin wrapper that imports the canonical check from loop.py.
    """
    from openlist_ani.assistant.core.loop import _is_transient

    return _is_transient(error)


async def _retry_with_backoff(
    agent_type: str,
    round_num: int,
    attempt: int,
    reason: str,
) -> None:
    """Log a warning and sleep with exponential backoff before retrying."""
    delay = API_RETRY_BACKOFF_BASE * (2 ** attempt)
    logger.warning(
        f"SubAgent[{agent_type}] round {round_num + 1}: "
        f"{reason} (attempt {attempt + 1}/{MAX_API_RETRIES}), "
        f"retrying in {delay:.1f}s"
    )
    await _async_sleep(delay)


async def _call_provider_with_recovery(
    provider: Provider,
    messages: list[Message],
    tool_defs: list[dict] | None,
    config: SubAgentConfig,
    round_num: int,
) -> ProviderResponse | str:
    """Call the provider with per-call timeout and retry logic.

    Returns the ProviderResponse on success, or an error message
    string if all retries are exhausted.
    """
    last_error: Exception | None = None

    for attempt in range(MAX_API_RETRIES):
        try:
            response = await asyncio.wait_for(
                provider.chat_completion(messages, tool_defs or None),
                timeout=config.per_call_timeout,
            )
            return response  # type: ignore[return-value]
        except asyncio.TimeoutError:
            last_error = TimeoutError(
                f"Provider call timed out after {config.per_call_timeout}s"
            )
            if attempt < MAX_API_RETRIES - 1:
                await _retry_with_backoff(
                    config.agent_type, round_num, attempt, "provider call timed out",
                )
                continue
            break
        except asyncio.CancelledError:
            raise
        except Exception as e:
            last_error = e
            if _is_transient_error(e) and attempt < MAX_API_RETRIES - 1:
                await _retry_with_backoff(
                    config.agent_type, round_num, attempt, f"transient error: {e}",
                )
                continue
            break

    return f"SubAgent[{config.agent_type}] provider error after {MAX_API_RETRIES} attempts: {last_error}"


async def _run_subagent_loop(
    config: SubAgentConfig,
    prompt: str,
    provider: Provider,
    registry: ToolRegistry,
    parent_context: list[Message] | None,
) -> str:
    """Inner loop for sub-agent execution.

    Separated from ``run_subagent`` so the overall timeout wraps
    the entire loop (including all tool executions).
    """
    sub_registry = _build_filtered_registry(config, registry)
    messages = _build_subagent_messages(config, prompt, parent_context)

    # Sub-agent's own orchestrator
    orchestrator = ToolOrchestrator(sub_registry)
    tool_defs = provider.format_tool_definitions(sub_registry.all_tools())

    logger.info(
        f"SubAgent[{config.agent_type}] starting "
        f"(max_rounds={config.max_rounds}, timeout={config.timeout_seconds}s, "
        f"tools={[t.name for t in sub_registry.all_tools()]})"
    )

    # Independent agentic loop
    final_text = ""
    for round_num in range(config.max_rounds):
        logger.info(
            f"SubAgent[{config.agent_type}] round {round_num + 1}/{config.max_rounds}: "
            f"calling provider..."
        )

        # Call provider with per-call timeout and retry
        result = await _call_provider_with_recovery(
            provider, messages, tool_defs, config, round_num,
        )

        # If result is a string, it's an error message
        if isinstance(result, str):
            return result

        response = result

        if not response.tool_calls:
            final_text = response.text
            logger.info(
                f"SubAgent[{config.agent_type}] completed in "
                f"{round_num + 1} round(s) (text response, no tool calls)"
            )
            break

        # Append assistant message with tool calls
        messages.append(
            Message(
                role=Role.ASSISTANT,
                tool_calls=response.tool_calls,
                content=response.text,
                reasoning_content=response.reasoning_content,
                thinking_blocks=response.thinking_blocks,
            )
        )

        # Execute tool calls via orchestrator (parallel/serial)
        results: list[ToolResult] = []
        for tc in response.tool_calls:
            logger.info(
                f"SubAgent[{config.agent_type}] round {round_num + 1}: "
                f"executing tool '{tc.name}'"
            )
        async for tool_result in orchestrator.execute_tool_calls(response.tool_calls):
            results.append(tool_result)
        messages.append(Message(role=Role.TOOL, tool_results=results))
    else:
        final_text = (
            f"SubAgent[{config.agent_type}] reached maximum tool call rounds "
            f"({config.max_rounds})."
        )
        logger.warning(final_text)

    return final_text


async def run_subagent(
    config: SubAgentConfig,
    prompt: str,
    provider: Provider,
    registry: ToolRegistry,
    parent_context: list[Message] | None = None,
) -> str:
    """Run a sub-agent loop and return the final text output.

    1. Build independent message list (optionally inheriting parent context)
    2. Filter registry: always exclude disallowed tools, then apply config
    3. Run independent agentic while-loop with timeout + retry
    4. Collect final text response and return

    Error recovery:
    - Overall timeout (``config.timeout_seconds``): prevents indefinite hangs
    - Per-call timeout (``config.per_call_timeout``): catches hung API calls
    - Transient error retry with exponential backoff
    - Cancellation: ``asyncio.CancelledError`` propagates to parent loop

    Args:
        config: Sub-agent configuration.
        prompt: The task prompt for the sub-agent.
        provider: LLM provider to use.
        registry: Parent tool registry.
        parent_context: Optional parent conversation context to inherit.

    Returns:
        Final text output from the sub-agent.
    """
    try:
        return await asyncio.wait_for(
            _run_subagent_loop(
                config=config,
                prompt=prompt,
                provider=provider,
                registry=registry,
                parent_context=parent_context,
            ),
            timeout=config.timeout_seconds,
        )
    except asyncio.TimeoutError:
        msg = (
            f"SubAgent[{config.agent_type}] timed out after "
            f"{config.timeout_seconds}s. The task may be too complex for "
            f"a sub-agent — consider breaking it into smaller steps."
        )
        logger.warning(msg)
        return msg
    except asyncio.CancelledError:
        logger.info(f"SubAgent[{config.agent_type}] cancelled by user")
        raise  # Propagate to parent loop's cancellation handling
