"""Create a new download task by magnet/torrent URL."""

from openlist_ani.assistant.skill_support.oani_backend_client import BackendClient
from openlist_ani.adapters.outbound.configuration import config
from openlist_ani.assistant.skill_support.library_query import (
    SqliteAnimeLibraryQueryAdapter,
)


async def _url_already_in_library(download_url: str) -> dict | None:
    """Return the existing library row for ``download_url`` if any.

    The ``resources`` table treats ``url`` as a unique source
    identity. We refuse duplicates here as a hard guard rather than relying
    on the LLM to remember the skill rules.
    """
    query_adapter = SqliteAnimeLibraryQueryAdapter()
    sql = (
        "SELECT title, anime_name, season, episode, downloaded_at "
        "FROM resources WHERE url = ? LIMIT 1"
    )
    rows = await query_adapter.execute_sql_query(sql, (download_url,))
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
            "in the library.\n"
            f"Existing row: title={existing.get('title')!r}, "
            f"anime_name={existing.get('anime_name')!r}, "
            f"season={existing.get('season')}, "
            f"episode={existing.get('episode')}, "
            f"downloaded_at={existing.get('downloaded_at')}.\n"
            "If the user really wants to re-download this release, "
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
