"""Tests for SqliteAnimeLibraryRepository."""

import sqlite3

from openlist_ani.adapters.outbound.persistence import (
    SqliteAnimeLibraryRepository,
)


async def test_repository_reuses_existing_resources_table(tmp_path):
    db_path = tmp_path / "data.db"
    title = "[ANi] Already Downloaded - 01 [1080P]"
    with sqlite3.connect(db_path) as db:
        db.execute("""
            CREATE TABLE resources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL,
                title TEXT UNIQUE NOT NULL,
                anime_name TEXT,
                season INTEGER,
                episode INTEGER,
                fansub TEXT,
                quality TEXT,
                languages TEXT,
                version INTEGER,
                downloaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        db.execute(
            "INSERT INTO resources (url, title) VALUES (?, ?)",
            ("magnet:?xt=urn:btih:test", title),
        )
        db.commit()

    repository = SqliteAnimeLibraryRepository(db_path=db_path)
    await repository.init()

    assert await repository.is_downloaded(title)
    with sqlite3.connect(db_path) as db:
        tables = {
            row[0]
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
    assert "resources" in tables
    assert "anime_library_entries" not in tables
