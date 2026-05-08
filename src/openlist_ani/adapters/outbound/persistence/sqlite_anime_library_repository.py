from datetime import datetime
from pathlib import Path

import aiosqlite

from openlist_ani.domain.anime_release import AnimeRelease

DEFAULT_DB_PATH = Path.cwd() / "data/data.db"


class SqliteAnimeLibraryRepository:
    def __init__(self, db_path: Path = DEFAULT_DB_PATH):
        self.db_path = db_path

    async def init(self) -> None:
        """Initialize the database table if it doesn't exist."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
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
                """)
            await db.execute("CREATE INDEX IF NOT EXISTS idx_title ON resources(title)")
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_anime_episode "
                "ON resources(anime_name, season, episode)"
            )
            await db.commit()

    async def is_downloaded(self, title: str) -> bool:
        """Check if a release has already been ingested based on title."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT 1 FROM resources WHERE title = ?", (title,)
            )
            row = await cursor.fetchone()
            return row is not None

    async def find_releases_by_episode(
        self,
        anime_name: str,
        season: int,
        episode: int,
    ) -> list[dict]:
        """Find all ingested releases for a specific episode.

        Args:
            anime_name: Anime series name.
            season: Season number.
            episode: Episode number.

        Returns:
            List of dicts with keys: fansub, quality, languages, version.
        """
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT fansub, quality, languages, version "
                "FROM resources "
                "WHERE anime_name = ? AND season = ? AND episode = ?",
                (anime_name, season, episode),
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def remove_release(self, title: str) -> None:
        """Remove a library entry by title."""
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                "DELETE FROM resources WHERE title = ?",
                (title,),
            )
            await conn.commit()

    async def add_release(
        self,
        release: AnimeRelease,
        downloaded_at: datetime | None = None,
    ) -> None:
        """Add a downloaded release to the anime library."""
        async with aiosqlite.connect(self.db_path) as db:
            try:
                languages_str = "".join(lang.value for lang in release.languages)
                quality_str = release.quality.value if release.quality else None

                await db.execute(
                    """
                    INSERT OR IGNORE INTO resources
                    (
                        url,
                        title,
                        anime_name,
                        season,
                        episode,
                        fansub,
                        quality,
                        languages,
                        version,
                        downloaded_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        release.download_url,
                        release.title,
                        release.anime_name,
                        release.season,
                        release.episode,
                        release.fansub,
                        quality_str,
                        languages_str,
                        release.version,
                        downloaded_at or datetime.now(),
                    ),
                )
                await db.commit()
            except aiosqlite.IntegrityError:
                pass
