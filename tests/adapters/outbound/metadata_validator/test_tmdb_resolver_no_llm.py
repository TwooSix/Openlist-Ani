import asyncio

from openlist_ani.adapters.outbound.metadata_validator.pipeline import (
    MetadataValidationPipeline,
)
from openlist_ani.adapters.outbound.metadata_validator.tmdb import (
    HeuristicCandidateSelector,
    StaticQueryExpander,
    TMDBAnimeIdentityResolver,
    TMDBEpisodeValidator,
)
from openlist_ani.application.anime_library_ingestion.models import (
    ParseResult,
    ReleaseTitleParseResult,
    TMDBCandidate,
)
from openlist_ani.domain.anime_release import LanguageType, VideoQuality


class FakeTMDBClient:
    def __init__(self):
        self.search_queries = []
        self.detail_ids = []
        self.closed = False

    async def search_tv_show(self, query: str):
        await asyncio.sleep(0)
        self.search_queries.append(query)
        return [
            {
                "id": 100,
                "name": "金牌厨师",
                "original_name": "Gold Chef",
                "first_air_date": "2025-01-01",
                "overview": "",
                "genre_ids": [],
                "origin_country": ["JP"],
            },
            {
                "id": 3822,
                "name": "金牌得主",
                "original_name": "メダリスト",
                "first_air_date": "2025-01-05",
                "overview": "",
                "genre_ids": [],
                "origin_country": ["JP"],
            },
        ]

    async def get_tv_show_details(self, tmdb_id: int):
        await asyncio.sleep(0)
        self.detail_ids.append(tmdb_id)
        return {
            "seasons": [
                {"season_number": 0, "episode_count": 0, "name": "Specials"},
                {"season_number": 1, "episode_count": 13, "name": "Season 1"},
            ]
        }

    async def get_season_episodes(self, tmdb_id: int, season_number: int):
        await asyncio.sleep(0)
        return []

    async def close(self):
        await asyncio.sleep(0)
        self.closed = True


async def test_tmdb_validation_pipeline_can_resolve_and_validate_without_llm():
    tmdb_client = FakeTMDBClient()
    validator = MetadataValidationPipeline(
        identity_resolver=TMDBAnimeIdentityResolver(
            tmdb_client=tmdb_client,
            query_expander=StaticQueryExpander(),
            candidate_selector=HeuristicCandidateSelector(),
        ),
        episode_validator=TMDBEpisodeValidator(tmdb_client=tmdb_client),
    )
    parsed = ParseResult(
        success=True,
        release_title="[喵萌奶茶屋] 金牌得主 - 05 [1080p][简]",
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

    validated = await validator.validate([parsed])

    assert "金牌得主" in tmdb_client.search_queries
    assert 3822 in tmdb_client.detail_ids
    assert parsed.result is not None
    assert parsed.result.tmdb_id is None
    assert validated[0].success is True
    assert validated[0].result is not None
    assert validated[0].result.tmdb_id == 3822
    assert validated[0].result.anime_name == "金牌得主"


async def test_heuristic_candidate_selector_prefers_authoritative_tmdb_name_for_alias():
    selector = HeuristicCandidateSelector()

    match = await selector.select(
        "判处勇者刑",
        [
            TMDBCandidate(
                id=249907,
                name="判处勇者刑：惩罚勇者9004队刑务记录",
                original_name="勇者刑に処す 懲罰勇者9004隊刑務記録",
            )
        ],
    )

    assert match is not None
    assert match.tmdb_id == 249907
    assert match.anime_name == "判处勇者刑：惩罚勇者9004队刑务记录"


async def test_static_query_expander_adds_non_llm_search_variants():
    expander = StaticQueryExpander()

    long_title_queries = await expander.expand(
        "安逸领主的愉快领地防卫～用生产系魔术将无名村改造成最强要塞都市～"
    )
    fate_queries = await expander.expand("Fatestrange Fake")
    diary_queries = await expander.expand("他国日记")

    assert "安逸领主的愉快领地防卫" in long_title_queries
    assert "Fate/strange Fake" in fate_queries
    assert "違国日記" in diary_queries
