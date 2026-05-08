from openlist_ani.domain.download_task.memento import TaskMemento
from openlist_ani.domain.download_task.task import DownloadState
from openlist_ani.adapters.outbound.persistence import JsonTaskMementoStore
from openlist_ani.domain.anime_release import AnimeRelease, LanguageType, VideoQuality


def test_memento_store_round_trip_real_release(tmp_path):
    store = JsonTaskMementoStore(tmp_path / "task_mementos.json")
    release = AnimeRelease(
        title="[ANi] 葬送的芙莉莲 - 01 [1080P][Baha][WEB-DL][AAC AVC][CHT][MP4]",
        download_url="magnet:?xt=urn:btih:abc123",
        anime_name="葬送的芙莉莲",
        season=1,
        episode=1,
        fansub="ANi",
        quality=VideoQuality.Q1080P,
        languages=[LanguageType.CHT],
    )
    task = TaskMemento(
        task_id="task-1",
        state=DownloadState.DOWNLOADED,
        release=release,
        base_path="/anime",
    )
    task.pipeline.next_buffer = "rename"
    task.pipeline.downloaded_directory_path = "/anime/葬送的芙莉莲/Season 1"
    task.pipeline.downloaded_filename = "raw file.mp4"

    store.save(task)
    loaded = JsonTaskMementoStore(tmp_path / "task_mementos.json").load_all()

    assert len(loaded) == 1
    assert loaded[0].release.anime_name == "葬送的芙莉莲"
    assert loaded[0].release.quality == VideoQuality.Q1080P
    assert loaded[0].release.languages == [LanguageType.CHT]
    assert loaded[0].pipeline.next_buffer == "rename"


def test_memento_store_does_not_persist_completed_tasks(tmp_path):
    store = JsonTaskMementoStore(tmp_path / "task_mementos.json")
    task = TaskMemento(
        task_id="task-completed",
        state=DownloadState.COMPLETED,
        release=AnimeRelease(
            title="[ANi] Test Anime - 01 [1080p]",
            download_url="magnet:?xt=urn:btih:completed",
        ),
        base_path="/anime",
    )

    store.save(task)
    loaded = JsonTaskMementoStore(tmp_path / "task_mementos.json").load_all()

    assert loaded == []
