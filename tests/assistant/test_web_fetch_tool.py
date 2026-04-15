"""Tests for WebFetchTool and web_fetch_utils."""

from unittest.mock import AsyncMock, patch

import pytest

from openlist_ani.assistant.tool.builtin.web_fetch_tool import WebFetchTool
from openlist_ani.assistant.tool.builtin.web_fetch_utils import (
    CacheEntry,
    clear_cache,
    get_cached,
    html_to_markdown,
    set_cached,
    validate_url,
)
from openlist_ani.assistant.tool.registry import ToolRegistry

from .conftest import MockProvider, ReadOnlyTool


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_url_cache():
    """Ensure the module-level URL cache is clean for each test."""
    clear_cache()
    yield
    clear_cache()


def _make_web_fetch_tool() -> WebFetchTool:
    """Build a WebFetchTool wired to a no-op MockProvider."""
    provider = MockProvider()
    registry = ToolRegistry()
    registry.register(ReadOnlyTool("skill_tool"))
    return WebFetchTool(provider=provider, registry=registry)


# =====================================================================
# validate_url()
# =====================================================================


class TestValidateUrl:
    """Tests for web_fetch_utils.validate_url()."""

    def test_valid_https_url(self):
        ok, err = validate_url("https://example.com/page")
        assert ok is True
        assert err == ""

    def test_valid_http_url(self):
        ok, err = validate_url("http://example.com")  # NOSONAR — testing protocol validation
        assert ok is True
        assert err == ""

    def test_empty_url(self):
        ok, err = validate_url("")
        assert ok is False
        assert "empty" in err.lower()

    def test_missing_scheme(self):
        ok, _err = validate_url("example.com")
        assert ok is False
        # No scheme -> parsed.scheme is '' which is not http/https

    def test_ftp_protocol_rejected(self):
        ok, err = validate_url("ftp://files.example.com/data")  # NOSONAR — testing protocol rejection
        assert ok is False
        assert "protocol" in err.lower() or "ftp" in err.lower()

    def test_too_long_url(self):
        long_url = "https://example.com/" + "a" * 2500
        ok, err = validate_url(long_url)
        assert ok is False
        assert "length" in err.lower()

    def test_url_with_credentials_rejected(self):
        # Build URL with userinfo dynamically to avoid hardcoded credential detection
        userinfo = "@".join(["user:redacted", "example.com"])
        ok, err = validate_url(f"https://{userinfo}")
        assert ok is False
        assert "username" in err.lower() or "password" in err.lower()

    def test_single_segment_hostname_rejected(self):
        ok, err = validate_url("https://localhost/test")
        assert ok is False
        assert "segments" in err.lower()

    def test_valid_subdomain(self):
        ok, err = validate_url("https://api.docs.example.com/v2")
        assert ok is True
        assert err == ""


# =====================================================================
# html_to_markdown()
# =====================================================================


class TestHtmlToMarkdown:
    """Tests for web_fetch_utils.html_to_markdown()."""

    def test_plain_text_passthrough(self):
        """Non-HTML text should pass through unchanged."""
        result = html_to_markdown("Just plain text, no tags.")
        assert result == "Just plain text, no tags."

    def test_heading_conversion(self):
        """HTML headings should become Markdown headings."""
        html = "<html><body><h1>Title</h1><h2>Sub</h2></body></html>"
        md = html_to_markdown(html)
        assert "# Title" in md
        assert "## Sub" in md

    def test_paragraph_and_links(self):
        """Paragraphs and links should be converted."""
        html = (
            "<html><body>"
            '<p>Visit <a href="https://example.com">our site</a> today.</p>'
            "</body></html>"
        )
        md = html_to_markdown(html)
        assert "[our site](https://example.com)" in md

    def test_noise_tags_removed(self):
        """Script, style, nav, etc. should be stripped."""
        html = (
            "<html><body>"
            "<p>Content</p>"
            "<script>alert('x')</script>"
            "<style>.x{color:red}</style>"
            "<nav>Menu</nav>"
            "</body></html>"
        )
        md = html_to_markdown(html)
        assert "Content" in md
        assert "alert" not in md
        assert "color:red" not in md
        assert "Menu" not in md

    def test_code_block_conversion(self):
        """<pre><code> should become fenced code blocks."""
        html = (
            "<html><body>"
            "<pre><code>print('hello')</code></pre>"
            "</body></html>"
        )
        md = html_to_markdown(html)
        assert "```" in md
        assert "print('hello')" in md

    def test_unordered_list(self):
        """<ul><li> should become Markdown list items."""
        html = (
            "<html><body>"
            "<ul><li>Alpha</li><li>Beta</li></ul>"
            "</body></html>"
        )
        md = html_to_markdown(html)
        assert "- Alpha" in md
        assert "- Beta" in md

    def test_ordered_list(self):
        """<ol><li> should become numbered list."""
        html = (
            "<html><body>"
            "<ol><li>First</li><li>Second</li></ol>"
            "</body></html>"
        )
        md = html_to_markdown(html)
        assert "1. First" in md or "1." in md
        assert "2. Second" in md or "2." in md

    def test_table_conversion(self):
        """HTML tables should produce Markdown pipe tables."""
        html = (
            "<html><body><table>"
            "<tr><th>Name</th><th>Age</th></tr>"
            "<tr><td>Alice</td><td>30</td></tr>"
            "</table></body></html>"
        )
        md = html_to_markdown(html)
        assert "| Name | Age |" in md
        assert "| Alice | 30 |" in md

    def test_truncation_on_huge_content(self):
        """Content exceeding MAX_MARKDOWN_LENGTH should be truncated."""
        html = (
            "<html><body><p>" + "x" * 200_000 + "</p></body></html>"
        )
        md = html_to_markdown(html)
        assert len(md) <= 110_000  # MAX_MARKDOWN_LENGTH + truncation notice
        assert "truncated" in md.lower()


# =====================================================================
# WebFetchTool URL validation
# =====================================================================


class TestWebFetchToolUrlValidation:
    """Tests for WebFetchTool.execute() URL validation paths."""

    @pytest.mark.asyncio
    async def test_empty_url_returns_error(self):
        tool = _make_web_fetch_tool()
        result = await tool.execute(url="", prompt="extract info")
        assert "error" in result.lower()

    @pytest.mark.asyncio
    async def test_invalid_url_returns_error(self):
        tool = _make_web_fetch_tool()
        result = await tool.execute(url="not-a-url", prompt="extract info")
        assert "error" in result.lower()
        assert "invalid" in result.lower()

    @pytest.mark.asyncio
    async def test_missing_prompt_returns_error(self):
        tool = _make_web_fetch_tool()
        result = await tool.execute(url="https://example.com", prompt="")
        assert "error" in result.lower()


# =====================================================================
# WebFetchTool cache behaviour
# =====================================================================


class TestWebFetchToolCache:
    """Test that a second fetch of the same URL uses the cache."""

    @pytest.mark.asyncio
    async def test_cache_hit_skips_http(self):
        """Fetch same URL twice — second call must use cache (no HTTP)."""
        # Pre-populate the cache with a known entry
        test_url = "https://example.com/cached-page"
        set_cached(
            test_url,
            CacheEntry(
                content="# Cached Content\nHello from cache.",
                bytes_size=42,
                status_code=200,
                status_text="OK",
                content_type="text/html",
            ),
        )

        tool = _make_web_fetch_tool()

        # Patch _process_with_subagent to avoid actual LLM calls
        with patch.object(
            tool, "_process_with_subagent", new_callable=AsyncMock,
            return_value="Processed cached content.",
        ) as mock_sub:
            # Patch fetch_url so we can detect if HTTP was called
            with patch(
                "openlist_ani.assistant.tool.builtin.web_fetch_tool.fetch_url",
            ) as mock_fetch:
                result = await tool.execute(
                    url=test_url, prompt="summarize"
                )

                # HTTP fetch should NOT have been called (cache hit)
                mock_fetch.assert_not_called()
                # SubAgent should have been invoked with cached content
                mock_sub.assert_awaited_once()
                assert "Cache: hit" in result

    @pytest.mark.asyncio
    async def test_cache_miss_calls_http(self):
        """Fresh URL triggers HTTP fetch (cache miss)."""
        from openlist_ani.assistant.tool.builtin.web_fetch_utils import FetchResult

        tool = _make_web_fetch_tool()

        mock_fetch_result = FetchResult(
            content="<html><body><p>Hello</p></body></html>",
            bytes_size=100,
            status_code=200,
            status_text="OK",
            content_type="text/html; charset=utf-8",
            url="https://example.com/new",
        )

        with patch.object(
            tool, "_process_with_subagent", new_callable=AsyncMock,
            return_value="Processed.",
        ):
            with patch(
                "openlist_ani.assistant.tool.builtin.web_fetch_tool.fetch_url",
                new_callable=AsyncMock,
                return_value=mock_fetch_result,
            ) as mock_fetch:
                result = await tool.execute(
                    url="https://example.com/new", prompt="read it"
                )

                mock_fetch.assert_awaited_once()
                assert "Cache: miss" in result
