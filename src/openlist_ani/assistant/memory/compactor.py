"""
Autocompact — in-context conversation compaction.

When the in-memory message list grows past the autocompact threshold,
the compactor uses the LLM to generate a structured summary and replaces
old messages with the summary + restored context.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from openlist_ani.assistant._constants import (
    AUTOCOMPACT_BUFFER_TOKENS,
    AUTOCOMPACT_RESERVED_OUTPUT_TOKENS,
    DEFAULT_MAX_CONTEXT_CHARS,
    MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES,
    POST_COMPACT_MAX_FILES_TO_RESTORE,
    POST_COMPACT_MAX_TOKENS_PER_FILE,
    POST_COMPACT_TOKEN_BUDGET,
    PRESERVED_TAIL_MIN_MESSAGES,
)
from openlist_ani.assistant.core.models import Message, Role

if TYPE_CHECKING:
    from openlist_ani.assistant.provider.base import Provider

from loguru import logger


class ReadFileTracker:
    """Tracks recently-read files for post-compact restoration.

    When a tool reads a file, it should call track() with the filename
    and content. After compaction, the most recently-read files are
    re-injected into the conversation within the token budget.
    """

    def __init__(self) -> None:
        self._files: dict[str, tuple[str, float]] = {}  # {path: (content, timestamp)}

    def track(self, path: str, content: str) -> None:
        """Record a file read.

        Args:
            path: File path that was read.
            content: The file content.
        """
        import time

        self._files[path] = (content, time.time())

    def get_recent_files(
        self,
        max_files: int = POST_COMPACT_MAX_FILES_TO_RESTORE,
        max_tokens_per_file: int = POST_COMPACT_MAX_TOKENS_PER_FILE,
        total_token_budget: int = POST_COMPACT_TOKEN_BUDGET,
    ) -> list[tuple[str, str]]:
        """Get the most recently-read files within the token budget.

        - Sort by recency (most recent first)
        - Cap each file at max_tokens_per_file
        - Cap total at total_token_budget
        - Return at most max_files

        Args:
            max_files: Maximum number of files to return.
            max_tokens_per_file: Max tokens per individual file.
            total_token_budget: Total token budget for all files.

        Returns:
            List of (path, content) tuples, most recent first.
        """
        if not self._files:
            return []

        # Sort by timestamp descending (most recent first)
        sorted_files = sorted(
            self._files.items(),
            key=lambda item: item[1][1],
            reverse=True,
        )[:max_files]

        max_chars_per_file = max_tokens_per_file * 4
        total_budget_chars = total_token_budget * 4

        result: list[tuple[str, str]] = []
        used_chars = 0

        for path, (content, _ts) in sorted_files:
            # Truncate per-file content
            if len(content) > max_chars_per_file:
                content = content[:max_chars_per_file] + "\n[... file truncated]"

            file_chars = len(content) + len(path) + 50  # overhead
            if used_chars + file_chars > total_budget_chars:
                break

            result.append((path, content))
            used_chars += file_chars

        return result

    def clear(self) -> None:
        """Clear all tracked files."""
        self._files.clear()


def get_autocompact_threshold(
    max_context_chars: int = DEFAULT_MAX_CONTEXT_CHARS,
) -> int:
    """Calculate the autocompact threshold in characters.

    threshold = (contextWindow - reservedOutput) - buffer

    Args:
        max_context_chars: The max context window in characters.

    Returns:
        Threshold in characters above which autocompact triggers.
    """
    # Convert token-based constants to chars (×4)
    reserved_output_chars = AUTOCOMPACT_RESERVED_OUTPUT_TOKENS * 4
    buffer_chars = AUTOCOMPACT_BUFFER_TOKENS * 4
    effective_window = max_context_chars - reserved_output_chars
    return effective_window - buffer_chars


# 9-section compaction prompt
COMPACT_PROMPT = """\
CRITICAL: Respond with TEXT ONLY. Do NOT call any tools.

Your task is to create a detailed summary of the conversation so far, \
paying close attention to the user's explicit requests and your previous actions.
This summary should be thorough in capturing technical details, code patterns, \
and architectural decisions that would be essential for continuing development \
work without losing context.

Before providing your final summary, wrap your analysis in <analysis> tags to \
organize your thoughts and ensure you've covered all necessary points. In your \
analysis process:

1. Chronologically analyze each message and section of the conversation. \
For each section thoroughly identify:
   - The user's explicit requests and intents
   - Your approach to addressing the user's requests
   - Key decisions, technical concepts and code patterns
   - Specific details like:
     - file names
     - full code snippets
     - function signatures
     - file edits
   - Errors that you ran into and how you fixed them
   - Pay special attention to specific user feedback that you received, \
especially if the user told you to do something differently.
2. Double-check for technical accuracy and completeness, addressing each \
required element thoroughly.

Your summary should include the following sections:

1. Primary Request and Intent: Capture all of the user's explicit requests \
and intents in detail
2. Key Technical Concepts: List all important technical concepts, technologies, \
and frameworks discussed.
3. Files and Code Sections: Enumerate specific files and code sections examined, \
modified, or created. Pay special attention to the most recent messages and \
include full code snippets where applicable and include a summary of why this \
file read or edit is important.
4. Errors and fixes: List all errors that you ran into, and how you fixed them. \
Pay special attention to specific user feedback that you received, especially \
if the user told you to do something differently.
5. Problem Solving: Document problems solved and any ongoing troubleshooting efforts.
6. All user messages: List ALL user messages that are not tool results. These \
are critical for understanding the users' feedback and changing intent.
7. Pending Tasks: Outline any pending tasks that you have explicitly been asked \
to work on.
8. Current Work: Describe in detail precisely what was being worked on \
immediately before this summary request, paying special attention to the most \
recent messages from both user and assistant. Include file names and code \
snippets where applicable.
9. Optional Next Step: List the next step that you will take that is related to \
the most recent work you were doing. IMPORTANT: ensure that this step is \
DIRECTLY in line with the user's most recent explicit requests, and the task \
you were working on immediately before this summary request.

REMINDER: Do NOT call any tools. Respond with plain text only — an <analysis> \
block followed by a <summary> block. Tool calls will be rejected.
"""


def _format_compact_summary(raw_summary: str) -> str:
    """Strip <analysis> scratchpad and format the <summary> section."""
    import re

    # Strip analysis section
    result = re.sub(r"<analysis>[\s\S]*?</analysis>", "", raw_summary)

    # Extract and format summary section
    match = re.search(r"<summary>([\s\S]*?)</summary>", result)
    if match:
        content = match.group(1).strip()
        result = re.sub(
            r"<summary>[\s\S]*?</summary>",
            f"Summary:\n{content}",
            result,
        )

    # Clean up extra whitespace
    result = re.sub(r"\n\n+", "\n\n", result)
    return result.strip()


def _build_post_compact_summary_message(summary: str) -> str:
    """Build the user message injected after compaction."""
    formatted = _format_compact_summary(summary)
    return (
        "This session is being continued from a previous conversation "
        "that ran out of context. The summary below covers the earlier "
        "portion of the conversation.\n\n"
        f"{formatted}\n\n"
        "Continue the conversation from where it left off without asking "
        "the user any further questions. Resume directly — do not "
        "acknowledge the summary, do not recap what was happening, "
        "do not preface with \"I'll continue\" or similar. Pick up the "
        "last task as if the break never happened."
    )


def _estimate_chars(messages: list[Message]) -> int:
    """Estimate total character count of a message list."""
    total = 0
    for msg in messages:
        total += len(msg.content)
        for tc in msg.tool_calls:
            total += len(tc.name) + len(str(tc.arguments)) + 50
        for tr in msg.tool_results:
            total += len(tr.content) + len(tr.name) + 50
    return total


class AutoCompactor:
    """Autocompact engine for the agentic loop.

    1. Check if token count exceeds threshold
    2. Use LLM to generate 9-section summary
    3. Replace messages with [system, summary_user_msg]
    4. Restore critical context (skill catalog, etc.)

    Circuit breaker: stops after MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES.
    """

    def __init__(
        self,
        provider: Provider,
        max_context_chars: int = DEFAULT_MAX_CONTEXT_CHARS,
        file_tracker: ReadFileTracker | None = None,
    ) -> None:
        self._provider = provider
        self._threshold = get_autocompact_threshold(max_context_chars)
        self._consecutive_failures = 0
        self._file_tracker = file_tracker

    @property
    def threshold(self) -> int:
        """Current autocompact threshold in characters."""
        return self._threshold

    async def maybe_compact(
        self,
        messages: list[Message],
    ) -> list[Message] | None:
        """Check if compaction is needed and perform it.

        Args:
            messages: Current conversation message list.

        Returns:
            New message list if compacted, None if no compaction needed.
        """
        # Circuit breaker
        if self._consecutive_failures >= MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES:
            logger.warning(
                "Autocompact circuit breaker active — skipping "
                f"({self._consecutive_failures} consecutive failures)."
            )
            return None

        total_chars = _estimate_chars(messages)
        if total_chars <= self._threshold:
            return None

        logger.info(
            f"Autocompact triggered: {total_chars} chars > "
            f"threshold {self._threshold}"
        )

        try:
            result = await self._perform_compaction(messages)
            self._consecutive_failures = 0
            return result
        except Exception as e:
            self._consecutive_failures += 1
            logger.error(
                f"Autocompact failed ({self._consecutive_failures}/"
                f"{MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES}): {e}"
            )
            return None

    async def force_compact(
        self,
        messages: list[Message],
    ) -> list[Message] | None:
        """Force compaction regardless of threshold.

        Used for reactive compaction on prompt-too-long errors.
        Bypasses threshold check and circuit breaker.

        Args:
            messages: Current conversation message list.

        Returns:
            New message list if compacted, None on failure.
        """
        logger.info("Reactive compact (forced): bypassing threshold check")

        try:
            result = await self._perform_compaction(messages)
            self._consecutive_failures = 0
            return result
        except Exception as e:
            logger.error(f"Reactive compact failed: {e}")
            return None

    async def partial_compact(
        self,
        messages: list[Message],
        pivot_index: int | None = None,
        preserved_tail: int = PRESERVED_TAIL_MIN_MESSAGES,
    ) -> list[Message] | None:
        """Compact older messages while preserving the most recent tail.

        - Splits messages at a pivot index
        - Summarizes messages BEFORE the pivot (the "head")
        - Preserves messages AFTER the pivot (the "tail")
        - The system message is always kept

        This is useful when you want to keep recent context intact while
        reclaiming space from older conversation history.

        Args:
            messages: Current conversation message list.
            pivot_index: Index to split at. Messages before this index
                are summarized; messages at/after are preserved.
                If None, automatically calculated to preserve at least
                ``preserved_tail`` non-system messages from the end.
            preserved_tail: Minimum number of non-system messages to
                preserve at the end (used when pivot_index is None).

        Returns:
            New message list if compacted, None on failure.
        """
        if len(messages) < 3:
            # Need at least system + something to compact + something to keep
            logger.debug("Too few messages for partial compaction")
            return None

        # Auto-calculate pivot: preserve at least `preserved_tail` messages
        if pivot_index is None:
            # Count non-system messages from the end
            non_system_count = 0
            pivot = len(messages)
            for i in range(len(messages) - 1, 0, -1):
                if messages[i].role != Role.SYSTEM:
                    non_system_count += 1
                if non_system_count >= preserved_tail:
                    pivot = i
                    break
            pivot_index = pivot

        # Validate pivot
        if pivot_index <= 0 or pivot_index >= len(messages):
            logger.debug(
                f"Invalid pivot index {pivot_index} for "
                f"{len(messages)} messages"
            )
            return None

        # Split messages
        head = messages[:pivot_index]  # to be summarized
        tail = messages[pivot_index:]  # to be preserved

        # Ensure we have something to summarize (excluding system)
        non_system_head = [m for m in head if m.role != Role.SYSTEM]
        if not non_system_head:
            logger.debug("No non-system messages to compact in head")
            return None

        logger.info(
            f"Partial compact: summarizing {len(head)} head messages, "
            f"preserving {len(tail)} tail messages"
        )

        try:
            # Compact only the head portion
            compacted_head = await self._perform_compaction(head)
            # Result is [system, summary_user_msg, (optional file restore)]
            # Append the preserved tail
            result = compacted_head + tail
            self._consecutive_failures = 0
            logger.info(
                f"Partial compact complete: "
                f"{len(messages)} → {len(result)} messages"
            )
            return result
        except Exception as e:
            self._consecutive_failures += 1
            logger.error(f"Partial compact failed: {e}")
            return None

    async def _perform_compaction(
        self,
        messages: list[Message],
    ) -> list[Message]:
        """Compact the conversation into a summary.

        Flow:
        1. Send all messages + compact prompt to LLM
        2. Parse summary from response
        3. Build new message list: [system, summary_as_user_msg]
        """
        pre_chars = _estimate_chars(messages)

        # Build the summary request: feed conversation + compact prompt
        summary_messages: list[Message] = [
            Message(
                role=Role.SYSTEM,
                content=(
                    "You are a helpful AI assistant tasked with "
                    "summarizing conversations."
                ),
            ),
        ]

        # Include the conversation content (skip system msg for the summary
        # request — it goes into the compact prompt context)
        for msg in messages:
            if msg.role == Role.SYSTEM:
                # Include system prompt as context in user message
                summary_messages.append(
                    Message(
                        role=Role.USER,
                        content=f"[System context]\n{msg.content}",
                    )
                )
            elif msg.role == Role.ASSISTANT:
                content = msg.content
                if msg.tool_calls:
                    tool_summary = ", ".join(
                        f"{tc.name}({tc.arguments})" for tc in msg.tool_calls
                    )
                    content = f"{content}\n[Tool calls: {tool_summary}]"
                if content.strip():
                    summary_messages.append(
                        Message(role=Role.ASSISTANT, content=content)
                    )
            elif msg.role == Role.USER:
                summary_messages.append(
                    Message(role=Role.USER, content=msg.content)
                )
            elif msg.role == Role.TOOL:
                # Summarize tool results compactly
                results_summary = "\n".join(
                    f"[{tr.name}]: {tr.content[:500]}..."
                    if len(tr.content) > 500
                    else f"[{tr.name}]: {tr.content}"
                    for tr in msg.tool_results
                )
                summary_messages.append(
                    Message(role=Role.USER, content=f"[Tool results]\n{results_summary}")
                )

        # Add the compact prompt
        summary_messages.append(
            Message(role=Role.USER, content=COMPACT_PROMPT)
        )

        # Call LLM for summary
        response = await self._provider.chat_completion(summary_messages)
        summary_text = response.text

        if not summary_text:
            raise RuntimeError("Compaction produced empty summary")

        # Build post-compact messages
        # Keep the original system message
        system_msg = None
        for msg in messages:
            if msg.role == Role.SYSTEM:
                system_msg = msg
                break

        new_messages: list[Message] = []
        if system_msg:
            new_messages.append(system_msg)

        # Add summary as a user message
        summary_user_content = _build_post_compact_summary_message(summary_text)
        new_messages.append(
            Message(role=Role.USER, content=summary_user_content)
        )

        # Post-compact file restoration:
        # Re-inject recently-read files so the model doesn't lose
        # context about files it was working with.
        if self._file_tracker:
            recent_files = self._file_tracker.get_recent_files()
            if recent_files:
                file_parts: list[str] = [
                    "The following files were recently accessed and are "
                    "restored for context continuity:"
                ]
                for path, content in recent_files:
                    file_parts.append(f"\n--- {path} ---\n{content}")
                new_messages.append(
                    Message(role=Role.USER, content="\n".join(file_parts))
                )
                logger.info(
                    f"Post-compact: restored {len(recent_files)} file(s)"
                )

        post_chars = _estimate_chars(new_messages)
        logger.info(
            f"Autocompact complete: {pre_chars} → {post_chars} chars "
            f"({len(messages)} → {len(new_messages)} messages)"
        )

        return new_messages


class SessionCompactor:
    """Compacts MEMORY.md when it exceeds the token threshold.

    This is separate from AutoCompactor — it handles persistent memory
    file compaction, not in-context conversation compaction.

    Strategy:
    - Estimate tokens via len(text) // 4
    - When MEMORY.md grows too large, summarize with LLM
    - Circuit breaker: stop after 3 consecutive failures
    """

    MAX_CONSECUTIVE_FAILURES = 3

    def __init__(
        self,
        memory: object,  # MemoryManager — TYPE_CHECKING import avoided for clarity
        provider: Provider,
        threshold_tokens: int = 100_000,
    ) -> None:
        from openlist_ani.assistant.memory.manager import MemoryManager

        assert isinstance(memory, MemoryManager)
        self._memory: MemoryManager = memory
        self._provider = provider
        self._threshold = threshold_tokens
        self._failure_count = 0

    async def maybe_compact(self) -> bool:
        """Check if compaction is needed and perform it.

        Returns:
            True if compaction was performed, False otherwise.
        """
        if self._failure_count >= self.MAX_CONSECUTIVE_FAILURES:
            logger.warning("Session compactor circuit breaker active -- skipping.")
            return False

        memory_text = self._memory.load_memory()
        token_count = self._memory.estimate_tokens(memory_text)

        if token_count < self._threshold:
            return False

        logger.info(
            f"MEMORY.md ({token_count} tokens) exceeds threshold "
            f"({self._threshold}), compacting..."
        )

        try:
            await self._perform_compaction(memory_text)
            self._failure_count = 0
            return True
        except Exception as e:
            self._failure_count += 1
            logger.error(
                f"Compaction failed ({self._failure_count}/"
                f"{self.MAX_CONSECUTIVE_FAILURES}): {e}"
            )
            return False

    async def _perform_compaction(self, memory_text: str) -> None:
        """Summarize and rewrite MEMORY.md."""
        summary = await self._summarize(memory_text)

        # Rewrite MEMORY.md with the compacted summary
        memory_file = self._memory.data_dir / "MEMORY.md"
        memory_file.write_text(summary, encoding="utf-8")

        logger.info("MEMORY.md compacted successfully.")

    async def _summarize(self, text: str) -> str:
        """Use the LLM to summarize memory content."""
        messages = [
            Message(
                role=Role.SYSTEM,
                content=(
                    "You are a memory compactor. Summarize the following "
                    "long-term memory entries into a more concise version, "
                    "preserving all key facts and their timestamps. "
                    "Keep each entry as a single line starting with '- '."
                ),
            ),
            Message(
                role=Role.USER,
                content=f"Compact these memory entries:\n\n{text}",
            ),
        ]

        response = await self._provider.chat_completion(messages)
        return response.text
