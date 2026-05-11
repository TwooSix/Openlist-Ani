import asyncio

from openlist_ani.adapters.outbound.metadata_validator.pipeline import (
    MetadataValidationPipeline,
)
from openlist_ani.application.anime_library_ingestion.models import (
    EpisodeMapping,
    ParseResult,
    ReleaseTitleParseResult,
    TMDBMatch,
)
from openlist_ani.domain.anime_release import LanguageType, VideoQuality


class FakeIdentityResolver:
    def __init__(self, resolved: TMDBMatch | None):
        self.resolved = resolved
        self.names = []
        self.closed = False

    async def resolve(self, anime_name: str):
        await asyncio.sleep(0)
        self.names.append(anime_name)
        return self.resolved

    async def close(self):
        await asyncio.sleep(0)
        self.closed = True


class FakeEpisodeValidator:
    def __init__(self, mapping: EpisodeMapping | None):
        self.mapping = mapping
        self.calls = []

    async def validate(self, *, tmdb_id, season, episode, anime_name, release_title):
        await asyncio.sleep(0)
        self.calls.append((tmdb_id, season, episode, anime_name, release_title))
        return self.mapping


def _parse_result(anime_name: str = "金牌得主") -> ParseResult:
    return ParseResult(
        success=True,
        release_title="[喵萌奶茶屋] 金牌得主 - 15 [1080p][简]",
        result=ReleaseTitleParseResult(
            anime_name=anime_name,
            season=1,
            episode=15,
            quality=VideoQuality.Q1080P,
            fansub="喵萌奶茶屋",
            languages=[LanguageType.CHS],
            version=1,
        ),
    )


async def test_validation_pipeline_corrects_identity_and_episode_mapping():
    identity = FakeIdentityResolver(
        TMDBMatch(tmdb_id=3822, anime_name="金牌得主", confidence="heuristic")
    )
    episodes = FakeEpisodeValidator(
        EpisodeMapping(season=2, episode=3, strategy="absolute")
    )
    pipeline = MetadataValidationPipeline(
        identity_resolver=identity,
        episode_validator=episodes,
    )
    parsed = _parse_result()

    validated = await pipeline.validate([parsed])

    assert identity.names == ["金牌得主"]
    assert episodes.calls == [
        (
            3822,
            1,
            15,
            "金牌得主",
            "[喵萌奶茶屋] 金牌得主 - 15 [1080p][简]",
        )
    ]
    assert parsed.success is True
    assert parsed.result is not None
    assert parsed.result.tmdb_id is None
    assert parsed.result.season == 1
    assert parsed.result.episode == 15
    assert validated[0].success is True
    assert validated[0].result is not None
    assert validated[0].result.tmdb_id == 3822
    assert validated[0].result.anime_name == "金牌得主"
    assert validated[0].result.season == 2
    assert validated[0].result.episode == 3


async def test_validation_pipeline_marks_result_failed_when_identity_is_missing():
    identity = FakeIdentityResolver(None)
    episodes = FakeEpisodeValidator(
        EpisodeMapping(season=1, episode=1, strategy="direct")
    )
    pipeline = MetadataValidationPipeline(
        identity_resolver=identity,
        episode_validator=episodes,
    )
    parsed = _parse_result("不存在的番")

    validated = await pipeline.validate([parsed])

    assert identity.names == ["不存在的番"]
    assert episodes.calls == []
    assert parsed.success is True
    assert parsed.result is not None
    assert parsed.error is None
    assert validated[0].success is False
    assert validated[0].result is None
    assert validated[0].error == "TMDB match not found for parsed anime name"


async def test_validation_pipeline_caches_duplicate_episode_validation():
    identity = FakeIdentityResolver(
        TMDBMatch(tmdb_id=3822, anime_name="金牌得主", confidence="heuristic")
    )
    episodes = FakeEpisodeValidator(
        EpisodeMapping(season=1, episode=5, strategy="direct")
    )
    pipeline = MetadataValidationPipeline(
        identity_resolver=identity,
        episode_validator=episodes,
    )
    first = _parse_result()
    second = _parse_result()
    first.result.episode = 5
    second.result.episode = 5

    validated = await pipeline.validate([first, second])

    assert identity.names == ["金牌得主"]
    assert len(episodes.calls) == 1
    assert first.result is not None
    assert second.result is not None
    assert first.result.tmdb_id is None
    assert second.result.tmdb_id is None
    assert validated[0].result is not None
    assert validated[1].result is not None
    assert validated[0].result.tmdb_id == 3822
    assert validated[1].result.tmdb_id == 3822
