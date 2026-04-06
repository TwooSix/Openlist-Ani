"""
AgenticLoop -- the core while-loop driving the assistant.

Repeatedly calls the model, dispatches tool calls via ToolOrchestrator,
and continues until a text-only response or max rounds reached.

Context management (6-layer pipeline):
1. Tool result truncation — per-result + per-message budget (orchestrator)
2. Context truncation — drop old messages if over window limit
3. Autocompact — LLM-summarized compaction when threshold exceeded

Error recovery:
1. Prompt-too-long — force reactive compact, then retry
2. Transient errors — exponential backoff retry (rate-limit, overloaded, network)
3. Max output tokens — continue message injection, then retry
4. Unrecoverable — surface graceful error message

Session persistence: each final response is appended to the
daily SESSION_YYYYMMDD.md file so conversations survive restarts.

Multi-turn conversation: the loop maintains a message list across
user turns so the model has access to full conversation history.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING

from openlist_ani.assistant._constants import (
    API_RETRY_BACKOFF_BASE,
    DEFAULT_MAX_CONTEXT_CHARS,
    ESCALATED_MAX_TOKENS,
    MAX_API_RETRIES,
    MAX_OUTPUT_TOKENS_CONTINUE_MESSAGE,
    MAX_OUTPUT_TOKENS_RECOVERY_LIMIT,
    MAX_TOOL_ROUNDS,
)
from openlist_ani.assistant.core.models import (
    EventType,
    LoopEvent,
    Message,
    ProviderResponse,
    Role,
)
from openlist_ani.assistant.memory.compactor import AutoCompactor, ReadFileTracker
from openlist_ani.assistant.tool.orchestrator import ToolOrchestrator

if TYPE_CHECKING:
    from openlist_ani.assistant.core.context import ContextBuilder
    from openlist_ani.assistant.memory.manager import MemoryManager
    from openlist_ani.assistant.provider.base import Provider
    from openlist_ani.assistant.tool.registry import ToolRegistry

from loguru import logger


def _is_prompt_too_long(error: Exception) -> bool:
    """Check if the error is a prompt-too-long / context window overflow.

    Checks for the common error patterns across OpenAI and Anthropic SDKs.
    """
    msg = str(error).lower()
    return any(
        phrase in msg
        for phrase in (
            "prompt is too long",
            "maximum context length",
            "context_length_exceeded",
            "prompt too long",
            "input is too long",
            "request too large",
            "max_tokens",  # Anthropic: "max_tokens: ... is too large"
        )
    )


def _is_overloaded(error: Exception) -> bool:
    """Check if the error is a transient overload / rate-limit."""
    msg = str(error).lower()
    return any(
        phrase in msg
        for phrase in (
            "rate_limit",
            "rate limit",
            "overloaded",
            "too many requests",
            "429",
            "529",
            "503",
            "capacity",
        )
    )


def _is_transient(error: Exception) -> bool:
    """Check if the error is transient and worth retrying.

    Includes network errors, timeouts, and server errors.
    """
    if _is_overloaded(error):
        return True
    msg = str(error).lower()
    return any(
        phrase in msg
        for phrase in (
            "connection",
            "timeout",
            "timed out",
            "server error",
            "internal error",
            "500",
            "502",
            "504",
        )
    )


class AgenticLoop:
    """Core agentic loop that drives the assistant.

    Flow:
    1. Build context on first turn (system prompt + memory)
    2. Append user message to conversation history
    3. Pre-query pipeline: autocompact → context truncation
    4. Call provider.chat_completion()
    5. If tool_calls -> dispatch via ToolOrchestrator -> inject results -> continue
    6. If pure text -> yield final response -> persist to session -> exit loop
    7. Safety: max rounds limit

    Context management layers:
    - Tool result truncation: handled by ToolOrchestrator (per-result budget)
    - Autocompact: LLM-summarized compaction at ~80% of context window
    - Emergency truncation: drop oldest messages as last resort

    Error recovery:
    - Prompt-too-long: reactive compact → retry
    - Max output tokens (stop_reason=max_tokens): continue message injection
    - Max output tokens escalation: retry at higher max_tokens
    - Transient errors: exponential backoff retry
    - Tombstone: inject synthetic tool_results for orphaned tool_calls

    Turn tracking:
    - turn_count: incremented each time tool results are processed
    - Distinct from loop rounds — a turn is one model response + tool execution

    Thread-safety: an asyncio.Lock serialises concurrent calls to process()
    so that messages are never interleaved (important for Telegram's
    concurrent update dispatching).
    """

    def __init__(
        self,
        provider: Provider,
        registry: ToolRegistry,
        context: ContextBuilder,
        memory: MemoryManager,
        max_rounds: int = MAX_TOOL_ROUNDS,
        max_context_chars: int = DEFAULT_MAX_CONTEXT_CHARS,
    ) -> None:
        self._provider = provider
        self._registry = registry
        self._context = context
        self._memory = memory
        self._max_rounds = max_rounds
        self._max_context_chars = max_context_chars
        self._orchestrator = ToolOrchestrator(registry)
        self._file_tracker = ReadFileTracker()
        self._autocompactor = AutoCompactor(
            provider, max_context_chars, file_tracker=self._file_tracker
        )
        # Persistent conversation history across turns
        self._messages: list[Message] = []
        self._initialized = False
        # Serialise concurrent process() calls (prevents message interleaving)
        self._lock = asyncio.Lock()
        # Turn counter across process() calls
        self._turn_count = 0

    @property
    def turn_count(self) -> int:
        """Total turn count across all process() calls.

        A "turn" is one cycle of model-response + tool-execution
        within the agentic loop.
        """
        return self._turn_count

    async def _ensure_initialized(self) -> None:
        """Build the system prompt on first invocation."""
        if self._initialized:
            return
        system_messages = await self._context.build_system()
        self._messages.extend(system_messages)
        self._initialized = True

    def reset(self) -> None:
        """Reset conversation history (used by /clear, /reset)."""
        self._messages.clear()
        self._initialized = False
        self._file_tracker.clear()
        self._turn_count = 0

    @property
    def file_tracker(self) -> ReadFileTracker:
        """The file tracker for recording file reads.

        Tools that read files should call file_tracker.track(path, content)
        so the compactor can restore them after compaction.
        """
        return self._file_tracker

    def _truncate_if_needed(self) -> None:
        """Truncate old messages if context grows beyond the limit.

        Keeps the system prompt (first message) and the most recent
        messages that fit within max_context_chars. Older messages in
        the middle are dropped.

        This is called before every LLM call to prevent context
        window overflow.
        """
        total_chars = sum(self._estimate_message_chars(m) for m in self._messages)
        if total_chars <= self._max_context_chars:
            return

        # Always keep the system message (index 0)
        if not self._messages or self._messages[0].role != Role.SYSTEM:
            return

        system_msg = self._messages[0]
        system_chars = self._estimate_message_chars(system_msg)
        budget = self._max_context_chars - system_chars

        # Walk backwards from the end, keeping messages that fit
        kept: list[Message] = []
        used = 0
        for msg in reversed(self._messages[1:]):
            msg_chars = self._estimate_message_chars(msg)
            if used + msg_chars > budget:
                break
            kept.append(msg)
            used += msg_chars

        kept.reverse()

        old_count = len(self._messages)
        dropped = old_count - 1 - len(kept)  # -1 for system msg

        # Inject a truncation notice so the model knows context was lost
        if dropped > 0:
            notice = Message(
                role=Role.USER,
                content=(
                    f"[Context truncated: {dropped} older messages were dropped "
                    f"to stay within the context window. "
                    f"({total_chars} → {used + system_chars} chars). "
                    f"If you need information from earlier in the conversation, "
                    f"ask the user to repeat it.]"
                ),
            )
            self._messages = [system_msg, notice] + kept
            logger.info(
                f"Context truncated: dropped {dropped} old messages "
                f"({total_chars} → {used + system_chars} chars), "
                f"injected truncation notice"
            )

    @staticmethod
    def _estimate_message_chars(msg: Message) -> int:
        """Estimate the character count of a message."""
        chars = len(msg.content)
        for tc in msg.tool_calls:
            chars += len(tc.name) + len(str(tc.arguments)) + 50  # overhead
        for tr in msg.tool_results:
            chars += len(tr.content) + len(tr.name) + 50
        return chars

    async def _call_with_recovery(
        self,
        tool_defs: list[dict] | None,
        has_attempted_reactive_compact: bool,
        max_tokens_override: int | None = None,
    ) -> "ProviderResponse | None":
        """Call the provider with error recovery.

        Error handling strategy:
        1. Prompt-too-long → reactive compact → retry (returns None)
        2. Transient errors → exponential backoff retry
        3. Unrecoverable → raise with clear message

        Args:
            tool_defs: Tool definitions for the provider.
            has_attempted_reactive_compact: Whether reactive compact
                has already been attempted this turn.
            max_tokens_override: Override max_tokens for this call
                (used for escalation recovery).

        Returns:
            ProviderResponse on success, None if reactive compact was
            applied (caller should retry the round).

        Raises:
            RuntimeError: If all recovery attempts are exhausted.
        """

        last_error: Exception | None = None

        for attempt in range(MAX_API_RETRIES):
            try:
                response = await self._provider.chat_completion(
                    self._messages,
                    tool_defs if tool_defs else None,
                    max_tokens_override=max_tokens_override,
                )
                return response

            except Exception as e:
                last_error = e
                logger.warning(
                    f"Provider call failed (attempt {attempt + 1}/"
                    f"{MAX_API_RETRIES}): {e}"
                )

                # Prompt-too-long: try reactive compact once
                if _is_prompt_too_long(e) and not has_attempted_reactive_compact:
                    logger.info(
                        "Prompt-too-long detected — attempting reactive compact"
                    )
                    compacted = await self._autocompactor.force_compact(
                        self._messages
                    )
                    if compacted is not None:
                        self._messages = compacted
                        logger.info(
                            "Reactive compact succeeded — retrying"
                        )
                        return None  # Signal caller to retry the round

                    # Compact failed — also try emergency truncation
                    logger.warning(
                        "Reactive compact failed — attempting "
                        "aggressive truncation"
                    )
                    self._truncate_if_needed()
                    # Fall through to retry

                # Transient errors: backoff and retry
                elif _is_transient(e) and attempt < MAX_API_RETRIES - 1:
                    delay = API_RETRY_BACKOFF_BASE * (2 ** attempt)
                    logger.info(
                        f"Transient error — retrying in {delay:.1f}s"
                    )
                    await asyncio.sleep(delay)
                    continue

                # Non-transient, non-prompt-too-long: don't retry
                elif not _is_prompt_too_long(e) and not _is_transient(e):
                    break

        # All retries exhausted
        error_msg = f"Provider error after {MAX_API_RETRIES} attempts: {last_error}"
        logger.error(error_msg)
        raise RuntimeError(error_msg) from last_error

    async def process(self, user_message: str) -> AsyncGenerator[LoopEvent, None]:
        """Process a user message through the agentic loop.

        Yields LoopEvent objects for real-time UI updates:
        - THINKING: model is being called
        - TEXT_DELTA: streaming text chunk
        - TEXT_DONE: final assembled text
        - TOOL_START: tool execution starting
        - TOOL_END: tool execution finished
        - ERROR: error occurred

        Uses a queue-based approach to yield events from inside the lock.

        Args:
            user_message: The user's input message.

        Yields:
            LoopEvent objects for frontend rendering.
        """
        queue: asyncio.Queue[LoopEvent | None] = asyncio.Queue()

        async def _run() -> None:
            """Inner coroutine that runs under the lock and pushes events."""
            try:
                async with self._lock:
                    await self._process_locked(user_message, queue)
            except asyncio.CancelledError:
                logger.info("AgenticLoop task cancelled (user interrupt)")
                await queue.put(
                    LoopEvent(
                        type=EventType.TEXT_DONE,
                        text="(interrupted)",
                    )
                )
            except Exception as e:
                logger.opt(exception=True).error(f"AgenticLoop error: {e}")
                await queue.put(
                    LoopEvent(type=EventType.ERROR, text=str(e))
                )
            finally:
                await queue.put(None)  # Sentinel to signal completion

        task = asyncio.create_task(_run())

        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield event
        finally:
            # If the generator is closed (e.g. KeyboardInterrupt, break, or
            # async for exit), cancel the background task so it releases the
            # lock and doesn't hang the next call.
            if not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

        # Propagate any exception from the task
        if task.done() and task.exception():
            raise task.exception()

    async def _stream_provider_call(
        self,
        tool_defs: list[dict] | None,
        queue: asyncio.Queue[LoopEvent | None],
        has_attempted_reactive_compact: bool,
        max_tokens_override: int | None = None,
    ) -> ProviderResponse | None:
        """Call the provider with streaming, error recovery, and text delta events.

        Integrates _call_with_recovery's error handling into the streaming path.
        Returns ProviderResponse on success, None if reactive compact was applied.
        Raises RuntimeError if all recovery attempts are exhausted.
        """
        last_error: Exception | None = None

        for attempt in range(MAX_API_RETRIES):
            try:
                full_text_parts: list[str] = []
                final_response: ProviderResponse | None = None

                async for partial in self._provider.chat_completion_stream(
                    self._messages,
                    tool_defs if tool_defs else None,
                    max_tokens_override=max_tokens_override,
                ):
                    if partial.text:
                        full_text_parts.append(partial.text)
                        await queue.put(
                            LoopEvent(type=EventType.TEXT_DELTA, text=partial.text)
                        )
                    if partial.stop_reason or partial.tool_calls:
                        final_response = partial

                if final_response is None:
                    final_response = ProviderResponse(
                        text="".join(full_text_parts),
                        stop_reason="stop",
                    )

                if full_text_parts and not final_response.text:
                    final_response.text = "".join(full_text_parts)

                return final_response

            except Exception as e:
                last_error = e
                logger.warning(
                    f"Provider call failed (attempt {attempt + 1}/"
                    f"{MAX_API_RETRIES}): {e}"
                )

                # Prompt-too-long: try reactive compact once
                if _is_prompt_too_long(e) and not has_attempted_reactive_compact:
                    logger.info(
                        "Prompt-too-long detected — attempting reactive compact"
                    )
                    compacted = await self._autocompactor.force_compact(
                        self._messages
                    )
                    if compacted is not None:
                        self._messages = compacted
                        logger.info("Reactive compact succeeded — retrying")
                        return None  # Signal caller to retry the round

                    logger.warning(
                        "Reactive compact failed — attempting aggressive truncation"
                    )
                    self._truncate_if_needed()

                # Transient errors: backoff and retry
                elif _is_transient(e) and attempt < MAX_API_RETRIES - 1:
                    delay = API_RETRY_BACKOFF_BASE * (2 ** attempt)
                    logger.info(f"Transient error — retrying in {delay:.1f}s")
                    await asyncio.sleep(delay)
                    continue

                # Non-transient, non-prompt-too-long: don't retry
                elif not _is_prompt_too_long(e) and not _is_transient(e):
                    break

        # All retries exhausted
        error_msg = f"Provider error after {MAX_API_RETRIES} attempts: {last_error}"
        logger.error(error_msg)
        raise RuntimeError(error_msg) from last_error

    async def _process_locked(
        self,
        user_message: str,
        queue: asyncio.Queue[LoopEvent | None],
    ) -> None:
        """Core processing logic that runs under the lock.

        Pushes LoopEvent objects into the queue for the consumer.
        """
        try:
            await self._ensure_initialized()

            # Append user message to conversation history
            self._messages.append(Message(role=Role.USER, content=user_message))

            tool_defs = self._provider.format_tool_definitions(
                self._registry.all_tools()
            )

            # Track tool usage for session logging
            tool_names_used: list[str] = []
            # Track whether we've already attempted reactive compact this turn
            has_attempted_reactive_compact = False
            # Max output tokens recovery state
            max_output_tokens_recovery_count = 0
            max_tokens_override: int | None = None

            for round_num in range(self._max_rounds):
                logger.debug(f"AgenticLoop round {round_num + 1}/{self._max_rounds}")

                # Context management pipeline
                compacted = await self._autocompactor.maybe_compact(self._messages)
                if compacted is not None:
                    self._messages = compacted
                    logger.info("Autocompact applied — messages replaced with summary")

                self._truncate_if_needed()

                # Signal: thinking
                await queue.put(LoopEvent(type=EventType.THINKING))

                # Call provider with streaming + integrated error recovery
                response = await self._stream_provider_call(
                    tool_defs, queue, has_attempted_reactive_compact,
                    max_tokens_override,
                )

                # Handle reactive compact signal
                if response is None:
                    has_attempted_reactive_compact = True
                    max_tokens_override = None
                    continue

                if not response.tool_calls:
                    # Check for max_output_tokens hit
                    if response.stop_reason in ("max_tokens", "length"):
                        if (
                            max_tokens_override is None
                            and max_output_tokens_recovery_count == 0
                        ):
                            max_tokens_override = ESCALATED_MAX_TOKENS
                            logger.info(
                                f"Max output tokens hit — escalating to "
                                f"{ESCALATED_MAX_TOKENS} tokens"
                            )
                            if response.text:
                                self._messages.append(
                                    Message(
                                        role=Role.ASSISTANT,
                                        content=response.text,
                                    )
                                )
                            continue

                        if (
                            max_output_tokens_recovery_count
                            < MAX_OUTPUT_TOKENS_RECOVERY_LIMIT
                        ):
                            max_output_tokens_recovery_count += 1
                            max_tokens_override = None
                            logger.info(
                                f"Max output tokens recovery "
                                f"({max_output_tokens_recovery_count}/"
                                f"{MAX_OUTPUT_TOKENS_RECOVERY_LIMIT})"
                            )
                            if response.text:
                                self._messages.append(
                                    Message(
                                        role=Role.ASSISTANT,
                                        content=response.text,
                                    )
                                )
                            self._messages.append(
                                Message(
                                    role=Role.USER,
                                    content=MAX_OUTPUT_TOKENS_CONTINUE_MESSAGE,
                                )
                            )
                            continue

                    # Pure text response
                    if response.text:
                        self._messages.append(
                            Message(role=Role.ASSISTANT, content=response.text)
                        )
                        await queue.put(
                            LoopEvent(
                                type=EventType.TEXT_DONE,
                                text=response.text,
                            )
                        )
                        # Persist the conversation turn to session file
                        tool_context = (
                            ", ".join(tool_names_used)
                            if tool_names_used
                            else ""
                        )
                        await self._memory.append_turn(
                            user_msg=user_message,
                            assistant_msg=response.text,
                            tool_context=tool_context,
                        )
                    break

                # Reset max_tokens_override after successful tool response
                max_tokens_override = None

                # Has tool_calls -> dispatch and continue loop
                self._messages.append(
                    Message(
                        role=Role.ASSISTANT,
                        content=response.text,
                        tool_calls=response.tool_calls,
                    )
                )

                # Track tool names and emit TOOL_START events
                for tc in response.tool_calls:
                    tool_names_used.append(tc.name)
                    tool = self._registry.get(tc.name)
                    activity = (
                        tool.get_activity_description(tc.arguments)
                        if tool
                        else None
                    )
                    await queue.put(
                        LoopEvent(
                            type=EventType.TOOL_START,
                            tool_name=tc.name,
                            tool_args=tc.arguments,
                            activity=activity or f"Running {tc.name}",
                        )
                    )

                # Dispatch via orchestrator (parallel/serial batching)
                results = await self._orchestrator.execute_tool_calls(
                    response.tool_calls
                )

                # Emit TOOL_END events
                for result in results:
                    preview = (
                        result.content[:200] + "..."
                        if len(result.content) > 200
                        else result.content
                    )
                    await queue.put(
                        LoopEvent(
                            type=EventType.TOOL_END,
                            tool_name=result.name,
                            tool_result_preview=preview,
                        )
                    )

                # Tombstone handling
                result_ids = {r.tool_call_id for r in results}
                for tc in response.tool_calls:
                    if tc.id not in result_ids:
                        from openlist_ani.assistant.core.models import ToolResult
                        results.append(
                            ToolResult(
                                tool_call_id=tc.id,
                                name=tc.name,
                                content="Error: Tool execution was interrupted.",
                                is_error=True,
                            )
                        )
                        logger.warning(
                            f"Tombstone: injected synthetic result for "
                            f"orphaned tool_call {tc.name} ({tc.id})"
                        )

                self._messages.append(
                    Message(role=Role.TOOL, tool_results=results)
                )

                # Increment turn count
                self._turn_count += 1

            else:
                # Max rounds reached
                max_rounds_msg = "Reached maximum tool call rounds."
                self._messages.append(
                    Message(role=Role.ASSISTANT, content=max_rounds_msg)
                )
                await queue.put(
                    LoopEvent(type=EventType.TEXT_DONE, text=max_rounds_msg)
                )
                await self._memory.append_turn(
                    user_msg=user_message,
                    assistant_msg=max_rounds_msg,
                    tool_context=(
                        ", ".join(tool_names_used)
                        if tool_names_used
                        else ""
                    ),
                )

        except RuntimeError as e:
            # Unrecoverable provider error
            error_msg = (
                "I'm sorry, I encountered an error while processing your "
                "request. Please try again."
            )
            logger.error(f"AgenticLoop unrecoverable error: {e}")
            self._messages.append(
                Message(role=Role.ASSISTANT, content=error_msg)
            )
            await queue.put(
                LoopEvent(type=EventType.TEXT_DONE, text=error_msg)
            )
