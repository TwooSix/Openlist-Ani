import asyncio
from typing import Any, Dict, List, Optional, Union

import aiohttp

from openlist_ani.logger import logger

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

    async def _request(self, method: str, url: str, **kwargs) -> Optional[dict]:
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

    async def _post(self, url: str, json: dict) -> Optional[dict]:
        """Helper to perform post request with aiohttp"""
        return await self._request("POST", url, json=json)

    async def _get(self, url: str, params: dict = None) -> Optional[dict]:
        """Helper to perform get request with aiohttp"""
        return await self._request("GET", url, params=params)

    async def check_health(self) -> bool:
        """
        Check whether the OpenList server is healthy / reachable.
        Uses the public settings endpoint which requires no authentication.
        :return: True if the server is reachable and responds correctly, False otherwise.
        """
        url = f"{self.base_url}/api/public/settings"
        data = await self._get(url)
        if data is not None and data.get("code") == 200:
            logger.debug("OpenList server health check passed")
            return True
        else:
            logger.error(f"OpenList server health check failed (url: {self.base_url})")
            return False

    async def add_offline_download(
        self, urls: List[str], path: str, tool: Union[str, OfflineDownloadTool]
    ) -> Optional[List[OpenlistTask]]:
        """
        Add offline download tasks.
        :param urls: List of download URLs (http/magnet/torrent)
        :param path: Destination path in OpenList
        :param tool: Offline download tool to use (OfflineDownloadTool or string)
        :return: List of created tasks on success, or None on error.
        """
        if not self.token:
            return None

        url = f"{self.base_url}/api/fs/add_offline_download"
        payload = {"urls": urls, "path": path, "tool": str(tool)}

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

    async def get_offline_download_tools(self) -> Optional[List[Dict[str, Any]]]:
        """
        Get available offline download tools (public).
        :return: List of tools or None on error.
        """
        url = f"{self.base_url}/api/public/offline_download_tools"
        data = await self._get(url)
        if data and data.get("code") == 200:
            return data.get("data")
        else:
            msg = data.get("message") if data else self.UNKNOWN_ERROR_MESSAGE
            logger.error(f"Failed to get offline download tools: {msg}")
            return None

    async def get_offline_download_done(self) -> Optional[List[OpenlistTask]]:
        """
        Get list of completed offline download tasks.
        Endpoint: GET /api/task/offline_download/done
        :return: List of OpenlistTask or None on error.
        """
        url = f"{self.base_url}/api/task/offline_download/done"
        data = await self._get(url)
        if data and data.get("code") == 200:
            tasks = data.get("data") or []
            return [OpenlistTask.from_dict(t) for t in tasks]
        else:
            msg = data.get("message") if data else self.UNKNOWN_ERROR_MESSAGE
            logger.error(f"Failed to fetch done offline download tasks: {msg}")
            return None

    async def get_offline_download_undone(self) -> Optional[List[OpenlistTask]]:
        """
        Get list of not-yet-completed offline download tasks.
        Endpoint: GET /api/task/offline_download/undone
        :return: List of OpenlistTask or None on error.
        """
        url = f"{self.base_url}/api/task/offline_download/undone"
        data = await self._get(url)
        if data and data.get("code") == 200:
            tasks = data.get("data") or []
            return [OpenlistTask.from_dict(t) for t in tasks]
        else:
            msg = data.get("message") if data else self.UNKNOWN_ERROR_MESSAGE
            logger.error(f"Failed to fetch undone offline download tasks: {msg}")
            return None

    async def list_files(self, path: str) -> Optional[List[FileEntry]]:
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
        """
        Rename a file.
        :param full_path: Full path to the file (e.g., /videos/movie.mp4)
        :param new_name: New filename (e.g., specific_name.mp4)
        """
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

    async def move_file(self, src_dir: str, dst_dir: str, filenames: List[str]) -> bool:
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

    async def remove_path(self, dir_path: str, names: List[str]) -> bool:
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
