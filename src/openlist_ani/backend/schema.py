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


# ── parse_rss ────────────────────────────────────────────────────────


class ParseRSSRequest(BaseModel):
    """Request body for parsing an RSS feed."""

    url: str = Field(..., description="RSS feed URL to parse")
    limit: int | None = Field(
        default=None,
        description="Maximum number of entries to return (None = all)",
    )


class ParseRSSEntry(BaseModel):
    """A single resource entry parsed from an RSS feed."""

    index: int = Field(..., description="0-based position in the feed")
    title: str
    download_url: str
    anime_name: str | None = None
    episode: int | None = None
    fansub: str | None = None
    quality: str | None = None
    languages: list[str] = Field(default_factory=list)


class ParseRSSResponse(BaseModel):
    """Response for parsing an RSS feed."""

    success: bool
    message: str
    total: int = 0
    entries: list[ParseRSSEntry] = Field(default_factory=list)


# ── resolve_magnet ───────────────────────────────────────────────────


class ResolveMagnetRequest(BaseModel):
    """Request body for resolving a magnet link to its title / files."""

    magnet: str = Field(..., description="Magnet URI (magnet:?xt=urn:btih:…)")
    metadata_timeout: int = Field(
        default=30,
        description="libtorrent metadata fetch budget, in seconds",
    )


class ResolveMagnetFile(BaseModel):
    """A single file inside the torrent's metadata."""

    name: str
    size: int = 0


class ResolveMagnetResponse(BaseModel):
    """Response for ``/api/resolve_magnet``.

    ``title`` may be ``None`` when both ``dn=`` and metadata fetch fail;
    callers must in that case ask the user for the resource title rather
    than fabricate one.
    """

    success: bool
    message: str
    title: str | None = None
    source: str | None = Field(
        default=None,
        description="Where the title came from: 'dn' | 'metadata' | None",
    )
    file_count: int | None = None
    files: list[ResolveMagnetFile] = Field(default_factory=list)
    is_collection: bool = False
    collection_reason: str | None = None


# ── resolve_torrent ──────────────────────────────────────────────────


class ResolveTorrentRequest(BaseModel):
    """Request body for resolving a .torrent file URL to its title / files."""

    url: str = Field(..., description="HTTP(S) URL to a .torrent file")


class ResolveTorrentResponse(BaseModel):
    """Response for ``/api/resolve_torrent``.

    Mirrors :class:`ResolveMagnetResponse` so callers can share the
    same downstream pipeline.
    """

    success: bool
    message: str
    title: str | None = None
    source: str | None = Field(
        default=None,
        description="Where the title came from: 'torrent_file' | None",
    )
    file_count: int | None = None
    files: list[ResolveMagnetFile] = Field(default_factory=list)
    is_collection: bool = False
    collection_reason: str | None = None
