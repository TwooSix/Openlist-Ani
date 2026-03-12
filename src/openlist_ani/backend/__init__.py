"""
Backend module providing FastAPI-based HTTP API for OpenList-Ani.

Exposes download management, RSS monitoring, and service control
endpoints for internal use by the assistant and other components.
"""

from .app import create_app
from .client import BackendClient
from .main import main
from .service import BackendService

__all__ = [
    "BackendClient",
    "BackendService",
    "create_app",
    "main",
]
