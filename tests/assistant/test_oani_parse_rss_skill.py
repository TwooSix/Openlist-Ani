from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openlist_ani.builtin_skills.skills.oani.script import parse_rss


class FakeBackendClient:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url
        self.closed = False
        long_url = "magnet:?xt=urn:btih:" + ("a" * 80) + "&dn=episode"
        self.parse_rss = AsyncMock(
            return_value={
                "success": True,
                "total": 1,
                "entries": [
                    {
                        "index": 0,
                        "title": "Example Anime - 01",
                        "download_url": long_url,
                        "fansub": "ANi",
                        "quality": "1080p",
                        "languages": ["繁"],
                    }
                ],
            }
        )
        self.close = AsyncMock(side_effect=self._mark_closed)

    def _mark_closed(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_parse_rss_skill_keeps_full_download_url(monkeypatch):
    monkeypatch.setattr(parse_rss, "BackendClient", FakeBackendClient)
    monkeypatch.setattr(parse_rss, "config", SimpleNamespace(backend_url="https://api"))

    result = await parse_rss.run(url="https://example.test/rss")

    expected_url = "magnet:?xt=urn:btih:" + ("a" * 80) + "&dn=episode"
    assert expected_url in result
    assert expected_url[:60] + "…" not in result
