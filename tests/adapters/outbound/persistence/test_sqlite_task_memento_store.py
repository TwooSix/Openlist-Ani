import json
import sqlite3

from openlist_ani.adapters.outbound.persistence import (
    JsonTaskMementoStore,
    SqliteTaskMementoStore,
)
from openlist_ani.domain.anime_release import AnimeRelease, LanguageType, VideoQuality
from openlist_ani.domain.download_task.memento import TaskMemento
from openlist_ani.domain.download_task.task import DownloadState


def test_sqlite_memento_store_round_trip_real_release(tmp_path):
    db_path = tmp_path / "task_mementos.db"
    store = SqliteTaskMementoStore(db_path)
    task = _task("task-1", state=DownloadState.DOWNLOADED)
    task.pipeline.next_buffer = "rename"
    task.pipeline.downloaded_directory_path = "/anime/葬送的芙莉莲/Season 1"
    task.pipeline.downloaded_filename = "raw file.mp4"

    store.save(task)
    loaded = SqliteTaskMementoStore(db_path).load_all()

    assert len(loaded) == 1
    assert loaded[0].release.anime_name == "葬送的芙莉莲"
    assert loaded[0].release.quality == VideoQuality.Q1080P
    assert loaded[0].release.languages == [LanguageType.CHT]
    assert loaded[0].pipeline.next_buffer == "rename"


def test_sqlite_memento_store_persists_each_task_as_own_row(tmp_path):
    db_path = tmp_path / "task_mementos.db"
    store = SqliteTaskMementoStore(db_path)

    store.save(_task("task-1", state=DownloadState.PENDING))
    store.save(_task("task-2", state=DownloadState.DOWNLOADING))
    updated = _task("task-1", state=DownloadState.DOWNLOADED)
    updated.output_path = "/anime/out.mkv"
    store.save(updated)

    with sqlite3.connect(db_path) as db:
        rows = db.execute(
            "SELECT task_id, state, payload FROM task_mementos ORDER BY task_id"
        ).fetchall()

    assert [row[0] for row in rows] == ["task-1", "task-2"]
    assert [row[1] for row in rows] == ["downloaded", "downloading"]
    payload = json.loads(rows[0][2])
    assert payload["task_id"] == "task-1"
    assert "tasks" not in payload


def test_sqlite_memento_store_deletes_terminal_task_without_dropping_others(tmp_path):
    db_path = tmp_path / "task_mementos.db"
    store = SqliteTaskMementoStore(db_path)
    store.save(_task("active", state=DownloadState.DOWNLOADING))
    store.save(_task("done", state=DownloadState.DOWNLOADED))

    terminal = _task("done", state=DownloadState.COMPLETED)
    store.save(terminal)
    loaded = SqliteTaskMementoStore(db_path).load_all()

    assert [task.task_id for task in loaded] == ["active"]


def test_sqlite_memento_store_imports_legacy_json_when_empty(tmp_path):
    legacy_path = tmp_path / "task_mementos.json"
    db_path = tmp_path / "task_mementos.db"
    JsonTaskMementoStore(legacy_path).save(_task("legacy", DownloadState.DOWNLOADING))

    loaded = SqliteTaskMementoStore(
        db_path,
        legacy_json_path=legacy_path,
    ).load_all()

    assert [task.task_id for task in loaded] == ["legacy"]
    assert SqliteTaskMementoStore(db_path).load_all()[0].task_id == "legacy"


def test_sqlite_memento_store_imports_legacy_json_only_once(tmp_path):
    legacy_path = tmp_path / "task_mementos.json"
    db_path = tmp_path / "task_mementos.db"
    JsonTaskMementoStore(legacy_path).save(_task("legacy", DownloadState.DOWNLOADING))

    store = SqliteTaskMementoStore(db_path, legacy_json_path=legacy_path)
    assert [task.task_id for task in store.load_all()] == ["legacy"]

    store.delete("legacy")
    assert (
        SqliteTaskMementoStore(
            db_path,
            legacy_json_path=legacy_path,
        ).load_all()
        == []
    )


def _task(task_id: str, state: DownloadState) -> TaskMemento:
    return TaskMemento(
        task_id=task_id,
        state=state,
        release=AnimeRelease(
            title="[ANi] 葬送的芙莉莲 - 01 [1080P][Baha][WEB-DL][AAC AVC][CHT][MP4]",
            download_url=f"magnet:?xt=urn:btih:{task_id}",
            anime_name="葬送的芙莉莲",
            season=1,
            episode=1,
            fansub="ANi",
            quality=VideoQuality.Q1080P,
            languages=[LanguageType.CHT],
        ),
        base_path="/anime",
    )
