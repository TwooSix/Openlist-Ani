"""Tests for batch parsing functionality."""

import json
from unittest.mock import AsyncMock, patch

from openlist_ani.core.parser.llm.batch_parser import extract_batch_results
from openlist_ani.core.parser.model import ParseResult, ResourceTitleParseResult
from openlist_ani.core.parser.parser import parse_metadata
from openlist_ani.core.website.model import AnimeResourceInfo


def _make_entry(title: str = "[Sub] Anime - 01 [1080p]") -> AnimeResourceInfo:
    return AnimeResourceInfo(title=title, download_url="magnet:?xt=urn:btih:abc")


BATCH_SUCCESS_JSON = json.dumps(
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
        },
        {
            "index": 2,
            "status": "success",
            "anime_name": "Frieren",
            "season": 1,
            "episode": 6,
            "quality": "1080p",
            "fansub": "SubGroup",
            "languages": ["简", "日"],
            "version": 1,
            "tmdb_id": 209867,
        },
    ]
)


class TestParseBatchResults:

    def test_all_success(self):
        content = f"```json\n{BATCH_SUCCESS_JSON}\n```"
        results = extract_batch_results(content, 2)
        assert len(results) == 2
        assert all(r.success for r in results)
        assert results[0].result.anime_name == "Frieren"
        assert results[0].result.episode == 5
        assert results[1].result.episode == 6

    def test_mixed_success_and_failed(self):
        content = json.dumps(
            [
                {
                    "index": 1,
                    "status": "success",
                    "anime_name": "Test",
                    "season": 1,
                    "episode": 1,
                    "quality": "1080p",
                    "fansub": None,
                    "languages": ["简"],
                    "version": 1,
                    "tmdb_id": 100,
                },
                {
                    "index": 2,
                    "status": "failed",
                    "title": "Unparsable Title",
                    "reason": "cannot determine anime",
                },
            ]
        )
        results = extract_batch_results(content, 2)
        assert results[0].success
        assert results[0].result.anime_name == "Test"
        assert not results[1].success
        assert "cannot determine anime" in results[1].error

    def test_missing_indices(self):
        content = json.dumps(
            [
                {
                    "index": 1,
                    "status": "success",
                    "anime_name": "A",
                    "season": 1,
                    "episode": 1,
                    "quality": "720p",
                    "fansub": None,
                    "languages": ["日"],
                    "version": 1,
                    "tmdb_id": 1,
                },
            ]
        )
        results = extract_batch_results(content, 3)
        assert len(results) == 3
        assert results[0].success
        assert not results[1].success
        assert "missing from response" in results[1].error
        assert not results[2].success

    def test_malformed_json(self):
        results = extract_batch_results("This is not JSON at all", 2)
        assert len(results) == 2
        assert all(not r.success for r in results)

    def test_non_array_json(self):
        results = extract_batch_results('{"not": "an array"}', 1)
        assert len(results) == 1
        assert not results[0].success

    def test_empty_content(self):
        results = extract_batch_results("", 2)
        assert len(results) == 2
        assert all(not r.success for r in results)

    def test_none_content(self):
        results = extract_batch_results("", 1)
        assert len(results) == 1
        assert not results[0].success

    def test_invalid_model_data_falls_back_to_failed(self):
        content = json.dumps(
            [
                {
                    "index": 1,
                    "status": "success",
                    "anime_name": "Test",
                },
            ]
        )
        results = extract_batch_results(content, 1)
        assert not results[0].success
        assert "Validation failed" in results[0].error


class TestParseMetadataBatch:

    async def test_returns_failed_list_when_no_api_key(self):
        entries = [_make_entry("Title A"), _make_entry("Title B")]
        with patch("openlist_ani.core.parser.parser.config") as mock_config:
            mock_config.llm.openai_api_key = ""
            results = await parse_metadata(entries)
        assert len(results) == 2
        assert all(not r.success for r in results)

    async def test_successful_batch_parse(self):
        entries = [
            _make_entry("[Sub] Frieren - 05 [1080p]"),
            _make_entry("[Sub] Frieren - 06 [1080p]"),
        ]

        batch_results = [
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
            ),
            ParseResult(
                success=True,
                result=ResourceTitleParseResult(
                    anime_name="Frieren",
                    season=1,
                    episode=6,
                    quality="1080p",
                    fansub="SubGroup",
                    languages=["简", "日"],
                    version=1,
                    tmdb_id=209867,
                ),
            ),
        ]

        with (
            patch("openlist_ani.core.parser.parser.config") as mock_config,
            patch(
                "openlist_ani.core.parser.parser.parse_title_batch_via_llm",
                new_callable=AsyncMock,
                return_value=list(batch_results),
            ),
            patch("openlist_ani.core.parser.parser.TMDBResolver") as MockResolver,
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

        assert len(results) == 2
        assert all(r.success for r in results)
        assert results[0].result.episode == 5
        assert results[1].result.episode == 6

    async def test_batch_failure_returns_error_results(self):
        entries = [_make_entry("[Sub] Anime - 01 [1080p]")]

        with (
            patch("openlist_ani.core.parser.parser.config") as mock_config,
            patch(
                "openlist_ani.core.parser.parser.parse_title_batch_via_llm",
                new_callable=AsyncMock,
                return_value=[
                    ParseResult(success=False, error="LLM returned no valid JSON array")
                ],
            ),
            patch("openlist_ani.core.parser.parser.TMDBResolver") as MockResolver,
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

    async def test_chunking_large_batch(self):
        entries = [_make_entry(f"Anime - {i:02d}") for i in range(25)]

        def make_chunk_results(titles):
            return [
                ParseResult(
                    success=True,
                    result=ResourceTitleParseResult(
                        anime_name="Anime",
                        season=1,
                        episode=i,
                        quality="1080p",
                        fansub=None,
                        languages=["日"],
                        version=1,
                        tmdb_id=1,
                    ),
                )
                for i in range(len(titles))
            ]

        with (
            patch("openlist_ani.core.parser.parser.config") as mock_config,
            patch(
                "openlist_ani.core.parser.parser.parse_title_batch_via_llm",
                new_callable=AsyncMock,
                side_effect=lambda llm, titles: make_chunk_results(titles),
            ),
            patch("openlist_ani.core.parser.parser.TMDBResolver") as MockResolver,
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

            results = await parse_metadata(entries, batch_size=20)

        assert len(results) == 25
        assert all(r.success for r in results)
