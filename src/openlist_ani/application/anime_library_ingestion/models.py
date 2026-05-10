from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

from openlist_ani.domain.anime_release import AnimeRelease, LanguageType, VideoQuality
from openlist_ani.domain.download_task.downloader import DownloaderMemento

PayloadT = TypeVar("PayloadT")


@dataclass(frozen=True)
class PipelineContext(Generic[PayloadT]):
    """Correlates one payload with a single ingestion workflow instance."""

    workflow_id: str
    payload: PayloadT


@dataclass(frozen=True)
class DownloadCandidate:
    release: AnimeRelease
    base_path: str
    downloader_memento: DownloaderMemento | None = None


class ReleaseTitleParseResult(BaseModel):
    anime_name: str = Field(..., description="The name of the anime")
    season: int = Field(
        ...,
        description="The season of the anime.Default to be 1. Note: If special episode, it should be 0",
    )
    episode: int = Field(
        ...,
        description="The episode number. It should be int. If float, it means special episode, default to be 1",
    )
    quality: VideoQuality | None = Field(None, description="The quality of the video")
    fansub: str | None = Field(None, description="The fansub of the video")
    languages: list[LanguageType] = Field(
        ..., description="The subtitle language of the video"
    )
    version: int = Field(
        ..., description="The version of the video's subtitle, default to be 1"
    )
    tmdb_id: int | None = Field(None, description="The TMDB ID of the anime found")


class ParseResult(BaseModel):
    success: bool
    result: ReleaseTitleParseResult | None = None
    error: str | None = None
    release_title: str | None = None


class TMDBMatch(BaseModel):
    tmdb_id: int
    anime_name: str
    confidence: str = "unknown"


class SeasonInfo(BaseModel):
    season_number: int
    episode_count: int
    name: str = ""

    @staticmethod
    def from_raw_list(raw_seasons: list[dict[str, Any]]) -> list["SeasonInfo"]:
        int_field_defaults: dict[str, int | str] = {
            "season_number": 0,
            "episode_count": 0,
            "name": "",
        }
        return sorted(
            [
                SeasonInfo(
                    **{
                        k: s.get(k, default)
                        for k, default in int_field_defaults.items()
                    }
                )
                for s in raw_seasons
            ],
            key=lambda s: s.season_number,
        )


class CourGroup(BaseModel):
    cour_index: int
    start_episode: int
    end_episode: int
    air_date_start: str = ""
    air_date_end: str = ""


class TMDBCandidate(BaseModel):
    id: int
    name: str | None = None
    original_name: str | None = None
    first_air_date: str | None = None
    overview: str = ""
    genre_ids: list[int] = Field(default_factory=list)
    origin_country: list[str] = Field(default_factory=list)


class EpisodeMapping(BaseModel):
    season: int
    episode: int
    strategy: str
