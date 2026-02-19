"""
Core assistant logic for LLM interaction and tool calling.
"""

import json
from enum import Enum
from typing import Any, Awaitable, Callable, List, Optional

from openai import AsyncOpenAI

from ..config import config
from ..core.download import DownloadManager
from ..logger import logger
from .tools import get_assistant_tools, handle_tool_call


class AssistantStatus(str, Enum):
    """Assistant execution status emitted to outer integrations."""

    THINKING = "thinking"
    FINALIZING = "finalizing"
    TOOL_EXECUTING = "tool_executing"


StatusCallback = Callable[[AssistantStatus, dict[str, Any]], Awaitable[None]]


class AniAssistant:
    """Core assistant for interacting with LLM and executing tools."""

    MAX_TOOL_ITERATIONS = 20
    HISTORY_FOCUS_PROMPT = "--- New user request below. Focus on addressing THIS request specifically. Previous conversation is provided only for context. ---"
    DEFAULT_SYSTEM_PROMPT = """You are a professional anime resource download assistant with access to several tools that you can combine to solve user requests.

## Available Tools:

1. **search_anime_resources**: Search for anime on websites (mikan/dmhy/acgrip)
2. **parse_rss**: Parse RSS feed to extract resource information and download links
3. **download_resource**: Download a single anime resource using its download URL
4. **execute_sql_query**: Query the download history database using SQL

## Database Schema:

Table: `resources`
- id: INTEGER PRIMARY KEY
- url: TEXT (download URL)
- title: TEXT (resource title)
- anime_name: TEXT (anime name)
- season: INTEGER (season number)
- episode: INTEGER (episode number)
- fansub: TEXT (fansub/subtitle group)
- quality: TEXT (quality like 1080p, 720p)
- languages: TEXT (language codes)
- version: INTEGER (version number)
- downloaded_at: TIMESTAMP (download timestamp)

## How to Compose Tools:

**Scenario 1: User provides RSS link**
1. Use `parse_rss` to extract resources from the RSS feed
2. For each resource you want to download, use `download_resource` with its URL

**Scenario 2: User wants to download but no URL**
1. Use `search_anime_resources` to find available resources
2. Identify the desired resource from search results
3. Use `download_resource` with the URL from search results

**Scenario 3: User asks about download history (e.g., "what's the latest episode?")**
1. Use `execute_sql_query` to query the database
2. The query tool supports pagination - results are paginated automatically
3. If you see "has_next_page: true" in the response, call the function again with the next page number to get more results
4. Example queries:
   - Latest episode: `SELECT anime_name, MAX(episode) as latest_ep, season, fansub, quality, downloaded_at FROM resources WHERE anime_name LIKE '%name%' GROUP BY anime_name ORDER BY downloaded_at DESC`
   - Recent downloads: `SELECT anime_name, season, episode, fansub, downloaded_at FROM resources ORDER BY downloaded_at DESC`
   - Specific anime: `SELECT * FROM resources WHERE anime_name LIKE '%name%' ORDER BY season, episode`
5. DO NOT add LIMIT or OFFSET to your SQL - pagination is handled automatically

**Scenario 4: User wants to download specific episode/fansub**
1. First search using `search_anime_resources` to see what's available
2. Analyze search results to find matching episode/fansub/quality
3. Use `download_resource` with the specific URL

## Important Guidelines:

- **Think step by step**: Break down complex requests into atomic tool calls
- **SQL queries**: When querying database, construct appropriate SQL SELECT statements
- **Database use**: The database contains all anime information known to the user and already downloaded. You can use this anime information (season, episode, etc.) to help determine the user's query and decide the actual download needs.
- **Search first**: If you don't have a download URL, search for it first
- **Parse RSS**: When user gives RSS link, parse it first to see what resources are available
- **Be smart**: Combine tools creatively to fulfill user requests
- **NEVER download resources marked as "✅ Downloaded"**: If search results show a resource is already downloaded, DO NOT attempt to download it again
- **Language**: Always respond in the same language as the user's message

Answer user questions in a friendly and professional manner."""

    def __init__(self, download_manager: DownloadManager):
        """Initialize assistant.

        Args:
            download_manager: DownloadManager instance for download operations
        """
        self.download_manager = download_manager
        self.client: Optional[AsyncOpenAI] = None
        self.model = config.llm.openai_model
        self.tools = get_assistant_tools()
        self.max_history = config.assistant.max_history_messages
        self.system_prompt = self.DEFAULT_SYSTEM_PROMPT
        self.client = self._create_openai_client()

    async def process_message(
        self,
        user_message: str,
        history: Optional[List[dict]] = None,
        status_callback: Optional[StatusCallback] = None,
    ) -> str:
        """Process user message and return response.

        Args:
            user_message: User's message
            history: Conversation history (list of message dicts with 'role' and 'content')
                    Should only include user/assistant messages from previous conversations
                status_callback: Optional callback for UI progress updates.
                        Receives (status enum, payload) for each state event.

        Returns:
            Assistant's response message
        """
        if not self.client:
            return (
                "❌ Assistant is not configured with OpenAI API key and cannot be used"
            )

        try:
            logger.info(f"Assistant: Processing message: {user_message}")
            messages = self._build_messages(user_message, history)

            await self._emit_status(status_callback, AssistantStatus.THINKING)

            return await self._run_conversation_loop(messages, status_callback)

        except Exception as e:
            logger.exception("Assistant: Error processing message")
            return f"❌ Error processing message: {str(e)}"

    async def _run_conversation_loop(
        self,
        messages: List[dict],
        status_callback: Optional[StatusCallback] = None,
    ) -> str:
        """Run the LLM interaction loop with tool calls until final answer."""
        for _ in range(self.MAX_TOOL_ITERATIONS):
            model_message = await self._request_model_response(messages)
            final_text = await self._handle_model_message(
                model_message, messages, status_callback
            )
            if final_text is not None:
                return final_text

        logger.warning("Assistant: Max iterations reached, forcing final response")
        return await self._force_final_response(messages)

    async def _request_model_response(self, messages: List[dict]):
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
        messages: List[dict],
        status_callback: Optional[StatusCallback] = None,
    ) -> Optional[str]:
        """Handle one model message; return final text if conversation can finish."""
        if not message.tool_calls:
            logger.info("Assistant: No tool calls, returning response")
            await self._emit_status(status_callback, AssistantStatus.FINALIZING)
            return message.content or "Sorry, I cannot understand your request"

        messages.append(message)
        await self._execute_all_tool_calls(
            message.tool_calls, messages, status_callback
        )
        return None

    async def _execute_all_tool_calls(
        self,
        tool_calls,
        messages: List[dict],
        status_callback: Optional[StatusCallback] = None,
    ) -> None:
        """Execute all tool calls in order and append their outputs to context."""
        for tool_call in tool_calls:
            tool_name = tool_call.function.name
            raw_arguments = tool_call.function.arguments
            arguments = self._safe_parse_arguments(raw_arguments)

            await self._emit_tool_status(status_callback, tool_name, arguments)

            tool_result = await self._execute_tool_call(tool_name, raw_arguments)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": tool_result,
                }
            )

            if not tool_result.startswith("Error"):
                logger.info(
                    f"Assistant: Tool {tool_name} result: {tool_result[:200]}..."
                )

    async def _execute_tool_call(self, tool_name: str, raw_arguments: str) -> str:
        """Execute one tool call and return tool output text."""
        try:
            arguments = json.loads(raw_arguments)
            logger.info(f"Assistant: Calling tool {tool_name} with {arguments}")
            return await handle_tool_call(tool_name, arguments, self.download_manager)
        except json.JSONDecodeError:
            error_msg = f"Failed to parse arguments for tool {tool_name}"
            logger.error(f"Assistant: {error_msg}: {raw_arguments}")
            return f"Error: {error_msg}. Please check your arguments format."
        except Exception as e:
            error_msg = f"Error executing tool {tool_name}"
            logger.exception(f"Assistant: {error_msg}")
            return f"Error: {error_msg}: {str(e)}"

    async def _force_final_response(self, messages: List[dict]) -> str:
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

    def _build_messages(
        self,
        user_message: str,
        history: Optional[List[dict]] = None,
    ) -> List[dict]:
        """Build the message list with system prompt, history, and user message."""
        messages = [{"role": "system", "content": self.system_prompt}]

        filtered_history = self._filter_history(history)
        if filtered_history:
            messages.extend(filtered_history)
            messages.append({"role": "system", "content": self.HISTORY_FOCUS_PROMPT})

        messages.append({"role": "user", "content": user_message})
        return messages

    def _filter_history(self, history: Optional[List[dict]]) -> List[dict]:
        """Keep only recent valid user/assistant history messages."""
        if not history:
            return []

        return [
            msg
            for msg in history[-self.max_history :]
            if msg.get("role") in ["user", "assistant"] and "tool_calls" not in msg
        ]

    @staticmethod
    def _build_tool_status_payload(tool_name: str, arguments: dict) -> dict[str, Any]:
        """Build structured payload for tool execution status event."""
        payload: dict[str, Any] = {"tool_name": tool_name}
        if tool_name == "download_resource" and "title" in arguments:
            payload["title"] = arguments["title"]
        return payload

    async def _emit_status(
        self,
        status_callback: Optional[StatusCallback],
        status: AssistantStatus,
        payload: Optional[dict[str, Any]] = None,
    ) -> None:
        """Emit one structured status event to outer integration layer."""
        if status_callback:
            await status_callback(status, payload or {})

    async def _emit_tool_status(
        self,
        status_callback: Optional[StatusCallback],
        tool_name: str,
        arguments: Optional[dict],
    ) -> None:
        """Emit tool-executing status event; skip when tool args are invalid JSON."""
        if arguments is None:
            return
        await self._emit_status(
            status_callback,
            AssistantStatus.TOOL_EXECUTING,
            self._build_tool_status_payload(tool_name, arguments),
        )

    @staticmethod
    def _safe_parse_arguments(raw_arguments: str) -> Optional[dict]:
        """Try to parse tool arguments JSON, return None on failure."""
        try:
            return json.loads(raw_arguments)
        except json.JSONDecodeError:
            return None

    def _create_openai_client(self) -> Optional[AsyncOpenAI]:
        """Create OpenAI client from configuration."""
        if not config.llm.openai_api_key:
            logger.warning("OpenAI API key not set, assistant will not work")
            return None

        return AsyncOpenAI(
            api_key=config.llm.openai_api_key,
            base_url=config.llm.openai_base_url,
            timeout=60.0,
        )
