"""
FastAPI application factory with lifespan management.
"""

from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI

from ..logger import logger
from .router import router


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Manage application lifespan — startup and shutdown hooks."""
    logger.info("Backend API server starting up")
    yield
    logger.info("Backend API server shutting down")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application.

    Returns:
        Configured FastAPI instance.
    """
    app = FastAPI(
        title="OpenList-Ani Backend",
        description="Internal API for anime download and RSS management",
        version="1.0.0",
        lifespan=lifespan,
    )
    app.include_router(router)
    return app
