"""Assistant long-term memory management with file-first persistence."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from openai import AsyncOpenAI

from ..logger import logger


@dataclass(slots=True)
class MemoryFact:
    """A structured long-term memory fact."""

    content: str
    category: str
    confidence: float


@dataclass(slots=True)
class MemorySnapshot:
    """The current memory snapshot for one user."""

    summary: str
    facts: list[MemoryFact]
    recent_messages: list[dict[str, str]]
    daily_notes: str
    related_sessions: list[str]


class AssistantMemoryManager:
    """Manage short-term and long-term memory using transparent local files."""

    _EMPTY_LIST_MARKER = "- None"
    _EMPTY_SUMMARY = "None"
    _NO_RECENT_DAILY_NOTES = "No recent daily notes."
    _INVALID_MEMORY_KEY_MSG = "Invalid memory key"
    _MEMORY_KEY_SEGMENT_PATTERN = re.compile(r"[A-Za-z0-9_-]+")

    MEMORY_SYSTEM_PROMPT = """You are a long-term memory curator for a chat assistant.

Compress the new dialogue into durable memory that will remain useful in future conversations.
Return JSON only.

Output format:
{
  "summary": "A concise summary under 180 words describing ongoing goals, context, and unresolved follow-ups",
  "facts": [
    {
      "content": "A stable and reusable fact about the user or their work",
      "category": "preference | constraint | identity | project_state | workflow | general",
      "confidence": 0.0
    }
    ],
    "session_summary": "A short archive summary for retrieving this session later",
    "session_keywords": ["keyword1", "keyword2"]
}

Rules:
1. Keep only information that improves future responses.
2. Facts should capture durable preferences, identity details, constraints, workflow habits, or important project state.
3. Do not store transient small talk, temporary emotions, or unresolved speculation.
4. Merge duplicates and near-duplicates. Return at most 12 facts.
5. Confidence must be between 0.0 and 1.0.
6. Write the summary in the user's language from the new dialogue when it is clear.
7. If there is no durable fact, return an empty facts array."""

    SESSION_TITLE_PROMPT = """Generate a short file-friendly session title for this conversation.
Return plain text only.
Requirements:
- 3 to 8 words
- lowercase letters, digits, spaces, or hyphens only
- reflect the main topic accurately"""

    MEMORY_CONTEXT_PROMPT = """The following file-based memory may help answer the current user message.
If any memory conflicts with the current message, trust the current message.

Long-term summary:
{summary}

Relevant long-term facts:
{facts}

Daily notes from today and yesterday:
{daily_notes}

Relevant archived sessions:
{related_sessions}"""

    _TOKEN_PATTERN = re.compile(r"\w+|[\u4e00-\u9fff]+", re.ASCII)
    _SESSION_STALE_AFTER = timedelta(hours=6)
    _CATEGORY_BONUS = {
        "constraint": 2.5,
        "project_state": 2.0,
        "workflow": 1.5,
        "preference": 1.0,
        "identity": 1.0,
        "general": 0.0,
    }

    def __init__(
        self,
        client: AsyncOpenAI | None,
        model: str,
        recent_message_limit: int,
        refresh_interval_messages: int = 6,
        fact_limit: int = 12,
        base_dir: Path | None = None,
    ):
        self._client = client
        self._model = model
        self._recent_message_limit = recent_message_limit
        self._refresh_interval_messages = refresh_interval_messages
        self._fact_limit = fact_limit
        self._base_dir = base_dir or (Path.cwd() / "data" / "assistant_memory")

    async def build_memory_messages(
        self,
        memory_key: str | None,
        current_message: str | None = None,
    ) -> list[dict[str, str]]:
        """Build the memory preamble injected into the main conversation."""
        if not memory_key:
            return []

        snapshot = await self.load_snapshot(memory_key)
        if (
            not snapshot.summary
            and not snapshot.facts
            and not snapshot.daily_notes
            and not snapshot.related_sessions
        ):
            return []

        relevant_facts = self._select_relevant_facts(snapshot.facts, current_message)
        facts_text = (
            "\n".join(
                f"- [{fact.category}] {fact.content} (confidence={fact.confidence:.2f})"
                for fact in relevant_facts
            )
            or self._EMPTY_LIST_MARKER
        )
        daily_notes = snapshot.daily_notes.strip() or self._NO_RECENT_DAILY_NOTES
        related_sessions = (
            "\n".join(snapshot.related_sessions) or self._EMPTY_LIST_MARKER
        )

        return [
            {
                "role": "system",
                "content": self.MEMORY_CONTEXT_PROMPT.format(
                    summary=snapshot.summary or self._EMPTY_SUMMARY,
                    facts=facts_text,
                    daily_notes=daily_notes,
                    related_sessions=related_sessions,
                ),
            }
        ]

    async def load_recent_history(self, memory_key: str | None) -> list[dict[str, str]]:
        """Load the active session transcript tail for short-term context."""
        if not memory_key:
            return []

        session_path = self._get_active_session_path(memory_key)
        if session_path is None or not session_path.exists():
            return []
        messages = await asyncio.to_thread(self._parse_session_messages, session_path)
        return messages[-self._recent_message_limit :]

    async def clear_memory(self, memory_key: str | None) -> None:
        """Clear all persisted memory files for one user."""
        if not memory_key:
            return

        user_dir = self._get_user_dir(memory_key)
        if not user_dir.exists():
            return

        await asyncio.to_thread(self._remove_user_dir, user_dir)

    async def remember_turn(
        self,
        memory_key: str | None,
        user_message: str,
        assistant_message: str,
    ) -> None:
        """Persist one turn into session files, daily notes, and long-term memory."""
        if not memory_key:
            return

        self._ensure_user_dirs(memory_key)
        session_path = await self._ensure_active_session(memory_key, user_message)
        self._append_session_turn(session_path, user_message, assistant_message)
        self._append_daily_log(memory_key, user_message, assistant_message)

        recent_messages = self._parse_session_messages(session_path)
        if len(recent_messages) < 2:
            return

        await self._refresh_memory(
            memory_key,
            session_path,
            recent_messages[-self._refresh_interval_messages :],
        )
        logger.debug("Assistant memory refreshed for file-backed key {}", memory_key)

    async def load_snapshot(self, memory_key: str) -> MemorySnapshot:
        """Load the full file-backed memory snapshot for one user."""
        summary, facts = self._read_memory_file(memory_key)
        recent_messages = await self.load_recent_history(memory_key)
        daily_notes = self._read_recent_daily_notes(memory_key)
        related_sessions = self._load_relevant_session_summaries(
            memory_key,
            recent_messages[-1]["content"] if recent_messages else None,
        )
        return MemorySnapshot(
            summary=summary,
            facts=facts,
            recent_messages=recent_messages,
            daily_notes=daily_notes,
            related_sessions=related_sessions,
        )

    async def _refresh_memory(
        self,
        memory_key: str,
        session_path: Path,
        recent_messages: list[dict[str, str]],
    ) -> None:
        """Refresh MEMORY.md from the newest conversation turns."""
        if self._client is None:
            logger.warning("OpenAI client unavailable, skip assistant memory refresh")
            return

        current_summary, current_facts = self._read_memory_file(memory_key)
        user_prompt = self._build_refresh_prompt(
            current_summary,
            current_facts,
            recent_messages,
        )

        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": self.MEMORY_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={"type": "json_object"},
                tools=None,
            )
            payload = json.loads(response.choices[0].message.content or "{}")
        except Exception:
            logger.exception("Failed to refresh assistant memory for {}", memory_key)
            return

        summary = str(payload.get("summary", "")).strip()
        facts = self._merge_facts(
            current_facts,
            self._sanitize_facts(payload.get("facts", [])),
        )
        self._write_memory_file(memory_key, summary, facts)
        session_summary = str(payload.get("session_summary", "")).strip()
        session_keywords = self._sanitize_keywords(payload.get("session_keywords", []))
        self._upsert_session_index(
            memory_key,
            session_path,
            session_summary,
            session_keywords,
        )

    def _build_refresh_prompt(
        self,
        current_summary: str,
        current_facts: list[MemoryFact],
        recent_messages: list[dict[str, str]],
    ) -> str:
        """Build the prompt used to refresh long-term memory."""
        existing_facts = (
            "\n".join(
                f"- [{fact.category}] {fact.content} (confidence={fact.confidence:.2f})"
                for fact in current_facts
            )
            or self._EMPTY_LIST_MARKER
        )
        dialogue = "\n".join(
            f"{message['role']}: {message['content']}" for message in recent_messages
        )
        return (
            "Update the long-term memory using the existing file memory and the recent dialogue.\n\n"
            f"Existing summary:\n{current_summary or 'None'}\n\n"
            f"Existing facts:\n{existing_facts}\n\n"
            f"Recent dialogue:\n{dialogue}"
        )

    async def _ensure_active_session(
        self,
        memory_key: str,
        user_message: str,
    ) -> Path:
        """Return the latest active session file or create a new one."""
        sessions_dir = self._get_sessions_dir(memory_key)
        sessions_dir.mkdir(parents=True, exist_ok=True)

        existing_sessions = sorted(
            sessions_dir.glob("*.md"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        if existing_sessions:
            newest_session = existing_sessions[0]
            last_modified = datetime.fromtimestamp(newest_session.stat().st_mtime)
            if datetime.now() - last_modified <= self._SESSION_STALE_AFTER:
                return newest_session

        session_title = await self._generate_session_title(user_message)
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        file_name = f"{timestamp}-{self._slugify(session_title)[:48]}.md"
        session_path = sessions_dir / file_name
        session_path.write_text(
            "\n".join(
                [
                    f"# Session: {session_title}",
                    f"- started_at: {datetime.now().isoformat(timespec='seconds')}",
                    f"- memory_key: {memory_key}",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        return session_path

    async def _generate_session_title(self, user_message: str) -> str:
        """Generate a concise file-friendly session title."""
        fallback_title = self._slugify(user_message)[:48] or "session"
        if self._client is None:
            return fallback_title

        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": self.SESSION_TITLE_PROMPT},
                    {"role": "user", "content": user_message},
                ],
                tools=None,
            )
            raw_title = (response.choices[0].message.content or "").strip()
        except Exception:
            logger.exception("Failed to generate session title")
            return fallback_title

        if raw_title.startswith("{") or raw_title.startswith("["):
            return fallback_title

        cleaned_title = self._slugify(raw_title)
        if not cleaned_title:
            return fallback_title
        return cleaned_title[:48]

    def _append_session_turn(
        self,
        session_path: Path,
        user_message: str,
        assistant_message: str,
    ) -> None:
        """Append one full turn to the active session transcript."""
        turn_index = self._count_session_turns(session_path) + 1
        with session_path.open("a", encoding="utf-8") as file_handle:
            file_handle.write(
                "\n".join(
                    [
                        f"## Turn {turn_index}",
                        "### User",
                        user_message,
                        "",
                        "### Assistant",
                        assistant_message,
                        "",
                    ]
                )
            )

    def _append_daily_log(
        self,
        memory_key: str,
        user_message: str,
        assistant_message: str,
    ) -> None:
        """Append the turn to the per-day markdown log."""
        daily_log_path = (
            self._get_daily_dir(memory_key) / f"{datetime.now():%Y-%m-%d}.md"
        )
        if not daily_log_path.exists():
            daily_log_path.write_text(
                f"# Daily Memory Log - {datetime.now():%Y-%m-%d}\n\n",
                encoding="utf-8",
            )

        timestamp = datetime.now().strftime("%H:%M:%S")
        with daily_log_path.open("a", encoding="utf-8") as file_handle:
            file_handle.write(
                "\n".join(
                    [
                        f"## {timestamp}",
                        f"- User: {user_message}",
                        f"- Assistant: {assistant_message}",
                        "",
                    ]
                )
            )
        self._compact_daily_log_if_needed(daily_log_path)

    def _read_memory_file(self, memory_key: str) -> tuple[str, list[MemoryFact]]:
        """Read MEMORY.md for one user."""
        memory_path = self._get_memory_file(memory_key)
        if not memory_path.exists():
            return "", []

        content = memory_path.read_text(encoding="utf-8")
        summary = self._read_markdown_section(content, "Summary")
        facts_section = self._read_markdown_section(content, "Facts")
        facts = self._parse_fact_lines(facts_section)
        return summary.strip(), facts

    def _write_memory_file(
        self,
        memory_key: str,
        summary: str,
        facts: list[MemoryFact],
    ) -> None:
        """Write MEMORY.md for one user."""
        memory_path = self._get_memory_file(memory_key)
        facts_text = (
            "\n".join(
                f"- [{fact.category}|{fact.confidence:.2f}] {fact.content}"
                for fact in facts
            )
            or self._EMPTY_LIST_MARKER
        )
        memory_path.write_text(
            "\n".join(
                [
                    "# Long-Term Memory",
                    "",
                    "## Summary",
                    summary or self._EMPTY_SUMMARY,
                    "",
                    "## Facts",
                    facts_text,
                    "",
                ]
            ),
            encoding="utf-8",
        )

    def _read_recent_daily_notes(self, memory_key: str) -> str:
        """Read today and yesterday daily logs for one user."""
        notes: list[str] = []
        for offset in (1, 0):
            target_date = datetime.now() - timedelta(days=offset)
            daily_path = self._get_daily_dir(memory_key) / f"{target_date:%Y-%m-%d}.md"
            if daily_path.exists():
                notes.append(daily_path.read_text(encoding="utf-8").strip())
        return "\n\n".join(note for note in notes if note)

    def _load_relevant_session_summaries(
        self,
        memory_key: str,
        current_message: str | None,
        limit: int = 3,
    ) -> list[str]:
        """Load the most relevant archived session summaries from INDEX.md."""
        index_path = self._get_sessions_dir(memory_key) / "INDEX.md"
        if not index_path.exists():
            return []

        active_session = self._get_active_session_path(memory_key)
        active_name = active_session.name if active_session is not None else None
        entries: list[tuple[float, str]] = []
        for line in index_path.read_text(encoding="utf-8").splitlines():
            if not line.startswith("- "):
                continue
            parts = [part.strip() for part in line[2:].split(" | ")]
            if len(parts) < 4:
                continue
            file_name = parts[0]
            if active_name and file_name == active_name:
                continue
            summary = parts[2].removeprefix("summary: ").strip()
            keywords = parts[3].removeprefix("keywords: ").strip()
            combined_text = f"{summary} {keywords}".strip()
            score = self._score_text_relevance(combined_text, current_message)
            entries.append((score, f"- {file_name}: {summary} | keywords: {keywords}"))

        entries.sort(key=lambda item: item[0], reverse=True)
        return [entry for _, entry in entries[:limit] if entry]

    def _get_active_session_path(self, memory_key: str) -> Path | None:
        """Return the newest session file if one exists."""
        sessions_dir = self._get_sessions_dir(memory_key)
        if not sessions_dir.exists():
            return None

        sessions = sorted(
            sessions_dir.glob("*.md"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        return sessions[0] if sessions else None

    def _parse_session_messages(self, session_path: Path) -> list[dict[str, str]]:
        """Parse the markdown session transcript into chat messages."""
        lines = session_path.read_text(encoding="utf-8").splitlines()
        messages: list[dict[str, str]] = []
        current_role: str | None = None
        current_lines: list[str] = []

        def flush_message() -> None:
            nonlocal current_role, current_lines
            if current_role is None:
                current_lines = []
                return
            content = "\n".join(current_lines).strip()
            if content:
                messages.append({"role": current_role, "content": content})
            current_role = None
            current_lines = []

        for line in lines:
            if line == "### User":
                flush_message()
                current_role = "user"
                continue
            if line == "### Assistant":
                flush_message()
                current_role = "assistant"
                continue
            if current_role is not None:
                current_lines.append(line)

        flush_message()
        return messages

    def _upsert_session_index(
        self,
        memory_key: str,
        session_path: Path,
        session_summary: str,
        session_keywords: list[str],
    ) -> None:
        """Upsert one session summary entry into sessions/INDEX.md."""
        index_path = self._get_sessions_dir(memory_key) / "INDEX.md"
        existing_entries: dict[str, str] = {}
        if index_path.exists():
            for line in index_path.read_text(encoding="utf-8").splitlines():
                if line.startswith("- "):
                    file_name = line[2:].split(" | ", 1)[0].strip()
                    existing_entries[file_name] = line

        summary_text = session_summary or "No summary yet"
        keywords_text = ", ".join(session_keywords) or "none"
        existing_entries[session_path.name] = (
            f"- {session_path.name} | updated_at: "
            f"{datetime.now().isoformat(timespec='seconds')} | "
            f"summary: {summary_text} | keywords: {keywords_text}"
        )

        lines = ["# Session Index", ""] + [
            existing_entries[key]
            for key in sorted(existing_entries.keys(), reverse=True)
        ]
        index_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _compact_daily_log_if_needed(self, daily_log_path: Path) -> None:
        """Compact older daily log entries while keeping recent raw turns readable."""
        content = daily_log_path.read_text(encoding="utf-8")
        entries = self._extract_daily_entries(content)
        if len(entries) <= 10:
            return

        header = f"# Daily Memory Log - {daily_log_path.stem}"
        older_entries = entries[:-6]
        recent_entries = entries[-6:]
        condensed_lines = [
            f"- {entry['timestamp']} | {entry['summary']}" for entry in older_entries
        ]
        rebuilt_recent_entries = []
        for entry in recent_entries:
            rebuilt_recent_entries.extend(entry["raw_lines"])

        rebuilt_content = "\n".join(
            [
                header,
                "",
                "## Condensed Notes",
                *condensed_lines,
                "",
                *rebuilt_recent_entries,
                "",
            ]
        )
        daily_log_path.write_text(rebuilt_content, encoding="utf-8")

    def _extract_daily_entries(self, content: str) -> list[dict[str, object]]:
        """Extract timestamped entries from one daily markdown log."""
        entries: list[dict[str, object]] = []
        current_lines: list[str] = []
        current_timestamp: str | None = None

        def flush_entry() -> None:
            nonlocal current_timestamp, current_lines
            if current_timestamp is None:
                current_lines = []
                return
            user_line = next(
                (
                    line.removeprefix("- User: ").strip()
                    for line in current_lines
                    if line.startswith("- User: ")
                ),
                "",
            )
            assistant_line = next(
                (
                    line.removeprefix("- Assistant: ").strip()
                    for line in current_lines
                    if line.startswith("- Assistant: ")
                ),
                "",
            )
            summary = f"User: {user_line[:60]} | Assistant: {assistant_line[:60]}"
            entries.append(
                {
                    "timestamp": current_timestamp,
                    "summary": summary,
                    "raw_lines": [f"## {current_timestamp}", *current_lines, ""],
                }
            )
            current_timestamp = None
            current_lines = []

        for line in content.splitlines()[1:]:
            if line.startswith("## ") and line != "## Condensed Notes":
                flush_entry()
                current_timestamp = line.removeprefix("## ").strip()
                continue
            if current_timestamp is not None:
                current_lines.append(line)

        flush_entry()
        return entries

    def _count_session_turns(self, session_path: Path) -> int:
        """Count how many turns already exist in a session transcript."""
        return session_path.read_text(encoding="utf-8").count("## Turn ")

    def _parse_fact_lines(self, facts_section: str) -> list[MemoryFact]:
        """Parse the Facts section from MEMORY.md."""
        facts: list[MemoryFact] = []
        for line in facts_section.splitlines():
            stripped_line = line.strip()
            if (
                not stripped_line.startswith("- ")
                or stripped_line == self._EMPTY_LIST_MARKER
            ):
                continue

            match = re.match(r"- \[(.+?)\|([0-9.]+)\] (.+)", stripped_line)
            if match is None:
                facts.append(
                    MemoryFact(
                        content=stripped_line[2:].strip(),
                        category="general",
                        confidence=0.5,
                    )
                )
                continue

            category, confidence, content = match.groups()
            facts.append(
                MemoryFact(
                    content=content.strip(),
                    category=category.strip(),
                    confidence=self._clamp_confidence(confidence),
                )
            )
        return facts

    def _sanitize_facts(self, raw_facts: object) -> list[MemoryFact]:
        """Normalize model-produced facts into structured memory entries."""
        if not isinstance(raw_facts, list):
            return []

        facts: list[MemoryFact] = []
        seen: set[str] = set()
        for item in raw_facts:
            fact = self._coerce_fact(item)
            normalized_content = fact.content.casefold()
            if not normalized_content or normalized_content in seen:
                continue
            seen.add(normalized_content)
            facts.append(fact)
            if len(facts) >= self._fact_limit:
                break
        return facts

    def _merge_facts(
        self,
        existing_facts: list[MemoryFact],
        new_facts: list[MemoryFact],
    ) -> list[MemoryFact]:
        """Merge new facts with existing ones while decaying stale entries."""
        merged: dict[str, MemoryFact] = {
            fact.content.casefold(): fact for fact in existing_facts if fact.content
        }
        refreshed_keys: set[str] = set()

        for fact in new_facts:
            if not fact.content:
                continue
            key = fact.content.casefold()
            refreshed_keys.add(key)
            current_fact = merged.get(key)
            if current_fact is None or fact.confidence >= current_fact.confidence:
                merged[key] = fact

        for key, fact in list(merged.items()):
            if key in refreshed_keys:
                continue
            decayed_confidence = round(max(0.0, fact.confidence - 0.1), 2)
            if decayed_confidence < 0.35:
                merged.pop(key)
                continue
            merged[key] = MemoryFact(
                content=fact.content,
                category=fact.category,
                confidence=decayed_confidence,
            )

        return sorted(
            merged.values(),
            key=lambda fact: (fact.confidence, fact.category != "general"),
            reverse=True,
        )[: self._fact_limit]

    def _sanitize_keywords(self, raw_keywords: object) -> list[str]:
        """Normalize session keywords returned by the model."""
        if not isinstance(raw_keywords, list):
            return []
        keywords: list[str] = []
        seen: set[str] = set()
        for item in raw_keywords:
            keyword = str(item).strip().lower()
            if not keyword or keyword in seen:
                continue
            seen.add(keyword)
            keywords.append(keyword)
            if len(keywords) >= 8:
                break
        return keywords

    def _coerce_fact(self, raw_fact: object) -> MemoryFact:
        """Convert a raw model value into a structured memory fact."""
        if isinstance(raw_fact, dict):
            return MemoryFact(
                content=str(raw_fact.get("content", "")).strip(),
                category=str(raw_fact.get("category", "general")).strip() or "general",
                confidence=self._clamp_confidence(raw_fact.get("confidence", 0.5)),
            )

        return MemoryFact(
            content=str(raw_fact).strip(),
            category="general",
            confidence=0.5,
        )

    def _select_relevant_facts(
        self,
        facts: list[MemoryFact],
        current_message: str | None,
    ) -> list[MemoryFact]:
        """Select memory facts most relevant to the current user message."""
        if not facts:
            return []

        if not current_message:
            return sorted(
                facts,
                key=lambda fact: (fact.confidence, fact.category != "general"),
                reverse=True,
            )[:6]

        return sorted(
            facts,
            key=lambda fact: self._score_fact_relevance(fact, current_message),
            reverse=True,
        )[:6]

    def _score_fact_relevance(self, fact: MemoryFact, current_message: str) -> float:
        """Compute a lightweight relevance score for one fact."""
        overlap_score = self._score_text_relevance(fact.content, current_message)
        category_bonus = self._CATEGORY_BONUS.get(fact.category, 0.0)
        return overlap_score + (fact.confidence * 2.0) + category_bonus

    def _score_text_relevance(
        self, reference_text: str, current_message: str | None
    ) -> float:
        """Score free text against the current user message."""
        if not current_message:
            return 0.0
        reference_tokens = set(self._extract_tokens(reference_text))
        message_tokens = set(self._extract_tokens(current_message))
        return float(len(reference_tokens & message_tokens)) * 10.0

    def _extract_tokens(self, text: str) -> list[str]:
        """Extract normalized tokens used for lightweight relevance matching."""
        return [token.casefold() for token in self._TOKEN_PATTERN.findall(text)]

    def _read_markdown_section(self, content: str, section_name: str) -> str:
        """Read one markdown section body from a file content string."""
        pattern = rf"## {re.escape(section_name)}\n(.*?)(?:\n## |\Z)"
        match = re.search(pattern, content, re.DOTALL)
        if match is None:
            return ""
        return match.group(1).strip()

    def _ensure_user_dirs(self, memory_key: str) -> None:
        """Ensure the user memory directory tree exists."""
        self._get_daily_dir(memory_key).mkdir(parents=True, exist_ok=True)
        self._get_sessions_dir(memory_key).mkdir(parents=True, exist_ok=True)

    def _remove_user_dir(self, user_dir: Path) -> None:
        """Delete one user's full memory directory tree."""
        for path in sorted(user_dir.rglob("*"), reverse=True):
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                path.rmdir()
        user_dir.rmdir()

    def _build_safe_memory_key(self, memory_key: str) -> str:
        """Encode one memory key into a file-system-safe path segment."""
        raw_segments = [segment.strip() for segment in memory_key.split(":")]
        if not raw_segments or any(not segment for segment in raw_segments):
            raise ValueError(self._INVALID_MEMORY_KEY_MSG)

        safe_segments: list[str] = []
        for segment in raw_segments:
            if self._MEMORY_KEY_SEGMENT_PATTERN.fullmatch(segment) is None:
                raise ValueError(self._INVALID_MEMORY_KEY_MSG)
            safe_segments.append(str(segment))
        return "%3A".join(safe_segments)

    def _get_user_dir(self, memory_key: str) -> Path:
        """Return the directory containing all files for one memory key."""
        safe_key = self._build_safe_memory_key(memory_key)
        base_dir = self._base_dir.resolve()
        user_dir = (base_dir / safe_key).resolve()
        if not safe_key or user_dir == base_dir:
            raise ValueError(self._INVALID_MEMORY_KEY_MSG)
        if base_dir not in user_dir.parents:
            msg = "Memory key resolves outside the configured base directory"
            raise ValueError(msg)
        return user_dir

    def _get_memory_file(self, memory_key: str) -> Path:
        """Return the path to MEMORY.md for one user."""
        return self._get_user_dir(memory_key) / "MEMORY.md"

    def _get_daily_dir(self, memory_key: str) -> Path:
        """Return the daily log directory for one user."""
        return self._get_user_dir(memory_key) / "daily"

    def _get_sessions_dir(self, memory_key: str) -> Path:
        """Return the session archive directory for one user."""
        return self._get_user_dir(memory_key) / "sessions"

    @staticmethod
    def _slugify(value: str) -> str:
        """Convert arbitrary text into a file-friendly slug."""
        lowered = value.strip().lower()
        slug = re.sub(r"[^a-z0-9]+", "-", lowered)
        return slug.strip("-") or "session"

    @staticmethod
    def _clamp_confidence(value: object) -> float:
        """Clamp a confidence value into the inclusive range [0.0, 1.0]."""
        try:
            numeric_value = float(value)
        except (TypeError, ValueError):
            return 0.5
        return max(0.0, min(1.0, numeric_value))
