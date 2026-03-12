"""
Pydantic request/response models for backend API.
"""

from pydantic import BaseModel, Field


class AddRSSRequest(BaseModel):
    """Request body for adding an RSS monitoring URL."""

    url: str = Field(..., description="RSS feed URL to monitor")


class AddRSSResponse(BaseModel):
    """Response for adding an RSS URL."""

    success: bool
    message: str
    urls: list[str] = Field(default_factory=list, description="Current RSS URL list")


class CreateDownloadRequest(BaseModel):
    """Request body for creating a new download task."""

    download_url: str = Field(..., description="Download URL (magnet/torrent link)")
    title: str = Field(..., description="Resource title for identification")


class DownloadTaskResponse(BaseModel):
    """Response model for a single download task."""

    id: str
    title: str
    download_url: str
    state: str
    anime_name: str | None = None
    season: int | None = None
    episode: int | None = None
    fansub: str | None = None
    quality: str | None = None
    error_message: str | None = None
    retry_count: int = 0
    created_at: str
    updated_at: str
    started_at: str | None = None
    completed_at: str | None = None
    save_path: str = ""
    final_path: str | None = None


class DownloadListResponse(BaseModel):
    """Response model for listing all download tasks."""

    tasks: list[DownloadTaskResponse]
    total: int


class CreateDownloadResponse(BaseModel):
    """Response for creating a download task."""

    success: bool
    message: str
    task: DownloadTaskResponse | None = None


class RestartResponse(BaseModel):
    """Response for restart endpoint."""

    success: bool
    message: str


class ErrorResponse(BaseModel):
    """Generic error response."""

    detail: str
