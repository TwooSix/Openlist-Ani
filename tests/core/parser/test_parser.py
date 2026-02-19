"""Tests for openlist_ani.core.parser.parser module (parse_metadata)."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openlist_ani.core.parser.model import ResourceTitleParseResult
from openlist_ani.core.parser.parser import parse_metadata
from openlist_ani.core.website.model import AnimeResourceInfo


def _make_entry(title: str = "[SubGroup] Frieren - 05 [1080p]") -> AnimeResourceInfo:
    return AnimeResourceInfo(title=title, download_url="magnet:?xt=urn:btih:abc123")


def _make_chat_message(content: str | None, tool_calls=None):
    """Build a mock ChatCompletionMessage."""
    msg = MagicMock()
    msg.content = content
    msg.tool_calls = tool_calls
    return msg


def _make_chat_response(message):
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message = message
    return resp


VALID_JSON_RESPONSE = json.dumps(
    {
        "anime_name": "Frieren",
        "season": 1,
        "episode": 5,
        "quality": "1080p",
        "fansub": "SubGroup",
        "languages": ["简", "日"],
        "version": 1,
        "tmdb_id": 209867,
    }
)


class TestParseMetadata:
    """Test parse_metadata async function with mocked LLM and TMDB."""

    @pytest.mark.asyncio
    async def test_returns_none_when_no_api_key(self):
        entry = _make_entry()
        with patch("openlist_ani.core.parser.parser.config") as mock_config:
            mock_config.llm.openai_api_key = ""
            result = await parse_metadata(entry)
        assert result is None

    @pytest.mark.asyncio
    async def test_successful_parse_no_tool_calls(self):
        """LLM returns valid JSON directly without tool calls."""
        entry = _make_entry()

        message = _make_chat_message(content=f"```json\n{VALID_JSON_RESPONSE}\n```")
        response = _make_chat_response(message)

        with (
            patch("openlist_ani.core.parser.parser.config") as mock_config,
            patch("openlist_ani.core.parser.parser.AsyncOpenAI") as MockOpenAI,
        ):
            mock_config.llm.openai_api_key = "test-key"
            mock_config.llm.openai_base_url = "https://api.example.com"
            mock_config.llm.openai_model = "gpt-4"

            mock_client = AsyncMock()
            mock_client.chat.completions.create = AsyncMock(return_value=response)
            MockOpenAI.return_value = mock_client

            result = await parse_metadata(entry)

        assert result is not None
        assert isinstance(result, ResourceTitleParseResult)
        assert result.anime_name == "Frieren"
        assert result.season == 1
        assert result.episode == 5

    @pytest.mark.asyncio
    async def test_returns_none_on_invalid_json(self):
        """LLM returns non-JSON content."""
        entry = _make_entry()

        message = _make_chat_message(content="I don't know the answer.")
        response = _make_chat_response(message)

        with (
            patch("openlist_ani.core.parser.parser.config") as mock_config,
            patch("openlist_ani.core.parser.parser.AsyncOpenAI") as MockOpenAI,
        ):
            mock_config.llm.openai_api_key = "test-key"
            mock_config.llm.openai_base_url = "https://api.example.com"
            mock_config.llm.openai_model = "gpt-4"

            mock_client = AsyncMock()
            mock_client.chat.completions.create = AsyncMock(return_value=response)
            MockOpenAI.return_value = mock_client

            result = await parse_metadata(entry)

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_validation_failure(self):
        """LLM returns JSON but with invalid schema."""
        invalid_json = json.dumps({"anime_name": "Test"})  # missing required fields
        entry = _make_entry()

        message = _make_chat_message(content=invalid_json)
        response = _make_chat_response(message)

        with (
            patch("openlist_ani.core.parser.parser.config") as mock_config,
            patch("openlist_ani.core.parser.parser.AsyncOpenAI") as MockOpenAI,
        ):
            mock_config.llm.openai_api_key = "test-key"
            mock_config.llm.openai_base_url = "https://api.example.com"
            mock_config.llm.openai_model = "gpt-4"

            mock_client = AsyncMock()
            mock_client.chat.completions.create = AsyncMock(return_value=response)
            MockOpenAI.return_value = mock_client

            result = await parse_metadata(entry)

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_timeout(self):
        """Timeout during LLM call should return None, not crash."""
        import asyncio

        entry = _make_entry()

        with (
            patch("openlist_ani.core.parser.parser.config") as mock_config,
            patch("openlist_ani.core.parser.parser.AsyncOpenAI") as MockOpenAI,
        ):
            mock_config.llm.openai_api_key = "test-key"
            mock_config.llm.openai_base_url = "https://api.example.com"
            mock_config.llm.openai_model = "gpt-4"

            mock_client = AsyncMock()
            mock_client.chat.completions.create = AsyncMock(
                side_effect=asyncio.TimeoutError()
            )
            MockOpenAI.return_value = mock_client

            result = await parse_metadata(entry)

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_generic_exception(self):
        """Unexpected exception should be caught, return None."""
        entry = _make_entry()

        with (
            patch("openlist_ani.core.parser.parser.config") as mock_config,
            patch("openlist_ani.core.parser.parser.AsyncOpenAI") as MockOpenAI,
        ):
            mock_config.llm.openai_api_key = "test-key"
            mock_config.llm.openai_base_url = "https://api.example.com"
            mock_config.llm.openai_model = "gpt-4"

            mock_client = AsyncMock()
            mock_client.chat.completions.create = AsyncMock(
                side_effect=RuntimeError("connection failed")
            )
            MockOpenAI.return_value = mock_client

            result = await parse_metadata(entry)

        assert result is None

    @pytest.mark.asyncio
    async def test_handles_tool_calls_then_final_answer(self):
        """LLM makes a tool call, then returns final JSON."""
        entry = _make_entry()

        # First response: tool call
        tool_call = MagicMock()
        tool_call.function.name = "search_tmdb"
        tool_call.function.arguments = json.dumps({"query": "Frieren"})
        tool_call.id = "call_001"

        first_message = _make_chat_message(content=None, tool_calls=[tool_call])
        first_response = _make_chat_response(first_message)

        # Second response: final JSON
        final_message = _make_chat_message(
            content=f"```json\n{VALID_JSON_RESPONSE}\n```"
        )
        final_response = _make_chat_response(final_message)

        with (
            patch("openlist_ani.core.parser.parser.config") as mock_config,
            patch("openlist_ani.core.parser.parser.AsyncOpenAI") as MockOpenAI,
            patch("openlist_ani.core.parser.parser.TMDBClient"),
            patch("openlist_ani.core.parser.parser.handle_search_tmdb") as mock_handle,
        ):
            mock_config.llm.openai_api_key = "test-key"
            mock_config.llm.openai_base_url = "https://api.example.com"
            mock_config.llm.openai_model = "gpt-4"

            mock_client = AsyncMock()
            mock_client.chat.completions.create = AsyncMock(
                side_effect=[first_response, final_response]
            )
            MockOpenAI.return_value = mock_client

            mock_handle.return_value = (
                None  # handle_search_tmdb modifies messages in place
            )

            result = await parse_metadata(entry)

        assert result is not None
        assert result.anime_name == "Frieren"

    @pytest.mark.asyncio
    async def test_llm_returns_empty_content(self):
        """LLM returns message with content=None and no tool calls."""
        entry = _make_entry()

        message = _make_chat_message(content=None)
        response = _make_chat_response(message)

        with (
            patch("openlist_ani.core.parser.parser.config") as mock_config,
            patch("openlist_ani.core.parser.parser.AsyncOpenAI") as MockOpenAI,
        ):
            mock_config.llm.openai_api_key = "test-key"
            mock_config.llm.openai_base_url = "https://api.example.com"
            mock_config.llm.openai_model = "gpt-4"

            mock_client = AsyncMock()
            mock_client.chat.completions.create = AsyncMock(return_value=response)
            MockOpenAI.return_value = mock_client

            result = await parse_metadata(entry)

        assert result is None
