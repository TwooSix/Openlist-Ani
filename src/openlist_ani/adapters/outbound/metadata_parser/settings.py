from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MetadataParserSettings:
    provider_type: str
    api_key: str
    base_url: str
    model: str
    tmdb_api_key: str
    tmdb_language: str
