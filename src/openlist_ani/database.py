from datetime import datetime
from pathlib import Path
from typing import Optional

import aiosqlite

from .core.website.model import AnimeResourceInfo

DB_FILE = Path.cwd() / "data/data.db"


class AniDatabase:
    def __init__(self, db_path: Path = DB_FILE):
        self.db_path = db_path

    async def init(self):
        """Initialize the database table if it doesn't exist."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS resources (
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
                """
            )
            await db.execute("CREATE INDEX IF NOT EXISTS idx_title ON resources(title)")
            await db.commit()

    async def is_downloaded(self, title: str) -> bool:
        """Check if a resource has been downloaded based on title."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT 1 FROM resources WHERE title = ?", (title,)
            )
            row = await cursor.fetchone()
            return row is not None

    async def add_resource(
        self,
        resource_info: AnimeResourceInfo,
        downloaded_at: Optional[datetime] = None,
    ):
        """Mark a resource as downloaded with complete information."""
        async with aiosqlite.connect(self.db_path) as db:
            try:
                languages_str = "".join(lang.value for lang in resource_info.languages)
                quality_str = (
                    resource_info.quality.value if resource_info.quality else None
                )

                await db.execute(
                    """
                    INSERT OR IGNORE INTO resources 
                    (url, title, anime_name, season, episode, fansub, quality, languages, version, downloaded_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        resource_info.download_url,
                        resource_info.title,
                        resource_info.anime_name,
                        resource_info.season,
                        resource_info.episode,
                        resource_info.fansub,
                        quality_str,
                        languages_str,
                        resource_info.version,
                        downloaded_at or datetime.now(),
                    ),
                )
                await db.commit()
            except aiosqlite.IntegrityError:
                pass

    async def execute_sql_query(self, sql: str, params: tuple = ()) -> list[dict]:
        """Execute a SELECT SQL query and return results.

        Args:
            sql: SQL query string (only SELECT queries allowed)
            params: Query parameters for safe parameter binding

        Returns:
            List of result dictionaries with column names as keys
        """
        try:
            # Security: Only allow SELECT queries
            sql_lower = sql.strip().lower()
            if not sql_lower.startswith("select"):
                from .logger import logger

                logger.error(f"Only SELECT queries are allowed, got: {sql}")
                return [{"error": "Only SELECT queries are allowed"}]

            # Block dangerous keywords
            dangerous_keywords = [
                "drop",
                "delete",
                "insert",
                "update",
                "alter",
                "create",
            ]
            if any(keyword in sql_lower for keyword in dangerous_keywords):
                from .logger import logger

                logger.error(f"Dangerous SQL keyword detected: {sql}")
                return [{"error": "Query contains dangerous keywords"}]

            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                cursor = await db.execute(sql, params)
                rows = await cursor.fetchall()

                # Convert Row objects to dictionaries
                results = [dict(row) for row in rows]
                return results

        except aiosqlite.Error as e:
            from .logger import logger

            logger.error(f"Error executing SQL query: {e}")
            return [{"error": str(e)}]


db = AniDatabase()
