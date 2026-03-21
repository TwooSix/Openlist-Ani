"""Session Compaction — LLM-driven history summarisation.

Extracted from the old ``memory.py`` monolith.  Compaction rewrites the
session Markdown file on disk, replacing older turns with a summary and
re-numbering the retained recent turns.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from openai import AsyncOpenAI

from ...logger import logger
from .memory_flush import MemoryFlushGuard
from .settings import CompactionSettings

_SESSION_COMPRESS_PROMPT = """\
You are a conversation summarizer. Compress the following conversation turns into a concise summary.
Keep key decisions, outcomes, and important context. Discard greetings and filler.
Write in the same language as the conversation.
Return plain text only, no markdown formatting."""


class SessionCompactor:
    """Compress old session turns into a summary.

    Attributes:
        flush_guard: The :class:`MemoryFlushGuard` shared with the
            prompt builder so compaction cycles are tracked consistently.
    """

    def __init__(
        self,
        client: AsyncOpenAI | None,
        model: str,
        flush_guard: MemoryFlushGuard,
        settings: CompactionSettings | None = None,
    ) -> None:
        self._client = client
        self._model = model
        self._settings = settings or CompactionSettings()
        self.flush_guard = flush_guard

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def should_compress(self, session_text: str) -> bool:
        """Return True if *session_text* exceeds the token budget."""
        return self._estimate_tokens(session_text) > self._settings.session_max_tokens

    async def compress(self, session_path: Path) -> None:
        """Compress older turns in *session_path*.

        Keeps the most recent ``keep_recent_turns`` turns verbatim, and
        replaces older turns with an LLM-generated summary.  The
        compaction counter is bumped so the memory-flush guard resets.

        Args:
            session_path: Path to the active ``SESSION_YYYYMMDD.md`` file.
        """
        if self._client is None:
            return

        content = await asyncio.to_thread(
            lambda: session_path.read_text(encoding="utf-8")
        )
        all_turns = list(
            re.finditer(
                r"(### Turn \d+\n\*\*User:\*\* (?:(?!\n\n\*\*Assistant:\*\*).)*"
                r"\n\n\*\*Assistant:\*\* (?:(?!\n\n(?:### Turn |\Z)).)*\n\n)",
                content,
                re.DOTALL,
            )
        )

        keep = self._settings.keep_recent_turns
        if len(all_turns) <= keep:
            return

        old_turns = all_turns[:-keep]
        recent_turns = all_turns[-keep:]
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
            r"(# Session.*?## Conversation\n\n)", content, re.DOTALL
        )
        header = (
            header_match.group(1)
            if header_match
            else "# Session\n\n## Conversation\n\n"
        )
        recent_text = "".join(m.group(0) for m in recent_turns)
        turn_num = 0

        def _renumber(_m: re.Match) -> str:
            nonlocal turn_num
            turn_num += 1
            return f"### Turn {turn_num}"

        recent_text = re.sub(r"### Turn \d+", _renumber, recent_text)
        new_content = header + recent_text + f"\n## Summary\n\n{summary}\n"
        await asyncio.to_thread(session_path.write_text, new_content, encoding="utf-8")
        self.flush_guard.record_compaction()
        logger.info(
            "Compressed session {}: {} turns → summary + {} recent",
            session_path.name,
            len(all_turns),
            len(recent_turns),
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _read_markdown_section(content: str, section_name: str) -> str:
        """Read one ``## section_name`` body from markdown content."""
        pattern = rf"## {re.escape(section_name)}\n\n?(.*?)(?:\n## |\Z)"
        match = re.search(pattern, content, re.DOTALL)
        return match.group(1).strip() if match else ""

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """Rough token estimate: CJK chars × 1.5 + English word count."""
        cjk = len(re.findall(r"[\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff]", text))
        ascii_words = len(re.findall(r"[A-Za-z]+", text))
        return int(cjk * 1.5) + ascii_words
