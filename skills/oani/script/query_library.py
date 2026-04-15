"""Query the anime resource database with SQL SELECT queries."""

from openlist_ani.database import db


async def run(
    sql: str = "",
    **kwargs,
) -> str:
    """Execute a SELECT query against the resources table.

    Args:
        sql: SQL SELECT query. The table schema is:
            resources(id, url, title, anime_name, season, episode,
                      fansub, quality, languages, version, downloaded_at)

    Examples:
        - "SELECT anime_name, season, episode FROM resources ORDER BY downloaded_at DESC LIMIT 10"
        - "SELECT DISTINCT anime_name FROM resources"
        - "SELECT COUNT(*) as total FROM resources WHERE anime_name = 'xxx'"
    """
    if not sql:
        return (
            "Error: 'sql' parameter is required.\n"
            "Table schema: resources(id, url, title, anime_name, season, episode, "
            "fansub, quality, languages, version, downloaded_at)\n"
            "Only SELECT queries are allowed."
        )

    await db.init()
    results = await db.execute_sql_query(sql)

    if not results:
        return "No results found."

    if len(results) == 1 and "error" in results[0]:
        return f"Error: {results[0]['error']}"

    # Format results as readable table
    lines = []
    headers = list(results[0].keys())
    lines.append(" | ".join(headers))
    lines.append("-" * len(lines[0]))
    for row in results:
        lines.append(" | ".join(str(row.get(h, "")) for h in headers))

    return "\n".join(lines)
