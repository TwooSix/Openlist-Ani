"""
Configuration management module.
Supports hot-reloading and Pydantic validation.
"""

import os
import tomllib
from pathlib import Path
from typing import Any, List

from pydantic import BaseModel, Field
from tomlkit import dumps as toml_dumps

from .core.download.downloader.api.model import OfflineDownloadTool
from .logger import logger


class RSSConfig(BaseModel):
    urls: List[str] = Field(default_factory=list)
    interval_time: int = 300  # RSS fetch interval in seconds (default: 5 minutes)


class OpenListConfig(BaseModel):
    url: str = "http://localhost:5244"
    token: str = ""
    download_path: str = "/"
    offline_download_tool: OfflineDownloadTool = OfflineDownloadTool.QBITTORRENT
    rename_format: str = "{anime_name} S{season:02d}E{episode:02d}"


class LLMConfig(BaseModel):
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o"
    tmdb_api_key: str = "8ed20a12d9f37dcf9484a505c8be696c"
    tmdb_language: str = "zh-CN"  # TMDB metadata language (zh-CN, en-US, ja-JP, etc.)


class BotConfig(BaseModel):
    """Configuration for a single notification bot."""

    type: str  # "telegram" or "pushplus"
    enabled: bool = True
    config: dict[str, Any] = Field(default_factory=dict)


class NotificationConfig(BaseModel):
    """Configuration for notification system."""

    enabled: bool = False
    batch_interval: float = 300.0  # Batch notifications interval in seconds (default: 5 minutes, 0 to disable)
    bots: List[BotConfig] = Field(default_factory=list)


class TelegramAssistantConfig(BaseModel):
    """Configuration for Telegram assistant bot."""

    bot_token: str = ""
    allowed_users: List[int] = Field(default_factory=list)


class AssistantConfig(BaseModel):
    """Configuration for assistant module."""

    enabled: bool = False
    max_history_messages: int = 10
    telegram: TelegramAssistantConfig = TelegramAssistantConfig()


class LogConfig(BaseModel):
    """Configuration for logging."""

    level: str = "INFO"  # Console log level: DEBUG, INFO, WARNING, ERROR, CRITICAL
    file_level: str = "INFO"  # File log level
    rotation: str = "00:00"  # Log rotation time (e.g., "00:00" for midnight, "500 MB" for size-based)
    retention: str = "1 week"  # How long to keep old logs


class ProxyConfig(BaseModel):
    """Configuration for proxy settings."""

    http: str = ""  # HTTP proxy URL (e.g., "http://127.0.0.1:7890")
    https: str = ""  # HTTPS proxy URL (e.g., "http://127.0.0.1:7890")


class UserConfig(BaseModel):
    rss: RSSConfig = RSSConfig()
    openlist: OpenListConfig = OpenListConfig()
    llm: LLMConfig = LLMConfig()
    notification: NotificationConfig = NotificationConfig()
    assistant: AssistantConfig = AssistantConfig()
    log: LogConfig = LogConfig()
    proxy: ProxyConfig = ProxyConfig()


class ConfigManager:
    def __init__(self, config_path: str = "config.toml"):
        self.config_path = Path(os.getcwd()) / config_path
        self._config: UserConfig = UserConfig()
        self._last_mtime: float = 0

        self.reload()

    def _set_proxy_env(self) -> None:
        """Set proxy environment variables from configuration."""
        if self._config.proxy.http:
            os.environ["HTTP_PROXY"] = self._config.proxy.http
            logger.info(f"Set HTTP_PROXY to {self._config.proxy.http}")

        if self._config.proxy.https:
            os.environ["HTTPS_PROXY"] = self._config.proxy.https
            logger.info(f"Set HTTPS_PROXY to {self._config.proxy.https}")

    def reload(self) -> None:
        """Reload configuration from file unconditionally."""
        if not self.config_path.exists():
            self.save()
            return

        try:
            content = self.config_path.read_bytes()
            raw = tomllib.loads(content.decode("utf-8"))
            self._config = UserConfig.model_validate(raw)
            self._last_mtime = self.config_file_stat.st_mtime
            self._set_proxy_env()
        except Exception as e:
            logger.error(f"Failed to load configuration: {e}")

    @property
    def config_file_stat(self) -> os.stat_result:
        return self.config_path.stat()

    @property
    def data(self) -> UserConfig:
        """
        Get configuration data.
        Checks for file updates on every access.
        """
        if self.config_path.exists():
            try:
                current_mtime = self.config_file_stat.st_mtime
                if current_mtime > self._last_mtime:
                    self.reload()
            except OSError:
                pass
        return self._config

    def save(self) -> None:
        """Save current configuration to file."""
        try:
            payload = self._config.model_dump()
            self.config_path.write_text(toml_dumps(payload), encoding="utf-8")
            self._last_mtime = self.config_file_stat.st_mtime
        except Exception as e:
            logger.error(f"Failed to save configuration: {e}")

    def validate(self) -> bool:
        """
        Validate configuration logic with dependency topology awareness.

        Dependency topology:
        - Core (always required): rss.urls, openlist.url, openlist.token
                - LLM (required): openai_api_key
        - Notification (if enabled): requires at least one properly configured bot
          - telegram bot: requires bot_token and user_id
          - pushplus bot: requires user_token
        - Assistant (if enabled): requires telegram.bot_token, allowed_users,
          and depends on llm.openai_api_key

        Returns:
            True if all required configuration is valid, False otherwise.
        """
        self.reload()

        errors: list[str] = []
        warnings: list[str] = []

        self._validate_core_config(errors)
        self._validate_llm_config(errors)
        self._validate_notification_config(errors, warnings)
        self._validate_assistant_config(errors)
        self._log_validation_results(errors, warnings)

        return len(errors) == 0

    def _validate_core_config(self, errors: list[str]) -> None:
        if not self.rss.urls:
            errors.append("No RSS URLs configured. Please add RSS URLs in [rss] urls.")

        if not self.openlist.url:
            errors.append("OpenList URL is not configured in [openlist] url.")

        if not self.openlist.token:
            errors.append(
                "OpenList token is not configured in [openlist] token. "
                "Authentication will fail without a valid token."
            )

    def _validate_llm_config(self, errors: list[str]) -> None:
        if not self.llm.openai_api_key:
            errors.append("OpenAI API key is missing in [llm] openai_api_key. ")

    def _validate_notification_config(
        self, errors: list[str], warnings: list[str]
    ) -> None:
        if not self.notification.enabled:
            return

        if not self.notification.bots:
            errors.append(
                "Notification is enabled but no bots are configured. "
                "Please add bot entries in [[notification.bots]]."
            )
            return

        for i, bot_cfg in enumerate(self.notification.bots):
            if not bot_cfg.enabled:
                continue
            self._validate_notification_bot(i, bot_cfg, errors, warnings)

    def _validate_notification_bot(
        self,
        index: int,
        bot_cfg: BotConfig,
        errors: list[str],
        warnings: list[str],
    ) -> None:
        bot_label = f"notification.bots[{index}] (type={bot_cfg.type})"

        if bot_cfg.type == "telegram":
            if not bot_cfg.config.get("bot_token"):
                errors.append(f"{bot_label}: 'bot_token' is required for Telegram bot.")
            if not bot_cfg.config.get("user_id"):
                errors.append(f"{bot_label}: 'user_id' is required for Telegram bot.")
            return

        if bot_cfg.type == "pushplus":
            if not bot_cfg.config.get("user_token"):
                errors.append(
                    f"{bot_label}: 'user_token' is required for PushPlus bot."
                )
            return

        warnings.append(f"{bot_label}: Unknown bot type '{bot_cfg.type}'.")

    def _validate_assistant_config(self, errors: list[str]) -> None:
        if not self.assistant.enabled:
            return

        if not self.assistant.telegram.bot_token:
            errors.append(
                "Assistant is enabled but Telegram bot token is missing. "
                "Please set [assistant.telegram] bot_token."
            )
        if not self.assistant.telegram.allowed_users:
            errors.append(
                "Assistant is enabled but no allowed users are configured. "
                "Please set [assistant.telegram] allowed_users."
            )
        if not self.llm.openai_api_key:
            errors.append(
                "Assistant is enabled but OpenAI API key is missing. "
                "Assistant requires LLM. Please set [llm] openai_api_key."
            )

    def _log_validation_results(self, errors: list[str], warnings: list[str]) -> None:
        for warning in warnings:
            logger.warning(f"Config Warning: {warning}")
        for error in errors:
            logger.error(f"Config Error: {error}")

    def add_rss_url(self, url: str) -> None:
        """Add a new RSS URL to configuration."""
        self.reload()
        if url not in self._config.rss.urls:
            self._config.rss.urls.append(url)
            self.save()

    @property
    def rss(self) -> RSSConfig:
        return self.data.rss

    @property
    def openlist(self) -> OpenListConfig:
        return self.data.openlist

    @property
    def llm(self) -> LLMConfig:
        return self.data.llm

    @property
    def notification(self) -> NotificationConfig:
        return self.data.notification

    @property
    def log(self) -> LogConfig:
        return self.data.log

    @property
    def assistant(self) -> AssistantConfig:
        return self.data.assistant

    @property
    def proxy(self) -> ProxyConfig:
        return self.data.proxy

    async def validate_openlist(self) -> bool:
        """
        Validate OpenList server health and offline download tool availability.

        1. Tests server health via the public /api/public/settings endpoint.
        2. Verifies the configured offline_download_tool is supported by the server.

        Returns:
            True if all checks pass, False otherwise.
        """
        from .core.download.downloader.api import OpenListClient

        client = OpenListClient(
            base_url=self.openlist.url,
            token=self.openlist.token,
        )

        # Step 1: health check
        logger.info("Verifying OpenList server health...")
        if not await client.check_health():
            logger.error(
                f"Cannot connect to OpenList server at {self.openlist.url}. "
                "Please check that the server is running and the URL is correct."
            )
            return False
        logger.info("OpenList server health check OK.")

        # Step 2: offline download tool validation
        tool: OfflineDownloadTool = self.openlist.offline_download_tool
        logger.info(f"Verifying offline download tool '{tool}'...")
        available_tools: (
            list[dict[str, Any]] | None
        ) = await client.get_offline_download_tools()
        if available_tools is None:
            logger.error("Failed to retrieve offline download tools from server.")
            return False

        tool_str: str = str(tool)
        available_names: list[str] = [
            t.get("name", "") if isinstance(t, dict) else str(t)
            for t in available_tools
        ]
        if tool_str not in available_names:
            logger.error(
                f"The configured offline download tool '{tool_str}' is not "
                f"available on the server. Available tools: {available_names}. "
                "Please check [openlist] offline_download_tool in config.toml."
            )
            return False
        logger.info(f"Offline download tool '{tool_str}' is available.")

        return True


if os.environ.get("CONFIG_PATH"):
    config = ConfigManager(os.environ["CONFIG_PATH"])
else:
    config = ConfigManager()
