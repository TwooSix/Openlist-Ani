"""
FastAPI router defining all backend API endpoints.
"""

import os
import signal

from fastapi import APIRouter, HTTPException

from ..logger import logger
from .schema import (
    AddRSSRequest,
    AddRSSResponse,
    CreateDownloadRequest,
    CreateDownloadResponse,
    DownloadListResponse,
    DownloadTaskResponse,
    ParseRSSRequest,
    ParseRSSResponse,
    ResolveMagnetRequest,
    ResolveMagnetResponse,
    ResolveTorrentRequest,
    ResolveTorrentResponse,
    RestartResponse,
)
from .service import BackendService

router = APIRouter(prefix="/api")


@router.post("/restart")
async def restart_service() -> RestartResponse:
    """Restart the application by sending SIGHUP to self."""
    logger.info("Backend: Restart requested via API")
    os.kill(
        os.getpid(), signal.SIGHUP
    )  # noqa: S603 – intentional self-signal for graceful restart
    return RestartResponse(success=True, message="Restart signal sent")


@router.post("/rss")
async def add_rss_url(request: AddRSSRequest) -> AddRSSResponse:
    """Add a new RSS monitoring URL."""
    svc = BackendService.get()
    success, message, urls = svc.add_rss_url(request.url)
    return AddRSSResponse(success=success, message=message, urls=urls)


@router.post("/downloads")
async def create_download(request: CreateDownloadRequest) -> CreateDownloadResponse:
    """Create a new download task."""
    svc = BackendService.get()
    success, message, task = await svc.create_download(
        download_url=request.download_url,
        title=request.title,
    )
    return CreateDownloadResponse(success=success, message=message, task=task)


@router.get("/downloads")
async def list_downloads() -> DownloadListResponse:
    """Get all active download tasks."""
    svc = BackendService.get()
    tasks = svc.list_downloads()
    return DownloadListResponse(tasks=tasks, total=len(tasks))


@router.get(
    "/downloads/{task_id}",
    responses={404: {"description": "Task not found"}},
)
async def get_download(task_id: str) -> DownloadTaskResponse:
    """Get a specific download task's status and progress."""
    svc = BackendService.get()
    task = svc.get_download(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    return task


@router.post("/parse_rss")
async def parse_rss(request: ParseRSSRequest) -> ParseRSSResponse:
    """Parse an RSS feed and return its resource entries.

    Returns raw, un-enriched entries (title, download_url, fansub, etc.).
    The caller (assistant) decides which entries to enqueue via
    ``/api/downloads``.
    """
    svc = BackendService.get()
    return await svc.parse_rss(url=request.url, limit=request.limit)


@router.post("/resolve_magnet")
async def resolve_magnet(request: ResolveMagnetRequest) -> ResolveMagnetResponse:
    """Resolve a magnet URI to its real title and file list.

    Order of operations: ``dn=`` parameter → libtorrent metadata
    (DHT/peers, bounded by ``metadata_timeout``).  Detects collection
    releases via title-keyword matching so callers can refuse them.
    """
    svc = BackendService.get()
    return await svc.resolve_magnet(
        magnet=request.magnet, metadata_timeout=request.metadata_timeout
    )


@router.post("/resolve_torrent")
async def resolve_torrent(request: ResolveTorrentRequest) -> ResolveTorrentResponse:
    """Resolve a .torrent file URL to its real title and file list.

    Downloads the .torrent via HTTP(S) (size- and time-bounded), then
    parses the blob with libtorrent.  Mirrors ``/api/resolve_magnet``'s
    response shape so callers can share the same downstream pipeline.
    """
    svc = BackendService.get()
    return await svc.resolve_torrent(url=request.url)
