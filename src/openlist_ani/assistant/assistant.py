"""Core assistant logic for LLM interaction and tool calling.

Architecture:

- **Context Engine** (``context_engine/``): Handles all prompt assembly,
  session pruning, compaction, skill discovery, and memory-flush logic.
- **Tools** (``tools/``): Exposed as OpenAI function-calling tools via
  :class:`ToolRegistry`.
- **Domain skills** (``skills/``): Standalone scripts discovered by the
  context engine and executed via ``run_skill``.
"""

import json

from openai import AsyncOpenAI

from ..backend.client import BackendClient
from ..config import config
from ..logger import logger
from .context_engine import (
    ContextPromptBuilder,
    MemoryFlushGuard,
    SessionCompactor,
    SkillCatalog,
)
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


class AniAssistant:
    """Core assistant for interacting with LLM and executing tools."""

    MAX_TOOL_ITERATIONS = 100

    def __init__(self, backend_client: BackendClient):
        """Initialize assistant.

        Sets up the LLM client, tool registry, memory manager, and the
        context engine sub-systems (skill catalog, session compactor,
        prompt builder).

        Args:
            backend_client: BackendClient instance for backend API interaction.
        """
        self.backend_client = backend_client
        self.client: AsyncOpenAI | None = None
        self.model = config.llm.openai_model
        self.tool_registry = ToolRegistry()
        self.tools = self.tool_registry.get_definitions()
        self.client = self._create_openai_client()

        # Core memory file manager (CRUD for SOUL/MEMORY/USER/session).
        self.memory_manager = AssistantMemoryManager(
            client=self.client,
            model=self.model,
        )

        # Context engine sub-systems.
        self._flush_guard = MemoryFlushGuard()
        self._skill_catalog = SkillCatalog()
        self._compactor = SessionCompactor(
            client=self.client,
            model=self.model,
            flush_guard=self._flush_guard,
        )
        self._prompt_builder = ContextPromptBuilder(
            memory_manager=self.memory_manager,
            skill_catalog=self._skill_catalog,
            flush_guard=self._flush_guard,
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

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

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
            messages = await self._prompt_builder.build_messages(user_message)
            start_messages_len = len(messages)

            # Set the stream callback on SendMessageTool for this turn
            send_tool = self.tool_registry.get_tool("send_message")
            if isinstance(send_tool, SendMessageTool):
                send_tool.set_callback(stream_callback)

            try:
                response = await self._run_conversation_loop(messages)

                # Format intermediate tool calls that were appended during this turn
                new_messages = messages[start_messages_len:]
                tool_context = self._format_tool_context(new_messages)

                await self.memory_manager.append_turn(
                    user_message, response, tool_context=tool_context
                )

                # Check if compaction is needed after this turn
                await self._maybe_compact()

                return response
            finally:
                if isinstance(send_tool, SendMessageTool):
                    send_tool.set_callback(None)

        except Exception as e:
            logger.exception("Assistant: Error processing message")
            return f"❌ Error processing message: {str(e)}"

    async def clear_memory(self) -> None:
        """Clear all persisted memory."""
        await self.memory_manager.clear_all_memory()

    async def start_new_session(self) -> None:
        """Close the current session and start a new one."""
        await self.memory_manager.start_new_session()

    # ------------------------------------------------------------------
    # LLM Conversation Loop
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Tool Context Formatting
    # ------------------------------------------------------------------

    def _format_tool_context(self, messages: list) -> str:
        """Format intermediate tool calls into a structured string for disk.

        Preserves original formatting (newlines, indentation) so the
        session Markdown is human-readable.  Pruning happens at
        read-time via the context engine, not here.

        Returns an empty string if no meaningful tool calls were made.
        """
        blocks: list[str] = []
        for msg in messages:
            if isinstance(msg, dict):
                self._collect_tool_result(msg, blocks)
            else:
                self._collect_tool_calls(msg, blocks)

        return "\n".join(blocks) if blocks else ""

    @staticmethod
    def _collect_tool_result(msg: dict, blocks: list[str]) -> None:
        """Append a formatted tool result block if *msg* is a tool result."""
        if msg.get("role") != "tool":
            return
        content = str(msg.get("content", ""))
        indented = "\n".join("    " + line for line in content.splitlines())
        blocks.append(f"  Result:\n{indented or '    (empty result)'}")

    @staticmethod
    def _collect_tool_calls(msg: object, blocks: list[str]) -> None:
        """Append formatted tool call entries from a ChatCompletionMessage."""
        tool_calls = getattr(msg, "tool_calls", None)
        if not tool_calls:
            return
        for tc in tool_calls:
            name = getattr(tc.function, "name", "")
            if name == "send_message":
                continue
            args = getattr(tc.function, "arguments", "")
            blocks.append(f"  Called {name}({args})")

    # ------------------------------------------------------------------
    # Post-turn Compaction
    # ------------------------------------------------------------------

    async def _maybe_compact(self) -> None:
        """Run session compaction if the token budget is exceeded."""
        import asyncio

        session_path = await asyncio.to_thread(
            self.memory_manager._get_today_session_path
        )
        if session_path is None:
            return
        content = session_path.read_text(encoding="utf-8")
        if self._compactor.should_compress(content):
            await self._compactor.compress(session_path)

    # ------------------------------------------------------------------
    # Client Factory
    # ------------------------------------------------------------------

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
