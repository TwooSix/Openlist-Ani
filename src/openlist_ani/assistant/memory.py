"""Assistant memory management with Markdown file-first persistence.

All memory is stored in ``data/assistant/`` as plain Markdown files:

- ``SOUL.md``   — agent persona (read-only by code)
- ``MEMORY.md`` — long-term facts curated by LLM
- ``USER.md``   — user profile + agent observations
- ``sessions/SESSION_*.md`` — per-session conversation transcripts

Context assembly, session pruning, and compaction have been extracted to
the ``context_engine/`` package.  This module is purely responsible for
**file-level CRUD** and **fact management**.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
from openai import AsyncOpenAI

from ..logger import logger
from .constants import TOOL_CONTEXT_FIELD

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
_PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
_USER_TEMPLATE = "USER.md.template"
_SESSION_GLOB = "SESSION_*.md"

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


class AssistantMemoryManager:
    """Manage assistant memory using transparent local Markdown files.

    All files live under a single ``base_dir`` (default ``data/assistant/``).
    No per-user isolation — designed for a single-user Telegram bot.

    This class is the single source of truth for *file-level operations*.
    Higher-level context assembly lives in ``context_engine.PromptBuilder``.
    """

    _SESSION_MAX_TOKENS = 100_000
    _REFRESH_EVERY_N_TURNS = 6
    _FACT_LIMIT = 12
    _KEEP_RECENT_TURNS = 4
    _PAST_SESSION_TOKEN_LIMIT = 4000
    _PAST_SESSION_SEARCH_DAYS = 7
    _SESSION_DATE_FORMAT = "%Y%m%d"

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
    # Public API — session turns
    # ------------------------------------------------------------------

    async def append_turn(
        self,
        user_message: str,
        assistant_message: str,
        *,
        tool_context: str = "",
    ) -> None:
        """Persist one conversation turn and maintain memory.

        Appends the turn to the active session file and periodically
        refreshes long-term memory.  Compaction is handled externally
        by ``SessionCompactor`` (called from ``AniAssistant``).

        Args:
            user_message: The user's message.
            assistant_message: The assistant's response.
            tool_context: Optional tool execution log for this turn.
        """
        self._ensure_dirs()
        session_path = await self._ensure_active_session()
        await asyncio.to_thread(
            self._append_session_turn,
            session_path,
            user_message,
            assistant_message,
            tool_context=tool_context,
        )

        self._turn_counter += 1

        # Periodically refresh long-term memory
        if self._turn_counter >= self._REFRESH_EVERY_N_TURNS:
            self._turn_counter = 0
            recent = await asyncio.to_thread(self._load_recent_messages)
            await self._refresh_memory(recent)

    async def start_new_session(self) -> None:
        """Delete all session files and reset conversation state."""
        await asyncio.to_thread(self._delete_all_sessions)
        self._turn_counter = 0

    async def clear_all_memory(self) -> None:
        """Clear MEMORY.md contents, USER.md agent observations, and all sessions."""
        await asyncio.to_thread(self._do_clear_all)

    # ------------------------------------------------------------------
    # Public API — memory tools
    # ------------------------------------------------------------------

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
        """Append a user instruction to ``## User Customizations`` in SOUL.md.

        Args:
            instruction: Behaviour or personality instruction from the user.
        """
        await asyncio.to_thread(self._do_append_soul_customization, instruction)

    # ------------------------------------------------------------------
    # Session management (private)
    # ------------------------------------------------------------------

    async def _ensure_active_session(self) -> Path:
        """Return today's session file or create a new one."""
        existing = await asyncio.to_thread(self._get_today_session_path)
        if existing is not None:
            return existing
        return await asyncio.to_thread(self._create_new_session)

    def _get_today_session_path(self) -> Path | None:
        """Return today's session file if it exists."""
        sessions_dir = self._sessions_dir
        if not sessions_dir.exists():
            return None

        today_str = datetime.now().strftime(self._SESSION_DATE_FORMAT)
        today_file = sessions_dir / f"SESSION_{today_str}.md"
        if today_file.exists():
            return today_file

        return None

    def _create_new_session(self) -> Path:
        """Create a fresh daily session file and return its path."""
        self._sessions_dir.mkdir(parents=True, exist_ok=True)
        today_str = datetime.now().strftime(self._SESSION_DATE_FORMAT)
        session_path = self._sessions_dir / f"SESSION_{today_str}.md"
        started = datetime.now().isoformat(timespec="seconds")
        session_path.write_text(
            f"# Session {today_str}\n\n- started_at: {started}\n\n## Conversation\n\n",
            encoding="utf-8",
        )
        return session_path

    def _delete_all_sessions(self) -> None:
        """Delete all session files from the sessions directory."""
        if not self._sessions_dir.exists():
            return
        for f in self._sessions_dir.glob(_SESSION_GLOB):
            f.unlink()

    def _append_session_turn(
        self,
        session_path: Path,
        user_message: str,
        assistant_message: str,
        *,
        tool_context: str = "",
    ) -> None:
        """Append one conversation turn to a session file."""
        turn_index = self._count_session_turns(session_path) + 1
        block = f"### Turn {turn_index}\n**User:** {user_message}\n\n"
        if tool_context:
            block += f"**{TOOL_CONTEXT_FIELD}:**\n{tool_context}\n\n"
        block += f"**Assistant:** {assistant_message}\n\n"
        with session_path.open("a", encoding="utf-8") as fh:
            fh.write(block)

    def _load_recent_messages(self) -> list[dict[str, str]]:
        """Parse today's session into simple user/assistant message pairs.

        Used exclusively by ``_refresh_memory`` — no pruning needed here.
        """
        session_path = self._get_today_session_path()
        if session_path is None:
            return []

        content = session_path.read_text(encoding="utf-8")
        messages: list[dict[str, str]] = []

        turn_pattern = re.compile(
            r"### Turn \d+\n"
            r"\*\*User:\*\* (.*?)\n\n"
            r"(?:\*\*Tool Context:\*\*\n.*?\n\n)?"
            r"\*\*Assistant:\*\* (.*?)\n(?:\n|$)",
            re.DOTALL,
        )
        for match in turn_pattern.finditer(content):
            messages.append({"role": "user", "content": match.group(1).strip()})
            messages.append({"role": "assistant", "content": match.group(2).strip()})

        return messages

    # ------------------------------------------------------------------
    # Past session BM25 search
    # ------------------------------------------------------------------

    def _search_past_sessions(self, query: str) -> str:
        """Search past session files for turns relevant to *query* via BM25.

        Uses *jieba* word segmentation for Chinese text tokenisation.
        Scans ``SESSION_YYYYMMDD.md`` files from the last
        ``_PAST_SESSION_SEARCH_DAYS`` days (excluding today).
        """
        sessions_dir = self._sessions_dir
        if not sessions_dir.exists():
            return ""

        entries, texts = self._collect_past_session_turns(sessions_dir)
        if not entries:
            return ""

        return self._bm25_rank_turns(query, entries, texts)

    def _collect_past_session_turns(
        self, sessions_dir: Path
    ) -> tuple[list[tuple[str, str]], list[str]]:
        """Collect conversation turns from past session files."""
        today_str = datetime.now().strftime(self._SESSION_DATE_FORMAT)
        cutoff = datetime.now() - timedelta(days=self._PAST_SESSION_SEARCH_DAYS)
        cutoff_str = cutoff.strftime(self._SESSION_DATE_FORMAT)

        turn_pattern = re.compile(
            r"(### Turn \d+\n\*\*User:\*\* [^\n]*\n\n\*\*Assistant:\*\* [^\n]*\n)\n",
        )

        entries: list[tuple[str, str]] = []
        texts: list[str] = []

        for path in sorted(sessions_dir.glob(_SESSION_GLOB), reverse=True):
            match = re.fullmatch(r"SESSION_(\d{8})\.md", path.name)
            if match is None:
                continue
            date_str = match.group(1)
            if date_str == today_str or date_str < cutoff_str:
                continue
            content = path.read_text(encoding="utf-8")
            for turn_match in turn_pattern.finditer(content):
                turn_text = turn_match.group(1)
                entries.append((date_str, turn_text))
                texts.append(turn_text)

        return entries, texts

    def _bm25_rank_turns(
        self,
        query: str,
        entries: list[tuple[str, str]],
        texts: list[str],
    ) -> str:
        """Rank turn texts by BM25 relevance and return top matches."""
        import jieba  # noqa: E402 — lazy import to avoid startup cost

        def _tokenize(text: str) -> list[str]:
            words = jieba.lcut(text.lower())
            return [
                w.strip()
                for w in words
                if len(w.strip()) >= 2 and not w.strip().isspace()
            ]

        n_docs = len(texts)
        doc_tokens = [_tokenize(t) for t in texts]
        doc_lens = np.array([len(t) for t in doc_tokens], dtype=np.float64)
        avgdl = doc_lens.mean() if n_docs > 0 else 1.0

        scores = self._compute_bm25_scores(
            _tokenize(query), doc_tokens, doc_lens, avgdl, n_docs
        )

        return self._select_top_turns(entries, scores)

    @staticmethod
    def _compute_bm25_scores(
        query_tokens: list[str],
        doc_tokens: list[list[str]],
        doc_lens: np.ndarray,
        avgdl: float,
        n_docs: int,
    ) -> np.ndarray:
        """Compute BM25 scores for each document given query tokens."""
        df: dict[str, int] = defaultdict(int)
        for tokens in doc_tokens:
            for tok in set(tokens):
                df[tok] += 1

        k1 = 1.5
        b = 0.75
        query_terms = list(set(query_tokens))

        scores = np.zeros(n_docs)
        for term in query_terms:
            if term not in df:
                continue
            n_t = df[term]
            idf = np.log((n_docs - n_t + 0.5) / (n_t + 0.5) + 1)
            for i, tokens in enumerate(doc_tokens):
                tf_val = tokens.count(term)
                if tf_val > 0:
                    numerator = tf_val * (k1 + 1)
                    denominator = tf_val + k1 * (1 - b + b * doc_lens[i] / avgdl)
                    scores[i] += idf * numerator / denominator
        return scores

    def _select_top_turns(
        self,
        entries: list[tuple[str, str]],
        scores: np.ndarray,
    ) -> str:
        """Select top-scoring turns within the token budget."""
        top_indices = np.argsort(scores)[::-1][:20]

        matched_blocks: list[str] = []
        total_tokens = 0
        for idx in top_indices:
            if scores[idx] <= 0:
                break
            file_date, turn_text = entries[idx]
            block = f"[{file_date}]\n{turn_text}\n"
            block_tokens = self._estimate_tokens(block)
            if total_tokens + block_tokens > self._PAST_SESSION_TOKEN_LIMIT:
                break
            matched_blocks.append(block)
            total_tokens += block_tokens

        return "\n".join(matched_blocks)

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
        safe_path = self._base_dir / "USER.md"
        if not safe_path.exists():
            src = _PROMPTS_DIR / _USER_TEMPLATE
            if src.exists():
                shutil.copy2(src, safe_path)
            else:
                safe_path.write_text("", encoding="utf-8")

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
            src = _PROMPTS_DIR / _USER_TEMPLATE
            if src.exists():
                shutil.copy2(src, self._user_path)
            else:
                self._user_path.write_text("", encoding="utf-8")

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
        """Replace or append a ``## section`` block in a Markdown file."""
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
            default_user = self._load_template(_USER_TEMPLATE)
            default_obs = self._read_markdown_section(
                default_user, _SECTION_AGENT_OBSERVATIONS
            )
            self._write_user_section(
                _SECTION_AGENT_OBSERVATIONS,
                default_obs or "",
            )

        if self._sessions_dir.exists():
            for f in self._sessions_dir.glob(_SESSION_GLOB):
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
        for target, template_name in (
            (self._soul_path, "SOUL.md.template"),
            (self._memory_path, "MEMORY.md.template"),
            (self._user_path, _USER_TEMPLATE),
        ):
            if not target.exists():
                src = _PROMPTS_DIR / template_name
                if src.exists():
                    shutil.copy2(src, target)
                else:
                    target.write_text("", encoding="utf-8")

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
    def _load_template(template_name: str) -> str:
        """Read a template file from the prompts directory.

        Args:
            template_name: Filename inside the ``prompts/`` directory.

        Returns:
            Template content, or empty string if the file is missing.
        """
        path = _PROMPTS_DIR / template_name
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")
