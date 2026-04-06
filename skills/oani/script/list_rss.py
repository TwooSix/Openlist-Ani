"""Show currently configured RSS feeds and priority settings."""

from openlist_ani.config import config


async def run(**kwargs) -> str:
    """List current RSS feed URLs and download priority configuration."""
    rss = config.rss

    lines = []

    # RSS URLs
    if rss.urls:
        lines.append(f"RSS Feeds ({len(rss.urls)}):")
        for url in rss.urls:
            lines.append(f"  - {url}")
    else:
        lines.append("No RSS feeds configured.")

    lines.append("")
    lines.append(f"Poll interval: {rss.interval_time}s")

    # Priority settings
    priority = rss.priority
    lines.append("")
    lines.append("Download Priority:")
    lines.append(f"  Field order: {', '.join(priority.field_order)}")
    if priority.fansub:
        lines.append(f"  Fansub priority: {', '.join(priority.fansub)}")
    if priority.quality:
        lines.append(f"  Quality priority: {', '.join(priority.quality)}")
    if priority.languages:
        lines.append(f"  Language priority: {', '.join(priority.languages)}")

    return "\n".join(lines)
