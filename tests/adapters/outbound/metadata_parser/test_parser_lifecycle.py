import asyncio

from openlist_ani.application.anime_library_ingestion.models import (
    ParseResult,
    ReleaseTitleParseResult,
)
from openlist_ani.domain.anime_release import AnimeRelease, LanguageType, VideoQuality
from openlist_ani.adapters.outbound.metadata_parser.parser import MetadataParserAdapter
from openlist_ani.adapters.outbound.metadata_parser.settings import (
    MetadataParserSettings,
)


def test_title_parser_engines_are_exposed_from_parser_package():
    from openlist_ani.adapters.outbound.metadata_parser import (
        LLMTitleExtractEngine,
        MetadataParserEngine,
        RegexTitleExtractEngine,
    )
    from openlist_ani.adapters.outbound.metadata_parser.llm import (
        LLMTitleExtractEngine as LLMEngineFromSiblingPackage,
    )
    from openlist_ani.adapters.outbound.metadata_parser.regex import (
        RegexTitleExtractEngine as RegexEngineFromSiblingPackage,
    )

    assert MetadataParserEngine is not None
    assert LLMTitleExtractEngine is LLMEngineFromSiblingPackage
    assert RegexTitleExtractEngine is RegexEngineFromSiblingPackage


def test_llm_clients_are_exposed_from_integrations_package():
    from openlist_ani.integrations.llm import (
        AnthropicLLMClient,
        LLMClient,
        LLMClientSettings,
        OpenAILLMClient,
        create_llm_client,
    )

    assert LLMClient is not None
    assert LLMClientSettings is not None
    assert OpenAILLMClient is not None
    assert AnthropicLLMClient is not None
    assert create_llm_client is not None


class FakeParserEngine:
    def __init__(self):
        self.title_batches = []

    async def parse_titles(self, titles):
        self.title_batches.append(list(titles))
        await asyncio.sleep(0)
        return [
            ParseResult(
                success=True,
                result=ReleaseTitleParseResult(
                    anime_name="金牌得主",
                    season=1,
                    episode=5,
                    quality=VideoQuality.Q1080P,
                    fansub="喵萌奶茶屋",
                    languages=[LanguageType.CHS],
                    version=1,
                ),
            )
            for _ in titles
        ]


async def test_metadata_parser_adapter_uses_injected_engine_and_cache():
    engine = FakeParserEngine()
    adapter = MetadataParserAdapter(
        llm_client=None,
        parser_engine=engine,
    )
    entry = AnimeRelease(
        title="[喵萌奶茶屋] 金牌得主 - 05 [1080p][简]",
        download_url="https://example.invalid/test.torrent",
    )

    first = await adapter.parse([entry])
    second = await adapter.parse([entry])

    assert engine.title_batches == [[entry.title]]
    assert first[0].success is True
    assert first[0].release_title == entry.title
    assert second[0].success is True
    assert second[0].result == first[0].result


async def test_metadata_parser_adapter_from_regex_settings_only_parses_title():
    adapter = MetadataParserAdapter.from_regex_settings(
        MetadataParserSettings(
            provider_type="openai",
            api_key="",
            base_url="https://example.invalid/v1",
            model="unused",
        )
    )

    results = await adapter.parse(
        [
            AnimeRelease(
                title="[喵萌奶茶屋] 金牌得主 - 05 [1080p][简]",
                download_url="https://example.invalid/test.torrent",
            )
        ]
    )

    assert results[0].success is True
    assert results[0].result is not None
    assert results[0].result.anime_name == "金牌得主"
    assert results[0].result.tmdb_id is None
