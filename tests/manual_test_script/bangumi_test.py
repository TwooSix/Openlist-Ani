"""
Manual integration test script for Bangumi API.

Run with:
  BANGUMI_TOKEN=<your_token> uv run python -m tests.manual_test_script.bangumi_test

Requires BANGUMI_TOKEN environment variable or config.toml [bangumi] access_token.

Tests:
  1-5:  Basic API client tests (calendar, subject, collection, cache)
  6:    End-to-end recommendation flow
  7:    User profile generation and validation
  8:    Profile incremental update (consistency check)
  9:    Full LLM E2E test (optional, requires OpenAI API key)
  10:   Fetch subject reviews
  11:   Post user collection (mark anime as wish)
"""

import asyncio
import json
import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

# Ensure project root is on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from openlist_ani.core.bangumi.client import BangumiClient

_PROFILE_FILENAME = "user_profile.json"
_STAFF_CACHE_FILENAME = "staff_cache.json"


# ---- Helper utilities ----


@contextmanager
def _bt_temp_paths(bt_module, tmpdir: str):
    """Temporarily redirect bt profile/cache paths into *tmpdir*."""
    orig_path, orig_dir = bt_module._PROFILE_PATH, bt_module._DATA_DIR
    orig_staff = bt_module._STAFF_CACHE_PATH
    bt_module._PROFILE_PATH = Path(tmpdir) / _PROFILE_FILENAME
    bt_module._STAFF_CACHE_PATH = Path(tmpdir) / _STAFF_CACHE_FILENAME
    bt_module._DATA_DIR = Path(tmpdir)
    try:
        yield
    finally:
        bt_module._PROFILE_PATH, bt_module._DATA_DIR = orig_path, orig_dir
        bt_module._STAFF_CACHE_PATH = orig_staff


async def _reset_bt_client(bt_module) -> None:
    """Reset bangumi_tool client singleton, closing the old one."""
    if bt_module._bangumi_client is not None:
        await bt_module._bangumi_client.close()
    bt_module._bangumi_client = None


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


async def _test_recommendation(bt_module, collections) -> None:
    """Test 6: End-to-end recommendation flow."""
    _print_header("Test 6: End-to-end recommendation flow")

    with tempfile.TemporaryDirectory() as tmpdir, _bt_temp_paths(bt_module, tmpdir):
        tool = bt_module.BangumiRecommendTool()
        result = await tool.execute()

        assert "User Anime Profile" in result, "Missing profile section"
        assert "Candidate Anime" in result, "Missing candidate section"
        assert "personalized" in result.lower(), "Missing recommendation prompt"

        collected_ids = {e.subject_id for e in collections}
        candidate_section = result.split("Candidate Anime")[-1]
        leaked = [cid for cid in collected_ids if f"[{cid}]" in candidate_section]
        assert not leaked, f"Collected anime leaked into candidates: {leaked}"

        profile = json.loads(bt_module._PROFILE_PATH.read_text("utf-8"))
        assert profile["version"] == 2
        assert len(profile["synced_subject_ids"]) == len(collections)

        llm_analysis = profile.get("llm_analysis", {})
        candidate_count = candidate_section.count("  - [")
        print(f"  \u2705 Output: {len(result)} chars")
        print(f"  \u2705 Profile: {len(profile['synced_subject_ids'])} synced subjects")
        print(f"  \u2705 Avg rating: {profile['avg_rating']}")
        print(
            f"  \u2705 LLM analysis: {list(llm_analysis.keys()) if llm_analysis else '(empty)'}"
        )
        print(
            f"  \u2705 Candidates: {candidate_count} anime (filtered {len(collected_ids)} collected)"
        )
    print()


async def _test_profile_generation(bt_module) -> None:
    """Test 7: User profile generation and validation."""
    _print_header("Test 7: User profile generation and validation")

    with tempfile.TemporaryDirectory() as tmpdir, _bt_temp_paths(bt_module, tmpdir):
        profile = await bt_module._build_or_update_profile()

        required_keys = [
            "version",
            "last_synced_at",
            "synced_subject_ids",
            "avg_rating",
            "total_rated",
            "collection_stats",
            "llm_analysis",
        ]
        for key in required_keys:
            assert key in profile, f"Missing field: {key}"

        assert profile["version"] == 2
        assert len(profile["synced_subject_ids"]) > 0, "No subjects synced"
        assert profile["last_synced_at"] != ""

        llm_analysis = profile.get("llm_analysis", {})

        if profile["total_rated"] > 0:
            assert 1.0 <= profile["avg_rating"] <= 10.0, (
                f"Avg rating {profile['avg_rating']} out of range"
            )

        assert len(profile["collection_stats"]) > 0, "No collection stats"

        print(f"  \u2705 Profile version: {profile['version']}")
        print(f"  \u2705 Synced subjects: {len(profile['synced_subject_ids'])}")
        print(
            f"  \u2705 Avg rating: {profile['avg_rating']} (from {profile['total_rated']} rated)"
        )
        print(f"  \u2705 Collection stats: {profile['collection_stats']}")
        if llm_analysis:
            pref = llm_analysis.get("preference_summary", "N/A")
            genres = llm_analysis.get("preferred_genres", [])
            print(f"  \u2705 LLM preference: {pref}")
            print(f"  \u2705 LLM genres: {[g['name'] for g in genres[:5]]}")
        else:
            print("  \u26a0\ufe0f  LLM analysis empty (no API key?)")
    print()


async def _test_profile_incremental(bt_module) -> None:
    """Test 8: Profile incremental update (consistency check)."""
    _print_header("Test 8: Profile incremental update (consistency check)")

    with tempfile.TemporaryDirectory() as tmpdir, _bt_temp_paths(bt_module, tmpdir):
        profile1 = await bt_module._build_or_update_profile()
        synced1 = set(profile1["synced_subject_ids"])
        avg1 = profile1["avg_rating"]
        rated1 = profile1["total_rated"]
        analysis1 = profile1.get("llm_analysis", {})

        profile2 = await bt_module._build_or_update_profile()
        synced2 = set(profile2["synced_subject_ids"])
        avg2 = profile2["avg_rating"]
        rated2 = profile2["total_rated"]

        assert synced1 == synced2, (
            f"Synced IDs differ: {len(synced1)} vs {len(synced2)}"
        )
        assert avg1 == avg2, f"Avg rating changed: {avg1} -> {avg2}"
        assert rated1 == rated2, f"Total rated changed: {rated1} -> {rated2}"

        print(f"  \u2705 Both builds: {len(synced1)} synced subjects")
        print(f"  \u2705 Avg rating stable: {avg1}")
        print(f"  \u2705 LLM analysis preserved: {bool(analysis1)}")
        print("  \u2705 Incremental update correctly detected 0 new entries")
    print()


async def _test_llm_e2e(bt_module) -> None:
    """Test 9: Full LLM E2E test (optional, requires OpenAI API key)."""
    _print_header("Test 9: Full LLM E2E test (optional)")

    from openlist_ani.config import config as app_config

    if not app_config.llm.openai_api_key:
        print("  \u26a0\ufe0f  Skipped: OpenAI API key not configured\n")
        return

    from unittest.mock import MagicMock

    import openlist_ani.assistant.tools as tools_mod
    from openlist_ani.assistant.assistant import AniAssistant
    from openlist_ani.core.download import DownloadManager

    tools_mod._default_registry = None

    with tempfile.TemporaryDirectory() as tmpdir, _bt_temp_paths(bt_module, tmpdir):
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


async def _test_reviews(client: BangumiClient, bt_module, subject_id: int) -> None:
    """Test 10: Fetch subject reviews."""
    _print_header(f"Test 10: Fetch subject reviews (subject {subject_id})")

    topics, blogs = await client.fetch_subject_reviews(subject_id)
    print(f"  Topics: {len(topics)}, Blogs: {len(blogs)}")
    for t in topics[:5]:
        print(f"    Topic: {t.title} (by {t.user_nickname}, {t.replies} replies)")
    for b in blogs[:5]:
        summary_preview = b.summary[:80] if b.summary else "N/A"
        print(f"    Blog: {b.title} (by {b.user_nickname}, {b.replies} replies)")
        print(f"      {summary_preview}...")

    from openlist_ani.assistant.tools.bangumi_tool import BangumiReviewsTool

    await _reset_bt_client(bt_module)
    reviews_tool = BangumiReviewsTool()
    reviews_result = await reviews_tool.execute(subject_id=subject_id)
    assert "Reviews & Discussions" in reviews_result
    print(f"  \u2705 Tool output: {len(reviews_result)} chars\n")


async def _test_collect_tool() -> None:
    """Test 11: BangumiCollectTool validation tests."""
    _print_header("Test 11: BangumiCollectTool validation tests")

    from openlist_ani.assistant.tools.bangumi_tool import BangumiCollectTool

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

    import openlist_ani.assistant.tools.bangumi_tool as bt

    client = BangumiClient(access_token=token)
    try:
        _user, _calendar, subject_id, collections = await _test_basic_api(client)

        os.environ["BANGUMI_TOKEN"] = token
        await _reset_bt_client(bt)

        await _test_recommendation(bt, collections)
        await _reset_bt_client(bt)
        await _test_profile_generation(bt)
        await _reset_bt_client(bt)
        await _test_profile_incremental(bt)
        await _reset_bt_client(bt)
        await _test_llm_e2e(bt)
        await _test_reviews(client, bt, subject_id)
        await _test_collect_tool()

        print("\u2705 All manual tests passed!")
    except Exception as e:
        print(f"\u274c Test failed: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
    finally:
        await client.close()
        if bt._bangumi_client is not None:
            await bt._bangumi_client.close()
            bt._bangumi_client = None


if __name__ == "__main__":
    asyncio.run(main())
