"""Read-only SQLite query helper local to the oani skill."""

from __future__ import annotations

import re
from pathlib import Path

import aiosqlite
from loguru import logger

DEFAULT_DB_PATH = Path.cwd() / "data/data.db"
_DANGEROUS_KEYWORD_RE = re.compile(
    r"\b(?:drop|delete|insert|update|alter|create)\b", re.IGNORECASE
)
_LEGACY_TABLE_RE = re.compile(r"\banime_library_entries\b", re.IGNORECASE)


class SqliteAnimeLibraryQueryAdapter:
    def __init__(self, db_path: Path = DEFAULT_DB_PATH):
        self.db_path = db_path

    async def execute_sql_query(self, sql: str, params: tuple = ()) -> list[dict]:
        """Execute a SELECT SQL query and return result dictionaries."""
        try:
            sql_normalized = _LEGACY_TABLE_RE.sub("resources", sql.strip())
            sql_lower = sql_normalized.lower()
            if not sql_lower.startswith("select"):
                logger.error(f"Only SELECT queries are allowed, got: {sql}")
                return [{"error": "Only SELECT queries are allowed"}]

            if _DANGEROUS_KEYWORD_RE.search(sql_lower):
                logger.error(f"Dangerous SQL keyword detected: {sql}")
                return [{"error": "Query contains dangerous keywords"}]

            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                cursor = await db.execute(sql_normalized, params)
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

        except aiosqlite.Error as e:
            logger.error(f"Error executing SQL query: {e}")
            return [{"error": str(e)}]
