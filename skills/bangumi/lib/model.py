"""
Data models for Bangumi API responses.

Defines Pydantic models and dataclasses for structured representation
of Bangumi API entities including subjects, collections, and calendar data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum


class SubjectType(IntEnum):
    """Bangumi subject type.

    Values:
        BOOK: 1
        ANIME: 2
        MUSIC: 3
        GAME: 4
        REAL: 6
    """

    BOOK = 1
    ANIME = 2
    MUSIC = 3
    GAME = 4
    REAL = 6


class CollectionType(IntEnum):
    """User collection status type.

    Values:
        WISH: Want to watch (想看)
        DONE: Completed (看过)
        DOING: Watching (在看)
        ON_HOLD: On hold (搁置)
        DROPPED: Dropped (抛弃)
    """

    WISH = 1
    DONE = 2
    DOING = 3
    ON_HOLD = 4
    DROPPED = 5


COLLECTION_TYPE_LABELS: dict[int, str] = {
    1: "想看",
    2: "看过",
    3: "在看",
    4: "搁置",
    5: "抛弃",
}


@dataclass
class BangumiTag:
    """A tag attached to a Bangumi subject."""

    name: str
    count: int = 0


@dataclass
class BangumiRating:
    """Rating information for a Bangumi subject."""

    rank: int = 0
    total: int = 0
    score: float = 0.0
    count: dict[str, int] = field(default_factory=dict)


@dataclass
class BangumiImages:
    """Image URLs for a Bangumi subject."""

    large: str = ""
    common: str = ""
    medium: str = ""
    small: str = ""
    grid: str = ""


@dataclass
class BangumiCollection:
    """Collection summary counts for a Bangumi subject."""

    wish: int = 0
    collect: int = 0
    doing: int = 0
    on_hold: int = 0
    dropped: int = 0


@dataclass
class SlimSubject:
    """Slim representation of a subject, embedded in user collections."""

    id: int = 0
    type: int = 2
    name: str = ""
    name_cn: str = ""
    short_summary: str = ""
    date: str = ""
    score: float = 0.0
    rank: int = 0
    collection_total: int = 0
    images: BangumiImages = field(default_factory=BangumiImages)
    tags: list[BangumiTag] = field(default_factory=list)
    eps: int = 0
    volumes: int = 0


@dataclass
class BangumiSubject:
    """Full Bangumi subject (anime/book/game/music/real) detail."""

    id: int = 0
    type: int = 2
    name: str = ""
    name_cn: str = ""
    summary: str = ""
    date: str = ""
    platform: str = ""
    nsfw: bool = False
    locked: bool = False
    eps: int = 0
    total_episodes: int = 0
    volumes: int = 0
    images: BangumiImages = field(default_factory=BangumiImages)
    rating: BangumiRating = field(default_factory=BangumiRating)
    collection: BangumiCollection = field(default_factory=BangumiCollection)
    tags: list[BangumiTag] = field(default_factory=list)
    meta_tags: list[str] = field(default_factory=list)
    infobox: list[dict] = field(default_factory=list)

    @property
    def display_name(self) -> str:
        """Return Chinese name if available, else original name."""
        return self.name_cn or self.name

    @property
    def url(self) -> str:
        """Bangumi subject page URL."""
        return f"https://bgm.tv/subject/{self.id}"


@dataclass
class CalendarItem:
    """A single anime entry in the daily calendar (Legacy_SubjectSmall)."""

    id: int = 0
    name: str = ""
    name_cn: str = ""
    summary: str = ""
    air_date: str = ""
    air_weekday: int = 0
    url: str = ""
    eps: int = 0
    eps_count: int = 0
    images: BangumiImages = field(default_factory=BangumiImages)
    rating: BangumiRating = field(default_factory=BangumiRating)
    rank: int = 0
    collection: BangumiCollection = field(default_factory=BangumiCollection)

    @property
    def display_name(self) -> str:
        """Return Chinese name if available, else original name."""
        return self.name_cn or self.name


@dataclass
class Weekday:
    """Weekday information from the calendar API."""

    en: str = ""
    cn: str = ""
    ja: str = ""
    id: int = 0


@dataclass
class CalendarDay:
    """One day in the weekly calendar, containing the weekday and its anime list."""

    weekday: Weekday = field(default_factory=Weekday)
    items: list[CalendarItem] = field(default_factory=list)


@dataclass
class BangumiUser:
    """Bangumi user information from /v0/me."""

    id: int = 0
    username: str = ""
    nickname: str = ""
    user_group: int = 0
    sign: str = ""


@dataclass
class BangumiTopic:
    """A discussion topic from the legacy subject API."""

    id: int = 0
    title: str = ""
    main_id: int = 0
    timestamp: int = 0
    lastpost: int = 0
    replies: int = 0
    user_nickname: str = ""
    url: str = ""


@dataclass
class BangumiBlog:
    """A blog/review entry from the legacy subject API."""

    id: int = 0
    title: str = ""
    summary: str = ""
    image: str = ""
    replies: int = 0
    timestamp: int = 0
    dateline: str = ""
    user_nickname: str = ""
    url: str = ""


@dataclass
class RelatedSubject:
    """A subject related to another subject (from /v0/subjects/{id}/subjects)."""

    relation: str = ""
    subject: SlimSubject = field(default_factory=SlimSubject)


@dataclass
class UserCollectionEntry:
    """A single entry in the user's collection list."""

    subject_id: int = 0
    subject_type: int = 2
    rate: int = 0
    type: int = 0  # CollectionType value
    comment: str = ""
    tags: list[str] = field(default_factory=list)
    ep_status: int = 0
    vol_status: int = 0
    updated_at: str = ""
    private: bool = False
    subject: SlimSubject | None = None

    @property
    def collection_type_label(self) -> str:
        """Human-readable collection type label in Chinese."""
        return COLLECTION_TYPE_LABELS.get(self.type, "未知")


# ---- Parsing helpers ----


def parse_images(data: dict | None) -> BangumiImages:
    """Parse images dict from API response to BangumiImages.

    Args:
        data: Raw images dict from API, may be None.

    Returns:
        Parsed BangumiImages instance.
    """
    if not data:
        return BangumiImages()
    return BangumiImages(
        large=data.get("large", ""),
        common=data.get("common", ""),
        medium=data.get("medium", ""),
        small=data.get("small", ""),
        grid=data.get("grid", ""),
    )


def parse_rating(data: dict | None) -> BangumiRating:
    """Parse rating dict from API response to BangumiRating.

    Args:
        data: Raw rating dict from API, may be None.

    Returns:
        Parsed BangumiRating instance.
    """
    if not data:
        return BangumiRating()
    return BangumiRating(
        rank=data.get("rank", 0),
        total=data.get("total", 0),
        score=data.get("score", 0.0),
        count=data.get("count", {}),
    )


def parse_collection(data: dict) -> BangumiCollection:
    """Parse collection dict from API response to BangumiCollection.

    Args:
        data: Raw collection dict from API, may be None.

    Returns:
        Parsed BangumiCollection instance.
    """
    if not data:
        return BangumiCollection()
    return BangumiCollection(
        wish=data.get("wish", 0),
        collect=data.get("collect", 0),
        doing=data.get("doing", 0),
        on_hold=data.get("on_hold", 0),
        dropped=data.get("dropped", 0),
    )


def parse_tags(data: list | None) -> list[BangumiTag]:
    """Parse tags list from API response.

    Args:
        data: Raw tags list from API, may be None.

    Returns:
        List of BangumiTag instances.
    """
    if not data:
        return []
    return [BangumiTag(name=t.get("name", ""), count=t.get("count", 0)) for t in data]


def parse_calendar_item(data: dict) -> CalendarItem:
    """Parse a Legacy_SubjectSmall dict from calendar API.

    Args:
        data: Raw subject dict from calendar endpoint.

    Returns:
        Parsed CalendarItem instance.
    """
    return CalendarItem(
        id=data.get("id", 0),
        name=data.get("name", ""),
        name_cn=data.get("name_cn", ""),
        summary=data.get("summary", ""),
        air_date=data.get("air_date", ""),
        air_weekday=data.get("air_weekday", 0),
        url=data.get("url", ""),
        eps=data.get("eps", 0),
        eps_count=data.get("eps_count", 0),
        images=parse_images(data.get("images")),
        rating=parse_rating(data.get("rating")),
        rank=data.get("rank", 0),
        collection=parse_collection(data.get("collection")),
    )


def parse_calendar_day(data: dict) -> CalendarDay:
    """Parse one calendar day from the /calendar response.

    Args:
        data: Dict containing weekday and items.

    Returns:
        Parsed CalendarDay instance.
    """
    wd = data.get("weekday", {})
    weekday = Weekday(
        en=wd.get("en", ""),
        cn=wd.get("cn", ""),
        ja=wd.get("ja", ""),
        id=wd.get("id", 0),
    )
    items = [parse_calendar_item(i) for i in data.get("items", [])]
    return CalendarDay(weekday=weekday, items=items)


def parse_subject(data: dict) -> BangumiSubject:
    """Parse a full Subject dict from /v0/subjects/{id}.

    Args:
        data: Raw subject dict from API.

    Returns:
        Parsed BangumiSubject instance.
    """
    return BangumiSubject(
        id=data.get("id", 0),
        type=data.get("type", 2),
        name=data.get("name", ""),
        name_cn=data.get("name_cn", ""),
        summary=data.get("summary", ""),
        date=data.get("date", ""),
        platform=data.get("platform", ""),
        nsfw=data.get("nsfw", False),
        locked=data.get("locked", False),
        eps=data.get("eps", 0),
        total_episodes=data.get("total_episodes", 0),
        volumes=data.get("volumes", 0),
        images=parse_images(data.get("images")),
        rating=parse_rating(data.get("rating")),
        collection=parse_collection(data.get("collection")),
        tags=parse_tags(data.get("tags")),
        meta_tags=data.get("meta_tags", []),
        infobox=data.get("infobox", []),
    )


def parse_slim_subject(data: dict) -> SlimSubject:
    """Parse a SlimSubject dict from collection API response.

    Args:
        data: Raw slim subject dict.

    Returns:
        Parsed SlimSubject instance.
    """
    if not data:
        return SlimSubject()
    return SlimSubject(
        id=data.get("id", 0),
        type=data.get("type", 2),
        name=data.get("name", ""),
        name_cn=data.get("name_cn", ""),
        short_summary=data.get("short_summary", ""),
        date=data.get("date", ""),
        score=data.get("score", 0.0),
        rank=data.get("rank", 0),
        collection_total=data.get("collection_total", 0),
        images=parse_images(data.get("images")),
        tags=parse_tags(data.get("tags")),
        eps=data.get("eps", 0),
        volumes=data.get("volumes", 0),
    )


def parse_related_subject(data: dict) -> RelatedSubject:
    """Parse a related subject from /v0/subjects/{id}/subjects.

    Args:
        data: Raw related subject dict from API.

    Returns:
        Parsed RelatedSubject instance.
    """
    return RelatedSubject(
        relation=data.get("relation", ""),
        subject=parse_slim_subject(data.get("subject", {})),
    )


def parse_user_collection_entry(data: dict) -> UserCollectionEntry:
    """Parse a single UserSubjectCollection dict.

    Args:
        data: Raw collection entry dict from API.

    Returns:
        Parsed UserCollectionEntry instance.
    """
    subject_data = data.get("subject")
    return UserCollectionEntry(
        subject_id=data.get("subject_id", 0),
        subject_type=data.get("subject_type", 2),
        rate=data.get("rate", 0),
        type=data.get("type", 0),
        comment=data.get("comment", ""),
        tags=data.get("tags", []),
        ep_status=data.get("ep_status", 0),
        vol_status=data.get("vol_status", 0),
        updated_at=data.get("updated_at", ""),
        private=data.get("private", False),
        subject=parse_slim_subject(subject_data) if subject_data else None,
    )


def parse_user(data: dict) -> BangumiUser:
    """Parse user info from /v0/me.

    Args:
        data: Raw user dict from API.

    Returns:
        Parsed BangumiUser instance.
    """
    return BangumiUser(
        id=data.get("id", 0),
        username=data.get("username", ""),
        nickname=data.get("nickname", ""),
        user_group=data.get("user_group", 0),
        sign=data.get("sign", ""),
    )


def parse_legacy_topic(data: dict) -> BangumiTopic:
    """Parse a topic dict from legacy subject API response.

    Args:
        data: Raw topic dict from legacy API.

    Returns:
        Parsed BangumiTopic instance.
    """
    user_data = data.get("user", {})
    return BangumiTopic(
        id=data.get("id", 0),
        title=data.get("title", ""),
        main_id=data.get("main_id", 0),
        timestamp=data.get("timestamp", 0),
        lastpost=data.get("lastpost", 0),
        replies=data.get("replies", 0),
        user_nickname=user_data.get("nickname", "") if user_data else "",
        url=data.get("url", ""),
    )


def parse_legacy_blog(data: dict) -> BangumiBlog:
    """Parse a blog dict from legacy subject API response.

    Args:
        data: Raw blog dict from legacy API.

    Returns:
        Parsed BangumiBlog instance.
    """
    user_data = data.get("user", {})
    return BangumiBlog(
        id=data.get("id", 0),
        title=data.get("title", ""),
        summary=data.get("summary", ""),
        image=data.get("image", ""),
        replies=data.get("replies", 0),
        timestamp=data.get("timestamp", 0),
        dateline=data.get("dateline", ""),
        user_nickname=user_data.get("nickname", "") if user_data else "",
        url=data.get("url", ""),
    )
