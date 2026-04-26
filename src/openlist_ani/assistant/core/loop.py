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

Session persistence: messages are recorded to JSONL session files via
SessionStorage so conversations survive restarts and can be resumed.

Multi-turn conversation: the loop maintains a message list across
user turns so the model has access to full conversation history.
"""

from __future__ import annotations

import asyncio
from asyncio import sleep as _async_sleep
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Literal, NoReturn

from openlist_ani.assistant._constants import (
    API_RETRY_BACKOFF_BASE,
    DEFAULT_MAX_CONTEXT_CHARS,
    ESCALATED_MAX_TOKENS,
    INTERRUPTED_TEXT,
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
    ToolResult,
)
from openlist_ani.assistant.memory.compactor import AutoCompactor, ReadFileTracker
from openlist_ani.assistant.tool.orchestrator import ToolOrchestrator, apply_per_message_budget

if TYPE_CHECKING:
    from openlist_ani.assistant.core.cancellation import CancellationToken
    from openlist_ani.assistant.core.context import ContextBuilder
    from openlist_ani.assistant.dream.runner import AutoDreamRunner
    from openlist_ani.assistant.memory.manager import MemoryManager
    from openlist_ani.assistant.provider.base import Provider
    from openlist_ani.assistant.session.storage import SessionStorage
    from openlist_ani.assistant.tool.registry import ToolRegistry

from openlist_ani.assistant.core.message_queue import MessageQueue

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
        message_queue: MessageQueue | None = None,
        session_storage: SessionStorage | None = None,
        auto_dream_runner: AutoDreamRunner | None = None,
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
        # Session persistence (JSONL-based)
        self._session_storage = session_storage
        # Auto-dream runner (background memory consolidation)
        self._auto_dream_runner = auto_dream_runner
        # Serialise concurrent process() calls (prevents message interleaving)
        self._lock = asyncio.Lock()
        # Turn counter across process() calls
        self._turn_count = 0
        # Pending user message queue — injected by frontend during active turns
        self._message_queue = message_queue if message_queue is not None else MessageQueue()
        # Background auto-dream tasks (tracked for cleanup)
        self._dream_tasks: set[asyncio.Task] = set()

    @property
    def turn_count(self) -> int:
        """Total turn count across all process() calls.

        A "turn" is one cycle of model-response + tool-execution
        within the agentic loop.
        """
        return self._turn_count

    def _ensure_initialized(self) -> None:
        """Build the system prompt on first invocation."""
        if self._initialized:
            return
        system_messages = self._context.build_system()
        self._messages.extend(system_messages)
        self._initialized = True

    def reset(self) -> None:
        """Reset conversation history (used by /clear, /reset)."""
        self._messages.clear()
        self._initialized = False
        self._file_tracker.clear()
        self._turn_count = 0

    async def shutdown(self) -> None:
        """Cancel background tasks and close owned resources.

        Called during application exit to prevent resource leaks
        (orphaned asyncio tasks, open file handles).
        """
        # Cancel pending auto-dream tasks
        for task in self._dream_tasks:
            task.cancel()
        if self._dream_tasks:
            await asyncio.gather(*self._dream_tasks, return_exceptions=True)
        self._dream_tasks.clear()

        # Close session storage file handle
        if self._session_storage:
            self._session_storage.close()

    async def resume(self, session_id: str) -> None:
        """Resume a previous session.

        Loads the message chain from the session JSONL file into
        ``self._messages`` and switches the session storage to append
        to the same file.
        """
        if not self._session_storage:
            logger.warning("Cannot resume: no session storage configured")
            return

        # Build system prompt first
        self._ensure_initialized()

        # Load the session messages
        messages = await self._session_storage.load_session(session_id)
        if messages:
            self._messages.extend(messages)

        # Switch the storage to append to this session
        await self._session_storage.switch_session(session_id)

        # Inject a system message noting the resume
        resume_note = Message(
            role=Role.SYSTEM,
            content=(
                "This session is being resumed. Continue from where "
                "you left off."
            ),
        )
        self._messages.append(resume_note)
        logger.info(
            f"Resumed session {session_id} with {len(messages)} messages"
        )

    @property
    def session_storage(self) -> SessionStorage | None:
        """The session storage instance, if configured."""
        return self._session_storage

    @property
    def file_tracker(self) -> ReadFileTracker:
        """The file tracker for recording file reads.

        Tools that read files should call file_tracker.track(path, content)
        so the compactor can restore them after compaction.
        """
        return self._file_tracker

    @property
    def message_queue(self) -> MessageQueue:
        """The message queue for injecting user messages during an active turn.

        Frontends should call ``message_queue.enqueue(PendingMessage(...))``
        when a user sends input while the loop is processing.
        """
        return self._message_queue

    @property
    def auto_dream_runner(self) -> AutoDreamRunner | None:
        """The auto-dream runner for background memory consolidation."""
        return self._auto_dream_runner

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
        if msg.reasoning_content:
            chars += len(msg.reasoning_content)
        for tb in msg.thinking_blocks:
            chars += len(tb.get("thinking", "")) + 50
        for tc in msg.tool_calls:
            chars += len(tc.name) + len(str(tc.arguments)) + 50  # overhead
        for tr in msg.tool_results:
            chars += len(tr.content) + len(tr.name) + 50
        return chars

    async def _handle_provider_error(
        self,
        error: Exception,
        attempt: int,
        has_attempted_reactive_compact: bool,
    ) -> Literal["compact_applied", "retry", "break"]:
        """Classify a provider error and apply recovery.

        Returns an action literal:
        - ``"compact_applied"``: reactive compact succeeded, caller should
          signal retry-round (return None).
        - ``"retry"``: caller should continue to the next attempt.
        - ``"break"``: caller should stop retrying immediately.
        """
        if _is_prompt_too_long(error) and not has_attempted_reactive_compact:
            logger.info("Prompt-too-long detected — attempting reactive compact")
            compacted = await self._autocompactor.force_compact(self._messages)
            if compacted is not None:
                self._messages = compacted
                logger.info("Reactive compact succeeded — retrying")
                return "compact_applied"
            # Compact failed — also try emergency truncation
            logger.warning(
                "Reactive compact failed — attempting aggressive truncation"
            )
            self._truncate_if_needed()
            return "retry"

        if _is_transient(error) and attempt < MAX_API_RETRIES - 1:
            delay = API_RETRY_BACKOFF_BASE * (2 ** attempt)
            logger.info(f"Transient error — retrying in {delay:.1f}s")
            await _async_sleep(delay)
            return "retry"

        if not _is_prompt_too_long(error) and not _is_transient(error):
            return "break"

        return "retry"

    @staticmethod
    def _raise_exhausted(last_error: Exception | None) -> NoReturn:
        """Raise RuntimeError after all retry attempts are exhausted."""
        error_msg = (
            f"Provider error after {MAX_API_RETRIES} attempts: {last_error}"
        )
        logger.error(error_msg)
        raise RuntimeError(error_msg) from last_error

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
                return await self._provider.chat_completion(
                    self._messages,
                    tool_defs if tool_defs else None,
                    max_tokens_override=max_tokens_override,
                )
            except Exception as e:
                last_error = e
                logger.warning(
                    f"Provider call failed (attempt {attempt + 1}/"
                    f"{MAX_API_RETRIES}): {e}"
                )
                action = await self._handle_provider_error(
                    e, attempt, has_attempted_reactive_compact,
                )
                if action == "compact_applied":
                    return None  # Signal caller to retry the round
                if action == "break":
                    break

        self._raise_exhausted(last_error)

    async def process(
        self,
        user_message: str,
        cancel_token: CancellationToken | None = None,
    ) -> AsyncGenerator[LoopEvent, None]:
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
            cancel_token: Optional cancellation token for cooperative
                interruption.  When cancelled, the loop emits a
                ``TEXT_DONE("(interrupted)")`` event and stops early.

        Yields:
            LoopEvent objects for frontend rendering.
        """
        queue: asyncio.Queue[LoopEvent | None] = asyncio.Queue()

        async def _run() -> None:
            """Inner coroutine that runs under the lock and pushes events."""
            try:
                async with self._lock:
                    await self._process_locked(user_message, queue, cancel_token)
            except asyncio.CancelledError:
                logger.info("AgenticLoop task cancelled (user interrupt)")
                await queue.put(
                    LoopEvent(
                        type=EventType.TEXT_DONE,
                        text=INTERRUPTED_TEXT,
                    )
                )
                raise
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

    async def _collect_stream(
        self,
        tool_defs: list[dict] | None,
        queue: asyncio.Queue[LoopEvent | None],
        max_tokens_override: int | None = None,
        cancel_token: CancellationToken | None = None,
    ) -> ProviderResponse:
        """Consume the provider stream, emitting TEXT_DELTA events.

        Returns the assembled ProviderResponse.
        """
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
                # Checkpoint 2: cancellation after each stream chunk
                if cancel_token and cancel_token.is_cancelled:
                    break
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

    async def _stream_provider_call(
        self,
        tool_defs: list[dict] | None,
        queue: asyncio.Queue[LoopEvent | None],
        has_attempted_reactive_compact: bool,
        max_tokens_override: int | None = None,
        cancel_token: CancellationToken | None = None,
    ) -> ProviderResponse | None:
        """Call the provider with streaming, error recovery, and text delta events.

        Integrates _handle_provider_error's error handling into the streaming
        path.  Returns ProviderResponse on success, None if reactive compact
        was applied.  Raises RuntimeError if all recovery attempts are
        exhausted.
        """
        last_error: Exception | None = None

        for attempt in range(MAX_API_RETRIES):
            # Checkpoint 3: cancellation before retry attempt
            if cancel_token and cancel_token.is_cancelled:
                return None

            try:
                return await self._collect_stream(
                    tool_defs, queue, max_tokens_override, cancel_token,
                )
            except Exception as e:
                last_error = e
                logger.warning(
                    f"Provider call failed (attempt {attempt + 1}/"
                    f"{MAX_API_RETRIES}): {e}"
                )
                action = await self._handle_provider_error(
                    e, attempt, has_attempted_reactive_compact,
                )
                if action == "compact_applied":
                    return None  # Signal caller to retry the round
                if action == "break":
                    break

        self._raise_exhausted(last_error)

    def _handle_max_tokens_hit(
        self,
        response: ProviderResponse,
        max_tokens_override: int | None,
        max_output_tokens_recovery_count: int,
    ) -> tuple[bool, int | None, int]:
        """Handle a max_output_tokens stop reason.

        Returns (should_continue, new_max_tokens_override, new_recovery_count).
        If should_continue is True, the caller should ``continue`` the loop.
        If should_continue is False, max_tokens recovery is exhausted.

        Side-effects:
            Appends assistant and/or user messages to ``self._messages``
            when recovery is attempted (escalation or continue-message).
        """
        if response.stop_reason not in ("max_tokens", "length"):
            return False, max_tokens_override, max_output_tokens_recovery_count

        # First hit: escalate token limit
        if max_tokens_override is None and max_output_tokens_recovery_count == 0:
            logger.info(
                f"Max output tokens hit — escalating to "
                f"{ESCALATED_MAX_TOKENS} tokens"
            )
            if response.text:
                self._messages.append(
                    Message(
                        role=Role.ASSISTANT,
                        content=response.text,
                        reasoning_content=response.reasoning_content,
                        thinking_blocks=response.thinking_blocks,
                    )
                )
            return True, ESCALATED_MAX_TOKENS, max_output_tokens_recovery_count

        # Subsequent hits: inject continue message
        if max_output_tokens_recovery_count < MAX_OUTPUT_TOKENS_RECOVERY_LIMIT:
            max_output_tokens_recovery_count += 1
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
                        reasoning_content=response.reasoning_content,
                        thinking_blocks=response.thinking_blocks,
                    )
                )
            self._messages.append(
                Message(
                    role=Role.USER,
                    content=MAX_OUTPUT_TOKENS_CONTINUE_MESSAGE,
                )
            )
            return True, None, max_output_tokens_recovery_count

        return False, max_tokens_override, max_output_tokens_recovery_count

    async def _finalize_text_response(
        self,
        response: ProviderResponse,
        queue: asyncio.Queue[LoopEvent | None],
    ) -> None:
        """Persist a pure-text response and emit TEXT_DONE."""
        if not response.text:
            return
        self._messages.append(
            Message(
                role=Role.ASSISTANT,
                content=response.text,
                reasoning_content=response.reasoning_content,
                thinking_blocks=response.thinking_blocks,
            )
        )
        await queue.put(
            LoopEvent(type=EventType.TEXT_DONE, text=response.text)
        )
        # Persist to JSONL session storage
        if self._session_storage:
            await self._session_storage.record_message(
                Message(
                    role=Role.ASSISTANT,
                    content=response.text,
                    reasoning_content=response.reasoning_content,
                    thinking_blocks=response.thinking_blocks,
                )
            )
        # Fire-and-forget: check auto-dream gates (tracked for cleanup)
        if self._auto_dream_runner and self._session_storage:
            task = asyncio.create_task(self._try_auto_dream())
            self._dream_tasks.add(task)
            task.add_done_callback(self._dream_tasks.discard)

    async def _try_auto_dream(self) -> None:
        """Background auto-dream check. Non-blocking."""
        try:
            if not self._auto_dream_runner or not self._session_storage:
                return
            result = await self._auto_dream_runner.maybe_run(
                current_session_id=self._session_storage.session_id
            )
            if result and result.files_touched:
                logger.info(
                    f"Auto-dream updated: {', '.join(result.files_touched)}"
                )
        except Exception as e:
            logger.warning(f"Auto-dream failed: {e}")

    async def _emit_tool_start_events(
        self,
        response: ProviderResponse,
        tool_names_used: list[str],
        queue: asyncio.Queue[LoopEvent | None],
    ) -> None:
        """Record tool names and emit TOOL_START events."""
        for tc in response.tool_calls:
            tool_names_used.append(tc.name)
            tool = self._registry.get(tc.name)
            activity = (
                tool.get_activity_description(tc.arguments)
                if tool
                else None
            )
            logger.info(
                f"Tool call: {tc.name} "
                f"(args={tc.arguments})"
            )
            await queue.put(
                LoopEvent(
                    type=EventType.TOOL_START,
                    tool_name=tc.name,
                    tool_args=tc.arguments,
                    activity=activity or f"Running {tc.name}",
                )
            )

    async def _dispatch_tool_calls(
        self,
        response: ProviderResponse,
        tool_names_used: list[str],
        queue: asyncio.Queue[LoopEvent | None],
        cancel_token: CancellationToken | None = None,
    ) -> bool:
        """Dispatch tool calls and append results to messages.

        Handles TOOL_START / TOOL_END events, orchestrator dispatch,
        tombstone injection for orphaned or interrupted tool_calls,
        and mid-turn user message injection.

        Returns:
            True if interrupted by a pending user message (caller should
            continue to the next API call — the model will see the user
            message in context). False if all tools completed normally.
        """
        self._messages.append(
            Message(
                role=Role.ASSISTANT,
                content=response.text,
                tool_calls=response.tool_calls,
                reasoning_content=response.reasoning_content,
                thinking_blocks=response.thinking_blocks,
            )
        )

        await self._emit_tool_start_events(response, tool_names_used, queue)

        # Execute tools, collecting results until completion or interruption
        completed_results, executed_tool_ids, interrupted = (
            await self._execute_tools_until_interrupt(
                response, queue, cancel_token,
            )
        )

        # Handle tombstone injection based on interruption state
        if interrupted:
            self._inject_interrupted_tombstones(
                response, completed_results, executed_tool_ids,
                cancel_token,
            )
        else:
            self._inject_tombstones(response, completed_results)

        # Apply truncation budget on collected results
        completed_results = apply_per_message_budget(completed_results)

        self._messages.append(
            Message(role=Role.TOOL, tool_results=completed_results)
        )
        self._turn_count += 1

        if interrupted:
            await self._drain_pending_messages(queue)

        return interrupted

    async def _execute_tools_until_interrupt(
        self,
        response: ProviderResponse,
        queue: asyncio.Queue[LoopEvent | None],
        cancel_token: CancellationToken | None = None,
    ) -> tuple[list[ToolResult], set[str], bool]:
        """Execute tool calls, stopping early on cancellation or new user input.

        Returns:
            Tuple of (completed_results, executed_tool_ids, interrupted).
        """
        completed_results: list[ToolResult] = []
        executed_tool_ids: set[str] = set()

        async for result in self._orchestrator.execute_tool_calls(response.tool_calls):
            completed_results.append(result)
            executed_tool_ids.add(result.tool_call_id)

            logger.debug(
                f"Tool '{result.name}' finished "
                f"(error={result.is_error}, {len(result.content)} chars)"
            )

            # Emit TOOL_END for this result
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

            # Yield to event loop so that any pending background tasks
            # (e.g. frontend enqueuing a user message) get a chance to run.
            await asyncio.sleep(0)

            # Checkpoint 4: cancellation between tool executions
            if cancel_token and cancel_token.is_cancelled:
                logger.info(
                    "Cancellation token set — interrupting remaining tools"
                )
                return completed_results, executed_tool_ids, True

            # Check for pending user messages between tool executions
            if self._message_queue.has_pending_prompts():
                logger.info(
                    "Pending user message detected mid-tool-execution — "
                    "interrupting remaining tools"
                )
                return completed_results, executed_tool_ids, True

        return completed_results, executed_tool_ids, False

    def _inject_interrupted_tombstones(
        self,
        response: ProviderResponse,
        completed_results: list[ToolResult],
        executed_tool_ids: set[str],
        cancel_token: CancellationToken | None,
    ) -> None:
        """Inject error tombstones for tool_calls skipped due to interruption."""
        for tc in response.tool_calls:
            if tc.id not in executed_tool_ids:
                reason = (
                    "[Cancelled by user]"
                    if cancel_token and cancel_token.is_cancelled
                    else "[Tool execution interrupted:"
                    " user sent a new message]"
                )
                completed_results.append(
                    ToolResult(
                        tool_call_id=tc.id,
                        name=tc.name,
                        content=reason,
                        is_error=True,
                    )
                )
                logger.info(
                    f"Tombstone: injected interrupted result for "
                    f"tool_call {tc.name} ({tc.id})"
                )

    async def _drain_pending_messages(
        self,
        queue: asyncio.Queue[LoopEvent | None],
    ) -> None:
        """Drain pending user messages and inject into conversation."""
        pending = self._message_queue.drain_prompts()
        for pm in pending:
            self._messages.append(Message(role=Role.USER, content=pm.content))
            await queue.put(
                LoopEvent(
                    type=EventType.USER_MESSAGE_INJECTED,
                    text=pm.content,
                )
            )
            logger.info(f"Injected pending user message: {pm.content[:80]}...")

    @staticmethod
    def _inject_tombstones(
        response: ProviderResponse,
        results: list,
    ) -> None:
        """Inject synthetic error results for any orphaned tool_calls."""
        result_ids = {r.tool_call_id for r in results}
        for tc in response.tool_calls:
            if tc.id not in result_ids:
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

    async def _handle_max_rounds_reached(
        self,
        queue: asyncio.Queue[LoopEvent | None],
    ) -> None:
        """Emit a TEXT_DONE event and persist when max rounds are reached."""
        max_rounds_msg = "Reached maximum tool call rounds."
        self._messages.append(
            Message(role=Role.ASSISTANT, content=max_rounds_msg)
        )
        await queue.put(
            LoopEvent(type=EventType.TEXT_DONE, text=max_rounds_msg)
        )
        # Persist to JSONL session storage
        if self._session_storage:
            await self._session_storage.record_message(
                Message(role=Role.ASSISTANT, content=max_rounds_msg)
            )

    async def _process_locked(
        self,
        user_message: str,
        queue: asyncio.Queue[LoopEvent | None],
        cancel_token: CancellationToken | None = None,
    ) -> None:
        """Core processing logic that runs under the lock.

        Pushes LoopEvent objects into the queue for the consumer.
        """
        try:
            await self._prepare_turn(user_message)

            tool_defs = self._provider.format_tool_definitions(
                self._registry.all_tools()
            )

            tool_names_used: list[str] = []
            has_attempted_reactive_compact = False
            max_output_tokens_recovery_count = 0
            max_tokens_override: int | None = None

            for round_num in range(self._max_rounds):
                logger.debug(f"AgenticLoop round {round_num + 1}/{self._max_rounds}")

                action, max_tokens_override, max_output_tokens_recovery_count, has_attempted_reactive_compact = (
                    await self._execute_round(
                        tool_defs, tool_names_used, queue, cancel_token,
                        has_attempted_reactive_compact,
                        max_tokens_override,
                        max_output_tokens_recovery_count,
                    )
                )
                if action == "return":
                    return
                if action == "break":
                    break
                # action == "continue" → next round

            else:
                await self._handle_max_rounds_reached(queue)

        except RuntimeError as e:
            await self._handle_unrecoverable_error(e, queue)

    async def _prepare_turn(self, user_message: str) -> None:
        """Initialize a new turn: append user message and persist it."""
        self._ensure_initialized()
        self._messages.append(Message(role=Role.USER, content=user_message))
        if self._session_storage:
            await self._session_storage.record_message(
                Message(role=Role.USER, content=user_message)
            )

    async def _execute_round(
        self,
        tool_defs: list[dict] | None,
        tool_names_used: list[str],
        queue: asyncio.Queue[LoopEvent | None],
        cancel_token: CancellationToken | None,
        has_attempted_reactive_compact: bool,
        max_tokens_override: int | None,
        max_output_tokens_recovery_count: int,
    ) -> tuple[Literal["continue", "break", "return"], int | None, int, bool]:
        """Execute a single round of the agentic loop.

        Returns:
            (action, max_tokens_override, recovery_count, has_attempted_compact)
            where action is "continue", "break", or "return".
        """
        if self._is_cancelled(cancel_token):
            await self._emit_interrupted(queue)
            return "return", max_tokens_override, max_output_tokens_recovery_count, has_attempted_reactive_compact

        # Context management pipeline
        compacted = await self._autocompactor.maybe_compact(self._messages)
        if compacted is not None:
            self._messages = compacted
            logger.info("Autocompact applied — messages replaced with summary")
        self._truncate_if_needed()

        await queue.put(LoopEvent(type=EventType.THINKING))

        response = await self._stream_provider_call(
            tool_defs, queue, has_attempted_reactive_compact,
            max_tokens_override, cancel_token,
        )

        # Handle reactive compact signal (response is None)
        if response is None:
            if self._is_cancelled(cancel_token):
                await self._emit_interrupted(queue)
                return "return", None, max_output_tokens_recovery_count, has_attempted_reactive_compact
            return "continue", None, max_output_tokens_recovery_count, True

        if self._is_cancelled(cancel_token):
            await self._emit_interrupted(queue)
            return "return", max_tokens_override, max_output_tokens_recovery_count, has_attempted_reactive_compact

        if not response.tool_calls:
            return await self._handle_text_response(
                response, queue,
                max_tokens_override, max_output_tokens_recovery_count,
                has_attempted_reactive_compact,
            )

        # Has tool_calls → dispatch
        max_tokens_override = None
        interrupted = await self._dispatch_tool_calls(
            response, tool_names_used, queue, cancel_token,
        )
        if interrupted:
            logger.info("Mid-turn interruption — continuing to next API call")

        if self._is_cancelled(cancel_token):
            await self._emit_interrupted(queue)
            return "return", max_tokens_override, max_output_tokens_recovery_count, has_attempted_reactive_compact

        return "continue", max_tokens_override, max_output_tokens_recovery_count, has_attempted_reactive_compact

    async def _handle_text_response(
        self,
        response: ProviderResponse,
        queue: asyncio.Queue[LoopEvent | None],
        max_tokens_override: int | None,
        max_output_tokens_recovery_count: int,
        has_attempted_reactive_compact: bool,
    ) -> tuple[Literal["continue", "break", "return"], int | None, int, bool]:
        """Handle a text-only response (no tool calls).

        Returns the same tuple as ``_execute_round``.
        """
        should_continue, max_tokens_override, max_output_tokens_recovery_count = (
            self._handle_max_tokens_hit(
                response, max_tokens_override,
                max_output_tokens_recovery_count,
            )
        )
        if should_continue:
            return "continue", max_tokens_override, max_output_tokens_recovery_count, has_attempted_reactive_compact

        await self._finalize_text_response(response, queue)
        return "break", max_tokens_override, max_output_tokens_recovery_count, has_attempted_reactive_compact

    async def _handle_unrecoverable_error(
        self,
        error: RuntimeError,
        queue: asyncio.Queue[LoopEvent | None],
    ) -> None:
        """Surface a graceful error message for unrecoverable provider errors."""
        error_msg = (
            "I'm sorry, I encountered an error while processing your "
            "request. Please try again."
        )
        logger.error(f"AgenticLoop unrecoverable error: {error}")
        self._messages.append(
            Message(role=Role.ASSISTANT, content=error_msg)
        )
        await queue.put(
            LoopEvent(type=EventType.TEXT_DONE, text=error_msg)
        )

    @staticmethod
    def _is_cancelled(cancel_token: CancellationToken | None) -> bool:
        """Check whether the cancel token has been triggered."""
        return cancel_token is not None and cancel_token.is_cancelled

    @staticmethod
    async def _emit_interrupted(
        queue: asyncio.Queue[LoopEvent | None],
    ) -> None:
        """Emit a TEXT_DONE interrupted event to the queue."""
        await queue.put(
            LoopEvent(type=EventType.TEXT_DONE, text=INTERRUPTED_TEXT)
        )
