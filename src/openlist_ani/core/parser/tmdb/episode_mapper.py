"""Episode mapping strategies for aligning fansub season/episode to TMDB.

Strategies (in execution order):
1. DirectMatch    — fansub S/E directly exist in TMDB
2. SpecialEpisode — episode==0, use LLM to match TMDB Season 0 specials
3. CourMapping    — fansub invented its own seasons based on broadcast cours
   3a. Relative   — fansub resets episode numbering per cour
   3b. Absolute   — fansub keeps accumulating episode numbers across cours
4. AbsoluteEpisode — fansub stays in S01 but accumulates episodes; TMDB has multiple seasons
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ....logger import logger
from ..cour.detector import detect_cours_from_episodes
from ..model import EpisodeMapping, SeasonInfo
from ..utils import parse_json_from_markdown

if TYPE_CHECKING:
    from ..llm.client import LLMClient
    from ..tmdb.api import TMDBClient


# ---------------------------------------------------------------------------
# Mapping context — carries all data needed by strategies
# ---------------------------------------------------------------------------


@dataclass
class MappingContext:
    """All context needed for episode mapping strategies."""

    tmdb_id: int
    fansub_season: int
    fansub_episode: int
    sorted_seasons: list[SeasonInfo]
    tmdb_client: TMDBClient
    resource_title: str = ""
    llm_client: LLMClient | None = None


# ---------------------------------------------------------------------------
# Strategy ABC
# ---------------------------------------------------------------------------


class MappingStrategy(ABC):
    @abstractmethod
    async def try_map(self, ctx: MappingContext) -> EpisodeMapping | None: ...


# ---------------------------------------------------------------------------
# 1. DirectMatchStrategy — 标准场景
# ---------------------------------------------------------------------------


class DirectMatchStrategy(MappingStrategy):
    """标准场景：fansub 的 season 和 episode 都能直接在 TMDB 上对应。"""

    async def try_map(self, ctx: MappingContext) -> EpisodeMapping | None:
        target = next(
            (s for s in ctx.sorted_seasons if s.season_number == ctx.fansub_season),
            None,
        )
        if target and 1 <= ctx.fansub_episode <= target.episode_count:
            return EpisodeMapping(
                season=ctx.fansub_season,
                episode=ctx.fansub_episode,
                strategy="direct",
            )
        return None


# ---------------------------------------------------------------------------
# 2. SpecialEpisodeStrategy — 特殊集
# ---------------------------------------------------------------------------


class SpecialEpisodeStrategy(MappingStrategy):
    """特殊集（episode==0）：调用 LLM 分析 resource_title 与 TMDB Season 0 的
    集数信息，找到最匹配的一集。无 LLM 或数据不足时回退到通用映射。"""

    async def try_map(self, ctx: MappingContext) -> EpisodeMapping | None:
        if ctx.fansub_episode != 0 and ctx.fansub_season != 0:
            return None

        has_specials = any(s.season_number == 0 for s in ctx.sorted_seasons)
        if not has_specials:
            return EpisodeMapping(
                season=0,
                episode=ctx.fansub_episode if ctx.fansub_episode > 0 else 0,
                strategy="special_passthrough",
            )

        if ctx.fansub_season == 0 and ctx.fansub_episode > 0:
            season0_info = next(
                (s for s in ctx.sorted_seasons if s.season_number == 0), None
            )
            if season0_info and ctx.fansub_episode <= season0_info.episode_count:
                return EpisodeMapping(
                    season=0,
                    episode=ctx.fansub_episode,
                    strategy="special_direct",
                )

        # Try LLM-based matching when both client and resource title are available
        llm_result = await self._try_llm_special_match(ctx)
        if llm_result is not None:
            return llm_result

        # Fallback: map to S0E1
        return EpisodeMapping(season=0, episode=1, strategy="special_fallback")

    async def _try_llm_special_match(
        self, ctx: MappingContext
    ) -> EpisodeMapping | None:
        """Attempt LLM-based special episode matching against Season 0."""
        if not ctx.llm_client or not ctx.resource_title:
            return None

        season0_episodes = await ctx.tmdb_client.get_season_episodes(ctx.tmdb_id, 0)
        if not season0_episodes:
            return None

        matched = await _match_special_episode_via_llm(
            ctx.llm_client, ctx.resource_title, season0_episodes
        )
        if matched is not None:
            return EpisodeMapping(season=0, episode=matched, strategy="special_llm")
        return None


# ---------------------------------------------------------------------------
# 3. CourMappingStrategy — fansub 按播出时间段自行分季度
# ---------------------------------------------------------------------------


class CourMappingStrategy(MappingStrategy):
    """fansub 按播出时间段自行分季度的两种场景：

    3a. 集数按季度重新计数（relative）：
        例：Oshi no Ko TMDB S1 有 35 集分 3 个 cour，fansub S03E06 → S01E29

    3b. 集数在前季基础上累加不重计（absolute）：
        例：Solo Leveling TMDB S1 有 25 集分 2 个 cour，fansub S02E14 → S01E14
    """

    async def try_map(self, ctx: MappingContext) -> EpisodeMapping | None:
        regular = [s for s in ctx.sorted_seasons if s.season_number > 0]
        if not regular:
            return None

        max_tmdb_season = max(s.season_number for s in regular)
        if ctx.fansub_season <= max_tmdb_season:
            return None

        global_cours = await self._build_global_cours(ctx, regular)
        if not global_cours:
            return None

        cour_idx = ctx.fansub_season - 1
        if not (0 <= cour_idx < len(global_cours)):
            return None

        tmdb_season, cour_start, cour_end = global_cours[cour_idx]
        season_info = next(
            (s for s in ctx.sorted_seasons if s.season_number == tmdb_season), None
        )

        logger.debug(
            f"Cour mapping: global_cours={global_cours}, "
            f"fansub=S{ctx.fansub_season:02d}E{ctx.fansub_episode:02d}, "
            f"cour_idx={cour_idx}"
        )

        # 3a. Relative — fansub resets episode numbering per cour
        # e.g. fansub S03E06 → cour3 starts at ep24, target = 24+6-1 = 29
        target_ep = cour_start + ctx.fansub_episode - 1
        if season_info and 1 <= target_ep <= season_info.episode_count:
            return EpisodeMapping(
                season=tmdb_season, episode=target_ep, strategy="cour_relative"
            )

        # 3b. Absolute — fansub keeps accumulating episode numbers
        # e.g. fansub S02E14 where 14 falls within cour2's range [13, 25]
        if (
            cour_start <= ctx.fansub_episode <= cour_end
            and season_info
            and 1 <= ctx.fansub_episode <= season_info.episode_count
        ):
            logger.debug(
                f"Cour absolute: fansub_episode={ctx.fansub_episode} "
                f"in cour range [{cour_start}, {cour_end}], treating as absolute"
            )
            return EpisodeMapping(
                season=tmdb_season,
                episode=ctx.fansub_episode,
                strategy="cour_absolute",
            )

        return None

    async def _build_global_cours(
        self,
        ctx: MappingContext,
        regular: list[SeasonInfo],
    ) -> list[tuple[int, int, int]]:
        """Build global cour list across all TMDB seasons.

        Returns:
            List of (tmdb_season_number, start_ep, end_ep) tuples.
        """
        global_cours: list[tuple[int, int, int]] = []

        for s in regular:
            if s.episode_count < 1:
                continue
            episodes = await ctx.tmdb_client.get_season_episodes(
                ctx.tmdb_id, s.season_number
            )
            if not episodes:
                logger.warning(
                    f"Cour detection: tmdb_id={ctx.tmdb_id} S{s.season_number:02d} → "
                    f"no episode data, treating as single cour"
                )
                global_cours.append((s.season_number, 1, s.episode_count))
                continue

            cours = detect_cours_from_episodes(episodes)
            logger.debug(
                f"Cour detection: tmdb_id={ctx.tmdb_id} S{s.season_number:02d} → "
                f"{len(episodes)} episodes, {len(cours)} cour(s) detected: "
                f"{[(c.start_episode, c.end_episode) for c in cours]}"
            )
            if len(cours) > 1:
                for cour in cours:
                    global_cours.append(
                        (s.season_number, cour.start_episode, cour.end_episode)
                    )
            else:
                global_cours.append((s.season_number, 1, s.episode_count))

        return global_cours


# ---------------------------------------------------------------------------
# 4. AbsoluteEpisodeStrategy — fansub 不分季，集数累计
# ---------------------------------------------------------------------------


class AbsoluteEpisodeStrategy(MappingStrategy):
    """fansub 没有重新分季度，集数一直累计，但 TMDB 分了多个季度。

    例：TMDB S1(12ep)+S2(12ep)，fansub S01E15 → S02E03
    """

    async def try_map(self, ctx: MappingContext) -> EpisodeMapping | None:
        if ctx.fansub_season == 0:
            return None

        target = next(
            (s for s in ctx.sorted_seasons if s.season_number == ctx.fansub_season),
            None,
        )
        # Only applies when fansub_season exists in TMDB (typically S01)
        # but episode exceeds that season's count.
        # If target_season is None and season > 1, the episode number is
        # relative to the fansub's own season, not absolute.
        if target is None and ctx.fansub_season > 1:
            return None
        return _map_absolute_episode(ctx.fansub_episode, ctx.sorted_seasons)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _map_absolute_episode(
    episode_abs: int, sorted_seasons: list[SeasonInfo]
) -> EpisodeMapping | None:
    """Map an absolute episode number across multiple TMDB seasons."""
    regular = [s for s in sorted_seasons if s.season_number > 0]
    current_total = 0
    for season in regular:
        range_end = current_total + season.episode_count
        if current_total < episode_abs <= range_end:
            return EpisodeMapping(
                season=season.season_number,
                episode=episode_abs - current_total,
                strategy="absolute",
            )
        current_total += season.episode_count
    return None


async def _match_special_episode_via_llm(
    llm_client: LLMClient,
    resource_title: str,
    season0_episodes: list[dict[str, Any]],
) -> int | None:
    """Use LLM to match a resource title against TMDB Season 0 (Specials)."""
    episode_info = [
        {
            "episode_number": ep.get("episode_number", 0),
            "name": ep.get("name", ""),
            "overview": (ep.get("overview") or "")[:200],
            "air_date": ep.get("air_date", ""),
        }
        for ep in season0_episodes
    ]

    system_prompt = (
        "You are an anime special episode matcher. Given a resource title from "
        "a fansub release and a list of special episodes from TMDB Season 0, "
        "determine which episode best matches the resource title.\n\n"
        "Analyze the title for clues: OVA numbers, special names, dates, etc.\n"
        'Return ONLY valid JSON: {"episode_number": <int>}\n'
        'If no confident match is found, return: {"episode_number": null}'
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": json.dumps(
                {
                    "resource_title": resource_title,
                    "special_episodes": episode_info,
                },
                ensure_ascii=False,
            ),
        },
    ]

    try:
        content = await llm_client.complete_chat(messages)
        payload = parse_json_from_markdown(content)
        if not payload:
            return None
        parsed = json.loads(payload)
        if isinstance(parsed, dict):
            ep_num = parsed.get("episode_number")
            if isinstance(ep_num, int) and ep_num >= 0:
                return ep_num
        return None
    except Exception as e:
        logger.debug(f"LLM special episode matching failed: {e}")
        return None


# ---------------------------------------------------------------------------
# Mapper — orchestrates strategies in order
# ---------------------------------------------------------------------------


class EpisodeMapper:
    def __init__(self, strategies: list[MappingStrategy] | None = None) -> None:
        self._strategies = strategies or [
            DirectMatchStrategy(),
            SpecialEpisodeStrategy(),
            CourMappingStrategy(),
            AbsoluteEpisodeStrategy(),
        ]

    async def map(self, ctx: MappingContext) -> EpisodeMapping | None:
        for strategy in self._strategies:
            result = await strategy.try_map(ctx)
            if result:
                return result
        return None
