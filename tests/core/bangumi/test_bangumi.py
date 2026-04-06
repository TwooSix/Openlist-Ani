"""
Unit tests for the Bangumi API client and data models.

Uses mock HTTP responses to verify parsing, caching, and error handling
without requiring a real API connection.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from openlist_ani.core.bangumi.client import BangumiClient
from openlist_ani.core.bangumi.model import (
    COLLECTION_TYPE_LABELS,
    BangumiBlog,
    BangumiImages,
    BangumiRating,
    BangumiTopic,
    CalendarDay,
    CalendarItem,
    CollectionType,
    SlimSubject,
    SubjectType,
    UserCollectionEntry,
    Weekday,
    parse_calendar_day,
    parse_calendar_item,
    parse_collection,
    parse_images,
    parse_legacy_blog,
    parse_legacy_topic,
    parse_rating,
    parse_related_subject,
    parse_slim_subject,
    parse_subject,
    parse_tags,
    parse_user,
    parse_user_collection_entry,
)

# ---- Sample API response fixtures ----

CALENDAR_RESPONSE = [
    {
        "weekday": {"en": "Mon", "cn": "星期一", "ja": "月曜日", "id": 1},
        "items": [
            {
                "id": 100,
                "name": "Test Anime",
                "name_cn": "测试动画",
                "summary": "A test anime.",
                "air_date": "2026-01-05",
                "air_weekday": 1,
                "url": "https://bgm.tv/subject/100",
                "eps": 12,
                "eps_count": 12,
                "images": {
                    "large": "https://example.com/l.jpg",
                    "common": "https://example.com/c.jpg",
                    "medium": "https://example.com/m.jpg",
                    "small": "https://example.com/s.jpg",
                    "grid": "https://example.com/g.jpg",
                },
                "rating": {"rank": 50, "total": 1000, "score": 8.5, "count": {}},
                "rank": 50,
                "collection": {
                    "wish": 100,
                    "collect": 200,
                    "doing": 300,
                    "on_hold": 40,
                    "dropped": 10,
                },
            },
        ],
    },
    {
        "weekday": {"en": "Tue", "cn": "星期二", "ja": "火曜日", "id": 2},
        "items": [],
    },
]

SUBJECT_RESPONSE = {
    "id": 100,
    "type": 2,
    "name": "Test Anime",
    "name_cn": "测试动画",
    "summary": "This is a test anime summary.",
    "date": "2026-01-05",
    "platform": "TV",
    "nsfw": False,
    "locked": False,
    "eps": 12,
    "total_episodes": 12,
    "volumes": 0,
    "images": {"large": "https://example.com/l.jpg"},
    "rating": {"rank": 50, "total": 1000, "score": 8.5, "count": {"8": 500}},
    "collection": {
        "wish": 100,
        "collect": 200,
        "doing": 300,
        "on_hold": 40,
        "dropped": 10,
    },
    "tags": [
        {"name": "搞笑", "count": 300},
        {"name": "校园", "count": 200},
    ],
    "meta_tags": ["TV", "2026"],
    "infobox": [{"key": "中文名", "value": "测试动画"}],
    "series": False,
}

USER_RESPONSE = {
    "id": 12345,
    "username": "testuser",
    "nickname": "Test User",
    "user_group": 10,
    "sign": "Hello!",
    "avatar": {
        "large": "https://example.com/avatar_l.jpg",
        "medium": "https://example.com/avatar_m.jpg",
        "small": "https://example.com/avatar_s.jpg",
    },
}

COLLECTION_RESPONSE = {
    "total": 2,
    "limit": 50,
    "offset": 0,
    "data": [
        {
            "subject_id": 100,
            "subject_type": 2,
            "rate": 9,
            "type": 2,
            "comment": "Great!",
            "tags": ["搞笑", "校园"],
            "ep_status": 12,
            "vol_status": 0,
            "updated_at": "2026-01-10T12:00:00+08:00",
            "private": False,
            "subject": {
                "id": 100,
                "type": 2,
                "name": "Test Anime",
                "name_cn": "测试动画",
                "short_summary": "A test anime.",
                "date": "2026-01-05",
                "score": 8.5,
                "rank": 50,
                "collection_total": 650,
                "images": {"small": "https://example.com/s.jpg"},
                "tags": [{"name": "搞笑", "count": 300}],
                "eps": 12,
                "volumes": 0,
            },
        },
        {
            "subject_id": 200,
            "subject_type": 2,
            "rate": 7,
            "type": 3,
            "comment": "",
            "tags": [],
            "ep_status": 5,
            "vol_status": 0,
            "updated_at": "2026-02-01T12:00:00+08:00",
            "private": False,
            "subject": {
                "id": 200,
                "type": 2,
                "name": "Another Anime",
                "name_cn": "",
                "short_summary": "Another.",
                "date": "2026-01-12",
                "score": 7.0,
                "rank": 120,
                "collection_total": 300,
                "images": {},
                "tags": [],
                "eps": 24,
                "volumes": 0,
            },
        },
    ],
}

LEGACY_REVIEW_RESPONSE = {
    "id": 100,
    "name": "Test Anime",
    "name_cn": "测试动画",
    "topic": [
        {
            "id": 1001,
            "title": "Great opening episode!",
            "main_id": 100,
            "timestamp": 1700000000,
            "lastpost": 1700100000,
            "replies": 15,
            "user": {"nickname": "user_a"},
            "url": "https://bgm.tv/subject/topic/1001",
        },
        {
            "id": 1002,
            "title": "Animation quality discussion",
            "main_id": 100,
            "timestamp": 1700050000,
            "lastpost": 1700150000,
            "replies": 8,
            "user": {"nickname": "user_b"},
            "url": "https://bgm.tv/subject/topic/1002",
        },
    ],
    "blog": [
        {
            "id": 2001,
            "title": "Review: A masterpiece of the season",
            "summary": "This anime exceeded all expectations with its intricate plot...",
            "image": "https://example.com/blog.jpg",
            "replies": 22,
            "timestamp": 1700200000,
            "dateline": "2023-11-17",
            "user": {"nickname": "blogger_x"},
            "url": "https://bgm.tv/blog/2001",
        },
    ],
}


# ================================================================
# Model parsing tests
# ================================================================


class TestModelParsing:
    """Tests for data model parsing functions."""

    def test_parse_images(self):
        data = {
            "large": "l.jpg",
            "common": "c.jpg",
            "medium": "m.jpg",
            "small": "s.jpg",
            "grid": "g.jpg",
        }
        img = parse_images(data)
        assert img.large == "l.jpg"
        assert img.grid == "g.jpg"

    def test_parse_images_none(self):
        img = parse_images(None)
        assert img == BangumiImages()

    def test_parse_rating(self):
        data = {"rank": 10, "total": 500, "score": 8.0, "count": {"8": 200}}
        rating = parse_rating(data)
        assert rating.rank == 10
        assert rating.score == pytest.approx(8.0)

    def test_parse_rating_none(self):
        rating = parse_rating(None)
        assert rating == BangumiRating()

    def test_parse_tags(self):
        data = [{"name": "搞笑", "count": 300}, {"name": "校园", "count": 200}]
        tags = parse_tags(data)
        assert len(tags) == 2
        assert tags[0].name == "搞笑"
        assert tags[0].count == 300

    def test_parse_tags_none(self):
        assert parse_tags(None) == []

    def test_parse_collection(self):
        data = {"wish": 1, "collect": 2, "doing": 3, "on_hold": 4, "dropped": 5}
        coll = parse_collection(data)
        assert coll.wish == 1
        assert coll.dropped == 5

    def test_parse_calendar_item(self):
        item_data = CALENDAR_RESPONSE[0]["items"][0]
        item = parse_calendar_item(item_data)
        assert item.id == 100
        assert item.display_name == "测试动画"
        assert item.rating.score == pytest.approx(8.5)

    def test_parse_calendar_day(self):
        day = parse_calendar_day(CALENDAR_RESPONSE[0])
        assert day.weekday.cn == "星期一"
        assert len(day.items) == 1

    def test_parse_subject(self):
        subject = parse_subject(SUBJECT_RESPONSE)
        assert subject.id == 100
        assert subject.display_name == "测试动画"
        assert subject.rating.score == pytest.approx(8.5)
        assert len(subject.tags) == 2
        assert subject.url == "https://bgm.tv/subject/100"

    def test_parse_user(self):
        user = parse_user(USER_RESPONSE)
        assert user.id == 12345
        assert user.username == "testuser"
        assert user.nickname == "Test User"

    def test_parse_slim_subject(self):
        data = COLLECTION_RESPONSE["data"][0]["subject"]
        slim = parse_slim_subject(data)
        assert slim.id == 100
        assert slim.score == pytest.approx(8.5)

    def test_parse_user_collection_entry(self):
        entry = parse_user_collection_entry(COLLECTION_RESPONSE["data"][0])
        assert entry.subject_id == 100
        assert entry.rate == 9
        assert entry.collection_type_label == "看过"
        assert entry.subject is not None
        assert entry.subject.name_cn == "测试动画"

    def test_collection_type_labels(self):
        assert COLLECTION_TYPE_LABELS[1] == "想看"
        assert COLLECTION_TYPE_LABELS[3] == "在看"

    def test_subject_type_enum(self):
        assert SubjectType.ANIME == 2
        assert SubjectType.BOOK == 1

    def test_collection_type_enum(self):
        assert CollectionType.WISH == 1
        assert CollectionType.DONE == 2
        assert CollectionType.DOING == 3

    def test_parse_legacy_topic(self):
        data = LEGACY_REVIEW_RESPONSE["topic"][0]
        topic = parse_legacy_topic(data)
        assert isinstance(topic, BangumiTopic)
        assert topic.id == 1001
        assert topic.title == "Great opening episode!"
        assert topic.main_id == 100
        assert topic.replies == 15
        assert topic.user_nickname == "user_a"
        assert topic.url == "https://bgm.tv/subject/topic/1001"

    def test_parse_legacy_topic_missing_user(self):
        data = {"id": 999, "title": "No user", "replies": 0}
        topic = parse_legacy_topic(data)
        assert topic.id == 999
        assert topic.user_nickname == ""

    def test_parse_legacy_blog(self):
        data = LEGACY_REVIEW_RESPONSE["blog"][0]
        blog = parse_legacy_blog(data)
        assert isinstance(blog, BangumiBlog)
        assert blog.id == 2001
        assert blog.title == "Review: A masterpiece of the season"
        assert "exceeded all expectations" in blog.summary
        assert blog.replies == 22
        assert blog.user_nickname == "blogger_x"
        assert blog.dateline == "2023-11-17"

    def test_parse_legacy_blog_missing_user(self):
        data = {"id": 888, "title": "Solo", "summary": "text"}
        blog = parse_legacy_blog(data)
        assert blog.id == 888
        assert blog.user_nickname == ""

    def test_parse_related_subject(self):
        data = {
            "relation": "续集",
            "subject": {
                "id": 200,
                "type": 2,
                "name": "Test Sequel",
                "name_cn": "测试续集",
                "eps": 13,
            },
        }
        rel = parse_related_subject(data)
        assert rel.relation == "续集"
        assert rel.subject.id == 200
        assert rel.subject.name_cn == "测试续集"
        assert rel.subject.eps == 13

    def test_parse_related_subject_empty(self):
        data = {"relation": "番外篇", "subject": {}}
        rel = parse_related_subject(data)
        assert rel.relation == "番外篇"
        assert rel.subject.id == 0


# ================================================================
# Client tests with mocked HTTP
# ================================================================


class TestBangumiClient:
    """Tests for BangumiClient with mocked HTTP responses."""

    @pytest.fixture
    def client(self):
        return BangumiClient(access_token="test-token-123")

    @pytest.fixture
    def mock_session(self):
        """Create a mock aiohttp session."""
        session = AsyncMock(spec=aiohttp.ClientSession)
        session.closed = False
        return session

    def _mock_request(self, client, response_data, status=200):
        """Set up client with a mocked _request method."""
        client._request = AsyncMock(return_value=response_data)

    @pytest.mark.asyncio
    async def test_request_accepts_202_empty_body(self, client):
        """_request should tolerate 202 with empty/non-JSON body."""
        mock_resp = MagicMock()
        mock_resp.status = 202
        mock_resp.request_info = MagicMock()
        mock_resp.history = ()
        mock_resp.raise_for_status = MagicMock(return_value=None)
        mock_resp.read = AsyncMock(return_value=b"")
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=None)

        mock_session = MagicMock(spec=aiohttp.ClientSession)
        mock_session.closed = False
        mock_session.request = MagicMock(return_value=mock_resp)

        client._ensure_session = MagicMock(return_value=mock_session)
        client._throttle = AsyncMock()

        result = await client._request(
            "POST",
            "/v0/users/-/collections/517057",
            json_body={"type": 3},
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_request_parses_success_json_body(self, client):
        """_request should still parse JSON when successful body exists."""
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.request_info = MagicMock()
        mock_resp.history = ()
        mock_resp.raise_for_status = MagicMock(return_value=None)
        mock_resp.read = AsyncMock(return_value=b'{"ok": true}')
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=None)

        mock_session = MagicMock(spec=aiohttp.ClientSession)
        mock_session.closed = False
        mock_session.request = MagicMock(return_value=mock_resp)

        client._ensure_session = MagicMock(return_value=mock_session)
        client._throttle = AsyncMock()

        result = await client._request("GET", "/calendar")
        assert result == {"ok": True}

    async def test_fetch_calendar(self, client):
        self._mock_request(client, CALENDAR_RESPONSE)
        days = await client.fetch_calendar()
        assert len(days) == 2
        assert days[0].weekday.cn == "星期一"
        assert len(days[0].items) == 1
        assert days[0].items[0].id == 100

    async def test_fetch_calendar_cached(self, client):
        self._mock_request(client, CALENDAR_RESPONSE)
        days1 = await client.fetch_calendar()
        days2 = await client.fetch_calendar()
        # _request should be called only once
        assert client._request.call_count == 1
        assert days1 == days2

    async def test_fetch_subject(self, client):
        self._mock_request(client, SUBJECT_RESPONSE)
        subject = await client.fetch_subject(100)
        assert subject.id == 100
        assert subject.display_name == "测试动画"
        assert subject.rating.score == pytest.approx(8.5)

    async def test_fetch_subject_cached(self, client):
        self._mock_request(client, SUBJECT_RESPONSE)
        s1 = await client.fetch_subject(100)
        s2 = await client.fetch_subject(100)
        assert client._request.call_count == 1
        assert s1 == s2

    async def test_fetch_current_user(self, client):
        self._mock_request(client, USER_RESPONSE)
        user = await client.fetch_current_user()
        assert user.username == "testuser"
        assert user.nickname == "Test User"

    async def test_fetch_current_user_cached(self, client):
        self._mock_request(client, USER_RESPONSE)
        u1 = await client.fetch_current_user()
        u2 = await client.fetch_current_user()
        # _request called once for user (cached permanently)
        assert client._request.call_count == 1
        assert u1 == u2

    async def test_fetch_user_collections(self, client):
        # Need to mock both /v0/me and collections
        call_count = 0
        responses = [USER_RESPONSE, COLLECTION_RESPONSE]

        def side_effect(*args, **kwargs):
            nonlocal call_count
            result = responses[min(call_count, len(responses) - 1)]
            call_count += 1
            return result

        client._request = AsyncMock(side_effect=side_effect)
        entries = await client.fetch_user_collections()
        assert len(entries) == 2
        assert entries[0].subject_id == 100
        assert entries[0].rate == 9
        assert entries[1].subject_id == 200

    async def test_fetch_user_collections_cached(self, client):
        call_count = 0
        responses = [USER_RESPONSE, COLLECTION_RESPONSE]

        def side_effect(*args, **kwargs):
            nonlocal call_count
            result = responses[min(call_count, len(responses) - 1)]
            call_count += 1
            return result

        client._request = AsyncMock(side_effect=side_effect)
        e1 = await client.fetch_user_collections()
        e2 = await client.fetch_user_collections()
        # Second call should hit cache, no additional _request calls
        assert e1 == e2
        # 2 calls total: 1 for /v0/me + 1 for collections (both only on first call)
        assert call_count == 2

    async def test_fetch_subject_reviews(self, client):
        """Test fetching topics and blogs from legacy API."""
        self._mock_request(client, LEGACY_REVIEW_RESPONSE)
        topics, blogs = await client.fetch_subject_reviews(100)
        assert len(topics) == 2
        assert topics[0].title == "Great opening episode!"
        assert topics[0].replies == 15
        assert len(blogs) == 1
        assert blogs[0].title == "Review: A masterpiece of the season"
        assert blogs[0].user_nickname == "blogger_x"

    async def test_fetch_subject_reviews_empty(self, client):
        """Test reviews when no topics or blogs exist."""
        self._mock_request(client, {"id": 100, "name": "Empty"})
        topics, blogs = await client.fetch_subject_reviews(100)
        assert topics == []
        assert blogs == []

    @pytest.mark.asyncio
    async def test_fetch_related_subjects(self, client):
        """Test fetching related subjects for a subject."""
        self._mock_request(
            client,
            [
                {
                    "relation": "续集",
                    "subject": {
                        "id": 200,
                        "type": 2,
                        "name": "Sequel",
                        "name_cn": "续集",
                        "eps": 12,
                    },
                },
                {
                    "relation": "番外篇",
                    "subject": {
                        "id": 300,
                        "type": 2,
                        "name": "OVA",
                        "eps": 2,
                    },
                },
            ],
        )
        related = await client.fetch_related_subjects(100)
        assert len(related) == 2
        assert related[0].relation == "续集"
        assert related[0].subject.id == 200
        assert related[1].relation == "番外篇"

    @pytest.mark.asyncio
    async def test_fetch_related_subjects_cached(self, client):
        """Related subjects should be cached for subsequent calls."""
        self._mock_request(
            client,
            [{"relation": "续集", "subject": {"id": 200, "eps": 12}}],
        )
        first = await client.fetch_related_subjects(100)
        client._request = AsyncMock(side_effect=AssertionError("Should use cache"))
        second = await client.fetch_related_subjects(100)
        assert first == second

    async def test_fetch_subject_episodes(self, client):
        """Should fetch episode list from /v0/episodes."""
        client._request = AsyncMock(
            return_value={
                "total": 2,
                "limit": 100,
                "offset": 0,
                "data": [
                    {"id": 1001, "ep": 1, "sort": 1, "type": 0},
                    {"id": 1002, "ep": 2, "sort": 2, "type": 0},
                ],
            }
        )
        episodes = await client.fetch_subject_episodes(subject_id=100)
        assert len(episodes) == 2
        assert episodes[0]["id"] == 1001
        assert episodes[1]["ep"] == 2

    async def test_patch_subject_episode_collections(self, client):
        """Should call PATCH episode collection endpoint with payload."""
        client._request = AsyncMock(return_value=None)
        await client.patch_subject_episode_collections(
            subject_id=100,
            episode_ids=[1001, 1002, 1003],
            collection_type=2,
        )
        client._request.assert_called_once()
        call = client._request.call_args
        assert call[0][0] == "PATCH"
        assert call[0][1] == "/v0/users/-/collections/100/episodes"
        assert call[1]["json_body"] == {
            "episode_id": [1001, 1002, 1003],
            "type": 2,
        }

    async def test_post_user_collection(self, client):
        """Test posting a collection update returns no error."""
        client._request = AsyncMock(return_value=None)
        await client.post_user_collection(
            subject_id=100,
            collection_type=2,
            rate=8,
            comment="Great!",
        )
        client._request.assert_called_once()
        call_args = client._request.call_args
        assert call_args[0][0] == "POST"
        assert "/v0/users/-/collections/100" in call_args[0][1]
        assert call_args[1]["json_body"]["type"] == 2
        assert call_args[1]["json_body"]["rate"] == 8

    async def test_post_user_collection_with_ep_status(self, client):
        """ep_status should trigger a PATCH after the POST."""
        client._request = AsyncMock(return_value=None)
        await client.post_user_collection(
            subject_id=200,
            collection_type=3,
            ep_status=5,
        )
        assert client._request.call_count == 2
        post_call, patch_call = client._request.call_args_list
        # First call: POST with collection type
        assert post_call[0][0] == "POST"
        assert "/v0/users/-/collections/200" in post_call[0][1]
        assert post_call[1]["json_body"]["type"] == 3
        assert "ep_status" not in post_call[1]["json_body"]
        # Second call: PATCH with ep_status only
        assert patch_call[0][0] == "PATCH"
        assert "/v0/users/-/collections/200" in patch_call[0][1]
        assert patch_call[1]["json_body"] == {"ep_status": 5}

    async def test_post_user_collection_invalidates_cache(self, client):
        """Collection cache should be cleared after updating a collection."""
        # Populate collection cache first
        call_count = 0
        responses = [USER_RESPONSE, COLLECTION_RESPONSE]

        def side_effect_fetch(*args, **kwargs):
            nonlocal call_count
            result = responses[min(call_count, len(responses) - 1)]
            call_count += 1
            return result

        client._request = AsyncMock(side_effect=side_effect_fetch)
        await client.fetch_user_collections()
        assert len(client._cache_fetch_user_collections) > 0

        # Now post a collection update
        client._request = AsyncMock(return_value=None)
        await client.post_user_collection(subject_id=100, collection_type=3)

        # Cache should be cleared
        assert len(client._cache_fetch_user_collections) == 0


# ================================================================
# Tool tests
# ================================================================


class TestBangumiTools:
    """Tests for Bangumi skill scripts (plain function interface).

    Skill scripts live under ``skills/bangumi/script/`` and are loaded at
    runtime by SkillCatalog, **not** via normal Python package imports.
    We use ``importlib.util`` to load them from their file paths.
    """

    @staticmethod
    def _load_skill(script_name: str):
        """Load a skill script module from ``skills/bangumi/script/``."""
        import importlib.util
        from pathlib import Path

        script_dir = Path(__file__).resolve().parents[3] / "skills" / "bangumi" / "script"
        path = script_dir / f"{script_name}.py"
        spec = importlib.util.spec_from_file_location(
            f"skill_bangumi_{script_name}", path
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    @pytest.mark.asyncio
    async def test_calendar_tool_format(self):
        calendar_mod = self._load_skill("calendar")

        mock_client = AsyncMock()
        mock_client.fetch_calendar.return_value = [
            CalendarDay(
                weekday=Weekday(en="Mon", cn="星期一", ja="月曜日", id=1),
                items=[
                    CalendarItem(
                        id=100,
                        name="Test",
                        name_cn="测试",
                        rating=BangumiRating(score=8.5),
                        rank=50,
                    )
                ],
            )
        ]
        mock_client.close = AsyncMock()

        with patch.object(calendar_mod, "BangumiClient", return_value=mock_client):
            result = await calendar_mod.run()

        assert "星期一" in result
        assert "测试" in result
        assert "8.5" in result

    def test_subject_detail_run_exists(self):
        mod = self._load_skill("subject_detail")
        assert callable(mod.run)

    def test_user_collections_run_exists(self):
        mod = self._load_skill("user_collections")
        assert callable(mod.run)

    @pytest.mark.asyncio
    async def test_user_collections_tool_format(self):
        mod = self._load_skill("user_collections")

        # The skill script accesses entry.collection_type, entry.subject,
        # entry.rate, entry.ep_status — use a simple namespace to match.
        entry = MagicMock()
        entry.collection_type = 2
        entry.rate = 9
        entry.ep_status = 0
        entry.subject = MagicMock()
        entry.subject.id = 100
        entry.subject.name = "Test"
        entry.subject.name_cn = "测试"

        mock_client = AsyncMock()
        mock_client.fetch_user_collections.return_value = [entry]
        mock_client.close = AsyncMock()

        with patch.object(mod, "BangumiClient", return_value=mock_client), \
             patch.object(mod, "config", MagicMock(bangumi_token="fake-token")):
            result = await mod.run()

        assert "测试" in result
        assert "rated:9" in result
        assert "done" in result

    def test_search_run_exists(self):
        mod = self._load_skill("search")
        assert callable(mod.run)

    def test_related_subjects_run_exists(self):
        mod = self._load_skill("related_subjects")
        assert callable(mod.run)

    def test_update_collection_run_exists(self):
        mod = self._load_skill("update_collection")
        assert callable(mod.run)

    @pytest.mark.asyncio
    async def test_update_collection_requires_subject_id(self):
        mod = self._load_skill("update_collection")
        result = await mod.run()
        assert "Error" in result
        assert "subject_id" in result

    @pytest.mark.asyncio
    async def test_update_collection_requires_at_least_one_field(self):
        mod = self._load_skill("update_collection")
        result = await mod.run(subject_id="100")
        assert "Error" in result

    @pytest.mark.asyncio
    async def test_update_collection_sets_status(self):
        mod = self._load_skill("update_collection")

        mock_client = AsyncMock()
        mock_client.post_user_collection = AsyncMock()
        mock_client.close = AsyncMock()

        with patch.object(mod, "BangumiClient", return_value=mock_client), \
             patch.object(mod, "config", MagicMock(bangumi_token="fake-token")):
            result = await mod.run(subject_id="517057", collection_type="3")

        assert "Collection updated for subject 517057" in result
        assert "status=doing" in result
        mock_client.post_user_collection.assert_called_once()
        call_kwargs = mock_client.post_user_collection.call_args[1]
        assert call_kwargs["subject_id"] == 517057
        assert call_kwargs["collection_type"] == 3

    @pytest.mark.asyncio
    async def test_update_collection_sets_rate(self):
        mod = self._load_skill("update_collection")

        mock_client = AsyncMock()
        mock_client.post_user_collection = AsyncMock()
        mock_client.close = AsyncMock()

        with patch.object(mod, "BangumiClient", return_value=mock_client), \
             patch.object(mod, "config", MagicMock(bangumi_token="fake-token")):
            result = await mod.run(subject_id="517057", rate="8")

        assert "Collection updated for subject 517057" in result
        assert "rate=8/10" in result

    @pytest.mark.asyncio
    async def test_update_collection_sets_ep_status(self):
        mod = self._load_skill("update_collection")

        mock_client = AsyncMock()
        mock_client.post_user_collection = AsyncMock()
        mock_client.close = AsyncMock()

        with patch.object(mod, "BangumiClient", return_value=mock_client), \
             patch.object(mod, "config", MagicMock(bangumi_token="fake-token")):
            result = await mod.run(subject_id="517057", ep_status="5")

        assert "Collection updated for subject 517057" in result
        assert "ep_status=5" in result

    @pytest.mark.asyncio
    async def test_update_collection_sets_comment(self):
        mod = self._load_skill("update_collection")

        mock_client = AsyncMock()
        mock_client.post_user_collection = AsyncMock()
        mock_client.close = AsyncMock()

        with patch.object(mod, "BangumiClient", return_value=mock_client), \
             patch.object(mod, "config", MagicMock(bangumi_token="fake-token")):
            result = await mod.run(subject_id="517057", comment="Great anime!")

        assert "Collection updated for subject 517057" in result
        assert "comment=" in result
