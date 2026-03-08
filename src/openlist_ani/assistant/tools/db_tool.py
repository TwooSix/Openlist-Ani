"""
Execute SQL query tool.
"""

import json
from typing import Any

from ...database import db
from ...logger import logger
from .base import BaseTool


class ExecuteSqlTool(BaseTool):
    """Tool for executing SQL queries on download history database."""

    @property
    def name(self) -> str:
        return "execute_sql_query"

    @property
    def description(self) -> str:
        return (
            "Execute a SQL SELECT query on the download history database (table: resources). "
            "Use to check download history, find latest episodes, or query downloaded resources. "
            "Only SELECT queries allowed. Results are paginated — request next page if has_next_page is true. "
            "Do NOT add LIMIT/OFFSET. "
            "Examples: "
            "Latest: SELECT anime_name, MAX(episode), season FROM resources WHERE anime_name LIKE '%name%' GROUP BY anime_name ORDER BY downloaded_at DESC; "
            "Recent: SELECT anime_name, season, episode, fansub, downloaded_at FROM resources ORDER BY downloaded_at DESC"
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "sql": {
                    "type": "string",
                    "description": "SQL SELECT query to execute. Available table: resources (columns: id, url, title, anime_name, season, episode, fansub, quality, languages, version, downloaded_at). Do NOT include LIMIT or OFFSET in your query - pagination is handled automatically.",
                },
                "page": {
                    "type": "integer",
                    "description": "Page number (starting from 1). Default is 1.",
                    "default": 1,
                },
                "page_size": {
                    "type": "integer",
                    "description": "Number of results per page. Default is 50. Use larger values (up to 200) if you need more context, or smaller values for quick checks.",
                    "default": 50,
                },
            },
            "required": ["sql"],
        }

    async def execute(
        self, sql: str, page: int = 1, page_size: int = 50, **kwargs
    ) -> str:
        """Execute SQL query on download history database with pagination.

        Args:
            sql: SQL SELECT query
            page: Page number (starting from 1)
            page_size: Number of results per page

        Returns:
            JSON string with query results and pagination info
        """
        logger.info(
            f"Assistant: Executing SQL query (page {page}, size {page_size}): {sql}"
        )

        try:
            # Validate page parameters
            page = max(1, page)
            page_size = min(max(1, page_size), 200)  # Cap at 200 for safety

            # Enforce that only single SELECT queries without comments or semicolons are allowed
            sanitized_sql = sql.strip()
            lowered = sanitized_sql.lstrip().lower()
            if not lowered.startswith("select"):
                return json.dumps(
                    {"error": "Only SELECT queries are allowed for this tool."}
                )

            # Disallow semicolons and SQL comments to prevent multiple statements or obfuscation
            forbidden_tokens = [";", "--", "/*", "*/"]
            if any(token in sanitized_sql for token in forbidden_tokens):
                return json.dumps(
                    {
                        "error": "Query contains disallowed characters (such as semicolons or comments). "
                        "Only a single, plain SELECT query is allowed.",
                    }
                )

            sql = sanitized_sql

            # Get total count first (for pagination info)
            count_sql = f"SELECT COUNT(*) as total FROM ({sql}) AS sub"
            count_results = await db.execute_sql_query(count_sql)

            if count_results and "error" in count_results[0]:
                return json.dumps({"error": count_results[0]["error"]})

            total_count = count_results[0]["total"] if count_results else 0

            # Execute paginated query
            offset = (page - 1) * page_size
            paginated_sql = f"{sql} LIMIT {page_size} OFFSET {offset}"
            results = await db.execute_sql_query(paginated_sql)

            # Check for errors
            if results and "error" in results[0]:
                return json.dumps({"error": results[0]["error"]})

            # Calculate pagination info
            total_pages = (total_count + page_size - 1) // page_size  # Ceiling division
            has_next = page < total_pages
            has_prev = page > 1

            # Calculate display indices
            start_index = offset + 1 if total_count > 0 else 0
            end_index = min(offset + page_size, total_count)
            if start_index > end_index:
                start_index = 0
                end_index = 0

            response = {
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
                    f"⚠️ There are more results! Call this function again with page={page + 1} to see the next page."
                )

            return json.dumps(response, ensure_ascii=False, default=str)

        except Exception as e:
            logger.exception("Assistant: Error executing SQL query")
            return json.dumps({"error": str(e)})
