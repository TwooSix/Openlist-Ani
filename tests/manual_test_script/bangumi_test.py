"""
Manual integration test script for Bangumi API.

Run with:
  BANGUMI_TOKEN=<your_token> uv run python -m tests.manual_test_script.bangumi_test

Requires BANGUMI_TOKEN environment variable or config.toml [bangumi] access_token.

Tests:
  1-5:  Basic API client tests (calendar, subject, collection, cache)
  6:    Full LLM E2E test (optional, requires OpenAI API key)
  7:    Fetch subject reviews
  8:    Post user collection (mark anime as wish)
"""

import asyncio
import os
import sys

# Ensure project root is on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from openlist_ani.core.bangumi.client import BangumiClient

# ---- Helper utilities ----


async def _reset_bt_client(bt_client_module) -> None:
    """Reset bangumi client singleton, closing the old one."""
    if bt_client_module._bangumi_client is not None:
        await bt_client_module._bangumi_client.close()
    bt_client_module._bangumi_client = None


def _print_header(title: str) -> None:
    """Print a formatted test section header."""
    print("=" * 60)
    print(title)
    print("=" * 60)


# ---- Individual test sections ----


async def _test_basic_api(client: BangumiClient) -> tuple:
    """Tests 1-5: basic API client tests (calendar, subject, collection, cache).

    Returns (user, calendar, subject_id, collections) for downstream tests.
    """
    _print_header("Test 1: Fetch current user (/v0/me)")
    user = await client.fetch_current_user()
    print(f"  User: {user.nickname} (@{user.username}), ID={user.id}\n")

    _print_header("Test 2: Fetch calendar (/calendar)")
    calendar = await client.fetch_calendar()
    total_items = sum(len(d.items) for d in calendar)
    print(f"  Days: {len(calendar)}, Total anime: {total_items}")
    for day in calendar:
        print(f"  {day.weekday.cn}: {len(day.items)} anime")
        for item in day.items[:3]:
            print(f"    - [{item.id}] {item.display_name} (score={item.rating.score})")
        if len(day.items) > 3:
            print(f"    ... and {len(day.items) - 3} more")
    print()

    subject_id = calendar[0].items[0].id if calendar and calendar[0].items else 425998
    _print_header(f"Test 3: Fetch subject detail (/v0/subjects/{subject_id})")
    subject = await client.fetch_subject(subject_id)
    print(f"  Name: {subject.display_name}")
    print(f"  Date: {subject.date}, Platform: {subject.platform}")
    print(f"  Rating: {subject.rating.score} (rank #{subject.rating.rank})")
    print(f"  Tags: {', '.join(t.name for t in subject.tags[:10])}")
    print(f"  Summary: {subject.summary[:200]}...\n")

    _print_header("Test 4: Fetch user collections")
    collections = await client.fetch_user_collections(subject_type=2)
    print(f"  Total entries: {len(collections)}")
    for entry in collections[:5]:
        name = ""
        if entry.subject:
            name = entry.subject.name_cn or entry.subject.name
        name = name or f"Subject#{entry.subject_id}"
        print(
            f"    - [{entry.subject_id}] {name} (rate={entry.rate}, {entry.collection_type_label})"
        )
    if len(collections) > 5:
        print(f"    ... and {len(collections) - 5} more")
    print()

    _print_header("Test 5: Cache verification")
    calendar2 = await client.fetch_calendar()
    print(f"  Calendar cached: {calendar is calendar2}")
    subject2 = await client.fetch_subject(subject_id)
    print(f"  Subject cached: {subject is subject2}")
    user2 = await client.fetch_current_user()
    print(f"  User cached: {user is user2}\n")

    return user, calendar, subject_id, collections


async def _test_llm_e2e() -> None:
    """Test 6: Full LLM E2E test (optional, requires OpenAI API key)."""
    _print_header("Test 6: Full LLM E2E test (optional)")

    from openlist_ani.config import config as app_config

    if not app_config.llm.openai_api_key:
        print("  \u26a0\ufe0f  Skipped: OpenAI API key not configured\n")
        return

    from unittest.mock import MagicMock

    import openlist_ani.assistant.skills as skills_mod
    from openlist_ani.assistant.assistant import AniAssistant
    from openlist_ani.core.download import DownloadManager

    skills_mod._default_registry = None

    dm = MagicMock(spec=DownloadManager)
    assistant = AniAssistant(download_manager=dm)
    response = await assistant.process_message("推荐一部新番")

    assert response, "LLM returned empty response"
    assert not response.startswith("\u274c"), f"LLM error: {response[:100]}"

    print(f"  \u2705 LLM response ({len(response)} chars):")
    for line in response[:500].split("\n"):
        print(f"    {line}")
    if len(response) > 500:
        print("    ...")
    print()


async def _test_reviews(
    client: BangumiClient, bt_client_module, subject_id: int
) -> None:
    """Test 7: Fetch subject reviews."""
    _print_header(f"Test 7: Fetch subject reviews (subject {subject_id})")

    topics, blogs = await client.fetch_subject_reviews(subject_id)
    print(f"  Topics: {len(topics)}, Blogs: {len(blogs)}")
    for t in topics[:5]:
        print(f"    Topic: {t.title} (by {t.user_nickname}, {t.replies} replies)")
    for b in blogs[:5]:
        summary_preview = b.summary[:80] if b.summary else "N/A"
        print(f"    Blog: {b.title} (by {b.user_nickname}, {b.replies} replies)")
        print(f"      {summary_preview}...")

    from openlist_ani.assistant.skills.bangumi.script.reviews import BangumiReviewsTool

    await _reset_bt_client(bt_client_module)
    reviews_tool = BangumiReviewsTool()
    reviews_result = await reviews_tool.execute(subject_id=subject_id)
    assert "Reviews & Discussions" in reviews_result
    print(f"  \u2705 Tool output: {len(reviews_result)} chars\n")


async def _test_collect_tool() -> None:
    """Test 8: BangumiCollectTool validation tests."""
    _print_header("Test 8: BangumiCollectTool validation tests")

    from openlist_ani.assistant.skills.bangumi.script.collect import BangumiCollectTool

    collect_tool = BangumiCollectTool()

    bad_type = await collect_tool.execute(subject_id=100, collection_type=99)
    assert "Invalid collection type" in bad_type
    print("  \u2705 Invalid type rejected correctly")

    bad_ep = await collect_tool.execute(subject_id=100, collection_type=2, ep_status=-1)
    assert "Invalid ep_status" in bad_ep
    print("  \u2705 Invalid ep_status rejected correctly")

    assert collect_tool.name == "update_bangumi_collection"
    assert "subject_id" in collect_tool.parameters["required"]
    assert "collection_type" in collect_tool.parameters["required"]
    props = collect_tool.parameters["properties"]
    assert "ep_status" in props
    assert "rate" not in props
    assert "comment" not in props
    assert "tags" not in props
    print("  \u2705 Tool definition correct (no rate/comment/tags)\n")


async def main() -> None:
    """Run manual API tests against real Bangumi API."""
    token = os.environ.get("BANGUMI_TOKEN", "")
    if not token:
        print("ERROR: Set BANGUMI_TOKEN environment variable")
        sys.exit(1)

    from openlist_ani.assistant.skills.bangumi.script.helper import (
        client as bt_client,
    )

    client = BangumiClient(access_token=token)
    try:
        _user, _calendar, subject_id, _collections = await _test_basic_api(client)

        os.environ["BANGUMI_TOKEN"] = token
        await _reset_bt_client(bt_client)

        await _test_llm_e2e()
        await _test_reviews(client, bt_client, subject_id)
        await _test_collect_tool()

        print("\u2705 All manual tests passed!")
    except Exception as e:
        print(f"\u274c Test failed: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
    finally:
        await client.close()
        if bt_client._bangumi_client is not None:
            await bt_client._bangumi_client.close()
            bt_client._bangumi_client = None


if __name__ == "__main__":
    asyncio.run(main())
