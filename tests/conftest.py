"""Shared test helpers and fixtures."""

from types import SimpleNamespace


def make_entry(
    title: str = "Test Anime - 01",
    download_url: str = "magnet:?xt=urn:btih:abc123",
    enclosures: list | None = None,
    link: str | None = None,
) -> SimpleNamespace:
    """Helper to build a feedparser-like entry object."""
    entry = SimpleNamespace(title=title, link=link or "")
    enc = enclosures or []

    def _get(key, default=None):
        if key == "enclosures":
            return enc
        return default

    entry.get = _get
    if not enclosures and download_url:
        enc.append({"href": download_url, "type": "application/x-bittorrent"})

    return entry
