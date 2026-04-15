"""Create a new download task by magnet/torrent URL."""

from openlist_ani.backend.client import BackendClient
from openlist_ani.config import config


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
        return "Error: 'download_url' parameter is required (magnet link or torrent URL)."
    if not title:
        return "Error: 'title' parameter is required."

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
        return f"Download created successfully.\nTask ID: {task_id}\nTitle: {title}\n{msg}"
    else:
        return f"Failed to create download: {msg}"
