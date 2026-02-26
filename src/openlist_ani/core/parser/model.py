from typing import Any, List, Optional

from pydantic import BaseModel, Field

from ..website.model import LanguageType, VideoQuality


class ResourceTitleParseResult(BaseModel):
    anime_name: str = Field(..., description="The name of the anime")
    season: int = Field(
        ...,
        description="The season of the anime.Default to be 1. Note: If special episode, it should be 0",
    )
    episode: int = Field(
        ...,
        description="The episode number. It should be int. If float, it means special episode, default to be 1",
    )
    quality: Optional[VideoQuality] = Field(
        None, description="The quality of the video"
    )
    fansub: Optional[str] = Field(None, description="The fansub of the video")
    languages: List[LanguageType] = Field(
        ..., description="The subtitle language of the video"
    )
    version: int = Field(
        ..., description="The version of the video's subtitle, default to be 1"
    )
    tmdb_id: Optional[int] = Field(None, description="The TMDB ID of the anime found")


class ParseResult(BaseModel):
    success: bool
    result: Optional[ResourceTitleParseResult] = None
    error: Optional[str] = None
    resource_title: Optional[str] = None


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
        return sorted(
            [
                SeasonInfo(
                    **{
                        k: s.get(k, "")
                        for k in ("season_number", "episode_count", "name")
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
    name: Optional[str] = None
    original_name: Optional[str] = None
    first_air_date: Optional[str] = None
    overview: str = ""
    genre_ids: list[int] = []
    origin_country: list[str] = []


class EpisodeMapping(BaseModel):
    season: int
    episode: int
    strategy: str
