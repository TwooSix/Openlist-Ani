from typing import List, Optional

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
        description="The episode number. It should be int. If float, it means special episode",
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
