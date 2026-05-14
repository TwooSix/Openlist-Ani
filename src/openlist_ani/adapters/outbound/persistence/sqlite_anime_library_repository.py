from datetime import datetime
from pathlib import Path
from typing import TypeVar

import aiosqlite

from openlist_ani.domain.anime_release import AnimeRelease

DEFAULT_DB_PATH = Path.cwd() / "data/data.db"
SQLITE_PARAMETER_CHUNK_SIZE = 900
SQLITE_EPISODE_KEY_CHUNK_SIZE = SQLITE_PARAMETER_CHUNK_SIZE // 3
T = TypeVar("T")


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

    async def find_existing_titles(self, candidate_titles: list[str]) -> set[str]:
        """Return candidate titles that already exist in the library."""
        unique_titles = _deduplicate(candidate_titles)
        if not unique_titles:
            return set()

        downloaded_titles: set[str] = set()
        async with aiosqlite.connect(self.db_path) as db:
            for chunk in _chunks(unique_titles, SQLITE_PARAMETER_CHUNK_SIZE):
                placeholders = ",".join("?" for _ in chunk)
                cursor = await db.execute(
                    f"SELECT title FROM resources WHERE title IN ({placeholders})",
                    tuple(chunk),
                )
                rows = await cursor.fetchall()
                downloaded_titles.update(row[0] for row in rows)
        return downloaded_titles

    async def find_releases_by_episodes(
        self,
        keys: list[tuple[str, int, int]],
    ) -> dict[tuple[str, int, int], list[dict]]:
        """Find ingested releases for multiple episode keys."""
        keys = _deduplicate(keys)
        records_by_key: dict[tuple[str, int, int], list[dict]] = {
            key: [] for key in keys
        }
        if not keys:
            return records_by_key

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            rows = []
            for chunk in _chunks(keys, SQLITE_EPISODE_KEY_CHUNK_SIZE):
                clauses = " OR ".join(
                    "(anime_name = ? AND season = ? AND episode = ?)" for _ in chunk
                )
                params = tuple(value for key in chunk for value in key)
                cursor = await db.execute(
                    "SELECT anime_name, season, episode, fansub, quality, "
                    "languages, version "
                    "FROM resources "
                    f"WHERE {clauses}",
                    params,
                )
                rows.extend(await cursor.fetchall())

        for row in rows:
            key = (row["anime_name"], row["season"], row["episode"])
            records_by_key.setdefault(key, []).append(
                {
                    "fansub": row["fansub"],
                    "quality": row["quality"],
                    "languages": row["languages"],
                    "version": row["version"],
                }
            )
        return records_by_key

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


def _deduplicate(items: list[T]) -> list[T]:
    return list(dict.fromkeys(items))


def _chunks(items: list[T], size: int):
    for index in range(0, len(items), size):
        yield items[index : index + size]
