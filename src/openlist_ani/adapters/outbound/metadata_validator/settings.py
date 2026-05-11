from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MetadataValidatorSettings:
    tmdb_api_key: str
    tmdb_language: str
