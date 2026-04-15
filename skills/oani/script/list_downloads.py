"""List all active/recent download tasks."""

from openlist_ani.backend.client import BackendClient
from openlist_ani.config import config


async def run(**kwargs) -> str:
    """List all download tasks with their status."""
    client = BackendClient(config.backend_url)
    try:
        data = await client.list_downloads()
    except Exception as e:
        return f"Error connecting to backend: {e}"
    finally:
        await client.close()

    tasks = data.get("tasks", [])
    if not tasks:
        return "No active download tasks."

    lines = [f"Total: {data.get('total', len(tasks))} tasks\n"]
    for t in tasks:
        state = t.get("state", "unknown")
        title = t.get("title", "untitled")
        anime = t.get("anime_name", "")
        ep = t.get("episode", "")
        created = t.get("created_at", "")
        error = t.get("error_message", "")

        line = f"- [{state}] {title}"
        if anime and ep:
            line += f" ({anime} E{ep})"
        if created:
            line += f"  created: {created}"
        if error:
            line += f"  error: {error}"
        lines.append(line)

    return "\n".join(lines)
