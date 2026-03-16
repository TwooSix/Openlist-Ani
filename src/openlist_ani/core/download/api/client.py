import asyncio
from typing import Any

import aiohttp

from ....logger import logger
from .model import FileEntry, OfflineDownloadTool, OpenlistTask


class OpenListClient:
    UNKNOWN_ERROR_MESSAGE = "Unknown error"

    def __init__(
        self,
        base_url: str,
        token: str = "",
        max_concurrent_requests: int = 4,
        request_timeout: float = 30.0,
        connect_timeout: float = 30.0,
        sock_read_timeout: float = 30.0,
        max_retries: int = 3,
        retry_backoff_seconds: float = 0.8,
    ):
        self.base_url = base_url.rstrip("/")
        self.token = token or ""
        self.headers = {
            "Content-Type": "application/json",
            "User-Agent": "OpenList-Ani/1.0",
        }
        if self.token:
            self.headers["Authorization"] = self.token

        self._semaphore = asyncio.Semaphore(max_concurrent_requests)
        self._timeout = aiohttp.ClientTimeout(
            total=request_timeout,
            connect=connect_timeout,
            sock_read=sock_read_timeout,
        )
        self._max_retries = max(1, int(max_retries))
        self._retry_backoff_seconds = float(retry_backoff_seconds)
        logger.info(
            f"OpenListClient initialized with max {max_concurrent_requests} concurrent requests"
        )

    async def _request(self, method: str, url: str, **kwargs) -> dict | None:
        """Perform an HTTP request with timeout + retries for transient network errors."""
        async with self._semaphore:
            last_exc: Exception | None = None
            for attempt in range(1, self._max_retries + 1):
                try:
                    async with aiohttp.ClientSession(
                        headers=self.headers,
                        timeout=self._timeout,
                        trust_env=True,
                    ) as session:
                        async with session.request(method, url, **kwargs) as response:
                            response.raise_for_status()
                            return await response.json()
                except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                    last_exc = e
                    if attempt < self._max_retries:
                        backoff = self._retry_backoff_seconds * (2 ** (attempt - 1))
                        logger.warning(
                            f"Request {method} {url} failed ({e}); retrying in {backoff:.1f}s "
                            f"({attempt}/{self._max_retries})"
                        )
                        await asyncio.sleep(backoff)
                        continue
                    break
                except Exception as e:
                    # Non-network errors (e.g. JSON decode) are not retried
                    last_exc = e
                    break

            logger.error(f"Request error to {url}: {last_exc}")
            return None

    async def _post(self, url: str, json: dict) -> dict | None:
        return await self._request("POST", url, json=json)

    async def _get(self, url: str, params: dict = None) -> dict | None:
        return await self._request("GET", url, params=params)

    async def is_healthy(self) -> bool:
        """Return True if the OpenList server is reachable and responds correctly."""
        url = f"{self.base_url}/api/public/settings"
        data = await self._get(url)
        if data is not None and data.get("code") == 200:
            logger.debug("OpenList server health check passed")
            return True
        else:
            logger.error(f"OpenList server health check failed (url: {self.base_url})")
            return False

    async def add_offline_download(
        self,
        urls: list[str],
        path: str,
        tool: str | OfflineDownloadTool,
        delete_policy: str = "delete_always",
    ) -> list[OpenlistTask] | None:
        """Add offline download tasks.

        Args:
            urls: Download URLs (http/magnet/torrent).
            path: Destination path in OpenList.
            tool: Offline download tool to use.
            delete_policy: Policy for source file deletion after transfer.

        Returns:
            List of created tasks on success, or None on error.
        """
        if not self.token:
            return None

        url = f"{self.base_url}/api/fs/add_offline_download"
        payload = {
            "urls": urls,
            "path": path,
            "tool": str(tool),
            "delete_policy": delete_policy,
        }

        data = await self._post(url, payload)
        if data and data.get("code") == 200:
            tasks = (data.get("data") or {}).get("tasks") or []
            task_objs = [OpenlistTask.from_dict(t) for t in tasks]
            logger.debug(f"Added offline download tasks for {urls} to {path}")
            return task_objs
        else:
            msg = data.get("message") if data else self.UNKNOWN_ERROR_MESSAGE
            logger.error(f"Failed to add offline download: {msg}")
            return None

    async def get_offline_download_tools(self) -> list[dict[str, Any]] | None:
        """Get available offline download tools (public endpoint)."""
        url = f"{self.base_url}/api/public/offline_download_tools"
        data = await self._get(url)
        if data and data.get("code") == 200:
            return data.get("data")
        else:
            msg = data.get("message") if data else self.UNKNOWN_ERROR_MESSAGE
            logger.error(f"Failed to get offline download tools: {msg}")
            return None

    async def get_offline_download_done(self) -> list[OpenlistTask] | None:
        """Get completed offline download tasks."""
        url = f"{self.base_url}/api/task/offline_download/done"
        data = await self._get(url)
        if data and data.get("code") == 200:
            tasks = data.get("data") or []
            return [OpenlistTask.from_dict(t) for t in tasks]
        else:
            msg = data.get("message") if data else self.UNKNOWN_ERROR_MESSAGE
            logger.error(f"Failed to fetch done offline download tasks: {msg}")
            return None

    async def get_offline_download_undone(self) -> list[OpenlistTask] | None:
        """Get in-progress offline download tasks."""
        url = f"{self.base_url}/api/task/offline_download/undone"
        data = await self._get(url)
        if data and data.get("code") == 200:
            tasks = data.get("data") or []
            return [OpenlistTask.from_dict(t) for t in tasks]
        else:
            msg = data.get("message") if data else self.UNKNOWN_ERROR_MESSAGE
            logger.error(f"Failed to fetch undone offline download tasks: {msg}")
            return None

    async def get_offline_download_transfer_done(
        self,
    ) -> list[OpenlistTask] | None:
        """Get completed offline download transfer tasks."""
        url = f"{self.base_url}/api/task/offline_download_transfer/done"
        data = await self._get(url)
        if data and data.get("code") == 200:
            tasks = data.get("data") or []
            return [OpenlistTask.from_dict(t) for t in tasks]
        else:
            msg = data.get("message") if data else self.UNKNOWN_ERROR_MESSAGE
            logger.error(f"Failed to fetch done offline download transfer tasks: {msg}")
            return None

    async def get_offline_download_transfer_undone(
        self,
    ) -> list[OpenlistTask] | None:
        """Get in-progress offline download transfer tasks."""
        url = f"{self.base_url}/api/task/offline_download_transfer/undone"
        data = await self._get(url)
        if data and data.get("code") == 200:
            tasks = data.get("data") or []
            return [OpenlistTask.from_dict(t) for t in tasks]
        else:
            msg = data.get("message") if data else self.UNKNOWN_ERROR_MESSAGE
            logger.error(
                f"Failed to fetch undone offline download transfer tasks: {msg}"
            )
            return None

    async def list_files(self, path: str) -> list[FileEntry] | None:
        """List files in a directory."""
        if not self.token:
            return None

        url = f"{self.base_url}/api/fs/list"
        payload = {
            "path": path,
            "password": "",
            "page": 1,
            "per_page": 0,
            "refresh": True,
        }

        data = await self._post(url, payload)
        if data and data.get("code") == 200:
            raw = data["data"].get("content") or []
            return [FileEntry.from_dict(r) for r in raw]
        else:
            return None

    async def rename_file(self, full_path: str, new_name: str) -> bool:
        """Rename a file at *full_path* to *new_name*."""
        if not self.token:
            return False

        url = f"{self.base_url}/api/fs/rename"
        payload = {"path": full_path, "name": new_name}

        data = await self._post(url, payload)
        if data and data.get("code") == 200:
            logger.debug(f"Renamed {full_path} to {new_name}")
            return True
        else:
            msg = data.get("message") if data else self.UNKNOWN_ERROR_MESSAGE
            logger.error(f"Failed to rename file: {msg}")
            return False

    async def mkdir(self, path: str) -> bool:
        """Create a directory."""
        if not self.token:
            return False

        url = f"{self.base_url}/api/fs/mkdir"
        payload = {"path": path}

        data = await self._post(url, payload)
        if data and data.get("code") == 200:
            logger.debug(f"Created directory: {path}")
            return True
        else:
            msg = data.get("message") if data else self.UNKNOWN_ERROR_MESSAGE
            logger.error(f"Failed to create directory: {msg}")
            return False

    async def move_file(self, src_dir: str, dst_dir: str, filenames: list[str]) -> bool:
        """Move files from source directory to destination directory."""
        if not self.token:
            return False

        url = f"{self.base_url}/api/fs/move"
        payload = {"src_dir": src_dir, "dst_dir": dst_dir, "names": filenames}

        data = await self._post(url, payload)
        if data and data.get("code") == 200:
            logger.debug(f"Moved {filenames} from {src_dir} to {dst_dir}")
            return True
        else:
            msg = data.get("message") if data else self.UNKNOWN_ERROR_MESSAGE
            logger.error(f"Failed to move files: {msg}")
            return False

    async def remove_path(self, dir_path: str, names: list[str]) -> bool:
        """Remove files or directories."""
        if not self.token:
            return False

        url = f"{self.base_url}/api/fs/remove"
        payload = {"dir": dir_path, "names": names}

        data = await self._post(url, payload)
        if data and data.get("code") == 200:
            logger.debug(f"Removed {names} from {dir_path}")
            return True
        else:
            msg = data.get("message") if data else self.UNKNOWN_ERROR_MESSAGE
            logger.error(f"Failed to remove path: {msg}")
            return False
