"""Tests for AniDatabase.execute_sql_query keyword filtering."""

import pytest

from openlist_ani.database import AniDatabase


@pytest.fixture
async def test_db(tmp_path):
    """Create a temporary database for testing."""
    db = AniDatabase(db_path=tmp_path / "test.db")
    await db.init()
    return db


class TestExecuteSqlQueryKeywordFilter:
    """Verify that dangerous keyword detection uses word boundaries."""

    async def test_blocks_real_drop_statement(self, test_db: AniDatabase) -> None:
        result = await test_db.execute_sql_query("SELECT 1; DROP TABLE resources")
        assert result == [{"error": "Query contains dangerous keywords"}]

    async def test_blocks_real_delete_statement(self, test_db: AniDatabase) -> None:
        result = await test_db.execute_sql_query(
            "SELECT 1 FROM resources; DELETE FROM resources"
        )
        assert result == [{"error": "Query contains dangerous keywords"}]

    async def test_blocks_real_insert_statement(self, test_db: AniDatabase) -> None:
        result = await test_db.execute_sql_query(
            "SELECT 1; INSERT INTO resources(url, title) VALUES('a', 'b')"
        )
        assert result == [{"error": "Query contains dangerous keywords"}]

    async def test_blocks_real_update_statement(self, test_db: AniDatabase) -> None:
        result = await test_db.execute_sql_query(
            "SELECT 1; UPDATE resources SET title='x'"
        )
        assert result == [{"error": "Query contains dangerous keywords"}]

    async def test_allows_column_name_containing_create(
        self, test_db: AniDatabase
    ) -> None:
        """'created_at' contains 'create' as a substring — must NOT be blocked."""
        result = await test_db.execute_sql_query(
            "SELECT 1 AS created_at FROM resources"
        )
        # Should succeed (not trigger false positive)
        assert result != [{"error": "Query contains dangerous keywords"}]

    async def test_allows_column_name_containing_update(
        self, test_db: AniDatabase
    ) -> None:
        """'updated_at' contains 'update' as a substring — must NOT be blocked."""
        result = await test_db.execute_sql_query(
            "SELECT 1 AS updated_at FROM resources"
        )
        assert result != [{"error": "Query contains dangerous keywords"}]

    async def test_allows_value_containing_drop(
        self, test_db: AniDatabase
    ) -> None:
        """Words like 'raindrop' or 'backdrop' must NOT be blocked."""
        result = await test_db.execute_sql_query(
            "SELECT * FROM resources WHERE title LIKE '%raindrop%'"
        )
        assert result != [{"error": "Query contains dangerous keywords"}]

    async def test_rejects_non_select(self, test_db: AniDatabase) -> None:
        result = await test_db.execute_sql_query("INSERT INTO resources VALUES(1)")
        assert result == [{"error": "Only SELECT queries are allowed"}]

    async def test_valid_select_works(self, test_db: AniDatabase) -> None:
        result = await test_db.execute_sql_query("SELECT COUNT(*) as cnt FROM resources")
        assert len(result) == 1
        assert result[0]["cnt"] == 0
