"""Unified system-message assembly pipeline.

Consolidates the old ``memory.py:build_system_messages`` and
``assistant.py:_build_messages`` into a single class that orchestrates
the complete context assembly:

    SOUL → MEMORY → USER → Pruned Session History → Compaction Ping
    → Past Sessions (BM25) → Behavioral Rules → Skill Catalog → User Message
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import TYPE_CHECKING


from .memory_flush import MemoryFlushGuard, build_flush_system_message
from .pruner import ParsedTurn, prune_turn_messages
from .settings import CompactionSettings, PruningSettings
from .skill_catalog import SkillCatalog

if TYPE_CHECKING:
    from ..memory import AssistantMemoryManager

# -----------------------------------------------------------------------
# Behavioral rules (formerly in assistant.py)
# -----------------------------------------------------------------------

_BEHAVIORAL_RULES = """\
## Behavioral Rules (MANDATORY)

### 1. Skill Accuracy — Read SKILL.md and Use Exact Arguments

When calling `run_skill`, the `skill_module` path and `arguments` \
keys MUST exactly match what is documented in the skill's SKILL.md. \
Common mistakes to avoid:
- ❌ `bangumi.script.search` with `{"query": "..."}` \
→ ✅ use `{"keyword": "..."}`
- ❌ `bangumi.script.collection` with `{"subject_id":..., "collection_type":...}` \
(that is the READ action) → ✅ for UPDATING, use `bangumi.script.collect`
- ❌ `oani.script.search` → ✅ `oani.script.search_anime`

If you are unsure about a skill's interface, use `read_file` to check \
the SKILL.md before calling it.

### 2. Progress Reporting

When you execute slow operations, call `send_message` concurrently or \
just before to keep the user informed. But do NOT use `send_message` \
for your final answer — it is sent automatically.

### 3. Operational Safety

- If no download link is available, search for resources first.
- When given an RSS link, always parse it before downloading.
- NEVER download resources already marked as downloaded.
- Check download history via database query before downloading.
- If tool arguments are uncertain, ask the user instead of guessing.
- When a tool returns confirmation or conflict info, relay it verbatim.

### 4. Memory Management

Persist important info proactively with these tools:
- `update_user_profile`: user info (name, preferences, collection analysis)
- `update_memory`: durable facts (task outcomes, environment details)
- `update_soul`: personality changes (ONLY when user explicitly requests)
"""


class ContextPromptBuilder:
    """Assemble the full LLM message list for each turn.

    This is the single entry point for context construction — it replaces
    the scattered logic that was previously split across
    ``AssistantMemoryManager.build_system_messages`` and
    ``AniAssistant._build_messages``.

    Args:
        memory_manager: Provides file-level CRUD for SOUL/MEMORY/USER/session.
        skill_catalog: Generates the skills prompt section.
        flush_guard: Guards one-flush-per-compaction-cycle.
        pruning_settings: Session pruning configuration.
        compaction_settings: Compaction threshold configuration.
    """

    def __init__(
        self,
        memory_manager: AssistantMemoryManager,
        skill_catalog: SkillCatalog,
        flush_guard: MemoryFlushGuard,
        pruning_settings: PruningSettings | None = None,
        compaction_settings: CompactionSettings | None = None,
    ) -> None:
        self._mm = memory_manager
        self._skill_catalog = skill_catalog
        self._flush_guard = flush_guard
        self._pruning = pruning_settings or PruningSettings()
        self._compaction = compaction_settings or CompactionSettings()

    async def build_messages(self, user_message: str) -> list[dict[str, str]]:
        """Build the complete message list for one LLM call.

        Assembly order mirrors OpenClaw's system prompt construction:

        1. SOUL.md              → persona
        2. MEMORY.md            → long-term facts
        3. USER.md              → user profile
        4. Session history      → pruned turns
        5. Compaction ping      → if near threshold
        6. Past sessions (BM25) → keyword-matched
        7. Behavioral rules     → mandatory rules
        8. Skill catalog        → skill discovery
        9. User message         → current turn

        Args:
            user_message: Current user input.

        Returns:
            Ordered list of message dicts ready for the LLM.
        """
        await asyncio.to_thread(self._mm._ensure_dirs)

        messages: list[dict[str, str]] = []

        # 1. SOUL — persona foundation
        soul_text = await asyncio.to_thread(self._mm._read_file, self._mm._soul_path)
        if soul_text.strip():
            messages.append({"role": "system", "content": soul_text.strip()})

        # 2. MEMORY — long-term facts
        memory_text = await asyncio.to_thread(
            self._mm._read_file, self._mm._memory_path
        )
        default_memory = self._mm._load_template("MEMORY.md.template").strip()
        if memory_text.strip() and memory_text.strip() != default_memory:
            messages.append(
                {
                    "role": "system",
                    "content": (
                        "The following is your long-term memory about the user "
                        "and past interactions. If any memory conflicts with "
                        "the current message, trust the current message.\n\n"
                        + memory_text.strip()
                    ),
                }
            )

        # 3. USER — user profile
        user_text = await asyncio.to_thread(self._mm._read_file, self._mm._user_path)
        default_user = self._mm._load_template("USER.md.template").strip()
        if user_text.strip() and user_text.strip() != default_user:
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
            messages.append(self._first_time_user_message())

        # 4. Session history — parsed + pruned
        session_path = await asyncio.to_thread(self._mm._get_today_session_path)
        session_messages, session_text = await self._load_pruned_session(session_path)
        if session_messages:
            messages.extend(session_messages)

        # 5. Compaction ping
        if session_text:
            estimated_tokens = self._mm._estimate_tokens(session_text)
            if self._flush_guard.should_flush(estimated_tokens, self._compaction):
                messages.append(build_flush_system_message())
                self._flush_guard.record_flush()

        # 6. Past sessions (BM25)
        past_context = await asyncio.to_thread(
            self._mm._search_past_sessions, user_message
        )
        if past_context.strip():
            messages.append(
                {
                    "role": "system",
                    "content": (
                        "The following are relevant excerpts from past "
                        "conversations (matched by keywords in the current "
                        "message). Use them as additional context if helpful.\n\n"
                        + past_context.strip()
                    ),
                }
            )

        # 7. Behavioral rules
        messages.append({"role": "system", "content": _BEHAVIORAL_RULES})

        # 8. Skill catalog
        skills_prompt = self._skill_catalog.build_prompt()
        messages.append({"role": "system", "content": skills_prompt})

        # 9. User message
        messages.append({"role": "user", "content": user_message})

        return messages

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _load_pruned_session(
        self,
        session_path: Path | None,
    ) -> tuple[list[dict[str, str]], str]:
        """Load today's session, parse turns, and apply pruning.

        Returns:
            A tuple of (pruned messages list, raw session text).
        """
        if session_path is None:
            return [], ""

        raw = await asyncio.to_thread(lambda: session_path.read_text(encoding="utf-8"))
        messages: list[dict[str, str]] = []

        # Summary section (from previous compactions)
        summary = self._mm._read_markdown_section(raw, "Summary")
        if summary.strip():
            messages.append(
                {
                    "role": "system",
                    "content": f"Previous conversation summary:\n{summary.strip()}",
                }
            )

        # Parse turns then prune
        turns = self._parse_raw_turns(raw)
        if turns:
            pruned = prune_turn_messages(turns, self._pruning)
            messages.extend(pruned)

        return messages, raw

    @staticmethod
    def _parse_raw_turns(content: str) -> list[ParsedTurn]:
        """Parse ``### Turn N`` blocks from raw session markdown."""
        pattern = re.compile(
            r"### Turn \d+\n"
            r"\*\*User:\*\* (.*?)\n\n"
            r"(?:\*\*Tool Context:\*\*\n(.*?)\n\n)?"
            r"\*\*Assistant:\*\* (.*?)\n(?:\n|$)",
            re.DOTALL,
        )
        return [
            ParsedTurn(
                user_text=m.group(1).strip(),
                tool_context=(m.group(2) or "").strip(),
                assistant_text=m.group(3).strip(),
            )
            for m in pattern.finditer(content)
        ]

    @staticmethod
    def _first_time_user_message() -> dict[str, str]:
        """System message for uninitialised user profiles."""
        return {
            "role": "system",
            "content": (
                "## First-Time User Initialization (Mandatory)\n\n"
                "The user profile (USER.md) has not been initialized yet. "
                "This is a new user or the first conversation.\n"
                "Before doing anything else, you **must** complete the "
                "following initialization flow:\n\n"
                "1. Call `send_message` to greet the user and introduce "
                "yourself as oAni\n"
                "2. Ask the user: 'What should I call you?'\n"
                "3. Ask the user: 'Would you like me to check your Bangumi "
                "collection? That way I can learn your anime preferences "
                "and give you better recommendations in the future.'\n\n"
                "After the user responds:\n"
                "- Immediately call `update_user_profile` to save the "
                "user's name (e.g. content='User's name is Alice')\n"
                "- If the user agrees to check their Bangumi collection, "
                "run the collection query skill to fetch data, then "
                "**must** call `update_user_profile` with "
                "section='bangumi_preferences' to save the collection "
                "analysis results (preferred genres, rating tendencies, "
                "frequently watched tags, etc.) to the user profile\n\n"
                "**Do not invoke any other tools until the user has "
                "answered these questions.**"
            ),
        }
