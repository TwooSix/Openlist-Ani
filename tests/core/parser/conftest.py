import pytest

from openlist_ani.core.parser.parser import _parse_cache


@pytest.fixture(autouse=True)
def _clear_parse_cache():
    """Clear the parse metadata TTL cache between tests."""
    _parse_cache.clear()
