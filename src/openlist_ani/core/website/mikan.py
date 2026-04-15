import re
from typing import Any

import aiohttp
from bs4 import BeautifulSoup

from ...logger import logger
from .base import WebsiteBase
from .model import AnimeResourceInfo


class MikanWebsite(WebsiteBase):
    _CN_NUM_MAP = {
        "一": 1,
        "二": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
    }

    _SEASON_TOKEN_RE = re.compile(
        r"(?:第)?([一二三四五六七八九十0-9]+)\s*(?:季|部分|部)"
    )

    def _parse_cn_number(self, text: str) -> int:
        """Parse Chinese or Arabic numeral string to integer.

        Examples:
            '1' -> 1
            '一' -> 1
            '十' -> 10
            '十二' -> 12
            '二十' -> 20
        """
        if text.isdigit():
            return int(text)

        if text == "十":
            return 10

        cn_map = self._CN_NUM_MAP

        if text.startswith("十"):
            return 10 + cn_map.get(text[1:], 0)

        if text.endswith("十"):
            return cn_map.get(text[:-1], 1) * 10

        if "十" in text:
            parts = text.split("十")
            return cn_map.get(parts[0], 0) * 10 + cn_map.get(parts[1], 0)

        return cn_map.get(text, 1)

    def _split_anime_name_and_season(self, anime_name: str) -> tuple[str, int]:
        """Split anime name into base title and season number.

        Extracts season information from Chinese season tokens like '第二季'.
        Returns the base name without season token and the season number.

        Args:
            anime_name: Full anime name potentially with season token

        Returns:
            Tuple of (base_name, season_number)

        Examples:
            '我推的孩子 第二季' -> ('我推的孩子', 2)
            '进击的巨人 第二部分' -> ('进击的巨人', 2)
            '我独自升级' -> ('我独自升级', 1)
        """

        raw = (anime_name or "").strip()
        if not raw:
            return "", 1

        # Normalize spaces to single space
        normalized = re.sub(r"[ \t\u3000]+", " ", raw).strip()

        matches = list(self._SEASON_TOKEN_RE.finditer(normalized))
        for match in reversed(matches):
            season = self._parse_cn_number(match.group(1).replace(" ", ""))
            left = normalized[: match.start()].rstrip()
            base_name = left.strip()
            return (base_name or normalized), season

        return normalized, 1

    def _get_download_url(self, entry) -> str | None:
        """Extract download link from enclosures or link attribute."""
        for enclosure in entry.get("enclosures", []):
            if enclosure.get("type") == "application/x-bittorrent":
                return enclosure.get("href")

        link = getattr(entry, "link", "")
        if link and (link.startswith("magnet:") or link.endswith(".torrent")):
            return link

        return None

    async def _fetch_metadata(
        self, session: aiohttp.ClientSession, url: str
    ) -> dict[str, Any]:
        """Fetch anime metadata from Mikan details page.

        Args:
            session: Active aiohttp session
            url: Mikan anime details page URL

        Returns:
            Dictionary with anime_name, season, and fansub
        """
        metadata = {"anime_name": None, "season": None, "fansub": None}

        try:
            async with session.get(url, timeout=30) as response:
                if response.status != 200:
                    return metadata

                content = await response.text()
                soup = BeautifulSoup(content, "lxml")

                if title_elem := soup.select_one("p.bangumi-title > a.w-other-c"):
                    anime_name = title_elem.get_text(strip=True)
                    base_name, season = self._split_anime_name_and_season(anime_name)
                    metadata["anime_name"] = base_name
                    metadata["season"] = season

                if (info_p := soup.select_one("p.bangumi-info")) and (
                    fansub_elem := info_p.select_one("a.magnet-link-wrap")
                ):
                    metadata["fansub"] = fansub_elem.get_text(strip=True)
        except Exception as e:
            logger.warning(f"Failed to fetch metadata from {url}: {e}")

        return metadata

    async def parse_entry(
        self, entry, session: aiohttp.ClientSession
    ) -> AnimeResourceInfo | None:
        title = getattr(entry, "title", None)
        download_url = self._get_download_url(entry)

        if not download_url or not title:
            return None

        entry_data = {
            "title": title,
            "download_url": download_url,
            "anime_name": None,
            "season": None,
            "fansub": None,
        }

        homepage_link = getattr(entry, "link", "")
        is_web_link = homepage_link and not (
            homepage_link.endswith(".torrent") or homepage_link.startswith("magnet:")
        )
        if not is_web_link:
            logger.error(f"Mikan entry missing valid homepage link: {title}")
            return None

        metadata = await self._fetch_metadata(session, homepage_link)

        entry_data.update(metadata)
        return AnimeResourceInfo(**entry_data)
