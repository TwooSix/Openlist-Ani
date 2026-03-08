"""
Unit tests for the Bangumi API client and data models.

Uses mock HTTP responses to verify parsing, caching, and error handling
without requiring a real API connection.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from openlist_ani.core.bangumi.client import BangumiClient
from openlist_ani.core.bangumi.model import (
    COLLECTION_TYPE_LABELS,
    BangumiBlog,
    BangumiImages,
    BangumiRating,
    BangumiTag,
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
        assert len(client._collection_cache) > 0

        # Now post a collection update
        client._request = AsyncMock(return_value=None)
        await client.post_user_collection(subject_id=100, collection_type=3)

        # Cache should be cleared
        assert len(client._collection_cache) == 0


# ================================================================
# Tool tests
# ================================================================


class TestBangumiTools:
    """Tests for Bangumi assistant tools."""

    def test_calendar_tool_format(self):
        from openlist_ani.assistant.tools.bangumi_tool import BangumiCalendarTool

        tool = BangumiCalendarTool()
        assert tool.name == "get_bangumi_calendar"
        assert "calendar" in tool.description.lower()

        # Test formatting
        day = CalendarDay(
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
        result = tool._format_calendar([day])
        assert "星期一" in result
        assert "测试" in result
        assert "8.5" in result

    def test_subject_tool_definition(self):
        from openlist_ani.assistant.tools.bangumi_tool import BangumiSubjectTool

        tool = BangumiSubjectTool()
        assert tool.name == "get_bangumi_subject"
        assert "subject_id" in tool.parameters["properties"]
        assert "subject_id" in tool.parameters["required"]

    def test_collection_tool_definition(self):
        from openlist_ani.assistant.tools.bangumi_tool import BangumiCollectionTool

        tool = BangumiCollectionTool()
        assert tool.name == "get_bangumi_collection"
        assert "collection_type" in tool.parameters["properties"]

    def test_recommend_tool_definition(self):
        from openlist_ani.assistant.tools.bangumi_tool import BangumiRecommendTool

        tool = BangumiRecommendTool()
        assert tool.name == "recommend_anime"
        assert "recommend" in tool.description.lower()

    def test_collection_tool_format(self):
        from openlist_ani.assistant.tools.bangumi_tool import BangumiCollectionTool

        tool = BangumiCollectionTool()
        entries = [
            UserCollectionEntry(
                subject_id=100,
                rate=9,
                type=2,
                comment="Great!",
                subject=SlimSubject(id=100, name="Test", name_cn="测试"),
            ),
        ]
        result = tool._format_collections(entries)
        assert "测试" in result
        assert "rating:9" in result
        assert "看过" in result

    def test_reviews_tool_definition(self):
        from openlist_ani.assistant.tools.bangumi_tool import BangumiReviewsTool

        tool = BangumiReviewsTool()
        assert tool.name == "get_bangumi_reviews"
        assert "subject_id" in tool.parameters["properties"]
        assert "subject_id" in tool.parameters["required"]
        assert "review" in tool.description.lower()

    def test_reviews_tool_format(self):
        from openlist_ani.assistant.tools.bangumi_tool import BangumiReviewsTool

        tool = BangumiReviewsTool()
        topics = [
            BangumiTopic(id=1, title="Great show!", replies=10, user_nickname="alice"),
        ]
        blogs = [
            BangumiBlog(
                id=2,
                title="In-depth review",
                summary="This anime is phenomenal...",
                replies=5,
                user_nickname="bob",
            ),
        ]
        result = tool._format_reviews(100, topics, blogs)
        assert "Great show!" in result
        assert "alice" in result
        assert "10 replies" in result
        assert "In-depth review" in result
        assert "bob" in result
        assert "phenomenal" in result

    def test_collect_tool_definition(self):
        from openlist_ani.assistant.tools.bangumi_tool import BangumiCollectTool

        tool = BangumiCollectTool()
        assert tool.name == "update_bangumi_collection"
        assert "subject_id" in tool.parameters["required"]
        assert "collection_type" in tool.parameters["required"]
        props = tool.parameters["properties"]
        assert "ep_status" in props
        # rate/comment/tags must NOT be exposed
        assert "rate" not in props
        assert "comment" not in props
        assert "tags" not in props

    async def test_collect_tool_validates_type(self):
        from openlist_ani.assistant.tools.bangumi_tool import BangumiCollectTool

        tool = BangumiCollectTool()
        result = await tool.execute(subject_id=100, collection_type=99)
        assert "Invalid collection type" in result

    async def test_collect_tool_validates_ep_status(self):
        from openlist_ani.assistant.tools.bangumi_tool import BangumiCollectTool

        tool = BangumiCollectTool()
        result = await tool.execute(subject_id=100, collection_type=2, ep_status=-1)
        assert "Invalid ep_status" in result

    async def test_collect_tool_updates_single_episode(self):
        from openlist_ani.assistant.tools import bangumi_tool as bt
        from openlist_ani.assistant.tools.bangumi_tool import BangumiCollectTool

        tool = BangumiCollectTool()
        mock_client = AsyncMock()
        mock_client.fetch_subject_episodes.return_value = [
            {"id": 1001, "ep": 1, "sort": 1, "type": 0},
            {"id": 1002, "ep": 2, "sort": 2, "type": 0},
            {"id": 1028, "ep": 28, "sort": 28, "type": 0},
        ]

        with patch.object(bt, "_get_client", return_value=mock_client):
            result = await tool.execute(
                subject_id=517057,
                collection_type=3,
                episode_number=28,
            )

        assert "Successfully updated subject 517057" in result
        mock_client.post_user_collection.assert_called_once()
        post_call = mock_client.post_user_collection.call_args
        assert post_call[1]["subject_id"] == 517057
        assert post_call[1]["collection_type"] == 3
        assert post_call[1]["ep_status"] is None
        mock_client.patch_subject_episode_collections.assert_called_once_with(
            subject_id=517057,
            episode_ids=[1028],
            collection_type=2,
        )

    async def test_collect_tool_updates_progress_to_n(self):
        from openlist_ani.assistant.tools import bangumi_tool as bt
        from openlist_ani.assistant.tools.bangumi_tool import BangumiCollectTool

        tool = BangumiCollectTool()
        mock_client = AsyncMock()
        mock_client.fetch_subject_episodes.return_value = [
            {"id": 1001, "ep": 1, "sort": 1, "type": 0},
            {"id": 1002, "ep": 2, "sort": 2, "type": 0},
            {"id": 1003, "ep": 3, "sort": 3, "type": 0},
        ]

        with patch.object(bt, "_get_client", return_value=mock_client):
            result = await tool.execute(
                subject_id=517057,
                collection_type=3,
                ep_status=3,
            )

        assert "Episode updates: 3 matched" in result
        mock_client.patch_subject_episode_collections.assert_called_once_with(
            subject_id=517057,
            episode_ids=[1001, 1002, 1003],
            collection_type=2,
        )

    async def test_collect_tool_blocks_update_when_episode_mismatch(self):
        from openlist_ani.assistant.tools import bangumi_tool as bt
        from openlist_ani.assistant.tools.bangumi_tool import BangumiCollectTool

        tool = BangumiCollectTool()
        mock_client = AsyncMock()
        mock_client.fetch_subject_episodes.return_value = [
            {"id": 1001, "ep": 1, "sort": 1, "type": 0},
            {"id": 1002, "ep": 2, "sort": 2, "type": 0},
            {"id": 1003, "ep": 3, "sort": 3, "type": 0},
        ]

        with patch.object(bt, "_get_client", return_value=mock_client):
            result = await tool.execute(
                subject_id=517057,
                collection_type=3,
                ep_status=5,
            )

        assert "MISMATCH" in result
        assert "DO NOT call this tool again" in result
        mock_client.post_user_collection.assert_not_called()
        mock_client.patch_subject_episode_collections.assert_not_called()

    async def test_collect_tool_rolls_back_progress_when_target_lower(self):
        from openlist_ani.assistant.tools import bangumi_tool as bt
        from openlist_ani.assistant.tools.bangumi_tool import BangumiCollectTool
        from openlist_ani.core.bangumi.model import UserCollectionEntry

        tool = BangumiCollectTool()
        mock_client = AsyncMock()
        mock_client.fetch_subject_episodes.return_value = [
            {"id": 1001, "ep": 1, "sort": 1, "type": 0},
            {"id": 1002, "ep": 2, "sort": 2, "type": 0},
            {"id": 1003, "ep": 3, "sort": 3, "type": 0},
            {"id": 1004, "ep": 4, "sort": 4, "type": 0},
            {"id": 1005, "ep": 5, "sort": 5, "type": 0},
            {"id": 1006, "ep": 6, "sort": 6, "type": 0},
            {"id": 1007, "ep": 7, "sort": 7, "type": 0},
            {"id": 1008, "ep": 8, "sort": 8, "type": 0},
            {"id": 1009, "ep": 9, "sort": 9, "type": 0},
            {"id": 1010, "ep": 10, "sort": 10, "type": 0},
            {"id": 1011, "ep": 11, "sort": 11, "type": 0},
        ]
        mock_client.fetch_user_collections.return_value = [
            UserCollectionEntry(subject_id=517057, ep_status=11)
        ]

        with patch.object(bt, "_get_client", return_value=mock_client):
            result = await tool.execute(
                subject_id=517057,
                collection_type=3,
                ep_status=4,
            )

        assert "Rollback cleared episodes: 7" in result
        mock_client.post_user_collection.assert_called_once()
        assert mock_client.patch_subject_episode_collections.call_count == 2
        first_call = mock_client.patch_subject_episode_collections.call_args_list[0]
        second_call = mock_client.patch_subject_episode_collections.call_args_list[1]
        assert first_call.kwargs == {
            "subject_id": 517057,
            "episode_ids": [1001, 1002, 1003, 1004],
            "collection_type": 2,
        }
        assert second_call.kwargs == {
            "subject_id": 517057,
            "episode_ids": [1005, 1006, 1007, 1008, 1009, 1010, 1011],
            "collection_type": 0,
        }

    async def test_collect_tool_allows_episode_collection_type_zero(self):
        from openlist_ani.assistant.tools import bangumi_tool as bt
        from openlist_ani.assistant.tools.bangumi_tool import BangumiCollectTool

        tool = BangumiCollectTool()
        mock_client = AsyncMock()
        mock_client.fetch_subject_episodes.return_value = [
            {"id": 1028, "ep": 28, "sort": 28, "type": 0},
        ]

        with patch.object(bt, "_get_client", return_value=mock_client):
            result = await tool.execute(
                subject_id=517057,
                collection_type=3,
                episode_number=28,
                episode_collection_type=0,
            )

        assert "Successfully updated subject 517057" in result
        mock_client.patch_subject_episode_collections.assert_called_once_with(
            subject_id=517057,
            episode_ids=[1028],
            collection_type=0,
        )


# ================================================================
# User profile tests
# ================================================================


class TestUserProfile:
    """Tests for user profile build/load/save functions."""

    def test_load_profile_missing(self, tmp_path):
        from openlist_ani.assistant.tools.helper import profile as profile_helper

        original = profile_helper._PROFILE_PATH
        profile_helper._PROFILE_PATH = tmp_path / "nonexistent.json"
        try:
            result = profile_helper._load_profile()
            assert result is None
        finally:
            profile_helper._PROFILE_PATH = original

    def test_save_and_load_profile(self, tmp_path):
        from openlist_ani.assistant.tools.helper import profile as profile_helper

        original_path = profile_helper._PROFILE_PATH
        original_dir = profile_helper._DATA_DIR
        profile_helper._PROFILE_PATH = tmp_path / "profile.json"
        profile_helper._DATA_DIR = tmp_path
        try:
            profile = {
                "version": 2,
                "avg_rating": 8.0,
                "total_rated": 3,
                "rating_sum": 24.0,
                "collection_stats": {"看过": 3},
                "synced_subject_ids": [1, 2, 3],
                "last_synced_at": "2026-01-01T00:00:00Z",
                "llm_analysis": {
                    "preferred_genres": [{"name": "搞笑", "weight": 0.9}],
                    "preference_summary": "喜欢搞笑番",
                },
            }
            profile_helper._save_profile(profile)
            loaded = profile_helper._load_profile()
            assert loaded is not None
            assert loaded["version"] == 2
            assert loaded["llm_analysis"]["preference_summary"] == "喜欢搞笑番"
        finally:
            profile_helper._PROFILE_PATH = original_path
            profile_helper._DATA_DIR = original_dir

    def test_format_profile_summary(self):
        from openlist_ani.assistant.tools.helper.profile import _format_profile_summary

        profile = {
            "avg_rating": 8.0,
            "total_rated": 3,
            "collection_stats": {"看过": 3, "在看": 1},
            "llm_analysis": {
                "preferred_genres": [
                    {"name": "搞笑", "weight": 0.9},
                    {"name": "冒险", "weight": 0.7},
                ],
                "preferred_tags": [
                    {"name": "热血", "weight": 0.8},
                ],
                "disliked_tags": ["恐怖"],
                "rating_tendency": "moderate",
                "preference_summary": "偏好轻松搞笑的冒险类番剧",
            },
        }
        summary = _format_profile_summary(profile)
        assert "8.0" in summary
        assert "搞笑" in summary
        assert "冒险" in summary
        assert "热血" in summary
        assert "恐怖" in summary
        assert "moderate" in summary
        assert "偏好轻松搞笑" in summary

    def test_format_profile_summary_empty_analysis(self):
        from openlist_ani.assistant.tools.helper.profile import _format_profile_summary

        profile = {
            "avg_rating": 7.5,
            "total_rated": 2,
            "collection_stats": {"在看": 2},
            "llm_analysis": {},
        }
        summary = _format_profile_summary(profile)
        assert "7.5" in summary
        assert "在看" in summary

    def test_season_helpers(self):
        from openlist_ani.assistant.tools.helper.bangumi import _season_label

        assert _season_label(1) == "冬季/1月番"
        assert _season_label(4) == "春季/4月番"
        assert _season_label(7) == "夏季/7月番"
        assert _season_label(10) == "秋季/10月番"


# ================================================================
# End-to-end recommendation flow tests
# ================================================================


class TestEndToEndRecommendation:
    """End-to-end tests for the recommendation tool with mocked dependencies.

    Verifies the full recommend_anime flow:
    - Builds/loads user profile from Bangumi collections (LLM-analyzed)
    - Fetches calendar data for current season
    - Filters out collected anime
    - Returns properly formatted context for LLM
    """

    @pytest.fixture(autouse=True)
    def _setup_env(self, tmp_path):
        """Set up temporary profile path and reset shared client."""
        from openlist_ani.assistant.tools.helper import bangumi as bangumi_helper
        from openlist_ani.assistant.tools.helper import profile as profile_helper

        orig = (
            profile_helper._PROFILE_PATH,
            profile_helper._DATA_DIR,
            profile_helper._STAFF_CACHE_PATH,
            bangumi_helper._bangumi_client,
        )
        profile_helper._PROFILE_PATH = tmp_path / "user_profile.json"
        profile_helper._DATA_DIR = tmp_path
        profile_helper._STAFF_CACHE_PATH = tmp_path / "staff_cache.json"
        bangumi_helper._bangumi_client = None
        yield
        (
            profile_helper._PROFILE_PATH,
            profile_helper._DATA_DIR,
            profile_helper._STAFF_CACHE_PATH,
            bangumi_helper._bangumi_client,
        ) = orig

    @staticmethod
    def _make_calendar():
        """Build a mock calendar with collected and fresh anime."""
        return [
            CalendarDay(
                weekday=Weekday(en="Mon", cn="星期一", ja="月曜日", id=1),
                items=[
                    CalendarItem(
                        id=100,
                        name="Collected Anime",
                        name_cn="已收藏动画",
                        rating=BangumiRating(score=8.5),
                        rank=10,
                        air_date="2026-01-05",
                    ),
                    CalendarItem(
                        id=200,
                        name="Fresh Anime",
                        name_cn="全新动画",
                        rating=BangumiRating(score=7.0),
                        rank=50,
                        air_date="2026-01-12",
                    ),
                    CalendarItem(
                        id=300,
                        name="Another Fresh",
                        name_cn="另一部新番",
                        rating=BangumiRating(score=6.5),
                        rank=80,
                        air_date="2026-01-19",
                    ),
                ],
            ),
        ]

    @staticmethod
    def _make_collections():
        """Build mock collection entries (subject 100 collected)."""
        return [
            UserCollectionEntry(
                subject_id=100,
                rate=9,
                type=2,
                tags=["搞笑", "校园"],
                subject=SlimSubject(
                    id=100,
                    name="Collected Anime",
                    name_cn="已收藏动画",
                    tags=[BangumiTag(name="搞笑", count=300)],
                ),
            ),
        ]

    @staticmethod
    def _mock_llm_analysis():
        """Return a mock LLM analysis result."""
        return {
            "preferred_genres": [{"name": "搞笑", "weight": 0.9}],
            "preferred_tags": [{"name": "校园", "weight": 0.8}],
            "disliked_tags": [],
            "rating_tendency": "generous",
            "preference_summary": "喜欢搞笑校园番",
        }

    async def test_recommend_filters_collected_anime(self):
        """Already-collected anime should not appear in candidate list."""
        import openlist_ani.assistant.tools.bangumi_tool as bt
        from openlist_ani.assistant.tools.helper import profile as profile_helper

        mock_client = AsyncMock()
        mock_client.fetch_user_collections.return_value = self._make_collections()
        mock_client.fetch_calendar.return_value = self._make_calendar()

        with (
            patch.object(bt, "_get_client", return_value=mock_client),
            patch.object(profile_helper, "_enrich_with_staff", return_value={}),
            patch.object(
                profile_helper,
                "_analyze_with_llm",
                return_value=self._mock_llm_analysis(),
            ),
        ):
            tool = bt.BangumiRecommendTool()
            result = await tool.execute()

        # Extract only the candidate list (between "## Candidate Anime" and "---")
        after_header = result.split("## Candidate Anime")[-1]
        candidate_lines = after_header.split("---")[0]
        assert "[100]" not in candidate_lines, (
            "Collected anime 100 should be filtered from candidates"
        )
        assert "已收藏动画" not in candidate_lines
        assert "全新动画" in candidate_lines
        assert "另一部新番" in candidate_lines
        # Blacklist section should contain collected ID
        assert "NEVER recommend" in result
        assert "[100]" in after_header.split("---")[-1]

    async def test_recommend_output_format(self):
        """Output should contain profile summary and candidate list."""
        import openlist_ani.assistant.tools.bangumi_tool as bt
        from openlist_ani.assistant.tools.helper import profile as profile_helper

        mock_client = AsyncMock()
        mock_client.fetch_user_collections.return_value = self._make_collections()
        mock_client.fetch_calendar.return_value = self._make_calendar()

        with (
            patch.object(bt, "_get_client", return_value=mock_client),
            patch.object(profile_helper, "_enrich_with_staff", return_value={}),
            patch.object(
                profile_helper,
                "_analyze_with_llm",
                return_value=self._mock_llm_analysis(),
            ),
        ):
            tool = bt.BangumiRecommendTool()
            result = await tool.execute()

        assert "User Anime Profile" in result
        assert "Candidate Anime" in result
        assert "搞笑" in result
        assert "MUST ONLY recommend" in result
        assert "Recommendation Context" in result

    async def test_recommend_all_collected(self):
        """When all airing anime are in collection, show caught-up message."""
        import openlist_ani.assistant.tools.bangumi_tool as bt
        from openlist_ani.assistant.tools.helper import profile as profile_helper

        mock_client = AsyncMock()
        mock_client.fetch_user_collections.return_value = [
            UserCollectionEntry(
                subject_id=100,
                rate=9,
                type=2,
                tags=[],
                subject=SlimSubject(id=100, name="Only"),
            ),
        ]
        mock_client.fetch_calendar.return_value = [
            CalendarDay(
                weekday=Weekday(en="Mon", cn="星期一", ja="月曜日", id=1),
                items=[
                    CalendarItem(
                        id=100,
                        name="Only",
                        name_cn="唯一",
                        rating=BangumiRating(score=8.0),
                        rank=10,
                    ),
                ],
            ),
        ]

        with (
            patch.object(bt, "_get_client", return_value=mock_client),
            patch.object(profile_helper, "_enrich_with_staff", return_value={}),
            patch.object(profile_helper, "_analyze_with_llm", return_value={}),
        ):
            tool = bt.BangumiRecommendTool()
            result = await tool.execute()

        assert "caught up" in result.lower() or "0 titles" in result

    async def test_recommend_profile_reflects_user_preferences(self):
        """Profile embedded in output should reflect LLM-analyzed preferences."""
        import openlist_ani.assistant.tools.bangumi_tool as bt
        from openlist_ani.assistant.tools.helper import profile as profile_helper

        mock_client = AsyncMock()
        mock_client.fetch_user_collections.return_value = [
            UserCollectionEntry(
                subject_id=1,
                rate=10,
                type=2,
                tags=["神作"],
                subject=SlimSubject(
                    id=1,
                    name="Masterpiece",
                    name_cn="神作动画",
                    tags=[
                        BangumiTag(name="奇幻", count=500),
                        BangumiTag(name="战斗", count=400),
                    ],
                ),
            ),
            UserCollectionEntry(
                subject_id=2,
                rate=8,
                type=2,
                tags=["奇幻"],
                subject=SlimSubject(
                    id=2,
                    name="Fantasy",
                    name_cn="奇幻动画",
                    tags=[BangumiTag(name="奇幻", count=600)],
                ),
            ),
        ]
        mock_client.fetch_calendar.return_value = [
            CalendarDay(
                weekday=Weekday(en="Tue", cn="星期二", ja="火曜日", id=2),
                items=[
                    CalendarItem(
                        id=999,
                        name="New Fantasy",
                        name_cn="新奇幻",
                        rating=BangumiRating(score=7.5),
                        rank=30,
                    ),
                ],
            ),
        ]

        llm_result = {
            "preferred_genres": [{"name": "奇幻", "weight": 0.95}],
            "preferred_tags": [{"name": "战斗", "weight": 0.8}],
            "disliked_tags": [],
            "rating_tendency": "generous",
            "preference_summary": "深度喜欢奇幻战斗类番剧",
        }

        with (
            patch.object(bt, "_get_client", return_value=mock_client),
            patch.object(profile_helper, "_enrich_with_staff", return_value={}),
            patch.object(profile_helper, "_analyze_with_llm", return_value=llm_result),
        ):
            tool = bt.BangumiRecommendTool()
            result = await tool.execute()

        assert "奇幻" in result
        assert "9.0" in result or "9.00" in result
        assert "新奇幻" in result


# ================================================================
# User profile generation and incremental update tests
# ================================================================


class TestUserProfileGeneration:
    """Tests for user profile build, incremental update, and persistence."""

    @pytest.fixture(autouse=True)
    def _setup_env(self, tmp_path):
        """Set up temporary profile path and reset shared client."""
        from openlist_ani.assistant.tools.helper import bangumi as bangumi_helper
        from openlist_ani.assistant.tools.helper import profile as profile_helper

        orig = (
            profile_helper._PROFILE_PATH,
            profile_helper._DATA_DIR,
            profile_helper._STAFF_CACHE_PATH,
            bangumi_helper._bangumi_client,
        )
        profile_helper._PROFILE_PATH = tmp_path / "user_profile.json"
        profile_helper._DATA_DIR = tmp_path
        profile_helper._STAFF_CACHE_PATH = tmp_path / "staff_cache.json"
        bangumi_helper._bangumi_client = None
        self.tmp_path = tmp_path
        yield
        (
            profile_helper._PROFILE_PATH,
            profile_helper._DATA_DIR,
            profile_helper._STAFF_CACHE_PATH,
            bangumi_helper._bangumi_client,
        ) = orig

    async def test_initial_profile_has_correct_structure(self):
        """First-time build creates a profile with all required fields."""
        from openlist_ani.assistant.tools.helper import profile as profile_helper

        mock_client = AsyncMock()
        mock_client.fetch_user_collections.return_value = [
            UserCollectionEntry(
                subject_id=100,
                rate=9,
                type=2,
                tags=["搞笑", "校园"],
                subject=SlimSubject(
                    id=100,
                    name="A",
                    name_cn="甲",
                    tags=[
                        BangumiTag(name="搞笑", count=300),
                        BangumiTag(name="冒险", count=200),
                    ],
                ),
            ),
            UserCollectionEntry(
                subject_id=200,
                rate=7,
                type=3,
                tags=["战斗"],
                subject=SlimSubject(
                    id=200,
                    name="B",
                    name_cn="乙",
                    tags=[BangumiTag(name="战斗", count=150)],
                ),
            ),
        ]

        llm_result = {
            "preferred_genres": [{"name": "搞笑", "weight": 0.8}],
            "preferred_tags": [{"name": "校园", "weight": 0.7}],
            "disliked_tags": [],
            "rating_tendency": "generous",
            "preference_summary": "偏好搞笑校园",
        }

        with (
            patch.object(profile_helper, "_enrich_with_staff", return_value={}),
            patch.object(profile_helper, "_analyze_with_llm", return_value=llm_result),
        ):
            profile = await profile_helper._build_or_update_profile(mock_client)

        assert profile["version"] == 2
        assert set(profile["synced_subject_ids"]) == {100, 200}
        assert profile["total_rated"] == 2
        assert profile["avg_rating"] == pytest.approx(8.0)
        assert profile["collection_stats"]["看过"] == 1
        assert profile["collection_stats"]["在看"] == 1
        assert profile["last_synced_at"] != ""
        assert profile["llm_analysis"]["preferred_genres"][0]["name"] == "搞笑"
        assert (self.tmp_path / "user_profile.json").exists()

    async def test_incremental_update_processes_only_new_entries(self):
        """Incremental update should process only entries not yet synced."""
        from openlist_ani.assistant.tools.helper import profile as profile_helper

        profile_helper._save_profile(
            {
                "version": 2,
                "last_synced_at": "2026-01-01T00:00:00+00:00",
                "synced_subject_ids": [100],
                "avg_rating": 9.0,
                "total_rated": 1,
                "rating_sum": 9.0,
                "collection_stats": {"看过": 1},
                "llm_analysis": {
                    "preferred_genres": [{"name": "搞笑", "weight": 0.8}],
                    "preference_summary": "Old summary",
                },
            }
        )

        mock_client = AsyncMock()
        mock_client.fetch_user_collections.return_value = [
            UserCollectionEntry(
                subject_id=100,
                rate=9,
                type=2,
                tags=["搞笑"],
                subject=SlimSubject(id=100, name="A", name_cn="甲", tags=[]),
            ),
            UserCollectionEntry(
                subject_id=200,
                rate=6,
                type=2,
                tags=["战斗"],
                subject=SlimSubject(
                    id=200,
                    name="B",
                    name_cn="乙",
                    tags=[BangumiTag(name="战斗", count=100)],
                ),
            ),
        ]

        new_llm_result = {
            "preferred_genres": [
                {"name": "搞笑", "weight": 0.7},
                {"name": "战斗", "weight": 0.6},
            ],
            "preference_summary": "Updated summary with new data",
        }

        with (
            patch.object(profile_helper, "_enrich_with_staff", return_value={}),
            patch.object(
                profile_helper, "_analyze_with_llm", return_value=new_llm_result
            ),
        ):
            profile = await profile_helper._build_or_update_profile(mock_client)

        assert set(profile["synced_subject_ids"]) == {100, 200}
        assert profile["total_rated"] == 2
        assert profile["avg_rating"] == pytest.approx(7.5)
        assert profile["collection_stats"]["看过"] == 2
        # LLM analysis should be updated
        assert profile["llm_analysis"]["preference_summary"] == (
            "Updated summary with new data"
        )

    async def test_no_new_entries_preserves_analysis(self):
        """When no new entries exist, existing LLM analysis is preserved."""
        from openlist_ani.assistant.tools.helper import profile as profile_helper

        existing_analysis = {
            "preferred_genres": [{"name": "搞笑", "weight": 0.9}],
            "preferred_studios": [],
            "preferred_staff": [],
            "preference_summary": "Existing analysis",
        }
        profile_helper._save_profile(
            {
                "version": 2,
                "last_synced_at": "2026-01-01T00:00:00+00:00",
                "synced_subject_ids": [100],
                "avg_rating": 9.0,
                "total_rated": 1,
                "rating_sum": 9.0,
                "collection_stats": {"看过": 1},
                "llm_analysis": existing_analysis,
            }
        )

        mock_client = AsyncMock()
        mock_client.fetch_user_collections.return_value = [
            UserCollectionEntry(
                subject_id=100,
                rate=9,
                type=2,
                tags=["搞笑"],
                subject=SlimSubject(id=100, name="A", name_cn="甲", tags=[]),
            ),
        ]

        with (
            patch.object(profile_helper, "_analyze_with_llm") as mock_llm,
        ):
            profile = await profile_helper._build_or_update_profile(mock_client)

        # LLM should NOT be called when there are no new entries
        # and existing analysis is non-empty
        mock_llm.assert_not_called()
        assert profile["llm_analysis"] == existing_analysis

    async def test_version_mismatch_triggers_full_rebuild(self):
        """Incompatible version in saved profile triggers a full rebuild."""
        from openlist_ani.assistant.tools.helper import profile as profile_helper

        (self.tmp_path / "user_profile.json").write_text(
            json.dumps({"version": 999, "llm_analysis": {"stale": True}}),
            encoding="utf-8",
        )

        mock_client = AsyncMock()
        mock_client.fetch_user_collections.return_value = [
            UserCollectionEntry(
                subject_id=500,
                rate=8,
                type=2,
                tags=["新"],
                subject=SlimSubject(id=500, name="New", tags=[]),
            ),
        ]

        llm_result = {
            "preferred_genres": [{"name": "新", "weight": 0.5}],
            "preference_summary": "Fresh rebuild",
        }

        with (
            patch.object(profile_helper, "_enrich_with_staff", return_value={}),
            patch.object(profile_helper, "_analyze_with_llm", return_value=llm_result),
        ):
            profile = await profile_helper._build_or_update_profile(mock_client)

        assert profile["version"] == 2
        assert 500 in profile["synced_subject_ids"]
        assert profile["llm_analysis"]["preference_summary"] == "Fresh rebuild"

    async def test_profile_persisted_to_disk(self):
        """Built profile is saved to the expected file path."""
        from openlist_ani.assistant.tools.helper import profile as profile_helper

        mock_client = AsyncMock()
        mock_client.fetch_user_collections.return_value = []

        with (
            patch.object(profile_helper, "_enrich_with_staff", return_value={}),
            patch.object(profile_helper, "_analyze_with_llm", return_value={}),
        ):
            await profile_helper._build_or_update_profile(mock_client)

        path = self.tmp_path / "user_profile.json"
        assert path.exists()
        data = json.loads(path.read_text("utf-8"))
        assert data["version"] == 2
        assert "last_synced_at" in data
        assert "synced_subject_ids" in data
        assert "llm_analysis" in data

    async def test_repeated_build_is_idempotent(self):
        """Running build twice with same data yields identical profiles."""
        from openlist_ani.assistant.tools.helper import profile as profile_helper

        entries = [
            UserCollectionEntry(
                subject_id=10,
                rate=8,
                type=2,
                tags=["恋爱"],
                subject=SlimSubject(
                    id=10,
                    name="Romance",
                    name_cn="恋爱",
                    tags=[BangumiTag(name="恋爱", count=200)],
                ),
            ),
        ]

        llm_result = {
            "preferred_genres": [{"name": "恋爱", "weight": 0.9}],
            "preference_summary": "恋爱番爱好者",
        }

        mock_client = AsyncMock()
        mock_client.fetch_user_collections.return_value = entries

        with (
            patch.object(profile_helper, "_enrich_with_staff", return_value={}),
            patch.object(profile_helper, "_analyze_with_llm", return_value=llm_result),
        ):
            p1 = await profile_helper._build_or_update_profile(mock_client)
            p2 = await profile_helper._build_or_update_profile(mock_client)

        assert set(p1["synced_subject_ids"]) == set(p2["synced_subject_ids"])
        assert p1["avg_rating"] == p2["avg_rating"]
        assert p1["total_rated"] == p2["total_rated"]

    async def test_llm_analysis_called_on_first_build(self):
        """LLM analysis should be invoked during initial profile build."""
        from openlist_ani.assistant.tools.helper import profile as profile_helper

        mock_client = AsyncMock()
        mock_client.fetch_user_collections.return_value = [
            UserCollectionEntry(
                subject_id=1,
                rate=8,
                type=2,
                tags=["冒险"],
                subject=SlimSubject(id=1, name="Adventure", tags=[]),
            ),
        ]

        with (
            patch.object(profile_helper, "_enrich_with_staff", return_value={}),
            patch.object(
                profile_helper,
                "_analyze_with_llm",
                return_value={
                    "preferred_genres": [{"name": "冒险", "weight": 0.8}],
                    "preference_summary": "冒险番爱好者",
                },
            ) as mock_llm,
        ):
            profile = await profile_helper._build_or_update_profile(mock_client)

        mock_llm.assert_called_once()
        assert profile["llm_analysis"]["preference_summary"] == "冒险番爱好者"
