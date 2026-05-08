"""
Configuration management module.
Supports explicit loading and Pydantic validation.
"""

import os
import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator
from tomlkit import dumps as toml_dumps

from openlist_ani.integrations.openlist import normalize_offline_download_tool_name
from openlist_ani.logger import FATAL_LEVEL, logger


class PriorityConfig(BaseModel):
    """Configuration for release download priority filtering.

    Each field is an ordered list where earlier entries have higher priority.
    When a higher-priority release has already been downloaded for the same
    (anime_name, season, episode), lower-priority releases are skipped.

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
    offline_download_tool: str = "qBittorrent"
    rename_format: str = (
        "{anime_name} S{season:02d}E{episode:02d} {fansub} {quality} {languages}"
    )

    @field_validator("offline_download_tool", mode="before")
    @classmethod
    def _validate_offline_download_tool(cls, value: str) -> str:
        if isinstance(value, str):
            normalized = value.strip()
            if not normalized:
                raise ValueError("offline_download_tool cannot be empty.")
            return normalize_offline_download_tool_name(normalized)
        return value


class DownloaderConfig(BaseModel):
    provider: str = "openlist"


class FileRenamerConfig(BaseModel):
    provider: str = "openlist"


class LLMConfig(BaseModel):
    provider_type: str = "openai"  # "openai" | "anthropic"
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o"
    tmdb_api_key: str = "8ed20a12d9f37dcf9484a505c8be696c"
    tmdb_language: str = "zh-CN"  # TMDB metadata language (zh-CN, en-US, ja-JP, etc.)


class MetadataParserConfig(BaseModel):
    provider: str = "llm_tmdb"


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

    level: str = "INFO"  # Log level: DEBUG, INFO, WARNING, ERROR, FATAL
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
    downloader: DownloaderConfig = DownloaderConfig()
    file_renamer: FileRenamerConfig = FileRenamerConfig()
    metadata_parser: MetadataParserConfig = MetadataParserConfig()
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


class ConfigManager:
    def __init__(self, config_path: str = "config.toml"):
        self.config_path = Path(os.getcwd()) / config_path
        self._config: UserConfig = UserConfig()
        self._load_failed: bool = False

        self._load_from_file()

    def _set_proxy_env(self) -> None:
        """Set proxy environment variables from configuration."""
        from .environment import ProxyEnvironmentApplier

        ProxyEnvironmentApplier().apply(self._config.proxy)

    def _load_from_file(self) -> None:
        """Load configuration from file during adapter construction."""
        if not self.config_path.exists():
            self.save()
            return

        try:
            content = self.config_path.read_bytes()
            raw = tomllib.loads(content.decode("utf-8"))
            self._config = UserConfig.model_validate(raw)
            self._load_failed = False
            self._set_proxy_env()
        except Exception as e:
            self._load_failed = True
            logger.log(
                FATAL_LEVEL,
                f"Failed to load configuration from {self.config_path}: {e}. "
                "Application will exit.",
            )

    @property
    def data(self) -> UserConfig:
        """Get the in-memory configuration snapshot."""
        return self._config

    def save(self) -> None:
        """Save current configuration to file."""
        try:
            payload = self._config.model_dump()
            self.config_path.write_text(toml_dumps(payload), encoding="utf-8")
        except Exception as e:
            logger.error(
                f"Failed to save configuration to {self.config_path}: {e}. "
                "Runtime changes may not persist after restart."
            )

    def add_rss_url(self, url: str) -> None:
        """Add a new RSS URL to configuration."""
        if url not in self._config.rss.urls:
            self._config.rss.urls.append(url)
            self.save()

    @property
    def rss(self) -> RSSConfig:
        return self.data.rss

    @property
    def downloader(self) -> DownloaderConfig:
        return self.data.downloader

    @property
    def file_renamer(self) -> FileRenamerConfig:
        return self.data.file_renamer

    @property
    def openlist(self) -> OpenListConfig:
        return self.data.openlist

    @property
    def llm(self) -> LLMConfig:
        return self.data.llm

    @property
    def metadata_parser(self) -> MetadataParserConfig:
        return self.data.metadata_parser

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
        local_backend_scheme = "http"
        return f"{local_backend_scheme}://{self.backend.host}:{self.backend.port}"

    @property
    def load_failed(self) -> bool:
        return self._load_failed


_config_instance: ConfigManager | None = None


def load_config(config_path: str | None = None) -> ConfigManager:
    """Load configuration explicitly from *config_path* or CONFIG_PATH."""
    return ConfigManager(config_path or os.environ.get("CONFIG_PATH", "config.toml"))


def get_config() -> ConfigManager:
    """Return the process configuration, loading it on first use."""
    global _config_instance
    if _config_instance is None:
        _config_instance = load_config()
    return _config_instance


class LazyConfig:
    """Lazy proxy for the process configuration.

    Importing this module should not read/write config files or mutate the
    process environment. The first attribute access performs the load.
    """

    def __getattr__(self, name: str) -> Any:
        return getattr(get_config(), name)

    def __repr__(self) -> str:
        status = "loaded" if _config_instance is not None else "unloaded"
        return f"<LazyConfig {status}>"


config = LazyConfig()
