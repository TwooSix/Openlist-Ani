"""
Bangumi client singleton and season helpers.

Provides lazy-initialized BangumiClient management and anime season
utility functions used across Bangumi-related tools.
"""

from __future__ import annotations

from datetime import datetime, timezone

from ....config import config
from ....core.bangumi.client import BangumiClient

# Shared client instance (lazy init)
_bangumi_client: BangumiClient | None = None


def _get_client() -> BangumiClient:
    """Get or create the shared BangumiClient singleton."""
    global _bangumi_client
    token = config.bangumi_token
    if _bangumi_client is None:
        _bangumi_client = BangumiClient(access_token=token)
    return _bangumi_client


async def close_bangumi_client() -> None:
    """Close shared Bangumi client session if initialized."""
    global _bangumi_client
    if _bangumi_client is None:
        return

    await _bangumi_client.close()
    _bangumi_client = None


def _get_current_season() -> tuple[int, int]:
    """Return (year, month) for the current anime season start.

    Season mapping: Jan-Mar -> Jan, Apr-Jun -> Apr, Jul-Sep -> Jul, Oct-Dec -> Oct.

    Returns:
        Tuple of (year, season_start_month).
    """
    now = datetime.now(tz=timezone.utc)
    month = now.month
    if month <= 3:
        return now.year, 1
    if month <= 6:
        return now.year, 4
    if month <= 9:
        return now.year, 7
    return now.year, 10


def _season_label(month: int) -> str:
    """Convert season start month to human-readable Chinese label.

    Args:
        month: Season start month (1, 4, 7, or 10).

    Returns:
        Season name in Chinese.
    """
    return {1: "冬季/1月番", 4: "春季/4月番", 7: "夏季/7月番", 10: "秋季/10月番"}.get(
        month, "未知"
    )
