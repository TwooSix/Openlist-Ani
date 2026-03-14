"""Execute SQL query script — query download history database."""

from __future__ import annotations

import json
import logging

from openlist_ani.database import db

logger = logging.getLogger(__name__)


async def run(sql: str, page: int = 1, page_size: int = 50) -> str:
    """Execute SQL query on download history database with pagination.

    Args:
        sql: SQL SELECT query.
        page: Page number (starting from 1).
        page_size: Number of results per page.

    Returns:
        JSON string with query results and pagination info.
    """
    logger.info(f"Executing SQL query (page {page}, size {page_size}): {sql}")

    try:
        page = max(1, page)
        page_size = min(max(1, page_size), 200)

        sanitized_sql = sql.strip()
        lowered = sanitized_sql.lstrip().lower()
        if not lowered.startswith("select"):
            return json.dumps(
                {"error": "Only SELECT queries are allowed for this tool."}
            )

        forbidden_tokens = [";", "--", "/*", "*/"]
        if any(token in sanitized_sql for token in forbidden_tokens):
            return json.dumps(
                {
                    "error": (
                        "Query contains disallowed characters (such as "
                        "semicolons or comments). Only a single, plain "
                        "SELECT query is allowed."
                    ),
                }
            )

        sql = sanitized_sql

        count_sql = f"SELECT COUNT(*) as total FROM ({sql}) AS sub"
        count_results = await db.execute_sql_query(count_sql)

        if count_results and "error" in count_results[0]:
            return json.dumps({"error": count_results[0]["error"]})

        total_count = count_results[0]["total"] if count_results else 0

        offset = (page - 1) * page_size
        paginated_sql = f"{sql} LIMIT {page_size} OFFSET {offset}"
        results = await db.execute_sql_query(paginated_sql)

        if results and "error" in results[0]:
            return json.dumps({"error": results[0]["error"]})

        total_pages = (total_count + page_size - 1) // page_size
        has_next = page < total_pages
        has_prev = page > 1

        start_index = offset + 1 if total_count > 0 else 0
        end_index = min(offset + page_size, total_count)
        if start_index > end_index:
            start_index = 0
            end_index = 0

        response: dict = {
            "results": results,
            "pagination": {
                "current_page": page,
                "page_size": page_size,
                "total_results": total_count,
                "total_pages": total_pages,
                "has_next_page": has_next,
                "has_previous_page": has_prev,
                "showing_results": f"{start_index}-{end_index} of {total_count}",
            },
        }

        if has_next:
            response["pagination"]["hint"] = (
                f"⚠️ There are more results! Call this function "
                f"again with page={page + 1} to see the next page."
            )

        return json.dumps(response, ensure_ascii=False, default=str)

    except Exception as e:
        logger.exception("Error executing SQL query")
        return json.dumps({"error": str(e)})


if __name__ == "__main__":
    import argparse
    import asyncio

    parser = argparse.ArgumentParser(
        description="Execute SQL query on download history"
    )
    parser.add_argument("--sql", type=str, required=True, help="SQL SELECT query")
    parser.add_argument("--page", type=int, default=1, help="Page number (default: 1)")
    parser.add_argument(
        "--page_size", type=int, default=50, help="Results per page (default: 50)"
    )
    args = parser.parse_args()

    async def _main() -> None:
        await db.init()
        result = await run(sql=args.sql, page=args.page, page_size=args.page_size)
        print(result)

    asyncio.run(_main())
