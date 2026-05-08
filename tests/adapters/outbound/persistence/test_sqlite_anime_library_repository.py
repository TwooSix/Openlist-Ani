"""Tests for SqliteAnimeLibraryQueryAdapter SQL keyword filtering."""

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
            "SELECT 1; DROP TABLE anime_library_entries",
            "SELECT 1 FROM anime_library_entries; DELETE FROM anime_library_entries",
            "SELECT 1; INSERT INTO anime_library_entries(url, title) VALUES('a', 'b')",
            "SELECT 1; UPDATE anime_library_entries SET title='x'",
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
            "SELECT 1 AS created_at FROM anime_library_entries"
        )
        # Should succeed (not trigger false positive)
        assert result != [{"error": "Query contains dangerous keywords"}]

    async def test_allows_column_name_containing_update(
        self, test_db: SqliteAnimeLibraryQueryAdapter
    ) -> None:
        """'updated_at' contains 'update' as a substring — must NOT be blocked."""
        result = await test_db.execute_sql_query(
            "SELECT 1 AS updated_at FROM anime_library_entries"
        )
        assert result != [{"error": "Query contains dangerous keywords"}]

    async def test_allows_value_containing_drop(
        self, test_db: SqliteAnimeLibraryQueryAdapter
    ) -> None:
        """Words like 'raindrop' or 'backdrop' must NOT be blocked."""
        result = await test_db.execute_sql_query(
            "SELECT * FROM anime_library_entries WHERE title LIKE '%raindrop%'"
        )
        assert result != [{"error": "Query contains dangerous keywords"}]

    async def test_rejects_non_select(
        self, test_db: SqliteAnimeLibraryQueryAdapter
    ) -> None:
        result = await test_db.execute_sql_query(
            "INSERT INTO anime_library_entries VALUES(1)"
        )
        assert result == [{"error": "Only SELECT queries are allowed"}]

    async def test_valid_select_works(
        self, test_db: SqliteAnimeLibraryQueryAdapter
    ) -> None:
        result = await test_db.execute_sql_query(
            "SELECT COUNT(*) as cnt FROM anime_library_entries"
        )
        assert len(result) == 1
        assert result[0]["cnt"] == 0
