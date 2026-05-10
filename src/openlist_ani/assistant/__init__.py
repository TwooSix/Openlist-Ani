"""
Assistant module - agentic AI assistant framework.

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
from collections.abc import Callable
from pathlib import Path
from typing import Any

from loguru import logger


def _platform_frontends_enabled(assistant_cfg: Any) -> dict[str, bool]:
    return {
        "telegram": _telegram_frontend_enabled(assistant_cfg),
        "wechat": assistant_cfg.wechat.enabled,
        "feishu": assistant_cfg.feishu.enabled,
    }


def _telegram_frontend_enabled(assistant_cfg: Any) -> bool:
    non_telegram_enabled = assistant_cfg.wechat.enabled or assistant_cfg.feishu.enabled
    return bool(
        assistant_cfg.telegram.enabled
        or (assistant_cfg.telegram.bot_token and not non_telegram_enabled)
    )


def _validate_frontend_config(assistant_cfg: Any) -> list[str]:
    frontends_enabled = _platform_frontends_enabled(assistant_cfg)

    errors: list[str] = []
    if frontends_enabled["telegram"] and not assistant_cfg.telegram.bot_token:
        errors.append("Telegram assistant is enabled but bot_token is missing.")

    if frontends_enabled["wechat"]:
        errors.extend(_validate_wechat_frontend_config(assistant_cfg.wechat))

    if frontends_enabled["feishu"]:
        errors.extend(_validate_feishu_frontend_config(assistant_cfg.feishu))

    if not any(frontends_enabled.values()):
        errors.append(
            "Assistant enabled but no frontend is configured. "
            "Enable [assistant.telegram], [assistant.wechat], or [assistant.feishu]."
        )

    return errors


def _validate_wechat_frontend_config(wechat_cfg: Any) -> list[str]:
    hint = "Run openlist-ani-wechat-login and copy the printed config."
    errors: list[str] = []
    if not wechat_cfg.account_id:
        errors.append(f"WeChat assistant is enabled but account_id is missing. {hint}")
    if not wechat_cfg.token:
        errors.append(f"WeChat assistant is enabled but token is missing. {hint}")
    if not wechat_cfg.home_channel:
        errors.append(f"WeChat assistant is enabled but home_channel is missing. {hint}")
    return errors


def _validate_feishu_frontend_config(feishu_cfg: Any) -> list[str]:
    errors: list[str] = []
    if not feishu_cfg.app_id:
        errors.append("Feishu assistant is enabled but app_id is missing.")
    if not feishu_cfg.app_secret:
        errors.append("Feishu assistant is enabled but app_secret is missing.")
    return errors


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


async def _create_cli_frontend(
    *,
    loop: Any,
    llm_cfg: Any,
    catalog: Any,
    is_resume: bool,
) -> Any:
    from .frontend.textual_app import TextualFrontend

    session_meta = {"model": llm_cfg.openai_model, "frontend": "cli"}
    await _start_cli_session(loop, session_meta, is_resume)
    return TextualFrontend(
        loop,
        model_name=llm_cfg.openai_model,
        provider_type=llm_cfg.provider_type,
        catalog=catalog,
        session_metadata=session_meta,
    )


async def _start_cli_session(
    loop: Any, session_meta: dict[str, str], is_resume: bool
) -> None:
    if is_resume:
        selected = await _pick_session(loop.session_storage)
        if selected is not None:
            await loop.resume(selected)
            logger.info(f"CLI resumed session {selected}")
            return
    await loop.session_storage.start_new_session(metadata=session_meta)


def _create_telegram_frontend(
    *,
    loop: Any,
    loop_factory: Callable[[], Any],
    assistant_cfg: Any,
    catalog: Any,
) -> Any:
    from .frontend.telegram import TelegramFrontend

    bot_token = assistant_cfg.telegram.bot_token
    if not bot_token:
        raise ValueError("Telegram assistant is enabled but bot_token is missing.")
    return TelegramFrontend(
        loop,
        bot_token=bot_token,
        allowed_users=assistant_cfg.telegram.allowed_users or None,
        loop_factory=loop_factory,
        catalog=catalog,
    )


def _create_wechat_frontend(
    *, loop: Any, loop_factory: Callable[[], Any], assistant_cfg: Any, catalog: Any
) -> Any:
    from .frontend.messaging import AllowedChatAuthorizer, MessagingFrontend
    from openlist_ani.integrations.messaging.wechat_ilink import WechatIlinkMessenger

    wechat_cfg = assistant_cfg.wechat
    messenger = WechatIlinkMessenger(
        account_id=wechat_cfg.account_id,
        token=wechat_cfg.token,
        base_url=wechat_cfg.base_url,
        interactive_login=False,
    )
    return MessagingFrontend(
        platform="wechat",
        messenger=messenger,
        loop=loop,
        loop_factory=loop_factory,
        authorizer=AllowedChatAuthorizer([wechat_cfg.home_channel]),
        enable_notify_home_command=False,
        catalog=catalog,
    )


def _create_feishu_frontend(
    *, loop: Any, loop_factory: Callable[[], Any], assistant_cfg: Any, catalog: Any
) -> Any:
    from .frontend.messaging import MessagingFrontend
    from openlist_ani.integrations.messaging.feishu import FeishuMessenger
    from openlist_ani.integrations.messaging.state_store import MessagingStateStore

    feishu_cfg = assistant_cfg.feishu
    state_store = MessagingStateStore(feishu_cfg.state_dir)
    messenger = FeishuMessenger(
        app_id=feishu_cfg.app_id,
        app_secret=feishu_cfg.app_secret,
        domain=feishu_cfg.domain,
        connection_mode=feishu_cfg.connection_mode,
        webhook_host=feishu_cfg.webhook_host,
        webhook_port=feishu_cfg.webhook_port,
        webhook_path=feishu_cfg.webhook_path,
        bot_open_id=feishu_cfg.bot_open_id,
        require_mention=feishu_cfg.require_mention,
        store=state_store,
    )
    return MessagingFrontend(
        platform="feishu",
        messenger=messenger,
        loop=loop,
        loop_factory=loop_factory,
        state_store=state_store,
        allowed_users=feishu_cfg.allowed_users,
        catalog=catalog,
    )


def _create_messaging_frontends(
    *,
    loop: Any,
    loop_factory: Callable[[], Any],
    assistant_cfg: Any,
    catalog: Any,
) -> list[Any]:
    frontends = []
    if _telegram_frontend_enabled(assistant_cfg):
        frontends.append(
            _create_telegram_frontend(
                loop=loop,
                loop_factory=loop_factory,
                assistant_cfg=assistant_cfg,
                catalog=catalog,
            )
        )
    if assistant_cfg.wechat.enabled:
        frontends.append(
            _create_wechat_frontend(
                loop=loop,
                loop_factory=loop_factory,
                assistant_cfg=assistant_cfg,
                catalog=catalog,
            )
        )
    if assistant_cfg.feishu.enabled:
        frontends.append(
            _create_feishu_frontend(
                loop=loop,
                loop_factory=loop_factory,
                assistant_cfg=assistant_cfg,
                catalog=catalog,
            )
        )
    return frontends


async def run() -> None:
    """Build the component chain and start the assistant."""
    from openlist_ani.adapters.outbound.configuration import config

    from .core.context import ContextBuilder
    from .core.loop import AgenticLoop
    from .dream.config import AutoDreamConfig as _AutoDreamConfig
    from .dream.runner import AutoDreamRunner
    from .memory.manager import MemoryManager
    from .provider.factory import create_provider
    from .session.storage import SessionStorage
    from .skill.catalog import SkillCatalog
    from .skill.installer import install_bundled_skills_if_missing
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
    frontend_errors = _validate_frontend_config(assistant_cfg)
    if frontend_errors:
        for error in frontend_errors:
            logger.error(error)
        sys.exit(1)

    skills_dir = Path(assistant_cfg.skills_dir)
    data_dir = Path(assistant_cfg.data_dir)

    install_bundled_skills_if_missing(skills_dir)

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
        frontend = await _create_cli_frontend(
            loop=loop,
            llm_cfg=llm_cfg,
            catalog=catalog,
            is_resume=is_resume,
        )
    else:
        frontends = _create_messaging_frontends(
            loop=loop,
            loop_factory=_build_loop,
            assistant_cfg=assistant_cfg,
            catalog=catalog,
        )
        if not frontends:
            logger.error(
                "Assistant enabled but no frontend is configured. "
                "Enable [assistant.telegram], [assistant.wechat], or [assistant.feishu]."
            )
            sys.exit(1)

    logger.info(
        f"Starting assistant ({llm_cfg.provider_type} / {llm_cfg.openai_model})"
    )
    try:
        if is_cli:
            await frontend.run()
        else:
            await asyncio.gather(*(frontend.run() for frontend in frontends))
    finally:
        logger.info("Shutting down assistant - cleaning up resources")
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

    # Console handler - only for Telegram mode.
    # CLI mode uses Rich for terminal output; loguru stdout would
    # pollute the spinner and prompt_toolkit rendering.
    if not is_cli:
        loguru_logger.add(
            sys.stderr,
            level="INFO",
        )

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        loguru_logger.info("Assistant interrupted by user.")
