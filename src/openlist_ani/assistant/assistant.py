"""Core assistant logic for LLM interaction and tool calling.

Architecture:

- **Tools** (read_file, search_files, run_command, send_message): exposed
  as OpenAI function-calling tools via :class:`ToolRegistry`.
- **Domain skills** (bangumi, mikan, oani): completely independent
  standalone scripts.  The LLM discovers them by searching for SKILL.md
  files and executes them via ``run_command``.
"""

import json

from openai import AsyncOpenAI

from ..backend.client import BackendClient
from ..config import config
from ..logger import logger
from .memory import AssistantMemoryManager
from .tools import (
    MessageCallback,
    SendMessageTool,
    ToolRegistry,
    UpdateMemoryTool,
    UpdateSoulTool,
    UpdateUserProfileTool,
)

# Re-export so integration layers (telegram, etc.) can import from here.
StreamCallback = MessageCallback

_BEHAVIORAL_RULES = (
    "## Behavioral Rules (MANDATORY)\n\n"
    "### 1. Message-First Principle\n\n"
    "Before EVERY tool call, you MUST call `send_message` first to tell "
    "the user what you are about to do. This is a hard rule with NO "
    "exceptions. The user must never wait in silence.\n\n"
    "Example flow:\n"
    '1. Call `send_message`: "Let me search for resources for this anime 🔍"\n'
    "2. Call `run_command` to execute the search skill\n"
    '3. Call `send_message`: "Found some results, let me organize them 📋"\n'
    "4. Return final results to the user\n\n"
    "### 2. Operational Safety\n\n"
    "- If no download link is available, search for resources first.\n"
    "- When given an RSS link, always parse it before downloading.\n"
    "- NEVER download resources already marked as downloaded.\n"
    "- Check download history via database query before downloading.\n"
    "- If tool arguments are uncertain or conflicting, ask the user "
    "instead of guessing.\n"
    "- When a tool returns confirmation or conflict info, relay it "
    "to the user verbatim — do NOT retry on your own.\n\n"
    "### 3. Thinking Approach\n\n"
    "- Break complex requests into atomic steps.\n"
    "- Report progress to the user after each step.\n"
    "- On error, explain the situation and suggest alternatives.\n\n"
    "### 4. Memory Management\n\n"
    "You have three tools for persisting information across conversations. "
    "Be **proactive** — save important info immediately, don't wait.\n\n"
    "- `update_user_profile`: Save personal user info (name, preferences, "
    "habits, Bangumi collection analysis) to USER.md. Call this whenever "
    "you discover anything about the user.\n"
    "- `update_memory`: Save valuable contextual facts (task outcomes, "
    "environment details, workflow patterns) to MEMORY.md. Call this for "
    "durable knowledge that isn't personal info.\n"
    "- `update_soul`: Save personality/behaviour changes to SOUL.md. "
    "Call this ONLY when the user explicitly asks you to change how you "
    "behave or communicate.\n"
)

_SKILL_DISCOVERY_HINT = (
    "## Skill Discovery\n\n"
    "You have domain-specific skills available as standalone scripts.\n"
    "To discover and use them:\n\n"
    "1. Use `search_files` with pattern "
    "`src/openlist_ani/assistant/skills/**/SKILL.md` "
    "to find available skills.\n"
    "2. Use `read_file` to read the SKILL.md and learn about "
    "available actions and their CLI arguments.\n"
    "3. Use `run_command` to execute the skill script, e.g.:\n"
    "   `uv run python -m "
    "openlist_ani.assistant.skills.<skill>.script.<action> "
    "[--arg value ...]`\n\n"
    "Always read SKILL.md first so you know the correct arguments.\n"
)


class AniAssistant:
    """Core assistant for interacting with LLM and executing tools."""

    MAX_TOOL_ITERATIONS = 100

    def __init__(self, backend_client: BackendClient):
        """Initialize assistant.

        Args:
            backend_client: BackendClient instance for backend API interaction
        """
        self.backend_client = backend_client
        self.client: AsyncOpenAI | None = None
        self.model = config.llm.openai_model
        self.tool_registry = ToolRegistry()
        self.tools = self.tool_registry.get_definitions()
        self.client = self._create_openai_client()
        self.memory_manager = AssistantMemoryManager(
            client=self.client,
            model=self.model,
        )

        # Wire memory manager into all memory-related tools.
        for tool_name, tool_type in (
            ("update_user_profile", UpdateUserProfileTool),
            ("update_memory", UpdateMemoryTool),
            ("update_soul", UpdateSoulTool),
        ):
            tool = self.tool_registry.get_tool(tool_name)
            if isinstance(tool, tool_type):
                tool.set_memory_manager(self.memory_manager)

    async def process_message(
        self,
        user_message: str,
        stream_callback: StreamCallback | None = None,
    ) -> str:
        """Process user message and return response.

        Args:
            user_message: User's message.
            stream_callback: Optional callback to push progress messages.

        Returns:
            Assistant's response message.
        """
        if not self.client:
            return (
                "❌ Assistant is not configured with OpenAI API key and cannot be used"
            )

        try:
            logger.info(f"Assistant: Processing message: {user_message}")
            messages = await self._build_messages(user_message)

            # Set the stream callback on SendMessageTool for this turn
            send_tool = self.tool_registry.get_tool("send_message")
            if isinstance(send_tool, SendMessageTool):
                send_tool.set_callback(stream_callback)

            try:
                response = await self._run_conversation_loop(messages)
                await self.memory_manager.append_turn(user_message, response)
                return response
            finally:
                if isinstance(send_tool, SendMessageTool):
                    send_tool.set_callback(None)

        except Exception as e:
            logger.exception("Assistant: Error processing message")
            return f"❌ Error processing message: {str(e)}"

    async def _run_conversation_loop(
        self,
        messages: list[dict],
    ) -> str:
        """Run the LLM interaction loop with tool calls until final answer."""
        for _ in range(self.MAX_TOOL_ITERATIONS):
            model_message = await self._request_model_response(messages)
            final_text = await self._handle_model_message(model_message, messages)
            if final_text is not None:
                return final_text

        logger.warning("Assistant: Max iterations reached, forcing final response")
        return await self._force_final_response(messages)

    async def _request_model_response(self, messages: list[dict]):
        """Request one model response with tools enabled."""
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            tools=self.tools,
            tool_choice="auto",
        )
        return response.choices[0].message

    async def _handle_model_message(
        self,
        message,
        messages: list[dict],
    ) -> str | None:
        """Handle one model message; return final text or None to continue."""
        if not message.tool_calls:
            logger.info("Assistant: No tool calls, returning response")
            return message.content or "Sorry, I cannot understand your request"

        messages.append(message)
        await self._execute_all_tool_calls(message.tool_calls, messages)
        return None

    async def _execute_all_tool_calls(
        self,
        tool_calls,
        messages: list[dict],
    ) -> None:
        """Execute all tool calls and append results to context."""
        for tool_call in tool_calls:
            tool_name = tool_call.function.name
            raw_arguments = tool_call.function.arguments

            tool_result = await self._execute_tool_call(tool_name, raw_arguments)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": tool_result,
                }
            )

    async def _execute_tool_call(
        self,
        tool_name: str,
        raw_arguments: str,
    ) -> str:
        """Execute one tool call and return tool output text."""
        try:
            arguments = json.loads(raw_arguments)
        except json.JSONDecodeError:
            error_msg = f"Failed to parse arguments for tool {tool_name}"
            logger.error(f"Assistant: {error_msg}: {raw_arguments}")
            return f"Error: {error_msg}. Please check your arguments format."

        logger.info(f"Assistant: Calling tool {tool_name} with {arguments}")
        try:
            result = await self.tool_registry.handle_tool_call(tool_name, arguments)
            if not result.startswith("❌"):
                logger.info(f"Assistant: Tool {tool_name} result: {result[:200]}...")
            return result
        except Exception as e:
            error_msg = f"Error executing tool {tool_name}"
            logger.exception(f"Assistant: {error_msg}")
            return f"Error: {error_msg}: {str(e)}"

    async def _force_final_response(self, messages: list[dict]) -> str:
        """Force final plain response without tools after iteration limit."""
        final_response = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            tools=None,
        )
        return (
            final_response.choices[0].message.content
            or "Operation completed but unable to generate response"
        )

    async def _build_messages(self, user_message: str) -> list[dict]:
        """Build the message list from memory + skill discovery hint."""
        messages = await self.memory_manager.build_system_messages(user_message)

        # Behavioral rules + skill discovery hint
        messages.append({"role": "system", "content": _BEHAVIORAL_RULES})
        messages.append({"role": "system", "content": _SKILL_DISCOVERY_HINT})

        messages.append({"role": "user", "content": user_message})
        return messages

    async def clear_memory(self) -> None:
        """Clear all persisted memory."""
        await self.memory_manager.clear_all_memory()

    async def start_new_session(self) -> None:
        """Close the current session and start a new one."""
        await self.memory_manager.start_new_session()

    def _create_openai_client(self) -> AsyncOpenAI | None:
        """Create OpenAI client from configuration."""
        if not config.llm.openai_api_key:
            logger.warning("OpenAI API key not set, assistant will not work")
            return None

        return AsyncOpenAI(
            api_key=config.llm.openai_api_key,
            base_url=config.llm.openai_base_url,
            timeout=60.0,
        )
