import asyncio

from openlist_ani.application.anime_library_ingestion.settings import (
    MetadataFilterSettings,
    PrioritySettings,
)
from openlist_ani.application.anime_library_ingestion.filters import (
    FilterChain,
    MetadataFilter,
    PriorityFilter,
    RegexTitleFilter,
    StrictRenameFilter,
)
from openlist_ani.domain.anime_release import AnimeRelease, LanguageType, VideoQuality


class FakeRepository:
    def __init__(self, records=None):
        self.records = records or []

    async def find_releases_by_episode(self, anime_name, season, episode):
        await asyncio.sleep(0)
        return self.records


def _release(
    title="[Sub_A] Test Anime - 01 [1080p][简]",
    fansub="Sub_A",
    quality=VideoQuality.Q1080P,
    languages=None,
    version=1,
):
    return AnimeRelease(
        title=title,
        download_url="magnet:?xt=urn:btih:test",
        anime_name="Test Anime",
        season=1,
        episode=1,
        fansub=fansub,
        quality=quality,
        languages=languages if languages is not None else [LanguageType.CHS],
        version=version,
    )


async def test_regex_filter_excludes_collection_titles():
    result = await RegexTitleFilter().apply(
        [
            _release(title="[Sub_A] Test Anime - 01 [1080p]"),
            _release(title="[Sub_A] Test Anime - 01-12 合集 [1080p]"),
        ]
    )

    assert [entry.title for entry in result] == ["[Sub_A] Test Anime - 01 [1080p]"]


async def test_filter_chain_reports_skipped_counts():
    chain = FilterChain([RegexTitleFilter()])

    result = await chain.apply(
        [
            _release(title="[Sub_A] Test Anime - 01 [1080p]"),
            _release(title="[Sub_A] Test Anime - 01-12 合集 [1080p]"),
        ]
    )

    assert [entry.title for entry in result] == ["[Sub_A] Test Anime - 01 [1080p]"]
    assert chain.report_summary() == "regex=1"


async def test_metadata_filter_excludes_configured_fansub():
    result = await MetadataFilter(
        MetadataFilterSettings(exclude_fansub=["BadSub"])
    ).apply([_release(fansub="BadSub"), _release(fansub="GoodSub")])

    assert [entry.fansub for entry in result] == ["GoodSub"]


async def test_priority_filter_skips_lower_priority_existing_episode():
    repo = FakeRepository(
        [
            {
                "fansub": "Sub_A",
                "quality": "1080p",
                "languages": "简",
                "version": 1,
            }
        ]
    )
    result = await PriorityFilter(
        PrioritySettings(fansub=["Sub_A", "Sub_B"], quality=[]),
        repo,
    ).apply([_release(fansub="Sub_B")])

    assert result == []


async def test_strict_filter_allows_version_upgrade_for_same_rename_stem():
    repo = FakeRepository(
        [
            {
                "fansub": "Sub_A",
                "quality": "1080p",
                "languages": "简",
                "version": 1,
            }
        ]
    )
    result = await StrictRenameFilter(
        "{anime_name} S{season:02d}E{episode:02d} {fansub} {quality} {languages}",
        repo,
    ).apply([_release(version=2)])

    assert [entry.version for entry in result] == [2]
