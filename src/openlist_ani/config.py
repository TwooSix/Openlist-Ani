"""
Configuration management module.
Supports hot-reloading and Pydantic validation.
"""

import os
import re
import string
import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator
from tomlkit import dumps as toml_dumps

from .core.download.api.model import OfflineDownloadTool
from .logger import logger


class PriorityConfig(BaseModel):
    """Configuration for resource download priority filtering.

    Each field is an ordered list where earlier entries have higher priority.
    When a higher-priority resource has already been downloaded for the same
    (anime_name, season, episode), lower-priority resources are skipped.

    The ``version`` field is exempt: a newer version is always downloaded
    regardless of priority rules.
    """

    field_order: list[str] = Field(
        default_factory=lambda: ["fansub", "quality", "languages"]
    )  # Order in which fields are compared; earlier fields take precedence
    fansub: list[str] = Field(
        default_factory=list
    )  # Fansub group priority, e.g. ["Fansub_A", "Fansub_B"]
    languages: list[str] = Field(
        default_factory=list
    )  # Language priority, e.g. ["简", "繁"]
    quality: list[str] = Field(
        default_factory=lambda: ["2160p", "1080p", "720p", "480p"]
    )  # Quality priority (high to low); set to [] to disable


class MetadataFilterConfig(BaseModel):
    """Configuration for metadata-based blacklist filtering.

    Each field is a list of values to exclude.  An RSS entry whose
    metadata matches any value in the corresponding list is filtered out.
    """

    exclude_fansub: list[str] = Field(
        default_factory=list
    )  # Fansub groups to exclude, e.g. ["XX字幕组"]
    exclude_quality: list[str] = Field(
        default_factory=list
    )  # Quality values to exclude, e.g. ["480p"]
    exclude_languages: list[str] = Field(
        default_factory=list
    )  # Language values to exclude, e.g. ["未知"]
    exclude_patterns: list[str] = Field(
        default_factory=list
    )  # Regex patterns to exclude RSS entries by title


class RSSConfig(BaseModel):
    urls: list[str] = Field(default_factory=list)
    interval_time: int = 300  # RSS fetch interval in seconds (default: 5 minutes)
    strict: bool = False  # Strict mode: filter entries whose rename stem matches existing downloads
    filter: MetadataFilterConfig = MetadataFilterConfig()
    priority: PriorityConfig = PriorityConfig()


class OpenListConfig(BaseModel):
    url: str = "http://localhost:5244"
    token: str = ""
    download_path: str = "/"
    offline_download_tool: OfflineDownloadTool = OfflineDownloadTool.QBITTORRENT
    rename_format: str = (
        "{anime_name} S{season:02d}E{episode:02d} {fansub} {quality} {languages}"
    )

    @field_validator("offline_download_tool", mode="before")
    @classmethod
    def _normalize_offline_download_tool(
        cls, value: OfflineDownloadTool | str
    ) -> OfflineDownloadTool | str:
        if isinstance(value, OfflineDownloadTool):
            return value
        if isinstance(value, str):
            normalized = value.strip()
            if not normalized:
                raise ValueError("offline_download_tool cannot be empty.")
            lower_normalized = normalized.lower()
            for tool in OfflineDownloadTool:
                if (
                    lower_normalized == tool.value.lower()
                    or lower_normalized == tool.name.lower()
                ):
                    return tool
        return value


class LLMConfig(BaseModel):
    provider_type: str = "openai"  # "openai" | "anthropic"
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
    bots: list[BotConfig] = Field(default_factory=list)


class TelegramAssistantConfig(BaseModel):
    """Configuration for Telegram assistant bot."""

    bot_token: str = ""
    allowed_users: list[int] = Field(default_factory=list)


class AutoDreamConfig(BaseModel):
    """Configuration for auto-dream memory consolidation."""

    enabled: bool = True
    min_hours: float = 24.0  # Minimum hours since last consolidation
    min_sessions: int = 5  # Minimum sessions since last consolidation


class AssistantConfig(BaseModel):
    """Configuration for assistant module."""

    enabled: bool = False
    max_context_tokens: int = 128_000
    session_compact_threshold: int = 100_000
    skills_dir: str = "skills"  # Skill search directory
    data_dir: str = "data/assistant"  # Memory file directory
    telegram: TelegramAssistantConfig = TelegramAssistantConfig()
    auto_dream: AutoDreamConfig = AutoDreamConfig()


class LogConfig(BaseModel):
    """Configuration for logging."""

    level: str = "INFO"  # Log level: DEBUG, INFO, WARNING, ERROR, CRITICAL
    rotation: str = "00:00"  # Log rotation time (e.g., "00:00" for midnight, "500 MB" for size-based)
    retention: str = "1 week"  # How long to keep old logs


class BangumiConfig(BaseModel):
    """Configuration for Bangumi API integration."""

    access_token: str = (
        ""  # Bangumi API Access Token (also supports env var BANGUMI_TOKEN)
    )


class MikanConfig(BaseModel):
    """Configuration for Mikan (mikanani.me) integration."""

    username: str = ""  # Mikan account username
    password: str = ""  # Mikan account password


class ProxyConfig(BaseModel):
    """Configuration for proxy settings."""

    http: str = ""  # HTTP proxy URL (e.g., "http://127.0.0.1:7890")
    https: str = ""  # HTTPS proxy URL (e.g., "http://127.0.0.1:7890")


class BackendConfig(BaseModel):
    """Configuration for the backend API server."""

    host: str = "127.0.0.1"  # Bind address (localhost only by default)
    port: int = 26666  # Listening port


class UserConfig(BaseModel):
    rss: RSSConfig = RSSConfig()
    openlist: OpenListConfig = OpenListConfig()
    llm: LLMConfig = LLMConfig()
    notification: NotificationConfig = NotificationConfig()
    assistant: AssistantConfig = AssistantConfig()
    log: LogConfig = LogConfig()
    proxy: ProxyConfig = ProxyConfig()
    bangumi: BangumiConfig = BangumiConfig()
    mikan: MikanConfig = MikanConfig()
    backend: BackendConfig = BackendConfig()


# Supported field names for ``rename_format`` in ``[openlist]``.
_SUPPORTED_RENAME_FIELDS: frozenset[str] = frozenset(
    {"anime_name", "season", "episode", "fansub", "quality", "languages"}
)


class ConfigManager:
    def __init__(self, config_path: str = "config.toml"):
        self.config_path = Path(os.getcwd()) / config_path
        self._config: UserConfig = UserConfig()
        self._last_mtime: float = 0
        self._load_failed: bool = False

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
            self._load_failed = False
            self._set_proxy_env()
        except Exception as e:
            self._last_mtime = self.config_file_stat.st_mtime
            self._load_failed = True
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
        - Assistant (if enabled): requires telegram.bot_token,
          and depends on llm.openai_api_key

        Returns:
            True if all required configuration is valid, False otherwise.
        """
        # Access self.data to trigger mtime-based reload if needed,
        # avoiding unconditional reload that duplicates error logs.
        _ = self.data

        if self._load_failed:
            return False

        errors: list[str] = []
        warnings: list[str] = []

        self._validate_core_config(errors)
        self._validate_rename_format(errors)
        self._validate_exclude_patterns(errors)
        self._validate_llm_config(errors)
        self._validate_notification_config(errors, warnings)
        self._validate_assistant_config(errors, warnings)
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

    def _validate_rename_format(self, errors: list[str]) -> None:
        """Validate that ``rename_format`` uses only supported field names."""
        fmt = self.openlist.rename_format
        if not fmt:
            return

        formatter = string.Formatter()
        try:
            parsed_fields = {
                field_name
                for _, field_name, _, _ in formatter.parse(fmt)
                if field_name is not None
            }
        except (ValueError, KeyError) as e:
            errors.append(
                f"Invalid rename_format syntax: '{fmt}'. Error: {e}"
            )
            return

        unsupported = parsed_fields - _SUPPORTED_RENAME_FIELDS
        if unsupported:
            errors.append(
                f"rename_format contains unsupported fields: {unsupported}. "
                f"Supported fields: {sorted(_SUPPORTED_RENAME_FIELDS)}"
            )

    def _validate_exclude_patterns(self, errors: list[str]) -> None:
        """Validate that ``exclude_patterns`` entries are valid regular expressions."""
        for i, pattern in enumerate(self.rss.filter.exclude_patterns):
            try:
                re.compile(pattern)
            except re.error as e:
                errors.append(
                    f"rss.filter.exclude_patterns[{i}] is not a valid regex: "
                    f"'{pattern}'. Error: {e}"
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

    def _validate_assistant_config(
        self, errors: list[str], warnings: list[str]
    ) -> None:
        if not self.assistant.enabled:
            return

        # API key is required for any provider
        if not self.llm.openai_api_key:
            errors.append(
                "Assistant is enabled but API key is missing. "
                "Assistant requires LLM. Please set [llm] openai_api_key."
            )

        # Validate provider_type
        if self.llm.provider_type not in ("openai", "anthropic"):
            errors.append(
                f"Unknown LLM provider_type: '{self.llm.provider_type}'. "
                "Supported values: 'openai', 'anthropic'."
            )

        # Telegram bot_token is required
        if not self.assistant.telegram.bot_token:
            errors.append(
                "Assistant is enabled but Telegram bot_token is missing. "
                "Please set [assistant.telegram] bot_token."
            )

        # allowed_users empty = allow all (warn for security awareness)
        if not self.assistant.telegram.allowed_users:
            warnings.append(
                "Assistant allowed_users is empty — all Telegram users can interact. "
                "Consider adding specific user IDs in [assistant.telegram] allowed_users."
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

    @property
    def bangumi(self) -> BangumiConfig:
        return self.data.bangumi

    @property
    def bangumi_token(self) -> str:
        """Get Bangumi token with env var override."""
        return os.environ.get("BANGUMI_TOKEN", "") or self.bangumi.access_token

    @property
    def mikan(self) -> MikanConfig:
        return self.data.mikan

    @property
    def backend(self) -> BackendConfig:
        return self.data.backend

    @property
    def backend_url(self) -> str:
        """Get the full backend API base URL."""
        # Local-only backend; HTTPS is not needed for localhost communication.
        return f"http://{self.backend.host}:{self.backend.port}"  # noqa: S501

    async def validate_openlist(self) -> bool:
        """
        Validate OpenList server health and offline download tool availability.

        1. Tests server health via the public /api/public/settings endpoint.
        2. Verifies the configured offline_download_tool is supported by the server.

        Returns:
            True if all checks pass, False otherwise.
        """
        from .core.download.api import OpenListClient

        client = OpenListClient(
            base_url=self.openlist.url,
            token=self.openlist.token,
        )

        # Step 1: health check
        logger.info("Verifying OpenList server health...")
        if not await client.is_healthy():
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
