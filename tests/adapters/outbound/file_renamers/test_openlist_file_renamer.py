from unittest.mock import AsyncMock

from openlist_ani.application.anime_library_ingestion.models import PipelineContext
from openlist_ani.adapters.outbound.file_renamers import OpenListFileRenamer
from openlist_ani.domain.anime_release import (
    AnimeRelease,
    LanguageType,
    ReleaseFilenamePlanner,
    VideoQuality,
)
from openlist_ani.domain.download_task.file_renamer import RenameRequest


def _release():
    return AnimeRelease(
        title="[ANi] 葬送的芙莉莲 - 01 [1080P][Baha][WEB-DL][AAC AVC][CHT][MP4]",
        download_url="magnet:?xt=urn:btih:abc123",
        anime_name="葬送的芙莉莲",
        season=1,
        episode=1,
        fansub="ANi",
        quality=VideoQuality.Q1080P,
        languages=[LanguageType.CHT],
    )


async def test_rename_only_touches_file_inside_target_directory():
    client = AsyncMock()
    client.list_files = AsyncMock(return_value=[])
    client.rename_file = AsyncMock(return_value=True)
    client.move_file = AsyncMock(return_value=True)
    file_renamer = OpenListFileRenamer(client, sleep=AsyncMock())
    release = _release()

    renamed = await file_renamer.rename(
        PipelineContext(
            workflow_id="workflow-1",
            payload=RenameRequest(
                release=release,
                directory_path="/anime/葬送的芙莉莲/Season 1",
                source_filename="raw episode [1080p].mkv",
                target_filename=ReleaseFilenamePlanner(
                    "{anime_name} S{season:02d}E{episode:02d} {quality} {languages}"
                ).filename(release, "raw episode [1080p].mkv"),
            ),
        )
    )

    assert renamed.filename == "葬送的芙莉莲 S01E01 1080p 繁.mkv"
    client.rename_file.assert_awaited_once_with(
        "/anime/葬送的芙莉莲/Season 1/raw episode [1080p].mkv",
        "葬送的芙莉莲 S01E01 1080p 繁.mkv",
    )
    client.move_file.assert_not_awaited()
