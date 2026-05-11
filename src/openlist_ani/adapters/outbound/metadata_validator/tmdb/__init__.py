"""TMDB validation adapters."""

from .api import CachedTMDBClient, TMDBClient, close_tmdb_clients, get_tmdb_client
from .candidate import (
    CandidateSelector,
    HeuristicCandidateSelector,
    LLMCandidateSelector,
    LLMQueryExpander,
    QueryExpander,
    StaticQueryExpander,
)
from .episode_mapper import EpisodeMapper, MappingContext
from .episode_validator import TMDBEpisodeValidator
from .factory import create_tmdb_metadata_validator
from .resolver import TMDBAnimeIdentityResolver

__all__ = [
    "CachedTMDBClient",
    "CandidateSelector",
    "EpisodeMapper",
    "HeuristicCandidateSelector",
    "LLMCandidateSelector",
    "LLMQueryExpander",
    "MappingContext",
    "QueryExpander",
    "StaticQueryExpander",
    "TMDBAnimeIdentityResolver",
    "TMDBClient",
    "TMDBEpisodeValidator",
    "close_tmdb_clients",
    "create_tmdb_metadata_validator",
    "get_tmdb_client",
]
