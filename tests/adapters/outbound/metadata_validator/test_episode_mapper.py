from openlist_ani.adapters.outbound.metadata_validator.tmdb import (
    EpisodeMapper,
    MappingContext,
)
from openlist_ani.application.anime_library_ingestion.models import SeasonInfo


class FakeTMDBClient:
    async def get_season_episodes(self, tmdb_id: int, season_number: int):
        return []


async def test_episode_mapper_preserves_episode_when_tmdb_season_lags_release():
    mapper = EpisodeMapper()
    mapping = await mapper.map(
        MappingContext(
            tmdb_id=280758,
            fansub_season=1,
            fansub_episode=12,
            sorted_seasons=[
                SeasonInfo(season_number=0, episode_count=0, name="Specials"),
                SeasonInfo(season_number=1, episode_count=8, name="Season 1"),
            ],
            tmdb_client=FakeTMDBClient(),
            release_title="[ANi] 双人单身露营 - 12 [1080P][CHT]",
        )
    )

    assert mapping is not None
    assert mapping.season == 1
    assert mapping.episode == 12


async def test_episode_mapper_maps_absolute_numbering_inside_later_season():
    mapper = EpisodeMapper()
    mapping = await mapper.map(
        MappingContext(
            tmdb_id=237529,
            fansub_season=2,
            fansub_episode=21,
            sorted_seasons=[
                SeasonInfo(season_number=1, episode_count=13, name="第 1 季"),
                SeasonInfo(season_number=2, episode_count=9, name="第 2 季"),
            ],
            tmdb_client=FakeTMDBClient(),
            release_title="[绿茶字幕组] 金牌得主 第二季 / Medalist S2 [21]",
        )
    )

    assert mapping is not None
    assert mapping.season == 2
    assert mapping.episode == 8
