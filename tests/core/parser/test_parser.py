"""Tests for openlist_ani.core.parser.parser module (parse_metadata)."""

import json
from unittest.mock import AsyncMock, patch

import pytest

from openlist_ani.core.parser.model import ParseResult, ResourceTitleParseResult
from openlist_ani.core.parser.parser import parse_metadata
from openlist_ani.core.website.model import AnimeResourceInfo


def _make_entry(title: str = "[SubGroup] Frieren - 05 [1080p]") -> AnimeResourceInfo:
    return AnimeResourceInfo(title=title, download_url="magnet:?xt=urn:btih:abc123")


VALID_BATCH_JSON = json.dumps(
    [
        {
            "index": 1,
            "status": "success",
            "anime_name": "Frieren",
            "season": 1,
            "episode": 5,
            "quality": "1080p",
            "fansub": "SubGroup",
            "languages": ["简", "日"],
            "version": 1,
            "tmdb_id": 209867,
        }
    ]
)

VALID_PARSE_RESULTS = [
    ParseResult(
        success=True,
        result=ResourceTitleParseResult(
            anime_name="Frieren",
            season=1,
            episode=5,
            quality="1080p",
            fansub="SubGroup",
            languages=["简", "日"],
            version=1,
            tmdb_id=209867,
        ),
    )
]


class TestParseMetadata:

    @pytest.mark.asyncio
    async def test_returns_failed_when_no_api_key(self):
        entries = [_make_entry()]
        with patch("openlist_ani.core.parser.parser.config") as mock_config:
            mock_config.llm.openai_api_key = ""
            results = await parse_metadata(entries)
        assert len(results) == 1
        assert not results[0].success
        assert results[0].error is not None

    @pytest.mark.asyncio
    async def test_successful_parse(self):
        entries = [_make_entry()]

        with (
            patch("openlist_ani.core.parser.parser.config") as mock_config,
            patch(
                "openlist_ani.core.parser.parser.parse_title_batch_via_llm",
                new_callable=AsyncMock,
                return_value=list(VALID_PARSE_RESULTS),
            ),
            patch(
                "openlist_ani.core.parser.parser.TMDBResolver",
            ) as MockResolver,
            patch("openlist_ani.core.parser.parser.get_tmdb_client"),
            patch("openlist_ani.core.parser.parser.OpenAILLMClient"),
        ):
            mock_config.llm.openai_api_key = "test-key"
            mock_config.llm.openai_base_url = "https://api.example.com"
            mock_config.llm.openai_model = "gpt-4"

            mock_resolver = AsyncMock()
            mock_resolver.resolve_and_validate = AsyncMock(
                side_effect=lambda parsed: parsed
            )
            MockResolver.return_value = mock_resolver

            results = await parse_metadata(entries)

        assert len(results) == 1
        assert results[0].success
        assert isinstance(results[0].result, ResourceTitleParseResult)
        assert results[0].result.anime_name == "Frieren"
        assert results[0].result.season == 1
        assert results[0].result.episode == 5

    @pytest.mark.asyncio
    async def test_returns_failed_on_invalid_json(self):
        entries = [_make_entry()]

        with (
            patch("openlist_ani.core.parser.parser.config") as mock_config,
            patch(
                "openlist_ani.core.parser.parser.parse_title_batch_via_llm",
                new_callable=AsyncMock,
                return_value=[
                    ParseResult(success=False, error="LLM returned no valid JSON array")
                ],
            ),
            patch(
                "openlist_ani.core.parser.parser.TMDBResolver",
            ) as MockResolver,
            patch("openlist_ani.core.parser.parser.get_tmdb_client"),
            patch("openlist_ani.core.parser.parser.OpenAILLMClient"),
        ):
            mock_config.llm.openai_api_key = "test-key"
            mock_config.llm.openai_base_url = "https://api.example.com"
            mock_config.llm.openai_model = "gpt-4"

            mock_resolver = AsyncMock()
            mock_resolver.resolve_and_validate = AsyncMock(
                side_effect=lambda parsed: parsed
            )
            MockResolver.return_value = mock_resolver

            results = await parse_metadata(entries)

        assert len(results) == 1
        assert not results[0].success

    @pytest.mark.asyncio
    async def test_returns_failed_on_exception(self):
        entries = [_make_entry()]

        with (
            patch("openlist_ani.core.parser.parser.config") as mock_config,
            patch(
                "openlist_ani.core.parser.parser.parse_title_batch_via_llm",
                new_callable=AsyncMock,
                return_value=[ParseResult(success=False, error="connection failed")],
            ),
            patch(
                "openlist_ani.core.parser.parser.TMDBResolver",
            ) as MockResolver,
            patch("openlist_ani.core.parser.parser.get_tmdb_client"),
            patch("openlist_ani.core.parser.parser.OpenAILLMClient"),
        ):
            mock_config.llm.openai_api_key = "test-key"
            mock_config.llm.openai_base_url = "https://api.example.com"
            mock_config.llm.openai_model = "gpt-4"

            mock_resolver = AsyncMock()
            mock_resolver.resolve_and_validate = AsyncMock(
                side_effect=lambda parsed: parsed
            )
            MockResolver.return_value = mock_resolver

            results = await parse_metadata(entries)

        assert len(results) == 1
        assert not results[0].success
        assert "connection failed" in results[0].error

    @pytest.mark.asyncio
    async def test_resolve_and_validate_called_after_parse(self):
        entries = [_make_entry()]

        with (
            patch("openlist_ani.core.parser.parser.config") as mock_config,
            patch(
                "openlist_ani.core.parser.parser.parse_title_batch_via_llm",
                new_callable=AsyncMock,
                return_value=list(VALID_PARSE_RESULTS),
            ),
            patch(
                "openlist_ani.core.parser.parser.TMDBResolver",
            ) as MockResolver,
            patch("openlist_ani.core.parser.parser.get_tmdb_client"),
            patch("openlist_ani.core.parser.parser.OpenAILLMClient"),
        ):
            mock_config.llm.openai_api_key = "test-key"
            mock_config.llm.openai_base_url = "https://api.example.com"
            mock_config.llm.openai_model = "gpt-4"

            mock_resolver = AsyncMock()
            mock_resolver.resolve_and_validate = AsyncMock(
                side_effect=lambda parsed: parsed
            )
            MockResolver.return_value = mock_resolver

            results = await parse_metadata(entries)

        assert len(results) == 1
        assert results[0].success
        assert results[0].result.anime_name == "Frieren"
        mock_resolver.resolve_and_validate.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_empty_content_returns_failed(self):
        entries = [_make_entry()]

        with (
            patch("openlist_ani.core.parser.parser.config") as mock_config,
            patch(
                "openlist_ani.core.parser.parser.parse_title_batch_via_llm",
                new_callable=AsyncMock,
                return_value=[
                    ParseResult(success=False, error="LLM returned no valid JSON array")
                ],
            ),
            patch(
                "openlist_ani.core.parser.parser.TMDBResolver",
            ) as MockResolver,
            patch("openlist_ani.core.parser.parser.get_tmdb_client"),
            patch("openlist_ani.core.parser.parser.OpenAILLMClient"),
        ):
            mock_config.llm.openai_api_key = "test-key"
            mock_config.llm.openai_base_url = "https://api.example.com"
            mock_config.llm.openai_model = "gpt-4"

            mock_resolver = AsyncMock()
            mock_resolver.resolve_and_validate = AsyncMock(
                side_effect=lambda parsed: parsed
            )
            MockResolver.return_value = mock_resolver

            results = await parse_metadata(entries)

        assert len(results) == 1
        assert not results[0].success
