"""
RSS resource filter package.

Provides a composable filter chain for RSS entry filtering:

- ``ResourceFilter`` — protocol that all filters implement.
- ``FilterChain`` — orchestrator that runs filters in sequence.
- ``RegexTitleFilter`` — regex-based title exclusion filtering.
- ``MetadataFilter`` — metadata-based blacklist filtering.
- ``PriorityFilter`` — priority-based filtering (fansub/quality/language).
- ``StrictRenameFilter`` — strict duplicate filtering based on rename output.
"""

from .base import EpisodeKey, FilterChain, ResourceFilter, group_by_episode
from .metadata import MetadataFilter
from .priority import PriorityFilter
from .regex import RegexTitleFilter
from .strict import StrictRenameFilter, compute_rename_stem

__all__ = [
    "EpisodeKey",
    "FilterChain",
    "MetadataFilter",
    "PriorityFilter",
    "RegexTitleFilter",
    "ResourceFilter",
    "StrictRenameFilter",
    "compute_rename_stem",
    "group_by_episode",
]
