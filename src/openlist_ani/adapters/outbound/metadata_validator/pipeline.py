"""Metadata validation pipeline."""

from __future__ import annotations

import asyncio
from typing import Protocol

from openlist_ani.application.anime_library_ingestion.models import (
    EpisodeMapping,
    ParseResult,
    TMDBMatch,
)
from openlist_ani.logger import logger

from .corrector import ParseResultCorrector
from .resolver import AnimeIdentityResolver, EpisodeValidator

_CACHE_MISS = object()


class _EpisodeValidationCache:
    def __init__(self) -> None:
        self._items: dict[tuple[int, int, int], EpisodeMapping | None] = {}

    def get(self, key: tuple[int, int, int]) -> EpisodeMapping | None | object:
        return self._items.get(key, _CACHE_MISS)

    def put(self, key: tuple[int, int, int], value: EpisodeMapping | None) -> None:
        self._items[key] = value


class MetadataValidator(Protocol):
    """Validation strategy for parsed release metadata."""

    async def validate(self, results: list[ParseResult]) -> list[ParseResult]:
        """Return validated and corrected release metadata."""
        ...

    async def close(self) -> None:
        """Release resources held by the validator."""
        ...


class MetadataValidationPipeline:
    """Validate extracted metadata against authoritative data.

    The pipeline is deliberately source-agnostic. TMDB specifics live in the
    resolver/validator adapters passed to the constructor.
    """

    def __init__(
        self,
        *,
        identity_resolver: AnimeIdentityResolver,
        episode_validator: EpisodeValidator,
        corrector: ParseResultCorrector | None = None,
        max_concurrency: int = 8,
    ) -> None:
        self._identity_resolver = identity_resolver
        self._episode_validator = episode_validator
        self._corrector = corrector or ParseResultCorrector()
        self._semaphore = asyncio.Semaphore(max_concurrency)

    async def close(self) -> None:
        await self._identity_resolver.close()

    async def validate(self, results: list[ParseResult]) -> list[ParseResult]:
        validated_results = [item.model_copy(deep=True) for item in results]
        successful_items = [
            item for item in validated_results if item.success and item.result
        ]
        if successful_items:
            names = {
                item.result.anime_name.strip()
                for item in successful_items
                if item.result and item.result.anime_name.strip()
            }
            resolved_map = await self._resolve_identities(names)
            episode_cache = _EpisodeValidationCache()

            for item in successful_items:
                await self._validate_item(item, resolved_map, episode_cache)

        return validated_results

    async def _resolve_identities(self, names: set[str]) -> dict[str, TMDBMatch]:
        async def resolve_one(name: str) -> tuple[str, TMDBMatch | None]:
            async with self._semaphore:
                return name, await self._identity_resolver.resolve(name)

        pairs = await asyncio.gather(*(resolve_one(name) for name in names))
        return {name: match for name, match in pairs if match is not None}

    async def _validate_item(
        self,
        item: ParseResult,
        resolved_map: dict[str, TMDBMatch],
        episode_cache: _EpisodeValidationCache,
    ) -> None:
        parse_result = item.result
        if parse_result is None:
            return

        original_name = parse_result.anime_name.strip()
        identity = resolved_map.get(original_name)
        if identity is None:
            logger.debug(
                f"Authoritative metadata unresolved for parsed anime '{original_name}'"
            )
            self._corrector.fail_identity(item)
            return

        self._corrector.apply_identity(item, identity)
        mapping = await self._validate_episode(item, episode_cache)
        if mapping is None:
            self._corrector.fail_episode(
                item,
                season=parse_result.season,
                episode=parse_result.episode,
            )
            return

        self._corrector.apply_episode_mapping(item, mapping)

    async def _validate_episode(
        self,
        item: ParseResult,
        episode_cache: _EpisodeValidationCache,
    ) -> EpisodeMapping | None:
        parse_result = item.result
        if parse_result is None or parse_result.tmdb_id is None:
            return None

        key = (parse_result.tmdb_id, parse_result.season, parse_result.episode)
        cached = episode_cache.get(key)
        if cached is not _CACHE_MISS:
            return cached  # type: ignore[return-value]

        mapping = await self._episode_validator.validate(
            tmdb_id=parse_result.tmdb_id,
            season=parse_result.season,
            episode=parse_result.episode,
            anime_name=parse_result.anime_name,
            release_title=item.release_title or "",
        )
        episode_cache.put(key, mapping)
        return mapping
