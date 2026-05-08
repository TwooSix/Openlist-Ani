"""Environment side effects derived from runtime configuration."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from openlist_ani.logger import logger

if TYPE_CHECKING:
    from .settings import ProxyConfig


class ProxyEnvironmentApplier:
    """Apply configured proxy settings to process environment variables."""

    def apply(self, proxy: "ProxyConfig") -> None:
        if proxy.http:
            os.environ["HTTP_PROXY"] = proxy.http
            logger.debug("HTTP proxy configured")

        if proxy.https:
            os.environ["HTTPS_PROXY"] = proxy.https
            logger.debug("HTTPS proxy configured")
