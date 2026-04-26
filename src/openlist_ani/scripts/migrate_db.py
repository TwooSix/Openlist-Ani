import argparse
import sqlite3
import sys
from pathlib import Path


def migrate():
    parser = argparse.ArgumentParser(
        description="Migrate data from old database to new format."
    )
    parser.add_argument(
        "--old-db",
        dest="old_db",
        help="Path to the old database file (default: data/subscribe_database.db in project root)",
    )
    parser.add_argument(
        "--new-db",
        dest="new_db",
        help="Path to the new database file (default: data/data.db in project root)",
    )
    args = parser.parse_args()

    # Paths
    # Determine root dir relative to this script:
    # src/openlist_ani/scripts/migrate_db.py -> src/openlist_ani/scripts -> src/openlist_ani -> src -> root
    script_dir = Path(__file__).resolve().parent
    root_dir = script_dir.parent.parent.parent

    # Fallback: if running from cwd (often the case with 'uv run'), check if we are already at root
    if not (root_dir / "pyproject.toml").exists():
        # Try cwd
        cwd = Path.cwd()
        if (cwd / "pyproject.toml").exists():
            root_dir = cwd

    old_db_path = (
        Path(args.old_db) if args.old_db else root_dir / "data/subscribe_database.db"
    )
    new_db_path = Path(args.new_db) if args.new_db else root_dir / "data/data.db"

    if not old_db_path.exists():
        print(f"Error: Old database not found at {old_db_path}")
        # Debug info
        print(f"Searched in Root Dir: {root_dir}")
        sys.exit(1)

    print(f"Found old database at {old_db_path}")

    # Ensure new db directory exists
    new_db_path.parent.mkdir(parents=True, exist_ok=True)

    # Connect to databases
    try:
        old_conn = sqlite3.connect(old_db_path)
        old_conn.row_factory = sqlite3.Row
        old_cursor = old_conn.cursor()

        # Check if table exists in old db
        old_cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='resource_data'"
        )
        if not old_cursor.fetchone():
            print("Error: Table 'resource_data' not found in old database.")
            sys.exit(1)

        new_conn = sqlite3.connect(new_db_path)
        new_cursor = new_conn.cursor()

        # Initialize new table if not exists (schema from openlist_ani.database)
        new_cursor.execute("""
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
        new_cursor.execute("CREATE INDEX IF NOT EXISTS idx_title ON resources(title)")
        new_conn.commit()

        # Read old data
        print("Reading data from old database...")
        old_cursor.execute("SELECT * FROM resource_data")
        rows = old_cursor.fetchall()

        print(f"Found {len(rows)} records to migrate.")

        success_count = 0
        skip_count = 0
        error_count = 0

        for row in rows:
            try:
                # Mapping
                # version: default to 1 as it's not in old db

                new_cursor.execute(
                    """
                    INSERT OR IGNORE INTO resources 
                    (url, title, anime_name, season, episode, fansub, quality, languages, version, downloaded_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["torrent_url"],
                        row["resource_title"],
                        row["anime_name"],
                        row["season"],
                        row["episode"],
                        row["fansub"],
                        row["quality"],
                        row["language"],
                        1,  # version
                        row["downloaded_date"],
                    ),
                )

                if new_cursor.rowcount > 0:
                    success_count += 1
                else:
                    skip_count += 1

            except Exception as e:
                print(f"Error migrating row {row['id']}: {e}")
                error_count += 1

        new_conn.commit()

        print("Migration complete.")
        print(f"Success: {success_count}")
        print(f"Skipped (Duplicate): {skip_count}")
        print(f"Errors: {error_count}")

    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        sys.exit(1)
    finally:
        if "old_conn" in locals():
            old_conn.close()
        if "new_conn" in locals():
            new_conn.close()


if __name__ == "__main__":
    migrate()
