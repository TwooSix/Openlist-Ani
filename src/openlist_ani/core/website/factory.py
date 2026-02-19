from urllib.parse import urlparse

from .aniapi import AniapiWebsite
from .base import WebsiteBase
from .common import CommonRSSWebsite
from .mikan import MikanWebsite


class WebsiteFactory:
    """
    Factory class for creating appropriate website parsers based on URL.

    Usage:
        factory = WebsiteFactory()
        parser = factory.create("https://acg.rip/.xml")
        resources = await parser.fetch_feed("https://acg.rip/.xml")
    """

    # Domain to parser class mapping
    _DOMAIN_MAPPING = {
        # Mikan Project - requires special handling
        "mikanani.me": MikanWebsite,
        "mikanime.tv": MikanWebsite,
        # ANi API - uses direct MP4 links
        "ani.rip": AniapiWebsite,
        "api.ani.rip": AniapiWebsite,
    }

    def create(self, url: str) -> WebsiteBase:
        """
        Create appropriate website parser based on URL.

        Args:
            url: RSS feed URL

        Returns:
            Instance of appropriate WebsiteBase subclass

        Raises:
            ValueError: If URL cannot be parsed or domain is not supported

        Examples:
            >>> factory = WebsiteFactory()
            >>> parser = factory.create("https://mikanani.me/RSS/Bangumi")
            >>> type(parser).__name__
            'MikanWebsite'

            >>> parser = factory.create("https://acg.rip/.xml")
            >>> type(parser).__name__
            'CommonRSSWebsite'
        """
        if not url:
            raise ValueError("URL cannot be empty")

        try:
            parsed = urlparse(url)
            domain = parsed.netloc.lower()

            # Remove www. prefix if present
            if domain.startswith("www."):
                domain = domain[4:]

            if not domain:
                raise ValueError(f"Cannot extract domain from URL: {url}")

            # Try exact domain match first
            if domain in self._DOMAIN_MAPPING:
                parser_class = self._DOMAIN_MAPPING[domain]
                return parser_class()

            # Try subdomain matching
            for registered_domain, parser_class in self._DOMAIN_MAPPING.items():
                if domain.endswith(f".{registered_domain}"):
                    return parser_class()

            # Default to common RSS parser for unknown domains
            return CommonRSSWebsite()

        except Exception as e:
            raise ValueError(f"Failed to parse URL '{url}': {e}") from e
