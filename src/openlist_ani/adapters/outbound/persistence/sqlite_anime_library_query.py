"""Read-only SQL query adapter for assistant library lookup."""

from __future__ import annotations

import re
from pathlib import Path

import aiosqlite

from openlist_ani.logger import logger

from .sqlite_anime_library_repository import DEFAULT_DB_PATH

_DANGEROUS_KEYWORD_RE = re.compile(
    r"\b(?:drop|delete|insert|update|alter|create)\b", re.IGNORECASE
)


class SqliteAnimeLibraryQueryAdapter:
    def __init__(self, db_path: Path = DEFAULT_DB_PATH):
        self.db_path = db_path

    async def execute_sql_query(self, sql: str, params: tuple = ()) -> list[dict]:
        """Execute a SELECT SQL query and return result dictionaries."""
        try:
            sql_lower = sql.strip().lower()
            if not sql_lower.startswith("select"):
                logger.error(f"Only SELECT queries are allowed, got: {sql}")
                return [{"error": "Only SELECT queries are allowed"}]

            if _DANGEROUS_KEYWORD_RE.search(sql_lower):
                logger.error(f"Dangerous SQL keyword detected: {sql}")
                return [{"error": "Query contains dangerous keywords"}]

            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                cursor = await db.execute(sql, params)
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

        except aiosqlite.Error as e:
            logger.error(f"Error executing SQL query: {e}")
            return [{"error": str(e)}]
