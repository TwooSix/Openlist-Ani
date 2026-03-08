"""
Bangumi API tools for the LLM assistant.

Provides tools for fetching anime calendar, subject details,
user collections, reviews, collection management, and personalized
anime recommendations based on LLM-analyzed user profile.

Helper functions (client singleton, season utilities, user profile system)
are located in the ``helper`` subpackage.
"""

from __future__ import annotations

from typing import Any

from ...core.bangumi.model import (
    COLLECTION_TYPE_LABELS,
    BangumiBlog,
    BangumiTopic,
    CalendarDay,
    CalendarItem,
    CollectionType,
    SubjectType,
    UserCollectionEntry,
)
from ...logger import logger
from .base import BaseTool
from .helper.bangumi import (
    _get_client,
    _get_current_season,
    _season_label,
)
from .helper.profile import (
    _build_or_update_profile,
    _format_profile_summary,
)

# ================================================================
# Tool implementations
# ================================================================


class BangumiCalendarTool(BaseTool):
    """Tool for fetching the Bangumi weekly anime calendar."""

    @property
    def name(self) -> str:
        return "get_bangumi_calendar"

    @property
    def description(self) -> str:
        return (
            "Fetch the current weekly anime airing calendar from Bangumi. "
            "Returns anime titles grouped by day-of-week with ratings and "
            "air dates. Use this when the user asks about currently airing "
            "anime or today's schedule."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "weekday": {
                    "type": "integer",
                    "description": (
                        "Filter by weekday (1=Monday .. 7=Sunday). "
                        "Omit to get the full week."
                    ),
                },
            },
            "required": [],
        }

    async def execute(self, weekday: int | None = None, **kwargs) -> str:
        """Execute calendar fetch.

        Args:
            weekday: Optional weekday filter (1-7).

        Returns:
            Formatted calendar text.
        """
        client = _get_client()
        try:
            days = await client.fetch_calendar()
        except Exception as exc:
            logger.exception("BangumiCalendarTool: Failed to fetch calendar")
            return f"Failed to fetch Bangumi calendar: {exc}"

        if weekday:
            days = [d for d in days if d.weekday.id == weekday]

        return self._format_calendar(days)

    @staticmethod
    def _format_calendar(days: list[CalendarDay]) -> str:
        """Format calendar days into readable text."""
        if not days:
            return "No calendar data found for the specified day."

        lines: list[str] = ["Bangumi Weekly Anime Calendar\n"]
        for day in days:
            lines.append(f"### {day.weekday.cn} ({day.weekday.en})")
            if not day.items:
                lines.append("  (no anime)")
                continue
            for item in day.items:
                score = f"score:{item.rating.score}" if item.rating.score else "unrated"
                lines.append(
                    f"  - [{item.id}] {item.display_name} "
                    f"({score}, rank #{item.rank or 'N/A'})"
                )
            lines.append("")
        return "\n".join(lines)


class BangumiSubjectTool(BaseTool):
    """Tool for fetching detailed information about a Bangumi subject."""

    @property
    def name(self) -> str:
        return "get_bangumi_subject"

    @property
    def description(self) -> str:
        return (
            "Fetch detailed information about an anime/subject from Bangumi "
            "by ID. Returns summary, rating, tags, episode count and more. "
            "Use this when the user asks about a specific anime's details."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "subject_id": {
                    "type": "integer",
                    "description": "Bangumi subject ID",
                },
            },
            "required": ["subject_id"],
        }

    async def execute(self, subject_id: int, **kwargs) -> str:
        """Execute subject detail fetch.

        Args:
            subject_id: Bangumi subject ID.

        Returns:
            Formatted subject detail text.
        """
        client = _get_client()
        try:
            subject = await client.fetch_subject(subject_id)
        except Exception as exc:
            logger.exception(
                f"BangumiSubjectTool: Failed to fetch subject {subject_id}"
            )
            return f"Failed to fetch subject {subject_id}: {exc}"

        tags_str = ", ".join(f"{t.name}({t.count})" for t in subject.tags[:15])
        summary_text = subject.summary[:500] if subject.summary else "N/A"

        return (
            f"Subject: {subject.display_name}\n"
            f"  ID: {subject.id} | Type: {subject.type}"
            f" | Platform: {subject.platform}\n"
            f"  Date: {subject.date}"
            f" | Episodes: {subject.total_episodes}\n"
            f"  Rating: {subject.rating.score} "
            f"(rank #{subject.rating.rank}, "
            f"{subject.rating.total} votes)\n"
            f"  Tags: {tags_str}\n"
            f"  URL: {subject.url}\n\n"
            f"  Summary:\n{summary_text}"
        )


class BangumiCollectionTool(BaseTool):
    """Tool for fetching the current user's Bangumi anime collection."""

    @property
    def name(self) -> str:
        return "get_bangumi_collection"

    @property
    def description(self) -> str:
        return (
            "Fetch the current user's anime collection from Bangumi. "
            "Shows watched/watching/wish-to-watch anime with ratings and "
            "comments. Use this when the user asks about their collection "
            "or watching history."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "collection_type": {
                    "type": "integer",
                    "description": (
                        "Filter by collection type: "
                        "1=Wish, 2=Done, 3=Doing, "
                        "4=OnHold, 5=Dropped. "
                        "Omit for all types."
                    ),
                },
            },
            "required": [],
        }

    async def execute(self, collection_type: int | None = None, **kwargs) -> str:
        """Execute collection fetch.

        Args:
            collection_type: Optional CollectionType filter.

        Returns:
            Formatted collection text.
        """
        client = _get_client()
        try:
            entries = await client.fetch_user_collections(
                subject_type=SubjectType.ANIME,
                collection_type=collection_type,
            )
        except Exception as exc:
            logger.exception("BangumiCollectionTool: Failed to fetch collections")
            return f"Failed to fetch Bangumi collection: {exc}"

        if not entries:
            return "No collection entries found."

        return self._format_collections(entries)

    @staticmethod
    def _format_collections(entries: list[UserCollectionEntry]) -> str:
        """Format collection entries into readable text."""
        lines: list[str] = [f"Bangumi Collection ({len(entries)} entries)\n"]
        for entry in entries[:50]:
            name = ""
            if entry.subject:
                name = entry.subject.name_cn or entry.subject.name
            name = name or f"Subject#{entry.subject_id}"
            rate_str = f"rating:{entry.rate}" if entry.rate else "unrated"
            label = entry.collection_type_label
            lines.append(f"  - [{entry.subject_id}] {name} ({label}, {rate_str})")
            if entry.comment:
                lines.append(f"    Comment: {entry.comment}")

        if len(entries) > 50:
            lines.append(f"\n  ...and {len(entries) - 50} more entries")
        return "\n".join(lines)


class BangumiReviewsTool(BaseTool):
    """Tool for fetching and summarizing reviews/discussions for an anime."""

    @property
    def name(self) -> str:
        return "get_bangumi_reviews"

    @property
    def description(self) -> str:
        return (
            "Fetch discussion topics and blog reviews for a Bangumi anime "
            "by ID. Returns topic titles, blog summaries, and reply counts "
            "so you can summarize what other users think about this anime. "
            "Use this when the user asks about reviews or opinions on an "
            "anime."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "subject_id": {
                    "type": "integer",
                    "description": "Bangumi subject ID",
                },
            },
            "required": ["subject_id"],
        }

    async def execute(self, subject_id: int, **kwargs) -> str:
        """Execute reviews/topics fetch for a subject.

        Args:
            subject_id: Bangumi subject ID.

        Returns:
            Formatted reviews and discussions text.
        """
        client = _get_client()
        try:
            topics, blogs = await client.fetch_subject_reviews(subject_id)
        except Exception as exc:
            logger.exception(
                f"BangumiReviewsTool: Failed to fetch reviews for {subject_id}"
            )
            return f"Failed to fetch reviews for subject {subject_id}: {exc}"

        return self._format_reviews(subject_id, topics, blogs)

    @staticmethod
    def _format_reviews(
        subject_id: int,
        topics: list[BangumiTopic],
        blogs: list[BangumiBlog],
    ) -> str:
        """Format topics and blogs into readable text."""
        lines: list[str] = [f"Reviews & Discussions for Subject #{subject_id}\n"]

        if topics:
            lines.append(f"### Discussion Topics ({len(topics)})")
            for t in topics[:15]:
                lines.append(
                    f"  - {t.title} (by {t.user_nickname}, {t.replies} replies)"
                )
        else:
            lines.append("### Discussion Topics: None found")

        lines.append("")

        if blogs:
            lines.append(f"### Blog Reviews ({len(blogs)})")
            for b in blogs[:15]:
                summary = b.summary[:200] if b.summary else "No summary"
                lines.append(
                    f"  - [{b.title}] by {b.user_nickname} ({b.replies} replies)"
                )
                lines.append(f"    {summary}")
        else:
            lines.append("### Blog Reviews: None found")

        if not topics and not blogs:
            lines.append("\nNo discussions or reviews found for this anime.")
        else:
            lines.append(
                "\n---\nPlease summarize the community opinions based "
                "on the topics and blog reviews above."
            )

        return "\n".join(lines)


class BangumiCollectTool(BaseTool):
    """Tool for updating an anime's collection status and watch progress."""

    @property
    def name(self) -> str:
        return "update_bangumi_collection"

    @property
    def description(self) -> str:
        return (
            "Update an anime's collection status or watch progress in "
            "the user's Bangumi collection. ONLY supports two operations: "
            "1) Change collection type (wish/doing/done/on_hold/dropped). "
            "2) Update watch progress (ep_status = number of episodes watched). "
            "If requested episodes cannot be matched exactly, the tool will "
            "NOT execute updates and will ask for user confirmation. "
            "Do NOT use this tool to add comments, ratings, or tags "
            "— those must be done by the user directly on Bangumi."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "subject_id": {
                    "type": "integer",
                    "description": "Bangumi subject ID of the anime",
                },
                "collection_type": {
                    "type": "integer",
                    "description": (
                        "Collection type: 1=Wish, 2=Done, 3=Doing, 4=OnHold, 5=Dropped"
                    ),
                },
                "ep_status": {
                    "type": "integer",
                    "description": (
                        "Watch progress count. If user says 'watched to "
                        "episode N', set this to N. Tool will mark episodes "
                        "1..N as watched in batch."
                    ),
                },
                "episode_number": {
                    "type": "integer",
                    "description": (
                        "Single episode number to update, e.g. 28. "
                        "Use this when user mentions one specific episode."
                    ),
                },
                "episode_numbers": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": (
                        "Batch episode numbers to update, e.g. [1,2,3,4]. "
                        "Use this when user asks to mark multiple episodes."
                    ),
                },
                "episode_collection_type": {
                    "type": "integer",
                    "description": (
                        "Episode status type: 0=Remove/Clear, 1=Wish, "
                        "2=Done(watched), 3=Dropped. Defaults to 2."
                    ),
                },
            },
            "required": ["subject_id", "collection_type"],
        }

    async def execute(
        self,
        subject_id: int,
        collection_type: int,
        ep_status: int | None = None,
        episode_number: int | None = None,
        episode_numbers: list[int] | None = None,
        episode_collection_type: int = 2,
        **kwargs,
    ) -> str:
        """Execute collection update with strict safety checks.

        Args:
            subject_id: Bangumi subject ID.
            collection_type: Collection type (1-5).
            ep_status: Optional "watched to episode N" progress.
            episode_number: Optional single episode number.
            episode_numbers: Optional list of episode numbers.
            episode_collection_type: Episode status type (0/1/2/3).

        Returns:
            Success or error message.
        """
        error = self._validate_params(
            collection_type,
            ep_status,
            episode_number,
            episode_numbers,
            episode_collection_type,
        )
        if error:
            return error

        requested_episode_numbers = self._resolve_episode_numbers(
            ep_status,
            episode_number,
            episode_numbers,
        )

        client = _get_client()
        try:
            (
                matched_episode_ids,
                missing_episode_numbers,
                rollback_episode_ids,
            ) = await self._pre_validate_episodes(
                client,
                subject_id,
                requested_episode_numbers,
                ep_status,
                episode_number,
                episode_numbers,
            )
            if isinstance(matched_episode_ids, str):
                return matched_episode_ids

            await self._apply_collection_updates(
                client,
                subject_id,
                collection_type,
                ep_status,
                requested_episode_numbers,
                matched_episode_ids,
                rollback_episode_ids,
                episode_collection_type,
            )
        except Exception as exc:
            logger.exception(
                "BangumiCollectTool: Failed to update collection "
                f"for subject {subject_id}"
            )
            return f"Failed to update collection for subject {subject_id}: {exc}"

        return self._format_result(
            subject_id,
            collection_type,
            requested_episode_numbers,
            matched_episode_ids,
            missing_episode_numbers,
            rollback_episode_ids,
            ep_status,
        )

    @staticmethod
    def _validate_params(
        collection_type: int,
        ep_status: int | None,
        episode_number: int | None,
        episode_numbers: list[int] | None,
        episode_collection_type: int,
    ) -> str | None:
        """Validate input parameters.

        Returns:
            Error message string if invalid, None if valid.
        """
        valid_types = {t.value for t in CollectionType}
        if collection_type not in valid_types:
            return (
                f"Invalid collection type: {collection_type}. "
                f"Valid: 1=Wish, 2=Done, 3=Doing, 4=OnHold, 5=Dropped"
            )
        if ep_status is not None and ep_status < 0:
            return f"Invalid ep_status: {ep_status}. Must be >= 0."
        if episode_number is not None and episode_number <= 0:
            return f"Invalid episode_number: {episode_number}. Must be >= 1."
        if episode_numbers is not None and any(n <= 0 for n in episode_numbers):
            return "Invalid episode_numbers: all values must be >= 1."
        if episode_collection_type not in {0, 1, 2, 3}:
            return (
                f"Invalid episode_collection_type: {episode_collection_type}. "
                "Valid: 0=Remove, 1=Wish, 2=Done, 3=Dropped"
            )
        return None

    @staticmethod
    def _resolve_episode_numbers(
        ep_status: int | None,
        episode_number: int | None,
        episode_numbers: list[int] | None,
    ) -> list[int]:
        """Determine the list of requested episode numbers.

        Returns:
            Sorted list of unique episode numbers to update.
        """
        if episode_numbers:
            return sorted(set(episode_numbers))
        if episode_number is not None:
            return [episode_number]
        if ep_status is not None and ep_status > 0:
            return list(range(1, ep_status + 1))
        return []

    @staticmethod
    def _build_number_to_id_map(episodes: list[dict]) -> dict[int, int]:
        """Build mapping from episode number to episode ID.

        Args:
            episodes: Raw episode dicts from API.

        Returns:
            Dict mapping episode number to episode ID.
        """
        number_to_id: dict[int, int] = {}
        for ep in episodes:
            number = ep.get("ep") or ep.get("sort")
            episode_id = ep.get("id")
            if not number or not episode_id:
                continue
            try:
                episode_number_value = int(number)
            except (TypeError, ValueError):
                continue
            if episode_number_value > 0:
                number_to_id[episode_number_value] = int(episode_id)
        return number_to_id

    async def _pre_validate_episodes(
        self,
        client: Any,
        subject_id: int,
        requested_episode_numbers: list[int],
        ep_status: int | None,
        episode_number: int | None,
        episode_numbers: list[int] | None,
    ) -> tuple[list[int] | str, list[int], list[int]]:
        """Pre-validate requested episodes and compute rollback.

        Returns:
            Tuple of (matched_ids_or_error, missing_numbers, rollback_ids).
            If matched_ids_or_error is a string, it's an error message.
        """
        matched_episode_ids: list[int] = []
        missing_episode_numbers: list[int] = []
        rollback_episode_ids: list[int] = []

        if not requested_episode_numbers:
            return matched_episode_ids, missing_episode_numbers, rollback_episode_ids

        episodes = await client.fetch_subject_episodes(
            subject_id=subject_id,
            episode_type=0,
        )
        number_to_id = self._build_number_to_id_map(episodes)
        available_numbers = sorted(number_to_id)
        max_available = available_numbers[-1] if available_numbers else 0

        for num in requested_episode_numbers:
            eid = number_to_id.get(num)
            if eid is not None:
                matched_episode_ids.append(eid)
            else:
                missing_episode_numbers.append(num)

        if missing_episode_numbers:
            return (
                (
                    f"⚠️ MISMATCH — subject {subject_id} only has "
                    f"episodes 1-{max_available} on Bangumi, but "
                    f"requested up to episode "
                    f"{requested_episode_numbers[-1]}. "
                    f"Unmatched: {missing_episode_numbers}. "
                    "ACTION REQUIRED: Tell the user about this "
                    "discrepancy and ask them to clarify. "
                    "DO NOT call this tool again with modified "
                    "episode numbers."
                ),
                missing_episode_numbers,
                rollback_episode_ids,
            )

        if not matched_episode_ids:
            return (
                (
                    f"⚠️ MISMATCH — subject {subject_id}: no valid "
                    "episodes matched. ACTION REQUIRED: Relay this to "
                    "the user and ask for correct episode numbers. "
                    "DO NOT retry with different parameters."
                ),
                missing_episode_numbers,
                rollback_episode_ids,
            )

        # Support rollback when user explicitly sets progress via ep_status.
        if ep_status is not None and episode_number is None and not episode_numbers:
            rollback_episode_ids = await self._compute_rollback(
                client,
                subject_id,
                ep_status,
                number_to_id,
            )

        return matched_episode_ids, missing_episode_numbers, rollback_episode_ids

    @staticmethod
    async def _compute_rollback(
        client: Any,
        subject_id: int,
        ep_status: int,
        number_to_id: dict[int, int],
    ) -> list[int]:
        """Compute episode IDs to rollback (clear) when lowering watch progress.

        Returns:
            List of episode IDs to set as uncollected.
        """
        entries = await client.fetch_user_collections(
            subject_type=SubjectType.ANIME,
        )
        current_entry = next(
            (entry for entry in entries if entry.subject_id == subject_id),
            None,
        )
        current_ep_status = current_entry.ep_status if current_entry else 0
        if current_ep_status <= ep_status:
            return []
        rollback_numbers = list(range(ep_status + 1, current_ep_status + 1))
        return [number_to_id[num] for num in rollback_numbers if num in number_to_id]

    @staticmethod
    async def _apply_collection_updates(
        client: Any,
        subject_id: int,
        collection_type: int,
        ep_status: int | None,
        requested_episode_numbers: list[int],
        matched_episode_ids: list[int],
        rollback_episode_ids: list[int],
        episode_collection_type: int,
    ) -> None:
        """Apply collection and episode updates to Bangumi API.

        Args:
            client: BangumiClient instance.
            subject_id: Bangumi subject ID.
            collection_type: Collection type (1-5).
            ep_status: Optional watched-to progress.
            requested_episode_numbers: Resolved episode numbers.
            matched_episode_ids: Validated episode IDs.
            rollback_episode_ids: Episode IDs to clear.
            episode_collection_type: Episode status type.
        """
        await client.post_user_collection(
            subject_id=subject_id,
            collection_type=collection_type,
            ep_status=None if requested_episode_numbers else ep_status,
        )
        if matched_episode_ids:
            await client.patch_subject_episode_collections(
                subject_id=subject_id,
                episode_ids=matched_episode_ids,
                collection_type=episode_collection_type,
            )
        if rollback_episode_ids:
            await client.patch_subject_episode_collections(
                subject_id=subject_id,
                episode_ids=rollback_episode_ids,
                collection_type=0,
            )

    @staticmethod
    def _format_result(
        subject_id: int,
        collection_type: int,
        requested_episode_numbers: list[int],
        matched_episode_ids: list[int],
        missing_episode_numbers: list[int],
        rollback_episode_ids: list[int],
        ep_status: int | None,
    ) -> str:
        """Format the success result message.

        Returns:
            Formatted result string.
        """
        type_label = COLLECTION_TYPE_LABELS.get(collection_type, "unknown")
        parts = [f"Successfully updated subject {subject_id} -> {type_label}"]
        if requested_episode_numbers:
            parts.append(f"Episode updates: {len(matched_episode_ids)} matched")
            parts.append(f"Requested episodes: {requested_episode_numbers}")
            if missing_episode_numbers:
                parts.append(f"Not found: {missing_episode_numbers}")
            if rollback_episode_ids:
                parts.append(f"Rollback cleared episodes: {len(rollback_episode_ids)}")
        elif ep_status is not None:
            parts.append(f"Episodes watched: {ep_status}")
        return " | ".join(parts)


class BangumiRecommendTool(BaseTool):
    """Tool for recommending current-season anime based on user profile."""

    @property
    def name(self) -> str:
        return "recommend_anime"

    @property
    def description(self) -> str:
        return (
            "Recommend current-season anime based on the user's Bangumi "
            "collection and LLM-analyzed user profile. Returns a list of "
            "airing anime the user hasn't watched yet, together with a "
            "user profile summary for personalized recommendations. "
            "Use this when the user asks for anime recommendations."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {},
            "required": [],
        }

    async def execute(self, **kwargs) -> str:
        """Execute anime recommendation.

        Returns:
            Context string containing user profile + candidate anime list
            for the LLM to generate personalized recommendations.
        """
        client = _get_client()

        # 1) Build / load user profile
        try:
            profile = await _build_or_update_profile(client)
        except Exception as exc:
            logger.exception("BangumiRecommendTool: Failed to build profile")
            return f"Failed to build user profile: {exc}"

        # 2) Fetch calendar for current-season candidates
        try:
            calendar = await client.fetch_calendar()
        except Exception as exc:
            logger.exception("BangumiRecommendTool: Failed to fetch calendar")
            return f"Failed to fetch calendar: {exc}"

        # 3) Build exclusion set and filter candidates
        collected_ids = set(profile.get("synced_subject_ids", []))
        candidates = self._filter_candidates(calendar, collected_ids)

        return self._format_recommendation(profile, candidates, collected_ids)

    @staticmethod
    def _filter_candidates(
        calendar: list[CalendarDay],
        collected_ids: set[int],
    ) -> list[CalendarItem]:
        """Filter calendar items to exclude already-collected anime.

        Args:
            calendar: Weekly anime calendar.
            collected_ids: IDs already in user collection.

        Returns:
            Filtered list of candidate anime.
        """
        return [
            item
            for day in calendar
            for item in day.items
            if item.id not in collected_ids
        ]

    @staticmethod
    def _format_recommendation(
        profile: dict[str, Any],
        candidates: list[CalendarItem],
        collected_ids: set[int],
    ) -> str:
        """Format recommendation context for LLM.

        Args:
            profile: User profile dict.
            candidates: Candidate anime list.
            collected_ids: IDs in user collection.

        Returns:
            Formatted recommendation context string.
        """
        year, month = _get_current_season()
        season_str = _season_label(month)

        lines: list[str] = [
            f"Anime Recommendation Context ({year} {season_str})\n",
            _format_profile_summary(profile),
            f"\n## Candidate Anime ({len(candidates)} titles, "
            f"excluding {len(collected_ids)} collected)\n",
        ]

        if not candidates:
            lines.append(
                "All currently airing anime are already in your "
                "collection. You're all caught up!"
            )
        else:
            for item in candidates:
                score = f"score:{item.rating.score}" if item.rating.score else "unrated"
                lines.append(
                    f"  - [{item.id}] {item.display_name} "
                    f"({score}, rank #{item.rank or 'N/A'}, "
                    f"air: {item.air_date})"
                )

        lines.append(
            "\n---\n"
            "IMPORTANT RULES:\n"
            "1. You MUST ONLY recommend anime from the Candidate Anime "
            "list above. Do NOT recommend any anime not in that list.\n"
            "2. The following Bangumi subject IDs are already in the "
            "user's collection — NEVER recommend these:\n"
            f"   {sorted(collected_ids)}\n"
            "3. Always include the Bangumi subject ID [id] for each "
            "recommendation so the user can look it up.\n"
            "4. Generate personalized recommendations with reasons "
            "based on the user profile above."
        )
        return "\n".join(lines)
