"""Resolve a .torrent file URL to its real title and detect collections.

Calls the backend ``/api/resolve_torrent`` endpoint, which downloads
the .torrent file (size- and time-bounded) and parses its metadata
with libtorrent.  Mirrors ``resolve_magnet``'s output so the assistant
can feed the result into the same downstream pipeline (library
duplicate check → user confirmation → create_download).
"""

from openlist_ani.backend.client import BackendClient
from openlist_ani.config import config


async def run(
    url: str = "",
    **kwargs,
) -> str:
    """Resolve a .torrent file URL to its real title.

    Args:
        url: ``http(s)://`` URL pointing at a .torrent file (required).
    """
    if not url:
        return (
            "Error: 'url' parameter is required (http(s):// link to a .torrent file)."
        )

    client = BackendClient(config.backend_url)
    try:
        data = await client.resolve_torrent(url)
    except Exception as e:
        return f"Error resolving torrent file: {e}"
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
            f"Failed to resolve torrent file: {msg}\n"
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
            "Next: check the library for duplicates via oani/query_library, "
            "confirm with the user (download URL + title), then call "
            "oani/create_download(download_url=<torrent-url>, title=<Title>). "
            "Pass the title verbatim — it is used by the backend to rename "
            "the file. Do NOT modify or fabricate it.",
        ]

    return "\n".join(lines)
