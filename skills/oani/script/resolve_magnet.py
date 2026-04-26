"""Resolve a magnet link to its real title and detect collection releases.

Calls the backend ``/api/resolve_magnet`` endpoint.  The backend tries
the magnet's ``dn=`` parameter first, then falls back to libtorrent
metadata (DHT/peers, time-bounded).  The response also flags collection
torrents — the assistant must NOT enqueue those.
"""

from openlist_ani.backend.client import BackendClient
from openlist_ani.config import config


async def run(
    magnet: str = "",
    metadata_timeout: int = 30,
    **kwargs,
) -> str:
    """Resolve a magnet to its real title.

    Args:
        magnet: Magnet URI (required).
        metadata_timeout: Budget in seconds for the libtorrent metadata
            fetch.  Ignored when the magnet's ``dn=`` parameter is
            already usable.  Defaults to 30.
    """
    if not magnet:
        return "Error: 'magnet' parameter is required."

    client = BackendClient(config.backend_url)
    try:
        data = await client.resolve_magnet(magnet, metadata_timeout=metadata_timeout)
    except Exception as e:
        return f"Error resolving magnet: {e}"
    finally:
        await client.close()

    success = data.get("success", False)
    title = data.get("title")
    source = data.get("source") or "?"
    is_collection = data.get("is_collection", False)
    reason = data.get("collection_reason")
    file_count = data.get("file_count")
    msg = data.get("message", "")

    if not success:
        return (
            f"Failed to resolve magnet: {msg}\n"
            "Ask the user for the resource title — do NOT fabricate one."
        )

    lines = [
        f"Title: {title}",
        f"Source: {source}",
    ]
    if file_count is not None:
        lines.append(f"Files: {file_count}")

    if is_collection:
        lines += [
            "",
            f"COLLECTION DETECTED (matched: '{reason}').",
            "OpenList-Ani does not currently support downloading collection "
            "resources. Tell the user and DO NOT call oani/create_download.",
        ]
    else:
        lines += [
            "",
            "Next: confirm with the user (download URL + title), check the "
            "library for duplicates via oani/query_library, then call "
            "oani/create_download(download_url=<magnet>, title=<Title>). "
            "Pass the title verbatim — it is used by the backend to rename "
            "the file. Do NOT modify or fabricate it.",
        ]

    return "\n".join(lines)
