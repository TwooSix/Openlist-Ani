"""
Assistant module — agentic AI assistant framework.

Architecture:
- Frontend layer (Telegram / CLI)
- AgenticLoop (core while-loop)
- ToolOrchestrator (parallel/serial dispatch)
- Provider abstraction (OpenAI / Anthropic)
- Tool + Skill system
- SubAgent mechanism
- Directory-based memory (SOUL.md + memory/ + sessions/)
- Auto-dream background memory consolidation

Entry point: `main()` is registered as `openlist-ani-assistant` console script.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

from loguru import logger


async def _pick_session(storage) -> str | None:
    """Interactive session picker shown before the TUI launches.

    Runs a lightweight Textual app with an OptionList so the user can
    navigate sessions with arrow keys and press Enter to select.
    Press Esc to start a fresh session instead.

    Returns the selected session_id or None.
    """
    from openlist_ani.assistant.frontend.textual_app.app import SessionPickerApp

    sessions = await storage.list_sessions()
    if not sessions:
        return None

    picker = SessionPickerApp(sessions)
    await picker.run_async()
    return picker.selected_session_id


async def run() -> None:
    """Build the component chain and start the assistant."""
    from openlist_ani.config import config

    from .core.context import ContextBuilder
    from .core.loop import AgenticLoop
    from .dream.config import AutoDreamConfig as _AutoDreamConfig
    from .dream.runner import AutoDreamRunner
    from .frontend.textual_app import TextualFrontend
    from .frontend.telegram import TelegramFrontend
    from .memory.manager import MemoryManager
    from .provider.factory import create_provider
    from .session.storage import SessionStorage
    from .skill.catalog import SkillCatalog
    from .tool.builtin.agent_tool import AgentTool
    from .tool.builtin.send_message_tool import SendMessageTool
    from .tool.builtin.skill_tool import SkillTool
    from .tool.builtin.grep_tool import GrepTool
    from .tool.builtin.memory_tool import MemoryTool
    from .tool.builtin.read_file_tool import ReadFileTool
    from .tool.builtin.web_fetch_tool import WebFetchTool
    from .tool.registry import ToolRegistry

    # Configuration
    assistant_cfg = config.assistant
    llm_cfg = config.llm

    skills_dir = Path(assistant_cfg.skills_dir)
    data_dir = Path(assistant_cfg.data_dir)

    # Create provider (shared across all loops)
    provider = create_provider(
        provider_type=llm_cfg.provider_type,
        api_key=llm_cfg.openai_api_key,
        base_url=llm_cfg.openai_base_url,
        model=llm_cfg.openai_model,
    )

    # Create memory manager (directory-based memory + CLAUDE.md)
    memory = MemoryManager(
        data_dir=data_dir,
        project_root=Path.cwd(),
    )

    # Run data migration if needed (old flat-file -> directory-based)
    await memory.migrate_if_needed()

    # Create auto-dream runner
    dream_cfg = assistant_cfg.auto_dream
    auto_dream_runner = AutoDreamRunner(
        config=_AutoDreamConfig(
            enabled=dream_cfg.enabled,
            min_hours=dream_cfg.min_hours,
            min_sessions=dream_cfg.min_sessions,
        ),
        provider=provider,
        memory_dir=data_dir / "memory",
        sessions_dir=data_dir / "sessions",
        data_dir=data_dir,
    )

    # Discover skills
    catalog = SkillCatalog(skills_dir)
    catalog.discover()

    sessions_dir = data_dir / "sessions"

    def _build_loop() -> AgenticLoop:
        """Factory function to create an AgenticLoop with fresh state.

        Each loop gets its own tool registry, context builder, and
        session storage instance (so per-chat file handles don't
        collide).  Provider, memory, skill catalog, and auto-dream
        runner are shared.
        """
        registry = ToolRegistry()
        registry.register(SkillTool(catalog))

        intermediate_messages: list[str] = []

        def send_message_callback(message: str) -> None:
            intermediate_messages.append(message)

        registry.register(SendMessageTool(send_message_callback))
        registry.register(AgentTool(provider, registry))
        registry.register(WebFetchTool(provider, registry))
        registry.register(MemoryTool(memory.memory_dir))
        registry.register(ReadFileTool())
        registry.register(GrepTool())

        context = ContextBuilder(
            memory,
            catalog,
            model_name=llm_cfg.openai_model,
            provider_type=llm_cfg.provider_type,
            tools=registry.all_tools(),
        )
        # Each loop gets its own SessionStorage instance pointing at the
        # shared sessions/ directory.  This avoids file-handle conflicts
        # when multiple Telegram chats are active concurrently.
        loop_session_storage = SessionStorage(sessions_dir)
        return AgenticLoop(
            provider,
            registry,
            context,
            memory,
            session_storage=loop_session_storage,
            auto_dream_runner=auto_dream_runner,
        )

    # Create the primary loop
    loop = _build_loop()

    # Select frontend
    is_cli = "--cli" in sys.argv
    is_resume = "--resume" in sys.argv

    if is_cli:
        session_meta = {"model": llm_cfg.openai_model, "frontend": "cli"}

        if is_resume:
            # CLI resume mode: show a pre-launch session picker
            selected = await _pick_session(loop.session_storage)
            if selected is not None:
                await loop.resume(selected)
                logger.info(f"CLI resumed session {selected}")
            else:
                # User cancelled or no sessions — start fresh
                await loop.session_storage.start_new_session(metadata=session_meta)
        else:
            # CLI mode: always start a fresh session
            await loop.session_storage.start_new_session(metadata=session_meta)
        frontend = TextualFrontend(
            loop,
            model_name=llm_cfg.openai_model,
            provider_type=llm_cfg.provider_type,
            catalog=catalog,
            session_metadata=session_meta,
        )
    else:
        # Telegram mode: session lifecycle is per-chat and managed by
        # TelegramFrontend.  Each chat_id gets its own session that is
        # resumed across bot restarts.  No session is started here.
        bot_token = assistant_cfg.telegram.bot_token
        allowed_users = assistant_cfg.telegram.allowed_users

        if not bot_token:
            logger.error(
                "Telegram bot token not configured. "
                "Set [assistant.telegram] bot_token or use --cli mode."
            )
            sys.exit(1)

        frontend = TelegramFrontend(
            loop,
            bot_token=bot_token,
            allowed_users=allowed_users or None,
            loop_factory=_build_loop,
            catalog=catalog,
        )

    logger.info(
        f"Starting assistant ({llm_cfg.provider_type} / {llm_cfg.openai_model})"
    )
    try:
        await frontend.run()
    finally:
        logger.info("Shutting down assistant — cleaning up resources")
        await loop.shutdown()
        await provider.close()


def main() -> None:
    """Console script entry point."""
    is_cli = "--cli" in sys.argv

    # Suppress noisy third-party loggers (stdlib logging side)
    for noisy_logger in ("httpx", "httpcore", "urllib3", "asyncio"):
        logging.getLogger(noisy_logger).setLevel(logging.WARNING)

    # Configure loguru: single source of truth for all logging.
    # The project-wide loguru logger (openlist_ani.logger) adds both a
    # stdout handler and a file handler by default. We reconfigure here
    # to match the assistant's needs.
    from loguru import logger as loguru_logger

    from openlist_ani.logger import LOG_DIR

    loguru_logger.remove()  # Remove all default handlers

    # Dedicated assistant log file (matches legacy "assistant_xxx.log" naming)
    loguru_logger.add(
        LOG_DIR / "assistant_{time:YYYY-MM-DD}.log",
        rotation="00:00",
        retention="1 week",
        level="INFO",
        encoding="utf-8",
        mode="a",
    )

    # Console handler — only for Telegram mode.
    # CLI mode uses Rich for terminal output; loguru stdout would
    # pollute the spinner and prompt_toolkit rendering.
    if not is_cli:
        loguru_logger.add(
            sys.stderr,
            level="INFO",
        )

    asyncio.run(run())
