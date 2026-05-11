"""Factory helpers for TMDB-backed metadata validation."""

from __future__ import annotations

from openlist_ani.integrations.llm import LLMClient

from ..pipeline import MetadataValidationPipeline
from ..settings import MetadataValidatorSettings
from .api import TMDBClient, get_tmdb_client
from .candidate import (
    HeuristicCandidateSelector,
    LLMCandidateSelector,
    LLMQueryExpander,
    StaticQueryExpander,
)
from .episode_validator import TMDBEpisodeValidator
from .resolver import TMDBAnimeIdentityResolver


def create_tmdb_metadata_validator(
    settings: MetadataValidatorSettings,
    *,
    llm_client: LLMClient | None = None,
    tmdb_client: TMDBClient | None = None,
    max_concurrency: int = 8,
) -> MetadataValidationPipeline:
    client = tmdb_client or get_tmdb_client(
        api_key=settings.tmdb_api_key,
        language=settings.tmdb_language,
    )

    if llm_client is None:
        query_expander = StaticQueryExpander()
        candidate_selector = HeuristicCandidateSelector()
    else:
        query_expander = LLMQueryExpander(llm_client)
        candidate_selector = LLMCandidateSelector(llm_client)

    return MetadataValidationPipeline(
        identity_resolver=TMDBAnimeIdentityResolver(
            tmdb_client=client,
            query_expander=query_expander,
            candidate_selector=candidate_selector,
        ),
        episode_validator=TMDBEpisodeValidator(
            tmdb_client=client,
            llm_client=llm_client,
        ),
        max_concurrency=max_concurrency,
    )
