"""Create a new download task by magnet/torrent URL."""

from openlist_ani.backend.client import BackendClient
from openlist_ani.config import config
from openlist_ani.database import db


async def _url_already_in_library(download_url: str) -> dict | None:
    """Return the existing library row for ``download_url`` if any.

    The ``resources`` table uses ``url`` as the primary key, so inserting
    a duplicate corrupts subsequent lookups.  We refuse the download
    here as a hard guard rather than relying on the LLM to remember the
    skill rules.
    """
    await db.init()
    escaped = download_url.replace("'", "''")
    sql = (
        "SELECT title, anime_name, season, episode, downloaded_at "
        f"FROM resources WHERE url = '{escaped}' LIMIT 1"
    )
    rows = await db.execute_sql_query(sql)
    if not rows:
        return None
    first = rows[0]
    if "error" in first and len(first) == 1:
        # Surface query errors as "no match" — let the backend respond
        # so we don't block downloads on a broken pre-check.
        return None
    return first


async def run(
    download_url: str = "",
    title: str = "",
    **kwargs,
) -> str:
    """Submit a new download task.

    Args:
        download_url: Magnet link or torrent URL (required).
        title: Resource title for identification (required).
    """
    if not download_url:
        return (
            "Error: 'download_url' parameter is required (magnet link or torrent URL)."
        )
    if not title:
        return "Error: 'title' parameter is required."

    existing = await _url_already_in_library(download_url)
    if existing is not None:
        return (
            "Refusing to create download: this download_url is already "
            "in the library and `url` is the primary key — re-inserting "
            "would corrupt subsequent queries.\n"
            f"Existing row: title={existing.get('title')!r}, "
            f"anime_name={existing.get('anime_name')!r}, "
            f"season={existing.get('season')}, "
            f"episode={existing.get('episode')}, "
            f"downloaded_at={existing.get('downloaded_at')}.\n"
            "If the user really wants to re-download this resource, "
            "delete the existing row first or pick a different source URL."
        )

    client = BackendClient(config.backend_url)
    try:
        data = await client.create_download(download_url, title)
    except Exception as e:
        return f"Error creating download: {e}"
    finally:
        await client.close()

    success = data.get("success", False)
    msg = data.get("message", "")
    task = data.get("task", {})

    if success:
        task_id = task.get("id", "unknown")
        return (
            f"Download created successfully.\nTask ID: {task_id}\nTitle: {title}\n{msg}"
        )
    else:
        return f"Failed to create download: {msg}"
