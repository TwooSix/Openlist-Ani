"""
Assistant module — agentic AI assistant framework.

Architecture:
- Frontend layer (Telegram / CLI)
- AgenticLoop (core while-loop)
- ToolOrchestrator (parallel/serial dispatch)
- Provider abstraction (OpenAI / Anthropic)
- Tool + Skill system
- SubAgent mechanism
- Four-file persistent memory (SOUL/MEMORY/USER/SESSION)

Entry point: `main()` is registered as `openlist-ani-assistant` console script.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

from loguru import logger


async def run() -> None:
    """Build the component chain and start the assistant."""
    from openlist_ani.config import config

    from .core.context import ContextBuilder
    from .core.loop import AgenticLoop
    from .frontend.cli import CLIFrontend
    from .frontend.telegram import TelegramFrontend
    from .memory.compactor import SessionCompactor
    from .memory.manager import MemoryManager
    from .provider.factory import create_provider
    from .skill.catalog import SkillCatalog
    from .tool.builtin.agent_tool import AgentTool
    from .tool.builtin.send_message_tool import SendMessageTool
    from .tool.builtin.skill_tool import SkillTool
    from .tool.registry import ToolRegistry

    # Configuration
    assistant_cfg = config.assistant
    llm_cfg = config.llm

    skills_dir = Path(assistant_cfg.skills_dir)
    data_dir = Path(assistant_cfg.data_dir)

    # Create provider (shared across all loops)
    provider = create_provider(
        provider_type=assistant_cfg.provider_type,
        api_key=llm_cfg.openai_api_key,
        base_url=llm_cfg.openai_base_url,
        model=llm_cfg.openai_model,
    )

    # Create memory manager (four-file persistent memory + CLAUDE.md)
    memory = MemoryManager(
        data_dir=data_dir,
        project_root=Path.cwd(),
    )

    # Start a new session (creates today's session file header if needed)
    await memory.start_new_session()

    # Create memory compactor (compresses MEMORY.md when too large)
    compactor = SessionCompactor(
        memory=memory,
        provider=provider,
        threshold_tokens=assistant_cfg.session_compact_threshold,
    )
    # Run initial compaction check
    await compactor.maybe_compact()

    # Discover skills
    catalog = SkillCatalog(skills_dir)
    catalog.discover()

    def _build_loop() -> AgenticLoop:
        """Factory function to create an AgenticLoop with fresh state.

        Each loop gets its own tool registry and context builder,
        but shares the same provider, memory, and skill catalog.
        """
        registry = ToolRegistry()
        registry.register(SkillTool(catalog))

        intermediate_messages: list[str] = []

        def send_message_callback(message: str) -> None:
            intermediate_messages.append(message)

        registry.register(SendMessageTool(send_message_callback))
        registry.register(AgentTool(provider, registry, memory))

        context = ContextBuilder(
            memory, catalog,
            model_name=llm_cfg.openai_model,
            provider_type=assistant_cfg.provider_type,
            tools=registry.all_tools(),
        )
        return AgenticLoop(provider, registry, context, memory)

    # Create the primary loop
    loop = _build_loop()

    # Select frontend
    is_cli = "--cli" in sys.argv

    if is_cli:
        frontend = CLIFrontend(
            loop,
            model_name=llm_cfg.openai_model,
            provider_type=assistant_cfg.provider_type,
            catalog=catalog,
        )
    else:
        # Telegram mode — pass a loop factory so each chat gets its own loop
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
        )

    logger.info(
        f"Starting assistant ({assistant_cfg.provider_type} / {llm_cfg.openai_model})"
    )
    await frontend.run()


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
