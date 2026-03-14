"""Search anime resources script — search on mikan/dmhy/acgrip websites."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from urllib.parse import quote_plus

from openlist_ani.core.website import WebsiteFactory
from openlist_ani.database import db

logger = logging.getLogger(__name__)


@dataclass
class _SearchResult:
    """Search result from anime resource websites."""

    title: str
    download_url: str
    is_downloaded: bool
    anime_name: str | None = None
    episode: int | None = None
    quality: str | None = None


async def run(anime_name: str, website: str) -> str:
    """Search anime resources on specified website.

    Args:
        anime_name: Anime name to search.
        website: Website to search on (mikan/dmhy/acgrip).

    Returns:
        Formatted search results.
    """
    logger.info(f"Searching {anime_name} on {website}")

    search_url = _build_search_url(anime_name, website)
    if not search_url:
        return f"❌ Unsupported website: {website}"

    try:
        entries = await _fetch_entries(search_url)
        if entries is None:
            return f"❌ Failed to create handler for {website}"

        logger.info(f"Found {len(entries)} results for {anime_name}")
        results = await _build_search_results(entries)
    except Exception:
        logger.exception(f"Error searching {anime_name} on {website}")
        return f"❌ Error searching {anime_name} on {website}"

    if not results:
        return f"❌ No resources found for {anime_name} on {website}"

    return _format_search_results(anime_name, website, results)


def _build_search_url(anime_name: str, website: str) -> str | None:
    """Build search RSS URL based on website type."""
    encoded_name = quote_plus(anime_name)
    website_search_urls = {
        "mikan": f"https://mikanani.me/RSS/Search?searchstr={encoded_name}",
        "dmhy": f"https://dmhy.org/topics/rss/rss.xml?keyword={encoded_name}",
        "acgrip": f"https://acg.rip/.xml?term={encoded_name}",
    }
    return website_search_urls.get(website)


async def _fetch_entries(search_url: str) -> list | None:
    """Fetch RSS entries through website handler."""
    factory = WebsiteFactory()
    handler = factory.create(search_url)
    if not handler:
        return None
    return await handler.fetch_feed(search_url)


async def _build_search_results(entries: list) -> list[_SearchResult]:
    """Convert raw feed entries to search result list with download status."""
    results: list[_SearchResult] = []
    for entry in entries:
        is_downloaded = await db.is_downloaded(entry.title)
        results.append(
            _SearchResult(
                title=entry.title,
                download_url=entry.download_url or "",
                is_downloaded=is_downloaded,
                anime_name=entry.anime_name,
                episode=entry.episode,
                quality=entry.quality.value if entry.quality else None,
            )
        )
    return results


def _format_resource_list(resources: list[_SearchResult], limit: int = 10) -> str:
    """Format a list of resources into numbered lines."""
    lines: list[str] = []
    for idx, resource in enumerate(resources[:limit], 1):
        lines.append(f"  {idx}. {resource.title}")
        if resource.quality:
            lines.append(f"     Quality: {resource.quality}")
    if len(resources) > limit:
        lines.append(f"  ...and {len(resources) - limit} more")
    return "\n".join(lines)


def _format_search_results(
    anime_name: str, website: str, results: list[_SearchResult]
) -> str:
    """Format search results for output."""
    downloaded = [r for r in results if r.is_downloaded]
    new_resources = [r for r in results if not r.is_downloaded]

    msg = f"🔍 Search Results for '{anime_name}' on {website}:\n\n"

    if downloaded:
        msg += f"📦 Already Downloaded ({len(downloaded)} resources):\n"
        msg += _format_resource_list(downloaded)
        msg += (
            "\n\n⚠️ These resources are already downloaded, "
            "do NOT download them again!\n\n"
        )

    if new_resources:
        msg += f"🆕 New Resources ({len(new_resources)} available):\n"
        for idx, resource in enumerate(new_resources[:10], 1):
            msg += f"  {idx}. {resource.title}\n"
            if resource.quality:
                msg += f"     Quality: {resource.quality}\n"
            msg += f"     Download URL: {resource.download_url}\n\n"
        if len(new_resources) > 10:
            msg += f"  ...and {len(new_resources) - 10} more\n"
    else:
        msg += "ℹ️ No new resources found (all have been downloaded)\n"

    return msg


if __name__ == "__main__":
    import argparse
    import asyncio

    parser = argparse.ArgumentParser(description="Search anime resources")
    parser.add_argument(
        "--anime_name", type=str, required=True, help="Anime name to search"
    )
    parser.add_argument(
        "--website",
        type=str,
        required=True,
        choices=["mikan", "dmhy", "acgrip"],
        help="Website to search on",
    )
    args = parser.parse_args()

    async def _main() -> None:
        await db.init()
        result = await run(anime_name=args.anime_name, website=args.website)
        print(result)

    asyncio.run(_main())
