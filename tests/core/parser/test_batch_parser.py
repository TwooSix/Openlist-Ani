"""Tests for batch parsing functionality and LLM TMDB selector."""

import json
from unittest.mock import AsyncMock, patch

from openlist_ani.core.parser.llm.batch_parser import extract_batch_results
from openlist_ani.core.parser.llm.tmdb_selector import (
    generate_tmdb_queries,
    select_tmdb_candidate,
)
from openlist_ani.core.parser.model import (
    ParseResult,
    ResourceTitleParseResult,
    TMDBCandidate,
    TMDBMatch,
)
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
    # Additionally, test_returns_failed_list_when_no_api_key is an
    # exact duplicate of TestParseMetadata.test_returns_failed_when_no_api_key.
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


# =========================================================================
# generate_tmdb_queries — LLM query expansion
# =========================================================================


class TestGenerateTmdbQueries:
    """Tests for generate_tmdb_queries in llm/tmdb_selector.py."""

    async def test_returns_expanded_queries(self):
        """LLM returns valid JSON with queries list."""
        mock_llm = AsyncMock()
        mock_llm.complete_chat.return_value = json.dumps(
            {"queries": ["Frieren", "葬送のフリーレン", "Sousou no Frieren"]}
        )
        result = await generate_tmdb_queries(mock_llm, "Frieren")
        assert result == ["Frieren", "葬送のフリーレン", "Sousou no Frieren"]
        mock_llm.complete_chat.assert_awaited_once()

    async def test_returns_queries_from_markdown_code_block(self):
        mock_llm = AsyncMock()
        mock_llm.complete_chat.return_value = (
            '```json\n{"queries": ["Query A", "Query B"]}\n```'
        )
        result = await generate_tmdb_queries(mock_llm, "Test Anime")
        assert result == ["Query A", "Query B"]

    async def test_fallback_to_anime_name_when_no_payload(self):
        """When LLM returns no valid JSON, fall back to [anime_name]."""
        mock_llm = AsyncMock()
        mock_llm.complete_chat.return_value = "I cannot help with that."
        result = await generate_tmdb_queries(mock_llm, "Frieren")
        assert result == ["Frieren"]

    async def test_fallback_to_anime_name_when_empty_queries(self):
        """When LLM returns JSON with empty queries list."""
        mock_llm = AsyncMock()
        mock_llm.complete_chat.return_value = json.dumps({"queries": []})
        result = await generate_tmdb_queries(mock_llm, "Frieren")
        assert result == ["Frieren"]

    async def test_fallback_on_exception(self):
        """When LLM raises an exception, fall back to [anime_name]."""
        mock_llm = AsyncMock()
        mock_llm.complete_chat.side_effect = RuntimeError("LLM error")
        result = await generate_tmdb_queries(mock_llm, "Frieren")
        assert result == ["Frieren"]

    async def test_deduplicates_queries(self):
        """Duplicate queries should be removed."""
        mock_llm = AsyncMock()
        mock_llm.complete_chat.return_value = json.dumps(
            {"queries": ["Frieren", "Frieren", "Other"]}
        )
        result = await generate_tmdb_queries(mock_llm, "Frieren")
        assert result == ["Frieren", "Other"]

    async def test_strips_whitespace_from_queries(self):
        mock_llm = AsyncMock()
        mock_llm.complete_chat.return_value = json.dumps(
            {"queries": ["  Frieren  ", "Other"]}
        )
        result = await generate_tmdb_queries(mock_llm, "Frieren")
        assert result == ["Frieren", "Other"]

    async def test_skips_non_string_queries(self):
        mock_llm = AsyncMock()
        mock_llm.complete_chat.return_value = json.dumps(
            {"queries": ["Valid", 123, None, "Also Valid"]}
        )
        result = await generate_tmdb_queries(mock_llm, "Test")
        assert result == ["Valid", "Also Valid"]

    async def test_limits_to_max_tmdb_queries(self):
        """Result should be capped at MAX_TMDB_QUERIES."""
        mock_llm = AsyncMock()
        mock_llm.complete_chat.return_value = json.dumps(
            {"queries": [f"query_{i}" for i in range(20)]}
        )
        result = await generate_tmdb_queries(mock_llm, "Test")
        # MAX_TMDB_QUERIES is 5
        assert len(result) == 5

    async def test_non_dict_json_falls_back(self):
        """If LLM returns a JSON array instead of dict, fall back."""
        mock_llm = AsyncMock()
        mock_llm.complete_chat.return_value = '["Frieren", "Other"]'
        # parse_json_from_markdown looks for {}, not [] — returns None
        # → fallback to [anime_name]
        result = await generate_tmdb_queries(mock_llm, "Frieren")
        assert result == ["Frieren"]

    async def test_skips_empty_string_queries(self):
        mock_llm = AsyncMock()
        mock_llm.complete_chat.return_value = json.dumps(
            {"queries": ["", "  ", "Valid"]}
        )
        result = await generate_tmdb_queries(mock_llm, "Test")
        assert result == ["Valid"]


# =========================================================================
# select_tmdb_candidate — LLM candidate selection
# =========================================================================


def _make_candidate(
    tmdb_id: int, name: str, original_name: str = ""
) -> TMDBCandidate:
    return TMDBCandidate(
        id=tmdb_id,
        name=name,
        original_name=original_name or name,
        first_air_date="2023-01-01",
        overview="A test anime.",
    )


class TestSelectTmdbCandidate:
    """Tests for select_tmdb_candidate in llm/tmdb_selector.py."""

    async def test_selects_correct_candidate(self):
        mock_llm = AsyncMock()
        mock_llm.complete_chat.return_value = json.dumps(
            {"tmdb_id": 209867, "anime_name": "Frieren", "confidence": "high"}
        )
        candidates = [
            _make_candidate(209867, "Frieren: Beyond Journey's End", "葬送のフリーレン"),
            _make_candidate(100000, "Other Anime"),
        ]
        result = await select_tmdb_candidate(mock_llm, "Frieren", candidates)
        assert result is not None
        assert result.tmdb_id == 209867
        assert result.anime_name == "Frieren: Beyond Journey's End"
        assert result.confidence == "high"

    async def test_returns_none_when_no_payload(self):
        mock_llm = AsyncMock()
        mock_llm.complete_chat.return_value = "Sorry, I can't determine this."
        candidates = [_make_candidate(1, "Test")]
        result = await select_tmdb_candidate(mock_llm, "Unknown", candidates)
        assert result is None

    async def test_returns_none_when_tmdb_id_not_in_candidates(self):
        """LLM picks an ID that doesn't match any candidate."""
        mock_llm = AsyncMock()
        mock_llm.complete_chat.return_value = json.dumps(
            {"tmdb_id": 999999, "confidence": "low"}
        )
        candidates = [_make_candidate(1, "Anime A"), _make_candidate(2, "Anime B")]
        result = await select_tmdb_candidate(mock_llm, "Test", candidates)
        assert result is None

    async def test_returns_none_when_tmdb_id_is_none(self):
        mock_llm = AsyncMock()
        mock_llm.complete_chat.return_value = json.dumps(
            {"tmdb_id": None, "confidence": "low"}
        )
        candidates = [_make_candidate(1, "Anime")]
        result = await select_tmdb_candidate(mock_llm, "Test", candidates)
        assert result is None

    async def test_returns_none_on_non_dict_json(self):
        mock_llm = AsyncMock()
        mock_llm.complete_chat.return_value = "[1, 2, 3]"
        candidates = [_make_candidate(1, "Anime")]
        result = await select_tmdb_candidate(mock_llm, "Test", candidates)
        assert result is None

    async def test_returns_none_on_exception(self):
        mock_llm = AsyncMock()
        mock_llm.complete_chat.side_effect = RuntimeError("Network error")
        candidates = [_make_candidate(1, "Anime")]
        result = await select_tmdb_candidate(mock_llm, "Test", candidates)
        assert result is None

    async def test_uses_candidate_name_over_llm_anime_name(self):
        """The selected candidate's name should be used if available."""
        mock_llm = AsyncMock()
        mock_llm.complete_chat.return_value = json.dumps(
            {"tmdb_id": 42, "anime_name": "LLM Name", "confidence": "medium"}
        )
        candidates = [_make_candidate(42, "Candidate Name")]
        result = await select_tmdb_candidate(mock_llm, "Original", candidates)
        assert result is not None
        # candidate.name takes precedence
        assert result.anime_name == "Candidate Name"

    async def test_falls_back_to_llm_anime_name_when_candidate_name_none(self):
        """When candidate.name is None, use LLM's anime_name."""
        mock_llm = AsyncMock()
        mock_llm.complete_chat.return_value = json.dumps(
            {"tmdb_id": 42, "anime_name": "LLM Name", "confidence": "medium"}
        )
        candidates = [TMDBCandidate(id=42, name=None, original_name="Original JP")]
        result = await select_tmdb_candidate(mock_llm, "Original", candidates)
        assert result is not None
        assert result.anime_name == "LLM Name"

    async def test_default_confidence_is_unknown(self):
        mock_llm = AsyncMock()
        mock_llm.complete_chat.return_value = json.dumps({"tmdb_id": 42})
        candidates = [_make_candidate(42, "Test Anime")]
        result = await select_tmdb_candidate(mock_llm, "Test", candidates)
        assert result is not None
        assert result.confidence == "unknown"

    async def test_response_in_markdown_code_block(self):
        mock_llm = AsyncMock()
        mock_llm.complete_chat.return_value = (
            '```json\n{"tmdb_id": 42, "confidence": "high"}\n```'
        )
        candidates = [_make_candidate(42, "Test Anime")]
        result = await select_tmdb_candidate(mock_llm, "Test", candidates)
        assert result is not None
        assert result.tmdb_id == 42
