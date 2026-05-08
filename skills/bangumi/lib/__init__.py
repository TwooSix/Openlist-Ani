"""
Bangumi API integration module.

Provides an async client for interacting with the Bangumi API,
including calendar, subject details, and user collection data.
"""

from .client import BangumiClient
from .model import (
    BangumiBlog,
    BangumiCollection,
    BangumiRating,
    BangumiSubject,
    BangumiTag,
    BangumiTopic,
    BangumiUser,
    CalendarDay,
    CalendarItem,
    RelatedSubject,
    SlimSubject,
)

__all__ = [
    "BangumiBlog",
    "BangumiClient",
    "BangumiCollection",
    "BangumiRating",
    "BangumiSubject",
    "BangumiTag",
    "BangumiTopic",
    "BangumiUser",
    "CalendarDay",
    "CalendarItem",
    "RelatedSubject",
    "SlimSubject",
]
