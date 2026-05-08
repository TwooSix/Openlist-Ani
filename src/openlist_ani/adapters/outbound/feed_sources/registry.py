from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlparse

from .feed_source import FeedSource


FeedSourceFactoryFn = Callable[[], FeedSource]


@dataclass(frozen=True)
class DomainFeedSourceRule:
    domains: tuple[str, ...]
    factory: FeedSourceFactoryFn

    def can_handle(self, url: str) -> bool:
        domain = _domain_from_url(url)
        return any(
            domain == registered or domain.endswith(f".{registered}")
            for registered in self.domains
        )


class FeedSourceRegistry:
    """Registry for URL-specific feed source adapters."""

    def __init__(self, fallback: FeedSourceFactoryFn) -> None:
        self._rules: list[DomainFeedSourceRule] = []
        self._fallback = fallback

    def register_domains(
        self,
        domains: tuple[str, ...],
        factory: FeedSourceFactoryFn,
    ) -> None:
        self._rules.append(DomainFeedSourceRule(domains, factory))

    def create(self, url: str) -> FeedSource:
        if not url:
            raise ValueError("URL cannot be empty")

        # Validate early so the fallback does not hide malformed URLs.
        _domain_from_url(url)
        for rule in self._rules:
            if rule.can_handle(url):
                return rule.factory()

        return self._fallback()


def _domain_from_url(url: str) -> str:
    parsed = urlparse(url)
    domain = parsed.netloc.lower()
    if domain.startswith("www."):
        domain = domain[4:]
    if not domain:
        raise ValueError(f"Cannot extract domain from URL: {url}")
    return domain
