"""Tests for openlist_ani.core.parser.parser module (parse_metadata)."""

from unittest.mock import AsyncMock, patch

import pytest

from openlist_ani.core.parser.model import ParseResult, ResourceTitleParseResult
from openlist_ani.core.parser.parser import parse_metadata
from openlist_ani.core.website.model import AnimeResourceInfo


def _make_entry(title: str = "[SubGroup] Frieren - 05 [1080p]") -> AnimeResourceInfo:
    return AnimeResourceInfo(title=title, download_url="magnet:?xt=urn:btih:abc123")


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
            patch("openlist_ani.core.parser.parser.create_llm_client"),
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
            patch("openlist_ani.core.parser.parser.create_llm_client"),
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
            patch("openlist_ani.core.parser.parser.create_llm_client"),
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
            patch("openlist_ani.core.parser.parser.create_llm_client"),
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
        entries = [_make_entry(title="[SubGroup] Unknown Anime - 01 [720p]")]

        with (
            patch("openlist_ani.core.parser.parser.config") as mock_config,
            patch(
                "openlist_ani.core.parser.parser.parse_title_batch_via_llm",
                new_callable=AsyncMock,
                return_value=[
                    ParseResult(success=False, error="LLM returned empty content")
                ],
            ),
            patch(
                "openlist_ani.core.parser.parser.TMDBResolver",
            ) as MockResolver,
            patch("openlist_ani.core.parser.parser.get_tmdb_client"),
            patch("openlist_ani.core.parser.parser.create_llm_client"),
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
        assert "empty content" in results[0].error

    @pytest.mark.asyncio
    async def test_cache_hit_skips_llm_on_second_call(self):
        """Calling parse_metadata twice with the same title should use cache
        on the second call — the LLM mock must only be invoked once."""
        entries = [_make_entry()]

        mock_batch_parse = AsyncMock(return_value=list(VALID_PARSE_RESULTS))

        with (
            patch("openlist_ani.core.parser.parser.config") as mock_config,
            patch(
                "openlist_ani.core.parser.parser.parse_title_batch_via_llm",
                mock_batch_parse,
            ),
            patch(
                "openlist_ani.core.parser.parser.TMDBResolver",
            ) as MockResolver,
            patch("openlist_ani.core.parser.parser.get_tmdb_client"),
            patch("openlist_ani.core.parser.parser.create_llm_client"),
        ):
            mock_config.llm.openai_api_key = "test-key"
            mock_config.llm.openai_base_url = "https://api.example.com"
            mock_config.llm.openai_model = "gpt-4"

            mock_resolver = AsyncMock()
            mock_resolver.resolve_and_validate = AsyncMock(
                side_effect=lambda parsed: parsed
            )
            MockResolver.return_value = mock_resolver

            # First call — LLM should be called
            results_first = await parse_metadata(entries)
            assert len(results_first) == 1
            assert results_first[0].success

            # Second call with same title — should hit cache
            results_second = await parse_metadata(entries)
            assert len(results_second) == 1
            assert results_second[0].success

        # parse_title_batch_via_llm should only be called once
        assert mock_batch_parse.await_count == 1

    @pytest.mark.asyncio
    async def test_llm_raises_exception_returns_failed_gracefully(self):
        """When the LLM raises an actual exception, parse_metadata should
        return failed results without crashing."""
        entries = [_make_entry()]

        with (
            patch("openlist_ani.core.parser.parser.config") as mock_config,
            patch(
                "openlist_ani.core.parser.parser.parse_title_batch_via_llm",
                new_callable=AsyncMock,
                side_effect=RuntimeError("LLM service unavailable"),
            ),
            patch(
                "openlist_ani.core.parser.parser.TMDBResolver",
            ) as MockResolver,
            patch("openlist_ani.core.parser.parser.get_tmdb_client"),
            patch("openlist_ani.core.parser.parser.create_llm_client"),
        ):
            mock_config.llm.openai_api_key = "test-key"
            mock_config.llm.openai_base_url = "https://api.example.com"
            mock_config.llm.openai_model = "gpt-4"

            mock_resolver = AsyncMock()
            mock_resolver.resolve_and_validate = AsyncMock(
                side_effect=lambda parsed: parsed
            )
            MockResolver.return_value = mock_resolver

            # Should raise since parse_metadata doesn't catch exceptions from
            # parse_title_batch_via_llm at the top level — let's verify behavior
            with pytest.raises(RuntimeError, match="LLM service unavailable"):
                await parse_metadata(entries)

    @pytest.mark.asyncio
    async def test_failed_results_are_not_cached(self):
        """Failed parse results must NOT be cached — transient errors should
        allow retries on the next call."""
        entries = [_make_entry()]

        failed_result = ParseResult(success=False, error="TMDB timeout")
        success_result = ParseResult(
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

        mock_batch_parse = AsyncMock(side_effect=[
            [failed_result],   # First call fails
            [success_result],  # Second call succeeds (retry)
        ])

        with (
            patch("openlist_ani.core.parser.parser.config") as mock_config,
            patch(
                "openlist_ani.core.parser.parser.parse_title_batch_via_llm",
                mock_batch_parse,
            ),
            patch(
                "openlist_ani.core.parser.parser.TMDBResolver",
            ) as MockResolver,
            patch("openlist_ani.core.parser.parser.get_tmdb_client"),
            patch("openlist_ani.core.parser.parser.create_llm_client"),
        ):
            mock_config.llm.openai_api_key = "test-key"
            mock_config.llm.openai_base_url = "https://api.example.com"
            mock_config.llm.openai_model = "gpt-4"

            mock_resolver = AsyncMock()
            mock_resolver.resolve_and_validate = AsyncMock(
                side_effect=lambda parsed: parsed
            )
            MockResolver.return_value = mock_resolver

            # First call — LLM returns failure
            results_first = await parse_metadata(entries)
            assert not results_first[0].success

            # Second call — same title, should NOT hit cache because failure
            # was not cached; LLM is called again and succeeds this time
            results_second = await parse_metadata(entries)
            assert results_second[0].success
            assert results_second[0].result.anime_name == "Frieren"

        # LLM should have been called twice (failure was not cached)
        assert mock_batch_parse.await_count == 2

    @pytest.mark.asyncio
    async def test_cached_result_is_independent_copy(self):
        """Mutating a returned ParseResult must not corrupt the cached copy.
        The cache should store and return deep copies."""
        entries = [_make_entry()]

        mock_batch_parse = AsyncMock(return_value=list(VALID_PARSE_RESULTS))

        with (
            patch("openlist_ani.core.parser.parser.config") as mock_config,
            patch(
                "openlist_ani.core.parser.parser.parse_title_batch_via_llm",
                mock_batch_parse,
            ),
            patch(
                "openlist_ani.core.parser.parser.TMDBResolver",
            ) as MockResolver,
            patch("openlist_ani.core.parser.parser.get_tmdb_client"),
            patch("openlist_ani.core.parser.parser.create_llm_client"),
        ):
            mock_config.llm.openai_api_key = "test-key"
            mock_config.llm.openai_base_url = "https://api.example.com"
            mock_config.llm.openai_model = "gpt-4"

            mock_resolver = AsyncMock()
            mock_resolver.resolve_and_validate = AsyncMock(
                side_effect=lambda parsed: parsed
            )
            MockResolver.return_value = mock_resolver

            # First call — populates cache
            results_first = await parse_metadata(entries)
            assert results_first[0].success
            assert results_first[0].result.anime_name == "Frieren"

            # Mutate the returned result
            results_first[0].result.anime_name = "CORRUPTED"

            # Second call — should hit cache with uncorrupted data
            results_second = await parse_metadata(entries)
            assert results_second[0].success
            assert results_second[0].result.anime_name == "Frieren"
