"""
WebFetchTool — builtin tool for fetching and analyzing web page content.

Fetches a URL, converts HTML to Markdown, and processes the content
through a SubAgent with the user's prompt in a clean context window.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from loguru import logger

from openlist_ani.assistant.core.subagent import (
    BUILTIN_AGENT_CONFIGS,
    run_subagent,
)
from openlist_ani.assistant.tool.base import BaseTool
from openlist_ani.assistant.tool.builtin.web_fetch_utils import (
    CacheEntry,
    FetchResult,
    MAX_CONTENT_FOR_SUBAGENT,
    MAX_MARKDOWN_LENGTH,
    RedirectInfo,
    fetch_url,
    get_cached,
    html_to_markdown,
    set_cached,
    validate_url,
)

if TYPE_CHECKING:
    from openlist_ani.assistant.provider.base import Provider
    from openlist_ani.assistant.tool.registry import ToolRegistry


class WebFetchTool(BaseTool):
    """Builtin tool for fetching and analyzing web page content.

    Flow:
    1. Validate URL
    2. Check TTL cache
    3. HTTP fetch via aiohttp (with redirect handling)
    4. Convert HTML → Markdown
    5. Cache the result
    6. Process content through SubAgent with user's prompt
    7. Return formatted result
    """

    def __init__(
        self,
        provider: Provider,
        registry: ToolRegistry,
    ) -> None:
        self._provider = provider
        self._registry = registry

    @property
    def name(self) -> str:
        return "web_fetch"

    @property
    def aliases(self) -> list[str]:
        return ["WebFetch"]

    @property
    def description(self) -> str:
        return (
            "Fetch web page content from a URL, convert HTML to Markdown, "
            "and process it with a sub-agent using the given prompt. "
            "Use for reading documentation, articles, API references, etc."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The URL to fetch content from.",
                },
                "prompt": {
                    "type": "string",
                    "description": (
                        "What information to extract or how to process "
                        "the fetched content."
                    ),
                },
            },
            "required": ["url", "prompt"],
        }

    @property
    def max_result_size_chars(self) -> int:
        return 100_000

    def is_concurrency_safe(self, tool_input: dict | None = None) -> bool:
        return True

    def is_read_only(self, tool_input: dict | None = None) -> bool:
        return True

    def get_activity_description(self, tool_input: dict | None = None) -> str | None:
        if tool_input:
            url = str(tool_input.get("url", ""))
            if url:
                try:
                    from urllib.parse import urlparse

                    hostname = urlparse(url).hostname or url
                    return f"Fetching {hostname}"
                except Exception:
                    pass
        return "Fetching web page"

    def prompt(self, tools: list[BaseTool] | None = None) -> str:
        return """# WebFetch Tool

Fetches web content and processes it with a focused sub-agent.

## Parameters
- url (required): The URL to fetch. Must be a valid http/https URL.
- prompt (required): What information to extract or how to process the content.

## Usage Notes
- HTML is automatically converted to Markdown
- Results are cached for 15 minutes
- Maximum page size: 10 MB
- Content is processed by a sub-agent with clean context
- If a URL redirects to a different domain, the redirect URL is returned
  for you to fetch manually
- Use this tool for documentation, articles, API references, etc.
- NOT suitable for authenticated pages (login-required content)
"""

    async def execute(self, **kwargs: object) -> str:
        url = str(kwargs.get("url", ""))
        prompt = str(kwargs.get("prompt", ""))

        if not url:
            return "Error: url is required."
        if not prompt:
            return "Error: prompt is required."

        # 1. Validate URL
        is_valid, error = validate_url(url)
        if not is_valid:
            return f"Error: Invalid URL — {error}"

        start_time = time.monotonic()

        # 2. Check cache
        cached = get_cached(url)
        if cached is not None:
            logger.info(f"WebFetch cache hit: {url}")
            elapsed = time.monotonic() - start_time
            return await self._process_and_format(
                content=cached.content,
                prompt=prompt,
                url=url,
                status_code=cached.status_code,
                status_text=cached.status_text,
                bytes_size=cached.bytes_size,
                cache_hit=True,
                elapsed_ms=elapsed * 1000,
            )

        # 3. Fetch URL
        logger.info(f"WebFetch fetching: {url}")
        result = await fetch_url(url)

        # 4. Handle redirect
        if isinstance(result, RedirectInfo):
            status_text = {
                301: "Moved Permanently",
                302: "Found",
                307: "Temporary Redirect",
                308: "Permanent Redirect",
            }.get(result.status_code, "Redirect")
            return (
                f"REDIRECT DETECTED: The URL redirects to a different host.\n\n"
                f"Original URL: {result.original_url}\n"
                f"Redirect URL: {result.redirect_url}\n"
                f"Status: {result.status_code} {status_text}\n\n"
                f"To fetch the content, call web_fetch again with:\n"
                f'- url: "{result.redirect_url}"\n'
                f'- prompt: "{prompt}"'
            )

        # 5. Check for HTTP errors
        assert isinstance(result, FetchResult)
        if result.status_code >= 400 or result.status_code == 0:
            return (
                f"Error fetching {url}: "
                f"HTTP {result.status_code} {result.status_text}"
            )

        # 6. Convert HTML → Markdown
        content_type = result.content_type.lower()
        if "text/html" in content_type:
            markdown_content = html_to_markdown(result.content)
        else:
            # Plain text, markdown, JSON, etc. — use as-is
            markdown_content = result.content[:MAX_MARKDOWN_LENGTH]

        # 7. Cache
        set_cached(
            url,
            CacheEntry(
                content=markdown_content,
                bytes_size=result.bytes_size,
                status_code=result.status_code,
                status_text=result.status_text,
                content_type=result.content_type,
            ),
        )

        # 8. Process with SubAgent
        elapsed = time.monotonic() - start_time
        return await self._process_and_format(
            content=markdown_content,
            prompt=prompt,
            url=url,
            status_code=result.status_code,
            status_text=result.status_text,
            bytes_size=result.bytes_size,
            cache_hit=False,
            elapsed_ms=elapsed * 1000,
        )

    async def _process_and_format(
        self,
        content: str,
        prompt: str,
        url: str,
        status_code: int,
        status_text: str,
        bytes_size: int,
        cache_hit: bool,
        elapsed_ms: float,
    ) -> str:
        """Process content through SubAgent and format the result."""
        try:
            processed = await self._process_with_subagent(content, prompt)
        except Exception as e:
            logger.warning(f"WebFetch SubAgent failed, returning raw content: {e}")
            # Fallback: return truncated raw content
            processed = content[:MAX_CONTENT_FOR_SUBAGENT]

        cache_label = "hit" if cache_hit else "miss"
        header = (
            f"[WebFetch] {url}\n"
            f"Status: {status_code} {status_text}\n"
            f"Content size: {bytes_size} bytes\n"
            f"Cache: {cache_label}\n"
            f"Duration: {elapsed_ms:.0f}ms\n"
            f"\n---\n"
        )
        return header + processed

    async def _process_with_subagent(self, content: str, prompt: str) -> str:
        """Process web content through a SubAgent with clean context."""
        config = BUILTIN_AGENT_CONFIGS["explore"]
        truncated = content[:MAX_CONTENT_FOR_SUBAGENT]
        subagent_prompt = (
            "You are analyzing web page content. "
            "Provide a clear, informative response.\n\n"
            f"Web page content:\n---\n{truncated}\n---\n\n"
            f"User request: {prompt}\n\n"
            "Respond based only on the web content above."
        )
        return await run_subagent(
            config=config,
            prompt=subagent_prompt,
            provider=self._provider,
            registry=self._registry,
        )
