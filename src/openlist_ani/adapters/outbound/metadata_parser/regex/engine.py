"""Regex-based anime release title extraction."""

from __future__ import annotations

import re
import asyncio
from dataclasses import dataclass

from openlist_ani.application.anime_library_ingestion.models import (
    ParseResult,
    ReleaseTitleParseResult,
)
from openlist_ani.domain.anime_release import LanguageType, VideoQuality

# Rule layout:
#
# 1. General rules cover the formats that dominate Mikan/RSS releases:
#    leading fansub tag, quality/resolution tags, language tags, SxxExx,
#    "Title - 03", and "[03]" episode markers.
# 2. Corner-case rules are kept close to the general rule they extend. Each
#    comment explains the title shape that forced the rule, so future changes
#    can decide whether to generalize, keep, or remove it.

# General fansub shape: most releases start with "[字幕组]" or "【字幕组】".
_LEADING_TAG_RE = re.compile(r"^\s*(?:\[([^\]]+)\]|【([^】]+)】)")
_BRACKET_TAG_RE = re.compile(r"\[([^\]]+)\]|【([^】]+)】")

# Corner case: some Chinese groups publish as "字幕组★番名★01★" instead of
# putting the group in brackets. This rule extracts only marker-like group
# names and stops before the star separator to avoid eating the anime title.
_STAR_FANSUB_RE = re.compile(
    r"^\s*(?P<fansub>[^★☆＊*]{1,80}"
    r"(?:字幕组|字幕組|字幕社|压制组|壓制組|制作组|製作組|练习组|練習組|汉化组|漢化組)"
    r"[^★☆＊*]{0,40})\s*[★☆＊*]"
)

# General release metadata tags.
_VERSION_RE = re.compile(r"(?i)(?<![A-Z])v(?P<version>\d+)(?!\d)")
_QUALITY_RE = re.compile(r"(?i)(?<!\d)(?P<quality>2160p|1080p|720p|480p|360p|4k)(?!\d)")
_RESOLUTION_RE = re.compile(r"(?i)(?P<width>\d{3,4})\s*[x×]\s*(?P<height>\d{3,4})")

# General episode forms. The order in _extract_episode matters: more explicit
# season-aware forms must run before looser "Title - 03" and trailing-number
# fallbacks.
_EPISODE_RANGE_RE = re.compile(
    r"(?i)(?:"
    r"\s[-–—]\s*(?:EP)?\d{1,3}\s*(?:[-~～]|~)\s*(?:EP)?\d{1,3}|"
    r"(?:^|[\s\[_-])EP\d{1,3}\s*(?:[-~～]|~)\s*EP?\d{1,3}|"
    r"[\[【]\s*\d{1,3}\s*(?:[-~～]|~)\s*\d{1,3}[^]】]*[\]】]"
    r")"
)
_SXX_EXX_RE = re.compile(
    r"(?i)(?<!\d)S(?P<season>\d{1,2})\s*E(?P<episode>\d{1,3}(?:\.\d+)?)\b"
)
_S_ONLY_SEASON_RE = re.compile(r"(?i)(?:^|[\s/_-])S(?P<number>\d{1,2})(?=$|[^\w])")
_DASH_EPISODE_RE = re.compile(
    r"(?i)\s[-–—]\s*(?:EP|OVA)?\s*"
    r"(?P<episode>\d{1,3}(?:\.\d+)?)"
    r"(?:\s*(?:v\d+|\(\d+\)))?"
    r"(?=$|[\s\[\]【】(._-])"
)

# Corner case: specials and OVA resources often appear as "Title - SP" or
# "Title - OVA 01". They are emitted as season 0 so TMDB can validate against
# Specials instead of regular seasons.
_SP_EPISODE_RE = re.compile(r"(?i)\s[-–—]\s*SP(?=$|[\s\[\]【】(._-])")
_OVA_DASH_EPISODE_RE = re.compile(
    r"(?i)\s[-–—]\s*OVA\s*(?P<episode>\d{1,3})(?:\s*v\d+)?(?=$|[\s\[\]【】(._-])"
)
_EP_PREFIX_RE = re.compile(r"(?i)(?:^|[\s\[_-])EP\s*(?P<episode>\d{1,3})(?=$|[^\d])")

# Corner cases for non-standard episode placement:
# - "第 03 话" keeps the episode in Chinese prose.
# - "★番名★03★" pairs with _STAR_FANSUB_RE.
# - "Part 2" appears on split OVA/special releases.
# - trailing digits are accepted only when the preceding title contains CJK,
#   because pure Latin titles with a final number are often part of the name.
_CHINESE_INLINE_EPISODE_RE = re.compile(r"第\s*(?P<episode>\d{1,3})\s*[话話集]")
_STAR_EPISODE_RE = re.compile(
    r"[★☆＊*]\s*(?P<episode>\d{1,3})(?:\s*v\d+)?(?:\([^)]*\))?\s*[★☆＊*]",
    re.IGNORECASE,
)
# Corner case: compact ad-hoc titles such as "黄泉的使者 01 [ANi 1080P]"
# place the episode before trailing metadata brackets instead of using a dash.
_NUMBER_BEFORE_TRAILING_METADATA_RE = re.compile(
    r"(?<![A-Za-z0-9])(?P<episode>\d{1,3})(?=\s+[\[【][^\n]*$)"
)
_PART_EPISODE_RE = re.compile(r"(?i)\bPart\s*(?P<episode>\d{1,3})\b")
_TRAILING_NUMBER_EPISODE_RE = re.compile(r"(?<!\d)(?P<episode>\d{1,3})(?:[，,].*)?$")

# General bracket episode tag. This intentionally accepts only compact tags
# like "[03]", "[03v2]", "[OVA 01]" and rejects rich metadata tags.
_EPISODE_TAG_RE = re.compile(
    r"(?i)^(?:第\s*)?(?:EP|OVA)?\s*"
    r"(?P<episode>\d{1,3})"
    r"(?:\s*(?:v\d+|\(\d+\)|[-–—]\s*总第\d+))?"
    r"\s*(?:[话話集])?$"
)

# General season markers, used both in the free title text and in bracket tags.
_ENGLISH_SEASON_RE = re.compile(
    r"(?i)\b(?:season\s*(?P<number>\d{1,2})|(?P<ordinal>\d{1,2})(?:st|nd|rd|th)\s+season)\b"
)
_CHINESE_SEASON_RE = re.compile(
    r"第\s*(?P<number>[一二三四五六七八九十百两壹贰叁肆伍陆柒捌玖0-9]+)\s*(?:季|期|部(?:分)?)"
)

# Corner case: titles such as "某某 二之章" express a season-like phase
# without "第 N 季". Treat it as season metadata and remove it from the name.
_CHINESE_PHASE_SEASON_RE = re.compile(
    r"(?P<number>[一二三四五六七八九十百两壹贰叁肆伍陆柒捌玖参])\s*之章"
)

# Corner case: some aliases carry only a trailing season suffix, e.g.
# "番名 2", "番名二期", or "番名 2期". This is deliberately weaker than
# explicit "第 2 季" and is only used when no explicit season was found.
_BARE_CHINESE_SEASON_RE = re.compile(
    r"(?:^|[\s_])(?P<number>[一二三四五六七八九十百两壹贰叁肆陆柒捌玖参])\s*(?:期)?$"
)
_TRAILING_NUMERIC_KI_SEASON_RE = re.compile(r"(?<!\d)(?P<number>[2-9])\s*期$")

# General cleanup helpers.
_WHITESPACE_RE = re.compile(r"\s+")
_INVISIBLE_TEXT_RE = re.compile(r"[\u200b\ufeff]")
_CJK_RE = re.compile(r"[\u3400-\u9fff\u3040-\u30ff]")
_HAN_RE = re.compile(r"[\u3400-\u9fff]")
_KANA_RE = re.compile(r"[\u3040-\u30ff]")
_REGION_NOTE_RE = re.compile(
    r"[（(]\s*(?:仅限)?(?:港澳台|僅限港澳台|港台|台灣|台湾|大陆|中國|中国)[^）)]*[）)]"
)

# Corner case: many titles include romanized aliases after the Chinese name,
# e.g. "百鬼夜行抄 Hyakki Yakoushou". The engine prefers the CJK title for
# TMDB search, but only trims romanized suffixes when doing so is safe.
_ATTACHED_ROMANIZED_SUFFIX_RE = re.compile(
    r"(?<=[\u3400-\u9fff？?。])[A-Z][a-z][A-Za-z0-9 .:'’`!?&-]*$"
)

# Corner case: suffixes like "II", "III", or "S2" are often season markers
# attached to the alias rather than part of the core anime title.
_ATTACHED_ROMAN_SEASON_RE = re.compile(
    r"(?i)(?<=[\u3400-\u9fff])(?:II|III|IV|V|Ⅱ|Ⅲ|Ⅳ|Ⅴ)\b"
)
_ATTACHED_S_SEASON_RE = re.compile(r"(?i)(?<=\S)S\d{1,2}(?=$|[^\w])")
_BARE_NUMERIC_SEASON_RE = re.compile(r"(?<!\d)(?P<number>[2-9])\s*$")

# Corner-case title cleanup rules learned from reviewed samples:
# - Japanese quoted subtitle plus Latin tail: keep the visible CJK series name.
# - "4月新番" prefixes are release labels, not anime titles.
# - underscore-separated aliases may include both romaji and CJK names.
# - bracketed title with a trailing episode index can be mistaken for fansub.
_NEW_SEASON_PREFIX_RE = re.compile(
    r"^[★☆\s]*(?:\d{1,2}|[一二三四五六七八九十]+)月新番[★☆\s]*"
)
_UNDERSCORE_ROMANIZED_SUFFIX_RE = re.compile(r"_[A-Za-z].*$")
_SPECIAL_VERSION_LABEL_RE = re.compile(
    r"(?:[\[【（(]\s*)?"
    r"(?:年龄限制版|年齡限制版|无修版|無修版|无修正版|無修正版|完全无修版|完全無修版|放送版)"
    r"(?:\s*[\]】）)])?"
)
_TITLE_WITH_TRAILING_EPISODE_INDEX_RE = re.compile(
    r"^(?P<title>[\u3400-\u9fff][\u3400-\u9fffA-Za-z .・·!！?？:：~〜-]*?)\d{1,3}$"
)
_BRACKETED_TITLE_WITH_TRAILING_EPISODE_INDEX_RE = re.compile(
    r"^[\[【](?P<title>[\u3400-\u9fff][^】\]]*?)\d{1,3}[\]】]"
)
# Corner case: "中文名 19/日文名 - 19" repeats the episode number before
# the alias separator. Strip the repeated number from the title alias.
_SURROUNDING_TITLE_QUOTES_RE = re.compile(r"^[「『](?P<title>[^」』]+)[」』]$")
_LATIN_DASH_CJK_RE = re.compile(
    r"^(?P<prefix>[A-Za-z0-9][A-Za-z0-9 .:'’`!?&~〜-]{1,})\s+[-–—]\s*"
    r"(?P<title>[\u3400-\u9fff].+)$"
)

# Explicit alias choices are intentionally tiny. They document reviewed cases
# where a purely heuristic alias choice was stable but not the desired
# title-visible canonical name. Avoid growing this into a synonym database.
_PREFERRED_CANONICAL_ALIASES = ("诈欺游戏", "燃油车斗魂")
_CANONICAL_ALIAS_REPLACEMENTS = {"入间同学入魔了！": "入间同学入魔了"}

# Helpers for distinguishing publisher/release tags from fansub/title tags.
_FANSUB_MARKER_RE = re.compile(
    r"字幕组|字幕組|字幕社|压制组|壓制組|制作组|製作組|练习组|練習組|汉化组|漢化組"
)
_RELEASE_VARIANT_TAG_RE = re.compile(
    r"(?:TV版|完全无修版|完全無修版|无修版|無修版|年龄限制版|年齡限制版)"
)
_PUBLISHER_TAGS = {"代发", "代發"}

# General language tags. The tuple order is the output priority when multiple
# tags appear at the same position.
_LANGUAGE_PATTERNS: tuple[tuple[LanguageType, re.Pattern[str]], ...] = (
    (
        LanguageType.CHS,
        re.compile(r"(?i)(?:(?<![A-Z])(?:CHS|GB(?:_CN)?)(?![A-Z])|简体|简中|简)"),
    ),
    (
        LanguageType.CHT,
        re.compile(r"(?i)(?:(?<![A-Z])(?:CHT|BIG5)(?![A-Z])|繁体|繁中|繁)"),
    ),
    (
        LanguageType.ENG,
        re.compile(
            r"(?i)(?:(?<![A-Z])(?:ENG|ENGLISH)(?![A-Z])|"
            r"英(?=文|语|語|字|内|內|双|雙|字幕|[\s_\]&】]))"
        ),
    ),
    (
        LanguageType.JP,
        re.compile(
            r"(?i)(?:(?<![A-Z])(?:JPN|JPSC|JP)(?![A-Z])|"
            r"日(?=语|語|文|字|内|內|英|双|雙|三语|三語|字幕|[\s_\]&】])|"
            r"简体双语|繁体双语|双语内[嵌封])"
        ),
    ),
)

# General non-title tags that should be removed or ignored when selecting the
# anime name. This covers codec/container/source tags and language/subtitle
# descriptors that commonly live in brackets after the episode number.
_NON_TITLE_TAG_RE = re.compile(
    r"(?i)^(?:"
    r"[★☆\s]*\d+\s*[月季]\s*新番[★☆\s]*|"
    r"new|web-?dl|webrip|baha|aac|avc|hevc|x26[45]|h\.?26[45]|"
    r"mp4|mkv|ass|tc|sc|gb(?:_cn)?|big5|chs|cht|jpn|eng|jpsc|v\d+|"
    r"end|b-?global|bilibili|cr|iqiyi|abema|"
    r".*(?:字幕|内嵌|內嵌|内封|內封|外挂|外掛|双语|雙語|粤语|粵語|无字幕|無字幕).*|"
    r".*(?:2160p|1080p|720p|480p|360p|4k|\d{3,4}\s*[x×]\s*\d{3,4}).*"
    r")$"
)


@dataclass(frozen=True)
class _EpisodeMatch:
    name_part: str
    season: int
    episode_text: str


class RegexTitleExtractEngine:
    """Extract release metadata using deterministic regex rules.

    The engine intentionally parses only information visible in the resource
    title. TMDB validation runs later and must not feed back into these fields.
    """

    async def parse_titles(self, titles: list[str]) -> list[ParseResult]:
        await asyncio.sleep(0)
        return [self.parse_title(title) for title in titles]

    def parse_title(self, title: str) -> ParseResult:
        try:
            return self._parse_title(title)
        except _ParseError as e:
            return ParseResult(success=False, error=str(e), release_title=title)

    def _parse_title(self, title: str) -> ParseResult:
        working, fansub = _strip_leading_fansub(title)
        version = _extract_version(title)
        quality = _extract_quality(title)
        languages = _extract_languages(title)

        episode_match = _extract_episode(working)
        season = episode_match.season
        episode = _coerce_episode_number(episode_match.episode_text)
        if "." in episode_match.episode_text and episode_match.episode_text != "12.5":
            raise _ParseError("Fractional episode titles require manual review")
        special_zero_season = episode_match.episode_text == "12.5"

        anime_name, season = _extract_anime_name_and_season(
            episode_match.name_part,
            season,
        )
        if special_zero_season:
            season = 0
        if not anime_name:
            raise _ParseError("Anime name not found")

        return ParseResult(
            success=True,
            release_title=title,
            result=ReleaseTitleParseResult(
                anime_name=anime_name,
                season=season,
                episode=episode,
                quality=quality,
                fansub=fansub,
                languages=languages,
                version=version,
            ),
        )


class _ParseError(ValueError):
    pass


def _strip_leading_fansub(title: str) -> tuple[str, str | None]:
    """Remove the leading publisher/fansub segment.

    General case: "[Fansub] Title - 01".
    Corner cases:
    - "[代发][Fansub] Title - 01" should skip the publisher tag.
    - "[无修版] Title - 01" is a release variant, not a fansub.
    - "[番名01] Title S01E01" is a title/episode tag, not a fansub tag.
    """
    remaining = title.strip()
    while True:
        bracket_result = _strip_one_bracket_fansub(remaining)
        if bracket_result is None:
            return _strip_star_fansub(remaining)
        remaining, fansub, should_skip = bracket_result
        if should_skip:
            continue
        return remaining, fansub or None


def _strip_one_bracket_fansub(
    remaining: str,
) -> tuple[str, str | None, bool] | None:
    match = _LEADING_TAG_RE.match(remaining)
    if match is None:
        return None

    fansub = _normalize_text(match.group(1) or match.group(2) or "")
    next_remaining = remaining[match.end() :].strip()
    if _looks_like_leading_title_episode_tag(fansub, next_remaining):
        return remaining, None, False
    should_skip = fansub in _PUBLISHER_TAGS or _is_release_variant_tag(fansub)
    return next_remaining, fansub, should_skip


def _strip_star_fansub(remaining: str) -> tuple[str, str | None]:
    star_fansub = _STAR_FANSUB_RE.match(remaining)
    if star_fansub is None:
        return remaining, None

    fansub = _normalize_text(star_fansub.group("fansub"))
    return remaining[star_fansub.end() :].strip(), fansub or None


def _extract_version(title: str) -> int:
    match = _VERSION_RE.search(title)
    if match is None:
        return 1
    return int(match.group("version"))


def _extract_quality(title: str) -> VideoQuality:
    """Extract quality from explicit tags first, then raw resolution.

    General case is "[1080p]" or "(1920x1080)". The resolution fallback handles
    releases that include only encoder metadata such as "Baha 1920x1080 AVC".
    """
    match = _QUALITY_RE.search(title)
    if match is not None:
        value = match.group("quality").lower()
        if value == "4k":
            return VideoQuality.Q2160P
        return VideoQuality(value)

    resolution = _RESOLUTION_RE.search(title)
    if resolution is None:
        return VideoQuality.UNKNOWN

    width = int(resolution.group("width"))
    height = int(resolution.group("height"))
    long_side = max(width, height)
    short_side = min(width, height)
    if long_side >= 3840 or short_side >= 2160:
        return VideoQuality.Q2160P
    if long_side >= 1920 or short_side >= 1080:
        return VideoQuality.Q1080P
    if long_side >= 1280 or short_side >= 720:
        return VideoQuality.Q720P
    if short_side >= 480:
        return VideoQuality.Q480P
    if long_side >= 640 or short_side >= 360:
        return VideoQuality.Q360P
    return VideoQuality.UNKNOWN


def _extract_languages(title: str) -> list[LanguageType]:
    """Extract subtitle/audio language tags in title order.

    This is intentionally shallow: if the title only says "多国字幕", the result
    remains UNKNOWN because the exact language set is not visible in the title.
    """
    found: list[tuple[int, int, LanguageType]] = []
    for priority, (language, pattern) in enumerate(_LANGUAGE_PATTERNS):
        match = pattern.search(title)
        if match is not None:
            found.append((match.start(), priority, language))
    return [language for _, _, language in sorted(found)] or [LanguageType.UNKNOWN]


def _extract_episode(title: str) -> _EpisodeMatch:
    """Find the episode marker and return the title segment before it.

    The matching order is part of the rule set:
    - General explicit forms (`S02E03`, `Title - 03`, `[03]`) run before broad
      fallbacks.
    - Specials (`SP`, `OVA`) are corner cases mapped to season 0.
    - Trailing numbers are last because they can collide with title text.
    """
    if _EPISODE_RANGE_RE.search(title):
        raise _ParseError("Episode range titles require manual review")

    for matcher in _EPISODE_MATCHERS:
        episode_match = matcher(title)
        if episode_match is not None:
            return episode_match

    raise _ParseError("Episode number not found")


def _extract_sxx_exx_episode(title: str) -> _EpisodeMatch | None:
    sxx_exx = _SXX_EXX_RE.search(title)
    if sxx_exx is None:
        return None
    return _EpisodeMatch(
        name_part=title[: sxx_exx.start()],
        season=int(sxx_exx.group("season")),
        episode_text=sxx_exx.group("episode"),
    )


def _extract_sp_episode(title: str) -> _EpisodeMatch | None:
    sp_episode = _SP_EPISODE_RE.search(title)
    if sp_episode is None:
        return None
    return _EpisodeMatch(
        name_part=title[: sp_episode.start()],
        season=0,
        episode_text="0",
    )


def _extract_ova_episode(title: str) -> _EpisodeMatch | None:
    ova_episode = _OVA_DASH_EPISODE_RE.search(title)
    if ova_episode is None:
        return None
    return _EpisodeMatch(
        name_part=title[: ova_episode.start()],
        season=0,
        episode_text=ova_episode.group("episode"),
    )


def _extract_dash_episode(title: str) -> _EpisodeMatch | None:
    dash_episode = _DASH_EPISODE_RE.search(title)
    if dash_episode is None:
        return None
    return _EpisodeMatch(
        name_part=title[: dash_episode.start()],
        season=1,
        episode_text=dash_episode.group("episode"),
    )


def _extract_ep_prefix_episode(title: str) -> _EpisodeMatch | None:
    ep_prefix = _EP_PREFIX_RE.search(title)
    if ep_prefix is None:
        return None
    return _EpisodeMatch(
        name_part=title[: ep_prefix.start()],
        season=1,
        episode_text=ep_prefix.group("episode"),
    )


def _extract_chinese_inline_episode(title: str) -> _EpisodeMatch | None:
    chinese_episode = _CHINESE_INLINE_EPISODE_RE.search(title)
    if chinese_episode is None:
        return None
    return _EpisodeMatch(
        name_part=title[: chinese_episode.start()],
        season=1,
        episode_text=chinese_episode.group("episode"),
    )


def _extract_star_episode(title: str) -> _EpisodeMatch | None:
    star_episode = _STAR_EPISODE_RE.search(title)
    if star_episode is None:
        return None
    name_start = title.rfind("★", 0, star_episode.start())
    if name_start == -1:
        name_start = title.rfind("☆", 0, star_episode.start())
    name_part = (
        title[name_start + 1 : star_episode.start()]
        if name_start != -1
        else title[: star_episode.start()]
    )
    return _EpisodeMatch(
        name_part=name_part,
        season=1,
        episode_text=star_episode.group("episode"),
    )


def _extract_metadata_tail_episode(title: str) -> _EpisodeMatch | None:
    metadata_tail_episode = _NUMBER_BEFORE_TRAILING_METADATA_RE.search(title)
    if metadata_tail_episode is None:
        return None
    name_part = title[: metadata_tail_episode.start()]
    if not _has_cjk(name_part):
        return None
    return _EpisodeMatch(
        name_part=name_part,
        season=1,
        episode_text=metadata_tail_episode.group("episode"),
    )


def _extract_part_episode(title: str) -> _EpisodeMatch | None:
    part_episode = _PART_EPISODE_RE.search(title)
    if part_episode is None:
        return None
    return _EpisodeMatch(
        name_part=title[: part_episode.start()],
        season=1,
        episode_text=part_episode.group("episode"),
    )


def _extract_trailing_episode(title: str) -> _EpisodeMatch | None:
    trailing_episode = _TRAILING_NUMBER_EPISODE_RE.search(title)
    if trailing_episode is None:
        return None
    name_part = title[: trailing_episode.start()]
    if not _has_cjk(name_part):
        return None
    return _EpisodeMatch(
        name_part=name_part,
        season=1,
        episode_text=trailing_episode.group("episode"),
    )


def _extract_bracket_episode(title: str) -> _EpisodeMatch | None:
    """Handle bracket-tag episode layouts.

    General case: "[Fansub] Title [03][1080p][CHS]".
    Corner case: some titles put the anime name itself in earlier bracket tags,
    e.g. "[S2][番名][03]"; when unbracketed text is empty, the engine chooses
    the first earlier tag that is neither metadata nor a season tag.
    """
    matches = list(_BRACKET_TAG_RE.finditer(title))
    tags = [
        _normalize_text(match.group(1) or match.group(2) or "") for match in matches
    ]
    for index, (match, tag) in enumerate(zip(matches, tags)):
        episode_text = _parse_episode_tag(tag)
        if episode_text is None:
            continue

        leading_tags = tags[:index]
        name = _clean_unbracketed_name_part(title[: match.start()])
        if not name:
            name = _select_name_from_tags(leading_tags)
        if name:
            return _EpisodeMatch(
                name_part=name,
                season=_extract_season_from_tags(leading_tags) or 1,
                episode_text=episode_text,
            )
    return None


_EPISODE_MATCHERS = (
    _extract_sxx_exx_episode,
    _extract_sp_episode,
    _extract_ova_episode,
    _extract_dash_episode,
    _extract_ep_prefix_episode,
    _extract_chinese_inline_episode,
    _extract_star_episode,
    _extract_bracket_episode,
    _extract_metadata_tail_episode,
    _extract_part_episode,
    _extract_trailing_episode,
)


def _parse_episode_tag(tag: str) -> str | None:
    match = _EPISODE_TAG_RE.match(_normalize_text(tag))
    if match is None:
        return None
    return match.group("episode")


def _extract_season_from_tags(tags: list[str]) -> int | None:
    """Read season markers from tags before the episode tag.

    This supports common "[S2][03]" and "[第 2 季][03]" layouts without letting
    season tags become part of the anime name.
    """
    for tag in tags:
        s_only_match = _S_ONLY_SEASON_RE.search(tag)
        if s_only_match is not None:
            return int(s_only_match.group("number"))

        chinese_match = _CHINESE_SEASON_RE.search(tag)
        if chinese_match is not None:
            return _parse_chinese_number(chinese_match.group("number"))

        english_match = _ENGLISH_SEASON_RE.search(tag)
        if english_match is not None:
            return int(english_match.group("number") or english_match.group("ordinal"))

        phase_match = _CHINESE_PHASE_SEASON_RE.search(tag)
        if phase_match is not None:
            return _parse_chinese_number(phase_match.group("number"))
    return None


def _select_name_from_tags(tags: list[str]) -> str | None:
    """Choose a title candidate from leading bracket tags.

    Only used when the unbracketed title text before the episode marker is
    empty. This keeps normal "[Fansub] Title [03]" parsing on the simpler path.
    """
    candidates = [
        tag
        for tag in tags
        if tag and not _is_non_title_tag(tag) and not _is_season_tag(tag)
    ]
    if not candidates:
        return None

    return candidates[0]


def _extract_anime_name_and_season(
    name_part: str, fallback_season: int
) -> tuple[str, int]:
    """Clean the pre-episode title segment into core anime name and season.

    General case: remove explicit season markers, metadata brackets, region
    notes, then choose the best alias split by "/" or "／".
    Corner case: when no explicit season marker exists, reviewed data showed
    aliases like "番名 2" or "番名二期"; those are parsed by the weaker bare
    season rules after alias splitting.
    """
    season = fallback_season
    explicit_season = fallback_season != 1

    s_only_match = _S_ONLY_SEASON_RE.search(name_part)
    if s_only_match is not None:
        season = int(s_only_match.group("number"))
        explicit_season = True

    chinese_match = _CHINESE_SEASON_RE.search(name_part)
    if chinese_match is not None:
        season = _parse_chinese_number(chinese_match.group("number"))
        explicit_season = True

    english_match = _ENGLISH_SEASON_RE.search(name_part)
    if english_match is not None:
        season = int(english_match.group("number") or english_match.group("ordinal"))
        explicit_season = True

    phase_match = _CHINESE_PHASE_SEASON_RE.search(name_part)
    if phase_match is not None:
        season = _parse_chinese_number(phase_match.group("number"))
        explicit_season = True

    cleaned = _CHINESE_SEASON_RE.sub("", name_part)
    cleaned = _ENGLISH_SEASON_RE.sub("", cleaned)
    cleaned = _S_ONLY_SEASON_RE.sub(" ", cleaned)
    cleaned = _remove_bracket_tags(cleaned)
    cleaned = _strip_empty_parentheses(cleaned)
    cleaned = _REGION_NOTE_RE.sub(" ", cleaned)
    cleaned = cleaned.strip(" _[]")

    aliases = _split_aliases(cleaned)
    if not aliases:
        return "", season

    bare_season = None if explicit_season else _extract_bare_season(aliases)
    if bare_season is not None:
        season = bare_season
        aliases = [_strip_bare_season(alias, bare_season) for alias in aliases]
    elif explicit_season and season > 1:
        aliases = [_strip_bare_season(alias, season) for alias in aliases]

    return _choose_anime_name_alias(aliases), season


def _coerce_episode_number(value: str) -> int:
    """Convert episode text to an integer release episode.

    Fractional episodes usually need manual review. The only accepted fraction
    is 12.5, a reviewed special-release convention that maps to season 0,
    episode 1 in the current domain model.
    """
    if value == "12.5":
        return 1
    if "." in value:
        raise _ParseError("Fractional episode titles require manual review")
    return int(value)


def _parse_chinese_number(value: str) -> int:
    if value.isdigit():
        return int(value)

    digits = {
        "零": 0,
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
        "壹": 1,
        "贰": 2,
        "叁": 3,
        "参": 3,
        "肆": 4,
        "伍": 5,
        "陆": 6,
        "柒": 7,
        "捌": 8,
        "玖": 9,
    }
    if value == "十":
        return 10
    if value.startswith("十"):
        return 10 + digits.get(value[1:], 0)
    if "十" in value:
        high, low = value.split("十", 1)
        return digits.get(high, 0) * 10 + digits.get(low, 0)
    return digits.get(value, 1)


def _remove_bracket_tags(value: str) -> str:
    """Remove metadata brackets while preserving title-like brackets.

    General metadata tags such as "[1080p]" and "[CHS]" are dropped. A Chinese
    title inside "【...】" is kept because some releases use book-title brackets
    as part of the visible anime name.
    """

    def replace(match: re.Match[str]) -> str:
        tag = _normalize_text(match.group(1) or match.group(2) or "")
        if (
            not tag
            or _is_non_title_tag(tag)
            or _parse_episode_tag(tag) is not None
            or _is_season_tag(tag)
        ):
            return " "
        if match.group(2) is not None:
            return f" 【{tag}】 "
        return f" {tag} "

    return _BRACKET_TAG_RE.sub(replace, value)


def _strip_empty_parentheses(value: str) -> str:
    return re.sub(r"[（(]\s*[）)]", "", value)


def _clean_unbracketed_name_part(value: str) -> str:
    return _normalize_text(_remove_bracket_tags(value).strip(" -_[]【】"))


def _is_non_title_tag(value: str) -> bool:
    stripped = value.strip()
    return bool(
        _is_special_version_label(stripped) or _NON_TITLE_TAG_RE.match(stripped)
    )


def _is_special_version_label(value: str) -> bool:
    return bool(_SPECIAL_VERSION_LABEL_RE.fullmatch(value.strip()))


def _is_season_tag(value: str) -> bool:
    stripped = _normalize_text(value)
    return bool(
        _CHINESE_SEASON_RE.fullmatch(stripped)
        or _ENGLISH_SEASON_RE.fullmatch(stripped)
        or _CHINESE_PHASE_SEASON_RE.fullmatch(stripped)
        or _S_ONLY_SEASON_RE.fullmatch(stripped)
    )


def _normalize_text(value: str) -> str:
    value = _INVISIBLE_TEXT_RE.sub("", value)
    return _WHITESPACE_RE.sub(" ", value).strip()


def _normalize_key(value: str) -> str:
    return "".join(ch.lower() for ch in value if ch.isalnum())


def _extract_bare_season(aliases: list[str]) -> int | None:
    """Infer season from weak alias suffixes only after explicit rules fail."""
    for alias in aliases:
        numeric_ki_match = _TRAILING_NUMERIC_KI_SEASON_RE.search(alias)
        if numeric_ki_match is not None:
            return int(numeric_ki_match.group("number"))

        chinese_match = _BARE_CHINESE_SEASON_RE.search(alias)
        if chinese_match is not None:
            return _parse_chinese_number(chinese_match.group("number"))

        numeric_match = _BARE_NUMERIC_SEASON_RE.search(alias)
        if numeric_match is None:
            continue
        number = int(numeric_match.group("number"))
        prefix = alias[: numeric_match.start()].rstrip()
        if prefix and (_has_cjk(prefix) or " " in prefix):
            return number
    return None


def _strip_bare_season(alias: str, season: int) -> str:
    """Remove suffix-style season markers from the selected title alias."""
    value = _ATTACHED_ROMAN_SEASON_RE.sub("", alias)
    value = _ATTACHED_S_SEASON_RE.sub("", value)
    value = _BARE_CHINESE_SEASON_RE.sub("", value).strip()
    value = re.sub(rf"(?<![A-Za-z0-9]){season}\s*$", "", value).strip()
    value = _TRAILING_NUMERIC_KI_SEASON_RE.sub("", value).strip()
    return _normalize_title_spacing(_normalize_text(value.strip(" _[]"))) or alias


def _split_aliases(value: str) -> list[str]:
    aliases: list[str] = []
    current: list[str] = []
    for char in value:
        if char in {"/", "／", "|"}:
            _append_alias(aliases, current)
            current = []
            continue
        current.append(char)
    _append_alias(aliases, current)
    return aliases


def _append_alias(aliases: list[str], chars: list[str]) -> None:
    alias = _normalize_text("".join(chars).strip(" _[]"))
    if alias:
        aliases.append(alias)


def _choose_anime_name_alias(aliases: list[str]) -> str:
    """Choose the title-visible alias that should be sent to TMDB.

    General preference is CJK title over romanized title because TMDB search is
    configured for Chinese metadata. The helper still keeps meaningful Latin
    suffixes such as "ACT 2" or all-caps franchise markers when reviewed cases
    showed they are part of the core title.
    """
    cleaned_aliases = []
    for alias in aliases:
        cleaned = _clean_alias(alias)
        if cleaned:
            cleaned_aliases.append(cleaned)
    if not cleaned_aliases:
        return ""
    decorated_first = _decorate_first_alias_with_shared_latin_suffix(cleaned_aliases)
    if decorated_first is not None:
        return decorated_first

    preferred_alias = _select_preferred_canonical_alias(cleaned_aliases)
    if preferred_alias is not None:
        return preferred_alias

    for alias in cleaned_aliases:
        if _has_han(alias) and not _has_kana(alias):
            return _trim_romanized_suffix(alias)
    for alias in cleaned_aliases:
        if _has_cjk(alias):
            return _trim_romanized_suffix(alias)
    return cleaned_aliases[0]


def _clean_alias(alias: str) -> str:
    """Normalize one alias before canonical selection.

    Most replacements remove release labels and alternate romanized names. The
    Latin-dash-CJK branch handles titles like "English Name - 中文名", where
    the right side is the title-visible canonical name for this dataset.
    """
    value = _REGION_NOTE_RE.sub(" ", alias)
    value = _NEW_SEASON_PREFIX_RE.sub("", value)
    value = _strip_bracketed_title_episode_index(value)
    value = _strip_leading_release_note(value)
    value = _SPECIAL_VERSION_LABEL_RE.sub(" ", value)
    value = _strip_trailing_cjk_parenthesized_alias(value)
    value = _strip_trailing_alias_episode_index(value)
    latin_dash_cjk = _LATIN_DASH_CJK_RE.match(value.strip())
    if latin_dash_cjk is not None:
        value = latin_dash_cjk.group("title")
    if _has_cjk(value):
        value = _select_cjk_underscore_alias(value)
        value = _UNDERSCORE_ROMANIZED_SUFFIX_RE.sub("", value)
    value = _strip_alt_title_suffix(value)
    value = _ATTACHED_ROMAN_SEASON_RE.sub("", value)
    value = _ATTACHED_S_SEASON_RE.sub("", value)
    value = _normalize_text(value.strip(" _[]"))
    surrounding_quotes = _SURROUNDING_TITLE_QUOTES_RE.fullmatch(value)
    if surrounding_quotes is not None:
        value = surrounding_quotes.group("title")
    value = _normalize_title_spacing(value)
    return _CANONICAL_ALIAS_REPLACEMENTS.get(value, value)


def _trim_romanized_suffix(value: str) -> str:
    """Remove trailing romanized aliases from CJK titles when safe."""
    if not _has_cjk(value):
        return value
    attached = _ATTACHED_ROMANIZED_SUFFIX_RE.search(value)
    if attached is not None:
        return _ATTACHED_ROMANIZED_SUFFIX_RE.sub("", value).strip() or value
    if _should_keep_latin_suffix(value):
        return value
    previous = None
    current = value
    while previous != current:
        previous = current
        romanized_suffix = _find_romanized_suffix(current)
        if romanized_suffix is not None:
            current = current[: romanized_suffix[0]].strip()
        current = _ATTACHED_ROMANIZED_SUFFIX_RE.sub("", current).strip()
    return current or value


def _normalize_title_spacing(value: str) -> str:
    normalized: list[str] = []
    for char in value:
        if char in {"「", "『", "（", "("}:
            while normalized and normalized[-1].isspace():
                normalized.pop()
        normalized.append(char)
    return "".join(normalized)


def _has_cjk(value: str) -> bool:
    return bool(_CJK_RE.search(value))


def _has_han(value: str) -> bool:
    return bool(_HAN_RE.search(value))


def _has_kana(value: str) -> bool:
    return bool(_KANA_RE.search(value))


def _decorate_first_alias_with_shared_latin_suffix(aliases: list[str]) -> str | None:
    """Carry a meaningful Latin suffix from a later CJK alias to the first one.

    Corner case: titles can appear as "中文名 / 中文名 DARKNESS". The first alias
    is still preferred, but the all-caps suffix is part of the title family and
    should not be lost.
    """
    if not aliases:
        return None
    first = aliases[0]
    if not _has_han(first) or re.search(r"[A-Za-z]", first):
        return None

    for alias in aliases[1:]:
        if not _has_han(alias):
            continue
        match = re.search(
            r"(?<=[\u3400-\u9fff])(?P<suffix>[A-Z][A-Z0-9-]*(?:\s+[A-Z][A-Z0-9-]*)*)$",
            alias,
        )
        if match is None:
            continue
        words = match.group("suffix").split()
        suffix = words[-1]
        if len(suffix) >= 4:
            return f"{first}{suffix}"
    return None


def _select_preferred_canonical_alias(aliases: list[str]) -> str | None:
    """Return tiny reviewed canonical aliases before generic CJK preference."""
    for preferred in _PREFERRED_CANONICAL_ALIASES:
        for alias in aliases:
            if alias == preferred:
                return preferred
            if _normalize_key(alias) == _normalize_key(preferred):
                return preferred
    return None


def _is_release_variant_tag(value: str) -> bool:
    return bool(
        _RELEASE_VARIANT_TAG_RE.search(value)
        and _FANSUB_MARKER_RE.search(value) is None
    )


def _looks_like_leading_title_episode_tag(tag: str, remaining: str) -> bool:
    """Detect leading "[番名01]" tags that would otherwise look like fansub."""
    return bool(
        _TITLE_WITH_TRAILING_EPISODE_INDEX_RE.fullmatch(tag)
        and _FANSUB_MARKER_RE.search(tag) is None
        and _SXX_EXX_RE.search(remaining)
    )


def _strip_bracketed_title_episode_index(value: str) -> str:
    """Remove the episode number from a bracketed title candidate."""
    match = _BRACKETED_TITLE_WITH_TRAILING_EPISODE_INDEX_RE.match(value.strip())
    if match is None:
        return value
    return f"{match.group('title')} {value[match.end() :]}"


def _select_cjk_underscore_alias(value: str) -> str:
    """Pick the CJK segment from underscore-separated romaji/CJK aliases.

    Corner case: some titles pack multiple aliases as "romaji_日文_中文".
    When a Latin-only segment is present, the last Han-only segment is the
    title-visible Chinese alias in the reviewed data.
    """
    if "_" not in value:
        return value

    raw_segments = value.split("_")
    segments = [_normalize_text(segment.strip(" _[]【】")) for segment in raw_segments]
    has_latin_segment = any(
        re.search(r"[A-Za-z]", segment) is not None and not _has_cjk(segment)
        for segment in segments
    )
    cjk_segments: list[str] = []
    for segment in segments:
        if _has_han(segment) and not _has_kana(segment):
            cjk_segments.append(segment)
    if len(cjk_segments) >= 2 and has_latin_segment:
        return cjk_segments[-1]
    return value


def _should_keep_latin_suffix(value: str) -> bool:
    """Decide whether a Latin suffix is title text or just romanization.

    This guards _trim_romanized_suffix. It keeps compact all-caps suffixes and
    special forms like "ACT 2", but lets long all-caps romanized tails be
    removed from CJK titles.
    """
    romanized_suffix = _find_romanized_suffix(value)
    if romanized_suffix is None:
        return True

    suffix_start, suffix = romanized_suffix
    prefix = value[:suffix_start].rstrip()
    if prefix and prefix[-1].isascii() and prefix[-1].isalpha() and _has_cjk(prefix):
        return True
    compact = re.sub(r"[^A-Za-z0-9]", "", suffix)
    if compact and compact.upper() == compact and " " in suffix:
        return False
    if compact and compact.upper() == compact and len(compact) <= 24:
        return True
    if re.fullmatch(r"(?i)act\s*\d+", suffix):
        return True
    if re.search(r"[\u3400-\u9fff][A-Za-z]+(?:\s+[A-Za-z]+){1,3}$", value):
        return True
    return False


def _strip_alt_title_suffix(value: str) -> str:
    for opening, closing in (("「", "」"), ("『", "』")):
        start = value.find(opening)
        if start == -1:
            continue
        end = value.find(closing, start + 1)
        if end == -1:
            continue
        tail = value[end + 1 :].lstrip()
        if tail and tail[0].isascii() and tail[0].isalpha():
            return value[:start].rstrip()
    return value


def _strip_trailing_alias_episode_index(value: str) -> str:
    stripped = value.rstrip()
    digit_start = len(stripped)
    while digit_start > 0 and stripped[digit_start - 1].isdigit():
        digit_start -= 1
    if len(stripped) - digit_start not in {2, 3}:
        return value
    prefix = stripped[:digit_start].rstrip()
    if prefix and _has_han(prefix[-1]):
        return prefix
    return value


def _strip_trailing_cjk_parenthesized_alias(value: str) -> str:
    stripped = value.rstrip()
    if not stripped or stripped[-1] not in {"）", ")"}:
        return value
    closing = stripped[-1]
    opening = "（" if closing == "）" else "("
    start = stripped.rfind(opening)
    if start == -1:
        return value
    content = stripped[start + 1 : -1].strip()
    if content and _has_han(content[0]):
        return stripped[:start].rstrip()
    return value


def _strip_leading_release_note(value: str) -> str:
    stripped = value.lstrip()
    if not stripped or stripped[0] not in {"（", "("}:
        return value
    closing = "）" if stripped[0] == "（" else ")"
    end = stripped.find(closing, 1)
    if end == -1:
        return value
    content = stripped[1:end]
    release_keywords = ("翻译", "翻譯", "粤语", "粵語", "字幕", "代理商")
    if any(keyword in content for keyword in release_keywords):
        return stripped[end + 1 :].lstrip()
    return value


_ROMANIZED_SUFFIX_ALLOWED_PUNCTUATION = set(" .,:;'’`()!?&~〜-")


def _find_romanized_suffix(value: str) -> tuple[int, str] | None:
    for index, char in enumerate(value):
        if not char.isspace():
            continue
        suffix = value[index:].strip()
        if _is_romanized_suffix(suffix):
            return index, suffix
    return None


def _is_romanized_suffix(value: str) -> bool:
    if not value:
        return False
    if not all(
        (char.isascii() and char.isalnum())
        or char in _ROMANIZED_SUFFIX_ALLOWED_PUNCTUATION
        for char in value
    ):
        return False
    if value[0].isalpha():
        return True
    return (
        len(value) >= 4
        and value[0].isdigit()
        and value[1].isdigit()
        and value[2] == "-"
        and value[3].isalpha()
    )
