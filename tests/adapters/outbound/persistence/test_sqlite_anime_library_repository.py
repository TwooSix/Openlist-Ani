"""Tests for SqliteAnimeLibraryQueryAdapter SQL keyword filtering."""

import sqlite3

import pytest

from openlist_ani.adapters.outbound.persistence import (
    SqliteAnimeLibraryQueryAdapter,
    SqliteAnimeLibraryRepository,
)


@pytest.fixture(scope="module")
async def test_db(tmp_path_factory):
    """Create a single temporary database shared across all tests in this module."""
    tmp = tmp_path_factory.mktemp("db")
    db_path = tmp / "test.db"
    repository = SqliteAnimeLibraryRepository(db_path=db_path)
    await repository.init()
    return SqliteAnimeLibraryQueryAdapter(db_path=db_path)


class TestExecuteSqlQueryKeywordFilter:
    """Verify that dangerous keyword detection uses word boundaries."""

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT 1; DROP TABLE resources",
            "SELECT 1 FROM resources; DELETE FROM resources",
            "SELECT 1; INSERT INTO resources(url, title) VALUES('a', 'b')",
            "SELECT 1; UPDATE resources SET title='x'",
        ],
    )
    async def test_blocks_dangerous_write_keywords(
        self, test_db: SqliteAnimeLibraryQueryAdapter, sql: str
    ) -> None:
        result = await test_db.execute_sql_query(sql)
        assert result == [{"error": "Query contains dangerous keywords"}]

    async def test_allows_column_name_containing_create(
        self, test_db: SqliteAnimeLibraryQueryAdapter
    ) -> None:
        """'created_at' contains 'create' as a substring — must NOT be blocked."""
        result = await test_db.execute_sql_query(
            "SELECT 1 AS created_at FROM resources"
        )
        # Should succeed (not trigger false positive)
        assert result != [{"error": "Query contains dangerous keywords"}]

    async def test_allows_column_name_containing_update(
        self, test_db: SqliteAnimeLibraryQueryAdapter
    ) -> None:
        """'updated_at' contains 'update' as a substring — must NOT be blocked."""
        result = await test_db.execute_sql_query(
            "SELECT 1 AS updated_at FROM resources"
        )
        assert result != [{"error": "Query contains dangerous keywords"}]

    async def test_allows_value_containing_drop(
        self, test_db: SqliteAnimeLibraryQueryAdapter
    ) -> None:
        """Words like 'raindrop' or 'backdrop' must NOT be blocked."""
        result = await test_db.execute_sql_query(
            "SELECT * FROM resources WHERE title LIKE '%raindrop%'"
        )
        assert result != [{"error": "Query contains dangerous keywords"}]

    async def test_rejects_non_select(
        self, test_db: SqliteAnimeLibraryQueryAdapter
    ) -> None:
        result = await test_db.execute_sql_query("INSERT INTO resources VALUES(1)")
        assert result == [{"error": "Only SELECT queries are allowed"}]

    async def test_valid_select_works(
        self, test_db: SqliteAnimeLibraryQueryAdapter
    ) -> None:
        result = await test_db.execute_sql_query(
            "SELECT COUNT(*) as cnt FROM resources"
        )
        assert len(result) == 1
        assert result[0]["cnt"] == 0


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
