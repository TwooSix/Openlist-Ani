"""Assistant memory management with Markdown file-first persistence.

All memory is stored in ``data/assistant/`` as plain Markdown files:

- ``SOUL.md``   — agent persona (read-only by code)
- ``MEMORY.md`` — long-term facts curated by LLM
- ``USER.md``   — user profile + agent observations
- ``sessions/SESSION_*.md`` — per-session conversation transcripts
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from openai import AsyncOpenAI

from ..logger import logger

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class MemoryFact:
    """A structured long-term memory fact."""

    content: str
    category: str
    confidence: float


# ---------------------------------------------------------------------------
# Constants / prompts
# ---------------------------------------------------------------------------

_EMPTY_LIST_MARKER = "- None"
_EMPTY_SUMMARY = "None"
_SECTION_AGENT_OBSERVATIONS = "Agent Observations"

_MEMORY_REFRESH_PROMPT = """\
You are a long-term memory curator for a chat assistant.

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
  "user_observations": "One or two sentences about new user preferences, habits, or personality traits observed in this dialogue. Return empty string if nothing new."
}

Rules:
1. Keep only information that improves future responses.
2. Facts should capture durable preferences, identity details, constraints, workflow habits, or important project state.
3. Do not store transient small talk, temporary emotions, or unresolved speculation.
4. Merge duplicates and near-duplicates. Return at most 12 facts.
5. Confidence must be between 0.0 and 1.0.
6. Write the summary in the user's language from the new dialogue when it is clear.
7. If there is no durable fact, return an empty facts array."""

_SESSION_COMPRESS_PROMPT = """\
You are a conversation summarizer. Compress the following conversation turns into a concise summary.
Keep key decisions, outcomes, and important context. Discard greetings and filler.
Write in the same language as the conversation.
Return plain text only, no markdown formatting."""


class AssistantMemoryManager:
    """Manage assistant memory using transparent local Markdown files.

    All files live under a single ``base_dir`` (default ``data/assistant/``).
    No per-user isolation — designed for a single-user Telegram bot.
    """

    _SESSION_STALE_AFTER = timedelta(hours=6)
    _SESSION_MAX_TOKENS = 8000
    _REFRESH_EVERY_N_TURNS = 6
    _FACT_LIMIT = 12
    _KEEP_RECENT_TURNS = 4

    def __init__(
        self,
        client: AsyncOpenAI | None,
        model: str,
        base_dir: Path | None = None,
    ):
        self._client = client
        self._model = model
        self._base_dir = (base_dir or (Path.cwd() / "data" / "assistant")).resolve()
        self._turn_counter: int = 0

    # ------------------------------------------------------------------
    # Path helpers
    # ------------------------------------------------------------------

    @property
    def _soul_path(self) -> Path:
        return self._base_dir / "SOUL.md"

    @property
    def _memory_path(self) -> Path:
        return self._base_dir / "MEMORY.md"

    @property
    def _user_path(self) -> Path:
        return self._base_dir / "USER.md"

    @property
    def _sessions_dir(self) -> Path:
        return self._base_dir / "sessions"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def build_system_messages(self, _user_message: str) -> list[dict[str, str]]:
        """Build the full system + context message list for the LLM.

        Reads SOUL.md, MEMORY.md, USER.md, and the active session transcript.
        Does **not** include the current ``user_message`` — that is appended
        by the caller.

        Args:
            user_message: Current user message (used for logging, not appended).

        Returns:
            Ordered list of message dicts ready to prepend to the LLM call.
        """
        await asyncio.to_thread(self._ensure_dirs)
        soul_text = await asyncio.to_thread(self._read_file, self._soul_path)
        memory_text = await asyncio.to_thread(self._read_file, self._memory_path)
        user_text = await asyncio.to_thread(self._read_file, self._user_path)
        session_messages = await asyncio.to_thread(self._load_active_session_messages)

        messages: list[dict[str, str]] = []

        # 1. Soul → system prompt foundation (caller merges with tool rules)
        if soul_text.strip():
            messages.append({"role": "system", "content": soul_text.strip()})

        # 2. Long-term memory context
        if (
            memory_text.strip()
            and memory_text.strip() != self._default_memory_text().strip()
        ):
            messages.append(
                {
                    "role": "system",
                    "content": (
                        "The following is your long-term memory about the user and past interactions. "
                        "If any memory conflicts with the current message, trust the current message.\n\n"
                        + memory_text.strip()
                    ),
                }
            )

        # 3. User profile context
        if user_text.strip() and user_text.strip() != self._default_user_text().strip():
            messages.append(
                {
                    "role": "system",
                    "content": (
                        "The following is the user's profile and preferences:\n\n"
                        + user_text.strip()
                    ),
                }
            )
        else:
            # USER.md is missing or still the default template — first-time user
            messages.append(
                {
                    "role": "system",
                    "content": (
                        "## 首次用户初始化（强制）\n\n"
                        "用户档案（USER.md）尚未初始化，这是一个新用户或首次对话。\n"
                        "在执行任何其他操作之前，你**必须**先完成以下初始化流程：\n\n"
                        "1. 先调用 `send_message` 向用户打招呼，自我介绍你是 oAni\n"
                        "2. 询问用户：「我该怎么称呼你？」\n"
                        "3. 询问用户：「需要我先看看你的 Bangumi 收藏吗？"
                        "这样我可以了解你的番剧喜好，以后给你更好的推荐和服务 😊」\n\n"
                        "等用户回答后：\n"
                        "- 立即调用 `update_user_profile` 保存用户的称呼"
                        "（例如：content='用户名字叫小明'）\n"
                        "- 如果用户同意查看 Bangumi 收藏，执行收藏查询技能获取数据，"
                        "然后**必须**调用 `update_user_profile` 并设置 "
                        "section='bangumi_preferences'，将收藏分析结果"
                        "（喜好类型、评分倾向、常看标签等）保存到用户档案\n\n"
                        "**在用户回答这些问题之前，不要执行任何其他工具调用。**"
                    ),
                }
            )

        # 4. Session conversation history
        if session_messages:
            messages.extend(session_messages)

        return messages

    async def append_turn(
        self,
        user_message: str,
        assistant_message: str,
    ) -> None:
        """Persist one conversation turn and maintain memory.

        Appends the turn to the active session file, compresses if needed,
        and periodically refreshes long-term memory.

        Args:
            user_message: The user's message.
            assistant_message: The assistant's response.
        """
        self._ensure_dirs()
        session_path = await self._ensure_active_session()
        await asyncio.to_thread(
            self._append_session_turn, session_path, user_message, assistant_message
        )

        self._turn_counter += 1

        # Compress session if it exceeds the token budget
        session_text = await asyncio.to_thread(
            lambda: session_path.read_text(encoding="utf-8")
        )
        if self._estimate_tokens(session_text) > self._SESSION_MAX_TOKENS:
            await self._compress_session(session_path)

        # Periodically refresh long-term memory
        if self._turn_counter >= self._REFRESH_EVERY_N_TURNS:
            self._turn_counter = 0
            recent = await asyncio.to_thread(self._load_active_session_messages)
            await self._refresh_memory(recent)

    async def start_new_session(self) -> None:
        """Close the current session and start a fresh one on next turn."""
        active = await asyncio.to_thread(self._get_active_session_path)
        if active and active.exists():
            await asyncio.to_thread(self._close_session, active)
        self._turn_counter = 0

    async def clear_all_memory(self) -> None:
        """Clear MEMORY.md contents, USER.md agent observations, and all sessions."""
        await asyncio.to_thread(self._do_clear_all)

    async def update_user_profile(self, section_text: str) -> None:
        """Update the ``## Bangumi Preferences`` section of USER.md.

        Args:
            section_text: New content for the Bangumi Preferences section.
        """
        await asyncio.to_thread(
            self._write_user_section, "Bangumi Preferences", section_text
        )

    async def add_user_observation(self, observation: str) -> None:
        """Append one observation to the ``## Agent Observations`` section.

        This is the public async wrapper around the internal helper,
        intended to be called by the ``update_user_profile`` tool.

        Args:
            observation: A concise fact about the user.
        """
        await asyncio.to_thread(self._append_user_observations, observation)

    async def add_memory_fact(
        self,
        content: str,
        category: str = "general",
        confidence: float = 0.8,
    ) -> None:
        """Add a single fact to MEMORY.md.

        The new fact is merged with existing facts using the standard
        deduplication and decay logic.

        Args:
            content: Fact text.
            category: Fact category (preference / constraint / etc.).
            confidence: Confidence score between 0.0 and 1.0.
        """
        await asyncio.to_thread(
            self._do_add_memory_fact,
            content,
            category,
            confidence,
        )

    async def append_soul_customization(self, instruction: str) -> None:
        """Append a user instruction to the ``## User Customizations`` section of SOUL.md.

        Args:
            instruction: Behaviour or personality instruction from the user.
        """
        await asyncio.to_thread(self._do_append_soul_customization, instruction)

    # ------------------------------------------------------------------
    # Session management (private)
    # ------------------------------------------------------------------

    async def _ensure_active_session(self) -> Path:
        """Return the active session file or create a new one."""
        existing = await asyncio.to_thread(self._get_active_session_path)
        if existing is not None:
            return existing
        return await asyncio.to_thread(self._create_new_session)

    def _get_active_session_path(self) -> Path | None:
        """Return the newest non-closed session if it's still fresh."""
        sessions_dir = self._sessions_dir
        if not sessions_dir.exists():
            return None

        candidates = sorted(
            sessions_dir.glob("SESSION_*.md"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not candidates:
            return None

        newest = candidates[0]
        last_modified = datetime.fromtimestamp(newest.stat().st_mtime)
        if datetime.now() - last_modified > self._SESSION_STALE_AFTER:
            return None

        header = newest.read_text(encoding="utf-8")[:500]
        if "status: closed" in header:
            return None

        return newest

    def _create_new_session(self) -> Path:
        """Create a fresh session file and return its path."""
        self._sessions_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        session_path = self._sessions_dir / f"SESSION_{timestamp}.md"
        started = datetime.now().isoformat(timespec="seconds")
        session_path.write_text(
            f"# Session\n\n- started_at: {started}\n- status: active\n\n"
            f"## Conversation\n\n",
            encoding="utf-8",
        )
        return session_path

    def _close_session(self, session_path: Path) -> None:
        """Mark a session file as closed."""
        # Reconstruct path from safe base to break taint chain (S2083)
        safe_name = session_path.name
        if not re.fullmatch(r"SESSION_\d{8}_\d{6}_\d{6}\.md", safe_name):
            raise ValueError(f"Invalid session filename: {safe_name}")
        sanitized = self._sessions_dir / safe_name
        content = sanitized.read_text(encoding="utf-8")
        content = content.replace("- status: active", "- status: closed", 1)
        sanitized.write_text(content, encoding="utf-8")

    def _append_session_turn(
        self,
        session_path: Path,
        user_message: str,
        assistant_message: str,
    ) -> None:
        """Append one conversation turn to a session file."""
        turn_index = self._count_session_turns(session_path) + 1
        block = (
            f"### Turn {turn_index}\n"
            f"**User:** {user_message}\n\n"
            f"**Assistant:** {assistant_message}\n\n"
        )
        with session_path.open("a", encoding="utf-8") as fh:
            fh.write(block)

    def _load_active_session_messages(self) -> list[dict[str, str]]:
        """Parse the active session transcript into chat message dicts."""
        session_path = self._get_active_session_path()
        if session_path is None:
            return []

        content = session_path.read_text(encoding="utf-8")
        messages: list[dict[str, str]] = []

        # Parse summary section if present
        summary = self._read_markdown_section(content, "Summary")
        if summary.strip():
            messages.append(
                {
                    "role": "system",
                    "content": f"Previous conversation summary:\n{summary.strip()}",
                }
            )

        # Parse conversation turns
        messages.extend(self._parse_turn_messages(content))
        return messages

    def _parse_turn_messages(self, content: str) -> list[dict[str, str]]:
        """Extract user/assistant messages from ``### Turn N`` blocks."""
        messages: list[dict[str, str]] = []
        turn_pattern = re.compile(
            r"### Turn \d+\n"
            r"\*\*User:\*\* (.*?)\n\n"
            r"\*\*Assistant:\*\* (.*?)\n(?:\n|$)",
            re.DOTALL,
        )
        for match in turn_pattern.finditer(content):
            user_text = match.group(1).strip()
            assistant_text = match.group(2).strip()
            if user_text:
                messages.append({"role": "user", "content": user_text})
            if assistant_text:
                messages.append({"role": "assistant", "content": assistant_text})
        return messages

    async def _compress_session(self, session_path: Path) -> None:
        """Compress older turns in a session, keeping recent ones verbatim."""
        if self._client is None:
            return

        content = session_path.read_text(encoding="utf-8")
        all_turns = list(
            re.finditer(
                r"(### Turn \d+\n\*\*User:\*\* (?:(?!\n\n\*\*Assistant:\*\*).)*\n\n\*\*Assistant:\*\* (?:(?!\n\n(?:### Turn |\Z)).)*\n\n)",
                content,
                re.DOTALL,
            )
        )

        if len(all_turns) <= self._KEEP_RECENT_TURNS:
            return

        old_turns = all_turns[: -self._KEEP_RECENT_TURNS]
        recent_turns = all_turns[-self._KEEP_RECENT_TURNS :]

        old_text = "".join(m.group(0) for m in old_turns)

        existing_summary = self._read_markdown_section(content, "Summary")
        compress_input = ""
        if existing_summary.strip():
            compress_input = (
                f"Previous summary:\n{existing_summary.strip()}\n\nNew conversation:\n"
            )
        compress_input += old_text

        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": _SESSION_COMPRESS_PROMPT},
                    {"role": "user", "content": compress_input},
                ],
                tools=None,
            )
            summary = (response.choices[0].message.content or "").strip()
        except Exception:
            logger.exception("Failed to compress session")
            return

        header_match = re.match(
            r"(# Session\n.*?## Conversation\n\n)", content, re.DOTALL
        )
        header = (
            header_match.group(1)
            if header_match
            else "# Session\n\n## Conversation\n\n"
        )
        recent_text = "".join(m.group(0) for m in recent_turns)
        # Re-number recent turns starting from 1
        turn_num = 0

        def _renumber(_m: re.Match) -> str:
            nonlocal turn_num
            turn_num += 1
            return f"### Turn {turn_num}"

        recent_text = re.sub(r"### Turn \d+", _renumber, recent_text)

        new_content = header + recent_text + f"\n## Summary\n\n{summary}\n"
        await asyncio.to_thread(session_path.write_text, new_content, encoding="utf-8")
        logger.info(
            "Compressed session {}: {} turns → summary + {} recent",
            session_path.name,
            len(all_turns),
            len(recent_turns),
        )

    # ------------------------------------------------------------------
    # MEMORY.md management (private)
    # ------------------------------------------------------------------

    async def _refresh_memory(
        self,
        recent_messages: list[dict[str, str]],
    ) -> None:
        """Refresh MEMORY.md from recent conversation turns."""
        if self._client is None:
            logger.warning("OpenAI client unavailable, skip memory refresh")
            return

        current_summary, current_facts = self._read_memory_file()
        user_prompt = self._build_refresh_prompt(
            current_summary, current_facts, recent_messages
        )

        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": _MEMORY_REFRESH_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={"type": "json_object"},
                tools=None,
            )
            payload = json.loads(response.choices[0].message.content or "{}")
        except Exception:
            logger.exception("Failed to refresh long-term memory")
            return

        summary = str(payload.get("summary", "")).strip()
        new_facts = self._sanitize_facts(payload.get("facts", []))
        facts = self._merge_facts(current_facts, new_facts)
        self._write_memory_file(summary, facts)

        observations = str(payload.get("user_observations", "")).strip()
        if observations:
            self._append_user_observations(observations)

    def _build_refresh_prompt(
        self,
        current_summary: str,
        current_facts: list[MemoryFact],
        recent_messages: list[dict[str, str]],
    ) -> str:
        """Build the prompt for long-term memory refresh."""
        existing_facts = (
            "\n".join(
                f"- [{f.category}] {f.content} (confidence={f.confidence:.2f})"
                for f in current_facts
            )
            or _EMPTY_LIST_MARKER
        )
        dialogue = "\n".join(
            f"{m['role']}: {m['content']}"
            for m in recent_messages
            if m["role"] in ("user", "assistant")
        )
        return (
            "Update the long-term memory using the existing memory "
            "and the recent dialogue.\n\n"
            f"Existing summary:\n{current_summary or 'None'}\n\n"
            f"Existing facts:\n{existing_facts}\n\n"
            f"Recent dialogue:\n{dialogue}"
        )

    def _read_memory_file(self) -> tuple[str, list[MemoryFact]]:
        """Read MEMORY.md and return (summary, facts)."""
        if not self._memory_path.exists():
            return "", []
        content = self._memory_path.read_text(encoding="utf-8")
        summary = self._read_markdown_section(content, "Summary")
        facts_section = self._read_markdown_section(content, "Facts")
        return summary.strip(), self._parse_fact_lines(facts_section)

    def _write_memory_file(self, summary: str, facts: list[MemoryFact]) -> None:
        """Write MEMORY.md."""
        facts_text = (
            "\n".join(f"- [{f.category}|{f.confidence:.2f}] {f.content}" for f in facts)
            or _EMPTY_LIST_MARKER
        )
        self._memory_path.write_text(
            "\n".join(
                [
                    "# Long-Term Memory",
                    "",
                    "## Summary",
                    "",
                    summary or _EMPTY_SUMMARY,
                    "",
                    "## Facts",
                    "",
                    facts_text,
                    "",
                ]
            ),
            encoding="utf-8",
        )

    # ------------------------------------------------------------------
    # USER.md helpers
    # ------------------------------------------------------------------

    def _write_user_section(self, section_name: str, section_content: str) -> None:
        """Replace one ``## section_name`` block in USER.md."""
        # Use hardcoded filename joined to base dir (no user-controlled path, S2083)
        safe_path = self._base_dir / "USER.md"
        if not safe_path.exists():
            safe_path.write_text(self._default_user_text(), encoding="utf-8")

        content = safe_path.read_text(encoding="utf-8")
        pattern = rf"(## {re.escape(section_name)}\n\n)(.*?)(?=\n## |\Z)"
        replacement = rf"\g<1>{section_content}\n"
        new_content, count = re.subn(
            pattern, replacement, content, count=1, flags=re.DOTALL
        )
        if count == 0:
            new_content = (
                content.rstrip() + f"\n\n## {section_name}\n\n{section_content}\n"
            )
        safe_path.write_text(new_content, encoding="utf-8")

    def _append_user_observations(self, observation: str) -> None:
        """Append a new observation to the ``## Agent Observations`` section."""
        if not self._user_path.exists():
            self._user_path.write_text(self._default_user_text(), encoding="utf-8")

        content = self._user_path.read_text(encoding="utf-8")
        section = self._read_markdown_section(content, _SECTION_AGENT_OBSERVATIONS)
        if observation in section:
            return

        date_str = datetime.now().strftime("%Y-%m-%d")
        new_line = f"- [{date_str}] {observation}"

        existing_lines = [
            line
            for line in section.strip().splitlines()
            if line.strip() and not line.startswith("（")
        ]
        existing_lines.append(new_line)
        # Keep at most 20 observations
        existing_lines = existing_lines[-20:]

        self._write_user_section(_SECTION_AGENT_OBSERVATIONS, "\n".join(existing_lines))

    # ------------------------------------------------------------------
    # MEMORY.md direct-write helper
    # ------------------------------------------------------------------

    def _do_add_memory_fact(
        self,
        content: str,
        category: str,
        confidence: float,
    ) -> None:
        """Synchronous: add one fact and re-merge MEMORY.md."""
        self._ensure_dirs()
        current_summary, current_facts = self._read_memory_file()
        new_fact = MemoryFact(
            content=content.strip(),
            category=category.strip() or "general",
            confidence=self._clamp_confidence(confidence),
        )
        merged = self._merge_facts(current_facts, [new_fact])
        self._write_memory_file(current_summary, merged)

    # ------------------------------------------------------------------
    # SOUL.md helpers
    # ------------------------------------------------------------------

    def _do_append_soul_customization(self, instruction: str) -> None:
        """Synchronous: append to ``## User Customizations`` in SOUL.md."""
        self._ensure_dirs()
        content = self._soul_path.read_text(encoding="utf-8")
        section = self._read_markdown_section(content, "User Customizations")

        if instruction in section:
            return

        date_str = datetime.now().strftime("%Y-%m-%d")
        new_line = f"- [{date_str}] {instruction}"

        existing_lines = [line for line in section.strip().splitlines() if line.strip()]
        existing_lines.append(new_line)
        # Keep at most 15 customizations
        existing_lines = existing_lines[-15:]

        self._replace_or_append_section(
            self._soul_path,
            "User Customizations",
            "\n".join(existing_lines),
        )

    def _replace_or_append_section(
        self,
        file_path: Path,
        section_name: str,
        section_content: str,
    ) -> None:
        """Replace or append a ``## section`` block in a Markdown file.

        Args:
            file_path: Target file.
            section_name: Heading text (without ``##``).
            section_content: New body content.
        """
        # Reconstruct path from safe base to break taint chain (S2083)
        safe_name = file_path.name
        sanitized = self._base_dir / safe_name
        content = sanitized.read_text(encoding="utf-8")
        pattern = rf"(## {re.escape(section_name)}\n\n)(.*?)(?=\n## |\Z)"
        replacement = rf"\g<1>{section_content}\n"
        new_content, count = re.subn(
            pattern,
            replacement,
            content,
            count=1,
            flags=re.DOTALL,
        )
        if count == 0:
            new_content = (
                content.rstrip() + f"\n\n## {section_name}\n\n{section_content}\n"
            )
        sanitized.write_text(new_content, encoding="utf-8")

    # ------------------------------------------------------------------
    # Clear
    # ------------------------------------------------------------------

    def _do_clear_all(self) -> None:
        """Synchronous implementation of clear_all_memory."""
        self._write_memory_file("", [])

        if self._user_path.exists():
            self._write_user_section(
                _SECTION_AGENT_OBSERVATIONS,
                "（由 AI 主动维护，记录与用户互动中观察到的偏好和习惯）",
            )

        if self._sessions_dir.exists():
            for f in self._sessions_dir.glob("SESSION_*.md"):
                f.unlink()

        self._turn_counter = 0

    # ------------------------------------------------------------------
    # Fact management helpers
    # ------------------------------------------------------------------

    def _parse_fact_lines(self, facts_section: str) -> list[MemoryFact]:
        """Parse the Facts section from MEMORY.md."""
        facts: list[MemoryFact] = []
        for line in facts_section.splitlines():
            stripped = line.strip()
            if not stripped.startswith("- ") or stripped == _EMPTY_LIST_MARKER:
                continue
            match = re.match(r"- \[(.+?)\|([0-9.]+)] (.+)", stripped)
            if match is None:
                facts.append(
                    MemoryFact(
                        content=stripped[2:].strip(),
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
        """Normalize model-produced facts."""
        if not isinstance(raw_facts, list):
            return []
        facts: list[MemoryFact] = []
        seen: set[str] = set()
        for item in raw_facts:
            fact = self._coerce_fact(item)
            key = fact.content.casefold()
            if not key or key in seen:
                continue
            seen.add(key)
            facts.append(fact)
            if len(facts) >= self._FACT_LIMIT:
                break
        return facts

    def _merge_facts(
        self,
        existing: list[MemoryFact],
        new: list[MemoryFact],
    ) -> list[MemoryFact]:
        """Merge new facts with existing ones, decaying stale entries."""
        merged: dict[str, MemoryFact] = {
            f.content.casefold(): f for f in existing if f.content
        }
        refreshed: set[str] = set()

        for fact in new:
            if not fact.content:
                continue
            key = fact.content.casefold()
            refreshed.add(key)
            current = merged.get(key)
            if current is None or fact.confidence >= current.confidence:
                merged[key] = fact

        for key, fact in list(merged.items()):
            if key in refreshed:
                continue
            decayed = round(max(0.0, fact.confidence - 0.1), 2)
            if decayed < 0.35:
                merged.pop(key)
                continue
            merged[key] = MemoryFact(
                content=fact.content,
                category=fact.category,
                confidence=decayed,
            )

        return sorted(
            merged.values(),
            key=lambda f: f.confidence,
            reverse=True,
        )[: self._FACT_LIMIT]

    def _coerce_fact(self, raw: object) -> MemoryFact:
        """Convert a raw model value into a MemoryFact."""
        if isinstance(raw, dict):
            return MemoryFact(
                content=str(raw.get("content", "")).strip(),
                category=str(raw.get("category", "general")).strip() or "general",
                confidence=self._clamp_confidence(raw.get("confidence", 0.5)),
            )
        return MemoryFact(content=str(raw).strip(), category="general", confidence=0.5)

    # ------------------------------------------------------------------
    # Generic helpers
    # ------------------------------------------------------------------

    def _ensure_dirs(self) -> None:
        """Ensure the base directory tree and default template files exist."""
        self._base_dir.mkdir(parents=True, exist_ok=True)
        self._sessions_dir.mkdir(parents=True, exist_ok=True)
        if not self._soul_path.exists():
            self._soul_path.write_text(self._default_soul_text(), encoding="utf-8")
        if not self._memory_path.exists():
            self._memory_path.write_text(self._default_memory_text(), encoding="utf-8")
        if not self._user_path.exists():
            self._user_path.write_text(self._default_user_text(), encoding="utf-8")

    @staticmethod
    def _read_file(path: Path) -> str:
        """Read a file or return empty string if missing."""
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")

    @staticmethod
    def _read_markdown_section(content: str, section_name: str) -> str:
        """Read one ``## section_name`` body from markdown content."""
        pattern = rf"## {re.escape(section_name)}\n\n?(.*?)(?:\n## |\Z)"
        match = re.search(pattern, content, re.DOTALL)
        return match.group(1).strip() if match else ""

    @staticmethod
    def _count_session_turns(session_path: Path) -> int:
        """Count turns in a session file."""
        return session_path.read_text(encoding="utf-8").count("### Turn ")

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """Rough token estimate: CJK chars * 1.5 + English word count."""
        cjk = len(re.findall(r"[\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff]", text))
        ascii_words = len(re.findall(r"[A-Za-z]+", text))
        return int(cjk * 1.5) + ascii_words

    @staticmethod
    def _clamp_confidence(value: object) -> float:
        """Clamp a confidence value into [0.0, 1.0]."""
        try:
            v = float(value)
        except (TypeError, ValueError):
            return 0.5
        return max(0.0, min(1.0, v))

    @staticmethod
    def _default_soul_text() -> str:
        return (
            "# Soul\n\n"
            "你是 **oAni**，一个专注于动漫资源管理的私人助手。"
            "你运行在 Telegram 上，通过工具调用帮助用户搜索、下载和管理动漫资源。\n\n"
            "## 你是谁\n\n"
            "- 你是用户的动漫资源管家，熟悉 Bangumi、蜜柑计划（Mikan）、"
            "动漫花园（DMHY）、ACG.RIP 等平台\n"
            "- 你有自己的个性：热情但不啰嗦，像一个靠谱的朋友，做事干脆利落\n"
            "- 你用用户的语言回复（中文用户用中文，英文用户用英文）\n"
            "- 你会主动记住用户的偏好和习惯，并在合适的时候使用这些信息\n\n"
            "## 核心能力\n\n"
            "1. **搜索动漫资源**：在 mikan / dmhy / acgrip 网站上搜索资源\n"
            "2. **解析 RSS 订阅**：解析 RSS 链接获取资源列表\n"
            "3. **下载资源**：通过后端 API 提交下载任务\n"
            "4. **查询下载历史**：通过 SQL 查询已下载资源的记录\n"
            "5. **Bangumi 集成**：查看日历、收藏、条目详情、评论、收藏管理、推荐\n"
            "6. **Mikan 集成**：搜索番剧、订阅/退订字幕组\n"
        )

    @staticmethod
    def _default_memory_text() -> str:
        return "# Long-Term Memory\n\n## Summary\n\nNone\n\n## Facts\n\n- None\n"

    @staticmethod
    def _default_user_text() -> str:
        return (
            "# User Profile\n\n"
            "## Bangumi Preferences\n\n"
            "（由系统自动生成，基于 Bangumi 收藏分析）\n\n"
            f"## {_SECTION_AGENT_OBSERVATIONS}\n\n"
            "（由 AI 主动维护，记录与用户互动中观察到的偏好和习惯）\n"
        )
